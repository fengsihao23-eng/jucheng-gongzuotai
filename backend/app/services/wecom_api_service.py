"""企业微信接口中心：把官方目录里的接口统一暴露为可审计的调用。"""
import json
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.response import BizError
from app.integration.wecom import catalog
from app.integration.wecom.credentials import CREDENTIALS, PROBES, Gateway, WeComApiError

MAX_RESPONSE_BYTES = 256 * 1024


def _keys(value) -> list[str]:
    return sorted(value.keys())[:40] if isinstance(value, dict) else []


def _shrink(data: dict) -> tuple[dict, int]:
    """超长响应截断为预览，避免把几十万行成员列表塞进浏览器。"""
    text = json.dumps(data, ensure_ascii=False, default=str)
    size = len(text.encode())
    if size <= MAX_RESPONSE_BYTES:
        return data, size
    preview = {}
    for key, value in data.items():
        if isinstance(value, list):
            preview[key] = value[:200]
        elif isinstance(value, dict):
            preview[key] = dict(list(value.items())[:50])
        else:
            preview[key] = value
    preview["_truncated"] = {"original_bytes": size, "note": "响应过大，仅展示前 200 条"}
    return preview, size


def invoke(db: Session, user_id: int, doc_path: str, *, params: dict | None = None,
           payload: dict | None = None, credential: str = "", confirm: bool = False) -> dict:
    item = catalog.get(doc_path)
    if item is None:
        raise BizError(40404, "接口不存在或不在官方目录中")
    if credential:
        if credential not in CREDENTIALS:
            raise BizError(40004, "未知的凭证类型")
    else:
        credential = item["credential"]
    if item["method"] != "GET" and not confirm:
        raise BizError(40012, "该接口会写入企业微信数据，请确认后再执行")
    if params and len(params) > 20:
        raise BizError(40004, "查询参数过多")
    if payload is not None and not isinstance(payload, dict):
        raise BizError(40004, "请求包体必须是 JSON 对象")

    started = time.perf_counter()
    ok, errcode, errmsg, data = True, None, "", {}
    try:
        data = Gateway.call(credential, item["method"], item["path"],
                            params=params or None, json=payload if item["method"] != "GET" else None)
    except WeComApiError as error:
        ok, errcode, errmsg = False, error.errcode, error.errmsg
        data = error.data
    except BizError:
        raise
    elapsed = int((time.perf_counter() - started) * 1000)
    preview, size = _shrink(data) if isinstance(data, dict) else ({}, 0)

    from app.models import SysWecomApiCall
    db.add(SysWecomApiCall(user_id=user_id, doc_path=item["id"], name=item["name"][:120],
                           method=item["method"], path=item["path"][:200], credential=credential,
                           ok=ok, errcode=errcode, errmsg=(errmsg or "")[:300] or None,
                           duration_ms=elapsed, response_bytes=size,
                           request_summary={"params": _keys(params), "body": _keys(payload)}))
    db.commit()
    return {"ok": ok, "errcode": errcode, "errmsg": errmsg, "duration_ms": elapsed,
            "credential": credential, "data": preview, "response_bytes": size,
            "doc_url": item["doc_url"]}


def recent(db: Session, page: int = 1, page_size: int = 20, only_failed: bool = False) -> tuple[list[dict], int]:
    from app.models import SysWecomApiCall
    query = select(SysWecomApiCall)
    if only_failed:
        query = query.where(SysWecomApiCall.ok.is_(False))
    rows = db.scalars(query.order_by(SysWecomApiCall.id.desc())).all()
    total = len(rows)
    rows = rows[(page - 1) * page_size: page * page_size]
    return [{"id": row.id, "name": row.name, "doc_path": row.doc_path, "method": row.method,
             "path": row.path, "credential": row.credential, "ok": row.ok, "errcode": row.errcode,
             "errmsg": row.errmsg, "duration_ms": row.duration_ms, "response_bytes": row.response_bytes,
             "request_summary": row.request_summary, "user_id": row.user_id,
             "created_at": row.created_at} for row in rows], total


def credentials_status() -> list[dict]:
    out = []
    for name, meta in CREDENTIALS.items():
        secret = Gateway.secret_of(name)
        out.append({"name": name, "label": meta["label"], "desc": meta["desc"],
                    "configured": bool(secret), "hint": f"尾号 {secret[-4:]}" if secret else "",
                    "probe": name in PROBES})
    return out
