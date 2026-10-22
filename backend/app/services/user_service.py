"""账号体系：通讯录同步（FR-01-02）、首次登录默认角色（FR-01-06）。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import DEPT_DEFAULT_ROLE, ROLE_DEFS
from app.core.config import settings
from app.integration.wecom import get_wecom
from app.models import SysDepartment, SysRole, SysUser, SysUserRole


def role_by_code(db: Session, code: str) -> SysRole:
    role = db.scalar(select(SysRole).where(SysRole.role_code == code))
    if not role:
        d = ROLE_DEFS[code]
        role = SysRole(role_code=code, name=d["name"], dept_code=d["dept"], permissions={})
        db.add(role)
        db.flush()
    return role


def ensure_default_role(db: Session, user: SysUser) -> None:
    if user.roles:
        return
    code = DEPT_DEFAULT_ROLE.get(user.dept_code or "", "EMP")
    db.add(SysUserRole(user_id=user.id, role_id=role_by_code(db, code).id))
    db.flush()
    db.refresh(user)


def sync_addressbook(db: Session) -> dict:
    """真实源只更新已确认企业；模拟源只用于非生产演示。"""
    from app.services import company_directory_service as directory
    if settings.source_mode("wecom") == "live": return directory.sync_confirmed_company(db)
    if settings.environment == "prod": return {"rows": 0, "message": "公司通讯录未授权，已跳过模拟人员同步"}
    depts, users = get_wecom().get_addressbook()
    managed = directory.managed_department_ids(db)
    by_wid = {d.wecom_dept_id: d for d in db.scalars(select(SysDepartment)).all() if d.id not in managed}
    created_d = 0
    for d in depts:
        row = by_wid.get(d.wecom_dept_id)
        if row:
            row.name = d.name
        elif d.parent_id is not None:  # 根部门（公司）不建档
            row = SysDepartment(dept_code=f"D{d.wecom_dept_id}", name=d.name, wecom_dept_id=d.wecom_dept_id)
            db.add(row)
            by_wid[d.wecom_dept_id] = row
            created_d += 1
    db.flush()
    existing = {u.wecom_userid: u for u in db.scalars(select(SysUser)).all()}
    created = updated = disabled = 0
    seen = set()
    for u in users:
        seen.add(u.userid)
        dept = next((by_wid[i] for i in u.dept_ids if i in by_wid), None)
        row = existing.get(u.userid)
        if row and (row.preferences or {}).get(directory.MARKER): continue
        active = u.status in (1, 4)
        if not row:
            row = SysUser(wecom_userid=u.userid, name=u.name, department_id=dept.id if dept else None,
                          position=u.position, mobile=u.mobile, email=u.email, status=1 if active else 2)
            db.add(row)
            created += 1
            continue
        if not active and row.status == 1:
            disabled += 1
        row.name, row.position = u.name, u.position or row.position
        row.mobile, row.email = u.mobile or row.mobile, u.email or row.email
        row.department_id = dept.id if dept else row.department_id
        row.status = 1 if active else 2
        if not active: row.access_token = row.access_token_hint = None
        updated += 1
    db.commit()
    return {"rows": created + updated, "message": f"部门新增 {created_d}；成员新增 {created}、更新 {updated}、停用 {disabled}",
            "created": created, "updated": updated, "disabled": disabled}
