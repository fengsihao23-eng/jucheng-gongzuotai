"""Key project creation, phase execution, money and current notification eligibility."""
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.timeutil import TZ
from app.models import (BizContract, BizPayment, BizProject, BizProjectFeedback, BizProjectFollowSetting,
                        KeyProjectTaskNode, KeyProjectTaskPlan, SysNotify, SysUser)
from app.services import key_projects as keys
from tests.conftest import data

BASE = "/api/v1/market"


def ident(db, name):
    return db.scalar(select(SysUser.id).where(SysUser.wecom_userid == name))


@pytest.fixture
def key_project(client, login, db, monkeypatch):
    monkeypatch.setenv("FAKE_TODAY", "2026-11-19")
    body = {"name": "重点项目验收-" + uuid4().hex[:8], "content": "明确准备、阶段计划与完成要求",
            "customer_name": "合成验收客户", "business_line": "XY", "owner_id": ident(db, "wujun"),
            "preparation_on": "2026-11-10", "due_on": "2026-12-10"}
    project = data(client.post(BASE + "/projects", headers=login("admin"), json=body))
    path = BASE + f"/projects/{project['project_code']}"
    return path, project, body


def task_body(db, **changes):
    return {"title": "核对方案与交付资料", "requirements": "完成学校现场核对与方案确认", "owner_id": ident(db, "xuhao"),
            "preparation_on": "2026-11-10", "due_on": "2026-11-30", "cadence": "daily",
            "nodes": [{"name": "准备资料", "due_on": "2026-11-18"}, {"name": "确认方案", "due_on": "2026-11-25"}], **changes}


def test_create_from_market_has_required_schedule_and_no_invented_construction_nodes(client, login, db, key_project):
    path, project, body = key_project
    result = data(client.get(path, headers=login("admin")))
    assert result["name"] == body["name"] and result["content"] == body["content"]
    assert result["is_key"] and result["start_date"] == body["preparation_on"] and result["plan_end_date"] == body["due_on"]
    assert result["tasks"] == [] and result["money"]["contract_total"] == "0.00"
    listed = data(client.get(BASE + "/board", headers=login("admin")))
    assert any(p["project_code"] == project["project_code"] for p in listed["projects"])
    assert client.post(BASE + "/projects", headers=login("chenjing"), json=body).status_code == 403
    assert client.post(BASE + "/projects", headers=login("admin"), json={**body, "due_on": "2026-11-01"}).status_code == 400
    assert client.post(BASE + "/projects", headers=login("admin"), json={**body, "owner_id": ident(db, "yangyue")}).status_code == 400


def test_project_dates_revision_and_foreign_project_task_blocked(client, login, db, key_project):
    path, project, body = key_project
    headers = login("admin")
    project_view = data(client.get(path, headers=headers))
    saved = data(client.post(path + "/tasks", headers=headers, json=task_body(db)))
    assert client.put(path, headers=headers, json={**body, "due_on": "2026-11-20", "expected_revision": project_view["revision"]}).status_code == 400
    assert client.put(path, headers=headers, json={**body, "content": "更新工作内容", "expected_revision": project_view["revision"]}).status_code == 200
    assert client.put(path, headers=headers, json={**body, "expected_revision": project_view["revision"]}).status_code == 409
    outside = data(client.post(BASE + "/projects", headers=headers, json={**body, "name": "另一个合成项目"}))
    assert client.get(BASE + f"/projects/{outside['project_code']}/tasks/{saved['id']}", headers=headers).status_code == 404
    assert client.post(path + "/tasks", headers=headers, json=task_body(db, preparation_on="2026-11-01")).status_code == 400


def test_phase_feedback_flows_to_board_history_and_existing_reports(client, login, db, key_project):
    path, project, _ = key_project
    task = data(client.post(path + "/tasks", headers=login("admin"), json=task_body(db)))
    feedback = {"content": "现场资料已核对", "problems": "校方接口待提供", "next_step": "联系校方确认接口",
                "completed_node_ids": [task["nodes"][0]["id"]], "expected_revision": task["revision"]}
    result = data(client.post(path + f"/tasks/{task['id']}/feedback", headers=login("xuhao"), json=feedback))
    assert result["nodes"][0]["completed_on"] == "2026-11-19" and len(result["history"]) == 1
    assert result["history"][0]["values"]["next_step"] == feedback["next_step"]
    assert result["feedback_due_on"] == "2026-11-20" and not result["needs_feedback"]
    assert client.post(path + f"/tasks/{task['id']}/feedback", headers=login("xuhao"), json=feedback).status_code == 409
    board = data(client.get(BASE + "/board", headers=login("admin")))
    card = next(r for r in board["projects"] if r["project_code"] == project["project_code"])
    assert card["alert"] and card["reported_this_week"] and "现场资料已核对" in card["latest_feedback"]["content"]
    report = data(client.get(BASE + "/reports/summary", headers=login("admin"), params={"period": "2026-W47"}))
    assert "现场资料已核对" in report["text"] and "完成阶段：准备资料" in report["text"] and "校方接口待提供" in report["text"]
    assert db.scalar(select(func.count()).select_from(BizProjectFeedback).where(BizProjectFeedback.project_id == project["id"])) == 1


def test_completion_requires_all_phases_and_preserves_actual_date(client, login, db, key_project):
    path, _, _ = key_project
    task = data(client.post(path + "/tasks", headers=login("admin"), json=task_body(db)))
    endpoint = path + f"/tasks/{task['id']}/feedback"
    base = {"content": "交付检查完成", "status": 3, "completed_on": "2026-11-19", "expected_revision": task["revision"]}
    assert client.post(endpoint, headers=login("xuhao"), json=base).status_code == 400
    assert client.post(endpoint, headers=login("xuhao"), json={**base, "completed_on": "2026-11-20"}).status_code == 400
    done = data(client.post(endpoint, headers=login("xuhao"), json={**base, "completed_node_ids": [n["id"] for n in task["nodes"]]}))
    assert done["status"] == 3 and done["finish_date"] == "2026-11-19" and all(n["completed_on"] for n in done["nodes"])
    assert client.post(endpoint, headers=login("xuhao"), json={**base, "expected_revision": done["revision"]}).status_code == 409
    assert client.put(path + f"/tasks/{task['id']}", headers=login("admin"), json={**task_body(db), "expected_revision": done["revision"]}).status_code == 409


def test_task_owner_permissions_completed_phase_edit_and_external_revision(client, login, db, key_project):
    path, _, _ = key_project
    main = data(client.post(path + "/tasks", headers=login("admin"), json=task_body(db)))
    data(client.post(path + "/tasks", headers=login("admin"), json=task_body(db, title="另一项任务", owner_id=ident(db, "dingrui"))))
    fb = {"content": "不能替他人反馈", "expected_revision": main["revision"]}
    assert client.post(path + f"/tasks/{main['id']}/feedback", headers=login("dingrui"), json=fb).status_code == 403
    assert client.post(path + "/tasks", headers=login("dingrui"), json=task_body(db)).status_code == 403
    data(client.post(path + f"/tasks/{main['id']}/feedback", headers=login("xuhao"), json={**fb, "content": "第一阶段完成", "completed_node_ids": [main["nodes"][0]["id"]]}))
    current = data(client.get(path + f"/tasks/{main['id']}", headers=login("admin")))
    assert client.put(path + f"/tasks/{main['id']}", headers=login("admin"), json={**task_body(db, nodes=[]), "expected_revision": current["revision"]}).status_code == 409
    data(client.put(f"/api/v1/projects/{key_project[1]['project_code']}/milestones/{main['id']}", headers=login("admin"), json={"progress_note": "原节点页面产生的新记录"}))
    assert client.post(path + f"/tasks/{main['id']}/feedback", headers=login("xuhao"), json={**fb, "expected_revision": current["revision"]}).status_code == 409


def test_legacy_key_projects_open_without_rewriting_existing_nodes(client, login, db, key_project):
    path, project, _ = key_project
    node = data(client.post(f"/api/v1/projects/{project['project_code']}/milestones", headers=login("admin"), json={"node_name": "原有控制节点", "owner_id": ident(db, "xuhao"), "plan_date": "2026-11-30"}))
    old = data(client.get(path + f"/tasks/{node['id']}", headers=login("xuhao")))
    assert old["title"] == "原有控制节点" and old["cadence"] == "node" and not old["nodes"]
    assert db.get(KeyProjectTaskPlan, node["id"]) is None
    data(client.post(path + f"/tasks/{node['id']}/feedback", headers=login("xuhao"), json={"content": "原有节点继续跟进", "expected_revision": old["revision"]}))
    assert db.get(KeyProjectTaskPlan, node["id"]) is not None


def test_project_invoice_and_receipt_month_totals_do_not_count_other_projects(client, login, db, key_project):
    path, project, _ = key_project
    from app.models import FinSalesInvoice
    contract = BizContract(contract_no="KEY-TEST-" + uuid4().hex[:12], project_code=project["project_code"], customer_name="合成客户", business_line="XY", amount=Decimal(10000), sign_date=date(2026, 11, 1), status=1)
    db.add(contract);db.flush()
    db.add_all([FinSalesInvoice(contract_id=contract.id, customer="合成客户", requested_amount=Decimal(1000), invoice_no="KEY-INV-"+uuid4().hex[:10], issued_amount=Decimal(1000), reduced_amount=Decimal(100), issued_on=date(2026, 11, 10), state="issued"),
        FinSalesInvoice(customer="无关联项目", requested_amount=Decimal(99999), invoice_no="OTHER-"+uuid4().hex[:10], issued_amount=Decimal(99999), reduced_amount=Decimal(0), issued_on=date(2026, 11, 10), state="issued"),
        BizPayment(contract_id=contract.id, amount=Decimal(200), payment_date=date(2026, 11, 11), source_type=2),
        BizPayment(contract_id=contract.id, amount=Decimal(50), payment_date=date(2026, 10, 10), source_type=2)])
    db.commit()
    before = db.scalar(select(func.count()).select_from(BizPayment))
    view = data(client.get(path, headers=login("admin")))
    assert view["financials"]["invoiced"] == "900.00" and view["financials"]["received"] == "200.00"
    assert len(view["financials"]["invoices"]) == 1 and len(view["financials"]["payments"]) == 1
    assert view["money"]["received"] == "250.00"
    prev = data(client.get(path, headers=login("admin"), params={"period": "2026-10"}))
    assert prev["financials"]["invoiced"] == "0.00" and prev["financials"]["received"] == "50.00"
    assert client.get(path, headers=login("admin"), params={"period": "invalid"}).status_code == 400
    assert db.scalar(select(func.count()).select_from(BizPayment)) == before


def test_daily_weekly_feedback_reminders_deduplicate_and_stop_after_feedback(client, login, db, key_project):
    path, project, _ = key_project
    task = data(client.post(path + "/tasks", headers=login("admin"), json=task_body(db, due_on="2026-12-10", cadence="weekly", nodes=[])))
    plan = db.get(KeyProjectTaskPlan, task["id"])
    plan.created_at = datetime(2026, 11, 10, 9, tzinfo=TZ)
    db.commit()
    assert keys.scan_reminders(db)["rows"] >= 2
    db.commit()
    notices = list(db.scalars(select(SysNotify).where(SysNotify.msg_type == keys.NOTICE, SysNotify.ref_id == str(task["id"]))))
    assert len(notices) == 4 and all(keys.eligible_notice(db, n) for n in notices)
    keys.scan_reminders(db);db.commit()
    assert db.scalar(select(func.count()).select_from(SysNotify).where(SysNotify.msg_type == keys.NOTICE, SysNotify.ref_id == str(task["id"]))) == 4
    current = data(client.get(path + f"/tasks/{task['id']}", headers=login("xuhao")))
    assert current["needs_feedback"] and current["feedback_due_on"] == "2026-11-17"
    updated = data(client.post(path + f"/tasks/{task['id']}/feedback", headers=login("xuhao"), json={"content": "本周已更新实际进展", "expected_revision": current["revision"]}))
    assert updated["feedback_due_on"] == "2026-11-26" and not updated["needs_feedback"]
    assert all(not keys.eligible_notice(db, n) for n in notices)


def test_paused_project_and_revoked_visibility_stop_queued_reminders(client, login, db, key_project):
    path, project, _ = key_project
    task = data(client.post(path + "/tasks", headers=login("admin"), json=task_body(db, owner_id=ident(db, "dingrui"))))
    keys.scan_reminders(db);db.commit()
    notice = db.scalar(select(SysNotify).where(SysNotify.user_id == ident(db, "dingrui"), SysNotify.msg_type == keys.NOTICE, SysNotify.ref_id == str(task["id"]), SysNotify.channel == "wecom_app"))
    assert notice and keys.eligible_notice(db, notice)
    db.add(BizProjectFollowSetting(project_id=project["id"], paused=True));db.commit()
    assert not keys.eligible_notice(db, notice)
    assert client.post(path + f"/tasks/{task['id']}/feedback", headers=login("dingrui"), json={"content": "暂停后不提交", "expected_revision": task["revision"]}).status_code == 409
    db.get(BizProjectFollowSetting, project["id"]).paused = False
    user = db.get(SysUser, ident(db, "dingrui"));user.status=2;db.commit()
    assert not keys.eligible_notice(db, notice)
    user.status=1;db.commit()
