import json
import uuid
from pathlib import Path

import pytest

from wechat_agent.article_store import ArticleStore
from wechat_agent.config import Config
from wechat_agent.evidence import EvidenceCollector
from wechat_agent.formatter import MarkdownFormatter
from wechat_agent.history import HistoryStore

ROOT = Path(__file__).resolve().parents[1] / "output" / "_quality_gate_test"


def _make_article(fact_check: dict) -> tuple:
    root = ROOT / uuid.uuid4().hex
    article_dir = root / ("a" * 32)
    article_dir.mkdir(parents=True)
    (article_dir / "article.md").write_text("## 正文\n\n官方公告确认内容。", encoding="utf-8")
    (article_dir / "article.html").write_text("<p>官方公告确认内容。</p>", encoding="utf-8")
    (article_dir / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\nstub")
    meta = {
        "id": "a" * 32, "title": "测试文章", "digest": "摘要", "status": "generated",
        "word_count": 9, "version": 1, "assets": ["cover.png"],
        "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        "topic": {"title": "话题", "source": "manual", "score": 80},
        "fact_check": fact_check,
    }
    (article_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    store = ArticleStore(root, HistoryStore(root / "history.db"))
    return store, article_dir


@pytest.mark.parametrize("fact_check", [
    {"status": "failed", "risk_level": "high"},
    {"status": "completed", "risk_level": "high"},
    {"status": "stale", "risk_level": "low"},
    {},
])
def test_push_draft_blocked_without_valid_fact_check(fact_check):
    store, _ = _make_article(fact_check)
    with pytest.raises(ValueError, match="事实核验"):
        store.push_draft("a" * 32, Config(), lambda p, m: None)


def test_manual_edit_marks_fact_check_stale():
    store, article_dir = _make_article({"status": "completed", "risk_level": "low"})
    store.update("a" * 32, "新标题", "新摘要", "## 新正文\n\n人工修改后的内容。")
    meta = json.loads((article_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["fact_check"]["status"] == "stale"
    assert any("重新核验" in issue for issue in meta["fact_check"]["issues"])
    assert meta["version"] == 2


def test_formatter_flushes_unclosed_code_fence():
    html = MarkdownFormatter().convert("## 开头\n\n正常段落。\n\n```python\nprint('未闭合')")
    assert "正常段落" in html
    assert "print" in html


def test_institution_topic_requires_official_source_and_dedupes(monkeypatch):
    topic = type("T", (), {"title": "新补贴办法如何执行", "summary": "新补贴制度出台", "source": "weibo", "url": "https://s.weibo.com/x"})()
    collector = EvidenceCollector(search_config=None)
    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}
        url = "https://s.weibo.com/x"
        def raise_for_status(self): pass
        def iter_content(self, size): return [b"<p>" + "微博传闻内容，仍在扩散传播。" .encode() * 5 + b"</p>"]
    monkeypatch.setattr(collector.session, "get", lambda *a, **k: FakeResponse())
    pack = collector.collect(topic)
    assert pack.institution_topic is True
    assert pack.official_source_count == 0
    assert pack.grade == "weak"
    assert any("没有找到政府" in w for w in pack.warnings)
    urls = [s["url"] for s in pack.sources if s["url"]]
    assert len(urls) == len(set(urls))


def test_reference_links_fetched_and_typed(monkeypatch):
    topic = type("T", (), {"title": "某市发布人工智能扶持措施", "summary": "官方发布新政策", "source": "manual", "url": ""})()
    collector = EvidenceCollector(search_config=None)
    pages = {
        "https://www.gov.cn/policy": "市政府印发《人工智能扶持措施》，自2026年9月1日起施行，适用于本市科技企业。" * 3,
        "https://www.thepaper.cn/news": "澎湃新闻报道，措施包括资金补贴与人才引进，记者向市科技局核实。" * 3,
    }
    collector._fetch_public_text = lambda url: (url, pages[url])
    reference = "原文 https://www.gov.cn/policy 报道 https://www.thepaper.cn/news 请据此核实"
    pack = collector.collect(topic, reference_material=reference)
    assert pack.official_source_count >= 1
    assert pack.substantive_source_count >= 2
    assert pack.grade in {"strong", "medium"}
    assert any(s["type"] == "news_report" for s in pack.sources)
