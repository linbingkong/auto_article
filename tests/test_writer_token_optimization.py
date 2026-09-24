import json

from wechat_agent.config import Config
from wechat_agent.writer import ArticleWriter, EvidenceViews, _topic_story_framework


def _evidence(block_size=5000):
    preamble = "【编辑规则】仅依据来源。\n\n【研究主题】人工智能产业\n\n【证据等级】strong；实质信源 8 条；官方原文 1 条"
    blocks=[]
    for i in range(1,9):
        relevant = "人工智能模型于2026年9月发布，机构报告显示性能提升30%。" if i in {3,6} else "其他产业背景资料与历史情况。"
        noise = "[财经](http://a) [股票](http://b) [基金](http://c) [导航](http://d)\n"
        body = (relevant + " 该来源给出了主体、时间、数字和公开回应。\n") * 100
        blocks.append(f"【S{i}｜来源{i}｜{'official' if i==3 else 'web_source'}】\nURL：https://example.com/{i}\n{noise}{body}"[:block_size])
    return preamble + "\n\n" + "\n\n".join(blocks)


def test_topic_story_framework_selects_explanatory_track():
    assert _topic_story_framework("美联储利率变化").startswith("财经机制线")
    assert _topic_story_framework("国际关税与外交谈判").startswith("国际与历史线")
    assert _topic_story_framework("大模型芯片产业竞争").startswith("科技产业线")
    assert _topic_story_framework("员工被移出工作群").startswith("社会议题线")


def test_evidence_views_cap_size_and_preserve_provenance():
    evidence = _evidence()
    views = EvidenceViews(evidence, "人工智能模型发布")
    assert len(evidence) > 30000
    assert len(views.outline()) <= 14000
    assert len(views.section("模型性能和机构数据")) <= 8500
    assert len(views.refine()) <= 5000
    assert len(views.fact_revision()) <= 22000
    assert len(views.fact_audit()) <= 14000
    assert views.fact_revision().count("【S") == 8
    assert views.section("模型性能和机构数据").count("【S") <= 4
    assert "URL：https://example.com/3" in views.section("模型性能和机构数据")
    assert "URL：https://example.com/3" in views.fact_revision()
    assert "[财经](http://a)" not in views.outline()


class FakeLlm:
    def __init__(self):
        self.calls=[]

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        label=kwargs.get('usage_label')
        if label=='outline': return json.dumps(["已经确认的事实", "影响与边界", "延伸与讨论"])
        if label and label.startswith('section_'): return "这是有来源支持的章节正文，包含已经确认的事实与必要解释。"
        if label=='refine': return "## 已确认事实\n\n这是经过润色且有来源支持的正文内容。\n\n## 延伸与讨论\n\n这是有依据的讨论内容。"
        if label=='fact_revision': return (
            "## 已确认事实\n\n行业报告显示，人工智能模型在过去一年把基准成绩提高了 30%，头部厂商占据 63% 的市场份额。\n\n"
            "供应链数据显示，由于先进制程产能增加 20%，推理成本在 2026 年下降了 18%，这意味着应用部署速度会继续加快。\n\n"
            "多家机构预测，因为企业采购预算向头部集中，超过 400 家中小厂商必须转向行业定制方案才能维持增长。\n\n"
            "用户调研显示，78% 的企业客户最关心数据合规和私有化部署，这两项决定了采购周期的长短。\n\n"
            "平台披露，已有 12 个行业上线了定制方案，带动相关服务收入增长 24%。\n\n"
            "接下来值得观察的是第三季度资本开支是否超过 500 亿元，以及监管机构是否公布新的合规细则。")
        if label=='fact_audit': return '{"risk_level":"low","information_score":90,"issues":[],"claim_audit":[]}'
        if label=='titles': return '[{"title":"人工智能模型发布带来哪些变化","type":"悬念提问"}]'
        raise AssertionError(label)


def test_writer_uses_compact_contexts_and_tight_stage_budgets():
    llm=FakeLlm();cfg=Config();cfg.target_words=1200;cfg.llm.max_tokens=8096
    ArticleWriter(llm,cfg).write("人工智能模型发布",_evidence())
    calls={kwargs['usage_label']:(messages,kwargs) for messages,kwargs in llm.calls}
    assert calls['outline'][1]['max_tokens']==1024
    assert calls['section_1'][1]['max_tokens']==3072
    assert calls['refine'][1]['max_tokens']==4096
    assert calls['fact_revision'][1]['max_tokens']==4096
    assert calls['fact_audit'][1]['max_tokens']==3072
    assert calls['titles'][1]['max_tokens']==1024
    assert all(kwargs.get('thinking') is False for _,kwargs in llm.calls)
    assert "双线推进" in calls['outline'][0][-1]['content']
    assert all(name not in calls['outline'][0][-1]['content'] for name in ("财闻要鉴", "卢克文工作室", "云海观星社"))
    assert "前 120 字直接使用证据" in calls['section_1'][0][-1]['content']
    assert "未知变量" in calls['section_3'][0][-1]['content']
    assert "【本题叙事骨架】" in calls['refine'][0][-1]['content']
    total_prompt_chars=sum(len(m.get('content','')) for messages,_ in llm.calls for m in messages)
    assert total_prompt_chars < 130000
