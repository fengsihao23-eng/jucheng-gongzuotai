"""One feedback deadline, shared by task displays and the WeCom reminder queue."""
from datetime import timezone

from sqlalchemy import select

from app.core.timeutil import TZ, now
from app.models import SysNotify
from app.services import notify_service

OPS_OVERDUE = "operations_feedback_overdue"
KEY_OVERDUE = "key_task_feedback_overdue"


def set_deadline(row, due_at, *, reassigned=False):
    if row.feedback_due_at != due_at or reassigned:
        row.feedback_due_at = due_at
        row.feedback_requested_at = now() if due_at else None


def waiting(row, last_feedback_at):
    return bool(row and row.feedback_due_at and row.feedback_requested_at and
                (not last_feedback_at or last_feedback_at < row.feedback_requested_at))


def overdue(row, last_feedback_at, at=None):
    return waiting(row, last_feedback_at) and row.feedback_due_at < (at or now())


def view(row, last_feedback_at, recurring_on, *, active, at=None):
    at = at or now()
    day = at.astimezone(TZ).date()
    pending = waiting(row, last_feedback_at)
    due_at = row.feedback_due_at if row else None
    due_on = due_at.astimezone(TZ).date() if pending else recurring_on
    status = None
    if active and pending:
        status = "overdue" if due_at < at else "pending"
    elif active and due_on and due_on <= day:
        status = "overdue" if due_on < day else "pending"
    return {"feedback_due_at": due_at, "feedback_due_on": due_on, "feedback_status": status,
            "feedback_deadline_pending": active and pending,
            "needs_feedback": bool(active and due_on and due_on <= day)}


def reference(task_id, row):
    return f"{task_id}:{row.feedback_requested_at.astimezone(timezone.utc).isoformat(timespec='microseconds')}"


def deadline_text(due_at):
    return due_at.astimezone(TZ).strftime("%Y-%m-%d %H:%M") + "（北京时间）"


def queue_overdue(db, owner, *, kind, ref_type, task_id, row, title, task_title, link, wecom=True):
    ref_id = reference(task_id, row)
    if db.scalar(select(SysNotify.id).where(SysNotify.user_id == owner.id, SysNotify.msg_type == kind,
        SysNotify.ref_type == ref_type, SysNotify.ref_id == ref_id).limit(1)):
        return []
    prompt = f"反馈截止 {deadline_text(row.feedback_due_at)}，请打开工作台填写进展。"
    return notify_service.send(db, [owner], kind, title, f"{task_title}\n{prompt}", link=link,
        ref_type=ref_type, ref_id=ref_id, wecom=wecom, defer=True, wecom_content=prompt)
