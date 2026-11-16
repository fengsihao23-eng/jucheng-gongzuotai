"""Meeting iteration acceptance: real role boundaries and state transitions."""
from datetime import date, timedelta
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import select

from app.core.timeutil import now, today
from app.models import (BizContract, OpsDigestSnapshot, OpsMember, OpsSchool, OpsTask, OpsTaskDiscussion,
                        OpsTaskNodeUpdate, SysNotify, SysUser)
from app.services.operations_followup import cadence_view
from conftest import data

BASE = "/api/v1/operations"


def uid(db, name):
    return db.scalar(select(SysUser.id).where(SysUser.wecom_userid == name))


@pytest.fixture
def setup(client, login, db):
    admin = login("admin")
    project = data(client.post(BASE, headers=admin, json={"name": "会议迭代 " + uuid.uuid4().hex[:8], "owner_id": uid(db, "wujun")}))
    for name, role in (("xiekai", "member"), ("xuhao", "member"), ("yangyue", "viewer")):
        data(client.post(f"{BASE}/{project['id']}/members", headers=admin, json={"user_id": uid(db, name), "role": role}))
    school = data(client.post(f"{BASE}/{project['id']}/schools", headers=admin, json={"name": "试点目标学校"}))
    task = data(client.post(f"{BASE}/{project['id']}/tasks", headers=admin, json={
        "title": "关键对接任务", "owner_id": uid(db, "xiekai"), "school_id": school["id"], "kind": "outreach",
        "due_date": "2026-10-30", "cadence": "every_3_days",
        "nodes": [{"name": "确认需求", "due_date": "2026-10-25"}, {"name": "完成对接", "due_date": "2026-10-30"}]}))
    return project, school, task


def test_node_evidence_permissions_and_completion(client, login, db, setup):
    project, school, task = setup
    path = f"{BASE}/tasks/{task['id']}"
    body = {"content": "需求已确认", "status": "doing", "node_id": task["nodes"][0]["id"],
            "completed_node_ids": [task["nodes"][0]["id"]], "expected_revision": task["revision"]}
    assert client.post(path + "/feedback", headers=login("xuhao"), json=body).status_code == 403
    assert client.post(path + "/feedback", headers=login("xiekai"), json=body | {"node_id": 9999999}).status_code == 400
    result = data(client.post(path + "/feedback", headers=login("xiekai"), json=body))
    event = result["updates"][0]
    assert event["node_id"] == task["nodes"][0]["id"] and result["progress"] == 50
    assert db.get(OpsTaskNodeUpdate, event["id"]).node_id == event["node_id"]
    assert client.post(path + "/feedback", headers=login("xiekai"), json=body).status_code == 409
    assert client.post(path + "/feedback", headers=login("xiekai"), json={"content": "完成", "status": "done"}).status_code == 400
    result = data(client.post(path + "/feedback", headers=login("xiekai"), json={"content": "对接完成", "status": "done",
        "node_id": task["nodes"][1]["id"], "completed_node_ids": [task["nodes"][1]["id"]]}))
    assert result["status"] == "done" and not result["needs_update"] and len(result["updates"]) == 3
    assert client.get(path, headers=login("chenjing")).status_code == 404


@pytest.mark.parametrize("cadence,days", [("daily", 1), ("every_2_days", 2), ("every_3_days", 3), ("weekly", 7)])
def test_cadence_follows_effective_feedback(client, login, db, setup, cadence, days):
    project, school, task = setup
    row = db.get(OpsTask, task["id"])
    row.cadence = cadence
    row.last_feedback_at = now() - timedelta(days=days - 1)
    db.commit()
    path = f"{BASE}/tasks/{row.id}"
    assert data(client.get(path, headers=login("xiekai")))["needs_update"] is False
    row.last_feedback_at = now() - timedelta(days=days)
    db.commit()
    result = data(client.get(path, headers=login("xiekai")))
    assert result["needs_update"] and result["next_feedback_on"] == str(today())
    data(client.post(path + "/feedback", headers=login("xiekai"), json={"status": "doing", "content": "已完成本轮跟进"}))
    result = data(client.get(path, headers=login("xiekai")))
    assert not result["needs_update"] and result["next_feedback_on"] == str(today() + timedelta(days=days))


def test_requirement_reply_is_not_progress_and_unread_is_not_todo(client, login, db, setup):
    project, school, task = setup
    path = f"{BASE}/tasks/{task['id']}"
    body = {"kind": "request", "content": "请确认校方接口清单", "recipient_id": uid(db, "xiekai"), "node_id": task["nodes"][0]["id"]}
    assert client.post(path + "/discussions", headers=login("xuhao"), json=body).status_code == 403
    assert client.post(path + "/discussions", headers=login("admin"), json=body | {"recipient_id": uid(db, "chenjing")}).status_code == 403
    result = data(client.post(path + "/discussions", headers=login("admin"), json=body))
    request = result["discussions"][0]
    assert result["last_feedback_at"] == task["last_feedback_at"] and result["revision"] == task["revision"]
    data(client.post("/api/v1/notifications/read", headers=login("xiekai"), json={}))
    todos = data(client.get(BASE, headers=login("xiekai")))["todos"]
    assert next(r for r in todos if r["id"] == task["id"])["pending_replies"] == 1
    reply = {"kind": "reply", "reply_to_id": request["id"], "content": "已收到，今天与校方确认"}
    assert client.post(path + "/discussions", headers=login("xuhao"), json=reply).status_code == 403
    result = data(client.post(path + "/discussions", headers=login("xiekai"), json=reply))
    assert result["discussions"][0]["resolved_at"] and result["status"] == "todo" and result["last_feedback_at"] is None
    assert client.post(path + "/discussions", headers=login("xiekai"), json=reply).status_code == 409
    assert next(r for r in data(client.get(BASE, headers=login("xiekai")))["todos"] if r["id"] == task["id"])["pending_replies"] == 0
    assert client.post(path + "/discussions", headers=login("yangyue"), json={"content": "改动"}).status_code == 403


def test_reminders_dedupe_pause_completion_and_request_reply(client, login, db, setup):
    from app.jobs.operations import operations_task_reminder
    project, school, task = setup
    row = db.get(OpsTask, task["id"])
    row.last_feedback_at = now() - timedelta(days=4)
    db.commit()
    path = f"{BASE}/tasks/{task['id']}"
    data(client.post(path + "/discussions", headers=login("admin"), json={"kind": "request", "content": "回复确认",
        "recipient_id": uid(db, "xuhao")}))
    operations_task_reminder(db, {})
    reminders = select(SysNotify.id).where(SysNotify.ref_type == "ops_task_reminder", SysNotify.ref_id.startswith(f"{task['id']}:{today()}:"))
    before = list(db.scalars(reminders))
    assert before
    operations_task_reminder(db, {})
    assert list(db.scalars(reminders)) == before
    from app.models import OpsProject
    db.get(OpsProject, project["id"]).status = "paused"
    db.commit()
    assert not data(client.get(path, headers=login("xiekai")))["needs_update"]


def test_visits_and_transition_keep_identity_and_real_date(client, login, db, setup):
    project, school, task = setup
    path = f"{BASE}/{project['id']}/schools/{school['id']}"
    body = {"occurred_on": "2026-10-19", "content": "拜访校方，待确认设备清单", "next_action": "提交清单", "next_contact_date": "2026-10-22"}
    assert client.post(path + "/visits", headers=login("yangyue"), json=body).status_code == 403
    assert client.post(path + "/visits", headers=login("xiekai"), json=body | {"occurred_on": "2026-10-21"}).status_code == 400
    data(client.post(path + "/visits", headers=login("xiekai"), json=body))
    item = data(client.get(path + "/visits", headers=login("xiekai")))["items"][0]
    assert item["occurred_on"] == "2026-10-19" and item["created_at"].startswith("2026-10-20")
    transition = {"stage": "operating", "expected_stage": "contact", "reason": "已核对学校上线情况"}
    assert client.post(path + "/transition", headers=login("xiekai"), json=transition).status_code == 403
    result = data(client.post(path + "/transition", headers=login("wujun"), json=transition))
    assert result["id"] == school["id"] and result["stage"] == "operating"
    assert client.post(path + "/transition", headers=login("wujun"), json=transition).status_code == 409
    assert len(data(client.get(path + "/visits", headers=login("wujun")))["items"]) == 1
    assert db.get(OpsTask, task["id"]).school_id == school["id"]


def test_reports_include_all_feedback_are_versioned_and_recheck_access(client, login, db, setup):
    project, school, task = setup
    path = f"{BASE}/tasks/{task['id']}"
    for i, content in enumerate(("第一轮确认需求", "第二轮对接方案")):
        data(client.post(path + "/feedback", headers=login("xiekai"), json={"content": content, "status": "doing", "node_id": task["nodes"][i]["id"]}))
    body = {"project_id": project["id"], "mode": "week", "period": "2026-W43", "mine": True}
    first = data(client.post(BASE + "/digest/reports", headers=login("xiekai"), json=body))
    assert all(text in first["text"] for text in ("第一轮确认需求", "第二轮对接方案"))
    assert len(first["facts"]["items"][0]["feedback_events"]) == 2 and first["version"] == 1
    again = data(client.post(BASE + "/digest/reports", headers=login("xiekai"), json=body))
    assert again["id"] == first["id"]
    data(client.post(path + "/feedback", headers=login("xiekai"), json={"content": "第三轮补齐材料", "status": "doing"}))
    newer = data(client.post(BASE + "/digest/reports", headers=login("xiekai"), json=body | {"new_version": True}))
    assert newer["version"] == 2 and "第三轮补齐材料" in newer["text"]
    assert "第三轮补齐材料" not in data(client.get(BASE + f"/digest/reports/{first['id']}", headers=login("xiekai")))["text"]
    assert client.get(BASE + f"/digest/reports/{first['id']}", headers=login("xuhao")).status_code == 403
    data(client.put(path, headers=login("admin"), json={"owner_id": uid(db, "xuhao"), "visibility": "selected", "viewer_ids": []}))
    assert client.get(BASE + f"/digest/reports/{first['id']}", headers=login("xiekai")).status_code == 403


def test_automatic_reports_and_schedule_optout(client, login, db, setup, monkeypatch):
    from app.jobs.operations import generate_period_reports
    project, school, task = setup
    monkeypatch.setenv("FAKE_TODAY", "2026-10-26")
    generate_period_reports(db, "week")
    q = select(OpsDigestSnapshot).where(OpsDigestSnapshot.project_id == project["id"], OpsDigestSnapshot.period == "2026-W43")
    reports = db.scalars(q).all()
    assert any(r.audience == "project" for r in reports)
    assert {r.owner_id for r in reports if r.audience == "personal"} >= {uid(db, "wujun"), uid(db, "xiekai"), uid(db, "xuhao")}
    assert all(r.owner_id != uid(db, "yangyue") for r in reports)
    generate_period_reports(db, "week")
    assert len(db.scalars(q).all()) == len(reports)
    assert client.put(f"{BASE}/{project['id']}/digest-settings", headers=login("xiekai"), json={"weekly": False, "monthly": False}).status_code == 403
    data(client.put(f"{BASE}/{project['id']}/digest-settings", headers=login("wujun"), json={"weekly": False, "monthly": False}))
    generate_period_reports(db, "month")
    assert not db.scalar(select(OpsDigestSnapshot.id).where(OpsDigestSnapshot.project_id == project["id"], OpsDigestSnapshot.mode == "month"))


def test_contract_year_cards_list_and_export_share_filters(client, login, db):
    import io
    from openpyxl import load_workbook
    marker = "YEAR-" + uuid.uuid4().hex[:8]
    for i, (when, amount, status) in enumerate(((date(2025, 10, 1), "100", 1), (date(2026, 9, 1), "200", 1),
            (date(2026, 10, 20), "300", 1), (date(2026, 10, 20), "900", 3), (date(2026, 11, 1), "500", 1))):
        db.add(BizContract(contract_no=f"{marker}-{i}", name=marker, customer_name="年份测试", sign_date=when,
                           amount=Decimal(amount), status=status, business_line="XY", owner_id=uid(db, "songjia")))
    db.commit()
    headers = login("admin")
    args = {"keyword": marker, "sign_year": 2026}
    cards = data(client.get("/api/v1/contracts/signing", headers=headers, params=args))["items"]
    assert Decimal(cards[0]["amount"]) == 500
    for card in cards:
        current = args | {"sign_period": card["key"]}
        listing = data(client.get("/api/v1/contracts", headers=headers, params=current))
        assert listing["sums"]["amount"] == card["amount"] and listing["total"] == card["count"]
        exported = client.get("/api/v1/contracts/export", headers=headers, params=current)
        assert exported.status_code == 200 and int(exported.headers["X-Export-Rows"]) == card["count"]
        if card["count"]:
            workbook = load_workbook(io.BytesIO(exported.content))
            assert workbook.active.cell(workbook.active.max_row, 7).value == f"=SUM(G2:G{card['count'] + 1})"
    cards = data(client.get("/api/v1/contracts/signing", headers=headers, params=args | {"sign_month": "2026-09"}))["items"]
    assert Decimal(cards[0]["amount"]) == 200 and Decimal(cards[1]["amount"]) == 0
    history = data(client.get("/api/v1/contracts/signing", headers=headers, params=args | {"sign_year": 2025}))["items"]
    assert len(history) == 1 and Decimal(history[0]["amount"]) == 100
    assert client.get("/api/v1/contracts", headers=headers, params=args | {"sign_year": 2025, "sign_period": "today"}).status_code == 400


def test_readonly_reconciliation_current_difference_and_source_permission(client, login, db):
    from app.models import ContractWorkbook
    admin = uid(db, "admin")
    contract = BizContract(contract_no="RECON-" + uuid.uuid4().hex[:8], name="核对合同", customer_name="学校",
        amount=Decimal("80"), sign_date=date(2026, 10, 1), status=1, business_line="XY", owner_id=uid(db, "songjia"))
    db.add(contract)
    db.flush()
    source = ContractWorkbook(source_key="recon-" + uuid.uuid4().hex[:8], revision="1", config={"reader_id": admin, "title": "只读对账源"},
        sheets=[{"id": "one", "name": "原表", "rows": [{"row": 3, "cells": [], "record": {"name": "核对合同", "customer_name": "学校", "amount": "100", "sign_date": "2026-10-01"},
        "target": {"kind": "contract", "id": contract.id, "number": contract.contract_no}, "issues": []}]}])
    db.add(source)
    db.commit()
    result = data(client.get("/api/v1/contracts/reconciliation", headers=login("admin")))
    entry = next(r for r in result["items"] if r["source_key"] == source.source_key)
    assert entry["differences"] == [{"field": "金额", "source": "100", "ledger": "80.00"}]
    assert not data(client.get("/api/v1/contracts/reconciliation", headers=login("songjia")))["items"]
    db.refresh(contract)
    assert contract.amount == Decimal("80")
    db.delete(source)
    db.commit()


def test_wecom_assignment_retry_completion_and_invalid_recipient(client, login, db, setup, monkeypatch):
    from app.services import notify_service
    from app.integration.wecom.base import SendResult
    project, school, task = setup
    row = db.scalar(select(SysNotify).where(SysNotify.ref_type == "ops_task", SysNotify.ref_id == str(task["id"]),
                                          SysNotify.channel == "wecom_app"))
    sent = []
    class Provider:
        def send_app_message(self, ids, content):
            sent.append((ids, content.url))
            return SendResult(True, "test-message")
    monkeypatch.setattr(notify_service, "get_wecom", lambda: Provider())
    notify_service._deliver(db, [row])
    assert row.send_status == 1 and sent[0][0] == ["xiekai"] and f"task={task['id']}" in sent[0][1]
    db.get(OpsTask, task["id"]).status = "done"
    db.flush()
    row.send_status, row.retry_count = 0, 0
    notify_service._deliver(db, [row])
    assert len(sent) == 1 and row.retry_count == 3 and row.send_status == 2
    db.get(OpsTask, task["id"]).status = "doing"
    db.flush()
    class Invalid:
        def send_app_message(self, ids, content):
            return SendResult(True, "test-invalid", invalid_users=ids)
    monkeypatch.setattr(notify_service, "get_wecom", lambda: Invalid())
    row.send_status, row.retry_count = 0, 0
    notify_service._deliver(db, [row])
    assert row.send_status == 2 and row.retry_count == 1


def test_digest_marks_late_completion_and_visits_without_tasks(client, login, db, setup):
    project, school, task = setup
    row = db.get(OpsTask, task["id"])
    row.original_due_date = date(2026, 10, 19)
    db.commit()
    data(client.post(f"{BASE}/tasks/{task['id']}/feedback", headers=login("xiekai"), json={"status": "done", "content": "晚一天完成",
        "completed_node_ids": [n["id"] for n in task["nodes"]]}))
    result = data(client.get(BASE + "/digest", headers=login("xiekai"), params={"project_id": project["id"], "mode": "week", "period": "2026-W43", "mine": True}))
    assert result["stats"]["late_done"] == 1 and "延期完成" in result["text"]
    data(client.post(f"{BASE}/{project['id']}/schools/{school['id']}/visits", headers=login("xuhao"), json={"occurred_on": str(today()), "content": "无本人任务但实际完成拜访"}))
    result = data(client.get(BASE + "/digest", headers=login("xuhao"), params={"mode": "week", "period": "2026-W43", "mine": True}))
    assert "无本人任务但实际完成拜访" in result["text"]
