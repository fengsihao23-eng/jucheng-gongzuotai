"""大模型适配器：OpenAI 兼容 Chat Completions（腾讯混元 / DeepSeek / 通义等均兼容）。

- 配置：LLM_BASE_URL / LLM_API_KEY / LLM_MODEL（只来自环境变量）
- DeepSeek V4.1 Flash：模型名形如 deepseek-v4.1-flash-1017；LLM_THINKING_MODE=disabled 可关闭默认思考
- 未配置或调用失败一律返回 None，调用方必须有规则引擎兜底（DEV_GUIDE 约定）
- 不记录 prompt 原文与密钥，只记录耗时与错误类型
"""
import logging
import time

import httpx

from app.core.config import settings

log = logging.getLogger("llm")
TIMEOUT_SECONDS = 60


def enabled() -> bool:
    return bool(settings.llm_api_key and settings.llm_base_url and settings.llm_model)


def _endpoint() -> str:
    base = settings.llm_base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def complete(system: str, user: str, temperature: float = 0.3, max_tokens: int | None = None,
             timeout_seconds: float = TIMEOUT_SECONDS, json_mode: bool = False) -> str | None:
    """单轮补全。返回模型文本；未配置/超时/HTTP 错误/空响应 → None。"""
    if not enabled():
        return None
    payload: dict = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if settings.llm_thinking_mode:
        payload["thinking"] = {"type": settings.llm_thinking_mode}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout_seconds) as c:
            r = c.post(_endpoint(), json=payload,
                       headers={"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"})
        r.raise_for_status()
        body = r.json()
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content")
        log.info("llm ok model=%s cost=%.0fms", settings.llm_model, (time.perf_counter() - t0) * 1000)
        return text.strip() if isinstance(text, str) and text.strip() else None
    except Exception as exc:  # noqa: BLE001  任何失败都交给规则兜底
        log.warning("llm failed: %s (%.0fms)", exc.__class__.__name__, (time.perf_counter() - t0) * 1000)
        return None
