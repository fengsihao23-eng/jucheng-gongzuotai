"""项目工单月度统计。全部时间按北京时间归月和计时。"""
from collections import defaultdict
from datetime import datetime, time, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.permissions import TECHNICAL_DEPARTMENT
from app.core.response import bad_request
from app.core.timeutil import TZ, month_range, now
from app.models import SyncWecomApproval, SysUser
from app.services.technical_order_source import ACTIVE_STATUSES, DETAIL_KEY, TEMPLATE_NAME

RESPONSE_LIMIT = timedelta(hours=3)
COMPLETION_LIMIT = timedelta(hours=24)
START_OF_DAY = time(8, 30)
END_OF_DAY = time(18)


def response_deadline(submitted: datetime) -> datetime:
    submitted = submitted.astimezone(TZ)
    morning = datetime.combine(submitted.date(), START_OF_DAY, TZ)
    evening = datetime.combine(submitted.date(), END_OF_DAY, TZ)
    remaining = max(timedelta(0), evening - max(morning, submitted))
    if remaining >= RESPONSE_LIMIT:
        return max(morning, submitted) + RESPONSE_LIMIT
    return morning + timedelta(days=1) + RESPONSE_LIMIT - remaining


def _at(stamp: int | None) -> datetime | None:
    return datetime.fromtimestamp(stamp, TZ) if stamp else None


def _timing(at: datetime | None, due: datetime, as_of: datetime) -> str:
    if at is not None:
        return "on_time" if at <= due else "late"
    return "overdue" if as_of > due else "pending"


def _item(row: SyncWecomApproval, names: dict[str, str], as_of: datetime) -> dict:
    payload = row.detail or {}
    detail = payload.get(DETAIL_KEY) or {}
    fields = payload.get("fields") or {}
    submitted = row.apply_time.astimezone(TZ)
    accepted = _at(detail.get("accepted_at"))
    completed = _at(detail.get("completed_at"))
    transferred = _at(detail.get("transferred_at"))
    responded = min((at for at in (accepted, transferred) if at), default=None)
    response_due = response_deadline(submitted)
    completion_due = accepted + COMPLETION_LIMIT if accepted else None
    owner = detail.get("owner_id") or (fields.get("工单负责人") or "").partition(",")[0].strip()
    if completed:
        stage = "completed"
    elif detail.get("has_handling") and detail.get("has_closing") and row.status == 1:
        stage = "in_progress" if accepted else "waiting"
    else:
        stage = "unverified"
    response = (_timing(responded, response_due, as_of)
                if responded or (detail.get("has_handling") and row.status == 1) else "unknown")
    completion = "not_started" if stage == "waiting" else "unknown"
    if completion_due and stage != "unverified":
        completion = _timing(completed, completion_due, as_of)
    return {
        "sp_no": row.sp_no,
        "subject": fields.get("工单主题") or TEMPLATE_NAME,
        "project": fields.get("其他（项目名称）") or fields.get("所属项目") or "",
        "owner_id": owner,
        "owner_name": names.get(owner) or detail.get("owner_name") or owner or "未识别负责人",
        "submitted_at": submitted,
        "accepted_at": accepted,
        "completed_at": completed,
        "transferred_at": transferred,
        "accepted_by": names.get(detail.get("handler_id")) or detail.get("handler_id"),
        "completed_by": names.get(detail.get("closer_id")) or detail.get("closer_id"),
        "response_due": response_due,
        "completion_due": completion_due,
        "response": response,
        "completion": completion,
        "stage": stage,
        "checked_at": detail.get("checked_at"),
    }


def _totals(submitted: list[dict], completed: int) -> dict:
    order_count = len(submitted)
    timely_response = sum(row["response"] == "on_time" for row in submitted)
    timely_completion = sum(row["completion"] == "on_time" for row in submitted)
    due_count = sum(row["completion"] in {"on_time", "late", "overdue"} for row in submitted)
    return {
        "order_count": order_count,
        "accepted_count": sum(row["accepted_at"] is not None for row in submitted),
        "completed_count": completed,
        "timely_response_count": timely_response,
        "timely_completion_count": timely_completion,
        "due_count": due_count,
        "response_rate": timely_response / order_count if order_count else None,
        "completion_rate": timely_completion / due_count if due_count else None,
    }


def report(db: Session, principal: Principal, month: str | None, owner: str | None, stage: str | None,
           keyword: str | None, page: int, page_size: int) -> dict:
    as_of = now()
    month = month or as_of.strftime("%Y-%m")
    try:
        first, last = month_range(month)
        start = datetime.combine(first, time.min, TZ)
        end = datetime.combine(last + timedelta(days=1), time.min, TZ)
    except (ValueError, OverflowError):
        raise bad_request("请选择有效的统计月份") from None
    team_view = principal.is_admin or principal.can("technical_orders:team")
    approval = SyncWecomApproval
    completed_at = approval.detail[DETAIL_KEY]["completed_at"].as_integer()
    query = select(approval).where(
        approval.sp_name == TEMPLATE_NAME, approval.status.in_(ACTIVE_STATUSES),
        or_(and_(approval.apply_time >= start, approval.apply_time < end),
            and_(completed_at >= int(start.timestamp()), completed_at < int(end.timestamp()))),
    )
    users = db.scalars(select(SysUser).where(SysUser.is_deleted.is_(False))).unique().all()
    names = {u.wecom_userid: u.name for u in users}
    submitted = defaultdict(list)
    completed = defaultdict(int)
    items = []
    for row in db.scalars(query.order_by(approval.apply_time.desc(), approval.id.desc())):
        item = _item(row, names, as_of)
        key = item["owner_id"]
        if not team_view and key != principal.user.wecom_userid:
            continue
        items.append(item)
        if start <= item["submitted_at"] < end:
            submitted[key].append(item)
        if item["completed_at"] and start <= item["completed_at"] < end:
            completed[key] += 1
    assigned = [item for rows in submitted.values() for item in rows]
    after_sales = None
    if team_view:
        roster = sorted((u for u in users if u.status == 1 and u.position == "售后工程师"
                         and u.department and u.department.name == TECHNICAL_DEPARTMENT), key=lambda u: u.name)
        group_total = sum(completed[u.wecom_userid] for u in roster)
        average = group_total / len(roster) if roster else None
        people = []
        for user in roster:
            count = completed[user.wecom_userid]
            people.append({"owner_id": user.wecom_userid, "owner_name": user.name,
                           **_totals(submitted[user.wecom_userid], count),
                           "average_rate": count / average if average else None})
        after_sales = {"headcount": len(roster), "completed": group_total, "average": average, "people": people}

    owners = {item["owner_id"]: item["owner_name"] for item in items if item["owner_id"]}
    term = (keyword or "").strip().casefold()
    filtered = [item for item in items if (owner is None or item["owner_id"] == owner)
                and (not stage or item["stage"] == stage)
                and (not term or any(term in item[key].casefold() for key in ("sp_no", "subject", "project")))]
    return {
        "month": month,
        "source_mode": settings.source_mode("wecom"),
        "last_synced_at": max((item["checked_at"] for item in items if item["checked_at"]), default=None),
        "totals": _totals(assigned, sum(completed.values())),
        "unverified_count": sum(item["stage"] == "unverified" for item in assigned),
        "after_sales": after_sales,
        "owners": [{"id": key, "name": name} for key, name in sorted(owners.items(), key=lambda item: item[1])],
        "total": len(filtered),
        "items": filtered[(page - 1) * page_size:page * page_size],
    }
