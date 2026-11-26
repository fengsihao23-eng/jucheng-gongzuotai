from copy import deepcopy
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.deps import build_principal
from app.models import OpsProjectIncome, SysNotify, SysUser, WorkbookProjectFeedback
from app.services import collection_reminders as reminders
from app.services import record_followup_targets, record_followups
from app.services.workbook_project_feedback import ProjectArchiveIn
from app.services.xiaoju_business import BusinessExecutor
from tests.conftest import data
from tests.test_business_workbook import market_source, source_only  # noqa: F401
from tests.test_collection_reminders import reminder_source, rich_workbook  # noqa: F401
from tests.test_contract_workbook import tender

BASE = "/api/v1/business-workbook"


@pytest.fixture
def tracked_book(db, market_source):
    wb, _, _ = market_source
    wb.sheets = [*wb.sheets, tender()]
    db.commit()
    yield wb
    db.execute(delete(WorkbookProjectFeedback).where(WorkbookProjectFeedback.workbook_id == wb.id))
    db.commit()


@pytest.mark.parametrize("view,sheet,group,archive_row,category", [
    ("opportunities", "market", 3, 3, "competitor"),
    ("tender", "tender-sheet", 2, 3, "converted"),
])
def test_archive_requires_current_completion_and_follows_source_identity(client, login, db, tracked_book,
                                                                    view, sheet, group, archive_row, category):
    wb = tracked_book
    admin = login("admin")
    location = {"sheet": sheet, "revision": wb.revision}
    feedback = f"{BASE}/{view}/rows/{sheet}/{group}/feedback"
    record = f"/api/v1/record-followups/workbook-{view}/{archive_row}"
    archive = record + "/archive"
    doing = data(client.post(feedback, headers=admin, json={"revision": wb.revision, "content": "正在协调"}))
    first_id = doing["items"][0]["id"]
    body = {"category": category, "expected_latest_id": first_id}
    assert not doing["can_archive"]
    assert client.put(archive, headers=admin, params=location, json=body).status_code == 409

    initial = data(client.get(record, headers=admin, params=location))
    done = data(client.post(record, headers=admin, params=location, json={"action": "progress", "status": "done",
        "content": "已确认处理结果", "client_id": str(uuid4()), "expected_progress": initial["progress_revision"]}))
    assert done["can_archive"]
    assert client.put(archive, headers=admin, params=location, json=body).status_code == 409
    body["expected_latest_id"] = done["archive_feedback_id"]
    assert client.put(archive, headers=admin, params=location, json={**body, "category": "other"}).status_code == 400

    member = db.scalar(select(SysUser).where(SysUser.wecom_userid == "xiekai"))
    viewer = build_principal(member)
    viewer.menus.add("opportunities" if view == "opportunities" else "projects")
    viewer.buttons.clear()
    wb.config = {**wb.config, "view_user_ids": [member.id]}
    db.commit()
    target = record_followup_targets.resolve(db, viewer, f"workbook-{view}", {**location, "id": str(archive_row)})
    from app.core.response import BizError
    with pytest.raises(BizError) as denied:
        record_followups.archive(db, viewer, target, ProjectArchiveIn(**body))
    assert denied.value.http_status == 403
    db.rollback()

    result = data(client.put(archive, headers=admin, params=location, json=body))
    assert result["archive"]["category"] == category and not result["can_feedback"]
    assert data(client.put(archive, headers=admin, params=location, json=body))["archive"] == result["archive"]
    assert client.put(archive, headers=admin, params=location, json={**body, "category": "canceled"}).status_code == 409
    inline = data(client.get(feedback, headers=admin, params={"revision": wb.revision}))
    assert inline["archive"] == result["archive"] and not inline["can_write"]
    assert [entry["status"] for entry in inline["items"]] == ["done", "doing"]
    assert client.post(feedback, headers=admin, json={"revision": wb.revision,
        "expected_latest_id": body["expected_latest_id"], "content": "重复写入"}).status_code == 409
    assert client.post(record, headers=admin, params=location, json={"action": "progress", "status": "doing",
        "content": "重新开始", "client_id": str(uuid4()), "expected_progress": result["progress_revision"]}).status_code == 409

    archived = data(client.get(f"{BASE}/{view}", headers=admin, params={"sheet": sheet, "archive_category": category}))
    assert {item["group_row"] for item in archived["items"]} == {group}
    assert all(item["archive_category"] == category for item in archived["items"])
    active = data(client.get(f"{BASE}/{view}", headers=admin, params={"sheet": sheet, "archive_category": "unarchived"}))
    assert all(item["group_row"] != group for item in active["items"])

    changed = deepcopy(wb.sheets)
    original = next(s for s in changed if s["id"] == sheet)
    original["merges"] = []
    next(row for row in original["rows"] if row["row"] == group)["row"] = 19
    wb.sheets, wb.revision = changed, "archive-moved"
    db.commit()
    assert client.put(archive, headers=admin, params=location, json=body).status_code == 409
    moved = feedback.replace(f"/{group}/feedback", "/19/feedback")
    assert data(client.get(moved, headers=admin, params={"revision": wb.revision}))["archive"]["category"] == category


@pytest.mark.parametrize("view,sheet,row", [("opportunities", "market", 3), ("tender", "tender-sheet", 2)])
def test_tracking_follows_moved_project_and_rejects_stale_or_read_only_writes(client, login, db, tracked_book, view, sheet, row):
    wb = tracked_book
    url = f"{BASE}/{view}/rows/{sheet}/{row}/feedback"
    admin = login("admin")
    body = {"revision": wb.revision, "status": "blocked", "content": "材料正在核对", "problems": "待签认", "next_step": "复核资料"}
    added = data(client.post(url, headers=admin, json=body))["items"][0]
    assert added["content"] == body["content"]
    assert client.post(url, headers=admin, json=body).status_code == 409
    member = db.scalar(select(SysUser).where(SysUser.wecom_userid == "xiekai"))
    assert client.get(url, headers=login("xiekai"), params={"revision": wb.revision}).status_code == 403
    if view == "tender":
        child = url.replace("/2/feedback", "/3/feedback")
        assert data(client.get(child, headers=admin, params={"revision": wb.revision}))["items"] == [added]
    # Same authorized source, with module read permissions but no write button.
    viewer = build_principal(member)
    viewer.menus.add("projects" if view == "tender" else "opportunities")
    viewer.buttons.clear()
    wb.config = {**wb.config, "view_user_ids": [member.id]}
    db.commit()
    from app.core.response import BizError
    from app.api.business_workbook import ProjectFeedbackIn
    from app.services.workbook_project_feedback import history
    assert not history(db, viewer, view, sheet, row, wb.revision)["can_write"]
    with pytest.raises(BizError) as denied:
        history(db, viewer, view, sheet, row, wb.revision, body=ProjectFeedbackIn(**body))
    assert denied.value.http_status == 403
    db.rollback()
    changed = deepcopy(wb.sheets)
    target = next(s for s in changed if s["id"] == sheet)
    target["merges"] = []
    next(r for r in target["rows"] if r["row"] == row)["row"] = 19
    wb.sheets, wb.revision = changed, "moved"
    db.commit()
    assert client.post(url, headers=admin, json=body).status_code == 409
    moved = url.replace(f"/{row}/feedback", "/19/feedback")
    assert data(client.get(moved, headers=admin, params={"revision": "moved"}))["items"] == [added]


def test_payment_deadline_survives_feedback_until_balance_settles(client, login, db, reminder_source):
    wb, worker = reminder_source
    admin = login("admin")
    path = BASE + "/collections/items/sales-sheet/3"
    rule = data(client.put(path + "/reminder", headers=admin, json={"recipient_id": worker.id, "revision": wb.revision,
        "expected_revision": 0, "due_on": "2026-11-25", "payment_due_on": "2026-11-18"}))["rule"]
    assert reminders.scan(db)["rows"] == 1
    db.commit()
    assert reminders.scan(db)["rows"] == 0
    entries = list(db.scalars(select(SysNotify).where(SysNotify.msg_type == reminders.PAYMENT_NOTICE,
                                                        SysNotify.ref_id == str(rule["request_id"]))))
    assert {n.channel for n in entries} == {"inapp", "wecom_app"}
    assert all(reminders.eligible(db, n) for n in entries)
    reply_path = BASE + f"/collection-followups/{rule['request_id']}/reply"
    payload = {"stage": "awaiting", "content": "等待通知"}
    assert client.post(reply_path, headers=login("xiekai"), json=payload).status_code == 400
    assert client.post(path + "/execution", headers=admin, json={**payload, "revision": wb.revision}).status_code == 400
    data(client.post(reply_path, headers=login("xiekai"), json={**payload, "problems": "收款账户核验中"}))
    assert not any(reminders.eligible(db, n) for n in entries)
    listed = data(client.get(BASE + "/collections/reminders/overdue", headers=admin))["items"]
    assert listed[0]["kind"] == "payment" and listed[0]["reported_today"]
    changed = deepcopy(wb.sheets)
    next(r for r in changed[0]["rows"] if r["row"] == 3)["cells"][9] = 0
    wb.sheets = changed
    db.commit()
    assert data(client.get(BASE + "/collections/reminders/overdue", headers=admin))["items"] == []
    db.execute(delete(SysNotify).where(SysNotify.msg_type == reminders.PAYMENT_NOTICE))
    db.commit()


def test_estimate_without_rule_and_agent_pagination(client, login, db, rich_workbook):
    wb, _ = rich_workbook
    path = BASE + "/collections/items/sales-sheet/3/execution"
    data(client.post(path, headers=login("admin"), json={"revision": wb.revision, "content": "等待客户确认",
        "expected_on": "2026-11-17", "stage": "awaiting", "next_step": "补充资料"}))
    assert data(client.get(BASE + "/collections/reminders/overdue", headers=login("admin")))["items"][0]["kind"] == "payment"
    api = BASE + "/collections/progress-brief"
    pages = [data(client.get(api, params={"size": 2, "page": i}, headers=login("admin"))) for i in (1, 2, 3)]
    assert [p["next_page"] for p in pages] == [2, 3, None]
    assert len({r["id"] for page in pages for r in page["items"]}) == pages[0]["total"] == 5
    assert client.get(api, headers=login("xiekai")).status_code == 403
    actor = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "admin"))
    tool = BusinessExecutor(actor, "brief-qa", read_only=True, intent="逐笔回款汇报")
    result = tool.execute("business_workbook.collection_progress_brief", {"query": {"project_name": "智慧校园一期项目"}})
    assert result["ok"] and "等待客户确认" in str(result)


def test_actual_month_does_not_count_estimates_or_other_months(client, login, db, rich_workbook):
    wb, _ = rich_workbook
    sheets = deepcopy(wb.sheets)
    # Each monthly progress column has its own reconciliation total.
    for sheet, total in zip(sheets, (2000, 20000)):
        sheet["rows"][-1]["cells"][11] = total
    sheets[0]["rows"][2]["cells"][11] = "11月2日已回款2000元；10月31日已回款100元"
    wb.sheets = sheets
    db.commit()
    current = data(client.get(BASE + "/collections", headers=login("admin"), params={"basis": "current", "unpaid_only": True}))
    assert current["total"] == current["current_receipts"]["count"] == 2
    assert Decimal(current["summary"]["received"]["value"]) == Decimal("22000")
    assert all(date.fromisoformat(row["date"]).month == 11 for row in current["items"])
    assert len(current["collection_cards"]) == 2


def test_digest_scopes_income_progress_and_saved_versions(client, login, db, monkeypatch):
    monkeypatch.setenv("FAKE_TODAY", "2026-11-22")
    admin, employee = login("admin"), login("xiekai")
    owner, worker = (db.scalar(select(SysUser.id).where(SysUser.wecom_userid == uid)) for uid in ("admin", "xiekai"))
    base = "/api/v1/operations"
    project = data(client.post(base, headers=admin, json={"name": "综合汇报口径验收", "owner_id": owner,
        "enabled_sections": ["tasks", "team", "income", "progress", "reports", "outreach"]}))
    route = base + "/" + str(project["id"])
    data(client.post(route + "/members", headers=admin, json={"user_id": worker, "role": "member"}))
    school = data(client.post(route + "/schools", headers=admin, json={"name": "营收试点学校"}))
    for day, amount in [("2026-11-19", 80), ("2026-11-10", 1000)]:
        data(client.post(route + f"/schools/{school['id']}/income", headers=admin,
                         json={"income_date": day, "amount": amount, "source": "验收登记"}))
    db.add(OpsProjectIncome(project_id=project["id"], operator_id=owner, source="公共收入",
                            income_date=date(2026, 11, 20), amount=Decimal("9.50")))
    db.commit()
    data(client.post(route + "/progress", headers=admin, json={"progress": "确认交付", "problems": "资料待补"}))
    params = {"project_id": project["id"], "mode": "week", "period": "2026-W47"}
    saved = data(client.post(base + "/digest/reports", headers=admin, json=params))
    assert saved["facts"]["project_income"][0]["amount"] == "89.50"
    assert "确认交付" in saved["text"] and {"income", "progress"} <= set(saved["facts"]["content_sections"])
    data(client.put(route + f"/member-visibility/{worker}", headers=admin,
                    json={"sections": ["tasks", "reports"], "expected_revision": 0}))
    restricted = data(client.get(base + "/digest", headers=employee, params=params))
    assert restricted["project_income"] == restricted["project_progress"] == []
    assert client.get(base + f"/digest/reports/{saved['id']}", headers=employee).status_code == 403
