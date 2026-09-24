from wechat_agent.config import TopicFilterConfig
from wechat_agent.hot_topics import HotTopic
from wechat_agent.topic_selector import TopicSelector


def test_topic_selector_filters_and_scores():
    cfg = TopicFilterConfig(
        max_topics=2,
        min_rank=20,
        min_score=0,
        whitelist=["AI", "大模型"],
        blacklist=["明星"],
    )
    topics = [
        HotTopic("AI 大模型发布新能力", "weibo", rank=1, heat=1000000),
        HotTopic("某明星参加活动", "weibo", rank=2, heat=2000000),
        HotTopic("普通社会新闻", "zhihu", rank=3, heat=50000),
        HotTopic("排名太低", "toutiao", rank=30, heat=9999999),
    ]
    selected = TopicSelector(cfg).select(topics)
    titles = [x.title for x in selected]
    assert "AI 大模型发布新能力" in titles
    assert all("明星" not in x for x in titles)
    assert all(x != "排名太低" for x in titles)
    assert selected[0].matched_keywords


def test_topic_selector_deduplicates():
    cfg = TopicFilterConfig(max_topics=5, min_score=0)
    topics = [
        HotTopic("AI 芯片发布！", "weibo", rank=1, heat=1000),
        HotTopic("AI芯片发布", "zhihu", rank=2, heat=900),
    ]
    selected = TopicSelector(cfg).select(topics)
    assert len(selected) == 1
    assert any("跨平台合并" in r for r in selected[0].reasons)
