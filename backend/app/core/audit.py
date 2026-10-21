"""操作审计：台账人工改动、报表下载、配置变更全量留痕（PRD 9 章：保留 ≥ 1 年）。"""
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.response import encode
from app.core.timeutil import utcnow
from app.models import LogOperation

_MOBILE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_IDCARD = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[\dXx])(?!\d)")
_SECRET_KEYS = {"password", "token", "secret", "access_token", "authorization"}


def mask_text(s: str) -> str:
    return _IDCARD.sub(r"\1********\2", _MOBILE.sub(r"\1****\2", s))


def mask(value: Any) -> Any:
    """参数脱敏：手机号、身份证号、密钥类字段。"""
    if isinstance(value, dict):
        return {k: ("***" if k.lower() in _SECRET_KEYS else mask(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    if isinstance(value, str):
        return mask_text(value)
    return value


def snapshot(obj, fields: list[str]) -> dict:
    return encode({f: getattr(obj, f) for f in fields})


def log_op(db: Session, user_id: int | None, action: str, target_type: str | None = None,
           target_id: Any = None, before: dict | None = None, after: dict | None = None,
           ip: str | None = None) -> None:
    db.add(LogOperation(user_id=user_id, action=action, target_type=target_type,
                        target_id=None if target_id is None else str(target_id),
                        before=mask(encode(before)) if before else None,
                        after=mask(encode(after)) if after else None, client_ip=ip, created_at=utcnow()))
