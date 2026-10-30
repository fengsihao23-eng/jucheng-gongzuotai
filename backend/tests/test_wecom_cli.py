"""个人企业微信 CLI：目录、真实来源、确认、账号隔离和凭证保护。"""
import json
import subprocess
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.response import BizError
from app.models import SysUser
from app.services import wecom_cli_service as cli, xiaoju_business as business
from app.services.xiaoju_tools import private_bytes, private_write


@pytest.fixture
def connection(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "var/files")
    binary = tmp_path / "wecom-cli"; binary.write_text("test launcher")
    monkeypatch.setattr(settings, "wecom_cli_bin", str(binary))
    uid = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "admin"))
    manifest = {"user_id": uid, "binding_id": uuid.uuid4().hex,
                "services": [{"name": "doc", "methods": [{"name": "doc.search", "description": "检索文档"}, {"name": "doc.create", "description": "创建文档"},
                                {"name": "doc.import", "description": "导入文档"}, {"name": "doc.search;evil", "description": "invalid"}]}]}
    private_write(cli.home(uid) / "binding.json", manifest)
    private_bytes(cli.home(uid) / "config/credentials.enc", b"encrypted-test-only")
    private_bytes(cli.home(uid) / "config/.encryption_key", b"test-key-only")
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[1:3] == ["schema", "get"]:
            method = args[3]
            result = {"method": method, "request": {"$ref": "Request"}, "schemas": {"Request": {"type": "object", "properties": {"keywords": {"type": "array", "items": {"type": "string"}}, "title": {"type": "string"}}}}}
        else: result = {"docs": [{"docid": "doc-1", "doc_name": "真实企业微信文档"}], "has_more": False}
        return SimpleNamespace(returncode=0, stdout=json.dumps(result, ensure_ascii=False), stderr="")
    monkeypatch.setattr(cli.subprocess, "run", run)
    return uid, manifest, calls


def test_only_binding_owner_gets_cli_operations(connection, db):
    uid, manifest, calls = connection
    other = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "zengli"))
    ops = cli.operations(uid)
    assert set(ops) == {"wecom_cli.doc.search", "wecom_cli.doc.create"}
    assert ops["wecom_cli.doc.search"].effect == "query" and ops["wecom_cli.doc.create"].effect == "confirm"
    assert not cli.operations(other) and not cli.status(other)["available"]
    with pytest.raises(BizError): cli._owned(other)
    assert not calls


def test_special_import_runner_is_read_only_and_uses_private_output_directory(connection):
    uid, manifest, calls = connection
    request_id = uuid.uuid4().hex
    result = cli.import_read(uid, "doc.search", {"keywords": ["验收样例"]}, manifest["binding_id"], request_id)
    assert result["docs"]
    args, kwargs = calls[-1]
    assert args[-2] == "--output-dir" and args[-1].endswith(f"/{uid}/work/imports/{request_id}")
    assert kwargs["env"]["WECOM_CLI_CONFIG_DIR"].endswith(f"/{uid}/config")
    with pytest.raises(BizError): cli.import_read(uid, "doc.create", {}, manifest["binding_id"], request_id)
    with pytest.raises(BizError): cli.import_read(uid, "doc.search", {"file_path": "/etc/passwd"}, manifest["binding_id"], request_id)


def test_import_adapter_handles_actual_array_enum_schema_without_allowing_unknown_items(connection, monkeypatch):
    uid, manifest, _ = connection
    manifest["services"].append({"name": "sheet", "methods": [{"name": "sheet.cells.get"}]})
    private_write(cli.home(uid) / "binding.json", manifest)
    def run(user_id, args, **kwargs):
        if args[0] == "schema": return {"method": "sheet.cells.get", "request": {"type": "object", "properties": {
            "include": {"type": "array", "enum": ["formula", "style"], "items": {"type": "string"}}}}}
        return {"cells": []}
    monkeypatch.setattr(cli, "_run", run)
    assert cli.import_read(uid, "sheet.cells.get", {"include": ["formula"]}, manifest["binding_id"], uuid.uuid4().hex) == {"cells": []}
    with pytest.raises(BizError): cli.import_read(uid, "sheet.cells.get", {"include": ["secret"]}, manifest["binding_id"], uuid.uuid4().hex)


def test_actual_cli_query_is_recorded_as_live_and_no_credentials_in_args(connection, monkeypatch):
    uid, manifest, calls = connection
    monkeypatch.setenv("WECOM_CLI_ACCESS_TOKEN", "unrelated-account-token")
    runner = business.BusinessExecutor(uid, intent="查询企业微信文档")
    described = runner.describe("wecom_cli.doc.search")
    assert described["request_schema"]["body"]["properties"]["keywords"]["type"] == "array"
    result = runner.execute("wecom_cli.doc.search", {"body": {"keywords": ["项目"]}})
    assert result["ok"] and result["data_mode"] == "live" and result["data_source"] == "wecom_cli"
    assert result["data"]["docs"][0]["docid"] == "doc-1"
    args, kwargs = calls[-1]
    assert args[1:3] == ["doc", "search"] and json.loads(args[-1])["keywords"] == ["项目"]
    assert "WECOM_CLI_ACCESS_TOKEN" not in kwargs["env"] and kwargs["env"]["WECOM_CLI_CONFIG_DIR"].endswith(f"/{uid}/config")
    assert not kwargs.get("shell") and "encrypted-test-only" not in str(args)


def test_cli_write_only_runs_after_exact_confirmation(connection):
    uid, manifest, calls = connection
    runner = business.BusinessExecutor(uid, intent="创建企业微信文档")
    pending = runner.execute("wecom_cli.doc.create", {"body": {"title": "项目周报"}})
    assert pending["requires_confirmation"] and pending["action"]["data_mode"] == "live"
    assert all(args[1] == "schema" for args, _ in calls)
    executed = business.confirm(pending["action"]["id"], uid)
    assert executed["result"]["ok"] and calls[-1][0][1:3] == ["doc", "create"]
    before = len(calls)
    assert business.confirm(pending["action"]["id"], uid)["reused"] and len(calls) == before


def test_changed_cli_connection_invalidates_pending_write(connection):
    uid, manifest, calls = connection
    pending = business.BusinessExecutor(uid, intent="创建文档").execute("wecom_cli.doc.create", {"body": {"title": "文档"}})
    manifest["binding_id"] = uuid.uuid4().hex
    private_write(cli.home(uid) / "binding.json", manifest)
    with pytest.raises(BizError, match="连接已变化"):
        business.confirm(pending["action"]["id"], uid)
    assert all(args[1] == "schema" for args, _ in calls)


def test_cli_schema_rejects_unknown_parameters_and_local_file_inputs(connection, monkeypatch):
    uid, manifest, calls = connection
    with pytest.raises(BizError, match="参数格式"):
        business.BusinessExecutor(uid).execute("wecom_cli.doc.search", {"body": {"impersonate": "other"}})
    target = cli.home(uid) / "schemas/doc.search.json"; target.unlink()
    monkeypatch.setattr(cli, "_run", lambda *args: {"method": "doc.search", "request": {"type": "object", "properties": {"file_path": {"type": "string"}}}})
    with pytest.raises(BizError, match="本机文件"):
        cli.method_schema(uid, "doc.search")


def test_cli_error_never_echoes_secrets(connection, monkeypatch):
    uid, manifest, calls = connection
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=json.dumps({"error": {"code": 893201, "message": "secret=must-stay-private"}}), stderr=""))
    with pytest.raises(BizError) as error:
        cli._run(uid, ["doc", "search", "--json", "{}"])
    assert "893201" in error.value.message and "must-stay-private" not in error.value.message


def test_cli_feature_authorization_uses_official_url_and_keeps_link_out_of_model(connection, monkeypatch):
    from app.services.xiaoju_tools import XiaoJuTools
    uid, _, calls = connection
    # Cache schema before returning the real platform's per-feature auth response.
    cli.method_schema(uid, "doc.search")
    url = "https://work.weixin.qq.com/example?authorization=private-flow"
    response = {"errcode": 851008, "errmsg": "partial no authorization", "help_message": f"当前机器人未被授权「搜索与获取文档内容」能力，可[点击这里]({url})"}
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=json.dumps(response, ensure_ascii=False), stderr=""))
    tools = XiaoJuTools(uid)
    result = tools.execute("execute_business_operation", {"operation_id": "wecom_cli.doc.search", "request": {"body": {"keywords": ["项目"]}}})
    assert result["requires_authorization"] and url not in json.dumps(result)
    assert tools.business_errors[-1]["authorization_url"] == url


def test_production_demo_password_cannot_access_personal_cli(connection, monkeypatch):
    uid, _, _ = connection
    monkeypatch.setattr(settings, "environment", "prod")
    assert cli.status(uid)["requires_password_change"] and not cli.operations(uid)
    with pytest.raises(BizError, match="更换演示密码"): cli._owned(uid)


def test_password_change_invalidates_old_token_and_refreshes_current_session(client, login, db):
    from app.core.security import create_jwt
    user = db.scalar(select(SysUser).where(SysUser.wecom_userid == "zengli"))
    previous = user.password_hash
    token, _ = create_jwt(user.id)
    headers = {"Authorization": "Bearer " + token}
    try:
        changed = client.post("/api/v1/auth/password", headers=headers, json={"old_password": settings.demo_password, "new_password": "Unique-test-password-2026!"})
        assert changed.status_code == 200 and changed.headers.get("X-Refresh-Token")
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
        fresh = {"Authorization": "Bearer " + changed.headers["X-Refresh-Token"]}
        assert client.get("/api/v1/auth/me", headers=fresh).status_code == 200
    finally:
        db.expire_all(); user = db.get(SysUser, user.id); user.password_hash = previous; db.commit()


def test_legacy_session_cannot_inherit_new_personal_connection(connection, client):
    import jwt
    import time
    uid, _, _ = connection
    legacy = jwt.encode({"sub": str(uid), "exp": int(time.time()) + 300, "typ": "session"}, settings.secret_key, algorithm="HS256")
    assert client.get('/api/v1/auth/me', headers={"Authorization": "Bearer " + legacy}).status_code == 401


def test_running_task_stops_when_account_password_changes(db):
    from app.core.security import hash_password
    from app.services.xiaoju_tools import XiaoJuTools
    user = db.scalar(select(SysUser).where(SysUser.wecom_userid == "zengli"))
    original = user.password_hash
    tools = XiaoJuTools(user.id)
    try:
        user.password_hash = hash_password("Unique-test-password-two-2026!"); db.commit()
        result = tools.execute("list_business_capabilities", {})
        assert not result['ok'] and '密码已更新' in result['message']
    finally:
        db.expire_all(); user = db.get(SysUser, user.id); user.password_hash = original; db.commit()
