"""小聚：业务执行、文件与回执、重要操作确认，以及公司系统接入。"""
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deletion import DeletionIn, verify_deletion
from app.core.deps import Principal, client_ip, get_principal
from app.core.response import BizError, forbidden, ok
from app.services import assistant_service as service

router = APIRouter(prefix="/assistant", tags=["小聚"])


@router.get("/catalog")
def catalog(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.services.xiaoju_service import runtime_info
    from app.core.config import settings
    import json
    from app.core.security import hash_token
    info = service.catalog(db, p)
    from app.services.wecom_cli_service import status as cli_status
    info["wecom_cli"] = cli_status(p.id)
    if info["wecom_cli"]["available"]:
        next(connector for connector in info["connectors"] if connector["key"] == "wecom")["state_label"] = "CLI 个人连接已配置"
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


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=6000)


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=16000)
    context: list[Literal["workbuddy", "wecom", "lemoncloud"]] = Field(default_factory=list, max_length=3)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=12)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)
    resume_task_id: str | None = None
    current_page: str = Field(default="", max_length=250)
    session_id: str | None = None
    model: str | None = None
    thinking: Literal["off", "minimal", "low", "medium", "high"] | None = None
    action: Literal["prompt", "compact"] = "prompt"


@router.post("/chat")
def chat(body: ChatBody, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok(service.chat(body.message.strip(), body.context, p, db=db))


@router.post("/tasks")
def create_task(body: ChatBody, p: Principal = Depends(get_principal)):
    from app.services.xiaoju_service import create
    return ok(create(body.message, body.context, p.id, history=[turn.model_dump() for turn in body.history],
                     attachment_ids=body.attachment_ids, resume_task_id=body.resume_task_id, current_page=body.current_page,
                     session_id=body.session_id, model=body.model, thinking=body.thinking, action=body.action))


@router.get("/sessions")
def list_sessions(p: Principal = Depends(get_principal)):
    from app.services import xiaoju_sessions as sessions
    return ok(sessions.list_for_user(p.id))


class SessionBody(BaseModel):
    model: str | None = None
    thinking: Literal["off", "minimal", "low", "medium", "high"] | None = None


@router.post("/sessions")
def create_session(body: SessionBody, p: Principal = Depends(get_principal)):
    from app.services import xiaoju_sessions as sessions
    return ok(sessions.view(sessions.create(p.id, model=body.model, thinking=body.thinking), True))


@router.get("/sessions/{session_id}")
def read_session(session_id: str, p: Principal = Depends(get_principal)):
    from app.services import xiaoju_sessions as sessions
    return ok(sessions.view(sessions.load(p.id, session_id), True))


@router.patch("/sessions/{session_id}")
def configure_session(session_id: str, body: SessionBody, p: Principal = Depends(get_principal)):
    from app.services import xiaoju_sessions as sessions
    return ok(sessions.configure(p.id, session_id, body.model, body.thinking))


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, body: DeletionIn, request: Request, p: Principal = Depends(get_principal),
                   db: Session = Depends(get_db)):
    from app.services import xiaoju_sessions as sessions

    verify_deletion(db, body)
    title = sessions.archive(p.id, session_id)
    log_op(db, p.id, "xiaoju.session.delete", "assistant_session", session_id,
           before={"title": title}, after={"archived": True}, ip=client_ip(request))
    db.commit()
    return ok({"deleted": True})


@router.post("/sessions/{session_id}/fork")
def fork_session(session_id: str, p: Principal = Depends(get_principal)):
    from app.services import xiaoju_sessions as sessions
    return ok(sessions.fork(p.id, session_id))


@router.get("/sessions/{session_id}/files")
def download_workspace_file(session_id: str, path: str | None = None, p: Principal = Depends(get_principal)):
    from fastapi.responses import Response
    from urllib.parse import quote
    from app.services.xiaoju_workspace import read_file, list_files
    import mimetypes
    if path is None:
        return ok(list_files(p.id, session_id))
    content, filename = read_file(p.id, session_id, path)
    return Response(content, media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename), "Cache-Control": "no-store"})


@router.get("/tasks/{task_id}/stream")
async def stream_task(task_id: str, request: Request):
    import asyncio
    import json
    from fastapi.responses import StreamingResponse
    from app.services.xiaoju_service import read, TERMINAL
    from app.services import xiaoju_sessions as sessions
    from app.core.db import SessionLocal
    # StreamingResponse 生命周期内不占用数据库连接，避免排队任务耗尽连接池。
    with SessionLocal() as db:
        user_id = get_principal(request, db).id
    initial = read(task_id, user_id)
    if initial.get("session_id"):
        sessions.load(user_id, initial["session_id"])

    async def events():
        last = None
        while not await request.is_disconnected():
            try:
                task = await asyncio.to_thread(read, task_id, user_id)
                if task.get("session_id"):
                    await asyncio.to_thread(sessions.load, user_id, task["session_id"])
                revision = (task["updated_at"], task["state"])
                if revision != last:
                    yield "event: task\ndata: " + json.dumps(task, ensure_ascii=False) + "\n\n"
                    last = revision
                else:
                    yield ": keepalive\n\n"
                if task["state"] in TERMINAL:
                    return
                await asyncio.sleep(0.5)
            except BizError:
                yield 'event: error\ndata: {"message":"当前任务已不可访问，请新建对话。"}\n\n'
                return
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class SteerBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@router.post("/tasks/{task_id}/steer")
def steer_task(task_id: str, body: SteerBody, p: Principal = Depends(get_principal)):
    from app.services.xiaoju_service import steer
    return ok(steer(task_id, p.id, body.message))


@router.get("/business/capabilities")
def business_capabilities(query: str = Query("", max_length=200), area: str = Query("", max_length=64),
                          offset: int = Query(0, ge=0), limit: int = Query(70, ge=1, le=100),
                          p: Principal = Depends(get_principal)):
    from app.services.xiaoju_business import BusinessExecutor
    return ok(BusinessExecutor(p.id).discover(query, area, offset=offset, limit=limit, browse=True))


@router.post("/business/files")
async def upload_business_file(file: UploadFile = File(...), p: Principal = Depends(get_principal)):
    from app.services import xiaoju_business_state as state
    content = await file.read(state.MAX_FILE + 1)
    return ok(state.upload(p.id, file.filename or "业务文件", content, file.content_type))


@router.get("/business/results/{result_id}")
def business_result(result_id: str, full: bool = False, p: Principal = Depends(get_principal)):
    from app.services.xiaoju_business import read_result
    return ok(read_result(result_id, p.id, full=full))


@router.get("/business/actions/{action_id}")
def business_action(action_id: str, p: Principal = Depends(get_principal)):
    from app.services.xiaoju_business import read_action
    return ok(read_action(action_id, p.id))


class ActionDecision(BaseModel):
    approve: bool


@router.post("/business/actions/{action_id}/confirm")
def confirm_business_action(action_id: str, body: ActionDecision, p: Principal = Depends(get_principal)):
    from app.services.xiaoju_business import confirm
    from app.services.xiaoju_service import record_confirmation
    result = confirm(action_id, p.id, body.approve)
    record_confirmation(result["action"], result["result"], p.id)
    return ok(result)


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
            result["message"] = "公司应用已验证并保存，可继续预览员工通讯录；网页登录及消息回调按需配置。"
        else:
            result["message"] = "已验证并保存柠檬云应用凭证。账套与同步接口尚待联调，当前保留演示模式。"
    # 只记录工具名称、结果和字段名；不记录凭证、聊天或第三方原始响应。
    log_op(db, p.id, "assistant.configure" if saved else "assistant.test", "integration", provider,
           after={"ok": result["ok"], "saved": saved, "activated": result["activated"], "fields": sorted(body.values)},
           ip=client_ip(request))
    db.commit()
    return ok({**result, "saved": saved})
