"""WorkBuddy 接入配置（FR-10-02/03/06）：端点、个人令牌、客户端配置片段、连通性自检、工具注册表、调用审计与统计。"""
from datetime import timedelta

import httpx
from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.config import settings
from app.core.db import get_db
from app.core.deps import Principal, client_ip, get_principal
from app.core.permissions import DEPARTMENTS, ROLE_DEFS
from app.core.response import BizError, forbidden, not_found, ok, page_data
from app.core.timeutil import TZ, now
from app.models import LogMcpAudit, McpTool, SysUser

router = APIRouter(prefix="/mcp-config", tags=["MCP 配置"])


def endpoint() -> str:
    return settings.public_base_url.rstrip("/") + "/mcp"


def _can_call(p: Principal, t: McpTool) -> bool:
    allowed = set(t.allowed_roles or [])
    return t.enabled and ("*" in allowed or p.is_admin or bool(allowed & set(p.role_codes)) or p.dept_code in allowed)


@router.get("/server")
def server(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    tools = db.scalars(select(McpTool)).all()
    return ok({"endpoint": endpoint(), "transport": "Streamable HTTP", "auth": "Authorization: Bearer <个人 Access Token>",
               "rate_limit_per_min": settings.mcp_rate_limit_per_min, "file_link_ttl": settings.file_link_ttl_seconds,
               "tools_total": len(tools), "tools_enabled": sum(t.enabled for t in tools),
               "tools_mine": sum(_can_call(p, t) for t in tools), "is_admin": p.is_admin,
               "token_hint": p.user.access_token_hint, "has_token": bool(p.user.access_token),
               "server_name": settings.mcp_server_name})


@router.get("/tools")
def tools(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    since = now() - timedelta(days=30)
    counts = dict(db.execute(select(LogMcpAudit.tool_code, func.count()).where(LogMcpAudit.called_at >= since)
                             .group_by(LogMcpAudit.tool_code)).all())
    return ok([{"tool_code": t.tool_code, "name": t.name, "description": t.description, "allowed_roles": t.allowed_roles,
                "enabled": t.enabled, "can_call": _can_call(p, t), "calls_30d": counts.get(t.tool_code, 0)}
               for t in db.scalars(select(McpTool).order_by(McpTool.id))])


class ToolPatch(BaseModel):
    enabled: bool | None = None
    allowed_roles: list[str] | None = None


@router.patch("/tools/{code}")
def patch_tool(code: str, body: ToolPatch, request: Request, p: Principal = Depends(get_principal),
               db: Session = Depends(get_db)):
    if not p.is_admin:
        raise forbidden("仅平台管理员可调整工具注册表")
    t = db.scalar(select(McpTool).where(McpTool.tool_code == code))
    if not t:
        raise not_found("工具")
    valid = {"*", *ROLE_DEFS, *(d for d, _ in DEPARTMENTS)}
    if body.allowed_roles is not None and not set(body.allowed_roles) <= valid:
        raise BizError(40001, f"未知角色/部门：{set(body.allowed_roles) - valid}")
    before = {"enabled": t.enabled, "allowed_roles": t.allowed_roles}
    if body.enabled is not None:
        t.enabled = body.enabled
    if body.allowed_roles is not None:
        t.allowed_roles = body.allowed_roles
    log_op(db, p.id, "mcp.tool_update", "mcp_tool", code, before, {"enabled": t.enabled, "allowed_roles": t.allowed_roles},
           client_ip(request))
    db.commit()
    return ok()


@router.post("/tools/sync")
def sync_tools(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not p.is_admin:
        raise forbidden()
    from app.mcp_server.server import TOOL_META
    have = set(db.scalars(select(McpTool.tool_code)).all())
    added = []
    for code, m in TOOL_META.items():
        if code not in have:
            db.add(McpTool(tool_code=code, name=m["name"], description=m["desc"], allowed_roles=m["roles"], enabled=True))
            added.append(code)
    db.commit()
    return ok({"added": added})


@router.get("/roles")
def roles(p: Principal = Depends(get_principal)):
    return ok({"roles": [{"code": k, "name": v["name"]} for k, v in ROLE_DEFS.items()],
               "departments": [{"code": c, "name": n} for c, n in DEPARTMENTS]})


@router.get("/config")
def client_config(p: Principal = Depends(get_principal)):
    token = "<在本页生成的个人 Access Token>"
    return ok({
        "workbuddy": {"mcpServers": {settings.mcp_server_name: {
            "type": "streamableHttp", "url": endpoint(), "headers": {"Authorization": f"Bearer {token}"}}}},
        "curl": (f"curl -s -X POST '{endpoint()}' -H 'Authorization: Bearer {token}' "
                 "-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' "
                 "-d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}'"),
        "steps": ["在本页点击「生成令牌」，复制令牌（只显示一次）",
                  "打开 WorkBuddy → 设置 → MCP 服务器 → 添加，粘贴下方配置并替换令牌",
                  "保存后 WorkBuddy 会自动发现工具；对话中说『生成本周各项目利润表』即可调用",
                  "令牌泄露时请立即在本页重置，旧令牌即刻失效"],
    })


class TestIn(BaseModel):
    token: str


@router.post("/test")
async def self_test(body: TestIn, request: Request, p: Principal = Depends(get_principal)):
    """用给定令牌对本服务 /mcp 发起 initialize + tools/list，验证鉴权与工具发现。"""
    from app.main import app
    hdr = {"Authorization": f"Bearer {body.token}", "Content-Type": "application/json",
           "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://selftest") as c:
        r1 = await c.post("/mcp", headers=hdr, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "portal-selftest", "version": "1"}}})
        if r1.status_code != 200:
            return ok({"ok": False, "status": r1.status_code, "message": r1.json().get("error", {}).get("message", r1.text)})
        r2 = await c.post("/mcp", headers=hdr, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = [t["name"] for t in r2.json()["result"]["tools"]]
    info = r1.json()["result"]
    return ok({"ok": True, "server": info.get("serverInfo"), "protocol": info.get("protocolVersion"), "tools": tools})


@router.get("/audit")
def audit(tool: str = "", outcome: str = "", mine: bool = False, page: int = 1, page_size: int = 20,
          p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    q = select(LogMcpAudit)
    if not p.is_admin or mine:
        q = q.where(LogMcpAudit.user_id == p.id)
    if tool:
        q = q.where(LogMcpAudit.tool_code == tool)
    if outcome:
        q = q.where(LogMcpAudit.outcome == outcome)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(LogMcpAudit.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    names = dict(db.execute(select(SysUser.id, SysUser.name)).all())
    return ok(page_data([{"id": r.id, "user": names.get(r.user_id, "（未认证）"), "tool_code": r.tool_code,
                          "params": r.params, "outcome": r.outcome, "summary": r.result_summary, "cost_ms": r.cost_ms,
                          "ip": r.client_ip, "called_at": r.called_at} for r in rows], total, page, page_size))


@router.get("/stats")
def stats(days: int = 14, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    since = now() - timedelta(days=days)
    base = select(LogMcpAudit).where(LogMcpAudit.called_at >= since)
    if not p.is_admin:
        base = base.where(LogMcpAudit.user_id == p.id)
    sub = base.subquery()
    by_outcome = dict(db.execute(select(sub.c.outcome, func.count()).group_by(sub.c.outcome)).all())
    by_tool = [{"tool_code": t, "calls": n, "avg_ms": round(float(a or 0))} for t, n, a in db.execute(
        select(sub.c.tool_code, func.count(), func.avg(sub.c.cost_ms)).group_by(sub.c.tool_code).order_by(func.count().desc()))]
    day = func.date(func.timezone("Asia/Shanghai", sub.c.called_at))
    trend = {str(d): n for d, n in db.execute(select(day, func.count()).group_by(day))}
    days_list = [(now() - timedelta(days=i)).astimezone(TZ).date().isoformat() for i in range(days - 1, -1, -1)]
    return ok({"by_outcome": by_outcome, "by_tool": by_tool, "trend": [{"date": d, "calls": trend.get(d, 0)} for d in days_list],
               "total": sum(by_outcome.values())})


@router.post("/token/reveal-config")
def config_with_token(token: str = Body(embed=True), p: Principal = Depends(get_principal)):
    """前端在生成令牌后调用，返回已填入令牌的配置片段（令牌不落库明文）。"""
    return ok({"mcpServers": {settings.mcp_server_name: {"type": "streamableHttp", "url": endpoint(),
                                                        "headers": {"Authorization": f"Bearer {token}"}}}})
