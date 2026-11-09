"""运营项目会议需求：跨部门授权、任务反馈闭环、资金口径和 AI 共用入口。"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.deps import build_principal
from app.core.response import BizError
from app.models import SysNotify, SysUser
from tests.conftest import data

BASE = "/api/v1/operations"


def uid(db, name):
    return db.scalar(select(SysUser.id).where(SysUser.wecom_userid == name))


@pytest.fixture
def project(client, login, db):
    return data(client.post(BASE, headers=login("admin"), json={
        "name": f"智慧校园验收-{uuid.uuid4().hex[:8]}", "owner_id": uid(db, "wujun"),
        "objective": "完成目标学校对接和设备建设", "category": "智慧校园"}))


def member(client, login, db, project, name="xiekai", role="member"):
    return data(client.post(f"{BASE}/{project['id']}/members", headers=login("admin"),
                           json={"user_id": uid(db, name), "role": role}))


def task(client, login, db, project, name="xiekai", **kw):
    member(client, login, db, project, name)
    return data(client.post(f"{BASE}/{project['id']}/tasks", headers=login("wujun"), json={
        "title": "完成学校方案确认", "owner_id": uid(db, name), "due_date": "2026-10-25", **kw}))


def report(client, login, task, name="xiekai", **kw):
    return client.post(f"{BASE}/tasks/{task['id']}/feedback", headers=login(name), json={
        "content": "已联系校方，等待方案反馈", "status": "doing", "progress": 40, "next_step": "明天确认方案", "incomplete_reason": "等待校方确认", **kw})


def test_membership_is_independent_of_department_data_scope(client, login, db, project):
    # 财务虽有 all 数据范围，也不能读取未参与的运营项目。
    assert client.get(f"{BASE}/{project['id']}", headers=login("chenjing")).status_code == 404
    assert project["id"] not in [p["id"] for p in data(client.get(BASE, headers=login("chenjing")))["items"]]
    member(client, login, db, project)
    r = data(client.get(f"{BASE}/{project['id']}", headers=login("xiekai")))
    assert r["my_role"] == "member" and not r["can_manage"]
    assert "operations" in data(client.get("/api/v1/auth/me", headers=login("xiekai")))["menus"]


def test_project_progress_keeps_plan_date_history_and_task_state(client, login, db, project):
    item = task(client, login, db, project)
    base = f"{BASE}/{project['id']}"
    payload = {"progress": "已联系校方", "problems": "等清单", "next_plan": "复核设备"}
    assert client.get(base + "/progress", headers=login("chenjing")).status_code == 404
    assert client.post(base + "/progress", headers=login("xiekai"), json=payload | {"next_due_date": "2026-10-22"}).status_code == 403
    assert client.post(base + "/progress", headers=login("wujun"), json=payload).status_code == 400
    first = data(client.post(base + "/progress", headers=login("wujun"), json=payload | {"next_due_date": "2026-10-22"}))
    second = data(client.post(base + "/progress", headers=login("wujun"), json=payload | {
        "progress": "设备清单已收到", "problems": "", "next_due_date": "2026-10-23"}))
    viewer = login("xiekai")
    history = data(client.get(base + "/progress", headers=viewer))["items"]
    assert [record["id"] for record in history] == [second["id"], first["id"]]
    assert history[1]["problems"] == "等清单" and history[1]["next_due_date"] == "2026-10-22"
    result = data(client.get(base, headers=viewer))
    assert result["latest_progress"]["next_due_date"] == "2026-10-23"
    assert (result["tasks"][0]["id"], result["tasks"][0]["status"]) == (item["id"], "todo")
    assert "复核设备（计划完成：2026-10-23）" in data(client.get(base + "/brief", headers=viewer))["text"]


@pytest.mark.parametrize("name, allowed", [("zhangzhenhua", True), ("wujun", True), ("xuhao", False), ("chenjing", False)])
def test_project_creation_authority(client, login, db, name, allowed):
    r = client.post(BASE, headers=login(name), json={"name": "新运营项目", "owner_id": uid(db, name)})
    assert r.status_code == (200 if allowed else 403)


def test_assign_only_active_executing_members(client, login, db, project):
    body = {"title": "对接", "owner_id": uid(db, "xiekai"), "due_date": "2026-10-25"}
    path = f"{BASE}/{project['id']}/tasks"
    assert client.post(path, headers=login("admin"), json=body).status_code == 400
    member(client, login, db, project, role="viewer")
    assert client.post(path, headers=login("admin"), json=body).status_code == 400
    member(client, login, db, project)
    assert client.post(path, headers=login("xiekai"), json=body).status_code == 403
    assert client.post(path, headers=login("admin"), json=body).status_code == 200


def test_feedback_nodes_completion_history_and_notifications(client, login, db, project):
    t = task(client, login, db, project, nodes=[{"name": "确认需求", "due_date": "2026-10-21"}, {"name": "提交方案", "due_date": "2026-10-25"}])
    assert t["needs_update"] and t["progress"] == 0
    assert report(client, login, t, status="done", progress=100).status_code == 400
    first = data(report(client, login, t, completed_node_ids=[t["nodes"][0]["id"]]))
    assert first["progress"] == 40 and not first["needs_update"]
    assert first["nodes"][0]["completed_at"] and not first["nodes"][1]["completed_at"]
    final = data(report(client, login, t, status="done", progress=100, content="方案已获确认", completed_node_ids=[t["nodes"][1]["id"]]))
    assert final["progress"] == 100 and final["status"] == "done"
    assert len(final["updates"]) == 3 and "确认需求" in final["updates"][1]["content"]
    assert report(client, login, t).status_code == 409
    notices = db.scalars(select(SysNotify).where(SysNotify.ref_type == "ops_task", SysNotify.ref_id == str(t["id"]))).all()
    assert len(notices) >= 3 and {n.channel for n in notices} == {"inapp"}
    assert any(n.user_id == uid(db, "xiekai") for n in notices)
    assert any(n.user_id == uid(db, "wujun") for n in notices)


def test_feedback_rejects_other_members_and_foreign_nodes(client, login, db, project):
    t = task(client, login, db, project)
    member(client, login, db, project, "yangyue")
    assert report(client, login, t, "yangyue").status_code == 403
    member(client, login, db, project, "caofang", "viewer")
    assert report(client, login, t, "caofang").status_code == 403
    other = task(client, login, db, project, nodes=[{"name": "其他任务节点", "due_date": "2026-10-24"}])
    assert report(client, login, t, completed_node_ids=[other["nodes"][0]["id"]]).status_code == 400
    assert data(client.get(f"{BASE}/tasks/{t['id']}", headers=login("xiekai")))["status"] == "todo"
    assert report(client, login, t, content="   ").status_code == 400
    assert report(client, login, t, status="doing", progress=100).status_code == 400


def test_member_removal_transfer_and_revocation(client, login, db, project):
    t = task(client, login, db, project)
    path = f"{BASE}/{project['id']}/members/{uid(db, 'xiekai')}"
    assert client.delete(path, headers=login("admin")).status_code == 409
    assert client.post(f"{BASE}/{project['id']}/members", headers=login("admin"), json={"user_id": uid(db, "xiekai"), "role": "viewer"}).status_code == 409
    member(client, login, db, project, "xuhao")
    data(client.put(f"{BASE}/tasks/{t['id']}", headers=login("admin"), json={"owner_id": uid(db, "xuhao")}))
    assert client.delete(path, headers=login("admin")).status_code == 200
    for endpoint in (f"/{project['id']}", f"/{project['id']}/brief", f"/tasks/{t['id']}"):
        assert client.get(BASE + endpoint, headers=login("xiekai")).status_code == 404
    assert report(client, login, t).status_code == 404
    assert data(report(client, login, t, "xuhao"))["owner_name"]


def test_overdue_nodes_and_todo_summary(client, login, db, project):
    t = task(client, login, db, project, nodes=[{"name": "需要先确认", "due_date": "2026-10-19"}], cadence="node")
    assert t["overdue"] and not t["needs_update"]
    data(report(client, login, t, status="blocked", content="校方联系人暂未回复"))
    r = data(client.get(f"{BASE}/{project['id']}/brief", headers=login("wujun")))
    assert r["project"]["stats"]["blocked"] == 1 and r["project"]["stats"]["overdue"] == 1
    assert "遇到阻碍" in r["text"] and "明天确认方案" in r["text"]


def test_paused_project_prevents_new_execution(client, login, db, project):
    t = task(client, login, db, project)
    data(client.put(f"{BASE}/{project['id']}", headers=login("admin"), json={"name": project["name"], "owner_id": project["owner_id"], "status": "paused"}))
    assert report(client, login, t).status_code == 409
    assert not data(client.get(f"{BASE}/tasks/{t['id']}", headers=login("xiekai")))["can_feedback"]
    assert t["id"] not in [t["id"] for t in data(client.get(BASE, headers=login("wujun")))["todos"]]


def test_school_equipment_totals_and_income_correction(client, login, db, project):
    h = login("admin"); base = f"{BASE}/{project['id']}"
    s = data(client.post(base + "/schools", headers=h, json={"name": "实验学校", "labor_cost": "100.20", "other_cost": "9.99"}))
    p = data(client.post(base + "/products", headers=h, json={"name": "校园终端", "reference_price": "300.00"}))
    data(client.post(base + f"/schools/{s['id']}/equipment", headers=h, json={"product_id": p["id"], "quantity": 3, "unit_price": "20.01"}))
    # 产品参考价变更不得重写已经登记的实际采购投入。
    data(client.put(base + f"/products/{p['id']}", headers=h, json={"name": "校园终端", "reference_price": "999.00"}))
    path = base + f"/schools/{s['id']}/income"
    inc = data(client.post(path, headers=h, json={"income_date": "2026-10-19", "amount": "32.01"}))
    assert client.post(path, headers=h, json={"income_date": "2026-10-19", "amount": "32.01"}).status_code == 409
    data(client.put(path + f"/{inc['id']}", headers=h, json={"income_date": "2026-10-19", "amount": "42.03"}))
    r = data(client.get(base, headers=h))
    assert Decimal(r["financials"]["total_investment"]) == Decimal("170.22")
    assert Decimal(r["financials"]["total_income"]) == Decimal("42.03")
    assert len(r["schools"][0]["incomes"]) == 1
    assert r["financials"]["daily"] == [{"date": "2026-10-19", "amount": "42.03"}]
    assert client.post(path, headers=h, json={"income_date": "2026-10-21", "amount": "0"}).status_code == 400
    assert client.post(path, headers=h, json={"income_date": "2026-10-18", "amount": "-1"}).status_code == 400
    assert client.post(path, headers=h, json={"income_date": "2026-10-18", "amount": "0.001"}).status_code == 400


def test_nested_school_isolation_and_viewer_read_only(client, login, db, project):
    h = login("admin"); base = f"{BASE}/{project['id']}"
    s = data(client.post(base + "/schools", headers=h, json={"name": "本项目学校"}))
    other = data(client.post(BASE, headers=h, json={"name": "另一运营项目", "owner_id": uid(db, "admin")}))
    otherbase = f"{BASE}/{other['id']}"
    assert client.post(otherbase + f"/schools/{s['id']}/income", headers=h, json={"income_date": "2026-10-20", "amount": "5"}).status_code == 404
    member(client, login, db, project, role="viewer")
    assert client.post(base + "/products", headers=login("xiekai"), json={"name": "未授权写入"}).status_code == 403
    assert client.post(base + "/members", headers=login("xiekai"), json={"user_id": uid(db, "caofang"), "role": "manager"}).status_code == 403
    data(client.put(base + f"/schools/{s['id']}", headers=h, json={"name": "本项目学校", "stage": "proposal", "next_action": "下周提交方案"}))
    history = data(client.get(base + f"/schools/{s['id']}/history", headers=login("xiekai")))["items"]
    assert len(history) == 2 and history[0]["before"]["stage"] == "contact" and history[0]["after"]["stage"] == "proposal"
    assert client.get(base + f"/schools/{s['id']}/history", headers=login("caofang")).status_code == 404


def test_cross_project_product_library_requires_access(client, login, db, project):
    h = login("admin")
    p = data(client.post(f"{BASE}/{project['id']}/products", headers=h, json={"name": "共享设备"}))
    member(client, login, db, project)
    assert p["id"] in [x["id"] for x in data(client.get(BASE + "/products", headers=login("xiekai")))["items"]]
    assert p["id"] not in [x["id"] for x in data(client.get(BASE + "/products", headers=login("caofang")))["items"]]


def test_draft_only_changes_text_not_task(client, login, db, project, monkeypatch):
    from app.integration import llm
    t = task(client, login, db, project)
    monkeypatch.setattr(llm, "complete", lambda *args, **kwargs: "已联系校方，明天确认需求。")
    r = data(client.post(f"{BASE}/tasks/{t['id']}/draft", headers=login("xiekai"), json={"text": "今天联系了 明天确认"}))
    assert r["assisted"] and "明天" in r["text"]
    assert data(client.get(f"{BASE}/tasks/{t['id']}", headers=login("xiekai")))["progress"] == 0
    monkeypatch.setattr(llm, "complete", lambda *args, **kwargs: None)
    r = data(client.post(f"{BASE}/tasks/{t['id']}/draft", headers=login("xiekai"), json={"text": "保留原始文字"}))
    assert not r["assisted"] and r["text"] == "保留原始文字"


def test_xiaoju_confirmed_task_assignment_and_acl(client, login, db, project, tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services import xiaoju_business as business
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "var/files")
    member(client, login, db, project)
    runner = business.BusinessExecutor(uid(db, "wujun"), intent="给项目成员布置任务")
    discovered = runner.discover("智慧校园重点任务")
    assert any(op["operation_id"] == "operations.create_operation_task" for op in discovered["operations"])
    action = runner.execute("operations.create_operation_task", {"path": {"project_id": project["id"]}, "body": {
        "title": "通过小聚安排学校拜访", "owner_id": uid(db, "xiekai"), "due_date": "2026-10-26"}})["action"]
    assert action["state"] == "pending"
    result = business.confirm(action["id"], runner.user_id)["result"]
    tid = result["data"]["id"]
    assert data(client.get(f"{BASE}/tasks/{tid}", headers=login("xiekai")))["title"] == "通过小聚安排学校拜访"
    unauthorized = business.BusinessExecutor(uid(db, "caofang"), intent="查看项目")
    with pytest.raises(BizError):
        unauthorized.execute("operations.operation_detail", {"path": {"project_id": project["id"]}})


def test_mcp_reuses_feedback_validation_and_visibility(client, login, db, project):
    from app.mcp_server.server import t_ops_feedback, t_ops_status
    t = task(client, login, db, project)
    p = build_principal(db.get(SysUser, uid(db, "xiekai")))
    assert t_ops_status(db, p, task_id=t["id"])["data"]["id"] == t["id"]
    r = t_ops_feedback(db, p, t["id"], "通过 WorkBuddy 反馈", "doing", 30, "继续跟进")
    assert r["data"]["progress"] == 30
    with pytest.raises(BizError):
        t_ops_status(db, build_principal(db.get(SysUser, uid(db, "caofang"))), project_id=project["id"])


def test_deadline_reminder_is_inapp_and_idempotent(client, login, db, project):
    from app.jobs.operations import operations_task_reminder
    t = task(client, login, db, project, due_date="2026-10-19")
    operations_task_reminder(db, {})
    q = select(SysNotify).where(SysNotify.ref_type == "ops_task_reminder", SysNotify.ref_id == f"{t['id']}:2026-10-20")
    rows = db.scalars(q).all()
    assert {r.user_id for r in rows} == {uid(db, "xiekai"), project["owner_id"]}
    assert {r.channel for r in rows} == {"inapp"}
    operations_task_reminder(db, {})
    assert len(db.scalars(q).all()) == 2


def test_transfer_project_keeps_old_owner_task_access(client, login, db, project):
    h = login("admin")
    old_id = uid(db, "xuhao"); new_id = uid(db, "xiekai")
    base = f"{BASE}/{project['id']}"
    data(client.put(base, headers=h, json={"name": project["name"], "owner_id": old_id}))
    t = data(client.post(base + "/tasks", headers=h, json={"title": "交接前任务", "owner_id": old_id, "due_date": "2026-10-25"}))
    result = data(client.put(base, headers=h, json={"name": project["name"], "owner_id": new_id}))
    assert result["owner_id"] == new_id and result["owner_name"] == db.get(SysUser, new_id).name
    old = data(client.get(base, headers=login("xuhao")))
    assert old["my_role"] == "member" and not old["can_manage"]
    assert data(report(client, login, t, "xuhao"))["progress"] == 40


def test_workbuddy_protocol_operations_tools(client, login, db, project):
    from tests.test_mcp import call, rpc
    t = task(client, login, db, project)
    token = data(client.post("/api/v1/auth/mcp-token/reset", headers=login("xiekai")))["token"]
    names = {x["name"] for x in rpc(client, token, "tools/list").json()["result"]["tools"]}
    assert {"ops.project_status", "ops.task_feedback"} <= names
    assert call(client, token, "ops.project_status", task_id=t["id"])["data"]["id"] == t["id"]
    r = call(client, token, "ops.task_feedback", task_id=t["id"], content="WorkBuddy 协议反馈验证", status="doing", progress=25)
    assert r["ok"] and r["data"]["progress"] == 25
