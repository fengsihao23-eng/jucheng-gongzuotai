from uuid import uuid4

from sqlalchemy import select

from app.integration.wecom.base import SendResult
from app.models import BizProject, ContractWorkbook, SysNotify, SysUser
from app.services import business_workbook, dict_service, notify_service
from tests.conftest import add_project_test_members, data
from tests.test_contract_workbook import tender

BASE = "/api/v1/record-followups"


def send(client, headers, endpoint, action, content, **fields):
    return client.post(endpoint, headers=headers, json={"client_id": str(uuid4()), "action": action, "content": content, **fields})


def test_lead_requests_owner_replies_and_progress_is_visible_in_original_project(client, login, db, monkeypatch):
    deliveries = []

    class Provider:
        def send_app_message(self, users, message):
            deliveries.append((users, message))
            return SendResult(True, "record-test")

    monkeypatch.setattr(notify_service, "get_wecom", Provider)
    admin, leader, assignee, outsider = [login(name) for name in ("admin", "zhangzhenhua", "xuhao", "yangyue")]
    owner = db.scalar(select(SysUser).where(SysUser.wecom_userid == "xuhao"))
    project = data(client.post("/api/v1/projects", headers=admin, json={"name": "共享跟进验收-" + uuid4().hex[:8],
        "business_line": "XY", "customer_name": "合成客户", "owner_id": owner.id, "milestone_template": "none"}))
    add_project_test_members(db, [project["project_code"]], "zhangzhenhua")
    url = f"{BASE}/project/{project['project_code']}"
    assert client.get(url, headers=outsider).status_code == 403
    assert client.get(url + "/recipients", headers=outsider).status_code == 403
    assert {row["id"] for row in data(client.get(url + "/recipients", headers=leader))} >= {owner.id}
    assert data(client.get(url, headers=leader))["default_note_recipients"] == [{"id": owner.id, "name": owner.name}]
    assert client.post(url, headers=leader, json={"client_id": str(uuid4()), "action": "request", "content": "核对现场条件"}).status_code == 400
    requested = data(send(client, leader, url, "request", "核对现场条件", recipient_id=owner.id))
    ident = requested["items"][0]["id"]
    assert requested["pending"] == 1
    notice = db.scalar(select(SysNotify).where(SysNotify.user_id == owner.id, SysNotify.ref_type == "record_followup",
                                               SysNotify.ref_id == str(ident), SysNotify.channel == "wecom_app"))
    assert notice.send_status == 1 and notice.link == f"/record-followups/{ident}"
    assert "核对现场条件" not in deliveries[0][1].content
    assert client.get(f"{BASE}/entries/{ident}", headers=outsider).status_code == 403
    shown = data(client.get(f"{BASE}/entries/{ident}", headers=assignee))
    assert shown["my_requests"][0]["can_reply"]
    assert send(client, leader, url, "reply", "未经指定的回复", reply_to_id=ident).status_code == 403
    assert data(send(client, assignee, url, "reply", "已和校方核对现场条件", reply_to_id=ident))["pending"] == 0
    assert send(client, assignee, url, "reply", "重复回复", reply_to_id=ident).status_code == 409
    body = {"client_id": str(uuid4()), "action": "progress", "content": "已核对设备接入口", "status": "doing", "expected_progress": shown["progress_revision"]}
    first = data(client.post(url, headers=assignee, json=body))
    repeat = data(client.post(url, headers=assignee, json=body))
    assert first["items"] == repeat["items"] and first["latest"]["content"] == body["content"]
    assert not first["legacy"]
    assert send(client, leader, url, "progress", "过时草稿", status="done", expected_progress=shown["progress_revision"]).status_code == 409
    assert send(client, leader, url, "progress", "阻碍", status="blocked", expected_progress=first["progress_revision"]).status_code == 400
    project_data = data(client.get(f"/api/v1/projects/{project['project_code']}", headers=admin))
    assert project_data["feedbacks"][0]["content"] == body["content"]
    assert [users for users, _ in deliveries[:2]] == [["xuhao"], ["zhangzhenhua"]]
    assert set(deliveries[2][0]) == {"zhangzhenhua", "admin"}
    assert len(deliveries) == 3
    assert all(msg.msg_type == "textcard" for _, msg in deliveries)
    assert all("/record-followups/" in msg.url for _, msg in deliveries[:2])


def test_note_routes_to_owner_or_selected_person_and_retry_rechecks_scope(client, login, db, monkeypatch):
    deliveries = []

    class Provider:
        def send_app_message(self, users, message):
            deliveries.append((users, message))
            return SendResult(True, "note-test")

    monkeypatch.setattr(notify_service, "get_wecom", Provider)
    admin, owner, executive = [get_user(db, name) for name in ("admin", "xuhao", "zhangzhenhua")]
    project = data(client.post("/api/v1/projects", headers=login("admin"), json={
        "name": "留言通知验收", "business_line": "XY", "customer_name": "合成客户",
        "owner_id": owner.id, "milestone_template": "none"}))
    add_project_test_members(db, [project["project_code"]], "zhangzhenhua")
    url = f"{BASE}/project/{project['project_code']}"
    body = {"client_id": str(uuid4()), "action": "note", "content": "业务留言的验收正文"}
    result = data(client.post(url, headers=login("admin"), json=body))
    entry_id = result["items"][0]["id"]
    assert data(client.post(url, headers=login("admin"), json=body))["items"] == result["items"]
    assert [users for users, _ in deliveries] == [[owner.wecom_userid]]
    msg = deliveries[0][1]
    assert msg.msg_type == "textcard" and msg.url.endswith(f"/record-followups/{entry_id}") and body["content"] not in msg.content
    data(send(client, login("admin"), url, "note", "只通知指定人", recipient_id=executive.id))
    assert [users for users, _ in deliveries] == [[owner.wecom_userid], [executive.wecom_userid]]
    assert client.get(f"{BASE}/entries/{entry_id}", headers=login("xuhao")).status_code == 200

    with monkeypatch.context() as hold:
        hold.setattr(notify_service, "deliver_committed", lambda _: None)
        pending_record = data(send(client, login("admin"), url, "note", "投递前重新核对权限"))
    pending = db.scalar(select(SysNotify).where(SysNotify.ref_type == "record_followup", SysNotify.channel == "wecom_app",
                                                 SysNotify.ref_id == str(pending_record["items"][0]["id"])))
    assert pending.send_status == 0
    db.get(BizProject, project["id"]).owner_id = admin.id
    db.commit()
    assert client.get(pending.link.replace("/record-followups/", f"{BASE}/entries/"), headers=login("xuhao")).status_code == 403
    notify_service.deliver_pending(db, [pending.id])
    assert pending.send_status == 2 and pending.retry_count == notify_service.MAX_RETRY
    assert len(deliveries) == 2


def get_user(db, account):
    return db.scalar(select(SysUser).where(SysUser.wecom_userid == account))


def test_workbook_lots_share_history_when_rows_move_and_revision_changes(client, login, db):
    headers = login("admin")
    actor_id = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "admin"))
    wb = ContractWorkbook(source_key="record-followup-workbook-test", config={"title": "跟进测试表", "reader_id": actor_id,
        "enabled": True, "presentation_only": True, "profiles": {}, "view_user_ids": [], "view_department_ids": []},
        revision="original", sheets=[tender()], summary={})
    db.add(wb)
    dict_service.put(db, business_workbook.CONFIG, "primary", {"enabled": True, "source_key": wb.source_key})
    db.commit()
    prefix = f"{BASE}/workbook-tender/"
    query = "?sheet=tender-sheet&revision="
    saved = data(send(client, headers, prefix + "3" + query + "original", "note", "请复核两个标段"))
    ident = saved["items"][0]["id"]
    assert data(client.get(prefix + "2" + query + "original", headers=headers))["items"][0]["id"] == ident
    raw = tender()
    raw["merges"] = ["C4:C5"]
    raw["rows"][1]["row"], raw["rows"][2]["row"] = 4, 5
    wb.sheets = [raw]
    wb.revision = "moved"
    db.commit()
    assert client.get(prefix + "2" + query + "original", headers=headers).status_code == 409
    record = data(client.get(f"{BASE}/entries/{ident}", headers=headers))
    assert record["target"]["id"] == "4" and record["items"][0]["content"] == "请复核两个标段"
    progress = data(send(client, headers, prefix + "5" + query + "moved", "progress", "已核对标段",
                         status="done", expected_progress=record["progress_revision"]))
    assert not progress["legacy"]
    legacy = data(client.get("/api/v1/business-workbook/tender/rows/tender-sheet/4/feedback?revision=moved", headers=headers))
    assert legacy["items"][0]["content"] == "已核对标段"
    from sqlalchemy import delete
    from app.models import SysDict
    db.execute(delete(SysDict).where(SysDict.dict_type == business_workbook.CONFIG, SysDict.dict_key == "primary"))
    db.commit()
