"""Revalidate queued operational alerts against current business state."""
from sqlalchemy import select, or_
from app.core.deps import build_principal
from app.models import OpsTask, OpsProject, OpsTaskDiscussion, SysNotify
from app.services import notify_service, task_feedback_deadlines as deadlines
from app.services import operations_policy as policy, operations_scope as scope

PROJECT_ASSIGNMENT = "operations_project_assignment"
TASK_ASSIGNMENT = "operations_assignment"
TYPES = {PROJECT_ASSIGNMENT, TASK_ASSIGNMENT, deadlines.OPS_OVERDUE, "operations_reminder", "operations_request", "operations_discussion"}


def project_assignment_access(p):
    return (OpsProject.owner_id == p.id) & (OpsProject.status != "deleted") & p.has_menu("operations")


def needs_escalation(task, day):
    return task.status == "blocked" or task.due_date < day or any(
        not node.completed_at and node.due_date < day for node in task.nodes)


def eligible(db, p, notice):
    from app.services import operations_service as ops
    if not p.has_menu("operations"):
        return False
    try:
        ident = int(str(notice.ref_id).split(":")[0])
    except (TypeError, ValueError):
        return False
    if notice.msg_type == PROJECT_ASSIGNMENT:
        project_id = db.scalar(select(OpsProject.id).where(OpsProject.id == ident, project_assignment_access(p)))
        return bool(project_id and scope.controls(db, project_id)["wecom_reminders"])
    discussion = None
    if notice.ref_type in {"ops_discussion", "ops_request_reminder"}:
        discussion = db.get(OpsTaskDiscussion, ident)
        if not discussion:
            return False
        ident = discussion.task_id
    task = db.scalar(select(OpsTask).where(OpsTask.id == ident, policy.task_access(p)))
    if not task:
        return False
    project = db.get(OpsProject, task.project_id)
    if not project or project.status != "active" or not scope.controls(db, project.id)["wecom_reminders"]:
        return False
    if notice.msg_type == TASK_ASSIGNMENT:
        return task.owner_id == p.id and task.status != "done" and task.approval_status == "approved"
    if notice.msg_type == deadlines.OPS_OVERDUE:
        return (task.owner_id == p.id and task.status != "done" and ops.can_feedback(db, p, project, task)
                and deadlines.overdue(task, task.last_feedback_at) and notice.ref_id == deadlines.reference(task.id, task))
    if notice.msg_type == "operations_reminder":
        if task.status == "done" or task.approval_status != "approved":
            return False
        if p.id == task.owner_id:
            return ops.can_feedback(db, p, project, task)
        return p.id == project.owner_id and needs_escalation(task, ops.today())
    if discussion:
        if discussion.kind == "request":
            return (discussion.recipient_id == p.id and not discussion.resolved_at and task.status != "done"
                    and ops.role(db, p, project) in {"manager", "member", "reviewer"})
        target = discussion.recipient_id
        if discussion.kind == "reply":
            parent = db.get(OpsTaskDiscussion, discussion.reply_to_id)
            target = parent.author_id if parent else None
        elif discussion.kind == "note" and target is None:
            from app.services.operations_followup import default_note_recipient_ids

            return p.id in default_note_recipient_ids(project, task, discussion.author_id)
        return target == p.id
    return False


def scan_feedback_deadlines(db, at):
    from app.services.operations_service import can_feedback

    tasks = db.execute(select(OpsTask, OpsProject).join(OpsProject, OpsProject.id == OpsTask.project_id).where(
        OpsProject.status == "active", OpsTask.status != "done", OpsTask.approval_status == "approved", ~policy.archived(),
        OpsTask.feedback_due_at < at, OpsTask.feedback_requested_at.is_not(None),
        or_(OpsTask.last_feedback_at.is_(None), OpsTask.last_feedback_at < OpsTask.feedback_requested_at)))
    notices = []
    for task, project in tasks:
        owner = task.owner
        principal = build_principal(owner)
        if not principal.has_menu("operations") or not can_feedback(db, principal, project, task):
            continue
        if not db.scalar(select(OpsTask.id).where(OpsTask.id == task.id, policy.task_access(principal))):
            continue
        notices.extend(deadlines.queue_overdue(db, owner, kind=deadlines.OPS_OVERDUE, ref_type="ops_task_reminder",
            task_id=task.id, row=task, title="运营任务逾期未反馈", task_title=f"{project.name} · {task.title}",
            link=f"/operations/{project.id}?task={task.id}", wecom=scope.controls(db, project.id)["wecom_reminders"]))
    return notices


def delivery_status(db, p, project, task):
    from app.services import operations_service as ops
    ids = [str(i) for i in db.scalars(select(OpsTaskDiscussion.id).where(OpsTaskDiscussion.task_id == task.id))]
    conditions = [SysNotify.ref_type.in_(("ops_task", "ops_task_reminder")) & or_(
        SysNotify.ref_id == str(task.id), SysNotify.ref_id.startswith(str(task.id) + ":"))]
    if ids:
        from sqlalchemy import func
        conditions.append(SysNotify.ref_type.in_(("ops_discussion", "ops_request_reminder")) & func.split_part(SysNotify.ref_id, ":", 1).in_(ids))
    return notify_service.delivery_status(db, or_(*conditions), user_id=None if ops.role(db, p, project) == "manager" else p.id)
