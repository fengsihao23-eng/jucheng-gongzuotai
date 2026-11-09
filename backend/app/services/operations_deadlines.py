"""按首次生效的截止日统计准时情况；延期复核独立于任务执行。"""
from sqlalchemy import select

from app.core.audit import log_op
from app.core.response import conflict, forbidden
from app.core.timeutil import TZ, now, today
from app.models import OpsTaskDelay
from app.services.operations_plans import company_reviewer

DEADLINE_LABELS = {"on_time": "按时完成", "late_done": "逾期完成", "overdue": "原定期限已过", "pending": "未到期",
                   "unknown_done": "完成日期待核对"}
LATE_STATUSES = {"overdue", "late_done"}


def baseline(task):
    return task.original_due_date or task.due_date


def status(task, day):
    if task.approval_status != "approved":
        return None
    due = baseline(task)
    if task.status == "done":
        if task.completed_at is None:
            return "unknown_done"
        return "on_time" if task.completed_at.astimezone(TZ).date() <= due else "late_done"
    return "overdue" if day > due else "pending"


def latest_by_task(db, task_ids):
    if not task_ids:
        return {}
    rows = db.scalars(select(OpsTaskDelay).where(OpsTaskDelay.task_id.in_(task_ids)).distinct(OpsTaskDelay.task_id)
                      .order_by(OpsTaskDelay.task_id, OpsTaskDelay.id.desc()))
    return {row.task_id: row for row in rows}


def history(db, task_id):
    return db.scalars(select(OpsTaskDelay).where(OpsTaskDelay.task_id == task_id).order_by(OpsTaskDelay.id.desc())).all()


def reason_dict(row):
    return {"id": row.id, "reason": row.reason, "author_id": row.author_id, "author_name": row.author.name,
            "created_at": row.created_at, "original_due_date": row.original_due_date,
            "decision": row.decision, "review_comment": row.review_comment, "reviewed_at": row.reviewed_at,
            "reviewer_name": row.reviewer.name if row.reviewer else None}


def can_explain(p, task, role):
    return role == "manager" or (role in {"member", "reviewer"} and p.id == task.owner_id)


def can_decide(p, task, row):
    return company_reviewer(p) and row is not None and row.decision is None and p.id not in {task.owner_id, row.author_id}


def view(p, task, role, row, day):
    outcome = status(task, day)
    late = outcome in LATE_STATUSES
    delay_status = (row.decision or "pending") if row else "needs_reason"
    return {"deadline_status": outcome, "deadline_basis": baseline(task) if outcome else None,
            "completed_on": task.completed_at.astimezone(TZ).date() if task.completed_at else None,
            "delay_status": delay_status if late else None,
            "can_explain_delay": late and can_explain(p, task, role),
            "can_review_delay": late and can_decide(p, task, row)}


def _create_reason(db, p, task, reason):
    row = OpsTaskDelay(task_id=task.id, author_id=p.id, reason=reason, original_due_date=baseline(task), created_at=now())
    db.add(row)
    db.flush()
    log_op(db, p.id, "operations.task.delay_reason", "ops_task", task.id,
           after={"reason_id": row.id, "original_due_date": row.original_due_date, "reason": row.reason})
    return row


def capture_feedback_reason(db, p, task, reason, day):
    # An ordinary progress update may supply the first explanation, but cannot replace a reviewable version.
    if reason and status(task, day) in LATE_STATUSES and task.id not in latest_by_task(db, [task.id]):
        _create_reason(db, p, task, reason)


def submit_reason(db, p, task_id, body):
    from app.services import operations_service as ops
    task = ops.get_task(db, p, task_id, lock=True)
    project = ops.get_project(db, p, task.project_id, lock=True)
    if not can_explain(p, task, ops.role(db, p, project)):
        raise forbidden("仅任务负责人或项目管理员可说明延期原因")
    if task.revision != body.expected_revision:
        raise conflict("任务已有更新，请核对后重新提交延期原因")
    if status(task, today()) not in LATE_STATUSES:
        raise conflict("这项任务没有超过原定期限，无需提交延期原因")
    latest = latest_by_task(db, [task.id]).get(task.id)
    if latest and latest.reason == body.reason:
        raise conflict("该原因已经登记；如有补充，请修改后提交")
    _create_reason(db, p, task, body.reason)
    task.revision += 1


def review_reason(db, p, task_id, body):
    from app.services import operations_service as ops
    task = ops.get_task(db, p, task_id, lock=True)
    if not company_reviewer(p) or task.owner_id == p.id:
        raise forbidden("仅有公司审核权限的其他人员可复核延期原因")
    row = latest_by_task(db, [task.id]).get(task.id)
    if task.revision != body.expected_revision or not row or row.id != body.reason_id or row.decision is not None:
        raise conflict("延期原因已变化或已复核，请查看最新记录")
    if not can_decide(p, task, row):
        raise forbidden("不能复核自己提交的延期原因")
    if status(task, today()) not in LATE_STATUSES:
        raise conflict("这项任务无需复核延期原因")
    row.reviewer_id, row.decision, row.review_comment, row.reviewed_at = p.id, body.decision, body.comment, now()
    task.revision += 1
    db.flush()
    db.expire(row, ["reviewer"])
    log_op(db, p.id, "operations.task.delay_review", "ops_task", task.id,
           after={"reason_id": row.id, "original_due_date": row.original_due_date, "decision": row.decision, "comment": row.review_comment})
