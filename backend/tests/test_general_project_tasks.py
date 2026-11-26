from datetime import datetime, timedelta

from app.core.timeutil import TZ
from app.integration.wecom.mock import read_outbox
from app.jobs import operations as jobs
from app.services import key_projects, task_feedback_deadlines
from tests.conftest import data, deletion_body


def project(client, headers, name, owner_id, **fields):
    result = data(client.post("/api/v1/projects", headers=headers, json={"name": name, "customer_name": "验收客户",
        "business_line": "AF", "owner_id": owner_id, "start_date": "2026-11-20", "milestone_template": "none", **fields}))
    return f"/api/v1/projects/{result['project_code']}"


def test_general_task_uses_existing_nodes_permissions_and_process_history(client, login, monkeypatch):
    monkeypatch.setenv("FAKE_TODAY", "2026-11-26")
    admin, manager, worker, reader = (login(name) for name in ("admin", "xuhao", "dingrui", "pengbo"))
    manager_id = data(client.get("/api/v1/auth/me", headers=manager))["id"]
    worker_id = data(client.get("/api/v1/auth/me", headers=worker))["id"]
    path = project(client, admin, "一般项目任务验收", manager_id, milestone_template="standard")
    milestones = data(client.get(path + "/milestones", headers=manager))
    task_list = path + "/tasks"
    original = data(client.get(task_list, headers=manager))["tasks"]
    assert [row["id"] for row in original] == [row["id"] for row in milestones]
    assert all(not row["has_plan"] for row in original)
    assert client.get(task_list, headers=worker).status_code == 403
    assert client.get(task_list, headers=reader).status_code == 403
    options = data(client.get(task_list + "/options", headers=manager))
    assert worker_id in {row["id"] for row in options["users"]}

    plan = {"title": "核验新设备", "owner_id": worker_id, "preparation_on": "2026-11-20", "due_on": "2026-12-10",
            "requirements": "提交验收清单", "nodes": [{"name": "核对清单", "due_on": "2026-11-26"}]}
    task = data(client.post(task_list, headers=manager, json=plan))
    task_path = f"{task_list}/{task['id']}"
    workflow = f"{path}/milestones/{task['id']}"
    history_path = f"/api/v1/record-followups/key-task/{task['id']}"
    assert task["has_plan"] and task["requirements"] == plan["requirements"]
    assert client.get(task_path, headers=reader).status_code == 403
    assert client.get(history_path, headers=reader).status_code == 403
    assert client.post(task_list, headers=worker, json=plan).status_code == 403
    assert client.put(workflow, headers=manager, json={"status": 3}).status_code == 409

    persisted_nodes = [{"id": node["id"], "name": node["name"], "due_on": node["due_on"]} for node in task["nodes"]]
    revised_plan = {**plan, "title": "核验设备并提交清单", "expected_revision": task["revision"], "nodes": persisted_nodes}
    task = data(client.put(task_path, headers=manager, json=revised_plan))
    assert client.put(task_path, headers=manager, json=revised_plan).status_code == 409
    feedback = {"content": "清单已核验", "status": 3, "expected_revision": task["revision"]}
    assert client.post(task_path + "/feedback", headers=worker, json=feedback).status_code == 400
    done = data(client.post(task_path + "/feedback", headers=worker,
        json={**feedback, "completed_node_ids": [task["nodes"][0]["id"]]}))
    assert done["status"] == 3 and done["nodes"][0]["completed_on"] == "2026-11-26"
    history = data(client.get(history_path, headers=worker))
    assert history["record"]["module"] == "一般项目" and "清单已核验" in history["legacy"][0]["content"]
    detail = data(client.get(path, headers=manager))
    assert detail["done_nodes"] == 1
    assert any(node["id"] == task["id"] and node["status"] == 3 for node in detail["milestones"])
    assert any("清单已核验" in feedback["content"] for feedback in detail["feedbacks"])

    code = path.rsplit("/", 1)[1]
    assert client.get(f"/api/v1/market/projects/{code}/tasks/{task['id']}", headers=admin).status_code == 404
    data(client.put(path + "/key", headers=admin, json={"is_key": True}))
    assert client.get(task_path, headers=admin).status_code == 404
    assert data(client.get(f"/api/v1/market/projects/{code}/tasks/{task['id']}", headers=worker))["status"] == 3
    data(client.put(path + "/key", headers=admin, json={"is_key": False}))
    client_delete = f"/api/v1/record-deletions/key-task/{task['id']}"
    assert client.request("DELETE", client_delete, headers=worker, json=deletion_body()).status_code == 403
    data(client.request("DELETE", client_delete, headers=manager, json=deletion_body()))
    assert client.get(task_path, headers=manager).status_code == 404
    assert task["id"] not in {row["id"] for row in data(client.get(task_list, headers=manager))["tasks"]}


def test_general_assignment_and_overdue_wecom_cards_open_the_same_task(client, login, db, monkeypatch):
    clock = [datetime(2026, 11, 26, 9, tzinfo=TZ)]
    monkeypatch.setenv("FAKE_TODAY", "2026-11-26")
    for module in (jobs, key_projects, task_feedback_deadlines):
        monkeypatch.setattr(module, "now", lambda: clock[0])
    admin, worker = login("admin"), login("dingrui")
    admin_id = data(client.get("/api/v1/auth/me", headers=admin))["id"]
    worker_id = data(client.get("/api/v1/auth/me", headers=worker))["id"]
    path = project(client, admin, "一般项目企微提醒验收", admin_id)
    task = data(client.post(path + "/tasks", headers=admin, json={"title": "反馈设备台账", "owner_id": worker_id,
        "preparation_on": "2026-11-20", "due_on": "2026-11-28", "cadence": "node", "feedback_due_at": "2026-11-26T10:00:00+08:00"}))
    link = f"/general-projects/{path.rsplit('/', 1)[1]}?task={task['id']}"
    clock[0] += timedelta(hours=2)
    jobs.task_feedback_deadline_reminders(db, {})
    jobs.task_feedback_deadline_reminders(db, {})
    titles = {"收到一般项目任务", "一般项目任务逾期未反馈"}
    cards = [message for message in read_outbox(500) if (message["url"] or "").endswith(link)]
    assert len(cards) == 2 and {message["title"] for message in cards} == titles
    assert all(message["type"] == "textcard" and message["to"] == ["dingrui"] for message in cards)
    inbox = data(client.get("/api/v1/notifications", headers=worker, params={"page_size": 100}))["items"]
    assert {message["title"] for message in inbox if message["link"] == link} == titles
    task_path = f"{path}/tasks/{task['id']}"
    current = data(client.get(task_path, headers=worker))
    assert current["feedback_status"] == "overdue"
    assert {row["title"] for row in current["notification_deliveries"] if row["status"] == "企微已受理"} == titles
    result = data(client.post(task_path + "/feedback", headers=worker, json={"content": "设备清单已登记",
        "expected_revision": current["revision"]}))
    assert result["feedback_status"] is None and not result["feedback_deadline_pending"]
