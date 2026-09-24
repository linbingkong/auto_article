"""榜单外网页/新闻搜索服务（Tavily）。"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .config import SearchConfig


class SearchError(RuntimeError):
    """搜索服务请求或响应错误。"""


class TavilySearchService:
    """将 Tavily 搜索结果转换为现有热点选题结构。"""

    TIME_RANGES = {
        "24h": ("day", 1), "7d": ("week", 7), "30d": ("month", 30),
        "year": ("year", 365), "all": (None, None),
    }

    def __init__(self, config: SearchConfig):
        self.config = config
        base = (config.base_url or "https://api.tavily.com").strip().rstrip("/")
        self.endpoint = base if base.endswith("/search") else f"{base}/search"
        self._session = requests.Session()

    def search(
        self,
        query: str,
        *,
        topic: str = "news",
        time_range: str = "7d",
        limit: int = 20,
        prefer_chinese: bool = True,
        include_raw_content: bool = False,
    ) -> Dict[str, Any]:
        query = re.sub(r"\s+", " ", (query or "").strip())
        if len(query) < 2:
            raise SearchError("搜索关键词至少需要两个字符")
        if len(query) > 200:
            raise SearchError("搜索关键词不能超过 200 个字符")
        if not self.config.api_key:
            raise SearchError("Tavily API Key 未配置，请先到配置中心填写并保存")
        if topic not in {"general", "news"}:
            raise SearchError("搜索类型只支持 general 或 news")
        if time_range not in self.TIME_RANGES:
            raise SearchError("不支持的时间范围")

        range_name, days = self.TIME_RANGES[time_range]
        payload: Dict[str, Any] = {
            "query": query, "topic": topic, "search_depth": self.config.search_depth,
            "max_results": min(max(int(limit), 1), 20), "include_answer": False,
            "include_raw_content": bool(include_raw_content), "include_images": False,
        }
        if range_name:
            payload["time_range"] = range_name
        if topic == "news" and days:
            payload["days"] = days
        try:
            response = self._session.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"},
                json=payload, timeout=self.config.timeout,
            )
        except requests.RequestException as exc:
            raise SearchError(f"Tavily 网络连接失败：{exc}") from exc
        if response.status_code >= 400:
            detail = response.text.strip()[:500] or response.reason
            raise SearchError(f"Tavily 搜索失败（HTTP {response.status_code}）：{detail}")
        try:
            data = response.json()
        except ValueError as exc:
            raise SearchError("Tavily 返回了无法解析的响应") from exc

        items: List[Dict[str, Any]] = []
        seen = set()
        for result in data.get("results") or []:
            title = str(result.get("title") or "").strip()
            url = self._clean_url(str(result.get("url") or ""))
            if not title or not url or url in seen:
                continue
            seen.add(url)
            domain = urlsplit(url).hostname or "网页"
            summary = str(result.get("content") or "").strip()[:1500]
            zh_ratio = self._chinese_ratio(f"{title}\n{summary}")
            items.append({
                "id": uuid.uuid5(uuid.NAMESPACE_URL, url).hex, "title": title[:300],
                "source": "web_search", "source_name": domain.removeprefix("www."),
                "rank": len(items) + 1, "heat": None, "url": url,
                "summary": summary,
                "raw_content": str(result.get("raw_content") or "")[:12_000] if include_raw_content else "",
                "score": self._score(result.get("score")),
                "published_at": str(result.get("published_date") or ""),
                "matched_keywords": [query], "search_provider": "tavily", "evidence_ready": True,
                "language": "zh" if zh_ratio >= 0.3 else "other",
                "chinese_ratio": round(zh_ratio, 2),
            })
        if prefer_chinese:
            items.sort(
                key=lambda item: (
                    item["language"] == "zh",
                    item["chinese_ratio"],
                    item["score"],
                ),
                reverse=True,
            )
            for index, item in enumerate(items, start=1):
                item["rank"] = index
        return {
            "items": items, "query": query, "topic": topic, "time_range": time_range,
            "provider": "tavily", "response_time": data.get("response_time"),
            "request_id": data.get("request_id", ""),
            "prefer_chinese": prefer_chinese,
        }

    @staticmethod
    def _chinese_ratio(text: str) -> float:
        """计算中文字符在可见字符中的占比。"""
        visible = re.sub(r"\s+", "", text or "")
        if not visible:
            return 0.0
        zh = sum(1 for ch in visible if "\u4e00" <= ch <= "\u9fff")
        return zh / len(visible)

    def validate_connection(self) -> Dict[str, Any]:
        result = self.search("人工智能", topic="general", time_range="all", limit=1)
        return {
            "provider": "tavily",
            "result_count": len(result["items"]),
            "chargeable": True,
            "prefer_chinese": result.get("prefer_chinese", True),
        }

    @staticmethod
    def _score(value: Any) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = 0.0
        return round(max(0.0, min(1.0, score)) * 100, 1)

    @staticmethod
    def _clean_url(url: str) -> str:
        try:
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                return ""
            query = urlencode([(key, value) for key, value in parse_qsl(parts.query) if not key.lower().startswith("utm_")])
            return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
        except ValueError:
            return ""
