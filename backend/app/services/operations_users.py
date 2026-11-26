"""User lists scoped to one operations project."""
from sqlalchemy import select

from app.core.audit import snapshot
from app.core.response import conflict, not_found
from app.models import OpsProjectUser
from app.services import operations_scope as scope, operations_service as ops

FIELDS = ["category", "name", "contact", "contact_details", "note", "revision"]


def listing(db, p, project_id, category):
    project = ops.get_project(db, p, project_id)
    scope.require(db, p, project, "user_list")
    rows = db.scalars(select(OpsProjectUser).where(
        OpsProjectUser.project_id == project.id, OpsProjectUser.category == category,
    ).order_by(OpsProjectUser.name, OpsProjectUser.id))
    return {"items": [{"id": row.id, **snapshot(row, FIELDS)} for row in rows],
            "can_edit": ops.role(db, p, project) == "manager"}


def save(db, p, project_id, body, user_id=None):
    project = ops.get_project(db, p, project_id, lock=True)
    scope.require(db, p, project, "user_list")
    ops.manage(db, p, project)
    if user_id is None:
        row = OpsProjectUser(project_id=project.id)
        revision = 1
    else:
        row = db.scalar(select(OpsProjectUser).where(
            OpsProjectUser.id == user_id, OpsProjectUser.project_id == project.id))
        if row is None:
            raise not_found("清单用户")
        if row.revision != body.expected_revision:
            raise conflict("此条用户资料已更新，请重新打开后保存")
        revision = row.revision + 1
    ops._write(db, p, row, {**body.model_dump(exclude={"expected_revision"}), "revision": revision}, FIELDS)
    return {"id": row.id, **snapshot(row, FIELDS)}
