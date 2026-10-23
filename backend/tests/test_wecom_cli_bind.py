"""企业微信个人连接自助绑定：扫码会话、状态推进、凭证落位与账号隔离。"""
import stat

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.response import BizError
from app.models import SysUser
from app.services import wecom_cli_bind as bind, wecom_cli_service as cli

FAKE = """#!/bin/sh
case "$1 $2" in
  "auth init")
    head -c 4096 /dev/zero | tr '\\0' 'x' > qr.png
    mkdir -p "$WECOM_CLI_CONFIG_DIR"
    if [ "$JC_FAKE_COMPLETE" = "1" ]; then
      printf 'encrypted-test-only' > "$WECOM_CLI_CONFIG_DIR/credentials.enc"
      printf 'test-key-only' > "$WECOM_CLI_CONFIG_DIR/.encryption_key"
    fi
    sleep 4
    ;;
  "auth show")
    echo "Status: authorized"
    ;;
  "schema list")
    echo '[{"name":"doc","methods":[{"name":"doc.search","description":"检索文档"}]}]'
    ;;
esac
"""


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "var/files")
    binary = tmp_path / "wecom-cli"
    binary.write_text(FAKE)
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(settings, "wecom_cli_bin", str(binary))
    monkeypatch.setenv("JC_FAKE_COMPLETE", "0")
    return binary


def test_scan_session_turns_into_binding(cli_env, db):
    uid = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "chenjing"))
    started = bind.start(uid)
    assert started["state"] == "pending"
    qr = bind.qr_path(uid, started["session_id"])
    assert qr.is_file() and qr.stat().st_size > 512
    assert bind.status(uid, started["session_id"])["state"] == "pending"

    # 模拟员工在企业微信里扫码确认：CLI 把凭证写进本次会话目录
    config = cli.home(uid) / ".bind" / "config"
    (config / "credentials.enc").write_bytes(b"encrypted-test-only")
    (config / ".encryption_key").write_bytes(b"test-key-only")

    done = bind.status(uid, started["session_id"])
    assert done["state"] == "ready" and done["operations"] == 1
    document = cli.binding(uid)
    assert document["user_id"] == uid and document["source"] == "self-service"
    assert (cli.home(uid) / "config" / "credentials.enc").is_file()
    assert not (cli.home(uid) / ".bind").exists()

    # 会话目录清理后继续轮询仍返回就绪，不会报会话不存在
    assert bind.status(uid, started["session_id"])["state"] == "ready"

    assert bind.unbind(uid)["state"] == "unbound"
    assert cli.binding(uid) is None


def test_only_owner_can_read_session(cli_env, db):
    owner = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "zhaoli"))
    other = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "wujun"))
    started = bind.start(owner)
    with pytest.raises(BizError):
        bind.status(other, started["session_id"])
    with pytest.raises(BizError):
        bind.qr_path(other, started["session_id"])
    bind.cancel(owner, started["session_id"])
    with pytest.raises(BizError):
        bind.status(owner, started["session_id"])


def test_api_rejects_second_binding_for_bound_account(cli_env, db, client, login):
    uid = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "sunyan"))
    config = cli.home(uid) / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "credentials.enc").write_bytes(b"enc")
    (config / ".encryption_key").write_bytes(b"key")
    (cli.home(uid) / "binding.json").write_text(
        '{"user_id": %d, "binding_id": "%s", "services": []}' % (uid, "a" * 32))
    response = client.post("/api/v1/wecom-personal/bind", headers=login("sunyan"))
    assert response.json()["code"] == 40901
