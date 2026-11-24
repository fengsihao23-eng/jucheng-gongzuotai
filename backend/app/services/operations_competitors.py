"""Competitor notes belong to one operations project and its team."""
from sqlalchemy import select

from app.core.audit import snapshot
from app.core.response import conflict, forbidden, not_found
from app.models import OpsCompetitor, OpsMember, SysUser
from app.services import operations_scope as scope, operations_service as ops

FIELDS = ["name", "details", "response", "updated_by", "revision"]


def _can_edit(db, p, project):
    if project.owner_id == p.id:
        return True
    member_role = db.scalar(select(OpsMember.role).where(
        OpsMember.project_id == project.id, OpsMember.user_id == p.id))
    return member_role in ops.EXECUTION_ROLES


def _item(row, editor):
    return {"id": row.id, **snapshot(row, FIELDS), "updated_at": row.updated_at, "editor_name": editor}


def listing(db, p, project_id):
    project = ops.get_project(db, p, project_id)
    scope.require(db, p, project, "competitors")
    query = (select(OpsCompetitor, SysUser.name).join(SysUser, SysUser.id == OpsCompetitor.updated_by)
             .where(OpsCompetitor.project_id == project.id).order_by(OpsCompetitor.updated_at.desc(), OpsCompetitor.id.desc()))
    return {"items": [_item(row, name) for row, name in db.execute(query)], "can_edit": _can_edit(db, p, project)}


def save(db, p, project_id, body, competitor_id=None):
    project = ops.get_project(db, p, project_id, lock=True)
    scope.require(db, p, project, "competitors")
    if not _can_edit(db, p, project):
        raise forbidden("仅本项目有编辑权限的团队成员可维护对手信息")
    if competitor_id is None:
        row = OpsCompetitor(project_id=project.id)
        revision = 1
    else:
        row = db.scalar(select(OpsCompetitor).where(OpsCompetitor.id == competitor_id, OpsCompetitor.project_id == project.id))
        if not row:
            raise not_found("对手信息")
        if row.revision != body.expected_revision:
            raise conflict("对手信息已更新，请重新打开后保存")
        revision = row.revision + 1
    values = body.model_dump(exclude={"expected_revision"})
    ops._write(db, p, row, {**values, "updated_by": p.id, "revision": revision}, FIELDS)
    return _item(row, p.user.name)
