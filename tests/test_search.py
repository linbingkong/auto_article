import pytest

from wechat_agent.config import SearchConfig
from wechat_agent.search import SearchError, TavilySearchService


class Response:
    status_code = 200
    reason = "OK"
    text = ""

    def json(self):
        return {
            "request_id": "req-1",
            "response_time": 0.2,
            "results": [
                {"title": "AI 新闻", "url": "https://example.com/news?utm_source=x&id=1", "content": "新闻摘要", "score": 0.86, "published_date": "2026-08-27"},
                {"title": "重复结果", "url": "https://example.com/news?id=1&utm_medium=y", "content": "重复", "score": 0.7},
            ],
        }


def test_tavily_search_normalizes_results_and_time_range(monkeypatch):
    service = TavilySearchService(SearchConfig(api_key="tvly-test"))
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(service._session, "post", post)
    result = service.search("AI 医疗", topic="news", time_range="7d", limit=10)
    assert calls[0][0] == "https://api.tavily.com/search"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer tvly-test"
    assert calls[0][1]["json"]["days"] == 7
    assert calls[0][1]["json"]["time_range"] == "week"
    assert len(result["items"]) == 1
    assert result["items"][0]["url"] == "https://example.com/news?id=1"
    assert result["items"][0]["score"] == 86.0
    assert result["items"][0]["source"] == "web_search"


def test_tavily_search_prefers_chinese_results(monkeypatch):
    class MixedResponse:
        status_code = 200
        reason = "OK"
        text = ""

        def json(self):
            return {
                "results": [
                    {"title": "English AI update", "url": "https://en.example.com/news", "content": "This is an English article about artificial intelligence.", "score": 0.95},
                    {"title": "人工智能新进展", "url": "https://zh.example.com/news", "content": "这是一条中文新闻，讨论大模型的最新进展与应用。", "score": 0.7},
                    {"title": "AI行业动态", "url": "https://zh2.example.com/news", "content": "国内科技企业发布新模型，市场反应积极。", "score": 0.6},
                ]
            }

    service = TavilySearchService(SearchConfig(api_key="tvly-test"))
    monkeypatch.setattr(service._session, "post", lambda *args, **kwargs: MixedResponse())
    result = service.search("人工智能", topic="news", time_range="7d", limit=5)
    assert [item["language"] for item in result["items"]] == ["zh", "zh", "other"]
    assert result["items"][0]["rank"] == 1
    assert result["prefer_chinese"] is True
    # 关闭中文优先时保持 Tavily 原始分数顺序
    result = service.search("人工智能", topic="news", time_range="7d", limit=5, prefer_chinese=False)
    assert [item["language"] for item in result["items"]] == ["other", "zh", "zh"]


def test_tavily_search_requires_key():
    with pytest.raises(SearchError, match="API Key 未配置"):
        TavilySearchService(SearchConfig()).search("人工智能")
