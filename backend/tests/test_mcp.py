"""FR-10 MCP Server：协议握手、工具发现、鉴权（无/错/吊销/停用令牌）、角色越权、热配置、限流、审计、业务工具。"""
import json

import pytest
from sqlalchemy import select

from tests.conftest import data

H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
     "MCP-Protocol-Version": "2025-06-18"}
_tokens: dict[str, str] = {}


@pytest.fixture(scope="module")
def mcp_token(client, login):
    def get(userid):
        if userid not in _tokens:
            _tokens[userid] = data(client.post("/api/v1/auth/mcp-token/reset", headers=login(userid)))["token"]
        return _tokens[userid]
    return get


def rpc(client, token, method, params=None, rid=1):
    hdr = {**H, **({"Authorization": f"Bearer {token}"} if token else {})}
    return client.post("/mcp", headers=hdr, json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})


def call(client, token, tool, **args):
    r = rpc(client, token, "tools/call", {"name": tool, "arguments": args})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    return json.loads(res["content"][0]["text"])


def test_initialize_and_list_tools(client, mcp_token):
    t = mcp_token("chenjing")
    r = rpc(client, t, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "workbuddy-test", "version": "1"}})
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "jucheng-portal"
    names = {x["name"] for x in rpc(client, t, "tools/list").json()["result"]["tools"]}
    assert {"fin.daily_expense_report", "fin.project_profit", "fin.bank_flow_sync", "oa.pending_approvals",
            "crm.contract_register", "board.executive_daily", "kb.search", "kb.bid_check", "hr.attendance_summary",
            "proj.milestone_update", "cs.ticket_register"} <= names


def test_no_token_and_bad_token_rejected_and_audited(client, db):
    from app.models import LogMcpAudit
    before = db.scalar(select(LogMcpAudit.id).order_by(LogMcpAudit.id.desc())) or 0
    assert rpc(client, None, "tools/list").status_code == 401
    assert rpc(client, "jcmcp_wrong", "tools/list").status_code == 401
    db.expire_all()
    rows = db.scalars(select(LogMcpAudit).where(LogMcpAudit.id > before)).all()
    assert len([r for r in rows if r.outcome == "unauthorized"]) >= 2


def test_token_reset_revokes_old(client, login):
    old = data(client.post("/api/v1/auth/mcp-token/reset", headers=login("wangting")))["token"]
    assert rpc(client, old, "tools/list").status_code == 200
    new = data(client.post("/api/v1/auth/mcp-token/reset", headers=login("wangting")))["token"]
    assert rpc(client, old, "tools/list").status_code == 401
    assert rpc(client, new, "tools/list").status_code == 200


def test_disabled_user_token_rejected(client, login, db):
    from app.models import SysUser
    tok = data(client.post("/api/v1/auth/mcp-token/reset", headers=login("jinbo")))["token"]
    uid = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "jinbo"))
    client.put(f"/api/v1/admin/users/{uid}/status", json={"status": 2}, headers=login("admin"))
    assert rpc(client, tok, "tools/list").status_code == 401


def test_role_denied_is_structured_and_audited(client, mcp_token, db):
    from app.models import LogMcpAudit
    r = call(client, mcp_token("zhengyu"), "fin.project_profit")  # 市场专员无财务权限
    assert r["ok"] is False and "无权" in r["message"]
    db.expire_all()
    last = db.scalar(select(LogMcpAudit).where(LogMcpAudit.tool_code == "fin.project_profit").order_by(LogMcpAudit.id.desc()))
    assert last.outcome == "denied"


def test_hot_config_disable_and_grant(client, login, mcp_token):
    t = mcp_token("zhengyu")
    client.patch("/api/v1/mcp-config/tools/oa.pending_approvals", json={"enabled": False}, headers=login("admin"))
    assert call(client, t, "oa.pending_approvals")["ok"] is False
    client.patch("/api/v1/mcp-config/tools/oa.pending_approvals", json={"enabled": True}, headers=login("admin"))
    assert call(client, t, "oa.pending_approvals")["ok"] is True
    # 管理员授权市场部调用 crm.contract_register → 立即生效
    client.patch("/api/v1/mcp-config/tools/crm.contract_register", json={"allowed_roles": ["YX", "ZB", "ADMIN", "SC"]},
                 headers=login("admin"))
    assert call(client, t, "crm.contract_register")["ok"] is True
    client.patch("/api/v1/mcp-config/tools/crm.contract_register", json={"allowed_roles": ["YX", "ZB", "ADMIN"]},
                 headers=login("admin"))
    assert call(client, t, "crm.contract_register")["ok"] is False
    # 非管理员不能改注册表
    assert client.patch("/api/v1/mcp-config/tools/kb.search", json={"enabled": False},
                        headers=login("chenjing")).json()["code"] == 40301


def test_rate_limit(client, mcp_token, monkeypatch):
    from app.core.config import settings
    from app.mcp_server import server
    monkeypatch.setattr(settings, "mcp_rate_limit_per_min", 3)
    t = mcp_token("hanxiao")
    server._calls.clear()
    outs = [call(client, t, "oa.pending_approvals")["ok"] for _ in range(4)]
    assert outs == [True, True, True, False]
    server._calls.clear()


def test_params_masked_in_audit(client, mcp_token, db):
    from app.models import LogMcpAudit
    call(client, mcp_token("yangyue"), "cs.ticket_register", text="登记客户 测试学校 反馈：联系人电话13812345678 大屏无信号")
    db.expire_all()
    a = db.scalar(select(LogMcpAudit).where(LogMcpAudit.tool_code == "cs.ticket_register").order_by(LogMcpAudit.id.desc()))
    assert "13812345678" not in json.dumps(a.params, ensure_ascii=False)


def test_pending_approvals_and_contract_register(client, login, mcp_token):
    t = mcp_token("songjia")
    r = call(client, t, "oa.pending_approvals", type="submitted")
    assert r["ok"] and isinstance(r["data"]["items"], list)
    # 审批通过但回调丢失 → MCP 一句话登记
    sp = data(client.post("/api/v1/dev/mock/wecom-approval", headers=login("admin"), json={
        "template": "contract", "applicant": "songjia", "deliver": "drop",
        "fields": {"合同名称": "MCP 登记测试", "客户名称": "MCP测试学校", "合同金额": "666000", "业务线": "XY"}}))["sp_no"]
    r = call(client, t, "crm.contract_register", approval_no=sp)
    assert r["ok"] and any(x["customer"] == "MCP测试学校" for x in r["data"]["registered"])
    assert "当月签约" in r["summary"]


def test_config_and_selftest_endpoints(client, login, mcp_token):
    cfg = data(client.get("/api/v1/mcp-config/config", headers=login("chenjing")))
    assert cfg["workbuddy"]["mcpServers"]["jucheng-portal"]["url"].endswith("/mcp")
    res = data(client.post("/api/v1/mcp-config/test", json={"token": mcp_token("chenjing")}, headers=login("chenjing")))
    assert res["ok"] and len(res["tools"]) >= 11
    bad = data(client.post("/api/v1/mcp-config/test", json={"token": "x"}, headers=login("chenjing")))
    assert bad["ok"] is False and bad["status"] == 401
    st = data(client.get("/api/v1/mcp-config/stats", headers=login("admin")))
    assert st["total"] > 0


def test_report_tools_return_file_links(client, mcp_token, login, db):
    """回归：报表类工具必须返回可下载的签名链接（文件在同一事务内生成）。"""
    t = mcp_token("chenjing")
    from tests.conftest import add_project_test_members
    add_project_test_members(db, ["JC-XY-2026-001"], "chenjing")
    for tool in ("fin.daily_expense_report", "fin.project_profit"):
        r = call(client, t, tool)
        assert r["ok"] and r["files"], (tool, r)
        url = r["files"][0]["url"]
        assert client.get(url[url.index("/api/"):], headers=login("chenjing")).status_code == 200
    r = call(client, t, "fin.daily_expense_report", date="2026-10-19")
    assert "报销" in r["summary"] and "共 0 笔" not in r["summary"]
