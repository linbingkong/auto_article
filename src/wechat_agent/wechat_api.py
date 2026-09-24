"""微信公众号官方 API 模块（合规发布通道）。

核心链路：stable_token 取 token → material/uploadimg 传正文图 →
material/add_material 传封面 → draft/add 建草稿 →（可选 freepublish/submit 正式发表）。

参考官方文档：
- 发布能力: https://developers.weixin.qq.com/doc/subscription/guide/product/publish.html
- 发布草稿: https://developers.weixin.qq.com/doc/subscription/api/public/api_freepublish_submit
- 上传素材: https://developers.weixin.qq.com/doc/service/api/material/permanent/api_addmaterial
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .config import Config

logger = logging.getLogger(__name__)

API_BASE = "https://api.weixin.qq.com"


class WechatAPIError(RuntimeError):
    """公众号 API 调用错误。"""

    def __init__(self, errcode: int, errmsg: str):
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"Wechat API error {errcode}: {errmsg}")


class WechatClient:
    """公众号 API 客户端。"""

    def __init__(self, config: Config):
        self.cfg = config.wechat
        self._token: str = ""
        self._token_expire: float = 0.0
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # token 管理（stable_token + 2小时缓存）
    # ------------------------------------------------------------------
    @property
    def access_token(self) -> str:
        if self._token and time.time() < self._token_expire - 60:
            return self._token
        self._refresh_token()
        return self._token

    def _refresh_token(self) -> None:
        if not self.cfg.app_id or not self.cfg.app_secret:
            raise WechatAPIError(-1, "未配置 WECHAT_APP_ID / WECHAT_APP_SECRET")
        r = self._session.post(
            f"{API_BASE}/cgi-bin/stable_token",
            json={
                "grant_type": "client_credential",
                "appid": self.cfg.app_id,
                "secret": self.cfg.app_secret,
                "force_refresh": False,
            },
            timeout=15,
        )
        data = r.json()
        if "access_token" not in data:
            raise WechatAPIError(data.get("errcode", -1), data.get("errmsg", "token failed"))
        self._token = data["access_token"]
        self._token_expire = time.time() + int(data.get("expires_in", 7200))
        logger.info("access_token refreshed (expires in %ss)", data.get("expires_in", 7200))

    # ------------------------------------------------------------------
    # 通用请求
    # ------------------------------------------------------------------
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = dict(params or {})
        params["access_token"] = self.access_token
        r = self._session.get(f"{API_BASE}/cgi-bin/{path}", params=params, timeout=20)
        return self._check(r.json())

    def _post(self, path: str, body: Any, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = dict(params or {})
        params["access_token"] = self.access_token
        r = self._session.post(
            f"{API_BASE}/cgi-bin/{path}", params=params, json=body, timeout=30
        )
        return self._check(r.json())

    def _upload(self, path: str, files: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = dict(params or {})
        params["access_token"] = self.access_token
        r = self._session.post(
            f"{API_BASE}/cgi-bin/{path}", params=params, files=files, timeout=60
        )
        return self._check(r.json())

    def _check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if data.get("errcode", 0) != 0:
            errcode = data.get("errcode", -1)
            errmsg = data.get("errmsg", "unknown")
            if errcode == 40001:  # token 失效，清缓存下次自动刷新
                self._token = ""
                self._token_expire = 0.0
                logger.warning("access_token invalid (40001), cache cleared")
            raise WechatAPIError(errcode, errmsg)
        return data

    # ------------------------------------------------------------------
    # 素材上传
    # ------------------------------------------------------------------
    def upload_img(self, image_path: Path) -> str:
        """media/uploadimg：上传图片获得正文可用的 URL（不占素材库）。"""
        with open(image_path, "rb") as f:
            data = self._upload(
                "media/uploadimg",
                files={"media": (image_path.name, f, "image/png")},
            )
        return data["url"]

    def upload_material_image(self, image_path: Path) -> str:
        """material/add_material(type=image)：上传永久图片素材，返回 media_id（用于封面）。"""
        with open(image_path, "rb") as f:
            data = self._upload(
                "material/add_material",
                files={"media": (image_path.name, f, "image/png")},
                params={"type": "image"},
            )
        return data["media_id"]

    # ------------------------------------------------------------------
    # 草稿
    # ------------------------------------------------------------------
    def add_draft(
        self,
        title: str,
        content_html: str,
        thumb_media_id: str,
        digest: str = "",
        author: str = "",
        content_source_url: str = "",
        need_open_comment: int = 1,
        only_fans_can_comment: int = 0,
    ) -> str:
        """draft/add：新建草稿，返回 media_id。"""
        article: Dict[str, Any] = {
            "title": title,
            "author": author,
            "digest": digest[:120],
            "content": content_html,
            "content_source_url": content_source_url,
            "thumb_media_id": thumb_media_id,
            "need_open_comment": need_open_comment,
            "only_fans_can_comment": only_fans_can_comment,
        }
        data = self._post("draft/add", {"articles": [article]})
        return data["media_id"]

    def update_draft(self, media_id: str, index: int, article: Dict[str, Any]) -> None:
        """draft/update：更新草稿。"""
        self._post("draft/update", {"media_id": media_id, "index": index, "articles": article})

    def get_draft(self, media_id: str) -> Dict[str, Any]:
        return self._post("draft/get", {"media_id": media_id})

    # ------------------------------------------------------------------
    # 发布（可选，半自动模式下不用）
    # ------------------------------------------------------------------
    def freepublish_submit(self, media_id: str) -> str:
        """freepublish/submit：把草稿正式发表，返回 publish_id。

        注意：半自动模式（推荐）不调用此接口，由人工在后台点击发表。
        全自动模式使用时有可见性异常与平台治理风险，请谨慎。
        """
        data = self._post("freepublish/submit", {"media_id": media_id})
        return data["publish_id"]

    def freepublish_get(self, publish_id: str) -> Dict[str, Any]:
        return self._post("freepublish/get", {"publish_id": publish_id})

    def freepublish_wait(self, publish_id: str, timeout: int = 120, interval: int = 5) -> Dict[str, Any]:
        """轮询 freepublish/get 直到发布完成或超时。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.freepublish_get(publish_id)
            status = data.get("publish_status", -1)
            if status == 0:  # 成功
                return data
            if status == 1:  # 发布中
                time.sleep(interval)
                continue
            # 失败
            raise WechatAPIError(-2, f"freepublish failed, status={status}")
        raise WechatAPIError(-3, "freepublish wait timeout")


def build_draft_payload(
    title: str,
    digest: str,
    content_html: str,
    thumb_media_id: str,
    author: str = "",
) -> Dict[str, Any]:
    """构造 draft/add 的 article 参数（便于测试与复用）。"""
    return {
        "title": title,
        "author": author,
        "digest": digest[:120],
        "content": content_html,
        "content_source_url": "",
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }


