"""站内接入助手。聊天只给出计划；写操作必须由管理员提交配置卡片。"""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, client_ip, get_principal
from app.core.response import BizError, forbidden, ok
from app.services import assistant_service as service

router = APIRouter(prefix="/assistant", tags=["接入助手"])


@router.get("/catalog")
def catalog(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok(service.catalog(db, p))


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=1500)
    context: list[Literal["workbuddy", "wecom", "lemoncloud"]] = Field(default_factory=list, max_length=3)


@router.post("/chat")
def chat(body: ChatBody, p: Principal = Depends(get_principal)):
    return ok(service.chat(body.message.strip(), body.context, p))


class ConfigureBody(BaseModel):
    values: dict[str, SecretStr] = Field(default_factory=dict, max_length=5)
    save: bool = False


@router.post("/connectors/{provider}/configure")
def configure(provider: Literal["wecom", "lemoncloud"], body: ConfigureBody, request: Request,
              p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not p.is_admin:
        raise forbidden("仅平台管理员可以配置或检测企业级接入")
    values = service.candidate(provider, {key: value.get_secret_value() for key, value in body.values.items()})
    result = service.probe(provider, values)
    saved = False
    if body.save and result["ok"]:
        from app.core.integration_config import save
        try:
            save(values, provider, "live" if provider == "wecom" else "mock")
        except (OSError, ValueError, TypeError):
            raise BizError(50001, "连接验证通过，但服务器无法保存凭证文件，请检查持久化目录权限。配置未启用。") from None
        saved = True
        result["activated"] = provider == "wecom"
        if provider == "wecom":
            result["message"] = "已保存并启用企业微信真实接口。请继续完成企业微信后台的回调 URL 验证及业务权限授权。"
        else:
            result["message"] = "已验证并保存柠檬云应用凭证。账套与同步接口尚待联调，当前保留演示模式。"
    # 只记录工具名称、结果和字段名；不记录凭证、聊天或第三方原始响应。
    log_op(db, p.id, "assistant.configure" if saved else "assistant.test", "integration", provider,
           after={"ok": result["ok"], "saved": saved, "activated": result["activated"], "fields": sorted(body.values)},
           ip=client_ip(request))
    db.commit()
    return ok({**result, "saved": saved})
