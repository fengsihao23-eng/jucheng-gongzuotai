"""大模型协议适配：DeepSeek 思考开关、结构化分类输出与失败脱敏。"""
import json

import httpx
import pytest

from app.core.config import settings
from app.integration import llm


@pytest.fixture(autouse=True)
def fake_model_settings(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key-not-a-real-credential")
    monkeypatch.setattr(settings, "llm_base_url", "https://model.example/v1")
    monkeypatch.setattr(settings, "llm_model", "deepseek-flash")
    monkeypatch.setattr(settings, "llm_thinking_mode", "")


def transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_deepseek_json_classification_disables_thinking(monkeypatch):
    monkeypatch.setattr(settings, "llm_thinking_mode", "disabled")

    def handler(request):
        assert str(request.url) == "https://model.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key-not-a-real-credential"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 200 and body["stream"] is False
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"providers":["wecom"],"paths":[]}'}}]})

    transport(monkeypatch, handler)
    result = llm.complete("Return JSON", "connect", max_tokens=200, timeout_seconds=12, json_mode=True)
    assert json.loads(result)["providers"] == ["wecom"]


def test_generic_model_does_not_receive_optional_parameters(monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "https://model.example/chat/completions")

    def handler(request):
        assert str(request.url) == "https://model.example/chat/completions"
        body = json.loads(request.content)
        assert "thinking" not in body and "response_format" not in body
        return httpx.Response(200, json={"choices": [{"message": {"content": "  result  "}}]})

    transport(monkeypatch, handler)
    assert llm.complete("system", "user") == "result"


def test_platform_errors_never_echo_credentials(monkeypatch, caplog):
    transport(monkeypatch, lambda request: httpx.Response(401, json={"error": {"message": "echo-test-key-not-a-real-credential"}}))
    assert llm.complete("system", "user") is None
    assert "test-key-not-a-real-credential" not in caplog.text
    assert "HTTPStatusError" in caplog.text
