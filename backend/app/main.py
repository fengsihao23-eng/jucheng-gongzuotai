"""聚成数智统一工作台 API + MCP Server 入口。"""
import contextlib
import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import BASE_DIR, settings
from app.core.response import BizError, trace_id_var
from app.core.security import create_jwt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# 企微等官方 GET 鉴权接口将密钥放在 query 中，禁止 httpx 的完整请求 URL 日志。
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("app")


def _mcp():
    from app.mcp_server.server import mcp_server
    return mcp_server


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    from app import jobs
    from app.jobs import registry
    jobs.load_all()
    if settings.environment != 'test':
        from app.services.intake_agent import recover_interrupted
        recover_interrupted()
    if settings.scheduler_enabled and settings.environment != "test":
        registry.start_scheduler()
    mcp = _mcp()
    async with contextlib.AsyncExitStack() as stack:
        if mcp:
            await stack.enter_async_context(mcp.session_manager.run())
        yield
    registry.stop_scheduler()


app = FastAPI(title="聚成数智统一工作台", version="2.0.0", lifespan=lifespan,
              docs_url="/docs" if settings.environment != "prod" else None, redoc_url=None)


@app.middleware("http")
async def trace_and_renew(request: Request, call_next):
    from app.core.integration_config import refresh
    refresh()
    tid = request.headers.get("X-Trace-Id") or uuid.uuid4().hex[:16]
    trace_id_var.set(tid)
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Trace-Id"] = tid
    # 滑动续期：会话剩余 < 30 分钟时下发新 token
    exp = getattr(request.state, "jwt_exp", None)
    principal = getattr(request.state, "principal", None)
    if exp and principal and exp - time.time() < 1800:
        token, new_exp = create_jwt(principal.id)
        response.headers["X-Refresh-Token"] = token
        response.headers["Access-Control-Expose-Headers"] = "X-Refresh-Token, X-Trace-Id"
    if request.url.path.startswith("/api/"):
        log.info("%s %s %s %.0fms trace=%s", request.method, request.url.path, response.status_code,
                 (time.perf_counter() - t0) * 1000, tid)
    return response


@app.exception_handler(BizError)
async def biz_error(_: Request, exc: BizError):
    return JSONResponse({"code": exc.code, "message": exc.message, "data": exc.data, "trace_id": trace_id_var.get()},
                        status_code=exc.http_status)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    errs = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    msg = "参数错误：" + "；".join(f"{e['field']} {e['message']}" for e in errs[:3])
    return JSONResponse({"code": 40001, "message": msg, "data": errs, "trace_id": trace_id_var.get()}, status_code=400)


@app.exception_handler(Exception)
async def server_error(_: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse({"code": 50001, "message": "服务器内部错误，请联系管理员", "data": None,
                         "trace_id": trace_id_var.get()}, status_code=500)


from app.api import api_router  # noqa: E402

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
def health():
    from sqlalchemy import text

    from app.core.db import engine
    with engine.connect() as c:
        c.execute(text("select 1"))
    return {"status": "ok", "env": settings.environment, "mode": settings.data_source_mode,
            "overrides": settings.data_source_overrides, "revision": settings.app_revision}


_mcp_server = _mcp()
if _mcp_server:
    from starlette.routing import Route

    from app.mcp_server.server import build_asgi
    # 用 Route 而非 Mount：/mcp 精确匹配，避免 307 补斜杠导致部分 MCP 客户端 POST 失败
    _asgi = build_asgi()
    app.router.routes.append(Route("/mcp", endpoint=_asgi, methods=["GET", "POST", "DELETE"]))
    app.router.routes.append(Route("/mcp/", endpoint=_asgi, methods=["GET", "POST", "DELETE"]))

# 单进程部署便利：存在前端构建产物时直接托管（生产走 Nginx）
_DIST = BASE_DIR.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        f = _DIST / full_path
        return FileResponse(f if full_path and f.is_file() and Path(f).resolve().is_relative_to(_DIST) else _DIST / "index.html")
