"""热点采集模块。

从多个平台的公开 JSON 接口抓取热搜/热榜，统一为 HotTopic 数据结构。
数据源（均为免费公开接口，无需登录）：
- 微博热搜:  weibo.com/ajax/side/hotSearch
- 知乎热榜:  zhihu.com/api/v3/feed/topstory/hot-list-web
- 今日头条:  toutiao.com/hot-event/hot-board/?origin=toutiao_pc
- 财联社电报: cls.cn/nodeapi/telegraphList
- B站热榜:   api.bilibili.com/x/web-interface/ranking/v2
- 百度热搜:  top.baidu.com/board?tab=realtime（页面内嵌 JSON）
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# 默认请求头（避免被部分站点按 UA 拒绝）
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


@dataclass
class HotTopic:
    """统一的热点数据结构。"""

    title: str
    source: str                 # weibo / zhihu / toutiao / cls / bili / baidu
    rank: int = 0               # 榜单排名（越小越热）
    heat: Optional[int] = None  # 热度值（各平台口径不同，可能缺失）
    url: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover
        return f"HotTopic(#{self.rank} [{self.source}] {self.title} heat={self.heat})"


class HotTopicFetcher:
    """聚合多个平台的热点抓取。每个平台一个方法，失败自动降级跳过。"""

    def __init__(self, timeout: float = 15.0, proxies: Optional[Dict[str, str]] = None):
        self.timeout = timeout
        self.proxies = proxies
        self._session = requests.Session()
        # 热榜均为国内端点；没有显式传代理时不继承系统代理，避免本机代理
        # 导致知乎超时、CLS/B 站 TLS EOF 等随机故障。
        self._session.trust_env = bool(proxies)
        self._session.headers.update(_HEADERS)

    def _get(self, url: str, **kwargs):
        """GET 热榜接口；只对幂等请求做短暂网络重试。"""
        last_exc: Exception | None = None
        request_timeout = kwargs.pop("timeout", self.timeout)
        for attempt in range(3):
            try:
                response = self._session.get(
                    url,
                    timeout=request_timeout,
                    proxies=self.proxies,
                    **kwargs,
                )
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep((1, 3)[attempt])
        raise RuntimeError(f"热点接口连续 3 次请求失败：{last_exc}") from last_exc

    @staticmethod
    def _heat_number(value) -> Optional[int]:
        """把“766 万热度”等展示文本转换成近似整数热度。"""
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).replace(",", "").strip()
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([万亿]?)", text)
        if not match:
            return None
        multiplier = {"": 1, "万": 10_000, "亿": 100_000_000}[match.group(2)]
        return int(float(match.group(1)) * multiplier)

    # ------------------------------------------------------------------
    # 各平台抓取
    # ------------------------------------------------------------------
    def fetch_weibo(self, limit: int = 50) -> List[HotTopic]:
        """微博热搜。接口: weibo.com/ajax/side/hotSearch"""
        try:
            # 微博目前会对缺少页面来源头的裸 API 请求返回 403。
            r = self._get(
                "https://weibo.com/ajax/side/hotSearch",
                headers={"Referer": "https://weibo.com/hot/search", "Origin": "https://weibo.com"},
            )
            data = r.json()
            if data.get("ok") not in (None, 1):
                raise RuntimeError(data.get("message") or data.get("error") or "微博接口拒绝请求")
            items = data.get("data", {}).get("realtime", []) or []
            out: List[HotTopic] = []
            for i, item in enumerate(items[:limit]):
                title = item.get("word") or item.get("note") or ""
                if not title:
                    continue
                out.append(
                    HotTopic(
                        title=title,
                        source="weibo",
                        rank=i + 1,
                        heat=item.get("num"),
                        url=f"https://s.weibo.com/weibo?q={quote(str(title))}",
                        extra={"label": item.get("label_name", "")},
                    )
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("weibo fetch failed: %s", e)
            return []

    def fetch_zhihu(self, limit: int = 50) -> List[HotTopic]:
        """知乎热榜。接口: /api/v3/feed/topstory/hot-list-web"""
        try:
            r = self._get(
                "https://www.zhihu.com/api/v3/feed/topstory/hot-list-web?limit=50",
                headers={"Referer": "https://www.zhihu.com/hot"},
            )
            data = r.json()
            items = data.get("data", []) or []
            out: List[HotTopic] = []
            for i, item in enumerate(items[:limit]):
                target = item.get("target", {}) or {}
                # 兼容知乎新旧两版结构：新接口把标题、热度、链接分别放入
                # title_area / metrics_area / link，旧接口使用扁平字段。
                title = (
                    target.get("title")
                    or (target.get("question") or {}).get("title")
                    or (target.get("title_area") or {}).get("text")
                    or ""
                )
                if not title:
                    continue
                heat_text = item.get("detail_text") or (target.get("metrics_area") or {}).get("text")
                url = target.get("url") or (target.get("link") or {}).get("url") or ""
                excerpt = (target.get("excerpt_area") or {}).get("text") or ""
                out.append(
                    HotTopic(
                        title=title,
                        source="zhihu",
                        rank=i + 1,
                        heat=self._heat_number(heat_text),
                        url=url,
                        extra={"content": excerpt[:500], "heat_text": str(heat_text or "")},
                    )
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("zhihu fetch failed: %s", e)
            return []

    def fetch_toutiao(self, limit: int = 50) -> List[HotTopic]:
        """今日头条热榜。接口: /hot-event/hot-board/"""
        try:
            r = self._get(
                "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"
            )
            data = r.json()
            items = data.get("data", []) or []
            out: List[HotTopic] = []
            for i, item in enumerate(items[:limit]):
                title = item.get("Title") or item.get("title", "")
                if not title:
                    continue
                out.append(
                    HotTopic(
                        title=title,
                        source="toutiao",
                        rank=i + 1,
                        heat=item.get("HotValue"),
                        url=item.get("Url", ""),
                    )
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("toutiao fetch failed: %s", e)
            return []

    def fetch_cls(self, limit: int = 30) -> List[HotTopic]:
        """财联社电报。新版 Next.js 缓存接口: /api/cache?name=telegraph"""
        try:
            # 旧 /nodeapi/telegraphList 已随财联社新版站点下线并返回 404；
            # 页面自身当前使用该无签名缓存接口加载首屏电报。
            r = self._get(
                "https://www.cls.cn/api/cache?name=telegraph",
                headers={"Referer": "https://www.cls.cn/telegraph"},
            )
            data = r.json()
            if data.get("errno") not in (None, 0):
                raise RuntimeError(data.get("errmsg") or data.get("message") or f"errno={data.get('errno')}")
            items = (data.get("data", {}) or {}).get("roll_data", []) or []
            out: List[HotTopic] = []
            for i, item in enumerate(items[:limit]):
                content = item.get("brief") or item.get("content", "")
                title = item.get("title") or content
                title = re.sub(r"<[^>]+>", "", str(title)).strip()
                content = re.sub(r"<[^>]+>", "", str(content)).strip()
                if not title:
                    continue
                item_id = item.get("id")
                out.append(
                    HotTopic(
                        title=title[:100],
                        source="cls",
                        rank=i + 1,
                        heat=self._heat_number(item.get("reading_num") or item.get("level")),
                        url=f"https://www.cls.cn/detail/{item_id}" if item_id else "https://www.cls.cn/telegraph",
                        extra={"content": content[:500]},
                    )
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("cls fetch failed: %s", e)
            return []

    def fetch_bilibili(self, limit: int = 30) -> List[HotTopic]:
        """B站热榜；ranking 风控时自动回退 popular 接口。"""
        try:
            r = self._get(
                "https://api.bilibili.com/x/web-interface/ranking/v2",
                headers={"Referer": "https://www.bilibili.com/v/popular/rank/all"},
            )
            data = r.json()
            items = (data.get("data", {}) or {}).get("list", []) or []
            # ranking/v2 会间歇返回 code=-352 风控和空列表；popular 是
            # B站页面使用的公开备选接口，字段结构与 ranking 一致。
            if data.get("code") != 0 or not items:
                r = self._get(
                    "https://api.bilibili.com/x/web-interface/popular?ps=50&pn=1",
                    headers={"Referer": "https://www.bilibili.com/v/popular/all"},
                )
                data = r.json()
                items = (data.get("data", {}) or {}).get("list", []) or []
            if data.get("code") != 0:
                raise RuntimeError(data.get("message") or f"code={data.get('code')}")
            out: List[HotTopic] = []
            for i, item in enumerate(items[:limit]):
                title = item.get("title", "")
                if not title:
                    continue
                out.append(
                    HotTopic(
                        title=title,
                        source="bili",
                        rank=i + 1,
                        heat=item.get("stat", {}).get("view"),
                        url=f"https://www.bilibili.com/video/{item.get('bvid', '')}",
                    )
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("bilibili fetch failed: %s", e)
            return []

    def fetch_baidu(self, limit: int = 30) -> List[HotTopic]:
        """百度热搜。页面内嵌 JSON，正则提取。"""
        try:
            r = self._get(
                "https://top.baidu.com/board?tab=realtime",
                headers={"Referer": "https://top.baidu.com/"},
            )
            html = r.text
            m = re.search(r"<!--s-data:(.*?)-->", html, re.S)
            if not m:
                return []
            payload = json.loads(m.group(1).strip())
            items = (payload.get("data", {}) or {}).get("cards", [{}])[0].get(
                "content", []
            ) or []
            out: List[HotTopic] = []
            for i, item in enumerate(items[:limit]):
                title = item.get("word", "")
                if not title:
                    continue
                out.append(
                    HotTopic(
                        title=title,
                        source="baidu",
                        rank=i + 1,
                        heat=item.get("hotScore"),
                        url=item.get("url", ""),
                    )
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("baidu fetch failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # 聚合入口
    # ------------------------------------------------------------------
    SOURCES = {
        "weibo": fetch_weibo,
        "zhihu": fetch_zhihu,
        "toutiao": fetch_toutiao,
        "cls": fetch_cls,
        "bili": fetch_bilibili,
        "baidu": fetch_baidu,
    }

    def fetch_all(self, sources: Optional[List[str]] = None) -> List[HotTopic]:
        """抓取指定（或全部）平台的热点并合并。"""
        sources = sources or list(self.SOURCES.keys())
        merged: List[HotTopic] = []
        for name in sources:
            fn = self.SOURCES.get(name)
            if not fn:
                logger.warning("unknown source: %s", name)
                continue
            try:
                topics = fn(self)
                logger.info("source %s: %d topics", name, len(topics))
                merged.extend(topics)
            except Exception as e:  # noqa: BLE001
                logger.warning("source %s failed: %s", name, e)
        return merged


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    f = HotTopicFetcher()
    topics = f.fetch_all(["weibo", "zhihu", "toutiao"])
    for t in topics[:20]:
        print(t)
    print(f"total: {len(topics)}")
