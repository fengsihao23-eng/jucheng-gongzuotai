from datetime import datetime, timedelta
from uuid import uuid4

from app.core.timeutil import TZ
from app.integration.wecom.mock import read_outbox
from app.jobs import market as market_jobs
from app.jobs import operations as jobs
from app.models import SysNotify
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
    assert worker_id not in {row["id"] for row in options["users"]}
    code = path.rsplit("/", 1)[1]
    data(client.post(f"/api/v1/market/projects/{code}/team?scope=general", headers=manager, json={"user_ids": [worker_id]}))
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
    code = path.rsplit("/", 1)[1]
    data(client.post(f"/api/v1/market/projects/{code}/team?scope=general", headers=admin, json={"user_ids": [worker_id]}))
    task = data(client.post(path + "/tasks", headers=admin, json={"title": "反馈设备台账", "owner_id": worker_id,
        "preparation_on": "2026-11-20", "due_on": "2026-11-28", "cadence": "node", "feedback_due_at": "2026-11-26T10:00:00+08:00"}))
    link = f"/projects/{code}?task={task['id']}"
    legacy = SysNotify(user_id=worker_id, ref_type=key_projects.GENERAL_REF, ref_id=str(task['id']),
                       link=f"/general-projects/{code}?task={task['id']}")
    assert key_projects.eligible_team_notice(db, legacy)
    legacy.link = f"/general-projects/{code}?task={task['id'] + 1}"
    assert not key_projects.eligible_team_notice(db, legacy)
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


def test_general_overview_and_creation_keep_project_and_team_access_scoped(client, login, monkeypatch):
    monkeypatch.setenv("FAKE_TODAY", "2026-11-26")
    admin, worker, outsider = (login(name) for name in ("admin", "yangyue", "pengbo"))
    admin_id = data(client.get("/api/v1/auth/me", headers=admin))["id"]
    member = data(client.get("/api/v1/auth/me", headers=worker))
    assert "projects" not in member["menus"]
    board = "/api/v1/market/board?scope=general"
    assert client.get(board, headers=worker).status_code == 403

    body = {"name": "一般项目首页验收-" + uuid4().hex[:8], "owner_id": admin_id, "preparation_on": "2026-11-20",
            "due_on": "2026-12-15", "team_member_ids": [member["id"]], "tasks": [
                {"title": "复核交接资料", "owner_id": member["id"], "preparation_on": "2026-11-20", "due_on": "2026-12-10"}]}
    created = data(client.post("/api/v1/market/projects?scope=general", headers=admin, json=body))
    code = created["project_code"]
    detail_path = f"/api/v1/market/projects/{code}"
    assert client.get(detail_path, headers=admin).status_code == 404
    assert client.get(detail_path + "?scope=general", headers=outsider).status_code == 403
    assert data(client.get("/api/v1/auth/me", headers=worker))["general_project_team"] is True
    card = next(row for row in data(client.get(board, headers=admin))["projects"] if row["project_code"] == code)
    assert not card["is_key"] and card["total_nodes"] == 1 and card["can_view_financials"]
    assert code not in {row["project_code"] for row in data(client.get("/api/v1/market/board", headers=admin))["projects"]}
    assert code not in {row["project_code"] for row in data(client.get(board, headers=outsider))["projects"]}
    member_cards = data(client.get(board, headers=worker))["projects"]
    assert all(not row["can_view_financials"] for row in member_cards)
    assert code in {row["project_code"] for row in member_cards}

    detail = data(client.get(detail_path + "?scope=general", headers=worker))
    assert detail["money"] is None and detail["financials"] is None and not detail["can_manage"]
    task = detail["tasks"][0]
    assert task["can_feedback"]
    path = f"/api/v1/projects/{code}/tasks/{task['id']}"
    done = data(client.post(path + "/feedback", headers=worker, json={"status": 3, "content": "已确认交接完成",
        "expected_revision": task["revision"]}))
    assert done["status"] == 3
    assert client.get(f"/api/v1/projects/{code}", headers=worker).status_code == 403
    removal = detail_path + f"/team/{member['id']}?scope=general"
    data(client.request("DELETE", removal, headers=admin, json=deletion_body()))
    assert client.get(path, headers=worker).status_code == 403
    assert client.get(detail_path + "?scope=general", headers=worker).status_code == 403


def test_general_reports_use_the_same_periods_and_enforce_file_scope(client, login, db, monkeypatch):
    monkeypatch.setenv("FAKE_TODAY", "2026-11-26")
    admin, owner, reader = (login(name) for name in ("admin", "xuhao", "pengbo"))
    owner_id = data(client.get("/api/v1/auth/me", headers=owner))["id"]
    ledger_path = project(client, admin, "台账项目不计入一般项目报告", owner_id)
    created = data(client.post("/api/v1/market/projects?scope=general", headers=admin, json={
        "name": "一般项目报告范围验收", "owner_id": owner_id, "preparation_on": "2026-11-20", "due_on": "2026-12-15"}))
    code = created["project_code"]
    result = data(client.post("/api/v1/market/reports/generate?scope=general", headers=owner, json={"period_type": "weekly"}))
    assert result["report_type"] == "general_weekly" and result["title"].startswith("一般项目周报")
    report_codes = {item["project_code"] for item in result["data"]["projects"]}
    assert code in report_codes
    assert ledger_path.rsplit("/", 1)[1] not in report_codes
    path = f"/api/v1/market/reports/{result['id']}"
    assert data(client.get(path + "?scope=general", headers=owner))["text"].startswith("# 一般项目周报")
    assert client.get(path + "?scope=general", headers=reader).status_code == 404
    file = result["files"][0]
    assert client.get(file["url"], headers=owner).content.startswith(b"PK")
    assert client.get(file["url"], headers=reader).status_code == 404
    assert client.get(path, headers=admin).status_code == 404
    assert result["id"] not in {row["id"] for row in data(client.get("/api/v1/market/reports?scope=general", headers=reader))["items"]}
    assert market_jobs.general_monthly_report(db, {"period": "2026-10"})["message"].startswith("已生成 一般项目月报")


def test_ledger_keeps_old_records_and_member_access_when_general_workspace_starts_empty(client, login, monkeypatch):
    monkeypatch.setenv("FAKE_TODAY", "2026-11-26")
    admin, worker, outsider = (login(name) for name in ("admin", "yangyue", "pengbo"))
    admin_id = data(client.get("/api/v1/auth/me", headers=admin))["id"]
    worker_id = data(client.get("/api/v1/auth/me", headers=worker))["id"]
    path = project(client, admin, "留在台账的项目", admin_id, milestone_template="standard", budget_amount=3600)
    code = path.rsplit("/", 1)[1]
    team_path = f"/api/v1/market/projects/{code}/team?scope=general"
    data(client.post(team_path, headers=admin, json={"user_ids": [worker_id]}))

    board = "/api/v1/market/board?scope=general&include_paused=true"
    assert code not in {row["project_code"] for row in data(client.get(board, headers=admin))["projects"]}
    assert code not in {row["project_code"] for row in data(client.get("/api/v1/projects?scope=general", headers=admin))["items"]}
    ledger = data(client.get(path, headers=admin))
    assert not ledger["in_general_workspace"] and len(ledger["milestones"]) == 5 and ledger["budget_amount"] == "3600.00"
    assert client.get(path, headers=outsider).status_code == 403
    assert client.get(path, headers=worker).status_code == 403

    profile = data(client.get("/api/v1/auth/me", headers=worker))
    assert profile["ledger_project_team"] and "projects" not in profile["menus"]
    personal_ledger = data(client.get("/api/v1/projects", headers=worker))
    assert personal_ledger["data_scope"] == "team" and not personal_ledger["can_create"]
    own = next(row for row in personal_ledger["items"] if row["project_code"] == code)
    assert own["total_nodes"] == 5 and "budget_amount" not in own and "contract_total" not in own
    assert client.get("/api/v1/projects?scope=history", headers=worker).status_code == 403
    own_detail = data(client.get(f"/api/v1/market/projects/{code}?scope=general", headers=worker))
    assert not own_detail["in_general_workspace"] and own_detail["money"] is None and len(own_detail["tasks"]) == 5

    created = data(client.post("/api/v1/market/projects?scope=general", headers=admin, json={
        "name": "从一般项目新建", "owner_id": admin_id, "preparation_on": "2026-11-20", "due_on": "2026-12-15",
        "team_member_ids": [worker_id]}))
    new_code = created["project_code"]
    general_codes = {row["project_code"] for row in data(client.get(board, headers=worker))["projects"]}
    assert new_code in general_codes and code not in general_codes
    assert new_code not in {row["project_code"] for row in data(client.get("/api/v1/projects", headers=worker))["items"]}
    assert {code, new_code}.issubset({row["project_code"] for row in data(client.get("/api/v1/projects", headers=admin))["items"]})
    assert data(client.get("/api/v1/projects/" + new_code, headers=admin))["in_general_workspace"]
