"""小聚实际业务执行：原 API/MCP、数据范围、附件、文件、确认与重试。只在 TEST_DB 运行。"""
import io
import stat
import time
import uuid

import pytest
from fastapi.routing import APIRoute
from openpyxl import load_workbook
from sqlalchemy import func, select

from app.core.config import settings
from app.core.response import BizError
from app.models import BizContract, BizProject, CsTicket, FinReceipt, SysUser
from app.services import xiaoju_business as business
from app.services import xiaoju_business_catalog as catalog
from app.services import xiaoju_business_state as state
from app.services import xiaoju_service, xiaoju_tools
from tests.conftest import data


@pytest.fixture(autouse=True)
def isolated_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "var/files")


def uid(db, name="admin"):
    return db.scalar(select(SysUser.id).where(SysUser.wecom_userid == name))


def executor(db, name="admin", **kwargs):
    return business.BusinessExecutor(uid(db, name), intent="登记并更新业务记录，生成报告", **kwargs)


def new_contract(db, name="小聚执行测试"):
    runner = executor(db)
    body = {"contract_no": "XJ-" + uuid.uuid4().hex[:12], "name": name, "customer_name": "小聚测试客户",
            "amount": "18000", "sign_date": "2026-10-20", "business_line": "XY"}
    pending = runner.execute("contracts.create", {"body": body})
    return business.confirm(pending["action"]["id"], runner.user_id)["result"]["data"]


def test_catalog_covers_real_business_routes_and_mcp(db):
    from app.main import app
    ops = catalog.operations()
    routes = {(r.path, method) for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/v1/")
              and r.endpoint.__module__.rsplit(".", 1)[-1] not in {"assistant", "files", "dev", "wecom"}
              and f"{r.endpoint.__module__.rsplit('.', 1)[-1]}.{r.name}" not in catalog.ROUTE_EXCLUSIONS
              for method in r.methods if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}}
    assert routes == {(o.path, o.method) for o in ops.values() if o.source == "api"}
    assert {o.mcp_code for o in ops.values() if o.source == "mcp"} == set(catalog.MCP_IMPLS)
    assert set(catalog.MODULES) - {"wecom_cli"} == {o.module for o in ops.values()}
    assert not (set(ops) & catalog.ROUTE_EXCLUSIONS)
    assert {op.id for op in ops.values() if op.module == "auth"} == {"auth.me", "auth.save_preferences"}
    assert not any("/assistant/" in o.path or o.path.startswith("/api/v1/files") for o in ops.values())
    assert ops["attachments.preview"].effect == "query"


def test_catalog_obeys_menu_button_and_mcp_grants(db):
    with business.SessionLocal() as session:
        employee = catalog.available(business.principal(uid(db, "zengli")), session)
        finance = catalog.available(business.principal(uid(db, "chenjing")), session)
        manager = catalog.available(business.principal(uid(db, "zhaoli")), session)
    assert "hr.my_profile" in employee and "kb.search" in employee
    assert "admin.users" not in employee and "contracts.create" not in employee
    assert "contracts.create" not in finance and "contracts.archive" in finance
    assert "service.register" not in manager  # 菜单可见但没有工单写按钮
    with pytest.raises(BizError, match="无权"):
        business.BusinessExecutor(uid(db, "zengli")).execute("admin.users")


def test_receipt_workflow_discovery_and_confirmed_real_write(db, client, login):
    runner = executor(db)
    receipt_ref = "XJ-RC-" + uuid.uuid4().hex[:12].upper()
    result = data(client.get("/api/v1/assistant/business/capabilities", params={"area": "receivables", "query": "登记到账收款", "limit": 2}, headers=login("admin")))
    assert result["operations"][0]["operation_id"] == "receivables.record_receipt"
    body = {"reference": receipt_ref, "received_on": "2026-10-20", "amount": "375.50", "payer": "验收付款方", "account": "验收账户", "kind": "other"}
    with pytest.raises(BizError, match="无权"):
        executor(db, "zengli").execute("receivables.record_receipt", {"body": body})
    pending = runner.execute("receivables.record_receipt", {"body": body})
    assert pending["requires_confirmation"] and not db.scalar(select(FinReceipt.id).where(FinReceipt.reference == receipt_ref))
    receipt = business.confirm(pending["action"]["id"], runner.user_id)["result"]
    assert receipt["ok"] and receipt["changed"]
    row = db.get(FinReceipt, receipt["data"]["id"])
    assert row.reference == receipt_ref and str(row.amount) == "375.50"
    assert business.confirm(pending["action"]["id"], runner.user_id)["reused"]
    pending = executor(db).execute("receivables.void", {"path": {"ident": row.id}, "body": {"reason": "验收更正"}})
    assert pending["action"]["before"]["data"]["reference"] == receipt_ref


def test_wecom_console_model_cannot_confirm_or_leak_credentials(db, monkeypatch):
    from app.integration.wecom import catalog as official
    from app.integration.wecom.credentials import Gateway, WeComApiError

    item = next(row for row in official.items() if row["path"] == "/cgi-bin/user/create")
    calls = []

    def fake(cls, credential, method, path, **kwargs):
        calls.append((method, path))
        raise WeComApiError(60011, "接口未授权", {"nested": {"access_token": "fake-credential"}})

    monkeypatch.setattr(Gateway, "call", classmethod(fake))
    request = {"id": item["id"], "payload": {"userid": "fixture"}}
    runner = executor(db)
    with pytest.raises(BizError, match="参数格式"):
        runner.execute("wecom_console.call", {"body": {**request, "confirm": True}})
    pending = runner.execute("wecom_console.call", {"body": request})
    assert pending["action"]["targets"]["接口地址"] == item["path"] and not calls
    result = business.confirm(pending["action"]["id"], runner.user_id)["result"]
    assert calls == [("POST", item["path"])]
    assert not result["ok"] and not result["changed"] and "接口未授权" in result["message"]
    assert "fake-credential" not in str(business.read_result(result["result_id"], runner.user_id, full=True))


def test_xiaoju_feedback_snapshot_does_not_overwrite_newer_progress(db, client, login):
    headers = login("admin")
    post = lambda path, body: data(client.post("/api/v1" + path, headers=headers, json=body))
    owner = uid(db)
    project = post("/operations", {"name": "同步验收", "owner_id": owner})
    task = post(f"/operations/{project['id']}/tasks", {"title": "核对需求", "owner_id": owner, "due_date": "2026-10-31"})
    path = f"/operations/tasks/{task['id']}/feedback"
    runner = executor(db)
    pending = runner.execute("operations.operation_feedback", {"path": {"task_id": task["id"]},
                             "body": {"content": "小聚草稿", "status": "doing", "progress": 35}})
    assert pending["action"]["request"]["body"]["expected_revision"] == task["revision"]
    updated = post(path, {"content": "同事刚更新", "status": "doing", "progress": 60, "expected_revision": task["revision"]})
    with pytest.raises(BizError, match="已变化"):
        business.confirm(pending["action"]["id"], owner)
    assert client.post("/api/v1" + path, headers=headers, json={"content": "旧网页", "status": "doing", "progress": 20,
                       "expected_revision": task["revision"]}).status_code == 409
    assert updated["progress"] == data(client.get(f"/api/v1/operations/tasks/{task['id']}", headers=headers))["progress"] == 60


def test_actual_query_and_mcp_use_original_permissions(db, client, login):
    assert executor(db).execute("service.meta")["data"]["status"]
    employee = business.BusinessExecutor(uid(db, "zengli"))
    result = employee.execute("hr.my_profile")
    expected = data(client.get("/api/v1/hr/me", headers=login("zengli")))
    assert result["data"] == business.compact(expected) and result["ok"]
    approvals = employee.execute("mcp.oa.pending_approvals", {"body": {}})
    assert approvals["ok"] and approvals["data"]["summary"] and "data" in approvals["data"]
    own = executor(db, "xuhao").execute("projects.list_projects")
    assert own["data"]["items"]
    foreign = db.scalar(select(BizProject).where(BizProject.owner_id != uid(db, "xuhao"), BizProject.is_deleted.is_(False)))
    with pytest.raises(BizError):
        executor(db, "xuhao").execute("projects.detail", {"path": {"code": foreign.project_code}})


def test_reversible_ticket_writes_are_real_and_idempotent(db):
    runner = executor(db)
    marker = "小聚测试-" + uuid.uuid4().hex[:10]
    request = {"body": {"customer_name": marker, "problem_desc": "会议室录播设备无法联网"}}
    first = runner.execute("service.register", request)
    assert first["ok"] and not first.get("requires_confirmation")
    ticket = first["data"]["ticket"]
    db.expire_all()
    assert db.get(CsTicket, ticket["id"]).customer_name == marker
    repeated = business.BusinessExecutor(runner.user_id, workflow_id=runner.workflow_id, intent="登记工单").execute("service.register", request)
    assert repeated["reused"] and repeated["result_id"] == first["result_id"]
    assert db.scalar(select(func.count()).select_from(CsTicket).where(CsTicket.customer_name == marker)) == 1
    changed = runner.execute("service.set_status", {"path": {"ticket_id": ticket["id"]}, "body": {"status": 2}})
    assert changed["ok"]
    db.expire_all()
    assert db.get(CsTicket, ticket["id"]).status == 2


def test_query_only_and_missing_write_intent_cannot_modify(db):
    request = {"body": {"problem_desc": "不应写入的测试"}}
    with pytest.raises(BizError, match="只允许查询"):
        executor(db, read_only=True).execute("service.register", request)
    with pytest.raises(BizError, match="尚未要求"):
        business.BusinessExecutor(uid(db), intent="有哪些工单").execute("service.register", request)


def test_financial_confirmation_writes_once_and_rechecks_owner(db, client, login):
    runner = executor(db)
    no = "XJ-CNF-" + uuid.uuid4().hex[:10]
    request = {"body": {"contract_no": no, "customer_name": "确认单客户", "amount": "32000",
                         "sign_date": "2026-10-20", "business_line": "AF"}}
    pending = runner.execute("contracts.create", request)
    assert pending["requires_confirmation"] and db.scalar(select(BizContract.id).where(BizContract.contract_no == no)) is None
    action_id = pending["action"]["id"]
    assert client.post(f"/api/v1/assistant/business/actions/{action_id}/confirm", json={"approve": True}, headers=login("zengli")).status_code == 403
    first = data(client.post(f"/api/v1/assistant/business/actions/{action_id}/confirm", json={"approve": True}, headers=login("admin")))
    again = data(client.post(f"/api/v1/assistant/business/actions/{action_id}/confirm", json={"approve": True}, headers=login("admin")))
    assert first["result"]["ok"] and again["reused"]
    assert first["result"]["result_id"] == again["result"]["result_id"]
    assert db.scalar(select(func.count()).select_from(BizContract).where(BizContract.contract_no == no)) == 1


def test_partial_update_keeps_omitted_fields_and_checks_stale_snapshot(db):
    contract = new_contract(db)
    runner = executor(db)
    action = runner.execute("contracts.update", {"path": {"cid": contract["id"]}, "body": {"remark": "只改备注"}})["action"]
    assert action["request"]["body"] == {"remark": "只改备注"}
    assert action["before"]["data"]["amount"] == "18000.00"
    result = business.confirm(action["id"], runner.user_id)["result"]
    assert result["data"]["customer_name"] == "小聚测试客户" and result["data"]["amount"] == "18000.00"
    second = executor(db).execute("contracts.update", {"path": {"cid": contract["id"]}, "body": {"amount": "22000"}})["action"]
    row = db.get(BizContract, contract["id"]); row.remark = "其他同事已修改"; db.commit()
    with pytest.raises(BizError, match="已变化"):
        business.confirm(second["id"], runner.user_id)
    db.refresh(row)
    assert row.amount == 18000


def test_confirmation_cancel_expire_and_permission_revocation(db):
    contract = new_contract(db)
    def pending():
        return executor(db).execute("contracts.update", {"path": {"cid": contract["id"]}, "body": {"remark": uuid.uuid4().hex}})["action"]
    action = pending()
    assert business.confirm(action["id"], uid(db), False)["action"]["state"] == "cancelled"
    with pytest.raises(BizError): business.confirm(action["id"], uid(db))
    action = pending()
    stored = state.read("actions", action["id"], uid(db)); stored["expires_at"] = time.time() - 1; state.write("actions", stored)
    with pytest.raises(BizError, match="过期"): business.confirm(action["id"], uid(db))
    action = pending()
    user = db.get(SysUser, uid(db)); old_status = user.status
    try:
        user.status = 0; db.commit()
        with pytest.raises(BizError, match="停用"): business.confirm(action["id"], user.id)
    finally:
        user.status = old_status; db.commit()


def test_outbound_plan_freezes_recipients_and_source_environment(db, monkeypatch):
    runner = executor(db)
    action = runner.execute("submissions.remind", {"body": {"type": "weekly", "period": "2026-W42"}})["action"]
    assert action["data_mode"] == "mock" and action["targets"]["period"] == "2026-W42"
    assert action["request"]["body"]["user_ids"] == [item["user_id"] for item in action["targets"]["recipients"]]
    monkeypatch.setattr(settings, "data_source_mode", "live")
    with pytest.raises(BizError, match="数据源环境已变化"):
        business.confirm(action["id"], runner.user_id)


def test_schema_validation_blocks_unknown_paths_and_invalid_values(db):
    runner = executor(db)
    for request in ({"path": {"ticket_id": "../admin"}, "body": {"status": 2}},
                    {"path": {"ticket_id": 1}, "query": {"impersonate": "admin"}, "body": {"status": 2}},
                    {"url": "http://other-host"}, {"path": {"ticket_id": 1}, "body": {"status": "anything"}}):
        with pytest.raises(BizError): runner.execute("service.set_status", request)


def test_business_upload_preview_and_original_multipart_parser(db, client, login):
    from tests.test_hr import _march_excel
    user_id = uid(db, "caofang")
    response = client.post("/api/v1/assistant/business/files", headers=login("caofang"), files={"file": ("考勤.xlsx", _march_excel())})
    file = data(response)
    assert "path" not in file
    target = state.root() / "files" / (file["id"] + ".bin")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    preview = business.BusinessExecutor(user_id).execute("attachments.preview", {"body": {"file_id": file["id"]}})
    assert preview["data"]["sheets"][0]["rows"]
    with pytest.raises(BizError): executor(db).execute("attachments.preview", {"body": {"file_id": file["id"]}})
    result = executor(db, "caofang").execute("hr.import_attendance", {"files": {"file": file["id"]}})
    assert result["ok"] and result["data"]["period"] == "2026-03"
    with pytest.raises(BizError): state.upload(user_id, "credentials.env", b"fake")
    with pytest.raises(BizError): state.attachment("../../config", user_id)


def test_generated_report_and_binary_export_are_downloadable(db, client, login):
    runner = executor(db, "chenjing")
    report = runner.execute("reports.generate", {"body": {"report_type": "daily_expense", "period": "2026-10-20"}})
    assert report["ok"] and report["files"] and report["data_mode"] == "mock"
    for file in report["files"]:
        content = client.get(file["url"])
        assert content.status_code == 200
        if file["filename"].endswith(".xlsx"): assert "口径说明" in load_workbook(io.BytesIO(content.content)).sheetnames
    exported = runner.execute("contracts.export", {"query": {"business_line": "XY"}})
    assert exported["files"][0]["filename"].endswith(".xlsx")
    content = client.get(exported["files"][0]["url"])
    assert content.status_code == 200 and len(load_workbook(io.BytesIO(content.content)).sheetnames) >= 1
    result_id = exported["result_id"]
    assert data(client.get(f"/api/v1/assistant/business/results/{result_id}?full=true", headers=login("chenjing")))["data"]["files"]
    assert client.get(f"/api/v1/assistant/business/results/{result_id}", headers=login("zengli")).status_code == 403


def test_uncertain_write_is_never_automatically_submitted_twice(db, monkeypatch):
    runner = executor(db)
    request = {"body": {"problem_desc": "未知写入状态测试"}}
    attempts = []
    def interrupted(*args):
        attempts.append(1)
        raise RuntimeError("connection interrupted after commit")
    monkeypatch.setattr(business, "invoke", interrupted)
    with pytest.raises(RuntimeError): runner.execute("service.register", request)
    with pytest.raises(BizError, match="不能自动重复"):
        runner.execute("service.register", request)
    assert len(attempts) == 1


def test_business_error_is_factual_and_tracked(db):
    tools = xiaoju_tools.XiaoJuTools(uid(db), intent="更新工单")
    result = tools.execute("execute_business_operation", {"operation_id": "service.set_status", "request": {"path": {"ticket_id": 99999999}, "body": {"status": 2}}})
    assert not result["ok"] and "不存在" in result["message"]
    assert tools.business_errors and not tools.business.results


def test_resume_owner_history_files_and_confirmation_task_state(db, client, login, monkeypatch):
    monkeypatch.setattr(xiaoju_service, "runtime_info", lambda: {"available": True})
    calls = []
    monkeypatch.setattr(xiaoju_service._executor, "submit", lambda *args: calls.append(args))
    file = state.upload(uid(db), "计划.txt", "演示业务计划".encode())
    task = data(client.post("/api/v1/assistant/tasks", headers=login("admin"), json={"message": "维护合同备注", "attachment_ids": [file["id"]], "current_page": "/contracts"}))
    contract = new_contract(db)
    runner = executor(db, task_id=task["id"], workflow_id=task["workflow_id"])
    action = runner.execute("contracts.update", {"path": {"cid": contract["id"]}, "body": {"remark": "小聚任务测试"}})["action"]
    xiaoju_service._change(xiaoju_service.task_root(), task["id"], lambda value: value.update(state="awaiting_confirmation", business_actions=[action]))
    decision = data(client.post(f"/api/v1/assistant/business/actions/{action['id']}/confirm", headers=login("admin"), json={"approve": True}))
    updated = data(client.get(f"/api/v1/assistant/tasks/{task['id']}", headers=login("admin")))
    assert updated["business_results"][0]["result_id"] == decision["result"]["result_id"]
    resumed = data(client.post("/api/v1/assistant/tasks", headers=login("admin"), json={"message": "继续", "resume_task_id": task["id"], "history": [{"role": "user", "content": "之前的业务目标"}]}))
    assert resumed["workflow_id"] == task["workflow_id"] and resumed["attachment_ids"] == [file["id"]]
    # 完整会话以服务端保存的目标为准，客户端历史不能替换原对话。
    assert resumed["business_results"] and calls[-1][6][0]["content"] == "维护合同备注"
    assert "之前的业务目标" not in (xiaoju_service.task_root() / (resumed["id"] + ".json")).read_text()
    assert client.post("/api/v1/assistant/tasks", headers=login("zengli"), json={"message": "继续", "resume_task_id": task["id"]}).status_code == 403
    tools = xiaoju_tools.XiaoJuTools(uid(db))
    assert tools.execute("read_business_action", {"action_id": action["id"]})["result"]["ok"]
    assert tools.business.results


def test_unexecuted_model_success_text_cannot_complete_business(db, monkeypatch):
    import subprocess
    import sys
    monkeypatch.setattr(xiaoju_service, "runtime_info", lambda: {"available": True})
    monkeypatch.setattr(xiaoju_service._executor, "submit", lambda *args: None)
    task = xiaoju_service.create("登记一张客户工单", [], uid(db))
    original = subprocess.Popen
    program = 'import sys,json;sys.stdin.readline();print(json.dumps({"type":"done","message":"工单已经成功登记"}),flush=True);sys.stdin.read()'
    monkeypatch.setattr(xiaoju_service.subprocess, "Popen", lambda *args, **kwargs: original([sys.executable, "-c", program], **kwargs))
    xiaoju_service._run(xiaoju_service.task_root(), task["id"], "登记一张客户工单", [], uid(db))
    actual = xiaoju_service.read(task["id"], uid(db))
    assert actual["state"] == "waiting_input" and "尚未获得实际业务回执" in actual["message"]
    assert "工单已经成功登记" not in actual["message"]
