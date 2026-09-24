from pathlib import Path

import pytest

from wechat_agent.config import Config, ImageConfig
from wechat_agent.images import ImageGenerator


def test_config_load_env(monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", "abc123")
    p = Path(__file__).parent / "fixtures" / "config_test.yaml"
    cfg = Config.load(p)
    assert cfg.llm.api_key == "abc123"
    assert cfg.llm.model == "local-model"
    assert cfg.llm.base_url.endswith("/v1")
    assert cfg.hot_sources == ["zhihu", "toutiao"]
    assert cfg.wechat.publish_mode == "draft_only"


def test_pillow_image_connection_validation():
    result = ImageGenerator(Config()).validate_connection()
    assert result == {"source": "pillow", "provider": "pillow", "model": "local", "chargeable": False}


def test_qwen_image_uses_multimodal_endpoint_and_sync_fallback(monkeypatch):
    calls = []

    class Response:
        def __init__(self, status, payload, text=""):
            self.status_code = status
            self._payload = payload
            self.text = text
            self.reason = "error"

        def json(self):
            return self._payload

    responses = [
        Response(403, {}, "current user api does not support asynchronous calls"),
        Response(
            200,
            {"output": {"choices": [{"message": {"content": [{"image": "https://example.test/image.png"}]}}]}},
        ),
    ]

    def fake_post(method, url, **kwargs):
        calls.append((url, dict(kwargs["headers"]), kwargs["json"]))
        return responses.pop(0)

    monkeypatch.setattr("requests.request", fake_post)
    cfg = Config()
    cfg.image = ImageConfig(
        provider="dashscope",
        api_key="test",
        model="qwen-image-test",
        base_url="https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    )
    result = ImageGenerator(cfg).validate_connection()
    assert calls[0][0] == "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    assert calls[0][1]["X-DashScope-Async"] == "enable"
    assert calls[1][1]["X-DashScope-Async"] == "disable"
    assert "messages" in calls[0][2]["input"]
    assert result["model"] == "qwen-image-test"


def test_compatible_mode_url_is_passed_through_as_openai_base():
    cfg = Config()
    cfg.image = ImageConfig(
        provider="openai",
        api_key="test",
        model="gpt-image-1",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    runtime = ImageGenerator(cfg).runtime
    assert runtime.protocol == "openai"
    assert runtime.submit_url == "https://dashscope.aliyuncs.com/compatible-mode/v1/images/generations"
