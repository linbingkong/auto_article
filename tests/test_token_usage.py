from pathlib import Path

from wechat_agent.token_usage import TokenUsageStore


def test_token_usage_store_aggregates_tasks_and_costs():
    path = Path('output/_token_usage_test/usage.db')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    store = TokenUsageStore(path)
    store.record(task_id='task-1', article_id='article-1', user_id='u1', username='admin',
                 generation_mode='platform', usage={'stage':'outline','provider':'api.test','model':'m1','prompt_tokens':1000,'completion_tokens':500,'total_tokens':1500},
                 input_price_per_million=2, output_price_per_million=8)
    store.record(task_id='task-1', article_id='article-1', user_id='u1', username='admin',
                 generation_mode='platform', usage={'stage':'section_1','provider':'api.test','model':'m1','prompt_tokens':2000,'completion_tokens':1000,'total_tokens':3000,'cached_tokens':200},
                 input_price_per_million=2, output_price_per_million=8)
    result = store.dashboard(days=30)
    assert result['summary']['generations'] == 1
    assert result['summary']['calls'] == 2
    assert result['summary']['prompt_tokens'] == 3000
    assert result['summary']['completion_tokens'] == 1500
    assert result['summary']['total_tokens'] == 4500
    assert result['summary']['estimated_cost_yuan'] == 0.018
    assert result['generations'][0]['article_id'] == 'article-1'
    assert set(result['generations'][0]['stages']) == {'outline','section_1'}
    assert len(store.task_detail('task-1')['items']) == 2


def test_token_usage_link_article_updates_failed_or_late_rows():
    path = Path('output/_token_usage_test/link.db')
    path.unlink(missing_ok=True)
    store = TokenUsageStore(path)
    store.record(task_id='task-2', article_id='', user_id='u2', username='creator', generation_mode='personal',
                 usage={'stage':'outline','model':'m2','prompt_tokens':10,'completion_tokens':5})
    store.link_article('task-2','article-2')
    assert store.task_detail('task-2')['items'][0]['article_id'] == 'article-2'
