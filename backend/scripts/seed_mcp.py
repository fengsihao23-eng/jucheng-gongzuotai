"""MCP 工具注册表（mcp_tool）：启停与授权角色可在「WorkBuddy 接入」页由管理员在线调整。"""
from app.mcp_server.server import TOOL_META
from app.models import McpTool


def seed(db, ctx):
    for code, m in TOOL_META.items():
        db.add(McpTool(tool_code=code, name=m["name"], description=m["desc"], allowed_roles=m["roles"], enabled=True))
