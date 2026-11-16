"""Revalidate queued operational alerts against current business state."""
from sqlalchemy import select, or_
from app.models import OpsTask, OpsProject, OpsTaskDiscussion, SysNotify, SysUser
from app.services import operations_policy as policy, operations_scope as scope

PROJECT_ASSIGNMENT = "operations_project_assignment"
TYPES = {PROJECT_ASSIGNMENT, "operations_assignment", "operations_reminder", "operations_request", "operations_discussion"}


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
    if notice.msg_type == "operations_assignment":
        return task.owner_id == p.id and task.status != "done" and task.approval_status == "approved"
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
        return target == p.id
    return False


def delivery_status(db, p, project, task):
    from app.services import operations_service as ops
    ids = [str(i) for i in db.scalars(select(OpsTaskDiscussion.id).where(OpsTaskDiscussion.task_id == task.id))]
    conditions = [SysNotify.ref_type.in_(("ops_task", "ops_task_reminder")) & or_(
        SysNotify.ref_id == str(task.id), SysNotify.ref_id.startswith(str(task.id) + ":"))]
    if ids:
        from sqlalchemy import func
        conditions.append(SysNotify.ref_type.in_(("ops_discussion", "ops_request_reminder")) & func.split_part(SysNotify.ref_id, ":", 1).in_(ids))
    q = select(SysNotify, SysUser.name).join(SysUser, SysUser.id == SysNotify.user_id).where(
        SysNotify.channel == "wecom_app", or_(*conditions))
    if ops.role(db, p, project) != "manager":
        q = q.where(SysNotify.user_id == p.id)
    return [{"id": n.id, "recipient": name, "title": n.title, "status": {0: "等待发送", 1: "企微已受理", 2: "投递失败/已停止"}.get(n.send_status),
             "sent_at": n.sent_at, "retry_count": n.retry_count, "error": n.error_msg} for n, name in db.execute(q.order_by(SysNotify.id.desc()).limit(20))]
