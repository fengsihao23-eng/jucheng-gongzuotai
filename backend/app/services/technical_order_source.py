"""从企微「项目工单」的流程节点提取统计所需的字段。"""
from datetime import datetime

from app.integration.wecom.base import ApprovalDto

TEMPLATE_NAME = "项目工单"
DETAIL_KEY = "technical_order"
ACTIVE_STATUSES = (1, 2)
HANDLING, APPROVAL = 3, 1
HANDLED, AGREED, TRANSFERRED = 14, 2, 4


def _actions(node: dict, action: int, since: int) -> list[tuple[int, str]]:
    timestamps = []
    for step in node.get("sub_node_list") or []:
        if step.get("sp_yj") != action:
            continue
        try:
            stamp = int(step.get("sptime") or 0)
        except (TypeError, ValueError):
            continue
        if stamp >= since and stamp > 0:
            timestamps.append((stamp, step.get("userid") or ""))
    return timestamps


def _performed(node: dict, action: int, since: int) -> tuple[int | None, str]:
    if node.get("sp_status") != action:
        return None, ""
    actions = _actions(node, action, since)
    # 或签取最早的有效操作，会签取最后一个完成的分支。
    choose = min if node.get("apv_rel") == 1 else max
    return choose(actions, default=(None, ""))


def snapshot(approval: ApprovalDto, checked_at: datetime) -> dict:
    raw = approval.raw or {}
    contents = (raw.get("apply_data") or {}).get("contents") or []
    owner_id = (approval.fields.get("工单负责人") or "").partition(",")[0].strip()
    # Contact 控件携带的姓名可用于通讯录尚未同步的历史负责人。
    owner_name = next((member.get("name") for field in contents
                       if field.get("control") == "Contact"
                       for title in (field.get("title") or []) if title.get("text") == "工单负责人"
                       for member in (field.get("value") or {}).get("members", [])
                       if member.get("userid") == owner_id), None)
    nodes = (raw.get("process_list") or {}).get("node_list") or []
    index = next((i for i, node in enumerate(nodes) if node.get("node_type") == HANDLING), None)
    handling = nodes[index] if index is not None else {}
    closing = next((node for node in nodes[index + 1:] if node.get("node_type") == APPROVAL),
                   {}) if index is not None else {}
    submitted = int(approval.apply_time.timestamp())
    accepted, handler = _performed(handling, HANDLED, submitted)
    completed, closer = _performed(closing, AGREED, accepted or submitted)
    transferred = min((stamp for stamp, userid in _actions(handling, TRANSFERRED, submitted)
                       if userid and userid == owner_id), default=None)
    return {
        "checked_at": checked_at.isoformat(),
        "owner_id": owner_id or handler,
        "owner_name": owner_name,
        "accepted_at": accepted,
        "completed_at": completed,
        "transferred_at": transferred,
        "handler_id": handler,
        "closer_id": closer,
        "has_handling": bool(handling) and (handling.get("sp_status") != HANDLED or accepted is not None),
        "has_closing": bool(closing) and (closing.get("sp_status") != AGREED or completed is not None),
    }
