"""全局配置。所有密钥只来自环境变量 / .env，不入库不入日志。"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    environment: str = "dev"  # dev | test | prod
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
    return Settings()


settings = get_settings()
