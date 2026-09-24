"""通用图片源解析：用户只选择 Pillow 或自定义源，URL 原样使用、不做格式判断。

自定义源规则：
- URL 路径包含 /services/aigc/（DashScope 原生图片端点）时，按原生异步协议调用；
- 其余 URL 一律按 OpenAI Images 兼容协议调用（自动补 /images/generations）；
- 系统不校验、不拦截任何 URL 形态，服务商返回什么错误就透传什么错误。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.parse import urlparse


LEGACY_EXTERNAL_SOURCES = {
    "bailian_payg",
    "bailian_token_plan",
    "openai",
    "openai_compatible",
}
KNOWN_SOURCE_IDS = {"pillow", "custom", *LEGACY_EXTERNAL_SOURCES, "coding_plan"}


@dataclass(frozen=True)
class ImageSourceRuntime:
    source: str
    label: str
    protocol: str
    submit_url: str = ""
    task_base_url: str = ""
    default_model: str = ""
    adapter: str = ""
    payload: str = ""


_SOURCE_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "pillow",
        "label": "Pillow 本地图片服务",
        "protocol": "pillow",
        "default_model": "local",
        "default_base_url": "",
        "requires_key": False,
        "supported": True,
        "description": "不调用外部服务，本地生成品牌封面或要点卡片。",
        "key_hint": "无需 API Key",
    },
    {
        "id": "custom",
        "label": "自定义源",
        "protocol": "auto",
        "default_model": "",
        "default_base_url": "",
        "requires_key": True,
        "supported": True,
        "description": "URL 原样使用：原生 DashScope 图片端点走原生协议，其余按 OpenAI Images 兼容调用。",
        "key_hint": "填写当前图片服务对应的 API Key",
    },
]
_CATALOG_BY_ID = {item["id"]: item for item in _SOURCE_CATALOG}


def image_source_catalog() -> List[Dict[str, Any]]:
    """只返回用户可见的两个服务源。"""
    return [dict(item) for item in _SOURCE_CATALOG]


def infer_image_source(provider: str, base_url: str = "", source: str = "") -> str:
    """把旧 source/provider 配置规范化为 pillow 或 custom。"""
    raw_source = (source or "").strip().lower()
    protocol = (provider or "pillow").strip().lower()
    if raw_source == "pillow" or (not raw_source and protocol == "pillow"):
        return "pillow"
    if raw_source in LEGACY_EXTERNAL_SOURCES or raw_source in {"custom", "coding_plan"}:
        return "custom"
    if not raw_source and protocol in {"dashscope", "openai", "auto"}:
        return "custom"
    raise ValueError(f"不支持的图片服务配置：source={raw_source or '-'} provider={protocol}")


def legacy_image_base_url(source: str) -> str:
    """为旧版外部 source 提供迁移默认地址。"""
    if source == "bailian_token_plan":
        return "https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    if source == "bailian_payg":
        return "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    if source == "openai":
        return "https://api.openai.com/v1"
    return ""


def _normalize_url(url: str) -> str:
    value = (url or "").strip()
    if value and "://" not in value:
        value = f"https://{value}"
    return value.rstrip("/")


def resolve_image_source(config) -> ImageSourceRuntime:
    """解析自定义源。URL 原样使用：原生图片端点走原生协议，其余走 OpenAI Images。"""
    raw_source = (getattr(config, "source", "") or "").strip().lower()
    source = infer_image_source(config.provider, config.base_url, raw_source)
    if source == "pillow":
        return ImageSourceRuntime(
            source="pillow",
            label="Pillow 本地图片服务",
            protocol="pillow",
            default_model="local",
            adapter="pillow",
        )

    key = (config.api_key or "").strip()
    model = (config.model or "").strip()
    if not model:
        raise ValueError("自定义图片源必须填写图片模型名称")
    model_lower = model.lower()
    if "t2v" in model_lower or "video" in model_lower:
        raise ValueError(
            f"模型 {model} 是视频生成模型，不能用于文章静态配图；请改用 qwen-image、wan-image 等图片模型。"
        )
    if not key:
        raise ValueError("自定义图片源必须填写 API Key")

    base_url = _normalize_url(config.base_url or legacy_image_base_url(raw_source))
    if not base_url:
        raise ValueError("自定义图片源必须填写 API Base URL")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"自定义图片源 Base URL 无法解析：{base_url}")
    root = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.lower()

    # DashScope 原生图片端点：按 URL 自身形态选择请求体，不做格式拒绝。
    if "/services/aigc/" in path:
        adapter = "bailian_token_plan" if "token-plan." in parsed.netloc.lower() else "bailian_payg"
        if "text2image" in path:
            payload = "text2image"
            submit_url = base_url
        elif "multimodal-generation" in path:
            payload = "multimodal"
            submit_url = base_url
        elif "qwen-image" in model_lower:
            payload = "multimodal"
            submit_url = f"{root}/api/v1/services/aigc/multimodal-generation/generation"
        else:
            payload = "text2image"
            submit_url = f"{root}/api/v1/services/aigc/text2image/image-synthesis"
        return ImageSourceRuntime(
            source="custom",
            label="自定义源（DashScope 原生图片协议）",
            protocol="dashscope",
            submit_url=submit_url,
            task_base_url=f"{root}/api/v1",
            default_model=model,
            adapter=adapter,
            payload=payload,
        )

    # 其余 URL 一律按 OpenAI Images 兼容协议使用。
    endpoint = (
        base_url
        if path.rstrip("/").endswith("/images/generations")
        else f"{base_url}/images/generations"
    )
    return ImageSourceRuntime(
        source="custom",
        label="自定义源（OpenAI Images 兼容）",
        protocol="openai",
        submit_url=endpoint,
        default_model=model,
        adapter="openai_compatible",
        payload="openai",
    )
