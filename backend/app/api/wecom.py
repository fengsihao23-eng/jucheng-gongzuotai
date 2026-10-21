"""企微回调（FR-07-02）：GET 验证 URL；POST 审批事件 → 先回 success，再异步分发（企微 5s 超时限制）。"""
import logging

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import PlainTextResponse

from app.core.db import SessionLocal
from app.integration.wecom import get_crypto
from app.integration.wecom.crypto import WeComCryptoError, parse_approval_event
from app.services import dispatch_service

router = APIRouter(prefix="/wecom", tags=["企业微信"])
log = logging.getLogger("wecom.callback")


@router.get("/callback", response_class=PlainTextResponse)
def verify(msg_signature: str, timestamp: str, nonce: str, echostr: str):
    try:
        return get_crypto().verify_url(msg_signature, timestamp, nonce, echostr)
    except WeComCryptoError as e:
        log.warning("callback verify failed: %s", e)
        return PlainTextResponse("invalid signature", status_code=403)


def _process(sp_no: str) -> None:
    db = SessionLocal()
    try:
        dispatch_service.ingest(db, sp_no, via="callback")
    except Exception:  # noqa: BLE001  失败由兜底对账任务重试
        log.exception("ingest %s failed", sp_no)
    finally:
        db.close()


@router.post("/callback", response_class=PlainTextResponse)
async def receive(request: Request, background: BackgroundTasks, msg_signature: str, timestamp: str, nonce: str):
    body = (await request.body()).decode()
    try:
        plain = get_crypto().decrypt_msg(msg_signature, timestamp, nonce, body)
    except (WeComCryptoError, Exception) as e:  # noqa: BLE001
        log.warning("callback decrypt failed: %s", e)
        return PlainTextResponse("invalid", status_code=403)
    evt = parse_approval_event(plain)
    if evt and evt.get("sp_no"):
        background.add_task(_process, evt["sp_no"])
    return "success"
