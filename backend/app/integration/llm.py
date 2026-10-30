"""大模型适配器：OpenAI 兼容 Chat Completions（腾讯混元 / DeepSeek / 通义等均兼容）。

- 配置：LLM_BASE_URL / LLM_API_KEY / LLM_MODEL（只来自环境变量）
- DeepSeek V4.1 Flash：模型名形如 deepseek-v4.1-flash-1017；LLM_THINKING_MODE=disabled 可关闭默认思考
- 扫描件/图片：vision() 走同一个网关和密钥，模型可取 LLM_VISION_MODEL，留空沿用 LLM_MODEL
- 未配置或调用失败一律返回 None，调用方必须有规则引擎兜底（DEV_GUIDE 约定）
- 不记录 prompt 原文、图片内容与密钥，只记录耗时、体积与错误类型
"""
import base64
import logging
import time

import httpx

from app.core.config import settings

log = logging.getLogger("llm")
TIMEOUT_SECONDS = 60
VISION_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 单张已压缩图片；更大会被网关拒绝，调用方应先压缩


def enabled() -> bool:
    return bool(settings.llm_api_key and settings.llm_base_url and settings.llm_model)


def vision_model() -> str:
    return (settings.llm_vision_model or settings.llm_model).strip()


def vision_enabled() -> bool:
    """视觉识别与文本模型共用 LLM_BASE_URL / LLM_API_KEY；模型未配置时视为不可用。"""
    return bool(settings.llm_api_key and settings.llm_base_url and vision_model())


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


def image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"GIF8"):
        return "image/gif"
    return "image/jpeg"


def vision(system: str, prompt: str, image: bytes, mime: str | None = None, model: str | None = None,
           max_tokens: int = 8192, timeout_seconds: float | None = None) -> str | None:
    """把一张图片交给多模态模型转录/理解。返回模型文本；未配置、超时、HTTP 错误、空响应 → None。

    与 complete() 使用同一套 LLM_BASE_URL / LLM_API_KEY，仅模型名可单独用 LLM_VISION_MODEL 指定。
    """
    if not vision_enabled() or not image or len(image) > VISION_MAX_IMAGE_BYTES:
        return None
    payload: dict = {
        "model": model or vision_model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime or image_mime(image)};base64,"
                                                           f"{base64.b64encode(image).decode()}"}},
            ]},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if settings.llm_thinking_mode:
        payload["thinking"] = {"type": settings.llm_thinking_mode}
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout_seconds or settings.llm_vision_timeout_seconds) as c:
            r = c.post(_endpoint(), json=payload,
                       headers={"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"})
        if r.status_code >= 400:
            log.warning("llm vision http %s model=%s", r.status_code, payload["model"])
            return None
        body = r.json()
        choice = (body.get("choices") or [{}])[0]
        text = choice.get("message", {}).get("content")
        log.info("llm vision ok model=%s image_kb=%d cost=%.0fms", payload["model"], len(image) // 1024,
                 (time.perf_counter() - t0) * 1000)
        if choice.get("finish_reason") == "length":
            log.warning("llm vision hit max_tokens model=%s", payload["model"])
        return text.strip() if isinstance(text, str) and text.strip() else None
    except Exception as exc:  # noqa: BLE001  任何失败都交给上层保留原件、提示重试
        log.warning("llm vision failed: %s (%.0fms)", exc.__class__.__name__, (time.perf_counter() - t0) * 1000)
        return None
