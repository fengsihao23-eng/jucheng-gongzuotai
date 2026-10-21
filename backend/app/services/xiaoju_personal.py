"""员工个人 MCP 授权。令牌在服务器加密保管，不进入 Pi 上下文。"""
import hashlib
import json
import secrets
import time

from Crypto.Cipher import AES
from sqlalchemy import select

from app.core.audit import log_op
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import build_principal
from app.core.response import forbidden, not_found
from app.core.security import hash_token, new_access_token
from app.models import SysUser
from app.services.xiaoju_tools import check_mcp, private_write


def _path(user_id):
    return settings.storage_dir.parent / "xiaoju" / "personal" / f"{user_id}.json"


def _cipher(user_id, nonce=None):
    cipher = AES.new(hashlib.sha256(settings.secret_key.encode()).digest(), AES.MODE_GCM, nonce=nonce)
    cipher.update(str(user_id).encode())
    return cipher


def _read(user_id):
    path = _path(user_id)
    if not path.exists():
        return None, None
    try:
        stored = json.loads(path.read_text())
        cipher = _cipher(user_id, bytes.fromhex(stored["nonce"]))
        token = cipher.decrypt_and_verify(bytes.fromhex(stored["ciphertext"]), bytes.fromhex(stored["tag"])).decode()
        return stored, token
    except (ValueError, KeyError, TypeError):
        return None, None


def prepare(user_id):
    with SessionLocal() as db:
        user = db.scalar(select(SysUser).where(SysUser.id == user_id).with_for_update(of=SysUser))
        stored, token = _read(user_id)
        reuse = bool(token and hash_token(token) == user.access_token)
        # 管理员开发环境中已有的本机连接也可复用，避免另生成个人配置时吊销它。
        if not reuse and settings.assistant_host_access and build_principal(user).is_admin:
            path = settings.assistant_host_home / ".workbuddy/mcp.json"
            try:
                if path.is_file() and path.stat().st_size < 1048576:
                    entry = json.loads(path.read_text()).get("mcpServers", {}).get(settings.mcp_server_name, {})
                    candidate = entry.get("headers", {}).get("Authorization", "")[7:]
                    if candidate and hash_token(candidate) == user.access_token:
                        token, reuse = candidate, True
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        old_hash, old_hint = user.access_token, user.access_token_hint
        if not reuse:
            token = new_access_token()
            user.access_token, user.access_token_hint = hash_token(token), token[-6:]
            db.commit()
        try:
            endpoint = settings.public_base_url.rstrip("/") + "/mcp"
            result = check_mcp(endpoint, token)
            if not result["ok"]:
                raise RuntimeError()
            cipher = _cipher(user_id)
            ciphertext, tag = cipher.encrypt_and_digest(token.encode())
            config_id = secrets.token_hex(16)
            private_write(_path(user_id), {"id": config_id, "expires": time.time() + 3600, "nonce": cipher.nonce.hex(), "tag": tag.hex(), "ciphertext": ciphertext.hex(), "endpoint": endpoint})
            log_op(db, user_id, "xiaoju.personal_config", "integration", "workbuddy", after={"token_reused": reuse, "tools_count": result["tools_count"]})
            db.commit()
            return {"provider": "workbuddy", "ok": True, "status": "partial", "tools_count": result["tools_count"],
                    "message": "已自动准备你的个人 WorkBuddy 连接配置并通过服务自检，无需企业密钥；首次客户端导入仍需在 WorkBuddy 完成",
                    "download_url": f"/assistant/workbuddy-config/{config_id}", "needs_personal_credentials": False,
                    "pending": ["下载已填好的个人连接配置，首次在 WorkBuddy 导入；公司可统一分发 MCP 服务入口"]}
        except Exception:
            db.rollback()
            db.refresh(user)
            if not reuse and user.access_token == hash_token(token):
                user.access_token, user.access_token_hint = old_hash, old_hint
                db.commit()
            return {"provider": "workbuddy", "ok": False, "status": "failed", "message": "个人连接配置暂未准备完成，原有授权保持不变，请重试"}


def download(user_id, config_id):
    stored, token = _read(user_id)
    if not stored or stored["id"] != config_id or stored["expires"] < time.time():
        raise not_found("个人连接配置或下载已过期")
    with SessionLocal() as db:
        user = db.get(SysUser, user_id)
        if not user or hash_token(token) != user.access_token:
            raise forbidden("个人授权已变更，请重新生成连接配置")
    return {"mcpServers": {settings.mcp_server_name: {"type": "streamableHttp", "url": stored["endpoint"], "headers": {"Authorization": f"Bearer {token}"}}}}
