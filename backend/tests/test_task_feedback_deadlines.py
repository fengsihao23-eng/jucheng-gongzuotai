"""Assignment and timestamp-based reminders use the same task state as the board."""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.timeutil import TZ
from app.integration.wecom.mock import read_outbox
from app.jobs import operations as jobs
from app.models import BizProjectFollowSetting, OpsProject, SysNotify, SysUser
from app.services import key_projects, notify_service, operations_notifications, operations_service, task_feedback_deadlines
from tests.conftest import data


@pytest.fixture(params=["operations", "market"])
def task_case(request, client, login, db, monkeypatch):
    clock = [datetime(2026, 11, 25, 9, 0, tzinfo=TZ)]
    monkeypatch.setenv("FAKE_TODAY", "2026-11-25")
    for module in (jobs, key_projects, operations_service, task_feedback_deadlines):
        monkeypatch.setattr(module, "now", lambda: clock[0])
    ops = request.param == "operations"
    owner = "xiekai" if ops else "xuhao"
    owner_id = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == owner))
    admin = login("admin")
    manager_id = data(client.get("/api/v1/auth/me", headers=admin))["id"]
    name = f"反馈截止验收-{uuid4().hex[:8]}"
    if ops:
        base = "/api/v1/operations"
        project = data(client.post(base, headers=admin, json={"name": name, "owner_id": manager_id}))
        data(client.post(f"{base}/{project['id']}/members", headers=admin, json={"user_id": owner_id, "role": "member"}))
        create = f"{base}/{project['id']}/tasks"
        fields = {"due_date": "2026-12-10"}
    else:
        base = "/api/v1/market/projects"
        project = data(client.post(base, headers=admin, json={"name": name, "owner_id": manager_id,
            "preparation_on": "2026-11-25", "due_on": "2026-12-10"}))
        create = f"{base}/{project['project_code']}/tasks"
        fields = {"preparation_on": "2026-11-25", "due_on": "2026-12-10"}
    due = clock[0] + timedelta(hours=1)
    body = {"title": "确认进展", "owner_id": owner_id, "cadence": "node", **fields, "feedback_due_at": due.isoformat()}
    invalid = client.post(create, headers=admin, json={**body, "feedback_due_at": "2026-11-25T10:00:00"})
    assert invalid.status_code == 400
    task = data(client.post(create, headers=admin, json=body))
    link = (f"/operations/{project['id']}" if ops else f"/market/projects/{project['project_code']}") + f"?task={task['id']}"
    path = f"{base}/tasks/{task['id']}" if ops else f"{create}/{task['id']}"
    return ops, owner, project, task, path, link, body, clock, due


def test_assignment_and_overdue_deadline_send_one_text_card_to_task_owner(client, login, db, task_case):
    ops, owner, _, task, path, link, _, clock, due = task_case
    recipient = login(owner)
    assignment = "收到运营项目任务" if ops else "收到重点项目任务"
    overdue = "运营任务逾期未反馈" if ops else "重点项目任务逾期未反馈"
    cards = lambda title: [item for item in read_outbox(500) if item["title"] == title and (item["url"] or "").endswith(link)]
    assert task["feedback_status"] == "pending" and datetime.fromisoformat(task["feedback_due_at"]) == due
    initial = cards(assignment)
    assert len(initial) == 1 and initial[0]["type"] == "textcard" and initial[0]["to"] == [owner]

    clock[0] = due
    jobs.task_feedback_deadline_reminders(db, {})
    assert cards(overdue) == []
    assert data(client.get(path, headers=recipient))["feedback_status"] == "pending"

    clock[0] += timedelta(minutes=1)
    jobs.task_feedback_deadline_reminders(db, {})
    jobs.task_feedback_deadline_reminders(db, {})
    missed = cards(overdue)
    assert len(missed) == 1 and missed[0]["type"] == "textcard" and missed[0]["to"] == [owner]
    assert "2026-11-25 10:00" in missed[0]["content"]
    current = data(client.get(path, headers=recipient))
    assert current["feedback_status"] == "overdue"
    assert any(item["title"] == overdue and item["status"] == "企微已受理" for item in current["notification_deliveries"])
    inbox = data(client.get("/api/v1/notifications", headers=recipient, params={"page_size": 100}))["items"]
    assert any(row["title"] == overdue and row["link"] == link for row in inbox)

    data(client.post("/api/v1/notifications/read", headers=recipient, json={}))
    assert data(client.get(path, headers=recipient))["feedback_status"] == "overdue"
    result = data(client.post(path + "/feedback", headers=recipient, json={"content": "已填写实际进展", "status": "doing" if ops else 2,
        "expected_revision": current["revision"]}))
    assert result["feedback_status"] is None and not result["feedback_deadline_pending"]
    clock[0] += timedelta(minutes=10)
    jobs.task_feedback_deadline_reminders(db, {})
    assert cards(overdue) == missed


def test_postponing_or_clearing_feedback_deadline_stops_stale_queue(client, login, db, task_case):
    ops, _, project, task, path, _, body, clock, due = task_case
    admin = login("admin")
    scan = operations_notifications.scan_feedback_deadlines if ops else key_projects.scan_feedback_deadlines
    clock[0] = due + timedelta(minutes=1)
    notices = scan(db, clock[0])
    db.commit()
    queued = next(n.id for n in notices if n.channel == "wecom_app" and n.ref_id.startswith(f"{task['id']}:"))
    new_due = clock[0] + timedelta(hours=1)
    changes = {"expected_revision": task["revision"], "feedback_due_at": new_due.isoformat()}
    updated = data(client.put(path, headers=admin, json=changes if ops else {**body, **changes}))
    assert updated["feedback_status"] == "pending"
    assert notify_service.deliver_pending(db, [queued]) == 1
    db.commit()
    assert db.get(SysNotify, queued).send_status == 2

    clock[0] = new_due + timedelta(minutes=1)
    if ops:
        db.get(OpsProject, project["id"]).status = "paused"
    else:
        db.add(BizProjectFollowSetting(project_id=project["id"], paused=True))
    db.commit()
    assert not any(n.ref_id.startswith(f"{task['id']}:") for n in scan(db, clock[0]))
    assert data(client.get(path, headers=admin))["feedback_status"] is None
    if ops:
        db.get(OpsProject, project["id"]).status = "active"
    else:
        db.get(BizProjectFollowSetting, project["id"]).paused = False
    db.commit()
    current = data(client.get(path, headers=admin))
    cleared = {"expected_revision": current["revision"], "feedback_due_at": None}
    result = data(client.put(path, headers=admin, json=cleared if ops else {**body, **cleared}))
    assert result["feedback_due_at"] is None and result["feedback_status"] is None
    assert not any(n.ref_id.startswith(f"{task['id']}:") for n in scan(db, clock[0]))


def test_feedback_before_deadline_prevents_the_first_reminder(client, login, db, task_case):
    ops, owner, _, task, path, link, _, clock, due = task_case
    clock[0] = due - timedelta(minutes=15)
    result = data(client.post(path + "/feedback", headers=login(owner), json={"content": "已在截止时间前提交进展",
        "status": "doing" if ops else 2, "expected_revision": task["revision"]}))
    assert not result["feedback_deadline_pending"]
    clock[0] = due + timedelta(minutes=1)
    jobs.task_feedback_deadline_reminders(db, {})
    overdue = "运营任务逾期未反馈" if ops else "重点项目任务逾期未反馈"
    assert not any(msg["title"] == overdue and (msg["url"] or "").endswith(link) for msg in read_outbox(500))
