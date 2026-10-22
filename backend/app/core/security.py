import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

ALGO = "HS256"
_PBKDF2_ROUNDS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${dk.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        _, rounds, salt, digest = encoded.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds))
    return hmac.compare_digest(dk.hex(), digest)


def create_jwt(user_id: int, minutes: int | None = None) -> tuple[str, int]:
    from app.core.db import SessionLocal
    from app.models import SysUser
    with SessionLocal() as db:
        user = db.get(SysUser, user_id)
        version = password_version(user.password_hash if user else None)
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes or settings.jwt_expire_minutes)
    token = jwt.encode({"sub": str(user_id), "exp": exp, "typ": "session", "pwdv": version, "authv": 2}, settings.secret_key, algorithm=ALGO)
    return token, int(exp.timestamp())


def password_version(encoded: str | None) -> str:
    return hmac.new(settings.secret_key.encode(), (encoded or "sso-only").encode(), hashlib.sha256).hexdigest()[:32]


def decode_jwt(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[ALGO])


# ---- MCP 个人 Access Token：明文仅在生成/重置时返回一次，库内只存 sha256 ----
def new_access_token() -> str:
    return "jcmcp_" + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- 文件临时下载链接签名（替代 COS 预签名，TTL 默认 1h） ----
def sign_file(file_id: int, ttl: int | None = None) -> tuple[int, str]:
    exp = int(time.time()) + (ttl or settings.file_link_ttl_seconds)
    sig = hmac.new(settings.secret_key.encode(), f"{file_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return exp, sig


def verify_file_sig(file_id: int, exp: int, sig: str) -> bool:
    if exp < time.time():
        return False
    expect = hmac.new(settings.secret_key.encode(), f"{file_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expect, sig)
