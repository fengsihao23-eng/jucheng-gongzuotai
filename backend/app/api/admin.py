"""系统管理：账号角色、字典口径、数据源模式、定时任务、审批待处理队列、操作审计、通讯录同步。"""
from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.config import settings
from app.core.db import get_db, get_or_404
from app.core.deps import Principal, client_ip, get_principal, require_menu
from app.core.permissions import MENUS, ROLE_DEFS
from app.core.response import BizError, bad_request, not_found, ok, page_data
from app.jobs import registry
from app.models import LogOperation, SyncJobLog, SyncWecomApproval, SysDepartment, SysDict, SysRole, SysUser, SysUserRole
from app.services import dispatch_service, user_service

router = APIRouter(prefix="/admin", tags=["系统管理"])
Admin = Depends(require_menu("admin"))


@router.get("/users")
def users(keyword: str = "", dept: str = "", source: str = "", page: int = 1, page_size: int = 50, p: Principal = Admin,
          db: Session = Depends(get_db)):
    q = select(SysUser).where(SysUser.is_deleted.is_(False))
    if keyword:
        q = q.where(or_(SysUser.name.contains(keyword), SysUser.wecom_userid.contains(keyword)))
    rows = db.scalars(q.order_by(SysUser.department_id, SysUser.id)).all()
    directory_total = sum(bool((u.preferences or {}).get("company_directory")) for u in rows)
    if source == "directory": rows = [u for u in rows if (u.preferences or {}).get("company_directory")]
    elif source == "local": rows = [u for u in rows if not (u.preferences or {}).get("company_directory")]
    if dept:
        rows = [u for u in rows if u.dept_code == dept]
    total = len(rows)
    rows = rows[(page - 1) * page_size: page * page_size]
    items = [{"id": u.id, "userid": u.wecom_userid, "name": u.name, "dept_code": u.dept_code,
              "dept_name": u.department.name if u.department else None, "position": u.position,
              "status": u.status, "roles": [r.role_code for r in u.roles], "last_login_at": u.last_login_at,
              "mobile": u.mobile, "email": u.email,
              "source": "公司通讯录" if (u.preferences or {}).get("company_directory") else "已归档演示" if (u.preferences or {}).get("archived_demo_userid") else "本地账号",
              "has_mcp_token": bool(u.access_token)} for u in rows]
    return ok(page_data(items, total, page, page_size, directory_total=directory_total,
                       departments=[{"code": d.dept_code, "name": d.name} for d in db.scalars(select(SysDepartment).order_by(SysDepartment.name))]))


@router.put("/users/{user_id}/roles")
def set_roles(user_id: int, role_codes: list[str] = Body(embed=True), request: Request = None, p: Principal = Admin,
              db: Session = Depends(get_db)):
    u = get_or_404(db, SysUser, user_id, "用户")
    before = [r.role_code for r in u.roles]
    db.query(SysUserRole).filter(SysUserRole.user_id == u.id).delete()
    for code in role_codes:
        if code not in ROLE_DEFS:
            raise BizError(40001, f"未知角色 {code}")
        db.add(SysUserRole(user_id=u.id, role_id=user_service.role_by_code(db, code).id))
    log_op(db, p.id, "user.roles", "user", u.id, {"roles": before}, {"roles": role_codes}, client_ip(request))
    db.commit()
    return ok()


@router.put("/users/{user_id}/status")
def set_status(user_id: int, status: int = Body(embed=True), p: Principal = Admin, db: Session = Depends(get_db)):
    u = get_or_404(db, SysUser, user_id, "用户")
    log_op(db, p.id, "user.status", "user", u.id, {"status": u.status}, {"status": status})
    u.status = status
    if status != 1:
        u.access_token = None  # 停用即吊销 MCP 令牌
    db.commit()
    return ok()


@router.get("/roles")
def roles(p: Principal = Admin, db: Session = Depends(get_db)):
    rows = {r.role_code: r for r in db.scalars(select(SysRole)).all()}
    counts = dict(db.execute(select(SysRole.role_code, func.count(SysUserRole.user_id))
                             .join(SysUserRole, SysUserRole.role_id == SysRole.id).group_by(SysRole.role_code)).all())
    out = []
    for code, d in ROLE_DEFS.items():
        override = rows[code].permissions if code in rows else {}
        eff = {**d, **(override or {})}
        out.append({"code": code, "name": d["name"], "dept": d["dept"], "menus": eff["menus"], "buttons": eff["buttons"],
                    "data_scope": eff["data_scope"], "portal": eff["portal"], "users": counts.get(code, 0),
                    "customized": bool(override)})
    return ok({"roles": out, "menus": MENUS})


class RolePermIn(BaseModel):
    menus: list[str]
    buttons: list[str]
    data_scope: str


@router.put("/roles/{code}")
def update_role(code: str, body: RolePermIn, p: Principal = Admin, db: Session = Depends(get_db)):
    if code not in ROLE_DEFS:
        raise not_found("角色")
    role = user_service.role_by_code(db, code)
    log_op(db, p.id, "role.update", "role", code, role.permissions, body.model_dump())
    role.permissions = body.model_dump()
    db.commit()
    return ok()


@router.get("/dicts")
def dicts(dict_type: str = "", p: Principal = Admin, db: Session = Depends(get_db)):
    q = select(SysDict).order_by(SysDict.dict_type, SysDict.dict_key)
    if dict_type:
        q = q.where(SysDict.dict_type == dict_type)
    return ok([{"id": d.id, "dict_type": d.dict_type, "dict_key": d.dict_key, "dict_value": d.dict_value,
                "remark": d.remark, "status": d.status, "updated_at": d.updated_at} for d in db.scalars(q)])


class DictIn(BaseModel):
    dict_type: str
    dict_key: str
    dict_value: dict | list | str | int | float | None
    remark: str | None = None
    status: int = 1


@router.post("/dicts")
def upsert_dict(body: DictIn, request: Request, p: Principal = Admin, db: Session = Depends(get_db)):
    row = db.scalar(select(SysDict).where(SysDict.dict_type == body.dict_type, SysDict.dict_key == body.dict_key))
    before = {"value": row.dict_value, "status": row.status} if row else None
    if not row:
        row = SysDict(dict_type=body.dict_type, dict_key=body.dict_key)
        db.add(row)
    row.dict_value, row.remark, row.status = body.dict_value, body.remark, body.status
    log_op(db, p.id, "dict.update", "dict", f"{body.dict_type}:{body.dict_key}", before,
           {"value": body.dict_value, "status": body.status}, client_ip(request))
    db.commit()
    return ok({"id": row.id})


@router.get("/datasources")
def datasources(p: Principal = Admin):
    srcs = {"wecom": ("企业微信", bool(settings.wecom_corp_id and settings.wecom_secret)),
            "lemoncloud": ("柠檬云财务", bool(settings.lemoncloud_app_id)),
            "bankflow": ("银行流水", True), "tax": ("税务申报", True),
            "llm": ("大模型（方案生成/归因）", bool(settings.llm_api_key))}
    return ok({"global_mode": settings.data_source_mode, "overrides": settings.data_source_overrides,
               "sources": [{"key": k, "name": n, "mode": settings.source_mode(k), "credential_ready": c}
                           for k, (n, c) in srcs.items()],
               "note": "可通过接入助手验证并保存企业微信配置，即时生效；凭证仅保存在服务器受限文件中。其他数据源通过环境变量配置。"})


@router.get("/jobs")
def jobs(p: Principal = Admin, db: Session = Depends(get_db)):
    nxt = registry.next_runs()
    out = []
    for jd in registry.JOBS.values():
        last = db.scalar(select(SyncJobLog).where(SyncJobLog.job_name == jd.name, SyncJobLog.status != "skipped")
                         .order_by(SyncJobLog.id.desc()))
        out.append({"name": jd.name, "title": jd.title, "schedule": jd.schedule_text, "next_run": nxt.get(jd.name),
                    "last": {"status": last.status, "run_at": last.run_at, "rows": last.rows_affected,
                             "message": last.message, "error": (last.error_msg or "")[:300]} if last else None})
    return ok(out)


@router.post("/jobs/{name}/run")
def run_job(name: str, params: dict | None = Body(default=None, embed=True), force: bool = Body(default=True, embed=True),
            p: Principal = Admin, db: Session = Depends(get_db)):
    if name not in registry.JOBS:
        raise not_found("任务")
    log_op(db, p.id, "job.run", "job", name, after={"params": params, "force": force})
    db.commit()
    entry = registry.run_job(name, params, trigger="manual", force=force)
    return ok({"status": entry.status, "rows": entry.rows_affected, "message": entry.message, "error": entry.error_msg})


@router.get("/job-logs")
def job_logs(name: str = "", page: int = 1, page_size: int = 30, p: Principal = Admin, db: Session = Depends(get_db)):
    q = select(SyncJobLog)
    if name:
        q = q.where(SyncJobLog.job_name == name)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(SyncJobLog.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return ok(page_data([{"id": r.id, "job_name": r.job_name, "trigger": r.trigger, "status": r.status,
                          "run_at": r.run_at, "finished_at": r.finished_at, "rows": r.rows_affected,
                          "message": r.message, "error": r.error_msg} for r in rows], total, page, page_size))


@router.get("/approvals")
def approvals(status: int | None = None, template_key: str = "", page: int = 1, page_size: int = 30, p: Principal = Admin,
              db: Session = Depends(get_db)):
    q = select(SyncWecomApproval)
    if status is not None:
        q = q.where(SyncWecomApproval.dispatch_status == status)
    if template_key:
        q = q.where(SyncWecomApproval.template_key == template_key)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(SyncWecomApproval.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    stats = dict(db.execute(select(SyncWecomApproval.dispatch_status, func.count()).group_by(SyncWecomApproval.dispatch_status)).all())
    return ok(page_data([{"sp_no": r.sp_no, "template_key": r.template_key, "sp_name": r.sp_name,
                          "applicant": r.applicant_userid, "status": r.status, "dispatch_status": r.dispatch_status,
                          "dispatch_target": r.dispatch_target, "retry_count": r.retry_count, "error": r.error_msg,
                          "received_via": r.received_via, "apply_time": r.apply_time, "dispatched_at": r.dispatched_at,
                          "fields": (r.detail or {}).get("fields")} for r in rows],
                        total, page, page_size, stats={str(k): v for k, v in stats.items()}))


@router.post("/approvals/{sp_no}/retry")
def retry_approval(sp_no: str, p: Principal = Admin, db: Session = Depends(get_db)):
    rec = dispatch_service.manual_retry(db, sp_no)
    log_op(db, p.id, "approval.retry", "approval", sp_no, after={"dispatch_status": rec.dispatch_status})
    db.commit()
    return ok({"dispatch_status": rec.dispatch_status, "dispatch_target": rec.dispatch_target, "error": rec.error_msg})


@router.get("/audit")
def audit(action: str = "", user_id: int | None = None, page: int = 1, page_size: int = 30, p: Principal = Admin,
          db: Session = Depends(get_db)):
    q = select(LogOperation)
    if action:
        q = q.where(LogOperation.action.startswith(action))
    if user_id:
        q = q.where(LogOperation.user_id == user_id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(LogOperation.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    names = dict(db.execute(select(SysUser.id, SysUser.name)).all())
    return ok(page_data([{"id": r.id, "user": names.get(r.user_id), "action": r.action, "target_type": r.target_type,
                          "target_id": r.target_id, "before": r.before, "after": r.after, "ip": r.client_ip,
                          "created_at": r.created_at} for r in rows], total, page, page_size))


@router.post("/addressbook/sync")
def addressbook_sync(preview_id: str | None = Body(default=None, embed=True), p: Principal = Admin, db: Session = Depends(get_db)):
    """先读取公司通讯录预览，核对真实人员与演示账号归档清单，再提交 preview_id 确认同步。"""
    from app.services import company_directory_service as directory
    if preview_id: return ok(directory.confirm(db, p, preview_id))
    if settings.environment == "prod" or settings.source_mode("wecom") == "live":
        raise bad_request("请先预览公司通讯录，再使用 preview_id 确认导入；尚未修改任何员工账号")
    entry = registry.run_job("wecom_addressbook_sync", trigger="manual", force=True)
    return ok({"status": entry.status, "message": entry.message, "error": entry.error_msg})


@router.get("/addressbook/status")
def company_directory_status(p: Principal = Admin, db: Session = Depends(get_db)):
    from app.services.company_directory_service import status
    return ok(status(db))


@router.get("/addressbook/preview")
def preview_addressbook(p: Principal = Admin, db: Session = Depends(get_db)):
    """只读取真实公司通讯录并准备预览，不创建、更新或停用员工账号。"""
    from app.services.company_directory_service import preview
    return ok(preview(db, p))


@router.get("/wecom/menu")
def wecom_menu_preview(p: Principal = Admin):
    from app.services import wecom_inbox_service
    return ok({"menu": wecom_inbox_service.default_menu(), "mode": settings.source_mode("wecom"),
               "agent_id": settings.wecom_agent_id,
               "callback_url": settings.public_base_url.rstrip("/") + "/api/v1/wecom/callback"})


@router.post("/wecom/menu")
def wecom_menu_push(request: Request, p: Principal = Admin, db: Session = Depends(get_db)):
    """把「上传资料 / 知识库 / 更多」菜单下发到企业微信自建应用。"""
    from app.services import wecom_inbox_service
    if not p.is_admin:
        raise BizError(40301, "仅平台管理员可下发企业微信菜单")
    try:
        menu = wecom_inbox_service.push_menu()
    except Exception as exc:  # noqa: BLE001  企微错误码只回显数字，不回显原文
        code = getattr(exc, "errcode", None)
        raise BizError(50201, f"企业微信拒绝了菜单配置{f'（错误码 {code}）' if code else ''}，请检查应用 AgentID 与可信 IP") from None
    log_op(db, p.id, "wecom.menu.push", "wecom_app", str(settings.wecom_agent_id), after=menu, ip=client_ip(request))
    db.commit()
    return ok({"menu": menu})
