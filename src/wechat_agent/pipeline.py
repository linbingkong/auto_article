"""发布流水线编排模块（Pipeline）。

把各环节串成一条完整流水线：
热点采集 → 选题筛选 → AI 写作 → 配图/封面 → 排版 → 上传素材 → 投递草稿箱
→ 通知人工发表 → 归档输出。

支持 dry_run 模式（不调用公众号 API，只生成本地文件），便于先本地跑通。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config
from .database import database_target_from_env
from .evidence import EvidenceCollector
from .formatter import MarkdownFormatter
from .hot_topics import HotTopic, HotTopicFetcher
from .history import HistoryStore
from .images import ImageGenerator
from .llm import LLMClient
from .notify import Notifier
from .topic_selector import ScoredTopic, TopicSelector
from .wechat_api import WechatClient
from .writer import Article, ArticleWriter

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """一次运行的结果。"""

    date: str = ""
    selected: List[ScoredTopic] = field(default_factory=list)
    articles: List[Article] = field(default_factory=list)
    drafts: List[Dict[str, str]] = field(default_factory=list)  # {title, media_id}
    output_dir: str = ""
    dry_run: bool = False
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "selected": [s.title for s in self.selected],
            "articles": [a.title for a in self.articles],
            "drafts": self.drafts,
            "output_dir": self.output_dir,
            "dry_run": self.dry_run,
            "errors": self.errors,
        }


class Pipeline:
    """公众号自动发文流水线。"""

    def __init__(self, config: Config):
        self.cfg = config
        self.fetcher = HotTopicFetcher()
        self.selector = TopicSelector(config.topic_filter)
        self.llm = LLMClient(config.llm)
        self.writer = ArticleWriter(self.llm, config)
        self.images = ImageGenerator(config)
        self.formatter = MarkdownFormatter()
        self.notifier = Notifier(config.notify)
        self.history = HistoryStore(database_target_from_env(config.output_path / "history.db"))
        # 仅当配置了公众号凭证时才初始化（dry_run 无需）
        self.wechat: Optional[WechatClient] = None
        if config.wechat.app_id and config.wechat.app_secret:
            self.wechat = WechatClient(config)

    # ------------------------------------------------------------------
    def run(
        self,
        max_articles: int = 1,
        dry_run: bool = False,
        sources: Optional[List[str]] = None,
    ) -> PipelineResult:
        """执行完整流水线。"""
        result = PipelineResult(
            date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            dry_run=dry_run,
        )
        out_dir = self.cfg.output_path / datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        result.output_dir = str(out_dir)

        if not dry_run and self.wechat is None:
            result.errors.append(
                "未配置公众号 AppID/AppSecret；正式运行已取消，请配置凭证或使用 --dry-run"
            )
            self._save_json(out_dir / "result.json", result.to_dict())
            return result

        # 1. 热点采集
        topics = self.fetcher.fetch_all(sources or self.cfg.hot_sources)
        if not topics:
            result.errors.append("未采集到任何热点")
            logger.error("no hot topics fetched")
            return result
        logger.info("fetched %d topics from %d sources", len(topics), len(self.cfg.hot_sources))

        # 1.5 历史去重：默认跳过最近 7 天已经写过的选题。
        recent = self.history.recent_topic_norms(days=7)
        fresh_topics = [
            t for t in topics if self.history.normalize_topic(t.title) not in recent
        ]
        if fresh_topics:
            topics = fresh_topics
        elif recent:
            logger.warning("所有热点都在 7 天历史中，回退为允许重复选题")

        # 2. 选题筛选
        selected = self.selector.select(topics)
        result.selected = selected
        if not selected:
            result.errors.append("选题筛选后无候选")
            logger.error("no topic selected")
            return result
        logger.info("selected %d topics", len(selected))
        self._save_json(out_dir / "selected_topics.json", [s.title for s in selected])

        # 3. 逐篇写作 + 配图 + 排版 + 投递
        for i, s in enumerate(selected[:max_articles]):
            try:
                self._process_one(s, out_dir / f"article_{i + 1}", result, dry_run)
            except Exception as e:  # noqa: BLE001
                logger.exception("article %d failed", i + 1)
                result.errors.append(f"article '{s.title}' failed: {e}")

        # 4. 结果归档
        self._save_json(out_dir / "result.json", result.to_dict())
        logger.info("pipeline done: %d articles, %d errors", len(result.articles), len(result.errors))
        return result

    # ------------------------------------------------------------------
    def _process_one(
        self, s: ScoredTopic, dir_: Path, result: PipelineResult, dry_run: bool
    ) -> None:
        dir_.mkdir(parents=True, exist_ok=True)
        topic: HotTopic = s.topic

        # 3.1 多源研究与写作（与 Web 管理台同一证据链）
        evidence = EvidenceCollector(self.cfg.search).collect(topic)
        article = self.writer.write(topic.title, evidence.text)
        (dir_ / "article.md").write_text(
            f"# {article.title}\n\n{article.content_md}", encoding="utf-8"
        )

        # 3.2 封面 + 内文配图
        cover_path = dir_ / "cover.png"
        subtitle = f"#{topic.source} 热点"
        self.images.generate_cover(article.title, cover_path, subtitle)
        inline_texts = [sec.replace("## ", "") for sec in article.outline[:3]]
        inline_paths = self.images.generate_inline(inline_texts, dir_)

        # 3.3 排版（把内文图插入到正文开头）。HTML 中保留相对文件名，
        # 线上发布前再替换成微信 uploadimg 返回的 URL，避免目录重复拼接。
        md_with_images = article.content_md
        img_block = "".join(f"![配图]({p.name})\n\n" for p in inline_paths)
        md_with_images = img_block + md_with_images
        content_html = self.formatter.convert(md_with_images)

        article.meta["content_html_len"] = len(content_html)
        (dir_ / "article.html").write_text(content_html, encoding="utf-8")

        # 3.4 上传 + 草稿（dry_run 跳过）
        if dry_run or self.wechat is None:
            status = "dry_run" if dry_run else "local_only"
            logger.info("[%s] draft skipped: %s", status, article.title)
            result.articles.append(article)
            self.history.record(
                topic=topic.title,
                source=topic.source,
                score=s.score,
                article_title=article.title,
                status=status,
                output_dir=str(dir_),
            )
            return

        wechat = self.wechat
        thumb_media_id = wechat.upload_material_image(cover_path)
        # 把 HTML 里的本地图片路径替换为 uploadimg 得到的 URL
        url_map: Dict[str, str] = {}
        for p in inline_paths:
            url_map[p.name] = wechat.upload_img(p)
        final_html = content_html
        for local, url in url_map.items():
            final_html = final_html.replace(local, url)

        media_id = wechat.add_draft(
            title=article.title,
            digest=article.digest,
            content_html=final_html,
            thumb_media_id=thumb_media_id,
        )
        result.drafts.append({"title": article.title, "media_id": media_id})
        result.articles.append(article)
        logger.info("draft created: %s (%s)", article.title, media_id)

        # 3.5 通知人工发表
        self.notifier.notify_draft_ready(article.title, article.digest, media_id)

        # 3.6 可选：全自动模式
        status = "draft_created"
        if self.cfg.wechat.publish_mode == "freepublish":
            publish_id = wechat.freepublish_submit(media_id)
            wechat.freepublish_wait(publish_id)
            status = "published"
            logger.info("published (freepublish): %s", article.title)

        self.history.record(
            topic=topic.title,
            source=topic.source,
            score=s.score,
            article_title=article.title,
            draft_media_id=media_id,
            status=status,
            output_dir=str(dir_),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _save_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")




