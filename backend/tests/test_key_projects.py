"""Key project creation, phase execution, money and current notification eligibility."""
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.timeutil import TZ
from app.models import (BizContract, BizPayment, BizProject, BizProjectFeedback, BizProjectFollowSetting,
                        KeyProjectMember, KeyProjectTaskNode, KeyProjectTaskPlan, LogOperation, SysNotify, SysUser, SysUserPermission)
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
    project = data(client.post(BASE + "/projects", headers=login("admin"), json={
        **body, "team_member_ids": [ident(db, "xuhao"), ident(db, "dingrui")]}))
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


def test_new_key_project_sends_assignment_to_selected_owner(client, login, db, key_project):
    from app.integration.wecom.mock import read_outbox

    path, project, body = key_project
    notices = db.scalars(select(SysNotify).where(SysNotify.msg_type == keys.ASSIGNMENT_NOTICE,
        SysNotify.ref_type == "biz_project", SysNotify.ref_id == str(project["id"]))).all()
    assert {row.channel for row in notices} == {"inapp", "wecom_app"}
    link = path.removeprefix("/api/v1")
    assert all(row.user_id == body["owner_id"] and row.link == link and row.send_status == 1 for row in notices)
    assert any(msg["to"] == ["wujun"] and msg["type"] == "textcard" and (msg["url"] or "").endswith(link)
               for msg in read_outbox())

    regular = data(client.post("/api/v1/projects", headers=login("admin"), json={
        "name": "项目台账新建重点项目", "customer_name": "测试学校", "business_line": "XY",
        "owner_id": body["owner_id"], "is_key": True, "milestone_template": "none"}))
    assert db.scalar(select(SysNotify.id).where(SysNotify.msg_type == keys.ASSIGNMENT_NOTICE,
        SysNotify.ref_id == str(regular["id"]), SysNotify.send_status == 1, SysNotify.channel == "wecom_app"))


def test_create_project_with_tasks_and_notify_each_assignee(client, login, db, key_project):
    from app.integration.wecom.mock import read_outbox

    _, _, fields = key_project
    plans = [
        task_body(db, title="核对设备", feedback_due_at="2026-11-24T16:30:00+08:00"),
        task_body(db, title="校方确认", owner_id=ident(db, "dingrui"), nodes=[]),
    ]
    result = data(client.post(BASE + "/projects", headers=login("admin"), json={**fields, "name": "一次创建项目和任务", "tasks": plans,
        "team_member_ids": [plan["owner_id"] for plan in plans]}))
    route = f"/market/projects/{result['project_code']}"
    project = data(client.get(f"/api/v1{route}", headers=login("admin")))
    assert [task["title"] for task in project["tasks"]] == [plan["title"] for plan in plans]
    first, second = project["tasks"]
    assert first["feedback_deadline_pending"] and [(n["name"], n["due_on"]) for n in first["nodes"]] == [
        (node["name"], node["due_on"]) for node in plans[0]["nodes"]]
    assert second["owner_id"] == plans[1]["owner_id"] and second["nodes"] == []
    for target, suffix in [("wujun", route), ("xuhao", f"{route}?task={first['id']}"), ("dingrui", f"{route}?task={second['id']}")]:
        assert any(msg["to"] == [target] and msg["type"] == "textcard" and (msg["url"] or "").endswith(suffix)
                   for msg in read_outbox())


def test_invalid_initial_task_rolls_back_project_tasks_and_notices(client, login, db, key_project):
    from app.integration.wecom.mock import read_outbox
    from app.models import BizProjectMilestone

    _, _, fields = key_project
    models = (BizProject, BizProjectMilestone, KeyProjectTaskPlan, KeyProjectTaskNode, SysNotify)
    before = [db.scalar(select(func.count()).select_from(model)) for model in models]
    sent = read_outbox()
    response = client.post(BASE + "/projects", headers=login("admin"), json={**fields, "name": "不得部分创建", "tasks": [
        task_body(db), task_body(db, title="负责人无效", owner_id=999999999),
    ]})
    assert response.status_code == 400
    assert [db.scalar(select(func.count()).select_from(model)) for model in models] == before
    assert read_outbox() == sent


def test_pending_assignment_stops_after_owner_changes(db, key_project, monkeypatch):
    from app.services import notify_service

    _, project, _ = key_project
    row = db.get(BizProject, project["id"])
    ids = keys.queue_assignment(db, row)
    db.commit()
    row.owner_id = ident(db, "xuhao")
    db.commit()
    sent = []
    monkeypatch.setattr(notify_service, "get_wecom", lambda: sent.append("send attempted"))
    notify_service.deliver_pending(db, ids)
    stopped = db.get(SysNotify, ids[0])
    assert stopped.send_status == 2 and stopped.retry_count == notify_service.MAX_RETRY
    assert sent == []


def test_only_named_enterprise_accounts_can_change_follow_pause(client, login, db, key_project):
    from app.core.security import create_jwt

    path, project, _ = key_project
    for account in ("wujun", "admin"):
        headers = login(account)
        assert not data(client.get(path, headers=headers))["can_pause"]
        assert client.put(path + "/pause", headers=headers, json={"paused": True}).status_code == 403

    for account, name in [("ken", "梁道灿"), ("SZGLY", "聚成数智管理员"), ("FengSiHao", "冯思皓"),
                          ("pause-namesake-" + uuid4().hex[:8], "梁道灿")]:
        actor = db.scalar(select(SysUser).where(SysUser.wecom_userid == account))
        actor = actor or SysUser(wecom_userid=account, name=name, status=1, workbench_enabled=True)
        db.add(actor)
        db.flush()
        permission = db.get(SysUserPermission, actor.id)
        previous = (permission.menus, permission.buttons, permission.data_scope) if permission else None
        permission = permission or SysUserPermission(user_id=actor.id)
        permission.menus, permission.buttons, permission.data_scope = ["market", "projects"], [], "all"
        db.add(permission)
        membership = KeyProjectMember(project_id=project["id"], user_id=actor.id)
        db.add(membership)
        db.commit()
        headers = {"Authorization": "Bearer " + create_jwt(actor.id)[0]}
        try:
            allowed = account in {"ken", "SZGLY"}
            card = next(p for p in data(client.get(BASE + "/board", headers=headers))["projects"]
                        if p["project_code"] == project["project_code"])
            assert card["can_pause"] is allowed
            if allowed:
                assert data(client.put(path + "/pause", headers=headers, json={"paused": True}))["paused"]
                assert data(client.get(path, headers=headers))["paused"]
                assert not data(client.put(path + "/pause", headers=headers, json={"paused": False}))["paused"]
            else:
                assert client.put(path + "/pause", headers=headers, json={"paused": True}).status_code == 403
        finally:
            if previous is None:
                db.delete(permission)
            else:
                permission.menus, permission.buttons, permission.data_scope = previous
            db.delete(membership)
            db.commit()


def deletion_password():
    from app.core.config import settings
    return {"password": settings.operations_delete_password}


def test_team_membership_grants_only_that_projects_workspace_and_notices(client, login, db, key_project):
    from app.integration.wecom.mock import read_outbox
    from app.services import notify_service

    path, project, fields = key_project
    admin, member = login("admin"), login("chenjing")
    person = ident(db, "chenjing")
    link = path.removeprefix("/api/v1")
    other = data(client.post(BASE + "/projects", headers=admin, json={**fields, "name": "无需共享的重点项目"}))
    assert client.get(BASE + "/board", headers=member).status_code == 403
    assert client.get(path, headers=member).status_code == 403
    assert data(client.post(path + "/team", headers=admin, json={"user_ids": [person, person, fields["owner_id"]]}))["added"] == 1
    assert data(client.post(path + "/team", headers=admin, json={"user_ids": [person]}))["added"] == 0
    overview = data(client.get(BASE + "/board?include_all=true", headers=member))
    assert [row["id"] for row in overview["projects"]] == [project["id"]]
    assert not overview["projects"][0]["can_view_financials"] and "contract_total" not in overview["projects"][0]
    profile = data(client.get("/api/v1/auth/me", headers=member))
    assert profile["key_project_team"] and "market" not in profile["menus"]
    assert client.get(BASE + "/reports", headers=member).status_code == 403
    assert client.get(BASE + f"/projects/{other['project_code']}", headers=member).status_code == 403
    assert client.post(path + "/team", headers=member, json={"user_ids": [ident(db, "dingrui")]}).status_code == 403
    assert client.put(path, headers=member, json={**fields, "expected_revision": "0" * 64}).status_code == 403

    item = data(client.post(path + "/tasks", headers=admin, json=task_body(db)))
    task_path = f"{path}/tasks/{item['id']}"
    detail = data(client.get(path, headers=member))
    assert detail["team"][0]["id"] == fields["owner_id"]
    assert {u["id"] for u in detail["team"]} == {fields["owner_id"], person, ident(db, "admin"), ident(db, "xuhao"), ident(db, "dingrui")}
    assert not detail["can_manage"] and not detail["can_delete"]
    assert detail["money"] is None and detail["financials"] is None and "budget_amount" not in detail
    assert [t["id"] for t in detail["tasks"]] == [item["id"]]
    assert not data(client.get(task_path, headers=member))["can_feedback"]
    assert client.get(f"/api/v1/record-followups/key-task/{item['id']}", headers=member).status_code == 200
    assert client.post(task_path + "/feedback", headers=member, json={"content": "无权代填", "expected_revision": item["revision"]}).status_code == 403
    data(client.post(task_path + "/feedback", headers=login("xuhao"), json={"content": "接口需求已核对", "expected_revision": item["revision"]}))
    assert any("接口需求已核对" in note["content"] for note in data(client.get(path, headers=member))["feedbacks"])

    titles = {"你已加入重点项目团队", "重点项目新增任务", "重点项目有新进展"}
    cards = [msg for msg in read_outbox(200) if msg["type"] == "textcard" and "chenjing" in msg["to"] and link in (msg["url"] or "")]
    assert len(cards) == 3 and {msg["title"] for msg in cards} == titles
    notices = data(client.get("/api/v1/notifications", headers=member, params={"page_size": 100}))["items"]
    assert {n["title"] for n in notices if link in (n["link"] or "")} == titles

    pending = keys.queue_team_notice(db, db.get(BizProject, project["id"]), "等待投递的进展")
    db.commit()
    assert client.request("DELETE", f"{path}/team/{person}", headers=admin, json=deletion_password()).status_code == 200
    assert notify_service.deliver_pending(db, pending) == len(pending)
    db.commit()
    assert next(db.get(SysNotify, notice_id) for notice_id in pending if db.get(SysNotify, notice_id).user_id == person).send_status == 2
    assert not data(client.get("/api/v1/auth/me", headers=member))["key_project_team"]
    assert client.get(task_path, headers=member).status_code == 403
    assert client.get(f"/api/v1/record-followups/key-task/{item['id']}", headers=member).status_code == 403
    assert not any(link in (n["link"] or "") for n in data(client.get("/api/v1/notifications", headers=member))["items"])


def test_initial_team_member_can_be_assigned_own_task_and_receive_feedback_reminder(client, login, db, key_project, monkeypatch):
    from app.integration.wecom.mock import read_outbox
    from app.services import notify_service

    _, _, fields = key_project
    admin, member = login("admin"), login("yangyue")
    member_id = ident(db, "yangyue")
    result = data(client.post(BASE + "/projects", headers=admin, json={**fields, "name": "团队执行验收",
        "team_member_ids": [member_id], "tasks": [task_body(db, owner_id=member_id, nodes=[], feedback_due_at="2026-11-20T10:00:00+08:00")]}))
    path = BASE + f"/projects/{result['project_code']}"
    task = data(client.get(path, headers=member))["tasks"][0]
    task_path = f"{path}/tasks/{task['id']}"
    assert task["can_feedback"]
    assert client.request("DELETE", f"{path}/team/{member_id}", headers=admin, json=deletion_password()).status_code == 409
    assert any(msg["to"] == ["yangyue"] and msg["title"] == "收到重点项目任务" and
        (msg["url"] or "").endswith(f"/market/projects/{result['project_code']}?task={task['id']}") for msg in read_outbox(100))
    monkeypatch.setenv("FAKE_TODAY", "2026-11-21")
    queued = keys.scan_feedback_deadlines(db, datetime(2026, 11, 21, 12, tzinfo=TZ))
    pending = [n.id for n in queued if n.channel == "wecom_app" and n.ref_id.startswith(f"{task['id']}:")]
    db.commit()
    assert len(pending) == 1 and notify_service.deliver_pending(db, pending) == 1
    db.commit()
    assert db.get(SysNotify, pending[0]).send_status == 1
    task = data(client.get(task_path, headers=member))
    data(client.post(f"{task_path}/feedback", headers=member, json={"content": "已完成本人任务", "status": 3,
        "expected_revision": task["revision"]}))
    assert client.request("DELETE", f"{path}/team/{member_id}", headers=admin, json=deletion_password()).status_code == 200
    assert client.get(path, headers=member).status_code == 403


def test_delete_key_project_hides_board_details_events_reports_and_reminders_but_keeps_history(client, login, db, key_project):
    path, project, body = key_project
    headers = login("admin")
    task = data(client.post(path + "/tasks", headers=headers, json=task_body(db)))
    data(client.post(path + f"/tasks/{task['id']}/feedback", headers=login("xuhao"), json={
        "content": "保留本项目历史进展", "problems": "待确认现场时间", "expected_revision": task["revision"]}))
    contract = BizContract(contract_no="KEY-DELETE-" + uuid4().hex[:12], project_code=project["project_code"],
        customer_name="合成客户", business_line="XY", amount=Decimal(1000), sign_date=date(2026, 11, 1), status=1)
    db.add(contract); db.flush()
    payment = BizPayment(contract_id=contract.id, amount=Decimal(100), payment_date=date(2026, 11, 18), source_type=2)
    db.add(payment); db.commit()
    probe = SysNotify(user_id=ident(db, "xuhao"), ref_id=str(task["id"]))
    assert keys.eligible_notice(db, probe)
    board = data(client.get(BASE + "/board", headers=headers))
    assert next(p for p in board["projects"] if p["project_code"] == project["project_code"])["can_delete"]
    assert any(e["project_code"] == project["project_code"] for e in board["events"])

    result = data(client.request("DELETE", path, headers=headers, json=deletion_password()))
    assert result["deleted"] and result["project_code"] == project["project_code"]
    db.expire_all()
    assert db.get(BizProject, project["id"]).is_deleted
    assert db.get(KeyProjectTaskPlan, task["id"]) is not None
    assert db.get(KeyProjectTaskNode, task["nodes"][0]["id"]) is not None
    assert db.scalar(select(BizProjectFeedback.id).where(BizProjectFeedback.project_id == project["id"])) is not None
    assert not db.get(BizContract, contract.id).is_deleted and db.get(BizPayment, payment.id).amount == Decimal(100)
    assert not keys.eligible_notice(db, probe)
    board = data(client.get(BASE + "/board", headers=headers, params={"include_all": True, "include_paused": True}))
    assert all(p["project_code"] != project["project_code"] for p in board["projects"])
    assert all(e["project_code"] != project["project_code"] for e in board["events"])
    assert client.get(path, headers=headers).status_code == 404
    assert client.get(path + f"/tasks/{task['id']}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/projects/{project['project_code']}", headers=headers).status_code == 404
    assert client.request("DELETE", path, headers=headers, json=deletion_password()).status_code == 404
    report = data(client.get(BASE + "/reports/summary", headers=headers, params={"period": "2026-W47"}))
    assert body["name"] not in report["text"]
    audit = db.scalar(select(LogOperation).where(LogOperation.action == "market.key_project.delete",
                                                LogOperation.target_id == project["project_code"]))
    assert audit.user_id == ident(db, "admin") and not audit.before["is_deleted"] and audit.after["is_deleted"]
    assert "password" not in str(audit.after)


@pytest.mark.parametrize("payload", [None, {"password": ""}, {"password": "incorrect-delete-password"}])
def test_delete_key_project_rejects_missing_empty_or_wrong_password(client, login, db, key_project, payload):
    path, project, _ = key_project
    assert client.request("DELETE", path, headers=login("admin"), json=payload).status_code == 400
    assert client.get(path, headers=login("admin")).status_code == 200
    db.expire_all()
    assert not db.get(BizProject, project["id"]).is_deleted


@pytest.mark.parametrize("granted,scope,owned", [
    (False, "all", True), (False, "all", False), (True, "self", False),
    (True, "self", True), (True, "all", False),
])
def test_delete_key_project_requires_management_permission_and_visible_scope(client, db, key_project, granted, scope, owned):
    from app.core.security import create_jwt
    path, project, _ = key_project
    actor = SysUser(wecom_userid="key-delete-" + uuid4().hex[:12], name="重点项目删除权限验收", status=1, workbench_enabled=True)
    db.add(actor); db.flush()
    db.add(SysUserPermission(user_id=actor.id, menus=["portal", "market", "projects"],
                            buttons=["project:key"] if granted else [], data_scope=scope))
    if owned:
        db.get(BizProject, project["id"]).owner_id = actor.id
    db.commit()
    headers = {"Authorization": "Bearer " + create_jwt(actor.id)[0]}
    board = data(client.get(BASE + "/board", headers=headers))
    card = next((p for p in board["projects"] if p["project_code"] == project["project_code"]), None)
    if card:
        assert card["can_delete"] is granted
    else:
        assert scope == "self" and not owned
    allowed = granted and (scope == "all" or owned)
    response = client.request("DELETE", path, headers=headers, json=deletion_password())
    assert response.status_code == (200 if allowed else 403)
    db.expire_all()
    assert db.get(BizProject, project["id"]).is_deleted is allowed


def test_key_project_delete_does_not_delete_non_key_projects(client, login, db, key_project):
    path, project, _ = key_project
    db.get(BizProject, project["id"]).is_key = False
    db.commit()
    board = data(client.get(BASE + "/board", headers=login("admin"), params={"include_all": True}))
    assert not next(p for p in board["projects"] if p["project_code"] == project["project_code"])["can_delete"]
    assert client.request("DELETE", path, headers=login("admin"), json=deletion_password()).status_code == 404
    db.expire_all()
    assert not db.get(BizProject, project["id"]).is_deleted


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
