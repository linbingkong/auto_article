"""通知模块：企业微信 / 飞书 webhook 推送。

半自动模式下，AI 完成创作并投递草稿箱后，通过 webhook 通知管理员
"草稿已就绪，请到后台审阅并发表"。
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from .config import NotifyConfig

logger = logging.getLogger(__name__)


class Notifier:
    """webhook 通知器（feishu / wecom 兼容）。"""

    def __init__(self, config: NotifyConfig):
        self.cfg = config

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled and bool(self.cfg.webhook_url)

    def send_text(self, text: str) -> bool:
        """发送纯文本消息。"""
        if not self.enabled:
            logger.info("notify disabled, skip: %s", text[:80])
            return False
        url = self.cfg.webhook_url
        if self.cfg.webhook_type == "feishu":
            payload = {"msg_type": "text", "content": {"text": text}}
        elif self.cfg.webhook_type == "wecom":
            payload = {"msgtype": "text", "text": {"content": text}}
        else:
            raise ValueError(f"unsupported webhook type: {self.cfg.webhook_type}")
        try:
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
            logger.info("notify sent: %s", text[:60])
            return True
        except requests.RequestException as e:
            logger.warning("notify failed: %s", e)
            return False

    def send_markdown(self, title: str, body: str) -> bool:
        """发送富文本消息（feishu 支持 markdown）。"""
        if not self.enabled:
            return False
        if self.cfg.webhook_type == "feishu":
            content = f"**{title}**\n{body}"
            payload = {"msg_type": "text", "content": {"text": content}}
        else:  # wecom markdown
            content = f"**{title}**\n{body}"
            payload = {"msgtype": "markdown", "markdown": {"content": content}}
        try:
            r = requests.post(self.cfg.webhook_url, json=payload, timeout=15)
            r.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.warning("notify markdown failed: %s", e)
            return False

    def notify_draft_ready(self, title: str, digest: str = "", draft_media_id: str = "") -> bool:
        """通知草稿已投递，等待人工发表。"""
        body = (
            f"📝 今日文章草稿已生成并投递到公众号草稿箱\n\n"
            f"标题：{title}\n"
            f"摘要：{digest[:100]}\n"
            f"草稿ID：{draft_media_id}\n\n"
            f"👉 请登录 mp.weixin.qq.com → 草稿箱 → 审阅后点击【发表】"
        )
        return self.send_text(body)
