"""Web article styles have different research and writing paths."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from wechat_agent.config import Config, SearchConfig
from wechat_agent.evidence import EvidenceCollector
from wechat_agent.llm import LLMError
from wechat_agent.web_models import GenerateOptions
from wechat_agent.writer import ArticleWriter


class CaptureLLM:
    def __init__(self):
        self.prompts = []

    def chat(self, messages, **kwargs):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        return '["一、关键节点"]' if "生成写作大纲" in prompt else "正文"


def test_web_accepts_three_styles_but_not_new_list_articles():
    assert GenerateOptions().style == "deep"
    for style in ("story", "news", "deep"):
        assert GenerateOptions(style=style).style == style
    with pytest.raises(ValidationError):
        GenerateOptions(style="list")


def test_story_and_deep_research_queries_differ_from_default():
    ordinary = EvidenceCollector._research_queries("产品发布", False)
    assert ordinary == EvidenceCollector._research_queries("产品发布", False, style="news")
    story = EvidenceCollector._research_queries("产品发布", False, style="story")
    deep = EvidenceCollector._research_queries("产品发布", False, style="deep")
    assert any("历史" in query or "时间线" in query for query in story)
    assert any("争议" in query or "各方" in query for query in deep)
    assert len(story) > len(ordinary)


def test_story_requires_sourced_past_and_current_dates(monkeypatch):
    old_date = (date.today() - timedelta(days=800)).isoformat()
    current_date = (date.today() - timedelta(days=20)).isoformat()

    class Search:
        def __init__(self, config):
            pass

        def search(self, query, **kwargs):
            if "历史" in query or "时间线" in query:
                return {"items": [{"url": "https://news.example/old", "title": "历史报道", "score": 1,
                    "raw_content": f"{old_date}，甲机构宣布启动这个项目，公布了建设目标和第一阶段的工作计划。" * 3}]}
            return {"items": [{"url": "https://news.example/new", "title": "最新报道", "score": 1,
                "raw_content": f"{current_date}，甲机构宣布新进展，提供了测试结果和下一阶段的工作安排。" * 3}]}

    monkeypatch.setattr("wechat_agent.evidence.TavilySearchService", Search)
    topic = type("Topic", (), {"title": "甲机构项目进展", "summary": "", "source": "manual", "url": ""})()
    pack = EvidenceCollector(SearchConfig(api_key="test")).collect(topic, style="story")
    assert pack.story_ready
    assert "【历史节点】" in pack.text and "【当前进展】" in pack.text
    assert "https://news.example/old" in pack.text and "https://news.example/new" in pack.text
    assert "S1" in pack.text and "S2" in pack.text


def test_story_stops_without_verifiable_history_before_writing():
    llm = CaptureLLM()
    evidence = "【证据等级】strong；实质信源 3 条；官方原文 1 条\n【S1｜新闻｜news_report】\nURL：https://example.test/1\n只有当前新闻。"
    with pytest.raises(LLMError, match="历史节点"):
        ArticleWriter(llm, Config()).write("甲机构最新消息", evidence, style="story")
    assert not llm.prompts


@pytest.mark.parametrize("style,keyword", [
    ("story", "历史节点"), ("news", "当前进展"), ("deep", "分歧"),
])
def test_mode_guides_outline_section_and_refinement(style, keyword):
    llm = CaptureLLM()
    writer = ArticleWriter(llm, Config())
    evidence = "【证据等级】strong\n【S1｜报道｜news_report】\nURL：https://example.test/1\n相关报道。"
    writer._gen_outline("项目进展", evidence, style=style)
    writer._write_section("项目进展", ["关键节点"], 0, "关键节点", evidence, style=style)
    writer._refine("## 关键节点\n\n正文", evidence, "项目进展", style=style)
    assert len(llm.prompts) == 3
    assert all(keyword in prompt for prompt in llm.prompts)


def test_deep_does_not_invent_conflict_without_evidence():
    llm = CaptureLLM()
    ArticleWriter(llm, Config())._gen_outline("项目进展", "【证据等级】strong", style="deep")
    assert "不得制造对立" in llm.prompts[0]
    assert "机制" in llm.prompts[0]


def test_cli_default_retains_original_generic_outline_prompt():
    llm = CaptureLLM()
    ArticleWriter(llm, Config())._gen_outline("项目进展", "【证据等级】strong")
    assert "请为一篇新闻解释型微信公众号文章生成写作大纲" in llm.prompts[0]
    assert "【文章模式】" not in llm.prompts[0]


def test_story_rejects_two_old_reports_as_current_progress():
    old = (date.today() - timedelta(days=900)).isoformat()
    newer_but_stale = (date.today() - timedelta(days=400)).isoformat()
    sources = [
        {"id": "S1", "type": "news_report", "label": "旧报道", "url": "https://example.org/old"},
        {"id": "S2", "type": "official", "label": "旧公告", "url": "https://example.org/later"},
    ]
    texts = {"S1": f"{old} 甲机构启动项目。", "S2": f"{newer_but_stale} 甲机构宣布下一阶段。"}
    assert not EvidenceCollector._story_timeline(sources, texts)


def test_old_list_article_remains_readable(tmp_path):
    import json
    from wechat_agent.article_store import ArticleStore

    article_id = "a" * 32
    folder = tmp_path / article_id
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({"id": article_id, "style": "list", "title": "旧清单文章", "status": "generated"}), encoding="utf-8")
    (folder / "article.md").write_text("旧正文", encoding="utf-8")
    (folder / "article.html").write_text("<p>旧正文</p>", encoding="utf-8")
    store = ArticleStore(tmp_path, None)
    assert store.get(article_id)["style"] == "list"


def test_web_generation_passes_mode_without_mixing_it_into_evidence(monkeypatch, tmp_path):
    from wechat_agent.article_store import ArticleStore
    from wechat_agent.evidence import EvidencePack
    from wechat_agent.web_models import GenerateRequest, HotspotInput
    from wechat_agent.writer import Article

    seen = {}

    class Collector:
        def __init__(self, config):
            pass

        def collect(self, topic, reference_material, style=None):
            seen["collector_style"] = style
            return EvidencePack(text="【证据等级】strong\n证据", grade="strong", story_ready=True)

    class Writer:
        def __init__(self, llm, config):
            pass

        def write(self, topic, context, progress=None, *, style=None):
            seen["writer_style"] = style
            seen["context"] = context
            return Article(title="标题", digest="摘要", content_md="## 正文\n\n事实。")

    class Images:
        def __init__(self, config):
            pass

        def generate_cover(self, title, path, subtitle):
            path.write_bytes(b"png")

    monkeypatch.setattr("wechat_agent.article_store.EvidenceCollector", Collector)
    monkeypatch.setattr("wechat_agent.article_store.ArticleWriter", Writer)
    monkeypatch.setattr("wechat_agent.article_store.ImageGenerator", Images)
    monkeypatch.setattr("wechat_agent.article_store.LLMClient", lambda *args, **kwargs: None)
    store = ArticleStore(tmp_path, None)
    request = GenerateRequest(topic=HotspotInput(title="测试话题"), options=GenerateOptions(style="deep", with_images=False))
    result = store.generate(request, Config(), lambda *args: None)
    assert seen == {"collector_style": "deep", "writer_style": "deep", "context": "【证据等级】strong\n证据"}
    assert store.get(result["article_id"])["style"] == "deep"


def test_story_ignores_dates_that_are_only_page_metadata():
    recent = (date.today() - timedelta(days=10)).isoformat()
    old = (date.today() - timedelta(days=600)).isoformat()
    sources = [
        {"id": "S1", "type": "news_report", "label": "历史报道", "url": "https://example.org/old"},
        {"id": "S2", "type": "news_report", "label": "当前报道", "url": "https://example.org/current"},
    ]
    texts = {"S1": f"发布时间：{old}，责任编辑：某某。", "S2": f"{recent}，甲机构宣布新进展。"}
    assert not EvidenceCollector._story_timeline(sources, texts)


def test_web_mode_outline_does_not_force_legacy_social_extension():
    llm = CaptureLLM()
    ArticleWriter(llm, Config())._gen_outline("产品发布", "【证据等级】strong", style="story")
    assert "最后一章必须是“延伸与讨论”" not in llm.prompts[0]
    assert "回到当前进展" in llm.prompts[0]


def test_web_form_and_api_do_not_offer_or_accept_new_list_articles():
    from fastapi.testclient import TestClient
    from wechat_agent.web_app import app

    form = (Path(__file__).resolve().parents[1] / "web" / "hotspots.js").read_text(encoding="utf-8-sig")
    assert 'id="generateStyle"' in form
    assert '<option value="list">' not in form
    client = TestClient(app, client=("article-style-validation", 50000))
    response = client.post("/api/generate", json={
        "topic": {"title": "测试话题", "source": "manual"}, "options": {"style": "list"},
    })
    assert response.status_code == 422


@pytest.mark.parametrize("evidence", [
    "【证据等级】strong\n【S1｜主管机构｜official】甲机构确认新规。\n【S2｜利益相关方｜news_report】乙方质疑执行成本。",
    "【证据等级】strong\n【S1｜主管机构｜official】甲机构确认新规。\n【S2｜行业数据｜news_report】相关数据表明成本上升。",
])
def test_deep_mode_handles_disagreement_or_mechanisms_without_inventing_opposition(evidence):
    llm = CaptureLLM()
    ArticleWriter(llm, Config())._gen_outline("新规分析", evidence, style="deep")
    assert evidence in llm.prompts[0]
    assert "没有可靠分歧证据时" in llm.prompts[0]
    assert "不得制造对立" in llm.prompts[0]
