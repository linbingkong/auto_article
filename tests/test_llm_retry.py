import pytest

from wechat_agent.config import LLMConfig
from wechat_agent.llm import LLMClient, LLMError


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _client(monkeypatch, responses):
    client = LLMClient(LLMConfig(base_url="https://api.test", api_key="key", model="m", max_tokens=4096))
    calls = []

    def fake_post(endpoint, headers=None, json=None, timeout=None):
        calls.append(json["max_tokens"])
        return responses[len(calls) - 1]

    monkeypatch.setattr(client._session, "post", fake_post)
    return client, calls


def test_retries_with_doubled_budget_on_length_truncation(monkeypatch):
    first = FakeResponse({"choices": [{"message": {"content": None, "reasoning_content": "推理" * 100}, "finish_reason": "length"}]})
    second = FakeResponse({"choices": [{"message": {"content": "最终答案"}, "finish_reason": "stop"}]})
    client, calls = _client(monkeypatch, [first, second])
    assert client.chat([{"role": "user", "content": "写一节"}]) == "最终答案"
    assert calls == [4096, 8192]


def test_raises_after_retry_budget_exhausted(monkeypatch):
    def truncated():
        return FakeResponse({"choices": [{"message": {"content": None, "reasoning_content": "推理"}, "finish_reason": "length"}]})

    client, calls = _client(monkeypatch, [truncated(), truncated(), truncated()])
    with pytest.raises(LLMError, match="没有返回最终答案"):
        client.chat([{"role": "user", "content": "写一节"}])
    assert calls == [4096, 8192, 16384]


def test_raises_when_partial_content_truncated_after_retries(monkeypatch):
    def partial():
        return FakeResponse({"choices": [{"message": {"content": "部分正文", "reasoning_content": ""}, "finish_reason": "length"}]})

    client, calls = _client(monkeypatch, [partial(), partial(), partial()])
    with pytest.raises(LLMError, match="截断"):
        client.chat([{"role": "user", "content": "写一节"}])
    assert calls == [4096, 8192, 16384]


def test_reasoning_only_stop_retries_with_thinking_disabled():
    client = LLMClient(LLMConfig(base_url="https://api.deepseek.com", api_key="key", model="deepseek-v4-flash", max_tokens=4096))
    responses = [
        FakeResponse({"choices": [{"message": {"content": None, "reasoning_content": "推理" * 100}, "finish_reason": "stop"}]}),
        FakeResponse({"choices": [{"message": {"content": "最终答案", "reasoning_content": ""}, "finish_reason": "stop"}]}),
    ]
    payloads = []

    def fake_post(endpoint, headers=None, json=None, timeout=None):
        payloads.append(json)
        return responses[len(payloads) - 1]

    client._session.post = fake_post
    assert client.chat([{"role": "user", "content": "写一节"}]) == "最终答案"
    assert "thinking" not in payloads[0]
    assert payloads[1]["thinking"] == {"type": "disabled"}
    assert payloads[1]["max_tokens"] == 4096


def test_network_timeout_retries_and_uses_long_read_timeout(monkeypatch):
    import requests
    import wechat_agent.llm as llm_module

    client = LLMClient(LLMConfig(base_url="https://open.bigmodel.cn/api/paas/v4", api_key="key", model="glm", max_tokens=4096, timeout=120))
    calls = []

    def fake_post(endpoint, headers=None, json=None, timeout=None):
        calls.append(timeout)
        if len(calls) < 3:
            raise requests.ReadTimeout("slow provider")
        return FakeResponse({"choices": [{"message": {"content": "最终答案"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(llm_module.time, "sleep", lambda seconds: None)
    assert client.chat([{"role": "user", "content": "写一节"}], max_tokens=8192) == "最终答案"
    assert calls == [(15.0, 300.0), (15.0, 300.0), (15.0, 300.0)]


def test_ssl_eof_error_is_retried_with_backoff(monkeypatch):
    import requests
    import wechat_agent.llm as llm_module

    client = LLMClient(LLMConfig(base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", api_key="key", model="qwen3-max", max_tokens=4096))
    calls = []
    sleeps = []

    def flaky_post(endpoint, headers=None, json=None, timeout=None):
        calls.append(endpoint)
        if len(calls) < 4:
            raise requests.exceptions.SSLError(8, "EOF occurred in violation of protocol (_ssl.c:2417)")
        return FakeResponse({"choices": [{"message": {"content": "章节内容"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(client._session, "post", flaky_post)
    monkeypatch.setattr(llm_module.time, "sleep", sleeps.append)
    assert client.chat([{"role": "user", "content": "写一节"}]) == "章节内容"
    assert len(calls) == 4
    assert sleeps == [2, 4, 8]


def test_ssl_eof_error_gives_up_after_five_attempts(monkeypatch):
    import requests
    import wechat_agent.llm as llm_module

    client = LLMClient(LLMConfig(base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", api_key="key", model="qwen3-max", max_tokens=4096))
    calls = []

    def always_eof(endpoint, headers=None, json=None, timeout=None):
        calls.append(endpoint)
        raise requests.exceptions.SSLError(8, "EOF occurred in violation of protocol")

    monkeypatch.setattr(client._session, "post", always_eof)
    monkeypatch.setattr(llm_module.time, "sleep", lambda seconds: None)
    with pytest.raises(LLMError, match="连续 5 次失败"):
        client.chat([{"role": "user", "content": "写一节"}])
    assert len(calls) == 5


def test_explicit_thinking_false_is_sent_for_deepseek_json_mode():
    client = LLMClient(LLMConfig(base_url="https://api.deepseek.com", api_key="key", model="deepseek-v4-flash"))
    captured = {}

    def fake_post(endpoint, headers=None, json=None, timeout=None):
        captured.update(json)
        return FakeResponse({"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

    client._session.post = fake_post
    assert client.chat([{"role": "user", "content": "核验"}], json_mode=True, thinking=False) == "{}"
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["response_format"] == {"type": "json_object"}


def test_usage_callback_receives_provider_usage_and_stage(monkeypatch):
    records = []
    client = LLMClient(
        LLMConfig(base_url="https://api.example.com/v1", api_key="key", model="m"),
        usage_callback=records.append,
    )
    payload = {
        "choices": [{"message": {"content": "完成"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 8},
        },
    }
    monkeypatch.setattr(client._session, "post", lambda *args, **kwargs: FakeResponse(payload))
    assert client.chat([{"role": "user", "content": "test"}], usage_label="outline") == "完成"
    assert records == [{
        "stage": "outline", "provider": "api.example.com", "model": "m",
        "prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
        "cached_tokens": 20, "reasoning_tokens": 8, "estimated": False,
    }]


def test_usage_callback_marks_estimated_when_provider_omits_usage(monkeypatch):
    records = []
    client = LLMClient(LLMConfig(base_url="https://local.test/v1", model="m"), usage_callback=records.append)
    monkeypatch.setattr(client._session, "post", lambda *args, **kwargs: FakeResponse({"choices": [{"message": {"content": "输出文本"}, "finish_reason": "stop"}]}))
    client.chat([{"role": "user", "content": "输入文本"}], usage_label="section_1")
    assert records[0]["estimated"] is True
    assert records[0]["prompt_tokens"] > 0
    assert records[0]["completion_tokens"] > 0
