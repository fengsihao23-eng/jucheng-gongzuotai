"""Server-side password verification and rotation for record deletion."""
from hashlib import sha256
from secrets import compare_digest, token_bytes

from Crypto.Cipher import AES
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import text

from app.core.audit import log_op
from app.core.config import settings
from app.core.response import bad_request, conflict
from app.core.timeutil import utcnow
from app.models import SystemDeletePassword

PASSWORD_ROW = 1
NONCE_SIZE = 12
TAG_SIZE = 16
PASSWORD_LOCK = 190670115
NO_STORE = {"Cache-Control": "no-store"}


class DeletionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr = Field(min_length=1, max_length=128)


class RotateDeletionPasswordIn(DeletionIn):
    expected_revision: int = Field(ge=0)


def _cipher(nonce):
    key = sha256(b"company-workbench/deletion-password\0" + settings.secret_key.encode()).digest()
    return AES.new(key, AES.MODE_GCM, nonce=nonce)


def _current(row):
    if row is None:
        return settings.operations_delete_password
    blob = row.password_ciphertext
    nonce, tag = blob[:NONCE_SIZE], blob[NONCE_SIZE:NONCE_SIZE + TAG_SIZE]
    try:
        return _cipher(nonce).decrypt_and_verify(blob[NONCE_SIZE + TAG_SIZE:], tag).decode()
    except (ValueError, UnicodeError):
        raise conflict("删除密码无法读取，请在系统管理重新设置") from None


def verify_deletion(db, body: DeletionIn):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock_shared(:lock)"), {"lock": PASSWORD_LOCK})
    current = _current(db.get(SystemDeletePassword, PASSWORD_ROW, populate_existing=True))
    if not compare_digest(body.password.get_secret_value().encode(), current.encode()):
        raise bad_request("删除密码不正确，请重新输入")


def password_info(db, p, *, reveal=False):
    row = db.get(SystemDeletePassword, PASSWORD_ROW)
    info = {"revision": row.revision if row else 0, "updated_at": row.updated_at if row else None}
    if reveal:
        info["password"] = _current(row)
        log_op(db, p.id, "deletion.password_reveal", "system_delete_password", PASSWORD_ROW)
        db.commit()
    return info


def rotate_password(db, p, body):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": PASSWORD_LOCK})
    row = db.get(SystemDeletePassword, PASSWORD_ROW, populate_existing=True)
    previous = row.revision if row else 0
    if previous != body.expected_revision:
        raise conflict("删除密码已更新，请重新读取后再保存")
    secret = body.password.get_secret_value()
    if not secret.strip():
        raise bad_request("请输入有效的删除密码")
    nonce = token_bytes(NONCE_SIZE)
    value, tag = _cipher(nonce).encrypt_and_digest(secret.encode())
    row = row or SystemDeletePassword(id=PASSWORD_ROW)
    row.password_ciphertext, row.revision = nonce + tag + value, previous + 1
    row.updated_by, row.updated_at = p.id, utcnow()
    db.add(row)
    log_op(db, p.id, "deletion.password_update", "system_delete_password", PASSWORD_ROW,
           before={"revision": previous}, after={"revision": row.revision})
    db.commit()
    return {"revision": row.revision, "updated_at": row.updated_at}
