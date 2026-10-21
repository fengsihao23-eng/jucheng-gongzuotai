"""小聚自主接入：真实文件操作、令牌复用/恢复、权限、凭证隔离与任务状态。"""
import json
import stat
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import INTEGRATION_KEYS, settings
from app.core.security import hash_token
from app.models import SysUser
from app.services import xiaoju_service, xiaoju_tools
from tests.conftest import data


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "data/files")
    monkeypatch.setattr(settings, "assistant_host_home", tmp_path / "host")
    monkeypatch.setattr(settings, "assistant_host_access", True)
    for key in INTEGRATION_KEYS:
        monkeypatch.setattr(settings, key, 0 if key == "wecom_agent_id" else "")
    monkeypatch.setattr(settings, "data_source_mode", "mock")
    monkeypatch.setattr(settings, "llm_api_key", "")


@pytest.fixture
def tools(db):
    user_id = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "admin"))
    return xiaoju_tools.XiaoJuTools(user_id)


def test_workbuddy_merges_and_backups_existing_config(tools, monkeypatch, db):
    monkeypatch.setattr(xiaoju_tools, "check_mcp", lambda endpoint, token: {"ok": True, "tools_count": 13})
    target = settings.assistant_host_home / ".workbuddy/mcp.json"
    target.parent.mkdir(parents=True)
    original = b'{"extraSetting":true,"mcpServers":{"existing":{"command":"existing-cli","env":{"TOKEN":"keep-this-value"}}}}'
    target.write_bytes(original)
    result = tools.execute("install_workbuddy", {})
    assert result["ok"] and result["installed"] and result["changed"]
    current = json.loads(target.read_text())
    assert current["extraSetting"] is True
    assert current["mcpServers"]["existing"]["env"]["TOKEN"] == "keep-this-value"
    token = current["mcpServers"][settings.mcp_server_name]["headers"]["Authorization"][7:]
    assert token not in json.dumps(result)
    assert db.get(SysUser, tools.user_id).access_token == hash_token(token)
    assert Path(result["backup_path"]).read_bytes() == original
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(result["backup_path"]).stat().st_mode) == 0o600


def test_repeated_install_reuses_token_and_does_not_create_backups(tools, monkeypatch, db):
    monkeypatch.setattr(xiaoju_tools, "check_mcp", lambda endpoint, token: {"ok": True, "tools_count": 13})
    first = tools.execute("install_workbuddy", {})
    original = Path(first["config_path"]).read_bytes()
    current_hash = db.get(SysUser, tools.user_id).access_token
    second = tools.execute("install_workbuddy", {})
    db.expire_all()
    assert second["ok"] and not second["changed"]
    assert Path(first["config_path"]).read_bytes() == original
    assert db.get(SysUser, tools.user_id).access_token == current_hash
    assert not list(Path(first["config_path"]).parent.glob("*.bak"))


def test_connection_failure_restores_old_token_and_file(tools, monkeypatch, db):
    user = db.get(SysUser, tools.user_id)
    previous = user.access_token
    target = settings.assistant_host_home / ".workbuddy/mcp.json"
    target.parent.mkdir(parents=True)
    original = b'{"mcpServers":{"preserve":{"url":"https://example.com/mcp"}}}'
    target.write_bytes(original)
    monkeypatch.setattr(xiaoju_tools, "check_mcp", lambda endpoint, token: {"ok": False, "tools_count": 0})
    result = tools.execute("install_workbuddy", {})
    db.refresh(user)
    assert not result["ok"] and result["status"] == "failed"
    assert user.access_token == previous and target.read_bytes() == original


def test_failure_after_file_write_restores_exact_previous_bytes(tools, monkeypatch, db):
    monkeypatch.setattr(xiaoju_tools, "check_mcp", lambda endpoint, token: {"ok": True, "tools_count": 13})
    user = db.get(SysUser, tools.user_id)
    old_token = user.access_token
    target = settings.assistant_host_home / ".workbuddy/mcp.json"
    target.parent.mkdir(parents=True)
    original = b'{ "mcpServers": {"existing": {"url":"https://example.com"}} }\n'
    target.write_bytes(original)
    def failed_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(xiaoju_tools, "log_op", failed_audit)
    result = tools.execute("install_workbuddy", {})
    db.refresh(user)
    assert not result["ok"]
    assert target.read_bytes() == original and user.access_token == old_token


def test_invalid_config_is_preserved_and_reported_as_failure(tools, db):
    target = settings.assistant_host_home / ".workbuddy/mcp.json"
    target.parent.mkdir(parents=True)
    target.write_text("{invalid-json}")
    result = tools.execute("install_workbuddy", {})
    assert not result["ok"] and result["status"] == "failed"
    assert tools.outcomes[0]["status"] == "failed"
    assert target.read_text() == "{invalid-json}"


def test_read_only_task_cannot_write_even_if_model_requests_it(tools, monkeypatch):
    tools.allow_writes = False
    monkeypatch.setattr(tools, "install_workbuddy", lambda: pytest.fail("read-only task wrote config"))
    result = tools.execute("install_workbuddy", {})
    assert not result["ok"] and "状态检查" in result["message"]


def test_disabled_host_does_not_write_to_server_home(tools, monkeypatch):
    monkeypatch.setattr(settings, "assistant_host_access", False)
    result = tools.execute("install_workbuddy", {})
    assert result["status"] == "waiting_input"
    assert not settings.assistant_host_home.exists()


@pytest.mark.parametrize("content", [b'WECOM_CORP_ID=ww-test\nWECOM_AGENT_ID=1000002\nWECOM_SECRET=private-example\nLLM_API_KEY=unrelated-key\n',
                                     b'{"wecom":{"corpId":"ww-test","agentId":1000002,"secret":"private-example"},"LLM_API_KEY":"unrelated-key"}'])
def test_import_only_reads_provider_fields(content):
    values = xiaoju_tools.import_values("wecom", content)
    assert values == {"wecom_corp_id": "ww-test", "wecom_agent_id": "1000002", "wecom_secret": "private-example"}


def test_discovered_credentials_remain_outside_model_context(tools):
    path = settings.storage_dir.parent / "xiaoju/credentials" / str(tools.user_id) / "wecom.json"
    xiaoju_tools.private_write(path, {"wecom_corp_id": "ww-test", "wecom_agent_id": "1000002", "wecom_secret": "must-never-reach-model"})
    result = tools.execute("discover_credentials", {"provider": "wecom"})
    assert result["ready"]
    assert "must-never-reach-model" not in json.dumps(result)
    assert result["sources"][-1]["missing"] == []


def test_missing_credentials_requests_only_real_missing_items(tools):
    result = tools.execute("connect_enterprise", {"provider": "wecom"})
    assert result["status"] == "waiting_input" and len(result["missing"]) == 3
    assert not any("回调" in name for name in result["missing"])
    assert result["login_url"] == "https://work.weixin.qq.com/"


def test_explicit_incomplete_source_waits_for_auth_instead_of_failing(tools):
    result = tools.execute("connect_enterprise", {"provider": "wecom", "source_id": "current"})
    assert result["status"] == "waiting_input" and len(result["missing"]) == 3
    assert tools.outcomes[0]["status"] == "waiting_input"


def test_auto_connect_generates_callback_keys_without_form(tools, monkeypatch):
    path = settings.storage_dir.parent / "xiaoju/credentials" / str(tools.user_id) / "wecom.json"
    xiaoju_tools.private_write(path, {"wecom_corp_id": "ww-test", "wecom_agent_id": "1000002", "wecom_secret": "private-example"})

    def probe(provider, config):
        assert config["wecom_secret"] == "private-example"
        assert len(config["wecom_callback_aes_key"]) == 43
        assert len(config["wecom_callback_token"]) == 32
        return {"ok": True, "message": "授权通过", "checks": [], "pending": ["企业微信后台 URL 验证"]}
    monkeypatch.setattr(xiaoju_tools.connectors, "probe", probe)
    result = tools.execute("connect_enterprise", {"provider": "wecom"})
    assert result["ok"] and result["activated"] and result["status"] == "partial"
    assert settings.source_mode("wecom") == "live"
    assert "private-example" not in json.dumps(result)


@pytest.mark.parametrize("args", [["status"], ["auth", "print-api-key"], ["--help", ";", "anything"], ["--api-key", "fake"]])
def test_cli_never_interprets_unknown_subcommands_as_prompts(tools, monkeypatch, args):
    monkeypatch.setattr(xiaoju_tools.subprocess, "run", lambda *a, **k: pytest.fail("unexpected CLI execution"))
    result = tools.execute("run_cli", {"program": "pi", "args": args})
    assert not result["ok"]


def test_employee_can_use_agent_but_cannot_execute_host_tools(db):
    uid = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "zengli"))
    tools = xiaoju_tools.XiaoJuTools(uid)
    result = tools.execute("inspect_environment", {})
    assert result["ok"] and not result["host_access"] and result["cli"] == []
    result = tools.execute("install_workbuddy", {})
    assert not result["ok"] and "权限" in result["message"]


def test_task_endpoints_enforce_user_scope(client, login, monkeypatch):
    monkeypatch.setattr(xiaoju_service, "runtime_info", lambda: {"available": True})
    monkeypatch.setattr(xiaoju_service._executor, "submit", lambda *a: None)
    staff_task = data(client.post("/api/v1/assistant/tasks", headers=login("zengli"), json={"message": "接入WorkBuddy"}))
    assert staff_task["user_id"] != data(client.get("/api/v1/auth/me", headers=login("admin")))["id"]
    task = data(client.post("/api/v1/assistant/tasks", headers=login("admin"), json={"message": "接入WorkBuddy"}))
    assert task["name"] == "小聚" and task["engine"] == "pi-agent-core"
    assert client.get(f"/api/v1/assistant/tasks/{task['id']}", headers=login("zengli")).status_code == 403
    assert data(client.get(f"/api/v1/assistant/tasks/{task['id']}", headers=login("admin")))["state"] == "queued"
    assert data(client.post(f"/api/v1/assistant/tasks/{task['id']}/cancel", headers=login("admin")))["cancel_requested"]
    assert "接入WorkBuddy" not in (xiaoju_service.task_root() / f"{task['id']}.json").read_text()


def test_secret_prompt_does_not_start_pi(client, login, monkeypatch):
    monkeypatch.setattr(xiaoju_service._executor, "submit", lambda *a: pytest.fail("secret reached Pi"))
    result = client.post("/api/v1/assistant/tasks", headers=login("admin"), json={"message": "Secret=private-example-secret"})
    assert result.status_code == 400 and "private-example-secret" not in result.text


def test_credential_upload_stays_private(client, login):
    content = b'LEMONCLOUD_APP_ID=test-app\nLEMONCLOUD_APP_SECRET=private-lemon-secret\n'
    response = client.post("/api/v1/assistant/credentials/import", headers=login("admin"), data={"provider": "lemoncloud"}, files={"file": ("connector.env", content)})
    result = data(response)
    assert "private-lemon-secret" not in response.text
    path = settings.storage_dir.parent / "xiaoju/credentials" / str(data(client.get('/api/v1/auth/me', headers=login('admin')))["id"]) / "lemoncloud.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["lemoncloud_app_secret"] == "private-lemon-secret"
    assert client.post("/api/v1/assistant/credentials/import", headers=login("zengli"), data={"provider": "lemoncloud"}, files={"file": ("connector.env", content)}).status_code == 403


def test_name_is_xiaoju_for_all_accounts(client, login):
    result = data(client.post("/api/v1/assistant/chat", headers=login("zengli"), json={"message": "你叫什么"}))
    assert "小聚" in result["message"]


def test_employee_reuses_company_connection_without_secrets(db, monkeypatch):
    uid = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "zengli"))
    tools = xiaoju_tools.XiaoJuTools(uid)
    pending = tools.execute("connect_enterprise", {"provider": "wecom"})
    assert pending["status"] == "waiting_admin" and not pending["needs_personal_credentials"]
    assert "missing" not in pending and "login_url" not in pending
    monkeypatch.setattr(settings, "data_source_overrides", "wecom:live")
    monkeypatch.setattr(settings, "wecom_corp_id", "ww-company")
    monkeypatch.setattr(settings, "wecom_agent_id", 1000002)
    monkeypatch.setattr(settings, "wecom_secret", "company-secret-never-shared")
    result = tools.execute("connect_enterprise", {"provider": "wecom"})
    assert result["ok"] and not result["needs_personal_credentials"]
    assert "company-secret-never-shared" not in json.dumps(result)


def test_personal_config_is_encrypted_and_only_owner_can_download(client, login, db, monkeypatch):
    from app.services import xiaoju_personal
    monkeypatch.setattr(xiaoju_personal, "check_mcp", lambda *args: {"ok": True, "tools_count": 3})
    uid = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "zengli"))
    result = xiaoju_tools.XiaoJuTools(uid).execute("prepare_personal_workbuddy", {})
    assert result["ok"] and result["status"] == "partial"
    path = result["download_url"]
    response = client.get('/api/v1' + path, headers=login('zengli'))
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    token = response.json()['mcpServers'][settings.mcp_server_name]['headers']['Authorization'][7:]
    assert token not in json.dumps(result)
    assert token not in xiaoju_personal._path(uid).read_text()
    assert stat.S_IMODE(xiaoju_personal._path(uid).stat().st_mode) == 0o600
    assert client.get('/api/v1' + path, headers=login('admin')).status_code == 404
    repeated = xiaoju_personal.prepare(uid)
    new_id = repeated['download_url'].split('/')[-1]
    assert xiaoju_personal.download(uid, new_id)['mcpServers'][settings.mcp_server_name]['headers']['Authorization'][7:] == token


def test_failed_personal_connection_keeps_existing_token(db, monkeypatch):
    from app.services import xiaoju_personal
    monkeypatch.setattr(xiaoju_personal, "check_mcp", lambda *args: {"ok": False, "tools_count": 0})
    user = db.scalar(select(SysUser).where(SysUser.wecom_userid == "zengli"))
    old = user.access_token
    result = xiaoju_personal.prepare(user.id)
    db.refresh(user)
    assert not result['ok'] and user.access_token == old


def test_admin_personal_config_reuses_existing_local_connection(tools, monkeypatch):
    from app.services import xiaoju_personal
    monkeypatch.setattr(xiaoju_tools, "check_mcp", lambda *args: {"ok": True, "tools_count": 13})
    monkeypatch.setattr(xiaoju_personal, "check_mcp", lambda *args: {"ok": True, "tools_count": 13})
    installed = tools.execute('install_workbuddy', {})
    token = json.loads(Path(installed['config_path']).read_text())['mcpServers'][settings.mcp_server_name]['headers']['Authorization']
    result = xiaoju_personal.prepare(tools.user_id)
    config = xiaoju_personal.download(tools.user_id, result['download_url'].split('/')[-1])
    assert config['mcpServers'][settings.mcp_server_name]['headers']['Authorization'] == token
