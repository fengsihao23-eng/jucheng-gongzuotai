"""Daily/weekly feedback deadlines on stable workbook rows, queued with delivery rechecks."""
from datetime import timedelta

from sqlalchemy import or_, select, text

from app.core.audit import log_op
from app.core.deps import build_principal
from app.core.response import bad_request, conflict, forbidden
from app.core.timeutil import TZ, day_bounds, now, today
from app.models import (ContractWorkbook, SysNotify, SysUser, WorkbookCollectionProgress as Progress,
                        WorkbookCollectionReminder as Rule, WorkbookCollectionRequest as Followup)
from app.services import business_workbook as source, collection_followups as followups
from app.services.collection_service import can_request

NOTICE = "workbook_collection_overdue"
REF = "collection_reminder"
PAYMENT_NOTICE = "workbook_payment_due"


def _deadline(db, rule):
    request = db.get(Followup, rule.request_id)
    progress = db.scalar(select(Progress).join(Followup, Progress.request_id == Followup.id).where(
        Followup.workbook_id == rule.workbook_id, Followup.sheet_id == rule.sheet_id, Followup.row_key == rule.row_key,
        Progress.author_id == request.recipient_id, Progress.created_at >= rule.updated_at)
        .order_by(Progress.id.desc()).limit(1)) if request else None
    return max(rule.due_on, progress.created_at.astimezone(TZ).date() + timedelta(days=1 if rule.cadence == "daily" else 7)) if progress else rule.due_on


def find(db, wb, sheet, keys):
    return db.scalar(select(Rule).where(Rule.workbook_id == wb.id, Rule.sheet_id == sheet, Rule.row_key.in_(keys))) if keys else None


def info(db, wb, p, sheet, keys):
    rule = find(db, wb, sheet, keys)
    managers = [u for u in db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False)))
                if followups._approved_user(u) and can_request(build_principal(u)) and source.can_access_view(build_principal(u), wb.config, "collections")]
    result = {"can_manage": can_request(p), "managers": [{"id": u.id, "name": u.name} for u in managers] if can_request(p) else [],
              "recipients": [{"id": u.id, "name": u.name} for u in db.scalars(select(SysUser).where(
                  SysUser.status == 1, SysUser.is_deleted.is_(False)).order_by(SysUser.name)) if followups._approved_user(u)] if can_request(p) else [],
              "rule": None}
    if rule:
        req = db.get(Followup, rule.request_id)
        due = _deadline(db, rule)
        owner = db.get(SysUser, req.recipient_id)
        result["rule"] = {"id": rule.id, "enabled": rule.enabled, "cadence": rule.cadence, "due_on": rule.due_on,
                          "next_due_on": due, "overdue": rule.enabled and today() > due, "manager_ids": rule.manager_ids,
                          "recipient_id": req.recipient_id, "recipient_name": owner.name if owner else "历史成员",
                          "revision": rule.revision, "request_id": req.id, "payment_due_on": rule.payment_due_on}
    due = payment_node(db, wb, sheet, keys, rule)
    result["payment_due_on"] = due["due_on"] if due else None
    result["payment_due"] = bool(due and due["due_on"] <= today())
    return result


def save(db, p, sheet, row_number, body):
    if not can_request(p):
        raise forbidden("仅回款管理者可设置自动提醒")
    wb = followups._current(db, p, body.revision, lock=True)
    item, keys = followups._sheet_item(wb, sheet, row_number)
    if not keys:
        raise conflict("合同无法唯一识别，不能设置提醒")
    rule = find(db, wb, sheet, keys)
    if body.expected_revision != (rule.revision if rule else 0):
        raise conflict("提醒设置已更新，请刷新后重试")
    recipient = db.get(SysUser, body.recipient_id)
    if not followups._approved_user(recipient):
        raise bad_request("请选择有效的公司负责人")
    valid_managers = {r["id"] for r in info(db, wb, p, sheet, keys)["managers"]}
    if set(body.manager_ids) - valid_managers:
        raise bad_request("管理员提醒对象须具有当前回款管理权限")
    pending = db.scalar(followups._row_query(wb, sheet, keys).where(
        Followup.cancelled_at.is_(None), Followup.replied_at.is_(None)))
    if pending and pending.recipient_id != recipient.id:
        raise conflict("该合同已有其他负责人待回复，请先处理或撤回原跟进要求")
    req = db.get(Followup, rule.request_id) if rule else None
    if not req or req.cancelled_at or req.recipient_id != recipient.id:
        req = pending or Followup(workbook_id=wb.id, sheet_id=sheet, row_key=keys[0], contract_name=item["name"],
                                  author_id=p.id, recipient_id=recipient.id, content="自动节点反馈：请记录回款进展、存在问题及下一步")
        db.add(req)
        db.flush()
    rule = rule or Rule(workbook_id=wb.id, sheet_id=sheet, row_key=keys[0], revision=0)
    rule.request_id, rule.cadence, rule.due_on, rule.enabled = req.id, body.cadence, body.due_on, body.enabled
    if "payment_due_on" in body.model_fields_set:
        rule.payment_due_on = body.payment_due_on
    rule.manager_ids, rule.updated_by, rule.revision = sorted(set(body.manager_ids)), p.id, rule.revision + 1
    db.add(rule)
    db.flush()
    log_op(db, p.id, "collections.reminder.settings", REF, rule.id,
           after={"enabled": rule.enabled, "cadence": rule.cadence, "recipient_id": recipient.id, "due_on": str(rule.due_on)})
    return info(db, wb, p, sheet, keys)


def payment_node(db, wb, sheet_id, keys, rule=None):
    """An explicit contract deadline takes precedence over the latest follow-up estimate."""
    if not keys:
        return None
    rule = rule or find(db, wb, sheet_id, keys)
    latest = db.scalar(followups._row_query(wb, sheet_id, keys).order_by(Followup.id.desc()).limit(1))
    req = db.get(Followup, rule.request_id) if rule else latest
    if not req or req.cancelled_at or (rule and not rule.enabled):
        return None
    if rule and rule.payment_due_on:
        return {"due_on": rule.payment_due_on, "request": req, "managers": rule.manager_ids}
    estimate = db.scalar(select(Progress.expected_on).join(Followup, Progress.request_id == Followup.id).where(
        Followup.workbook_id == wb.id, Followup.sheet_id == sheet_id, Followup.row_key.in_(keys),
        Followup.cancelled_at.is_(None), Progress.expected_on.is_not(None)).order_by(Progress.id.desc()).limit(1))
    due = estimate or req.expected_on
    if not due:
        return None
    return {"due_on": due, "request": req, "managers": rule.manager_ids if rule else []}


def _payment_nodes(db, wb, candidates=None):
    """Resolve only contracts with a rule or a recorded payment estimate in the current snapshot."""
    from app.services.collection_workbook import state
    if candidates is None:
        candidates = set(db.execute(select(Rule.sheet_id, Rule.row_key).where(Rule.workbook_id == wb.id)).all())
        candidates.update(db.execute(select(Followup.sheet_id, Followup.row_key).where(
            Followup.workbook_id == wb.id, Followup.cancelled_at.is_(None), Followup.expected_on.is_not(None))).all())
        candidates.update(db.execute(select(Followup.sheet_id, Followup.row_key).join(Progress, Progress.request_id == Followup.id)
            .where(Followup.workbook_id == wb.id, Followup.cancelled_at.is_(None), Progress.expected_on.is_not(None)).distinct()).all())
    if not candidates:
        return {}
    sheets = {s.id: s for s in source.collection_sheets(wb)}
    nodes = {}
    for item in source.module_rows(wb, "collections"):
        sheet = sheets.get(item["sheet_id"])
        if not sheet or state(item) != "open":
            continue
        keys = followups._keys(sheet.rows[item["row"]].get("record"))
        if not any((sheet.id, key) in candidates for key in keys):
            continue
        node = payment_node(db, wb, sheet.id, keys)
        if node and node["due_on"] <= today():
            nodes[node["request"].id] = {**node, "item": item}
    return nodes


def request_payment_due(db, req):
    wb = source.configured_source(db)
    if not wb or wb.id != req.workbook_id:
        return None
    node = _payment_nodes(db, wb, {(req.sheet_id, req.row_key)}).get(req.id)
    return node["due_on"] if node else None


def _needs_payment_report(db, node):
    start, end = day_bounds(today())
    req = node["request"]
    return not db.scalar(select(Progress.id).where(Progress.request_id == req.id, Progress.author_id == req.recipient_id,
        Progress.created_at >= start, Progress.created_at < end,
        or_(Progress.problems != "", Progress.stage == "reported_paid")).limit(1))


def _payment_recipient(db, wb, node, uid):
    user = db.get(SysUser, uid)
    if not followups._approved_user(user) or not user.workbench_enabled:
        return False
    principal = build_principal(user)
    return uid == node["request"].recipient_id or (
        uid in node["managers"] and can_request(principal) and source.can_access_view(principal, wb.config, "collections"))


def _active_item(db, rule):
    from app.services.collection_workbook import state
    wb = source.configured_source(db)
    req = db.get(Followup, rule.request_id)
    if not rule.enabled or not wb or wb.id != rule.workbook_id or not req or req.cancelled_at:
        return None
    matches = []
    for sheet in source.collection_sheets(wb):
        if sheet.id != rule.sheet_id:
            continue
        for item in sheet.items:
            if rule.row_key in followups._keys(sheet.rows[item["row"]].get("record")):
                matches.append(item)
    if len(matches) != 1:
        return None
    adjusted = next((r for r in source.module_rows(wb, "collections") if r["id"] == matches[0]["id"]), None)
    return adjusted if adjusted and state(adjusted) == "open" else None


def eligible(db, notice):
    if notice.msg_type == PAYMENT_NOTICE:
        wb = source.configured_source(db)
        req = db.get(Followup, int(notice.ref_id)) if str(notice.ref_id).isdigit() else None
        node = _payment_nodes(db, wb, {(req.sheet_id, req.row_key)}).get(req.id) if req and wb and req.workbook_id == wb.id else None
        return bool(node and _payment_recipient(db, wb, node, notice.user_id) and _needs_payment_report(db, node))
    try:
        rule = db.get(Rule, int(notice.ref_id))
    except (TypeError, ValueError):
        return False
    if not rule or not _active_item(db, rule) or today() <= _deadline(db, rule):
        return False
    req, u = db.get(Followup, rule.request_id), db.get(SysUser, notice.user_id)
    if not followups._approved_user(u):
        return False
    if notice.user_id == req.recipient_id:
        return True
    wb = db.get(ContractWorkbook, rule.workbook_id)
    p = build_principal(u)
    return notice.user_id in rule.manager_ids and can_request(p) and source.can_access_view(p, wb.config, "collections")


def scan(db):
    from app.services import notify_service
    queued, overdue = 0, 0
    start, end = day_bounds(today())
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('collection_feedback_reminders'))"))
    wb = source.configured_source(db)
    nodes = _payment_nodes(db, wb) if wb else {}
    for ident, node in nodes.items():
        if not _needs_payment_report(db, node):
            continue
        req = node["request"]
        for uid in {req.recipient_id, *node["managers"]}:
            already = db.scalar(select(SysNotify.id).where(SysNotify.user_id == uid, SysNotify.msg_type == PAYMENT_NOTICE,
                SysNotify.ref_id == str(ident), SysNotify.created_at >= start, SysNotify.created_at < end).limit(1))
            if already or not _payment_recipient(db, wb, node, uid):
                continue
            notices = notify_service.send(db, [db.get(SysUser, uid)], PAYMENT_NOTICE, "回款到期仍有未收款项",
                f"{node['item']['name']} · 回款日 {node['due_on']}，未收 {node['item']['outstanding']} 元。请汇报跟进情况、未收原因和下一步。",
                link=f"/collections/followups/{ident}", ref_type=followups.REF_TYPE, ref_id=str(ident), defer=True,
                wecom_content="有合同到期未收款，请打开工作台填写跟进情况、未收原因和下一步。")
            for notice in notices:
                notice.created_at = now()
            queued += 1
    for rule in db.scalars(select(Rule).where(Rule.enabled.is_(True))):
        if rule.request_id in nodes:
            continue
        item = _active_item(db, rule)
        deadline = _deadline(db, rule)
        if not item or today() <= deadline:
            continue
        overdue += 1
        req = db.get(Followup, rule.request_id)
        for uid in {req.recipient_id, *rule.manager_ids}:
            probe = SysNotify(user_id=uid, ref_type=REF, ref_id=str(rule.id), msg_type=NOTICE)
            if not eligible(db, probe) or db.scalar(select(SysNotify.id).where(
                SysNotify.user_id == uid, SysNotify.msg_type == NOTICE, SysNotify.ref_id == str(rule.id),
                SysNotify.created_at >= start, SysNotify.created_at < end).limit(1)):
                continue
            user = db.get(SysUser, uid)
            notices = notify_service.send(db, [user], NOTICE, "回款节点反馈已超时",
                f"{item['name']}：应于 {deadline} 反馈，目前仍未完成节点反馈。请记录进展与存在问题。",
                link=f"/collections/followups/{req.id}", ref_type=REF, ref_id=str(rule.id), defer=True,
                wecom_content="回款节点反馈已超时，请打开工作台查看合同并反馈进展。")
            for notice in notices:
                notice.created_at = now()
            queued += 1
    return {"rows": queued, "message": f"到期未收 {len(nodes)} 份，反馈超时 {overdue} 份，共生成 {queued} 组提醒"}


def overdue_items(db, p):
    wb = source.active_source(db, p, "collections")
    if wb is None:
        return {"items": []}
    nodes = _payment_nodes(db, wb)
    items = []
    for node in nodes.values():
        item, req = node["item"], node["request"]
        owner = db.get(SysUser, req.recipient_id)
        items.append({"sheet_id": item["sheet_id"], "row": item["row"], "name": item["name"], "kind": "payment",
                      "recipient_name": owner.name if owner else "历史成员", "due_on": node["due_on"],
                      "overdue_days": (today() - node["due_on"]).days, "outstanding": item["outstanding"],
                      "reported_today": not _needs_payment_report(db, node)})
    for rule in db.scalars(select(Rule).where(Rule.workbook_id == wb.id, Rule.enabled.is_(True))):
        if rule.request_id in nodes:
            continue
        item = _active_item(db, rule)
        due = _deadline(db, rule)
        if item and today() > due:
            req = db.get(Followup, rule.request_id)
            owner = db.get(SysUser, req.recipient_id)
            items.append({"sheet_id": item["sheet_id"], "row": item["row"], "name": item["name"],
                          "recipient_name": owner.name if owner else "历史成员", "due_on": due,
                          "overdue_days": (today()-due).days, "cadence": rule.cadence})
    return {"items": sorted(items, key=lambda r:r["due_on"]), "checked_on": today()}
