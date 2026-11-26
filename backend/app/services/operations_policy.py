"""Task visibility, scope changes and reversible archiving."""
from sqlalchemy import cast, delete, exists, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from app.core.audit import log_op
from app.core.response import bad_request, conflict, forbidden
from app.core.timeutil import now
from app.models import OpsMember, OpsMemberView, OpsProject, OpsProjectFeatures, OpsTask, OpsTaskAudience, OpsTaskPolicy, SysUser


def archived():
    return exists().where(OpsTaskPolicy.task_id == OpsTask.id, OpsTaskPolicy.archived_at.is_not(None))


def member_clause(p):
    return or_(OpsProject.owner_id == p.id, OpsProject.id.in_(
        select(OpsMember.project_id).where(OpsMember.user_id == p.id)))


def team_user_ids(project_id):
    return select(OpsMember.user_id).where(OpsMember.project_id == project_id).union(
        select(OpsProject.owner_id).where(OpsProject.id == project_id))


def task_access(p, *, archive=False, team_only=False):
    from app.services import operations_service as ops

    if not p.has_menu("operations") or (not p.has_section("operations", "tasks") and not ops.can_manage_all(p)):
        from sqlalchemy import false
        return false()
    disabled_projects = select(OpsProjectFeatures.project_id).where(~cast(OpsProjectFeatures.sections, JSONB).has_key("tasks"))
    projects = select(OpsProject.id).where(OpsProject.status != "deleted")
    if team_only or not ops.can_view_all(p):
        projects = projects.where(member_clause(p))
    base = ((archived() if archive else ~archived())
            & OpsTask.project_id.in_(projects)
            & OpsTask.project_id.not_in(disabled_projects))
    if ops.can_view_all(p):
        return base
    owner_project = select(OpsProject.id).where(OpsProject.owner_id == p.id)
    joined = select(OpsMember.project_id).where(OpsMember.user_id == p.id)
    managed = joined.where(OpsMember.role.in_(("manager", "co_owner")))
    restricted = exists().where(OpsTaskPolicy.task_id == OpsTask.id, OpsTaskPolicy.restricted.is_(True))
    selected = exists().where(OpsTaskAudience.task_id == OpsTask.id, OpsTaskAudience.user_id == p.id)
    participants = or_(selected, OpsTask.creator_id == p.id, OpsTask.owner_id == p.id,
                       OpsTask.department_reviewer_id == p.id, OpsTask.company_reviewer_id == p.id)
    self_only = exists().where(OpsMemberView.project_id == OpsTask.project_id, OpsMemberView.user_id == p.id,
                               OpsMemberView.task_scope == "self")
    managers = or_(OpsTask.project_id.in_(owner_project), OpsTask.project_id.in_(managed))
    return base & or_(~restricted & ~self_only, managers, participants)


def details(db, task_id):
    policy = db.get(OpsTaskPolicy, task_id)
    viewers = list(db.scalars(select(OpsTaskAudience.user_id).where(OpsTaskAudience.task_id == task_id)
                             .order_by(OpsTaskAudience.user_id))) if policy and policy.restricted else []
    return {"kind": policy.kind if policy else "general", "visibility": "selected" if policy and policy.restricted else "team",
            "viewer_ids": viewers, "archived_at": policy.archived_at if policy else None,
            "archive_reason": policy.archive_reason if policy else ""}


def update_policy(db, p, project, task, values):
    from app.services import operations_service as ops

    if not values:
        return
    before = details(db, task.id)
    visibility = values.get("visibility", before["visibility"])
    viewer_ids = set(values.get("viewer_ids", before["viewer_ids"]))
    kind = values.get("kind", before["kind"])
    if visibility == "team" and viewer_ids:
        raise bad_request("全项目组可见时无需指定人员")
    if kind == "outreach" and not task.school_id:
        raise bad_request("市场开拓任务须选择学校")
    if values.keys() & {"visibility", "viewer_ids"}:
        ops.manage(db, p, project)
        valid = {m["user_id"] for m in ops.members(db, project) if m["active"]}
        if not viewer_ids <= valid:
            raise bad_request("可见人员须为在职的本项目成员")
    row = db.get(OpsTaskPolicy, task.id) or OpsTaskPolicy(task_id=task.id)
    row.restricted, row.kind = visibility == "selected", kind
    db.add(row)
    db.execute(delete(OpsTaskAudience).where(OpsTaskAudience.task_id == task.id))
    db.add_all([OpsTaskAudience(task_id=task.id, user_id=uid) for uid in sorted(viewer_ids)])
    db.flush()
    log_op(db, p.id, "operations.task.scope", "ops_task", task.id, before=before, after=details(db, task.id))


def can_archive(db, p, project):
    from app.services import operations_service as ops

    return (p.is_admin or p.can("operations:archive")) and ops.role(db, p, project) == "manager"


def archive(db, p, task_id, reason, *, restore=False):
    from app.services import operations_service as ops

    task = ops.get_task(db, p, task_id, lock=True, archived=restore)
    project = ops.get_project(db, p, task.project_id)
    if not can_archive(db, p, project):
        raise forbidden("作废与恢复须单独授权")
    policy = db.get(OpsTaskPolicy, task.id) or OpsTaskPolicy(task_id=task.id)
    before = details(db, task.id)
    if restore:
        owner = db.get(SysUser, task.owner_id)
        if task.status != "done" and (not owner or not owner.status == 1 or owner.is_deleted or
            (project.owner_id != task.owner_id and db.scalar(select(OpsMember.role).where(
                OpsMember.project_id == project.id, OpsMember.user_id == task.owner_id)) not in ops.EXECUTION_ROLES)):
            raise conflict("原负责人已无法执行此任务，请先恢复其项目成员身份")
        policy.archived_at, policy.archived_by, policy.archive_reason = None, None, ""
    else:
        policy.archived_at, policy.archived_by, policy.archive_reason = now(), p.id, reason
    task.revision += 1
    db.add(policy)
    db.flush()
    log_op(db, p.id, "operations.task.restore" if restore else "operations.task.archive", "ops_task", task.id,
           before=before, after=details(db, task.id))


def batch_details(db, ids):
    if not ids:
        return {}
    rows = {r.task_id: {"kind": r.kind, "visibility": "selected" if r.restricted else "team", "viewer_ids": [],
                        "archived_at": r.archived_at, "archive_reason": r.archive_reason}
            for r in db.scalars(select(OpsTaskPolicy).where(OpsTaskPolicy.task_id.in_(ids)))}
    selected = [key for key, item in rows.items() if item["visibility"] == "selected"]
    if selected:
        for task_id, user_id in db.execute(select(OpsTaskAudience.task_id, OpsTaskAudience.user_id)
                                           .where(OpsTaskAudience.task_id.in_(selected)).order_by(OpsTaskAudience.user_id)):
            rows[task_id]["viewer_ids"].append(user_id)
    return rows
