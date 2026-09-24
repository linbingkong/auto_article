"""热点信源采集：为写作与事实核验构建可审计证据包。"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from datetime import date, timedelta
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, List, Optional
from urllib.parse import urljoin, urlparse

import requests

from .config import SearchConfig
from .search import SearchError, TavilySearchService

logger = logging.getLogger(__name__)


@dataclass
class EvidencePack:
    text: str
    sources: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    grade: str = "weak"
    institution_topic: bool = False
    substantive_source_count: int = 0
    official_source_count: int = 0
    story_ready: bool = False


class _ArticleTextParser(HTMLParser):
    BLOCKS = {"title", "h1", "h2", "h3", "p", "li", "blockquote", "figcaption"}
    SKIP = {"script", "style", "noscript", "svg", "canvas", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.capture_depth = 0
        self.current: List[str] = []
        self.blocks: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip_depth += 1
        if not self.skip_depth and tag in self.BLOCKS:
            self.capture_depth += 1
            if self.capture_depth == 1:
                self.current = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
            return
        if not self.skip_depth and tag in self.BLOCKS and self.capture_depth:
            self.capture_depth -= 1
            if self.capture_depth == 0:
                text = re.sub(r"\s+", " ", "".join(self.current)).strip()
                if len(text) >= 4 and text not in self.blocks:
                    self.blocks.append(text)

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and self.capture_depth:
            self.current.append(data)


class EvidenceCollector:
    """以热点为线索执行多源研究，并构建带来源编号的证据包。"""

    INSTITUTION_RE = re.compile(r"制度|政策|办法|条例|规定|通知|意见|法律|法规|监管|补贴|标准|规则|改革")
    OFFICIAL_HOST_RE = re.compile(r"(?:^|\.)(?:gov\.cn|court\.gov\.cn|spp\.gov\.cn)$", re.I)

    def __init__(self, search_config: Optional[SearchConfig] = None, timeout: float = 12.0, max_bytes: int = 1_200_000):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.search_config = search_config
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; GuanSiBianMingBot/0.3; +editorial-research)"})

    @classmethod
    def _source_type(cls, url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        if cls.OFFICIAL_HOST_RE.search(host):
            return "official"
        if any(name in host for name in ("xinhuanet.com", "people.com.cn", "thepaper.cn", "nfnews.com", "chinanews.com.cn", "cctv.com")):
            return "news_report"
        return "web_source"

    @classmethod
    def _research_queries(cls, title: str, institution_topic: bool, style: str | None = None) -> List[str]:
        queries = [title, f"{title} 官方回应 公告 原文"]
        queries.append(f"{title} 政策 制度 办法 条例 原文 site:gov.cn" if institution_topic else f"{title} 数据 背景 最新进展")
        queries.append(f"{title} 同类案例 社会现象 调查 数据 争议")
        if style == "story":
            queries.extend((f"{title} 历史 起源 关键节点 时间线", f"{title} 早期 官方资料 历史报道"))
        elif style == "deep":
            queries.extend((f"{title} 各方观点 分歧 回应 原文", f"{title} 背景数据 机制 影响 边界"))
        return queries

    @staticmethod
    def _dated_claims(text: str) -> List[tuple[date, str]]:
        """Only surface dates actually printed by a source, not inferred milestones."""
        claims = []
        for match in re.finditer(r"(?<!\d)(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日?", text):
            try:
                when = date(*map(int, match.groups()))
            except ValueError:
                continue
            if when > date.today():
                continue
            left = max(text.rfind("。", 0, match.start()) + 1, match.start() - 90)
            right = text.find("。", match.end())
            excerpt = text[left:(right + 1 if right >= 0 else match.end() + 100)].strip()[:220]
            prefix = text[max(0, match.start() - 16):match.start()]
            if re.search(r"(?:发布时间|发布于|更新时间|更新于|责任编辑|稿源)[:：\s]*$", prefix):
                continue
            # A printed date alone (e.g. a page timestamp) is not an event.
            if not re.search(r"宣布|启动|发布|印发|发生|完成|批准|披露|回应|推出|上线|签署|通过|实施|公布|确认|达成|收购|成立|停止|撤销", excerpt[match.start() - left:]):
                continue
            claims.append((when, excerpt))
        return claims

    @classmethod
    def _story_timeline(cls, sources: List[dict], source_texts: dict[str, str]) -> str:
        milestones = []
        for source in sources:
            if source["type"] == "hotspot_summary" or not source["url"]:
                continue
            for when, excerpt in cls._dated_claims(source_texts[source["id"]]):
                milestones.append((when, source, excerpt))
        milestones.sort(key=lambda item: item[0])
        if not milestones:
            return ""
        previous = milestones[0]
        current = next((item for item in reversed(milestones) if item[0] > previous[0] and item[1]["id"] != previous[1]["id"]), None)
        if current is None or current[0] < date.today() - timedelta(days=180):
            return ""
        def line(label: str, item: tuple) -> str:
            when, source, excerpt = item
            return f"【{label}】{when.isoformat()}｜{source['id']}｜{source['label']}｜{source['url']}｜{excerpt}"
        intermediate = []
        used = {previous[1]["id"], current[1]["id"]}
        for item in milestones:
            if previous[0] < item[0] < current[0] and item[1]["id"] not in used:
                intermediate.append(item)
                used.add(item[1]["id"])
            if len(intermediate) == 3:
                break
        timeline = [line("历史节点", previous)]
        timeline.extend(line("关键转折", item) for item in intermediate)
        timeline.append(line("当前进展", current))
        return "【故事时间线：日期及节点摘录自不同来源，不得把报道发布日期当作事件发生日】\n" + "\n".join(timeline)

    def collect(self, topic, reference_material: str = "", style: str | None = None) -> EvidencePack:
        institution_topic = bool(self.INSTITUTION_RE.search(f"{topic.title} {topic.summary}"))
        sections = [
            "【编辑规则】热点标题只是线索。正文必须逐项标明信息来自哪个来源；事实、解释和未决问题必须分开。不得用反复声明‘待核实’代替核实，也不得在声明未核实后继续替传闻下结论。",
            f"【研究主题】{topic.title}",
        ]
        sources: List[dict] = []
        source_texts: dict[str, str] = {}
        warnings: List[str] = []
        seen_urls = set()

        def add_source(source_type: str, label: str, url: str, text: str) -> None:
            clean_url = (url or "").strip()
            if clean_url and source_type != "hotspot_summary" and clean_url in seen_urls:
                return
            clean = self._clean_text(text)[:6_000]
            if len(clean) < 40:
                return
            if clean_url and source_type != "hotspot_summary":
                seen_urls.add(clean_url)
            source_id = f"S{len(sources) + 1}"
            sources.append({"id": source_id, "type": source_type, "label": label, "url": clean_url})
            source_texts[source_id] = clean
            sections.append(f"【{source_id}｜{label}｜{source_type}】\nURL：{clean_url or '用户提供，无 URL'}\n{clean}")

        if reference_material.strip():
            residual = reference_material
            for url in re.findall(r"https?://[^\s)】」』」]+", reference_material)[:4]:
                try:
                    final_url, page_text = self._fetch_public_text(url)
                    add_source(self._source_type(final_url), "用户提供的参考链接", final_url, page_text)
                    residual = residual.replace(url, "")
                except (ValueError, requests.RequestException, OSError) as exc:
                    logger.warning("reference link fetch failed: %s", exc)
                    warnings.append(f"参考链接采集失败：{str(exc)[:120]}")
            if re.sub(r"https?://\S+", "", residual).strip():
                add_source("user_reference", "用户提供参考资料", "", residual[:20_000])
        if topic.summary.strip():
            add_source("hotspot_summary", f"{topic.source} 榜单摘要（仅作线索）", topic.url, topic.summary)
        if topic.url:
            try:
                final_url, page_text = self._fetch_public_text(topic.url)
                add_source(self._source_type(final_url), "热点来源网页", final_url, page_text)
            except (ValueError, requests.RequestException, OSError) as exc:
                logger.warning("source evidence fetch failed: %s", exc)
                warnings.append(f"热点来源页采集失败：{str(exc)[:160]}")

        if self.search_config and self.search_config.api_key:
            search = TavilySearchService(self.search_config)
            research_items: List[tuple[dict, str]] = []
            for query in self._research_queries(topic.title, institution_topic, style=style):
                phase = "history" if style == "story" and ("历史" in query or "早期" in query) else "current"
                try:
                    result = search.search(query, topic="general", time_range="all", limit=6, include_raw_content=True)
                    research_items.extend((item, phase) for item in result["items"])
                except SearchError as exc:
                    warnings.append(f"研究检索失败：{str(exc)[:160]}")
            research_items.sort(key=lambda pair: (self._source_type(pair[0]["url"]) == "official", pair[0].get("score", 0)), reverse=True)
            # Reserve a historical and a current source before filling the normal source budget.
            if style == "story":
                reserved = []
                for phase in ("history", "current"):
                    match = next((pair for pair in research_items if pair[1] == phase and pair[0]["url"] not in {p[0]["url"] for p in reserved}), None)
                    if match:
                        reserved.append(match)
                research_items = reserved + [pair for pair in research_items if pair not in reserved]
            for item, _phase in research_items:
                if len([s for s in sources if s["type"] != "hotspot_summary"]) >= 8:
                    break
                text = item.get("raw_content") or item.get("summary") or ""
                if len(text) < 200:
                    try:
                        _, fetched = self._fetch_public_text(item["url"])
                        text = fetched or text
                    except Exception:  # noqa: BLE001
                        pass
                add_source(self._source_type(item["url"]), item.get("title") or item.get("source_name") or "检索来源", item["url"], text)
        else:
            warnings.append("未配置 Tavily，生成阶段无法执行多源新闻研究。")

        substantive = [s for s in sources if s["type"] != "hotspot_summary"]
        official_count = sum(s["type"] == "official" for s in substantive)
        grade = "strong" if len(substantive) >= 3 and (not institution_topic or official_count >= 1) else "medium" if len(substantive) >= 2 else "weak"
        if institution_topic and official_count == 0:
            warnings.append("话题涉及制度或政策，但没有找到政府/司法机关原文；禁止对制度内容、适用范围和效果下确定结论。")
        if grade == "weak":
            warnings.append("实质信源不足两条，不应生成长篇评论；建议补充官方文件或权威报道。")
        sections.insert(2, f"【证据等级】{grade}；实质信源 {len(substantive)} 条；官方原文 {official_count} 条；制度议题：{'是' if institution_topic else '否'}")
        timeline = self._story_timeline(sources, source_texts) if style == "story" else ""
        if style == "story":
            if timeline:
                sections.insert(3, timeline)
            else:
                warnings.append("故事化叙事缺少可追溯的历史节点或当前进展：请补充带日期和链接的历史报道、官方资料及最新报道。")
        return EvidencePack(text="\n\n".join(sections), sources=sources, warnings=warnings, grade=grade, institution_topic=institution_topic, substantive_source_count=len(substantive), official_source_count=official_count, story_ready=bool(timeline))

    def _fetch_public_text(self, url: str) -> tuple[str, str]:
        current = url
        for _ in range(4):
            self._validate_public_url(current)
            response = self.session.get(
                current,
                timeout=self.timeout,
                allow_redirects=False,
                stream=True,
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                target = response.headers.get("Location")
                if not target:
                    raise ValueError("来源页返回空重定向")
                current = urljoin(current, target)
                continue
            response.raise_for_status()
            final_url = response.url
            self._validate_public_url(final_url)
            content_type = response.headers.get("Content-Type", "").lower()
            if not any(x in content_type for x in ("text/", "html", "json", "xml")):
                raise ValueError(f"来源页不是文本内容：{content_type or '未知类型'}")
            data = bytearray()
            for chunk in response.iter_content(32_768):
                data.extend(chunk)
                if len(data) > self.max_bytes:
                    break
            declared = (response.headers.get("Content-Type") or "").split("charset=")[-1].split(";")[0].strip().strip('"')
            candidates = [declared, "utf-8", "gb18030"] if declared else ["utf-8", "gb18030"]
            decoded = None
            for encoding in candidates:
                try:
                    decoded = bytes(data[: self.max_bytes]).decode(encoding)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if decoded is None:
                decoded = bytes(data[: self.max_bytes]).decode("utf-8", errors="replace")
            return final_url, self._extract_text(decoded, content_type)
        raise ValueError("来源页重定向次数过多")

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("只允许采集公开 HTTP/HTTPS 来源")
        try:
            records = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        except socket.gaierror as exc:
            raise ValueError("来源域名无法解析") from exc
        for record in records:
            ip = ipaddress.ip_address(record[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise ValueError("为防止 SSRF，禁止采集内网或保留地址")

    @classmethod
    def _extract_text(cls, body: str, content_type: str) -> str:
        if "html" not in content_type and "xml" not in content_type:
            return cls._clean_text(body)[:12_000]
        parser = _ArticleTextParser()
        try:
            parser.feed(body)
        except Exception:  # noqa: BLE001
            pass
        return "\n".join(parser.blocks)[:12_000]

    @staticmethod
    def _clean_text(text: str) -> str:
        text = re.sub(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f]", "", text or "")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
