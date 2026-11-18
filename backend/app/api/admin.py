"""系统管理：账号角色、字典口径、数据源模式、定时任务、审批待处理队列、操作审计、通讯录同步。"""
from typing import Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.config import settings
from app.core.db import get_db, get_or_404
from app.core.deps import Principal, build_principal, client_ip, get_principal, require_menu, require_recent_login
from app.core.permissions import MENUS, PUBLIC_MENUS, ROLE_DEFS
from app.core.response import BizError, bad_request, conflict, forbidden, not_found, ok, page_data
from app.jobs import registry
from app.models import LogOperation, OpsMember, OpsProject, SyncJobLog, SyncWecomApproval, SysDepartment, SysDict, SysRole, SysUser, SysUserRole
from app.services import dispatch_service, user_service, personal_permission_service as personal

router = APIRouter(prefix="/admin", tags=["系统管理"])
Admin = Depends(require_menu("admin"))


def _check_admin_write(request, p):
    if not p.is_admin:
        raise forbidden("只有平台管理员可变更授权")
    require_recent_login(request)


def _keep_admin(db, u, removing):
    if not removing or u.status != 1 or not any(r.role_code == "ADMIN-01" for r in u.roles):
        return
    others = db.scalar(select(SysUser.id).join(SysUserRole, SysUserRole.user_id == SysUser.id)
                       .join(SysRole, SysRole.id == SysUserRole.role_id).where(SysRole.role_code == "ADMIN-01",
                       SysUser.status == 1, SysUser.is_deleted.is_(False), SysUser.id != u.id).limit(1))
    if not others:
        raise conflict("必须保留至少一个有效的平台管理员")


AccountSource = Literal["", "directory", "local"]
UNASSIGNED_DEPARTMENT = "__unassigned__"


def _user_query(source: AccountSource = "", keyword: str = ""):
    q = select(SysUser).where(SysUser.is_deleted.is_(False))
    if source:
        marker = func.coalesce(SysUser.preferences["company_directory"].as_string(), "")
        in_directory = marker.not_in(("", "{}", "[]", "false", "0"))
        q = q.where(in_directory if source == "directory" else ~in_directory)
    if keyword:
        q = q.where(or_(SysUser.name.icontains(keyword, autoescape=True),
                        SysUser.wecom_userid.icontains(keyword, autoescape=True)))
    return q


def _count_users(db, query):
    return db.scalar(select(func.count()).select_from(query.with_only_columns(SysUser.id).subquery()))


@router.get("/users")
def users(keyword: str = Query("", max_length=100), dept: str = "", source: AccountSource = "",
          page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), role: str = "", compact: bool = False,
          p: Principal = Admin, db: Session = Depends(get_db)):
    q = _user_query(source, keyword.strip())
    if dept == UNASSIGNED_DEPARTMENT:
        q = q.where(SysUser.department_id.is_(None))
    elif dept:
        q = q.where(SysUser.department.has(SysDepartment.dept_code == dept))
    if role:
        if role not in ROLE_DEFS:
            raise not_found("角色")
        q = q.where(SysUser.roles.any(SysRole.role_code == role))
    total = _count_users(db, q)
    rows = db.scalars(q.order_by(SysUser.department_id, SysUser.id).offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for u in rows:
        item = {"id": u.id, "userid": u.wecom_userid, "name": u.name, "dept_code": u.dept_code,
                "dept_name": u.department.name if u.department else None, "position": u.position,
                "status": u.status, "roles": sorted(r.role_code for r in u.roles)}
        if not compact:
            prefs = u.preferences or {}
            item.update(last_login_at=u.last_login_at, mobile=u.mobile, email=u.email,
                        source="公司通讯录" if prefs.get("company_directory") else "已归档演示" if prefs.get("archived_demo_userid") else "本地账号",
                        has_mcp_token=bool(u.access_token))
        items.append(item)
    if compact:
        return ok(page_data(items, total, page, page_size))
    return ok(page_data(items, total, page, page_size, directory_total=_count_users(db, _user_query("directory", keyword.strip())),
                       departments=[{"code": d.dept_code, "name": d.name} for d in db.scalars(select(SysDepartment).order_by(SysDepartment.name))]))


class RoleAssignmentsIn(BaseModel):
    role_codes: list[str] = Field(max_length=50)
    expected_role_codes: list[str] | None = Field(default=None, max_length=50)


@router.put("/users/{user_id}/roles")
def set_roles(user_id: int, request: Request, body: RoleAssignmentsIn, p: Principal = Admin,
              db: Session = Depends(get_db)):
    _check_admin_write(request, p)
    role_codes = sorted(set(body.role_codes))
    unknown = set(role_codes) - ROLE_DEFS.keys()
    if unknown:
        raise bad_request(f"未知角色 {min(unknown)}")
    db.execute(text("SELECT pg_advisory_xact_lock(190670114)"))
    u = get_or_404(db, SysUser, user_id, "用户")
    db.refresh(u, attribute_names=["status", "roles"])
    before = sorted(r.role_code for r in u.roles)
    if body.expected_role_codes is not None and before != sorted(set(body.expected_role_codes)):
        raise conflict("这名员工的角色已被更新，请重新查看后再保存")
    if before == role_codes:
        return ok()
    _keep_admin(db, u, "ADMIN-01" not in role_codes)
    if "ADMIN-01" in role_codes and u.permission_override:
        db.delete(u.permission_override)
    db.query(SysUserRole).filter(SysUserRole.user_id == u.id).delete()
    for code in role_codes:
        db.add(SysUserRole(user_id=u.id, role_id=user_service.role_by_code(db, code).id))
    log_op(db, p.id, "user.roles", "user", u.id, {"roles": before}, {"roles": role_codes}, client_ip(request))
    db.commit()
    return ok()


@router.put("/users/{user_id}/status")
def set_status(user_id: int, request: Request, status: int = Body(embed=True), p: Principal = Admin, db: Session = Depends(get_db)):
    _check_admin_write(request, p)
    if status not in (1, 2):
        raise bad_request("请选择启用或停用")
    db.execute(text("SELECT pg_advisory_xact_lock(190670114)"))
    u = get_or_404(db, SysUser, user_id, "用户")
    _keep_admin(db, u, status != 1)
    log_op(db, p.id, "user.status", "user", u.id, {"status": u.status}, {"status": status})
    u.status = status
    if status != 1:
        u.access_token = None  # 停用即吊销 MCP 令牌
    db.commit()
    return ok()


@router.get("/roles")
def roles(source: AccountSource = "", p: Principal = Admin, db: Session = Depends(get_db)):
    rows = {r.role_code: r for r in db.scalars(select(SysRole)).all()}
    accounts = _user_query(source).with_only_columns(SysUser.id, SysUser.department_id, SysUser.status).subquery()
    by_department = {}
    counts = {}
    assignments = (select(accounts.c.department_id, SysRole.role_code, func.count())
        .select_from(accounts).join(SysUserRole, SysUserRole.user_id == accounts.c.id)
        .join(SysRole, SysRole.id == SysUserRole.role_id).group_by(accounts.c.department_id, SysRole.role_code))
    for dept_id, code, count in db.execute(assignments):
        by_department.setdefault(dept_id, {})[code] = count
        counts[code] = counts.get(code, 0) + count

    departments = []
    grouped = (select(SysDepartment.id, SysDepartment.dept_code, SysDepartment.name, SysDepartment.wecom_dept_id,
                      func.count().label("total"), func.count().filter(accounts.c.status == 1).label("active"))
        .select_from(accounts).outerjoin(SysDepartment, SysDepartment.id == accounts.c.department_id)
        .group_by(SysDepartment.id).order_by(SysDepartment.sort, SysDepartment.id))
    for dept_id, code, name, wecom_id, total, active in db.execute(grouped):
        departments.append({"code": code or UNASSIGNED_DEPARTMENT, "name": name or "未分配部门",
                            "directory": wecom_id is not None, "total": total, "active": active,
                            "role_counts": by_department.get(dept_id, {})})
    out = []
    for code, d in ROLE_DEFS.items():
        override = rows[code].permissions if code in rows else {}
        eff = {**d, **(override or {})}
        out.append({"code": code, "name": d["name"], "dept": d["dept"], "menus": eff["menus"], "buttons": eff["buttons"],
                    "data_scope": eff["data_scope"], "portal": eff["portal"], "users": counts.get(code, 0),
                    "customized": bool(override)})
    return ok({"roles": out, "menus": MENUS, "buttons": personal.options(db), "public_menus": PUBLIC_MENUS, "departments": departments,
               "directory_total": _count_users(db, _user_query("directory"))})


@router.get("/roles/{code}/members")
def role_members(code: str, keyword: str = Query("", max_length=100), page: int = Query(1, ge=1),
                 page_size: int = Query(30, ge=1, le=100), p: Principal = Admin, db: Session = Depends(get_db)):
    if code not in ROLE_DEFS:
        raise not_found("角色")
    q = select(SysUser).join(SysUserRole, SysUserRole.user_id == SysUser.id).join(SysRole, SysRole.id == SysUserRole.role_id)
    q = q.where(SysRole.role_code == code, SysUser.is_deleted.is_(False))
    if keyword:
        q = q.where(or_(SysUser.name.icontains(keyword, autoescape=True), SysUser.wecom_userid.icontains(keyword, autoescape=True)))
    total = db.scalar(select(func.count()).select_from(q.with_only_columns(SysUser.id).subquery()))
    rows = db.scalars(q.order_by(SysUser.department_id, SysUser.id).offset((page - 1) * page_size).limit(page_size)).unique().all()
    return ok(page_data([{"id": u.id, "name": u.name, "userid": u.wecom_userid, "status": u.status,
                          "department": u.department.name if u.department else None, "last_login_at": u.last_login_at}
                         for u in rows], total, page, page_size))


@router.get("/users/{user_id}/effective-permissions")
def effective_permissions(user_id: int, p: Principal = Admin, db: Session = Depends(get_db)):
    from app.api.auth import profile
    from app.services import operations_service as ops

    u = get_or_404(db, SysUser, user_id, "用户")
    target = build_principal(u)
    runtime = profile(target, db)
    owned = list(db.execute(select(OpsProject.name, OpsProject.id).where(OpsProject.owner_id == u.id)))
    joined = list(db.execute(select(OpsProject.name, OpsProject.id, OpsMember.role).join(OpsMember, OpsMember.project_id == OpsProject.id)
                             .where(OpsMember.user_id == u.id, OpsProject.owner_id != u.id)))
    last = db.scalar(select(LogOperation).where(LogOperation.user_id == u.id, LogOperation.action == "auth.login")
                     .order_by(LogOperation.id.desc()).limit(1))
    active = u.status == 1
    access_notes = []
    if active and "operations" in runtime["menus"] and not ops.can_view_all(target) and not (owned or joined):
        access_notes.append("运营项目入口已开放；尚未加入任何运营项目，列表暂为空。请在对应项目的团队里添加此人。")
    from app.services import business_workbook
    try:
        wb = business_workbook.configured_source(db)
        if wb:
            for view, label in (("contracts", "经营业绩"), ("collections", "回款情况"),
                                ("opportunities", "商机信息"), ("tender", "招标与在建"), ("board", "经营看板")):
                if any(target.has_menu(m) for m in business_workbook.MENUS[view]) and not business_workbook.can_access_view(target, wb.config, view):
                    access_notes.append(label + "：菜单已勾选，来源数据仍需授权。当前个人数据范围尚未满足此来源的读取条件。")
    except BizError as exc:
        access_notes.append(exc.message)
    return ok({"name": u.name, "userid": u.wecom_userid, "active": active, "roles": target.role_codes, **personal.view(u),
               "assigned_roles": sorted(r.role_code for r in u.roles),
               "department": u.department.name if u.department else None, "position": u.position,
               "menus": [MENUS[key] for key in runtime["menus"]] if active else [],
               "menu_keys": runtime["menus"] if active else [], "access_notes": access_notes,
               "login_permission_revision": runtime["permission_revision"],
               "buttons": sorted(target.buttons) if active else [], "data_scope": target.data_scope,
               "operations_all": active and ops.can_view_all(target), "operations_manage_all": active and ops.can_create(target),
               "operations_archive": active and (target.is_admin or target.can("operations:archive")),
               "projects": ([{"name": name, "id": pid, "role": "owner"} for name, pid in owned] +
                            [{"name": name, "id": pid, "role": role} for name, pid, role in joined]) if active else [],
               "last_login": {"at": last.created_at, "method": (last.after or {}).get("method")} if last else None})


@router.put("/users/{user_id}/permissions")
def set_personal_permissions(user_id: int, request: Request, body: personal.PersonalGrantIn,
                             p: Principal = Admin, db: Session = Depends(get_db)):
    _check_admin_write(request, p)
    db.execute(text("SELECT pg_advisory_xact_lock(190670114)"))
    user = get_or_404(db, SysUser, user_id, "用户")
    personal.update(db, p, user, body, _keep_admin, client_ip(request))
    db.commit()
    return effective_permissions(user_id, p, db)


class RolePermIn(BaseModel):
    menus: list[str]
    buttons: list[str]
    data_scope: str


@router.put("/roles/{code}")
def update_role(code: str, body: RolePermIn, request: Request, p: Principal = Admin, db: Session = Depends(get_db)):
    _check_admin_write(request, p)
    if body.data_scope not in {"self", "dept", "all"} or not set(body.menus) <= set(MENUS):
        raise bad_request("菜单或数据范围无效")
    if ("*" in body.buttons and code != "ADMIN-01") or (code == "ADMIN-01" and ("admin" not in body.menus or "*" not in body.buttons)):
        raise bad_request("平台管理员须保留系统管理入口与完整权限，其他角色不得使用全权限")
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


@router.post("/approvals/{sp_no}/review")
def review_approval(sp_no: str, reason: str = Body(..., embed=True, min_length=1, max_length=500),
                    p: Principal = Admin, db: Session = Depends(get_db)):
    from app.core.response import bad_request, conflict
    from app.models import FinSalesInvoice
    from app.services import receivables_service

    if not reason.strip():
        raise bad_request("请填写核对结果")
    receivables_service.lock(db)
    row = db.scalar(select(SyncWecomApproval).where(SyncWecomApproval.sp_no == sp_no).with_for_update())
    if not row or not row.dispatched_at or row.dispatch_status != 2 or row.template_key not in dispatch_service.LEDGER_KEYS:
        raise conflict("仅已登记后发生变更的审批可以标记核对完成")
    invoice = db.scalar(select(FinSalesInvoice).where(FinSalesInvoice.approval_no == sp_no)) if row.template_key == "invoice" else None
    if invoice and invoice.state == "pending":
        invoice.state = "cancelled"
        log_op(db, p.id, "receivables.invoice.cancel_request", "sales_invoice", invoice.id,
               after={"reason": reason.strip(), "approval_no": sp_no})
    log_op(db, p.id, "approval.ledger_review", "approval", sp_no, before={"error": row.error_msg},
           after={"reason": reason.strip(), "target": row.dispatch_target})
    row.dispatch_status, row.error_msg = 1, None
    db.commit()
    return ok({"dispatch_status": 1})


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
