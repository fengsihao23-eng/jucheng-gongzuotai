"""Projects explicitly tracked as sporadic collections, with progress on their shared timeline."""
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.audit import log_op, snapshot
from app.core.deps import build_principal, owner_scope
from app.core.response import bad_request, conflict, forbidden, not_found
from app.models import RecordFollowup, SporadicCollectionProject as Project, SysUser
from app.services.collection_service import can_request

KIND = "sporadic-collection"
FIELDS = ["name", "customer", "owner_id", "expected_amount", "due_on", "requirements"]


def _scoped(p):
    return select(Project).where(owner_scope(Project.owner_id, p))


def get_project(db, p, project_id, *, lock=False):
    query = _scoped(p).where(Project.id == project_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    project = db.scalar(query)
    if not project:
        raise not_found("零星回款项目")
    return project


def _owner_query(p):
    return select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False),
                                 owner_scope(SysUser.id, p))


def _eligible(user):
    if not user:
        return False
    owner = build_principal(user)
    return owner.has_menu("contracts") or owner.has_menu("receivables")


def options(db, p):
    if not can_request(p):
        raise forbidden("无权登记零星回款项目")
    query = _owner_query(p).options(selectinload(SysUser.roles), selectinload(SysUser.permission_override))
    return [{"id": u.id, "name": u.name, "department": u.dept_code}
            for u in db.scalars(query.order_by(SysUser.name, SysUser.id)) if _eligible(u)]


def save(db, p, body, project_id=None):
    if not can_request(p):
        raise forbidden("无权维护零星回款项目")
    project = get_project(db, p, project_id, lock=True) if project_id is not None else None
    if project and project.revision != body.expected_revision:
        raise conflict("项目资料已更新，请重新打开后保存")
    owner = db.scalar(_owner_query(p).where(SysUser.id == body.owner_id))
    if not _eligible(owner):
        raise bad_request("请选择数据范围内有回款情况访问权限的在职负责人")
    before = snapshot(project, FIELDS) if project else None
    if not project:
        project = Project(revision=1)
    else:
        project.revision += 1
    for field in FIELDS:
        setattr(project, field, getattr(body, field))
    db.add(project)
    db.flush()
    log_op(db, p.id, "collections.sporadic_update" if before else "collections.sporadic_create",
           Project.__tablename__, project.id, before=before, after=snapshot(project, FIELDS))
    return {"id": project.id, **snapshot(project, FIELDS), "revision": project.revision}


def _rows(db, query):
    from app.services.record_followup_targets import record_key

    projects = db.execute(query.add_columns(SysUser.name).join(SysUser, SysUser.id == Project.owner_id)).all()
    if not projects:
        return []
    keys = {p.id: record_key(str(p.id)) for p, _ in projects}
    latest_ids = select(func.max(RecordFollowup.id)).where(RecordFollowup.target_kind == KIND,
        RecordFollowup.target_key.in_(keys.values()), RecordFollowup.action == "progress").group_by(RecordFollowup.target_key)
    latest = {r.target_key: r for r in db.scalars(select(RecordFollowup).where(RecordFollowup.id.in_(latest_ids)))}
    rows = []
    for project, owner in projects:
        progress = latest.get(keys[project.id])
        rows.append({"id": project.id, **snapshot(project, FIELDS), "owner_name": owner, "revision": project.revision,
                     "latest_feedback": snapshot(progress, ["status", "content", "problems", "next_step", "next_due_on", "created_at"])
                     if progress else None})
    return rows


def listing(db, p, *, keyword, page, size):
    query = _scoped(p)
    if keyword := keyword.strip():
        query = query.where(or_(Project.name.icontains(keyword, autoescape=True),
                                Project.customer.icontains(keyword, autoescape=True)))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    page = min(page, max(1, (total + size - 1) // size))
    items = _rows(db, query.order_by(Project.id.desc()).offset((page - 1) * size).limit(size))
    can_manage = can_request(p)
    return {"items": items, "total": total, "page": page, "size": size,
            "can_manage": can_manage, "can_delete": can_manage or p.has_delete_grant}


def report_rows(db, p):
    return _rows(db, _scoped(p).order_by(Project.due_on.asc().nulls_last(), Project.id.desc()))
