"""Project viewing permissions and team-specific reminders are checked independently."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.integration.wecom.mock import read_outbox
from app.models import BizProject, SysNotify
from app.services import notify_service
from tests.conftest import data, deletion_body


def test_unassigned_existing_projects_stay_visible_to_authorized_accounts(client, login, db):
    marker = uuid4().hex[:8]
    general = BizProject(project_code=f"JC-XY-LEGACY-G-{marker}", name="存量一般项目", business_line="XY",
                         customer_name="验收客户", status=2, is_key=False)
    key = BizProject(project_code=f"JC-XY-LEGACY-K-{marker}", name="存量重点项目", business_line="XY",
                     customer_name="验收客户", status=2, is_key=True)
    history = BizProject(project_code=f"JC-XY-LEGACY-H-{marker}", name="存量历史项目", business_line="XY",
                         customer_name="验收客户", status=5, is_key=False)
    db.add_all([general, key, history])
    db.commit()

    manager = login("admin")
    for row, scope in ((general, "general"), (key, "key")):
        cards = data(client.get("/api/v1/market/board", params={"scope": scope}, headers=manager))["projects"]
        assert row.project_code in {card["project_code"] for card in cards}
        detail = data(client.get(f"/api/v1/market/projects/{row.project_code}", params={"scope": scope}, headers=manager))
        assert detail["name"] == row.name
    archived = data(client.get("/api/v1/projects", params={"scope": "history"}, headers=manager))["items"]
    assert history.project_code in {row["project_code"] for row in archived}
    assert client.get(f"/api/v1/projects/{general.project_code}", headers=login("xuhao")).status_code == 403


@pytest.mark.parametrize("kind", ["operations", "key", "general"])
def test_existing_project_scope_does_not_grant_reminders(client, login, db, monkeypatch, kind):
    monkeypatch.setenv("FAKE_TODAY", "2026-11-26")
    leader, teammate, outsider = (login(user) for user in ("wujun", "xuhao", "admin"))
    manager_id = data(client.get("/api/v1/auth/me", headers=leader))["id"]
    teammate_id = data(client.get("/api/v1/auth/me", headers=teammate))["id"]
    outsider_id = data(client.get("/api/v1/auth/me", headers=outsider))["id"]
    name = f"团队权限-{kind}-{uuid4().hex[:8]}"
    prefix = "/api/v1"
    if kind == "operations":
        created = data(client.post(prefix + "/operations", headers=leader, json={"name": name, "owner_id": manager_id}))
        project = prefix + f"/operations/{created['id']}"
        members = project + "/members"
        data(client.post(members, headers=leader, json={"user_id": teammate_id, "role": "member"}))
        task = data(client.post(project + "/tasks", headers=leader, json={
            "title": "团队成员权限核验", "owner_id": manager_id, "due_date": "2026-12-09"}))
        path = prefix + f"/operations/tasks/{task['id']}"
        assert {u["id"] for u in data(client.get(path, headers=leader))["participants"]} == {manager_id, teammate_id}
        notice_path = path + "/discussions"
        revoke_path = members + f"/{teammate_id}"
        overview = prefix + "/operations"
        overview_key = "items"
        identity_key, identity = "id", created["id"]
        notice_body = lambda user, content: {"kind": "note", "recipient_id": user, "content": content}
    else:
        workspace = f"?scope={kind}"
        created = data(client.post(prefix + "/market/projects" + workspace, headers=leader, json={
            "name": name, "owner_id": manager_id, "preparation_on": "2026-11-20", "due_on": "2026-12-10",
            "team_member_ids": [teammate_id], "tasks": [{"title": "团队成员权限核验", "owner_id": manager_id,
                "preparation_on": "2026-11-20", "due_on": "2026-12-09"}]}))
        code = created["project_code"]
        project = prefix + f"/market/projects/{code}" + workspace
        task = data(client.get(project, headers=leader))["tasks"][0]
        path = prefix + (f"/market/projects/{code}" if kind == "key" else f"/projects/{code}") + f"/tasks/{task['id']}"
        notice_path = prefix + f"/record-followups/key-task/{task['id']}"
        revoke_path = prefix + f"/market/projects/{code}/team/{teammate_id}" + workspace
        overview = prefix + "/market/board" + workspace
        overview_key = "projects"
        identity_key, identity = "project_code", code
        notice_body = lambda user, content: {"action": "note", "recipient_id": user, "content": content, "client_id": str(uuid4())}
        assert [u["id"] for u in data(client.get(notice_path + "/recipients", headers=leader))] == [teammate_id]
        assert client.get(prefix + f"/projects/{code}", headers=outsider).status_code == 200

    assert client.get(project, headers=outsider).status_code == 200
    assert client.get(path, headers=outsider).status_code == 200
    assert identity in {row[identity_key] for row in data(client.get(overview, headers=outsider))[overview_key]}
    assert client.get(path, headers=login("caofang")).status_code in {403, 404}
    assert client.get(project, headers=teammate).status_code == 200
    assert client.post(notice_path, headers=leader, json=notice_body(outsider_id, "不得通知给外部成员")).status_code == 403
    accepted = data(client.post(notice_path, headers=leader, json=notice_body(teammate_id, "请核对本项目材料")))
    title = "运营任务有新留言" if kind == "operations" else "有成员在记录中提醒你"
    if kind == "operations":
        notice_link = f"/operations/{identity}?task={task['id']}"
    else:
        notice_link = f"/record-followups/{accepted['items'][0]['id']}"
        assert client.get(prefix + notice_link.replace("/record-followups/", "/record-followups/entries/"),
                          headers=outsider).status_code == 200
    assert any(card["title"] == title and card["to"] == ["xuhao"] and card["url"].endswith(notice_link) for card in read_outbox(200))
    inbox = prefix + "/notifications?page_size=100"
    assert any(item["link"] == notice_link for item in data(client.get(inbox, headers=teammate))["items"])
    assert not any(item["link"] == notice_link for item in data(client.get(inbox, headers=outsider))["items"])

    pending = []
    monkeypatch.setattr(notify_service, "deliver_committed", pending.extend)
    data(client.post(notice_path, headers=leader, json=notice_body(teammate_id, "校验移除后撤回排队提醒")))
    queued = db.scalars(select(SysNotify).where(SysNotify.id.in_(pending))).all()
    assert len(queued) == 1 and queued[0].send_status == 0
    data(client.request("DELETE", revoke_path, headers=leader, json=deletion_body()))
    assert client.get(project, headers=teammate).status_code in {403, 404}
    assert client.get(path, headers=teammate).status_code in {403, 404}
    assert client.post(notice_path, headers=leader, json=notice_body(teammate_id, "不得通知已移除人员")).status_code == 403
    assert not any(item["title"] == title and item["link"] == notice_link for item in data(client.get(inbox, headers=teammate))["items"])
    before = read_outbox(200)
    assert notify_service.deliver_pending(db, pending) == 1
    db.commit()
    assert read_outbox(200) == before
    assert db.get(SysNotify, pending[0]).send_status == 2
