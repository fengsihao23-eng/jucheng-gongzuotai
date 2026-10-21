"""Mock 联调端点（仅 DATA_SOURCE_MODE 下 wecom=mock 且非生产环境可用）。

POST /dev/mock/wecom-approval：模拟企微侧产生一张审批单，并按 deliver 方式送达：
  - callback：生成与企微一致的加密回调报文，走真实验签/解密/事件解析/分发链路（默认）
  - drop：只写入企微侧（模拟回调丢失），由兜底对账任务补齐
GET  /dev/mock/outbox：查看 Mock 企微应用消息发件箱（复核推送内容）
"""
import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import Principal, get_principal
from app.core.response import BizError, ok
from app.core.timeutil import now
from app.integration.wecom import get_crypto
from app.integration.wecom.crypto import parse_approval_event
from app.integration.wecom.mock import put_approval, read_outbox
from app.services import dict_service, dispatch_service

router = APIRouter(prefix="/dev/mock", tags=["Mock 联调"])


def _guard():
    if settings.environment == "prod" or settings.source_mode("wecom") != "mock":
        raise BizError(40301, "仅 Mock 模式可用")


class MockApprovalIn(BaseModel):
    template: str  # contract | expense | opportunity
    applicant: str  # 企微 userid
    fields: dict[str, str]  # 企微控件标题 → 值（与 sys_dict.approval_template 映射一致）
    sp_status: int = 2
    sp_no: str | None = None
    deliver: str = "callback"  # callback | drop


def build_callback(sp: dict) -> dict:
    """构造企微 sys_approval_change 事件的加密回调报文（与真实推送格式一致）。"""
    crypto = get_crypto()
    ts, nonce = str(int(time.time())), secrets.token_hex(5)
    xml = (f"<xml><ToUserName><![CDATA[{crypto.receive_id}]]></ToUserName><FromUserName><![CDATA[sys]]></FromUserName>"
           f"<CreateTime>{ts}</CreateTime><MsgType><![CDATA[event]]></MsgType><Event><![CDATA[sys_approval_change]]></Event>"
           f"<AgentID>3010040</AgentID><ApprovalInfo><SpNo>{sp['sp_no']}</SpNo><SpName><![CDATA[{sp['sp_name']}]]></SpName>"
           f"<SpStatus>{sp['sp_status']}</SpStatus><TemplateId><![CDATA[{sp['template_id']}]]></TemplateId>"
           f"<ApplyTime>{ts}</ApplyTime><Applyer><UserId><![CDATA[{sp['applicant_userid']}]]></UserId></Applyer>"
           f"<StatuChangeEvent>1</StatuChangeEvent></ApprovalInfo></xml>")
    envelope = crypto.encrypt_msg(xml, ts, nonce)
    sig = envelope.split("<MsgSignature><![CDATA[")[1].split("]]>")[0]
    return {"query": {"msg_signature": sig, "timestamp": ts, "nonce": nonce}, "body": envelope}


def _put(db: Session, body: MockApprovalIn) -> dict:
    tpls = dict_service.items(db, "approval_template")
    tid = next((k for k, v in tpls.items() if v.get("key") == body.template), None)
    if not tid:
        raise BizError(40001, f"未配置模板 {body.template}")
    t = now()
    sp = {"sp_no": body.sp_no or t.strftime("%Y%m%d") + f"{secrets.randbelow(10**6):06d}",
          "template_id": tid, "sp_name": tpls[tid].get("name", body.template), "sp_status": body.sp_status,
          "apply_time": (t - timedelta(hours=2)).isoformat(), "applicant_userid": body.applicant,
          "fields": body.fields, "approved_time": t.isoformat() if body.sp_status == 2 else None}
    put_approval(sp)
    return sp


@router.post("/wecom-approval")
def mock_approval(body: MockApprovalIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _guard()
    sp = _put(db, body)
    result = {"sp_no": sp["sp_no"], "delivered": body.deliver}
    if body.deliver == "callback":
        cb = build_callback(sp)
        t0 = time.perf_counter()
        plain = get_crypto().decrypt_msg(cb["query"]["msg_signature"], cb["query"]["timestamp"],
                                         cb["query"]["nonce"], cb["body"])
        evt = parse_approval_event(plain)
        rec = dispatch_service.ingest(db, evt["sp_no"], via="callback")
        result.update(callback=cb, dispatch_status=rec.dispatch_status, dispatch_target=rec.dispatch_target,
                      error=rec.error_msg, cost_ms=round((time.perf_counter() - t0) * 1000))
    return ok(result)


@router.post("/wecom-callback-payload")
def callback_payload(body: MockApprovalIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """只写入企微侧并返回加密回调报文，调用方自行 POST /api/v1/wecom/callback（HTTP 全链路验收用）。"""
    _guard()
    sp = _put(db, body)
    return ok({"sp_no": sp["sp_no"], **build_callback(sp)})


@router.get("/outbox")
def outbox(limit: int = 50, p: Principal = Depends(get_principal)):
    _guard()
    return ok(list(reversed(read_outbox(limit))))
