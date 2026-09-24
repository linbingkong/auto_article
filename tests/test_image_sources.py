from pathlib import Path

import pytest

from wechat_agent.config import Config, ImageConfig
from wechat_agent.image_sources import (
    LEGACY_EXTERNAL_SOURCES,
    image_source_catalog,
    infer_image_source,
    resolve_image_source,
)
from wechat_agent.images import ImageGenerator


def test_catalog_only_exposes_pillow_and_custom():
    catalog = image_source_catalog()
    assert [item["id"] for item in catalog] == ["pillow", "custom"]
    assert catalog[0]["protocol"] == "pillow"
    assert catalog[1]["protocol"] == "auto"


@pytest.mark.parametrize(
    ("provider", "base_url", "source", "expected"),
    [
        ("pillow", "", "", "pillow"),
        ("auto", "https://images.example.com/v1", "custom", "custom"),
        ("dashscope", "", "", "custom"),
        ("openai", "https://api.openai.com/v1", "", "custom"),
        ("dashscope", "", "bailian_payg", "custom"),
        ("dashscope", "", "bailian_token_plan", "custom"),
        ("openai", "", "openai", "custom"),
        ("openai", "https://images.example.com/v1", "openai_compatible", "custom"),
        ("dashscope", "", "coding_plan", "custom"),
    ],
)
def test_infer_normalizes_legacy_sources(provider, base_url, source, expected):
    assert infer_image_source(provider, base_url, source) == expected


def test_all_legacy_external_sources_are_normalized_to_custom():
    for source in LEGACY_EXTERNAL_SOURCES:
        assert infer_image_source("auto", "", source) == "custom"


def test_custom_openai_endpoint_is_normalized_without_duplication():
    first = resolve_image_source(ImageConfig(source="custom", provider="auto", api_key="key", model="image-model", base_url="https://images.example.com/v1"))
    exact = resolve_image_source(ImageConfig(source="custom", provider="auto", api_key="key", model="image-model", base_url="https://images.example.com/v1/images/generations"))
    bare_host = resolve_image_source(ImageConfig(source="custom", provider="auto", api_key="key", model="image-model", base_url="images.example.com/v1"))
    assert first.submit_url == "https://images.example.com/v1/images/generations"
    assert exact.submit_url == first.submit_url
    assert bare_host.submit_url == first.submit_url


def test_compatible_mode_url_is_used_as_openai_base_without_blocking():
    runtime = resolve_image_source(ImageConfig(source="custom", provider="openai", api_key="test", model="qwen-image", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"))
    assert runtime.protocol == "openai"
    assert runtime.submit_url == "https://dashscope.aliyuncs.com/compatible-mode/v1/images/generations"


def test_native_aigc_url_is_used_verbatim_with_native_payload():
    native = "https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    runtime = resolve_image_source(ImageConfig(source="custom", provider="auto", api_key="sk-sp-demo", model="qwen-image-2.0", base_url=native))
    assert runtime.protocol == "dashscope"
    assert runtime.submit_url == native
    assert runtime.payload == "multimodal"
    assert runtime.task_base_url == "https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1"


def test_native_url_host_prefix_stays_for_polling():
    native = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    runtime = resolve_image_source(ImageConfig(source="custom", provider="auto", api_key="sk-demo", model="wanx2.1-t2i-turbo", base_url=native))
    assert runtime.payload == "text2image"
    assert runtime.submit_url == native
    assert runtime.task_base_url == "https://dashscope.aliyuncs.com/api/v1"


def test_payg_wan_model_uses_native_endpoint_and_requested_size(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        text = ""
        reason = ""

        def json(self):
            return {"output": {"task_id": "task-1"}}

    monkeypatch.setattr("requests.request", lambda method, url, **kwargs: calls.append((url, kwargs)) or Response())
    cfg = Config()
    cfg.image = ImageConfig(source="custom", provider="auto", api_key="sk-demo", model="wanx2.1-t2i-turbo", base_url="https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation")
    ImageGenerator(cfg)._dashscope_submit("test", size="1664*928")
    # URL 原样使用：用户给的是 multimodal 端点，就按 multimodal 请求体提交。
    assert calls[0][0] == "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    assert calls[0][1]["json"]["parameters"]["size"] == "1664*928"
    assert "messages" in calls[0][1]["json"]["input"]


def test_video_model_is_rejected_for_static_article_images():
    with pytest.raises(ValueError, match="视频生成模型"):
        resolve_image_source(ImageConfig(source="custom", provider="auto", api_key="sk-demo", model="wan2.7-t2v", base_url="https://dashscope.aliyuncs.com/api/v1"))


def test_missing_custom_fields_fail_with_clear_messages():
    with pytest.raises(ValueError, match="图片模型名称"):
        resolve_image_source(ImageConfig(source="custom", provider="auto", api_key="key", base_url="https://images.example.com/v1"))
    with pytest.raises(ValueError, match="API Key"):
        resolve_image_source(ImageConfig(source="custom", provider="auto", model="image-model", base_url="https://images.example.com/v1"))
    with pytest.raises(ValueError, match="API Base URL"):
        resolve_image_source(ImageConfig(source="custom", provider="auto", api_key="key", model="image-model"))


def test_pillow_source_is_self_contained():
    runtime = resolve_image_source(ImageConfig(source="pillow", provider="pillow"))
    assert runtime.protocol == "pillow"
    assert not runtime.submit_url


def test_custom_openai_connection_uses_real_images_endpoint(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        text = ""
        reason = ""

        def json(self):
            return {"data": [{"b64_json": "aGVsbG8="}]}

    monkeypatch.setattr("requests.request", lambda method, url, **kwargs: calls.append((url, kwargs)) or Response())
    cfg = Config()
    cfg.image = ImageConfig(source="custom", provider="auto", api_key="custom-key", model="image-model", base_url="https://images.example.com/v1")
    result = ImageGenerator(cfg).validate_connection()
    assert calls[0][0] == "https://images.example.com/v1/images/generations"
    assert result["source"] == "custom"
    assert result["chargeable"] is True


def test_api_size_parses_width_x_height_variants():
    assert ImageGenerator._parse_size("2560x1440") == (2560, 1440)
    assert ImageGenerator._parse_size("1920*1920") == (1920, 1920)
    assert ImageGenerator._parse_size(" 2048×2048 ") == (2048, 2048)
    assert ImageGenerator._parse_size("abc") is None
    assert ImageGenerator._parse_size("2560") is None
    assert ImageGenerator._parse_size("") is None


def test_configured_api_size_is_passed_verbatim_per_protocol():
    cfg = Config()
    cfg.image = ImageConfig(source="custom", provider="auto", api_key="key", model="seed-model", base_url="https://ark.example.com/api/v3", api_size="2560x1440")
    generator = ImageGenerator(cfg)
    assert generator._api_size("cover") == "2560x1440"

    cfg.image = ImageConfig(source="custom", provider="auto", api_key="key", model="seed-model", base_url="https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis", api_size="1920*1920")
    assert ImageGenerator(cfg)._api_size("cover") == "1920*1920"
    assert ImageGenerator(cfg)._api_size("inline") == "1920*1920"


def test_openai_image_request_uses_configured_size(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        text = ""
        reason = ""

        def json(self):
            return {"data": [{"b64_json": "aGVsbG8="}]}

    monkeypatch.setattr("requests.request", lambda method, url, **kwargs: calls.append((url, kwargs)) or Response())
    cfg = Config()
    cfg.image = ImageConfig(source="custom", provider="auto", api_key="key", model="doubao-seedream-4-5", base_url="https://ark.cn-beijing.volces.com/api/v3", api_size="2560x1440")
    ImageGenerator(cfg).validate_connection()
    assert calls[0][1]["json"]["size"] == "2560x1440"
    assert calls[0][1]["json"]["model"] == "doubao-seedream-4-5"


def test_dashscope_size_error_gets_actionable_hint():
    generator = ImageGenerator(Config())
    payload = {"message": "The parameter size specified in the request is not valid: image size must be at least 3686400 pixels."}

    class Response:
        status_code = 400
        text = ""

        def json(self):
            return payload

        reason = "Bad Request"

    with pytest.raises(RuntimeError, match="生成尺寸"):
        generator._raise_api_error(Response(), "OpenAI Image")


def test_http_request_retries_on_ssl_handshake_errors(monkeypatch):
    import requests

    cfg = Config()
    cfg.image = ImageConfig(source="custom", provider="auto", api_key="key", model="m", base_url="https://ark.example.com/api/v3")
    generator = ImageGenerator(cfg)
    attempts = []

    class Response:
        status_code = 200

    def flaky(method, url, **kwargs):
        attempts.append(url)
        if len(attempts) < 3:
            raise requests.exceptions.SSLError("EOF occurred in violation of protocol")
        return Response()

    monkeypatch.setattr("requests.request", flaky)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    assert generator._http_request("POST", "https://ark.example.com/api/v3/images/generations", provider="test").status_code == 200
    assert len(attempts) == 3


def test_http_request_does_not_retry_on_mid_read_errors(monkeypatch):
    import requests

    cfg = Config()
    cfg.image = ImageConfig(source="custom", provider="auto", api_key="key", model="m", base_url="https://ark.example.com/api/v3")
    generator = ImageGenerator(cfg)
    attempts = []

    def broken(method, url, **kwargs):
        attempts.append(url)
        raise requests.exceptions.ConnectionError("connection reset mid-read")

    monkeypatch.setattr("requests.request", broken)
    with pytest.raises(RuntimeError, match="网络连接失败"):
        generator._http_request("POST", "https://ark.example.com/api/v3/images/generations", provider="test")
    assert len(attempts) == 1


def test_openai_copyright_rejection_retries_with_safe_generic_prompt(monkeypatch):
    cfg = Config()
    cfg.image = ImageConfig(source="custom", provider="openai", api_key="key", model="gpt-image-1", base_url="https://api.openai.com/v1")
    generator = ImageGenerator(cfg)
    prompts = []

    class CopyrightResponse:
        status_code = 400
        text = "The input text may be related to copyright restrictions"
        reason = "Bad Request"

        def json(self):
            return {"error": {"message": self.text}}

    class SuccessResponse:
        status_code = 200
        text = ""
        reason = "OK"

        def json(self):
            return {"data": [{"b64_json": "aGVsbG8="}]}

    def fake_request(method, url, **kwargs):
        prompts.append(kwargs["json"]["prompt"])
        return CopyrightResponse() if len(prompts) == 1 else SuccessResponse()

    monkeypatch.setattr(generator, "_http_request", fake_request)
    output_path = Path("output/_image_fallback_test/copyright-cover.png")
    output = generator._openai_image("Mickey 与某品牌联名", "模仿某著名艺术家的作品风格", output_path)
    assert output.read_bytes() == b"hello"
    assert len(prompts) == 2
    assert "Mickey" in prompts[0]
    assert "Mickey" not in prompts[1]
    assert "某著名艺术家" not in prompts[1]
    assert "generic, fictional people" in prompts[1]
    assert "logo" in prompts[1]
    output.unlink(missing_ok=True)


def test_openai_non_copyright_400_is_not_retried(monkeypatch):
    cfg = Config()
    cfg.image = ImageConfig(source="custom", provider="openai", api_key="key", model="gpt-image-1", base_url="https://api.openai.com/v1")
    generator = ImageGenerator(cfg)
    attempts = []

    class BadSizeResponse:
        status_code = 400
        text = "invalid image size"
        reason = "Bad Request"

        def json(self):
            return {"error": {"message": self.text}}

    monkeypatch.setattr(generator, "_http_request", lambda *args, **kwargs: attempts.append(kwargs) or BadSizeResponse())
    with pytest.raises(RuntimeError, match="生成尺寸|HTTP 400"):
        generator._openai_image("普通社会新闻", "普通场景", Path("output/_image_fallback_test/bad-size.png"))
    assert len(attempts) == 1
