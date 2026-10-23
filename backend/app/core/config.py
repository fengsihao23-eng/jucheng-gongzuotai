"""全局配置。密钥来自环境变量 / .env / 服务器受限凭证文件，不入库不入日志。"""
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

INTEGRATION_KEYS = frozenset({
    "wecom_corp_id", "wecom_agent_id", "wecom_secret", "wecom_callback_token", "wecom_callback_aes_key",
    "lemoncloud_app_id", "lemoncloud_app_secret", "data_source_overrides",
})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    environment: str = "dev"  # dev | test | prod
    app_revision: str = ""
    secret_key: str = "dev-only-secret-change-me"
    jwt_expire_minutes: int = 120  # PRD：会话 JWT 2h，滑动续期
    public_base_url: str = "http://localhost:5180"  # 晨报链接、MCP 文件下载链接拼接

    database_url: str = "postgresql+psycopg://localhost/jucheng"

    # 数据源模式：mock | live；可按数据源覆盖，如 "wecom:live,lemoncloud:mock"
    data_source_mode: str = "mock"
    data_source_overrides: str = ""

    # 口令登录（演示/内网）；live 环境建议关闭只走企微扫码
    password_login_enabled: bool = True
    demo_password: str = "Jc@2026"
    # 登录页是否展示演示账号下拉：默认仅非 prod 可见，生产演示期可显式开启
    demo_accounts_enabled: bool = False

    storage_dir: Path = BASE_DIR / "var" / "files"
    file_link_ttl_seconds: int = 3600  # PRD FR-10-04：临时链接 1h

    scheduler_enabled: bool = True
    timezone: str = "Asia/Shanghai"

    # 企业微信
    wecom_corp_id: str = ""
    wecom_agent_id: int = 0
    wecom_secret: str = ""
    wecom_callback_token: str = ""
    wecom_callback_aes_key: str = ""
    wecom_api_base: str = "https://qyapi.weixin.qq.com"

    # 柠檬云
    lemoncloud_app_id: str = ""
    lemoncloud_app_secret: str = ""
    lemoncloud_api_base: str = "https://openapi.ningmengyun.com"

    # 腾讯乐享
    lexiang_app_key: str = ""
    lexiang_app_secret: str = ""
    lexiang_api_base: str = "https://lxapi.lexiangla.com/cgi-bin"
    lexiang_web_base: str = "https://lexiangla.com"

    # 大模型（OpenAI 兼容接口，如腾讯混元/DeepSeek）；未配置时走规则引擎兜底
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_thinking_mode: Literal["", "enabled", "disabled"] = ""  # DeepSeek 可显式关闭思考；其他服务默认不传

    # 小聚：Pi 核心在独立 Node 进程运行；主机接入能力仅向平台管理员开放。
    assistant_host_access: bool = False
    assistant_host_home: Path = Path.home()
    assistant_node_bin: str = "node"
    assistant_task_timeout_seconds: int = 900
    assistant_max_turns: int = 80
    assistant_max_tool_calls: int = 256
    assistant_max_output_tokens: int = 8192
    assistant_thinking_level: str = "medium"
    assistant_models: str = ""  # 逗号分隔，空值只启用 LLM_MODEL
    assistant_sandbox_enabled: bool = False
    assistant_sandbox_image: str = "jucheng-xiaoju-workspace:1.3.0"
    assistant_workspace_root: Path | None = None
    assistant_workspace_volume: str = ""  # Docker volume-subpath，仅挂载当前账号当前会话
    assistant_sandbox_timeout_seconds: int = 90
    # Optional override for deployments that install the official CLI separately.
    wecom_cli_bin: str = ""

    mcp_server_name: str = "jucheng-portal"
    mcp_rate_limit_per_min: int = 60
    mcp_allowed_hosts: str = ""  # 额外允许的 Host（反代域名），逗号分隔

    def source_mode(self, source: str) -> str:
        for pair in filter(None, (p.strip() for p in self.data_source_overrides.split(","))):
            name, _, mode = pair.partition(":")
            if name == source and mode in ("mock", "live"):
                return mode
        return self.data_source_mode

    @property
    def is_mock(self) -> bool:
        return self.data_source_mode == "mock"


@lru_cache
def get_settings() -> Settings:
    value = Settings()
    load_integration_settings(value)
    return value


def load_integration_settings(value: Settings) -> None:
    """持久化目录与上传文件目录同卷；显式保存的接入配置优先于部署时的环境变量。"""
    path = value.storage_dir.parent / "integrations.json"
    if not path.exists():
        return
    try:
        saved = json.loads(path.read_text())
        if not isinstance(saved, dict) or not saved.keys() <= INTEGRATION_KEYS:
            raise ValueError("invalid integration settings")
        candidate = Settings.model_validate({**value.model_dump(), **saved})
        for key in saved:
            setattr(value, key, getattr(candidate, key))
    except (OSError, ValueError, TypeError):
        logging.getLogger("config").error("接入凭证文件无法读取或格式错误")
        raise RuntimeError("接入凭证文件无法读取或格式错误") from None


settings = get_settings()
