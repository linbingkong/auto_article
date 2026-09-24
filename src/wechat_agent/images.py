"""配图与封面生成模块。

支持两种方式：
1. pillow（默认，零依赖风险）：用 Pillow 合成风格化封面（900x383），
   以及内文配图（文字信息图风格），无需任何付费 API。
2. 文生图 API：dashscope（通义万相）/ openai（DALL-E）等，按配置调用，
   需要 API Key。
"""

from __future__ import annotations

import json
import logging
import os
import re
import textwrap
from pathlib import Path
from typing import List, Optional

from .config import PROJECT_ROOT, Config, ImageConfig
from .image_sources import resolve_image_source

logger = logging.getLogger(__name__)


class ImageGenerator:
    """配图生成器。"""

    def __init__(self, config: Config):
        self.cfg = config
        self.img_cfg: ImageConfig = config.image
        self.runtime = resolve_image_source(self.img_cfg)

    # ------------------------------------------------------------------
    # Pillow 封面合成
    # ------------------------------------------------------------------
    def make_cover_pillow(self, title: str, out_path: Path, subtitle: str = "") -> Path:
        """用 Pillow 生成公众号首图封面（默认 900x383）。"""
        from PIL import Image, ImageDraw, ImageFont

        w, h = self.img_cfg.cover_width, self.img_cfg.cover_height
        bg = self._parse_color(self.img_cfg.cover_bg)
        fg = self._parse_color(self.img_cfg.cover_fg)

        img = Image.new("RGB", (w, h), bg)
        draw = ImageDraw.Draw(img)

        # 观思辩明品牌视觉：蓝色思辨环 + 金色灯塔之光。
        accent = self._parse_color("#63BCE8")
        gold = self._parse_color("#F3BB43")
        draw.ellipse([w - 300, h - 280, w - 20, h], outline=accent, width=7)
        draw.ellipse([w - 225, h - 205, w - 65, h - 45], outline=gold, width=4)

        font_title = self._load_font(44)
        font_sub = self._load_font(24)
        font_brand = self._load_font(20)

        logo_path = PROJECT_ROOT / "web" / "assets" / "brand-symbol.png"
        if logo_path.exists():
            logo = Image.open(logo_path).convert("RGBA").resize((44, 44), Image.Resampling.LANCZOS)
            img.paste(logo, (58, 20), logo)
            draw = ImageDraw.Draw(img)
        draw.text((112, 28), self.cfg.account_name, font=font_brand, fill=fg)
        draw.rectangle((112, 58, 214, 62), fill=gold)

        # 标题换行绘制（最多3行）
        lines = self._wrap_title(draw, title, font_title, max_width=w - 140, max_lines=3)
        y = 88
        for line in lines:
            draw.text((60, y), line, font=font_title, fill=fg)
            y += font_title.size + 12

        if subtitle:
            draw.text((60, h - 70), subtitle, font=font_sub, fill=fg)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG")
        logger.info("cover saved: %s", out_path)
        return out_path

    # ------------------------------------------------------------------
    # Pillow 内文配图（信息图风格）
    # ------------------------------------------------------------------
    def make_inline_image_pillow(
        self, text: str, out_path: Path, width: int = 900, height: int = 500
    ) -> Path:
        """生成一张要点卡片，作为正文配图。"""
        from PIL import Image, ImageDraw, ImageFont

        bg = self._parse_color("#F5F7FA")
        fg = self._parse_color("#2B3A55")
        accent = self._parse_color("#287BB4")
        muted = self._parse_color("#7C8B98")
        soft = self._parse_color("#EDF3F8")

        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        title = re.sub(r"^#{1,6}\s*", "", lines[0])[:40] if lines else "要点图解"
        points = lines[1:]
        if not points:
            points = [title]
            title = "要点图解"

        img = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, width, 12], fill=accent)

        title_font = self._load_font(34)
        body_font = self._load_font(24)
        accent_font = self._load_font(22)

        draw.rectangle([44, 60, 48, 60 + title_font.size], fill=accent)
        draw.text((70, 56), title, font=title_font, fill=fg)

        y = 140
        for index, point in enumerate(points[:3], start=1):
            draw.rounded_rectangle([60, y, 92, y + 34], radius=17, fill=accent)
            draw.text((75, y + 6), str(index), font=accent_font, fill=(255, 255, 255))
            wrapped = textwrap.wrap(point, width=width // 34 or 22)
            for line in wrapped[:2]:
                draw.text((112, y + 4), line, font=body_font, fill=fg)
                y += body_font.size + 10
            y += 26

        draw.text(
            (60, height - 46),
            "观思辩明 · 要点图解",
            font=accent_font,
            fill=muted,
        )
        draw.rectangle([0, height - 10, width, height], fill=soft)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG")
        logger.info("inline card saved: %s", out_path)
        return out_path

    # ------------------------------------------------------------------
    # 尺寸解析
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_size(value: str) -> tuple[int, int] | None:
        """解析 '宽x高' / '宽*高' 尺寸字符串；非法返回 None。"""
        raw = (value or "").strip().lower()
        match = re.fullmatch(r"(\d{2,5})\s*[x*×]\s*(\d{2,5})", raw)
        if not match:
            return None
        width, height = int(match.group(1)), int(match.group(2))
        if width <= 0 or height <= 0:
            return None
        return width, height

    def _api_size(self, kind: str) -> str:
        """API 生成尺寸：显式配置优先；未配置时按协议/用途给默认值。

        默认值只是兜底——部分服务商（如火山 Seedream）有最低总像素要求，
        建议在配置中心显式填写服务商支持的尺寸，系统会原样传递。
        """
        configured = self._parse_size(self.img_cfg.api_size)
        if configured:
            separator = "*" if self.runtime.protocol == "dashscope" else "x"
            return f"{configured[0]}{separator}{configured[1]}"
        if self.runtime.protocol == "dashscope":
            return "1280*720" if kind == "inline" else "1664*928"
        return "1536x1024"

    # ------------------------------------------------------------------
    # 网络请求（瞬时 TLS/连接错误自动重试）
    # ------------------------------------------------------------------
    def _http_request(self, method: str, url: str, *, provider: str, **kwargs):
        """发起 HTTP 请求，对握手阶段失败（SSLError/ConnectTimeout）自动重试。

        只重试"请求尚未到达服务器"的连接级错误，避免计费型请求被重复提交；
        读取中途断开等不安全场景不重试。
        """
        import time

        import requests

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return requests.request(method, url, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
                retryable = isinstance(exc, (requests.exceptions.SSLError, requests.exceptions.ConnectTimeout))
                if retryable and attempt < 2:
                    delay = (2, 5)[attempt]
                    logger.warning("%s 请求出现瞬时连接错误（%s），%d 秒后重试", provider, exc, delay)
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"{provider} 网络连接失败：{exc}") from exc
        raise RuntimeError(f"{provider} 网络连接失败：{last_exc}")

    # ------------------------------------------------------------------
    # 文生图 API（可选）
    # ------------------------------------------------------------------
    def make_cover_api(self, title: str, out_path: Path, prompt_hint: str = "") -> Path:
        """调用文生图 API 生成封面。provider: dashscope | openai"""
        if self.runtime.protocol == "dashscope":
            return self._dashscope_image(title, prompt_hint, out_path, kind="cover")
        if self.runtime.protocol == "openai":
            return self._openai_image(title, prompt_hint, out_path, kind="cover")
        raise ValueError(f"unsupported image protocol: {self.runtime.protocol}")

    def _dashscope_image(
        self, title: str, hint: str, out_path: Path, kind: str = "cover"
    ) -> Path:
        import base64
        import time

        import requests

        if kind == "inline":
            prompt = (
                f"微信公众号文章的新闻场景插画，主题：{title}。"
                f"{hint} 用具体人物、物体、设备或空间关系表达文章内容，避免空泛抽象背景。"
                "画面中绝对不能出现任何文字、汉字、字母、数字、Logo、水印或界面截图；横向构图。"
            )
            size = self._api_size("inline")
        else:
            prompt = (
                f"微信公众号文章的主题封面插画，文章主题：{title}。"
                f"{hint} 用与文章直接相关的具体场景、人物、物体或科技设备构成视觉主体，"
                "避免通用渐变背景和无意义装饰。画面中绝对不能出现任何文字、汉字、字母、"
                "数字、Logo、水印或界面截图；画面干净，横向构图。"
            )
            size = self._api_size("cover")
        data = self._dashscope_submit(prompt, size=size)
        output = data.get("output", {})
        task_id = output.get("task_id")
        if task_id:
            # 任务轮询跟随提交端点的主机：Token Plan 专属网关的密钥
            # 在 dashscope.aliyuncs.com 上无效，反之亦然。
            task_endpoint = self._task_query_endpoint(output.get("task_id"))
            for _ in range(60):
                result = self._http_request(
                    "GET",
                    task_endpoint,
                    provider="通义万相任务查询",
                    headers={"Authorization": f"Bearer {self.img_cfg.api_key}"},
                    timeout=20,
                )
                self._raise_api_error(result, "通义万相任务查询")
                data = result.json()
                status = str(data.get("output", {}).get("task_status", "")).upper()
                if status == "SUCCEEDED":
                    break
                if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                    message = data.get("message") or data.get("output", {}).get("message") or status
                    raise RuntimeError(f"通义万相生成失败：{message}")
                time.sleep(2)
            else:
                raise RuntimeError("通义万相生成超时（等待异步任务超过 120 秒）")

        image_value = self._dashscope_image_value(data)
        if not image_value:
            raise RuntimeError("通义万相响应中没有找到生成图片 URL")
        if image_value.startswith("http"):
            image_response = self._http_request("GET", image_value, provider="通义万相图片下载", timeout=60)
            self._raise_api_error(image_response, "通义万相图片下载")
            img_bytes = image_response.content
        else:
            img_bytes = base64.b64decode(image_value)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(img_bytes)
        logger.info("dashscope cover saved: %s", out_path)
        return out_path

    def _dashscope_submit(self, prompt: str, size: str = "1664*928") -> dict:
        import requests

        model = self.img_cfg.model or self.runtime.default_model
        endpoint = self.runtime.submit_url
        # 请求体形态由 URL 自身决定（multimodal-generation 或 text2image），
        # 不再根据服务商或模型名做格式判断。
        multimodal_payload = self.runtime.payload != "text2image"
        if multimodal_payload:
            payload = {
                "model": model,
                "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
                "parameters": {
                    "size": size,
                    "n": 1,
                    "prompt_extend": True,
                    "watermark": False,
                },
            }
        else:
            payload = {
                "model": model,
                "input": {"prompt": prompt},
                "parameters": {"size": size, "n": 1},
            }
        headers = {
            "Authorization": f"Bearer {self.img_cfg.api_key}",
            "X-DashScope-Async": "enable",
            "Content-Type": "application/json",
        }
        try:
            response = self._http_request(
                "POST", endpoint, provider="通义万相",
                headers=headers, json=payload, timeout=30,
            )
            if response.status_code >= 400 and "does not support asynchronous" in response.text.lower():
                headers["X-DashScope-Async"] = "disable"
                response = self._http_request(
                    "POST", endpoint, provider="通义万相",
                    headers=headers, json=payload, timeout=120,
                )
        except requests.RequestException as exc:
            raise RuntimeError(f"通义万相网络连接失败：{exc}") from exc
        self._raise_api_error(response, "通义万相")
        return response.json()

    def _task_query_endpoint(self, task_id: str) -> str:
        """任务查询地址由服务源适配器提供，确保提交和轮询使用同一网关。"""
        if not self.runtime.task_base_url:
            raise RuntimeError(f"{self.runtime.label}未配置异步任务查询地址")
        return f"{self.runtime.task_base_url}/tasks/{task_id}"

    @staticmethod
    def _dashscope_image_value(data: dict) -> str:
        output = data.get("output", {})
        results = output.get("results") or []
        if results and isinstance(results[0], dict):
            return str(results[0].get("url") or results[0].get("image") or "")
        choices = output.get("choices") or []
        if choices:
            content = choices[0].get("message", {}).get("content") or []
            for item in content:
                if isinstance(item, dict) and (item.get("image") or item.get("url")):
                    return str(item.get("image") or item.get("url"))
        return ""

    @staticmethod
    def _is_copyright_rejection(response) -> bool:
        """识别图片服务商针对提示词版权/IP 风险返回的 400。"""
        if getattr(response, "status_code", 0) != 400:
            return False
        text = str(getattr(response, "text", "") or "")
        try:
            payload = response.json()
            text += " " + json.dumps(payload, ensure_ascii=False)
        except Exception:
            pass
        lowered = text.casefold()
        markers = (
            "copyright", "copyrighted", "intellectual property",
            "third-party content", "character likeness", "版权", "著作权", "知识产权",
        )
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _safe_editorial_subject(text: str) -> str:
        """只从白名单主题中选择宽泛场景，避免在降级提示词中复述风险实体。"""
        value = (text or "").casefold()
        categories = (
            (("科技", "人工智能", "模型", "芯片", "软件", "互联网", "ai"), "technology and its impact on everyday life"),
            (("职场", "工作", "公司", "员工", "招聘", "离职"), "workplace communication and professional relationships"),
            (("经济", "金融", "消费", "价格", "养老金", "市场"), "household economic choices and consumer life"),
            (("教育", "学校", "学生", "考试", "学习"), "education and learning in a modern community"),
            (("医疗", "健康", "医院", "医生", "疾病"), "public health and access to care"),
            (("政策", "制度", "监管", "法律", "法院"), "public policy and civic services"),
            (("环境", "气候", "能源", "污染"), "environmental change and sustainable living"),
            (("交通", "汽车", "铁路", "航空", "物流"), "transportation and connected city life"),
        )
        for keywords, subject in categories:
            if any(keyword in value for keyword in keywords):
                return subject
        return "a contemporary social issue and the people affected by it"

    @classmethod
    def _copyright_safe_prompt(cls, title: str, hint: str, kind: str) -> str:
        subject = cls._safe_editorial_subject(f"{title} {hint}")
        purpose = "wide editorial cover" if kind == "cover" else "wide editorial scene illustration"
        return (
            f"Create an original {purpose} about {subject}. "
            "Use generic, fictional people and unbranded everyday objects in a clean documentary-inspired composition. "
            "Do not depict any recognizable real person, named character, franchise imagery, branded product, logo, "
            "existing artwork, artist imitation, text, letters, numbers, watermark, or user-interface screenshot. "
            "No direct visual reference to a specific protected work."
        )

    def _openai_image(
        self, title: str, hint: str, out_path: Path, kind: str = "cover"
    ) -> Path:
        import requests

        import base64

        if kind == "inline":
            prompt = (
                f"微信公众号文章的新闻场景插画，主题：{title}。"
                f"{hint} 用具体人物、物体、设备或空间关系表达文章内容，避免空泛抽象背景。"
                "画面中绝对不能出现任何文字、汉字、字母、数字、Logo、水印或界面截图；横向构图。"
            )
            size = self._api_size("inline")
        else:
            prompt = (
                f"微信公众号文章的主题封面插画，文章主题：{title}。{hint} "
                "用与文章直接相关的具体场景、人物、物体或设备表达主题；"
                "画面中绝对不能出现文字、字母、数字、Logo、水印或界面截图；横向构图。"
            )
            size = self._api_size("cover")
        endpoint = self.runtime.submit_url

        def submit(value: str):
            return self._http_request(
                "POST", endpoint, provider="OpenAI Image",
                headers={"Authorization": f"Bearer {self.img_cfg.api_key}"},
                json={
                    "model": self.img_cfg.model or self.runtime.default_model,
                    "prompt": value,
                    "size": size,
                    "n": 1,
                },
                timeout=120,
            )

        try:
            r = submit(prompt)
            if self._is_copyright_rejection(r):
                logger.warning("OpenAI Image 提示词触发版权限制，改用不含实体名称的原创编辑插画提示词重试一次")
                r = submit(self._copyright_safe_prompt(title, hint, kind))
        except requests.RequestException as exc:
            raise RuntimeError(f"OpenAI Image 网络连接失败：{exc}") from exc
        self._raise_api_error(r, "OpenAI Image")
        data = r.json()["data"][0]
        if data.get("url"):
            image_response = self._http_request("GET", data["url"], provider="OpenAI 图片下载", timeout=60)
            self._raise_api_error(image_response, "OpenAI 图片下载")
            img_bytes = image_response.content
        elif data.get("b64_json"):
            img_bytes = base64.b64decode(data["b64_json"])
        else:
            raise RuntimeError("OpenAI Image 响应中缺少 url 或 b64_json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(img_bytes)
        logger.info("openai cover saved: %s", out_path)
        return out_path

    def validate_connection(self) -> dict:
        """验证图片 Provider、密钥与模型是否可用。

        DashScope 没有稳定的免费模型查询接口，因此会提交一个最小异步测试任务；
        返回值会明确标注该测试可能产生一次调用费用。
        """
        provider = self.runtime.protocol
        if provider == "pillow":
            from PIL import Image, ImageColor

            Image.new("RGB", (8, 8), ImageColor.getrgb(self.img_cfg.cover_bg))
            return {"source": self.runtime.source, "provider": "pillow", "model": "local", "chargeable": False}
        if not self.img_cfg.api_key:
            raise ValueError("请先配置图片 API Key")

        import requests

        if provider == "dashscope":
            model = self.img_cfg.model or self.runtime.default_model
            data = self._dashscope_submit("连接测试：简洁的蓝色圆形图标")
            output = data.get("output", {})
            task_id = output.get("task_id")
            if not task_id and not self._dashscope_image_value(data):
                raise RuntimeError("通义万相同步响应中没有生成图片，无法确认连接成功")
            return {
                "source": self.runtime.source,
                "provider": provider,
                "model": model,
                "task_id": task_id or "synchronous",
                "chargeable": True,
            }

        if provider == "openai":
            model = self.img_cfg.model or self.runtime.default_model
            if self.runtime.adapter == "openai_compatible":
                from uuid import uuid4

                check_path = PROJECT_ROOT / "output" / f".image-connection-{uuid4().hex}.png"
                try:
                    self._openai_image("连接测试", "简洁的蓝色圆形图标", check_path)
                finally:
                    check_path.unlink(missing_ok=True)
                return {
                    "source": self.runtime.source,
                    "provider": provider,
                    "model": model,
                    "chargeable": True,
                }
            base = self.runtime.submit_url
            if base.endswith("/images/generations"):
                base = base[: -len("/images/generations")]
            try:
                response = self._http_request(
                    "GET", f"{base}/models/{model}", provider="OpenAI Image 模型校验",
                    headers={"Authorization": f"Bearer {self.img_cfg.api_key}"},
                    timeout=20,
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"OpenAI Image 网络连接失败：{exc}") from exc
            self._raise_api_error(response, "OpenAI Image 模型校验")
            return {"source": self.runtime.source, "provider": provider, "model": model, "chargeable": False}
        raise ValueError(f"unsupported image provider: {provider}")

    # ------------------------------------------------------------------
    # 统一入口
    # ------------------------------------------------------------------
    def generate_cover(self, title: str, out_path: Path, subtitle: str = "") -> Path:
        """生成封面：根据配置的 provider 选择实现。"""
        if self.runtime.protocol == "pillow":
            return self.make_cover_pillow(title, out_path, subtitle)
        return self.make_cover_api(title, out_path, subtitle)

    def make_inline_image_card(
        self, text: str, out_path: Path, width: int = 900, height: int = 500
    ) -> Path:
        """要点卡片别名，兼容不同调用方。"""
        return self.make_inline_image_pillow(text, out_path, width, height)

    def generate_inline(self, texts: List[str], out_dir: Path) -> List[Path]:
        """生成内文配图列表；外部文生图失败时跳过，不伪装成文字卡片。"""
        paths: List[Path] = []
        for i, text in enumerate(texts[: self.img_cfg.inline_images]):
            p = out_dir / f"inline_{i + 1}.png"
            if self.runtime.protocol == "pillow":
                self.make_inline_image_card(text, p)
                paths.append(p)
                continue
            if self.runtime.protocol not in {"dashscope", "openai"}:
                raise ValueError(f"unsupported inline image protocol: {self.runtime.protocol}")
            if not self.img_cfg.api_key:
                raise ValueError("外部文生图已启用，但图片 API Key 未配置")
            try:
                self._generate_inline_api(text, p)
                paths.append(p)
            except Exception as exc:  # noqa: BLE001
                logger.warning("inline %d API generation failed and was skipped: %s", i + 1, exc)
        return paths

    def _generate_inline_api(self, text: str, out_path: Path) -> Path:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        title = lines[0][:60] if lines else "主题"
        hint = "；".join(lines[1:])[:120]
        if self.runtime.protocol == "dashscope":
            return self._dashscope_image(title, hint, out_path, kind="inline")
        if self.runtime.protocol == "openai":
            return self._openai_image(title, hint, out_path, kind="inline")
        raise ValueError(f"unsupported inline image protocol: {self.runtime.protocol}")

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _raise_api_error(self, response, provider: str) -> None:
        if response.status_code < 400:
            return
        detail = ""
        try:
            payload = response.json()
            detail = (
                payload.get("message")
                or payload.get("error", {}).get("message")
                or payload.get("code")
                or str(payload)
            )
        except (ValueError, AttributeError):
            detail = (response.text or "").strip()
        detail = str(detail)[:500] or response.reason or "未知错误"
        tips = {
            401: f"当前服务源为“{self.runtime.label}”，请确认 Key 属于该服务源且尚未失效。",
            403: "请确认该账号已开通图片模型，并具有当前模型的调用权限。",
            404: "请检查模型名称，并确认该服务确实实现了 OpenAI Images 的 /images/generations 接口。",
            429: "图片服务额度或频率已达到上限，请稍后重试或检查服务商余额。",
        }
        tip = tips.get(response.status_code, "")
        lowered = (detail or "").lower()
        if response.status_code == 404 and ("dashscope" in self.runtime.submit_url.lower() or "aliyuncs" in self.runtime.submit_url.lower()):
            tip = (
                "阿里云百炼的 OpenAI 兼容模式没有图片生成接口；"
                "请把 Base URL 换成原生图片端点 "
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
            )
        if response.status_code == 400 and ("size" in lowered and ("not valid" in lowered or "must be" in lowered or "pixel" in lowered)):
            configured = self._parse_size(self.img_cfg.api_size)
            tip = (
                "当前尺寸不被该服务商接受。部分模型（如火山 Seedream）有最低总像素要求"
                "（例如至少 2560x1440=3686400 像素）；"
                "请在配置中心把“生成尺寸”改为该模型支持的尺寸后重试。"
            )
            if configured:
                tip += f" 当前配置尺寸为 {configured[0]}x{configured[1]}。"
        if self._is_copyright_rejection(response):
            tip = "提示词触发服务商版权/IP限制；系统已使用不含具体实体的原创编辑插画描述重试，若仍失败可改用 Pillow 配图。"
        failure = "请求被拒绝" if response.status_code == 400 else "连接失败"
        raise RuntimeError(f"{provider} {failure}（HTTP {response.status_code}）：{detail}{(' ' + tip) if tip else ''}")

    @staticmethod
    def _parse_color(hex_color: str):
        from PIL import ImageColor

        return ImageColor.getrgb(hex_color)

    @staticmethod
    def _font_has_cjk(font) -> bool:
        """通过多个汉字的字形差异检测字体是否真的包含中文字形。"""
        try:
            signatures = []
            for char in "观思辩明中":
                mask = font.getmask(char)
                signatures.append((mask.size, bytes(mask)))
            # 不支持 CJK 的西文字体通常会为每个汉字返回同一个缺字方框。
            return len(set(signatures)) >= 3
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    def _load_font(cls, size: int):
        from PIL import ImageFont

        configured = os.environ.get("WECHAT_AGENT_CJK_FONT", "").strip()
        candidates = [
            configured,
            "C:/Windows/Fonts/msyhbd.ttc",        # Windows 微软雅黑粗体
            "C:/Windows/Fonts/msyh.ttc",          # Windows 微软雅黑
            "C:/Windows/Fonts/simhei.ttf",        # Windows 黑体
            "C:/Windows/Fonts/simsun.ttc",        # Windows 宋体
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
        ]
        for path in candidates:
            if not path:
                continue
            try:
                font = ImageFont.truetype(path, size)
                if cls._font_has_cjk(font):
                    return font
                logger.warning("跳过不含中文字形的字体：%s", path)
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError(
            "未找到可用的中文字体，已停止生成图片以避免出现乱码。"
            "Linux/Docker 请安装 fonts-noto-cjk，或通过 WECHAT_AGENT_CJK_FONT 指定中文字体文件。"
        )

    def _wrap_title(self, draw, title: str, font, max_width: int, max_lines: int) -> List[str]:
        from PIL import ImageFont

        lines: List[str] = []
        for para in title.split("\n"):
            para = para.strip()
            if not para:
                continue
            current = ""
            for ch in para:
                if draw.textlength(current + ch, font=font) <= max_width:
                    current += ch
                else:
                    lines.append(current)
                    current = ch
                    if len(lines) >= max_lines:
                        break
            if current and len(lines) < max_lines:
                lines.append(current)
            if len(lines) >= max_lines:
                break
        if not lines:
            lines = [title[:12]]
        return lines[:max_lines]

