"""操作审计：台账人工改动、报表下载、配置变更全量留痕（PRD 9 章：保留 ≥ 1 年）。"""
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.response import encode
from app.core.timeutil import utcnow
from app.models import LogOperation

_MOBILE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_IDCARD = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[\dXx])(?!\d)")
_SECRET = re.compile(r"password|passwd|secret|token|authorization|cookie|credential|cipher|private.?key|api.?key|aes.?key|signature|binding_id", re.I)


def mask_text(s: str) -> str:
    s = _MOBILE.sub(r"\1****\2", _IDCARD.sub(r"\1********\2", s))
    return re.sub(r"((?:access_token|token|secret|signature|scode|password)=)[^&\s]+", r"\1***", s, flags=re.I)


def mask(value: Any) -> Any:
    """参数脱敏：手机号、身份证号、密钥类字段。"""
    if isinstance(value, dict):
        return {k: ("***" if _SECRET.search(str(k)) else mask(v)) for k, v in value.items()}
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
    from app.core.operation_audit import current
    ctx = current.get()
    if ctx is not None:
        ctx.notes.append(mask(encode({"action":action,"target":target_type,"id":target_id,"before":before,"after":after})))
    db.add(LogOperation(user_id=user_id, action=action, target_type=target_type,
                        target_id=None if target_id is None else str(target_id),
                        before=mask(encode(before)) if before else None,
                        after=mask(encode(after)) if after else None, client_ip=ip, created_at=utcnow()))
