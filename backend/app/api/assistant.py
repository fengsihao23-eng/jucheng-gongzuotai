"""小聚：Pi 自主执行任务，以及兼容现有配置表单。"""
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
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
    from app.services.xiaoju_service import runtime_info
    from app.core.config import settings
    import json
    from app.core.security import hash_token
    info = service.catalog(db, p)
    path = settings.assistant_host_home / ".workbuddy/mcp.json"
    if p.is_admin and settings.assistant_host_access and path.is_file() and path.stat().st_size < 1048576:
        try:
            entry = json.loads(path.read_text()).get("mcpServers", {}).get(settings.mcp_server_name, {})
            token = entry.get("headers", {}).get("Authorization", "")[7:]
            if token and hash_token(token) == p.user.access_token and entry.get("url") == settings.public_base_url.rstrip("/") + "/mcp":
                info["connectors"][0].update(state="installed", state_label="本机已自动配置")
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return ok({**info, "runtime": runtime_info()})


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=1500)
    context: list[Literal["workbuddy", "wecom", "lemoncloud"]] = Field(default_factory=list, max_length=3)


@router.post("/chat")
def chat(body: ChatBody, p: Principal = Depends(get_principal)):
    return ok(service.chat(body.message.strip(), body.context, p))


@router.post("/tasks")
def create_task(body: ChatBody, p: Principal = Depends(get_principal)):
    from app.services.xiaoju_service import create
    return ok(create(body.message, body.context, p.id))


@router.get("/workbuddy-config/{config_id}")
def personal_config(config_id: str, p: Principal = Depends(get_principal)):
    from fastapi.responses import Response
    from app.services.xiaoju_personal import download
    import json
    return Response(json.dumps(download(p.id, config_id), ensure_ascii=False, indent=2), media_type="application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="mcp.json"', "Cache-Control": "no-store"})


@router.get("/tasks/{task_id}")
def read_task(task_id: str, p: Principal = Depends(get_principal)):
    from app.services.xiaoju_service import read
    return ok(read(task_id, p.id))


@router.get("/tasks")
def list_tasks(p: Principal = Depends(get_principal)):
    from app.services.xiaoju_service import list_for_user
    return ok(list_for_user(p.id))


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, p: Principal = Depends(get_principal)):
    from app.services.xiaoju_service import cancel
    return ok(cancel(task_id, p.id))


@router.post("/credentials/import")
async def import_credentials(provider: Literal["wecom", "lemoncloud"] = Form(...), file: UploadFile = File(...),
                             p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not p.is_admin:
        raise forbidden("仅平台管理员可导入企业接入文件")
    from app.core.config import settings
    from app.core.response import bad_request
    from app.services.xiaoju_tools import import_values, private_write
    content = await file.read(65537)
    try:
        values = import_values(provider, content)
    except (ValueError, UnicodeError):
        raise bad_request("请提供不超过 64KB 的有效 .env 或 JSON 接入文件") from None
    if not values:
        raise bad_request("文件中没有找到该系统的接入字段")
    path = settings.storage_dir.parent / "xiaoju" / "credentials" / str(p.id) / f"{provider}.json"
    private_write(path, values)
    log_op(db, p.id, "xiaoju.credentials_import", "integration", provider, after={"fields": sorted(values)})
    db.commit()
    return ok({"provider": provider, "fields": sorted(values)}, message="小聚已读取接入文件，继续后会自动验证并配置")


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
