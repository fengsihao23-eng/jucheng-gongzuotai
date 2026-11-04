"""v1 路由注册。每个领域一个模块，模块内定义 `router = APIRouter(...)`。"""
import importlib

from fastapi import APIRouter

api_router = APIRouter()

MODULES = [
    # 平台（认证/管理/文件/企微回调/通知/Mock）
    "auth", "admin", "files", "wecom", "notify", "dev",
    # 企业微信个人连接与个人数据同步（按账号隔离）
    "wecom_personal",
    # 企业微信接口中心（官方全量接口目录、凭证与在线调用，仅管理员）
    "wecom_console",
    # 总办 / 台账
    "portal", "board", "contracts", "submissions",
    # 财务
    "expenses", "flows", "journal", "finance_data", "reports", "receivables",
    # 市场 / 项目
    "projects", "market",
    # 营销管控
    "opportunities", "performance", "clean",
    # 人事 / 客服 / 产品 / 知识库
    "hr", "service", "tender", "kb",
    # 统一资料导入（全员）
    "imports",
    # MCP 配置
    "mcp_config", "assistant",
]

for _m in MODULES:
    _mod = importlib.import_module(f"app.api.{_m}")
    api_router.include_router(_mod.router)
    if hasattr(_mod, "account"):
        api_router.include_router(_mod.account)
