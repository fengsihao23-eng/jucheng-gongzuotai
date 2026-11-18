"""Daily/weekly feedback deadlines on stable workbook rows, queued with delivery rechecks."""
from datetime import timedelta

from sqlalchemy import select

from app.core.audit import log_op
from app.core.deps import build_principal
from app.core.response import bad_request, conflict, forbidden
from app.core.timeutil import TZ, day_bounds, today
from app.models import (ContractWorkbook, SysNotify, SysUser, WorkbookCollectionProgress as Progress,
                        WorkbookCollectionReminder as Rule, WorkbookCollectionRequest as Followup)
from app.services import business_workbook as source, collection_followups as followups
from app.services.collection_service import can_request

NOTICE = "workbook_collection_overdue"
REF = "collection_reminder"


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
                          "revision": rule.revision, "request_id": req.id}
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
    rule.manager_ids, rule.updated_by, rule.revision = sorted(set(body.manager_ids)), p.id, rule.revision + 1
    db.add(rule)
    db.flush()
    log_op(db, p.id, "collections.reminder.settings", REF, rule.id,
           after={"enabled": rule.enabled, "cadence": rule.cadence, "recipient_id": recipient.id, "due_on": str(rule.due_on)})
    return info(db, wb, p, sheet, keys)


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
    for rule in db.scalars(select(Rule).where(Rule.enabled.is_(True))):
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
            notify_service.send(db, [user], NOTICE, "回款节点反馈已超时",
                f"{item['name']}：应于 {deadline} 反馈，目前仍未完成节点反馈。请记录进展与存在问题。",
                link=f"/collections/followups/{req.id}", ref_type=REF, ref_id=str(rule.id), defer=True,
                wecom_content="回款节点反馈已超时，请打开工作台查看合同并反馈进展。")
            queued += 1
    return {"rows": queued, "message": f"超时合同 {overdue} 份，已为负责人及管理者生成 {queued} 组提醒"}


def overdue_items(db, p):
    wb = source.active_source(db, p, "collections")
    if wb is None:
        return {"items": []}
    items = []
    for rule in db.scalars(select(Rule).where(Rule.workbook_id == wb.id, Rule.enabled.is_(True))):
        item = _active_item(db, rule)
        due = _deadline(db, rule)
        if item and today() > due:
            req = db.get(Followup, rule.request_id)
            owner = db.get(SysUser, req.recipient_id)
            items.append({"sheet_id": item["sheet_id"], "row": item["row"], "name": item["name"],
                          "recipient_name": owner.name if owner else "历史成员", "due_on": due,
                          "overdue_days": (today()-due).days, "cadence": rule.cadence})
    return {"items": sorted(items, key=lambda r:r["due_on"]), "checked_on": today()}
