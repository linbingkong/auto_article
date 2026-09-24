"""Web 文章仓库：生成、版本、预览、保存和推送公众号草稿。"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List

from .config import Config
from .evidence import EvidenceCollector
from .formatter import MarkdownFormatter, normalize_markdown
from .history import HistoryStore
from .hot_topics import HotTopic
from .images import ImageGenerator
from .llm import LLMClient
from .web_models import GenerateRequest
from .wechat_api import WechatClient
from .writer import ArticleWriter, _clean_title


class ArticleNotFoundError(FileNotFoundError):
    pass


def _inline_image_specs(content_md: str, limit: int) -> List[Dict[str, Any]]:
    """选择非引言/结语的核心章节，并提取章节首段作为配图语义。"""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", content_md) if block.strip()]
    heading_indexes = [
        index
        for index, block in enumerate(blocks)
        if re.match(r"^##\s+", block)
        and not re.search(r"引言|导语|结语|总结|写在最后", block, re.I)
    ]
    specs: List[Dict[str, Any]] = []
    for heading_index in heading_indexes[: max(0, limit)]:
        heading = re.sub(r"^##\s+", "", blocks[heading_index]).strip()
        paragraph_index = heading_index
        paragraph = ""
        for index in range(heading_index + 1, len(blocks)):
            if re.match(r"^##\s+", blocks[index]):
                break
            if blocks[index].startswith(("#", "![")):
                continue
            paragraph_index = index
            paragraph = re.sub(r"[`*_>#\[\]]", "", blocks[index])
            paragraph = re.sub(r"\s+", " ", paragraph).strip()
            if paragraph:
                break
        prompt = f"{heading}\n{paragraph[:100]}" if paragraph else heading
        specs.append(
            {
                "after_block": paragraph_index,
                "prompt": prompt,
                "alt": f"{heading}要点图",
            }
        )
    return specs


def _insert_inline_images(
    content_md: str, specs: List[Dict[str, Any]], paths: List[Path]
) -> str:
    """把正文图插入对应核心章节首段后，而不是集中堆在引言前。"""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", content_md) if block.strip()]
    inserts = list(zip(specs, paths))
    for spec, path in sorted(inserts, key=lambda item: item[0]["after_block"], reverse=True):
        image = f"![{spec['alt']}]({path.name})"
        blocks.insert(int(spec["after_block"]) + 1, image)
    return "\n\n".join(blocks)


class ArticleStore:
    """文件式文章仓库，便于本地部署、备份与迁移。"""

    def __init__(self, root: Path, history: HistoryStore):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.history = history

    @staticmethod
    def _valid_id(article_id: str) -> bool:
        return bool(re.fullmatch(r"[a-f0-9]{32}", article_id))

    def _dir(self, article_id: str) -> Path:
        if not self._valid_id(article_id):
            raise ArticleNotFoundError(article_id)
        path = self.root / article_id
        if not path.is_dir():
            raise ArticleNotFoundError(article_id)
        return path

    def generate(
        self,
        request: GenerateRequest,
        config: Config,
        progress: Callable[[int, str], None],
        owner_user: Dict[str, Any] | None = None,
        llm_usage_callback: Callable[[Dict[str, Any]], None] | None = None,
        task_id: str = "",
    ) -> Dict[str, Any]:
        article_id = uuid.uuid4().hex
        article_dir = self.root / article_id
        article_dir.mkdir(parents=True, exist_ok=False)

        if request.options.target_words:
            config = replace(config, target_words=request.options.target_words)
        progress(5, "正在执行多源新闻研究与制度原文检索")
        evidence = EvidenceCollector(config.search).collect(
            request.topic, request.options.reference_material, style=request.options.style
        )
        context = evidence.text

        progress(12, "正在生成证据约束的大纲")
        def report_usage(usage: Dict[str, Any]) -> None:
            if llm_usage_callback:
                llm_usage_callback({**usage, "article_id": article_id})
        writer = ArticleWriter(LLMClient(config.llm, usage_callback=report_usage), config)

        def write_stage(message: str) -> None:
            section = re.search(r"正在撰写第 (\d+)/(\d+) 章", message)
            if section:
                current, total = int(section.group(1)), int(section.group(2))
                progress(16 + int(40 * current / max(1, total)), message)
                return
            mapped = {
                "正在生成证据约束的大纲": 14,
                "正在全文润色": 58,
                "正在两阶段事实核验（修订正文 + 生成审计报告）": 62,
                "正在生成候选标题": 66,
            }
            progress(mapped.get(message, 20), message)

        article = writer.write(request.topic.title, context, progress=write_stage, style=request.options.style)
        progress(68, "正文、标题与事实核验已完成")

        image_generator = ImageGenerator(config)
        cover_path = article_dir / "cover.png"
        image_generator.generate_cover(
            article.title, cover_path, f"#{request.topic.source} 热点"
        )
        progress(76, "封面图已生成")

        inline_paths: List[Path] = []
        image_specs: List[Dict[str, Any]] = []
        if request.options.with_images and config.image.inline_images > 0:
            image_specs = _inline_image_specs(
                article.content_md, config.image.inline_images
            )
            if image_specs:
                inline_paths = image_generator.generate_inline(
                    [spec["prompt"] for spec in image_specs], article_dir
                )
            progress(84, f"已生成 {len(inline_paths)} 张章节语义配图")

        content_md = _insert_inline_images(
            article.content_md, image_specs, inline_paths
        )
        formatter = MarkdownFormatter()
        content_html = formatter.convert(content_md)

        now = datetime.now().isoformat(timespec="seconds")
        metadata = {
            "id": article_id,
            "task_id": task_id,
            "owner_user_id": (owner_user or {}).get("id", ""),
            "owner_username": (owner_user or {}).get("username", ""),
            "account_name": config.account_name,
            "topic": request.topic.model_dump(),
            "title": article.title,
            "title_candidates": article.meta.get("title_candidates", [article.title]),
            "digest": article.digest,
            "outline": article.outline,
            "word_count": article.word_count,
            "style": request.options.style,
            "fact_check": article.meta.get("fact_check", {}),
            "evidence": {
                "sources": evidence.sources,
                "warnings": evidence.warnings,
                "character_count": len(evidence.text),
                "grade": evidence.grade,
                "substantive_source_count": evidence.substantive_source_count,
                "official_source_count": evidence.official_source_count,
                "institution_topic": evidence.institution_topic,
            },
            "status": "generated",
            "draft_media_id": "",
            "created_at": now,
            "updated_at": now,
            "version": 1,
            "ai_disclosure": "本文由 AI 辅助创作，并由人工审核确认后发布。",
            "assets": ["cover.png"] + [p.name for p in inline_paths],
        }
        (article_dir / "article.md").write_text(content_md, encoding="utf-8")
        (article_dir / "article.html").write_text(content_html, encoding="utf-8")
        (article_dir / "evidence.txt").write_text(evidence.text, encoding="utf-8")
        self._write_json(article_dir / "metadata.json", metadata)
        progress(94, "文章已排版并保存到本地仓库")
        return {"article_id": article_id, "title": article.title}

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for path in self.root.iterdir():
            meta_path = path / "metadata.json"
            if path.is_dir() and meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    items.append(self._summary(meta))
                except (OSError, json.JSONDecodeError):
                    continue
        items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return items[:limit]

    def metadata(self, article_id: str) -> Dict[str, Any]:
        path = self._dir(article_id)
        return json.loads((path / "metadata.json").read_text(encoding="utf-8"))

    def assign_legacy_owner(self, user_id: str, username: str) -> int:
        changed = 0
        for item in self.list(limit=100000):
            meta = self.metadata(item["id"])
            if not meta.get("owner_user_id"):
                meta["owner_user_id"] = user_id; meta["owner_username"] = username
                self._write_json(self._dir(item["id"]) / "metadata.json", meta); changed += 1
        return changed

    def reassign_owner(self, from_user_id: str, to_user_id: str, to_username: str = "") -> int:
        """把某用户名下全部文章转移给新属主（删除用户时调用）。"""
        changed = 0
        for item in self.list(limit=100000):
            meta = self.metadata(item["id"])
            if meta.get("owner_user_id") != from_user_id:
                continue
            meta["owner_user_id"] = to_user_id
            meta["owner_username"] = to_username or meta.get("owner_username", "")
            meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._write_json(self._dir(item["id"]) / "metadata.json", meta)
            changed += 1
        return changed

    def get(self, article_id: str) -> Dict[str, Any]:
        path = self._dir(article_id)
        meta_path = path / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        clean_title = _clean_title(meta.get("title"), "未命名文章")
        candidates = [
            _clean_title(item, clean_title)
            for item in meta.get("title_candidates", [clean_title])
        ]
        if meta.get("title") != clean_title or meta.get("title_candidates") != candidates:
            meta["title"] = clean_title
            meta["title_candidates"] = list(dict.fromkeys(candidates))
            self._write_json(meta_path, meta)
        raw_md = (path / "article.md").read_text(encoding="utf-8")
        from .writer import _prepare_publish_markdown, _static_quality

        content_md = _prepare_publish_markdown(raw_md)
        content_html = MarkdownFormatter().convert(content_md)
        if content_md != raw_md:
            (path / "article.md").write_text(content_md, encoding="utf-8")
        html_path = path / "article.html"
        existing_html = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
        if content_html != existing_html:
            # 格式器升级后在首次打开文章时自动刷新 HTML，避免旧样式继续进入剪贴板或微信草稿。
            html_path.write_text(content_html, encoding="utf-8")
        preview_html = self._preview_html(article_id, content_html, meta.get("assets", []))
        quality = _static_quality(content_md)
        style_hints = []
        if not quality["opening_concrete"]:
            style_hints.append("开头 120 字缺少具体事实或数字，建议补入来源明确的关键信息")
        if quality["thin_paragraph_count"]:
            style_hints.append(f"有 {quality['thin_paragraph_count']} 个段落信息量不足，建议补充数据、来源或删除")
        if quality["cliche_count"]:
            style_hints.append(f"检测到 {quality['cliche_count']} 处 AIGC 套话，建议改写为具体事实或机制解释")
        if quality["slogan_count"]:
            style_hints.append("结尾包含口号式表达，建议改为具体结论和观察指标")
        if not quality["closing_unknown_variable"]:
            style_hints.append("结尾尚未交代未知变量")
        if not quality["closing_observation_indicator"]:
            style_hints.append("结尾尚未给出后续观察指标")
        for warning in quality.get("warnings", []):
            if warning not in style_hints:
                style_hints.append(warning)
        if style_hints:
            meta["style_check"] = {"passed": False, "hints": style_hints}
        else:
            meta.pop("style_check", None)
        return {
            **meta,
            "style_quality": quality,
            "content_md": content_md,
            "content_html": content_html,
            "preview_html": preview_html,
            "cover_url": f"/api/articles/{article_id}/assets/cover.png",
        }

    def update(self, article_id: str, title: str, digest: str, content_md: str) -> Dict[str, Any]:
        path = self._dir(article_id)
        meta_path = path / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        versions = path / "versions"
        versions.mkdir(exist_ok=True)
        current_version = int(meta.get("version", 1))
        shutil.copy2(path / "article.md", versions / f"v{current_version}.md")
        self._write_json(versions / f"v{current_version}.json", meta)

        title = _clean_title(title, "未命名文章")
        from .writer import _prepare_publish_markdown

        content_md = _prepare_publish_markdown(content_md)
        content_html = MarkdownFormatter().convert(content_md)
        meta.update(
            title=title,
            digest=digest[:120],
            word_count=len(re.sub(r"\s", "", content_md)),
            version=current_version + 1,
            updated_at=datetime.now().isoformat(timespec="seconds"),
        )
        fact_check = meta.get("fact_check") or {}
        if fact_check:
            fact_check["status"] = "stale"
            fact_check["issues"] = list(fact_check.get("issues") or []) + ["人工编辑后自动核验已失效，发布前必须重新核验。"]
            meta["fact_check"] = fact_check
        (path / "article.md").write_text(content_md, encoding="utf-8")
        (path / "article.html").write_text(content_html, encoding="utf-8")
        self._write_json(meta_path, meta)
        return self.get(article_id)

    def delete(self, article_id: str) -> Dict[str, Any]:
        """删除本地文章目录；已推送到微信的远端草稿不会被删除。"""
        path = self._dir(article_id)
        meta_path = path / "metadata.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {"id": article_id, "title": "", "status": "generated"}

        tombstone = self.root / f".deleting-{article_id}-{uuid.uuid4().hex[:8]}"
        path.rename(tombstone)
        try:
            shutil.rmtree(tombstone)
        except OSError:
            if tombstone.exists() and not path.exists():
                tombstone.rename(path)
            raise
        return {
            "article_id": article_id,
            "title": meta.get("title", ""),
            "remote_draft_preserved": bool(meta.get("draft_media_id")),
        }

    def versions(self, article_id: str) -> List[Dict[str, Any]]:
        path = self._dir(article_id) / "versions"
        if not path.exists():
            return []
        result = []
        for meta_path in sorted(path.glob("v*.json"), reverse=True):
            try:
                result.append(json.loads(meta_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        return result

    def asset(self, article_id: str, name: str) -> Path:
        path = self._dir(article_id)
        safe_name = Path(name).name
        if safe_name != name or not re.fullmatch(r"[\w.-]+", safe_name):
            raise ArticleNotFoundError(name)
        asset_path = path / safe_name
        if not asset_path.is_file() or asset_path.suffix.lower() not in {
            ".png", ".jpg", ".jpeg", ".webp"
        }:
            raise ArticleNotFoundError(name)
        return asset_path

    def push_draft(
        self,
        article_id: str,
        config: Config,
        progress: Callable[[int, str], None],
    ) -> Dict[str, Any]:
        path = self._dir(article_id)
        data = self.get(article_id)
        fact_check = data.get("fact_check") or {}
        if not fact_check or fact_check.get("status") in {"failed", "stale", ""} or fact_check.get("risk_level") == "high":
            raise ValueError(
                f"事实核验状态为 {fact_check.get('status') or '未核验'}/{fact_check.get('risk_level') or 'unknown'}，"
                "不能推送公众号草稿；请重新生成或完成人工复核并重新核验"
            )
        if not config.wechat.app_id or not config.wechat.app_secret:
            raise ValueError("公众号 AppID/AppSecret 尚未配置")

        client = WechatClient(config)
        progress(10, "正在验证公众号凭证")
        _ = client.access_token
        progress(25, "正在上传封面素材")
        thumb_media_id = client.upload_material_image(path / "cover.png")

        final_html = data["content_html"]
        for asset_name in data.get("assets", []):
            if asset_name == "cover.png":
                continue
            asset_path = path / asset_name
            if asset_path.exists():
                progress(35, f"正在上传正文图片 {asset_name}")
                image_url = client.upload_img(asset_path)
                final_html = final_html.replace(asset_name, image_url)

        progress(72, "正在创建微信公众号草稿")
        media_id = client.add_draft(
            title=data["title"],
            digest=data["digest"],
            content_html=final_html,
            thumb_media_id=thumb_media_id,
        )
        meta_path = path / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.update(
            status="draft_created",
            draft_media_id=media_id,
            pushed_at=datetime.now().isoformat(timespec="seconds"),
            updated_at=datetime.now().isoformat(timespec="seconds"),
        )
        self._write_json(meta_path, meta)

        topic = meta.get("topic", {})
        history_id = self.history.record(
            topic=topic.get("title", data["title"]),
            source=topic.get("source", "web"),
            score=float(topic.get("score", 0)),
            article_title=data["title"],
            draft_media_id=media_id,
            status="draft_created",
            output_dir=str(path),
        )
        progress(95, "草稿创建成功，等待人工审核发表")
        return {"article_id": article_id, "media_id": media_id, "history_id": history_id}

    @staticmethod
    def _summary(meta: Dict[str, Any]) -> Dict[str, Any]:
        summary = {
            key: meta.get(key)
            for key in (
                "id", "title", "digest", "status", "word_count", "created_at",
                "updated_at", "draft_media_id", "version", "topic", "fact_check",
                "owner_user_id", "owner_username",
            )
        }
        summary["title"] = _clean_title(summary.get("title"), "未命名文章")
        return summary

    @staticmethod
    def _preview_html(article_id: str, html_text: str, assets: List[str]) -> str:
        result = html_text
        for name in assets:
            if name != "cover.png":
                result = result.replace(name, f"/api/articles/{article_id}/assets/{name}")
        return result

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
