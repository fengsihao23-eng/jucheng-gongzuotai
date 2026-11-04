"""企业微信接口网关：按能力选择凭证、缓存 access_token、统一重试与错误码。

与 `app/integration/wecom/live.py` 的自建应用适配器并存：那个负责工作台既有的
通讯录/审批/消息业务，这里负责「接口中心」的全量目录调用。两者共用 settings
里的 corp_id，但各自持有 token 缓存，避免一处失效牵连另一处。
"""
import threading
import time
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.response import BizError

# 能力 → (标签, 配置字段, 说明)
CREDENTIALS: dict[str, dict] = {
    "app": {"label": "自建应用", "field": "wecom_secret",
            "desc": "聚成工作台应用 Secret，覆盖消息、审批、汇报、会议室、日程、文档等"},
    "contacts": {"label": "通讯录同步", "field": "wecom_contacts_secret",
                 "desc": "通讯录同步助手 Secret，成员/部门/标签的写入与全量同步需要它"},
    "external": {"label": "客户联系", "field": "wecom_external_secret",
                 "desc": "客户联系 Secret，外部联系人、客户群、朋友圈接口需要它"},
    "kf": {"label": "微信客服", "field": "wecom_kf_secret", "desc": "微信客服 Secret"},
    "checkin": {"label": "打卡", "field": "wecom_checkin_secret",
                "desc": "打卡应用 Secret，考勤记录、排班、补卡接口需要它"},
    "meeting": {"label": "会议", "field": "wecom_meeting_secret",
                "desc": "会议 Secret，预约会议、会中控制、录制接口需要它"},
    "doc": {"label": "文档与微盘", "field": "wecom_doc_secret",
            "desc": "文档/微盘 Secret，文档内容、空间与文件接口需要它"},
    "webhook": {"label": "群机器人", "field": "wecom_webhook_key",
                "desc": "群机器人 Webhook 的 key（在群设置里获取），只能发消息、不需要 CorpID"},
}

# 凭证自检用的低风险只读接口
PROBES: dict[str, tuple[str, str, dict | None]] = {
    "app": ("GET", "/cgi-bin/agent/get", {"agentid": None}),  # agentid 在探测时补
    "contacts": ("GET", "/cgi-bin/department/list", None),
    "external": ("GET", "/cgi-bin/externalcontact/get_follow_user_list", None),
    "kf": ("GET", "/cgi-bin/kf/account/list", {"offset": 0, "limit": 1}),
    "checkin": ("POST", "/cgi-bin/checkin/getcorpcheckinoption", {}),
}


class WeComApiError(Exception):
    def __init__(self, errcode: int, errmsg: str, data: dict | None = None):
        super().__init__(f"企业微信接口错误 {errcode}: {errmsg}")
        self.errcode = errcode
        self.errmsg = errmsg
        self.data = data or {}


class Gateway:
    """进程内单例：token 按凭证缓存，配置变更（文件 mtime）自动失效。"""

    _lock = threading.RLock()
    _tokens: dict[str, tuple[str, float]] = {}
    _revision: tuple | None = None

    @classmethod
    def _config_revision(cls) -> tuple:
        path = Path(settings.storage_dir).parent / "integrations.json"
        try:
            stat = path.stat()
            return (settings.wecom_corp_id, stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (settings.wecom_corp_id, 0, 0)

    @classmethod
    def _check_revision(cls) -> None:
        revision = cls._config_revision()
        if revision != cls._revision:
            cls._tokens.clear()
            cls._revision = revision

    @classmethod
    def secret_of(cls, name: str) -> str:
        meta = CREDENTIALS.get(name)
        if meta is None:
            raise BizError(40004, f"未知的企业微信凭证类型 {name}")
        return getattr(settings, meta["field"], "") or ""

    @classmethod
    def configured(cls, name: str) -> bool:
        if name == "webhook":
            return bool(cls.secret_of(name))
        return bool(settings.wecom_corp_id and cls.secret_of(name))

    @classmethod
    def token(cls, name: str, refresh: bool = False) -> str:
        cls._check_revision()
        secret = cls.secret_of(name)
        if not settings.wecom_corp_id or not secret:
            label = CREDENTIALS.get(name, {}).get("label", name)
            raise BizError(40010, f"尚未配置企业微信「{label}」凭证，请先在接口中心填写")
        with cls._lock:
            cached = cls._tokens.get(name)
            if not refresh and cached and time.time() < cached[1] - 300:
                return cached[0]
        try:
            with httpx.Client(base_url=settings.wecom_api_base, timeout=15) as http:
                body = http.get("/cgi-bin/gettoken", params={"corpid": settings.wecom_corp_id,
                                                             "corpsecret": secret}).json()
        except httpx.HTTPError as error:
            raise BizError(40011, f"连接企业微信失败：{type(error).__name__}") from None
        code = body.get("errcode", 0)
        if code:
            raise WeComApiError(code, body.get("errmsg", ""), body)
        with cls._lock:
            cls._tokens[name] = (body["access_token"], time.time() + int(body.get("expires_in", 7200)))
        return body["access_token"]

    @classmethod
    def call(cls, name: str, method: str, path: str, *, params: dict | None = None,
             json: dict | None = None, timeout: float = 20) -> dict:
        """调用企业微信接口。返回原始 JSON；网络/权限错误抛 WeComApiError 或 BizError。"""
        if not path.startswith("/cgi-bin/"):
            raise BizError(40004, "只允许调用 /cgi-bin/ 下的企业微信接口")
        if "access_token" in (params or {}) or "access_token" in (json or {}):
            raise BizError(40004, "access_token 由网关注入，请勿在请求中传入")
        clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        if name == "webhook":  # 群机器人用自己的 key，不经过 corp access_token
            secret = cls.secret_of(name)
            if not secret:
                raise BizError(40010, "尚未配置企业微信「群机器人」Webhook key")
            try:
                with httpx.Client(base_url=settings.wecom_api_base, timeout=timeout) as http:
                    body = http.request(method.upper(), path, params={"key": secret, **clean}, json=json).json()
            except (httpx.HTTPError, ValueError) as error:
                raise BizError(40011, f"调用企业微信失败：{type(error).__name__}") from None
            code = int(body.get("errcode", 0) or 0)
            if code:
                raise WeComApiError(code, body.get("errmsg", ""), body)
            return body
        last: WeComApiError | None = None
        for attempt in range(3):
            token = cls.token(name, refresh=bool(last and last.errcode in (40014, 42001)))
            query = {"access_token": token, **clean}
            try:
                with httpx.Client(base_url=settings.wecom_api_base, timeout=timeout) as http:
                    response = http.request(method.upper(), path, params=query, json=json)
                    body = response.json()
            except (httpx.HTTPError, ValueError) as error:
                raise BizError(40011, f"调用企业微信失败：{type(error).__name__}") from None
            code = int(body.get("errcode", 0) or 0)
            if code == 0:
                return body
            last = WeComApiError(code, body.get("errmsg", ""), body)
            if code in (40014, 42001) and attempt < 2:  # token 失效，刷新后重试
                continue
            if code in (45009, 45033, -1) and attempt < 2:  # 频控/系统繁忙，退避
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last
        raise last or WeComApiError(-1, "重试仍失败")

    @classmethod
    def probe(cls, name: str) -> dict:
        method, path, params = PROBES[name]
        body = dict(params or {})
        if name == "app":
            body["agentid"] = settings.wecom_agent_id
        data = cls.call(name, method, path, params=body if method == "GET" else None,
                        json=body if method == "POST" else None)
        return {"ok": True, "checked_at": time.time(),
                "detail": "凭证有效" if name != "doc" else "凭证有效"}


def save_secret(name: str, secret: str, *, source: str = "wecom") -> None:
    """保存凭证到受限配置文件并立即失效 token 缓存。"""
    meta = CREDENTIALS.get(name)
    if meta is None:
        raise BizError(40004, f"未知的企业微信凭证类型 {name}")
    from app.core import integration_config
    integration_config.save({meta["field"]: (secret or "").strip()}, source, settings.source_mode(source))
    Gateway._revision = None
    Gateway._tokens.pop(name, None)
