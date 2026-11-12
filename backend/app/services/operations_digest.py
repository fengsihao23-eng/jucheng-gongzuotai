"""Period summaries generated from visible task records and timestamped feedback."""
from datetime import date, datetime
import re

from sqlalchemy import select

from app.core.response import bad_request
from app.core.timeutil import TZ, day_bounds, last_week, month_range, now, prev_month, today, week_range
from app.models import OpsTaskUpdate
from app.services import operations_service as ops


def period_summary(db, p, mode, period, project_id=None, mine=False):
    if not period:
        period = last_week() if mode == "week" else prev_month(today().strftime("%Y-%m"))
    pattern = r"20\d{2}-W\d{2}" if mode == "week" else r"20\d{2}-(0[1-9]|1[0-2])"
    if not re.fullmatch(pattern, period):
        raise bad_request("请选择有效周次或月份")
    try:
        start, end = week_range(period) if mode == "week" else month_range(period)
    except ValueError as exc:
        raise bad_request("请选择有效周次或月份") from exc
    if start > today():
        raise bad_request("不能汇总未来期间")
    end = min(end, today())
    if project_id is not None:
        ops.get_project(db, p, project_id)
    rows = [r for r in ops.tasks(db, p, project_id, mine) if r["approval_status"] == "approved"]
    end_exclusive = day_bounds(end)[1]
    start_at = day_bounds(start)[0]
    events = {}
    for event in db.scalars(select(OpsTaskUpdate).where(
        OpsTaskUpdate.task_id.in_([r["id"] for r in rows]), OpsTaskUpdate.created_at < end_exclusive,
        OpsTaskUpdate.kind.in_(("assigned", "feedback", "planned", "submitted", "review")))
        .order_by(OpsTaskUpdate.created_at, OpsTaskUpdate.id)):
        entry = events.setdefault(event.task_id, {})
        entry["first"] = entry.get("first", event.created_at)
        if event.kind == "feedback":
            entry["last"] = event
            if event.created_at >= start_at:
                entry["in_period"] = event
    result = []
    for row in rows:
        event = events.get(row["id"], {})
        completed_at = row["completed_at"]
        completed_on = None
        if completed_at:
            raw = datetime.fromisoformat(completed_at) if isinstance(completed_at, str) else completed_at
            completed_on = raw.astimezone(TZ).date()
        if completed_on and completed_on < start:
            continue
        feedback = event.get("in_period")
        first_on = event["first"].astimezone(TZ).date() if "first" in event else None
        done = bool(completed_on and start <= completed_on <= end)
        if not (done or feedback or (first_on and start <= first_on <= end) or
                (date.fromisoformat(row["due_date"]) <= end and (first_on is None or first_on <= end))):
            continue
        last = event.get("last")
        status = "done" if done else (last.status if last and last.status != "done" else "todo")
        if not done and row["due_date"] < str(end) and status != "blocked":
            label = "逾期未完成"
        else:
            label = ops.STATUS_LABELS[status]
        result.append({k: row[k] for k in ("id", "project_id", "project_name", "title", "owner_id", "owner_name", "due_date")} |
                      {"status": status, "status_label": label, "done": done, "feedback": feedback.content if feedback else "",
                       "feedback_at": feedback.created_at if feedback else None, "next_step": last.next_step if last else "",
                       "completed_on": completed_on if done else None,
                       "link": f"/operations/{row['project_id']}?task={row['id']}"})
    stats = {"total": len(result), "done": sum(r["done"] for r in result),
             "blocked": sum(r["status"] == "blocked" for r in result),
             "unreported": sum(not r["feedback"] for r in result)}
    return {"period": period, "start": start, "end": end, "items": result, "stats": stats, "generated_at": now(),
            "note": "任务按当前权限和归属展示；反馈及完成按所选期间的已登记记录统计，未反馈不推测进展。"}
