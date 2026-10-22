"""真实公司通讯录：只读预览、本人确认、按 CorpID/UserID 增量建档。"""
import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict

from sqlalchemy import select

from app.core.audit import log_op
from app.core.config import settings
from app.core.response import bad_request, conflict, forbidden, not_found
from app.core.security import password_version
from app.integration.wecom import get_wecom
from app.models import SysDepartment, SysDict, SysUser, SysUserRole
from app.services.xiaoju_tools import private_write
from app.services.xiaoju_business_state import locked

MARKER = "company_directory"


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _binding(db):
    row = db.scalar(select(SysDict).where(SysDict.dict_type == MARKER, SysDict.dict_key == "primary"))
    if row and not isinstance(row.dict_value, dict): raise conflict("公司目录绑定配置格式异常，请联系管理员核对")
    return row, dict(row.dict_value or {}) if row else {}


def _source():
    return _hash({"corp": settings.wecom_corp_id, "secret": settings.wecom_secret,
                  "agent": settings.wecom_agent_id, "base": settings.wecom_api_base, "mode": settings.source_mode("wecom")})


def _actor(p):
    return _hash({"id": p.id, "menus": sorted(p.menus), "buttons": sorted(p.buttons),
                  "roles": sorted(p.role_codes), "password": password_version(p.user.password_hash)})


def _before(db):
    from app.services.wecom_cli_service import binding
    users = [{"id": u.id, "userid": u.wecom_userid, "name": u.name, "dept": u.department_id, "position": u.position,
              "email": u.email, "mobile": u.mobile, "status": u.status, "deleted": u.is_deleted,
              "roles": sorted(r.role_code for r in u.roles), "directory": (u.preferences or {}).get(MARKER),
              "password": password_version(u.password_hash), "personal_connection": bool(binding(u.id))} for u in db.scalars(select(SysUser).order_by(SysUser.id))]
    depts = [{"id": d.id, "code": d.dept_code, "name": d.name, "parent": d.parent_id, "wecom_id": d.wecom_dept_id} for d in db.scalars(select(SysDepartment).order_by(SysDepartment.id))]
    return _hash({"users": users, "departments": depts, "binding": _binding(db)[1]})


def status(db):
    from app.core.integration_config import refresh
    refresh()
    _, bound = _binding(db)
    mode = settings.source_mode("wecom")
    ready = mode == "live" and bool(settings.wecom_corp_id and settings.wecom_secret and settings.wecom_agent_id)
    return {"ready": ready, "mode": mode, "company_bound": bool(bound), "last_sync_at": bound.get("last_sync_at"),
            "message": "公司应用已配置，可读取授权范围内的通讯录并预览" if ready else "公司通讯录尚未接入；请在企业微信自建应用中授权部门和成员读取，再保存公司应用配置",
            "required": ["企业 ID（CorpID）", "自建应用 AgentID", "具备通讯录读取权限的应用 Secret", "应用可见范围覆盖需导入的员工和部门"],
            "scope_note": "数据范围以企业微信实际授权为准。个人 CLI 通讯录搜索不能证明已获取全公司完整名册。"}


def _require_source(db):
    if not status(db)["ready"]: raise bad_request("公司通讯录尚未授权，当前不能导入真实人员；请先配置具备通讯录权限的企业微信公司应用")
    _, bound = _binding(db)
    if bound and bound.get("corp_id") != settings.wecom_corp_id:
        raise conflict("本站已绑定另一企业的人员目录，请先完成企业迁移核对，不能直接混合导入")


def _path(preview_id):
    if not isinstance(preview_id, str) or not re.fullmatch(r"[a-f0-9]{32}", preview_id): raise not_found("通讯录预览")
    return settings.storage_dir.parent / MARKER / (preview_id + ".json")


def _read(preview_id, user_id):
    try: record = json.loads(_path(preview_id).read_text())
    except (OSError, ValueError): raise not_found("通讯录预览") from None
    if record["user_id"] != user_id: raise forbidden("该通讯录预览属于其他账号")
    return record


def _view(record):
    return {key: record.get(key) for key in ("preview_id", "state", "created_at", "expires_at", "counts", "departments", "members", "retire_demo_users", "warnings", "result")}


def _demo_accounts(db):
    from scripts.roster import ROSTER
    from app.services.wecom_cli_service import _demo_password_hash, binding
    roster = {r["userid"]: r for r in ROSTER}
    result = []
    for u in db.scalars(select(SysUser)):
        sample = roster.get(u.wecom_userid)
        if not sample or u.wecom_userid == "admin" or any(r.role_code == "ADMIN-01" for r in u.roles): continue
        if (u.preferences or {}).get(MARKER) or binding(u.id): continue
        if u.name == sample["name"] and u.mobile == sample.get("mobile") and _demo_password_hash(u.password_hash, settings.demo_password):
            result.append({"id": u.id, "userid": u.wecom_userid, "name": u.name})
    return result


def _plan(db):
    _require_source(db)
    try: departments, users = get_wecom().get_addressbook()
    except Exception as error:
        code = getattr(error, "errcode", None)
        raise bad_request(f"公司通讯录读取未完成{f'（企业微信错误码 {code}）' if code is not None else ''}，请检查部门及成员信息读取权限、应用可见范围和服务器可信 IP；现有账号未改动") from None
    if not departments or not users: raise bad_request("接口未返回可用部门和成员，请检查应用可见范围与通讯录权限；没有改动现有账号")
    _, bound = _binding(db)
    mapping = bound.get("department_map", {})
    local_depts = {d.id: d for d in db.scalars(select(SysDepartment))}
    codes = {d.dept_code for d in local_depts.values()}
    planned_depts, seen_departments = [], set()
    for department in departments:
        d = asdict(department)
        if not isinstance(d["wecom_dept_id"], int) or d["wecom_dept_id"] <= 0: raise bad_request("企业微信部门编号无效，请核对接口返回")
        ident = str(d["wecom_dept_id"])
        if ident in seen_departments: continue
        seen_departments.add(ident)
        if not d["name"] or len(d["name"]) > 64: raise bad_request("部门名称不可用，请确认通讯录基本信息读取权限")
        row = local_depts.get(mapping.get(ident))
        code = row.dept_code if row else ""
        salt = 0
        while not code or code in codes and not row:
            code = "W" + _hash([settings.wecom_corp_id, ident, salt])[:7]; salt += 1
        codes.add(code)
        planned_depts.append({**d, "local_id": row.id if row else None, "code": code, "action": "update" if row else "create"})
    existing = {u.wecom_userid: u for u in db.scalars(select(SysUser))}
    retire = _demo_accounts(db)
    retiring = {u["id"] for u in retire}
    members, seen = [], set()
    for user in users:
        u = asdict(user)
        userid = str(u["userid"]).strip()
        if not userid or len(userid) > 64 or not u["name"] or len(u["name"]) > 64: raise bad_request("接口返回的员工 UserID 或姓名不可用，请检查读取权限")
        if userid in seen: continue
        seen.add(userid)
        for field, size in (("position", 64), ("mobile", 32), ("email", 128)):
            if u.get(field) and len(u[field]) > size: raise bad_request(f"员工 {userid} 的 {field} 超过系统字段长度，请核对源数据")
        if u["status"] not in (1, 2, 4, 5): raise bad_request("企业微信返回了无法识别的人员状态，请先核对")
        old = existing.get(userid)
        archived_demo = bool(old and old.id in retiring)
        if archived_demo: old = None
        marker = (old.preferences or {}).get(MARKER, {}) if old else {}
        if not isinstance(marker, dict): marker = {}
        protected = old and (old.wecom_userid == "admin" or any(r.role_code == "ADMIN-01" for r in old.roles))
        action = "conflict" if old and (protected or old.is_deleted or marker.get("corp_id") != settings.wecom_corp_id) else "update" if old else "create"
        reason = "保留现有管理员账号，需人工核对企业账号映射" if protected else "UserID 与尚未绑定本企业的本地账号冲突，请先核对身份" if action == "conflict" else ""
        dept_id = next((str(d) for d in u["dept_ids"] if str(d) in seen_departments), None)
        dept_name = next((d["name"] for d in planned_depts if str(d["wecom_dept_id"]) == dept_id), None)
        if archived_demo: reason = "同账号的演示记录将归档，另建独立的真实员工账号"
        members.append({**u, "userid": userid, "action": action, "reason": reason, "department_key": dept_id,
                        "department_name": dept_name, "account_status": 1 if u["status"] in (1, 4) else 2})
    return {"departments": planned_depts, "members": members, "retire_demo_users": retire,
            "counts": {"departments": len(planned_depts), "members": len(members), "archive_demo": len(retire), **{name: sum(m["action"] == name for m in members) for name in ("create", "update", "conflict")}},
            "warnings": ["只导入企业微信实际授权范围内返回的人员，未返回不等于离职，不会批量停用。",
                         "新员工按普通员工权限建档，使用企业微信扫码登录；企业微信密码不会被导入。",
                         "确认导入会归档已核实的虚构演示账号，保留其业务引用和现有管理员账号；真实员工另建独立账号。",
                         "其他本地账号与真实 UserID 冲突会跳过，需先核对身份。"]}


def preview(db, p):
    plan = _plan(db)
    preview_id = uuid.uuid4().hex
    record = {**plan, "preview_id": preview_id, "user_id": p.id, "corp_id": settings.wecom_corp_id,
              "source": _source(), "actor": _actor(p), "before": _before(db),
              "created_at": time.time(), "expires_at": time.time() + 1800, "state": "pending"}
    private_write(_path(preview_id), record)
    return _view(record)


def inspect_preview(preview_id, user_id):
    record = _read(preview_id, user_id)
    if record["expires_at"] < time.time(): raise conflict("通讯录预览已过期，请重新预览")
    return _view(record)


def _apply(db, plan, operator_id):
    from app.services.user_service import role_by_code
    row, bound = _binding(db)
    mapping = dict(bound.get("department_map", {}))
    for item in plan.get("retire_demo_users", []):
        user = db.get(SysUser, item["id"])
        if user and user.wecom_userid == item["userid"]:
            original = user.wecom_userid
            user.wecom_userid = f"demo_{user.id}_{original}"[:64]
            user.status, user.password_hash, user.access_token, user.access_token_hint = 2, None, None, None
            user.preferences = {**(user.preferences or {}), "archived_demo_userid": original}
    db.flush()
    for d in plan["departments"]:
        local = db.get(SysDepartment, d["local_id"]) if d["local_id"] else None
        if not local:
            local = SysDepartment(dept_code=d["code"], name=d["name"], wecom_dept_id=d["wecom_dept_id"])
            db.add(local); db.flush()
        local.name = d["name"]
        mapping[str(d["wecom_dept_id"])] = local.id
    for d in plan["departments"]:
        db.get(SysDepartment, mapping[str(d["wecom_dept_id"])]).parent_id = mapping.get(str(d["parent_id"])) if d["parent_id"] else None
    default_role = role_by_code(db, "EMP")
    for m in plan["members"]:
        if m["action"] == "conflict": continue
        user = db.scalar(select(SysUser).where(SysUser.wecom_userid == m["userid"]))
        if not user:
            user = SysUser(wecom_userid=m["userid"], name=m["name"], password_hash=None)
            db.add(user); db.flush()
            db.add(SysUserRole(user_id=user.id, role_id=default_role.id))
        user.name, user.status = m["name"], m["account_status"]
        if m["position"]: user.position = m["position"]
        # Upstream privacy settings can omit optional fields. Preserve known values.
        if m["department_key"]: user.department_id = mapping[m["department_key"]]
        if m["mobile"]: user.mobile = m["mobile"]
        if m["email"]: user.email = m["email"]
        user.preferences = {**(user.preferences or {}), MARKER: {"corp_id": settings.wecom_corp_id, "userid": m["userid"], "source_status": m["status"]}}
        if user.status != 1: user.access_token = user.access_token_hint = None
    if not row:
        row = SysDict(dict_type=MARKER, dict_key="primary"); db.add(row)
    row.dict_value = {"corp_id": settings.wecom_corp_id, "department_map": mapping, "last_sync_at": time.time()}
    counts = plan["counts"]
    result = {"status": "succeeded", "rows": counts["create"] + counts["update"], **counts,
              "message": f"公司人员建档完成：新增 {counts['create']}、更新 {counts['update']}、冲突跳过 {counts['conflict']}、演示账号归档 {counts['archive_demo']}；新员工默认普通员工权限"}
    log_op(db, operator_id, "directory.sync", "company_directory", settings.wecom_corp_id,
           after={"create": counts["create"], "update": counts["update"], "conflict": counts["conflict"]})
    db.commit()
    return result


def confirm(db, p, preview_id):
    with locked("company-directory-import"):
        record = _read(preview_id, p.id)
        if record["state"] == "executed": return {**record["result"], "reused": True}
        if record["state"] != "pending" or record["expires_at"] < time.time(): raise conflict("通讯录预览已失效，请重新预览")
        _require_source(db)
        if record["source"] != _source() or record["actor"] != _actor(p): raise conflict("企业授权或账号权限已变化，请重新预览")
        if record["before"] != _before(db): raise conflict("账号或部门资料在预览后发生变化，请重新预览后确认")
        # Reserve before writing so an interrupted response cannot cause a second import.
        record["state"] = "executing"; private_write(_path(preview_id), record)
        try:
            result = _apply(db, record, p.id)
        except Exception:
            db.rollback(); record["state"] = "failed"; private_write(_path(preview_id), record)
            raise
        record.update(state="executed", result=result); private_write(_path(preview_id), record)
        return result


def sync_confirmed_company(db):
    _require_source(db)
    if not _binding(db)[1]: raise bad_request("首次公司人员同步需要管理员先预览并确认")
    with locked("company-directory-import"):
        return _apply(db, _plan(db), None)


def managed_department_ids(db):
    return set(_binding(db)[1].get("department_map", {}).values())
