import requests

from wechat_agent.hot_topics import HotTopicFetcher


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_weibo_adds_required_referer_and_parses_topics(monkeypatch):
    calls = []
    fetcher = HotTopicFetcher()

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            {
                "ok": 1,
                "data": {
                    "realtime": [
                        {"word": "人工智能 新进展", "num": 123456, "label_name": "热"}
                    ]
                },
            }
        )

    monkeypatch.setattr(fetcher._session, "get", fake_get)
    topics = fetcher.fetch_weibo()
    assert len(topics) == 1
    assert topics[0].title == "人工智能 新进展"
    assert topics[0].heat == 123456
    assert "%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD" in topics[0].url
    assert calls[0][1]["headers"]["Referer"] == "https://weibo.com/hot/search"


def test_zhihu_current_nested_schema_is_supported(monkeypatch):
    fetcher = HotTopicFetcher()
    payload = {
        "data": [
            {
                "target": {
                    "title_area": {"text": "知乎新版热榜问题"},
                    "metrics_area": {"text": "766 万热度"},
                    "excerpt_area": {"text": "问题摘要"},
                    "link": {"url": "https://www.zhihu.com/question/123"},
                }
            }
        ]
    }
    monkeypatch.setattr(fetcher._session, "get", lambda *args, **kwargs: FakeResponse(payload))
    topics = fetcher.fetch_zhihu()
    assert len(topics) == 1
    assert topics[0].title == "知乎新版热榜问题"
    assert topics[0].heat == 7_660_000
    assert topics[0].url == "https://www.zhihu.com/question/123"
    assert topics[0].extra["content"] == "问题摘要"


def test_hotspot_get_retries_transient_network_errors(monkeypatch):
    fetcher = HotTopicFetcher()
    calls = []

    def flaky_get(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise requests.ConnectTimeout("temporary")
        return FakeResponse({"data": []})

    monkeypatch.setattr(fetcher._session, "get", flaky_get)
    monkeypatch.setattr("wechat_agent.hot_topics.time.sleep", lambda seconds: None)
    assert fetcher._get("https://example.test/hot").json() == {"data": []}
    assert len(calls) == 3


def test_cls_uses_next_cache_endpoint_and_detail_url(monkeypatch):
    fetcher = HotTopicFetcher()
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(
            {
                "errno": 0,
                "data": {
                    "roll_data": [
                        {
                            "id": 2476701,
                            "title": "财联社测试电报",
                            "brief": "【财联社测试】这是电报摘要。",
                            "reading_num": 52000,
                        }
                    ]
                },
            }
        )

    monkeypatch.setattr(fetcher._session, "get", fake_get)
    topics = fetcher.fetch_cls()
    assert calls == ["https://www.cls.cn/api/cache?name=telegraph"]
    assert len(topics) == 1
    assert topics[0].title == "财联社测试电报"
    assert topics[0].heat == 52000
    assert topics[0].url == "https://www.cls.cn/detail/2476701"
    assert "电报摘要" in topics[0].extra["content"]


def test_bilibili_falls_back_when_ranking_is_risk_controlled(monkeypatch):
    fetcher = HotTopicFetcher()
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "ranking/v2" in url:
            return FakeResponse({"code": -352, "message": "risk control", "data": None})
        return FakeResponse(
            {
                "code": 0,
                "message": "OK",
                "data": {
                    "list": [
                        {
                            "title": "B站热门视频",
                            "bvid": "BV1TEST",
                            "stat": {"view": 987654},
                        }
                    ]
                },
            }
        )

    monkeypatch.setattr(fetcher._session, "get", fake_get)
    topics = fetcher.fetch_bilibili()
    assert len(calls) == 2
    assert "ranking/v2" in calls[0]
    assert "popular?ps=50&pn=1" in calls[1]
    assert len(topics) == 1
    assert topics[0].title == "B站热门视频"
    assert topics[0].heat == 987654
    assert topics[0].url == "https://www.bilibili.com/video/BV1TEST"
