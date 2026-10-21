"""接入助手：权限边界、凭证隔离、真实检测结果与持久化生效。外部平台使用 MockTransport。"""
import base64
import json
import stat

import httpx
import pytest
from sqlalchemy import select

from app.core.config import INTEGRATION_KEYS, Settings, load_integration_settings, settings
from app.models import LogOperation, SysUser
from app.services import assistant_service as service
from tests.conftest import data

WECOM = {"wecom_corp_id": "ww-assistant-test", "wecom_agent_id": "1000002", "wecom_secret": "test-enterprise-secret",
         "wecom_callback_token": "callback-test-token", "wecom_callback_aes_key": base64.b64encode(b"a" * 32).decode().rstrip("=")}
LEMON = {"lemoncloud_app_id": "test-lemon-app", "lemoncloud_app_secret": "test-lemon-secret"}
SUCCESS = {"ok": True, "activated": False, "message": "验证通过", "checks": ["授权通过"], "pending": ["后台授权"]}


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "files")
    for key in INTEGRATION_KEYS:
        monkeypatch.setattr(settings, key, 0 if key == "wecom_agent_id" else "")
    monkeypatch.setattr(settings, "data_source_mode", "mock")
    monkeypatch.setattr(settings, "llm_api_key", "")


def post(client, login, provider="wecom", values=None, save=True, user="admin"):
    return client.post(f"/api/v1/assistant/connectors/{provider}/configure", headers=login(user),
                       json={"values": WECOM if values is None else values, "save": save})


def test_login_required(client):
    assert client.get("/api/v1/assistant/catalog").status_code == 401
    assert client.post("/api/v1/assistant/chat", json={"message": "接入企业微信"}).status_code == 401


def test_catalog_never_returns_credentials(client, login, monkeypatch):
    monkeypatch.setattr(settings, "wecom_secret", "sensitive-value-must-not-appear")
    admin = data(client.get("/api/v1/assistant/catalog", headers=login("admin")))
    assert "sensitive-value-must-not-appear" not in json.dumps(admin)
    fields = admin["connectors"][1]["fields"]
    assert next(f for f in fields if f["key"] == "wecom_secret")["configured"]
    employee = data(client.get("/api/v1/assistant/catalog", headers=login("zengli")))
    assert employee["connectors"][0]["can_configure"]
    assert not employee["connectors"][1]["can_configure"]
    assert employee["connectors"][1]["fields"] == []


@pytest.mark.parametrize("message,expected", [("帮我接入workbuddyy", ["workbuddy"]), ("配置企微", ["wecom"]),
                                            ("柠檬云怎么授权", ["lemoncloud"]),
                                            ("检查所有连接", ["workbuddy", "wecom", "lemoncloud"])])
def test_natural_language_tools(client, login, message, expected):
    r = data(client.post("/api/v1/assistant/chat", headers=login("admin"), json={"message": message}))
    assert r["providers"] == expected
    assert not (settings.storage_dir.parent / "integrations.json").exists()


def test_followup_and_navigation_permissions(client, login):
    r = data(client.post("/api/v1/assistant/chat", headers=login("admin"), json={"message": "下一步呢", "context": ["wecom"]}))
    assert r["providers"] == ["wecom"]
    fin = data(client.post("/api/v1/assistant/chat", headers=login("chenjing"), json={"message": "报销在哪"}))
    assert fin["links"] == [{"label": "报销归集", "path": "/expenses"}]
    employee = data(client.post("/api/v1/assistant/chat", headers=login("zengli"), json={"message": "打开合同台账"}))
    assert employee["links"] == []


def test_chat_does_not_reset_token(client, login, db):
    user = db.scalar(select(SysUser).where(SysUser.wecom_userid == "admin"))
    previous = user.access_token
    data(client.post("/api/v1/assistant/chat", headers=login("admin"), json={"message": "帮我一键接入WorkBuddy"}))
    db.refresh(user)
    assert user.access_token == previous


def test_secret_in_chat_never_reaches_model(client, login, monkeypatch):
    monkeypatch.setattr(service, "_llm_enabled", lambda: True)
    from app.integration import llm
    def forbidden_call(*args, **kwargs):
        pytest.fail("credential reached model")
    monkeypatch.setattr(llm, "complete", forbidden_call)
    r = data(client.post("/api/v1/assistant/chat", headers=login("admin"), json={"message": "Secret=private-secret-value"}))
    assert "private-secret-value" not in json.dumps(r)
    assert "凭证输入框" in r["message"]


def test_model_actions_are_allowlisted(client, login, monkeypatch):
    monkeypatch.setattr(service, "_llm_enabled", lambda: True)
    from app.integration import llm
    monkeypatch.setattr(llm, "complete", lambda *args, **kwargs: '{"providers":["shell","wecom"],"paths":["https://evil.example","/admin"]}')
    r = data(client.post("/api/v1/assistant/chat", headers=login("zengli"), json={"message": "连接组织的办公应用"}))
    assert r["providers"] == ["wecom"]
    assert not r["links"]
    assert "管理员" in r["message"]


def test_non_admin_cannot_test_or_save(client, login, monkeypatch):
    monkeypatch.setattr(service, "probe", lambda *args: pytest.fail("unauthorized network access"))
    assert post(client, login, user="chenjing").status_code == 403
    assert post(client, login, user="zengli", save=False).status_code == 403


@pytest.mark.parametrize("change", [{"arbitrary_url": "http://localhost/"}, {"wecom_agent_id": "-1"}, {"wecom_callback_aes_key": "bad"}])
def test_invalid_settings_never_reach_network(client, login, monkeypatch, change):
    monkeypatch.setattr(service, "probe", lambda *args: pytest.fail("invalid config reached network"))
    assert post(client, login, values={**WECOM, **change}).status_code == 400


def test_failed_probe_does_not_save_or_enable(client, login, monkeypatch):
    monkeypatch.setattr(service, "probe", lambda *args: {**SUCCESS, "ok": False, "message": "授权失败"})
    r = data(post(client, login))
    assert not r["ok"] and not r["saved"] and not r["activated"]
    assert settings.source_mode("wecom") == "mock"
    assert not (settings.storage_dir.parent / "integrations.json").exists()


def test_test_only_does_not_change_configuration(client, login, monkeypatch):
    monkeypatch.setattr(service, "probe", lambda *args: dict(SUCCESS))
    r = data(post(client, login, save=False))
    assert r["ok"] and not r["saved"] and not r["activated"]
    assert settings.wecom_secret == ""


def test_activation_persists_reloads_and_masks_audit(client, login, monkeypatch, db):
    monkeypatch.setattr(service, "probe", lambda *args: dict(SUCCESS))
    from app.integration.wecom.live import LiveWeCom
    monkeypatch.setattr(LiveWeCom, "_token", "previous-cached-token")
    r = data(post(client, login))
    assert r["saved"] and r["activated"]
    assert settings.source_mode("wecom") == "live"
    assert settings.source_mode("lemoncloud") == "mock"
    assert LiveWeCom._token is None
    path = settings.storage_dir.parent / "integrations.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    restored = Settings(storage_dir=settings.storage_dir)
    load_integration_settings(restored)
    assert restored.wecom_secret == WECOM["wecom_secret"]
    assert restored.source_mode("wecom") == "live"
    audit = db.scalar(select(LogOperation).where(LogOperation.action == "assistant.configure").order_by(LogOperation.id.desc()))
    assert WECOM["wecom_secret"] not in json.dumps(audit.after)
    assert WECOM["wecom_callback_aes_key"] not in json.dumps(audit.after)
    # 留空沿用，避免管理员为再次检测而重新输入已有密钥。
    r = data(post(client, login, values={}, save=False))
    assert r["ok"]


def test_lemon_authorization_does_not_claim_financial_sync(client, login, monkeypatch):
    monkeypatch.setattr(service, "probe", lambda *args: dict(SUCCESS))
    r = data(post(client, login, provider="lemoncloud", values=LEMON))
    assert r["ok"] and r["saved"] and not r["activated"]
    assert settings.source_mode("lemoncloud") == "mock"
    assert "联调" in r["message"]


def mock_http(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(service.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_wecom_probe_checks_agent_and_does_not_leak_platform_response(monkeypatch):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("gettoken"):
            return httpx.Response(200, json={"access_token": "do-not-return-this-token", "expires_in": 7200})
        return httpx.Response(200, json={"errcode": 60020, "errmsg": "echoed test-enterprise-secret"})
    mock_http(monkeypatch, handler)
    result = service.probe("wecom", service.candidate("wecom", WECOM))
    assert not result["ok"] and "可信 IP" in result["message"]
    assert "test-enterprise-secret" not in json.dumps(result)
    assert paths == ["/cgi-bin/gettoken", "/cgi-bin/agent/get"]


def test_wecom_probe_success(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"access_token": "test-token"} if request.url.path.endswith("gettoken") else {"agentid": 1000002})
    mock_http(monkeypatch, handler)
    result = service.probe("wecom", service.candidate("wecom", WECOM))
    assert result["ok"] and result["pending"] and not result["activated"]


@pytest.mark.parametrize("body,expected", [({"access_token": "test-token"}, True), ({"message": "ok"}, False), ([1], False)])
def test_lemon_probe_requires_actual_token(monkeypatch, body, expected):
    def handler(request):
        assert request.url.host == "open.ningmengyun.com"
        assert request.url.path == "/api/OAuth2/AccessToken"
        return httpx.Response(200, json=body)
    mock_http(monkeypatch, handler)
    result = service.probe("lemoncloud", LEMON)
    assert result["ok"] is expected
    assert "test-token" not in json.dumps(result)


def test_write_failure_keeps_previous_runtime_configuration(client, login, monkeypatch):
    monkeypatch.setattr(service, "probe", lambda *args: dict(SUCCESS))
    from app.core import integration_config
    def fail(*args):
        raise PermissionError("private-path")
    monkeypatch.setattr(integration_config, "save", fail)
    r = post(client, login)
    assert r.status_code == 500 and "private-path" not in r.text
    assert settings.source_mode("wecom") == "mock" and not settings.wecom_secret


def test_workbuddy_configuration_uses_real_mcp_handshake(client, login):
    token = data(client.post("/api/v1/auth/mcp-token/reset", headers=login("zengli")))["token"]
    result = data(client.post("/api/v1/mcp-config/test", headers=login("zengli"), json={"token": token}))
    assert result["ok"] and "kb.search" in result["tools"]
