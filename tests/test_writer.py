import pytest

from wechat_agent.config import Config
from wechat_agent.evidence import EvidenceCollector
from wechat_agent.writer import (
    ArticleWriter,
    _build_digest,
    _clean_title,
    _loads_loose,
    _looks_like_editorial_reasoning,
)


def test_title_dict_is_extracted_instead_of_stringified():
    value = {"风格": "悬念式", "标题": "小米玄戒O3真机上手，谜底藏在哪？"}
    assert _clean_title(value) == "小米玄戒O3真机上手，谜底藏在哪？"
    assert _clean_title(str(value)) == "小米玄戒O3真机上手，谜底藏在哪？"


def test_loose_json_accepts_fenced_python_literal():
    parsed = _loads_loose("```python\n[{'风格': '观点式', '标题': '这是一个标题'}]\n```")
    assert parsed[0]["标题"] == "这是一个标题"


def test_title_removes_markdown_prefix():
    assert _clean_title("### 标题：一个准确的标题") == "一个准确的标题"


def test_editorial_reasoning_is_rejected_and_excluded_from_digest():
    leaked = "我们需要输出标准 Markdown，不要代码围栏。分析原文结构后开始编辑："
    assert _looks_like_editorial_reasoning(leaked)
    markdown = f"![配图](inline.png)\n\n{leaked}\n\n## 正文\n\n这是文章真正的内容，用于向读者解释话题仍待核实。"
    assert _build_digest(markdown) == "这是文章真正的内容，用于向读者解释话题仍待核实。"


def test_digest_accepts_heading_followed_by_text_without_blank_lines():
    markdown = "## 已确认事实\n第一段正文直接跟在标题下一行，包含足够的信息用于生成摘要。\n## 延伸与讨论\n第二段正文同样没有空行。"
    digest = _build_digest(markdown)
    assert digest.startswith("第一段正文直接跟在标题下一行")
    assert "第二段正文" in digest


def test_digest_falls_back_for_short_lines_separated_by_blank_lines():
    markdown = "## 正文\n\n事实一。\n\n事实二。\n\n事实三。"
    assert _build_digest(markdown) == "事实一。事实二。事实三。"


def test_refine_falls_back_when_model_leaks_editorial_reasoning():
    class LeakingLLM:
        def chat(self, *args, **kwargs):
            return "分析原文结构：需要润色。我们需要输出标准 Markdown。开始编辑："

    original = "## 正文\n\n这是没有污染的原始正文。"
    assert ArticleWriter(LeakingLLM(), Config())._refine(original, "证据") == original


def test_evidence_html_extracts_article_blocks():
    html = "<html><head><style>bad</style></head><body><h1>官方公告标题</h1><p>这是可供核验的正文内容。</p><script>ignore()</script></body></html>"
    text = EvidenceCollector._extract_text(html, "text/html")
    assert "官方公告标题" in text
    assert "可供核验" in text
    assert "ignore" not in text


def test_evidence_collector_blocks_private_urls(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args: [(None, None, None, None, ("127.0.0.1", 80))],
    )
    with pytest.raises(ValueError, match="SSRF"):
        EvidenceCollector._validate_public_url("http://example.test/private")


def test_writer_passes_evidence_to_all_fact_stages_and_applies_revision():
    evidence = "【证据等级】strong；实质信源 3 条；官方原文 1 条；制度议题：否\n\n【S1｜官方公告｜official】\nURL：https://www.gov.cn/announce\n官方公告确认产品已发布。"

    class FakeLLM:
        def __init__(self):
            self.prompts = []

        def chat(self, messages, **kwargs):
            prompt = messages[-1]["content"]
            self.prompts.append(prompt)
            if "生成写作大纲" in prompt:
                return '["一、已确认信息"]'
            if "发布前事实核验编辑" in prompt:
                return (
                    "## 一、已确认信息\n\n官方公告 3 月 4 日确认产品已经正式发布。\n\n"
                    "公告显示，发布时间比原计划提前了 2 周，因为产线在 1 月已经完成爬坡。\n\n"
                    "公司发言人称，由于上游零部件价格下降 8%，定价得以低于上一代产品。\n\n"
                    "行业数据显示，同类产品过去一年销量增长 25%，这意味着价格竞争还会持续。\n\n"
                    "接下来值得观察的是第二季度产能利用率，以及发改委是否公布新的能效标准。"
                )
            if "审计修订后的文章" in prompt:
                return '{"risk_level":"medium","information_score":80,"issues":["已删除无来源销量"],"claim_audit":[{"claim":"产品已发布","type":"fact","supporting_source_ids":["S1"],"status":"supported"}]}'
            if "生成 8 个候选标题" in prompt:
                return "[{'type':'结果前置','标题':'官方公告确认产品已经正式发布'}]"
            if "请编辑下面" in prompt:
                return (
                    "## 一、已确认信息\n\n官方公告 3 月 4 日确认产品已经正式发布，首批销量达到 100 万台。\n\n"
                    "公告显示，这次发布比原计划提前了 2 周，因为产线在 1 月已经完成爬坡。\n\n"
                    "公司发言人称，由于上游零部件价格下降 8%，定价得以低于上一代产品。\n\n"
                    "行业数据显示，同类产品过去一年销量增长 25%，这意味着价格竞争还会持续。\n\n"
                    "接下来值得观察的是第二季度产能利用率，以及发改委是否公布新的能效标准。"
                )
            return "官方公告确认产品已发布，销量达到100万。"

    llm = FakeLLM()
    article = ArticleWriter(llm, Config()).write("产品发布", evidence)
    assert "100万" not in article.content_md
    assert article.title == "官方公告确认产品已经正式发布"
    assert article.meta["fact_check"]["risk_level"] == "medium"
    assert "延伸与讨论" in article.outline[-1]
    # 大纲、正文、润色、事实核验都必须看到同一份证据；标题阶段仅使用核验后的摘要。
    assert all(evidence in prompt for prompt in llm.prompts[:4])
    assert any("数字钩子" in prompt and "归因不丢" in prompt for prompt in llm.prompts)
    assert any("这是孤立个案还是同类现象" in prompt and "价值观和社会发展" in prompt for prompt in llm.prompts)


def test_title_lint_filters_clickbait_and_rumor_escalation():
    from wechat_agent.writer import _lint_titles

    topic = "网传某公司与供应商发生纠纷"
    titles = _lint_titles(
        [
            "震惊！某公司竟然这样对待供应商",
            "某公司确认与供应商彻底决裂",
            "网传某公司与供应商发生纠纷？双方说法不一",
            "某公司被指与供应商发生纠纷，合作缘何生变",
        ],
        topic,
    )
    assert "震惊！某公司竟然这样对待供应商" not in titles
    assert "某公司确认与供应商彻底决裂" not in titles
    assert any("网传" in t or "？" in t or t.rstrip().endswith(("吗", "呢")) for t in titles)
    assert "某公司被指与供应商发生纠纷，合作缘何生变" in titles


def test_title_type_balance_limits_same_type_to_two():
    from wechat_agent.writer import _TITLE_TYPES

    assert len(_TITLE_TYPES) == 7
    assert {"时间纵深", "机制解释"}.issubset(_TITLE_TYPES)
    items = [
        {"type": "数字钩子", "标题": "标题一"},
        {"type": "数字钩子", "标题": "标题二"},
        {"type": "数字钩子", "标题": "标题三"},
        {"type": "悬念提问", "标题": "标题四"},
    ]
    typed = {}
    kept = []
    for item in items:
        kind = item.get("type", "")
        if typed.get(kind, 0) >= 2:
            continue
        typed[kind] = typed.get(kind, 0) + 1
        kept.append(item["标题"])
    assert kept == ["标题一", "标题二", "标题四"]


def test_writer_blocks_generation_without_substantive_sources():
    class ShouldNotBeCalled:
        def chat(self, *args, **kwargs):
            raise AssertionError("弱证据下不得调用写作模型")

    with pytest.raises(Exception, match="实质信源不足"):
        ArticleWriter(ShouldNotBeCalled(), Config()).write("热点传闻", "【证据等级】weak")


def test_citation_markers_are_stripped_from_body_and_digest():
    from wechat_agent.writer import _strip_citation_markers

    body = "苹果将于 9 月 10 日举行发布会【S1】【S3】【S5】。市场预期推出折叠屏 iPhone【S3、S5】。"
    cleaned = _strip_citation_markers(body)
    assert "【S" not in cleaned
    assert cleaned.endswith("。")
    digest = _build_digest(cleaned)
    assert "【S" not in digest
    assert "苹果将于 9 月 10 日举行发布会" in digest


def test_publish_markdown_removes_reference_section_and_links():
    from wechat_agent.writer import _prepare_publish_markdown

    original = (
        "## 正文\n\n据苹果官网公告，发布会将在 9 月 10 日举行。详情见"
        "[苹果官网](https://apple.com/newsroom)。\n\n"
        "## 主要信源\n\n- 苹果官网：https://apple.com/newsroom\n- 媒体报道：https://example.com/story"
    )
    cleaned = _prepare_publish_markdown(original)
    assert "## 主要信源" not in cleaned
    assert "https://" not in cleaned
    assert "苹果官网公告" in cleaned
    assert "详情见苹果官网" in cleaned


def test_writer_output_has_no_citation_markers():
    evidence = "【证据等级】strong；实质信源 2 条\n\n【S1｜官方公告｜official】\nURL：https://www.gov.cn/a\n官方公告确认产品已发布。"

    class MarkerLLM:
        def chat(self, messages, **kwargs):
            prompt = messages[-1]["content"]
            if "生成写作大纲" in prompt:
                return '["一、已确认信息"]'
            if "发布前事实核验编辑" in prompt:
                return (
                    "## 一、已确认信息\n\n官方公告 3 月 4 日确认产品已经正式发布【S1】。\n\n"
                    "公告显示，发布比原计划提前了 2 周，因为产线在 1 月已经完成爬坡【S1】。\n\n"
                    "公司发言人称，由于上游零部件价格下降 8%，定价得以低于上一代产品【S1】。\n\n"
                    "行业数据显示，同类产品过去一年销量增长 25%，这意味着价格竞争还会持续【S1】。\n\n"
                    "接下来值得观察的是第二季度产能利用率，以及发改委是否公布新的能效标准【S1】。\n\n"
                    "## 主要信源\n\n- [官方公告](https://www.gov.cn/a)"
                )
            if "审计修订后的文章" in prompt:
                return '{"risk_level":"low","information_score":80,"issues":[],"low_information_paragraphs":[],"claim_audit":[{"claim":"产品已发布","type":"fact","supporting_source_ids":["S1"],"status":"supported"}]}'
            if "生成 8 个候选标题" in prompt:
                return "[{'type':'结果前置','标题':'官方公告确认产品已经正式发布'}]"
            if "请编辑下面" in prompt:
                return "## 一、已确认信息\n\n官方公告确认产品已发布。"
            return "官方公告确认产品已发布。"

    article = ArticleWriter(MarkerLLM(), Config()).write("产品发布", evidence)
    assert "【S1】" not in article.content_md
    assert "【S" not in article.digest
    assert "主要信源" not in article.content_md
    assert "https://" not in article.content_md
    assert "官方公告 3 月 4 日确认产品已经正式发布。" in article.content_md


def test_static_quality_flags_thin_and_cliche_paragraphs():
    from wechat_agent.writer import _static_quality

    thin_md = "在当今时代，这个话题引发了广泛关注。\n\n值得注意的是，各方看法见仁见智。\n\n综上所述，未来可期。"
    metrics = _static_quality(thin_md)
    assert metrics["passed"] is False
    assert metrics["thin_paragraph_count"] >= 2
    assert metrics["cliche_count"] >= 3

    solid_md = (
        "该公司 3 月 4 日发布公告称，季度营收为 12.4 亿元，同比增长 18%。\n\n"
        "财报显示，增长主要来自海外订单：欧洲客户贡献了 4.1 亿元，因为两条新产线在 2024 年 11 月投产。\n\n"
        "管理层在电话会上表示，由于原材料成本下降 6%，毛利率回升到 21%。\n\n"
        "不过，行业报告提醒，国内需求仍处于低位，库存周转天数比去年多 9 天。\n\n"
        "发改委 4 月发布的新办法要求企业在 9 月前完成备案，否则不能继续出口。\n\n"
        "机构数据显示，头部五家公司占据了 63% 的份额，这意味着中小厂商必须转向细分市场。\n\n"
        "截至 5 月，已有 3 家企业宣布提价，但下游客户尚未接受。\n\n"
        "接下来值得观察的是 6 月的订单数据，以及发改委是否会公布第二批名单。"
    )
    good = _static_quality(solid_md)
    assert good["passed"] is True, good
    assert good["hard_failed"] == []
    assert good["thin_paragraph_count"] == 0
    assert good["mechanism_paragraph_ratio"] >= 0.3
    assert good["distinct_number_count"] >= 8

    short_but_real = (
        "官方公告 3 月 4 日确认产品已经正式发布。\n\n"
        "公告显示，发布比原计划提前了 2 周，因为产线在 1 月已经完成爬坡。\n\n"
        "发言人称，由于零部件价格下降 8%，定价低于上一代。"
    )
    borderline = _static_quality(short_but_real)
    assert borderline["hard_failed"] == [], borderline
    assert any("篇幅偏短" in item for item in borderline["warnings"])
    good = _static_quality(solid_md)
    assert good["passed"] is True
    assert good["thin_paragraph_count"] == 0
    assert good["mechanism_paragraph_ratio"] >= 0.3
    assert good["distinct_number_count"] >= 8


def test_writer_blocks_fact_check_failure_instead_of_saving():
    evidence = "【证据等级】strong；实质信源 2 条\n\n【S1｜官方公告｜official】\nURL：https://www.gov.cn/a\n官方公告确认产品已发布。"

    class FakeLLM:
        def chat(self, messages, **kwargs):
            prompt = messages[-1]["content"]
            if "生成写作大纲" in prompt:
                return '["一、已确认信息"]'
            if "请编辑下面" in prompt:
                return "## 一、已确认信息\n\n官方公告确认产品已发布。"
            if "发布前事实核验编辑" in prompt:
                raise RuntimeError("LLM timeout")
            if "审计修订后的文章" in prompt:
                return '{"risk_level":"low","information_score":75,"issues":[],"low_information_paragraphs":[],"claim_audit":[]}'
            return "官方公告确认产品已发布。"

    with pytest.raises(Exception, match="事实核验未完成"):
        ArticleWriter(FakeLLM(), Config()).write("产品发布", evidence)


def test_quality_repair_rescues_thin_revision_instead_of_failing():
    thin = (
        "## 已确认信息\n\n在当今时代，这个话题引发了广泛关注。\n\n"
        "值得注意的是，各方看法见仁见智。\n\n综上所述，未来可期。"
    )
    rich = (
        "## 已确认信息\n\n官方公告 3 月 4 日确认产品已经正式发布，首批出货 12 万台。\n\n"
        "公告显示，发布比原计划提前了 2 周，因为产线在 1 月完成爬坡。\n\n"
        "公司发言人称，由于上游零部件价格下降 8%，定价低于上一代产品。\n\n"
        "行业数据显示，同类产品过去一年销量增长 25%，这意味着价格竞争还会持续。\n\n"
        "机构报告提醒，渠道库存周转天数多了 9 天，需求仍未回到 2023 年水平。\n\n"
        "发改委 4 月发布的新办法要求企业在 9 月前完成备案，否则不能继续出口。\n\n"
        "接下来值得观察的是第二季度产能利用率，以及发改委是否公布第二批名单。"
    )

    class RepairingLLM:
        def __init__(self):
            self.labels = []

        def chat(self, messages, **kwargs):
            label = kwargs.get("usage_label")
            self.labels.append(label)
            if label == "fact_revision":
                return thin
            if label == "quality_repair":
                return rich
            return '{"risk_level":"medium","information_score":40,"issues":[],"low_information_paragraphs":[],"claim_audit":[]}'

    llm = RepairingLLM()
    revised, report = ArticleWriter(llm, Config())._fact_check(thin, "【S1】官方公告确认产品已经正式发布。")
    assert "quality_repair" in llm.labels
    assert revised == rich
    assert report["quality_metrics"]["hard_failed"] == []
    assert report["risk_level"] != "high"


def test_quality_residue_is_marked_high_risk_instead_of_blocking_save():
    thin = (
        "## 已确认信息\n\n在当今时代，这个话题引发了广泛关注。\n\n"
        "值得注意的是，各方看法见仁见智。\n\n综上所述，未来可期。"
    )

    class StuckLLM:
        def chat(self, messages, **kwargs):
            if kwargs.get("usage_label") == "fact_revision":
                return thin
            if kwargs.get("usage_label") == "quality_repair":
                return thin
            return '{"risk_level":"medium","information_score":30,"issues":[],"low_information_paragraphs":["第 1 段空洞"],"claim_audit":[{"claim":"未来可期","type":"interpretation","supporting_source_ids":[],"status":"unsupported"}]}'

    revised, report = ArticleWriter(StuckLLM(), Config())._fact_check(thin, "【S1】官方公告确认产品已经正式发布。")
    assert revised.strip()
    assert report["status"] == "completed"
    assert report["risk_level"] == "high"
    assert report["unsupported_claims"] >= 1
    assert any("低创作度风险" in item or "无法溯源断言" in item for item in report["issues"])


def test_fact_check_falls_back_when_revision_contains_only_headings():
    class HeadingOnlyRevision:
        def chat(self, messages, **kwargs):
            if kwargs.get("usage_label") == "fact_revision":
                return "## 已确认信息\n## 延伸与讨论"
            return '{"risk_level":"low","information_score":90,"issues":[],"low_information_paragraphs":[],"claim_audit":[]}'

    original = (
        "## 已确认信息\n\n官方公告 3 月 4 日确认产品已经正式发布。\n\n"
        "公告显示，发布比原计划提前了 2 周，因为产线在 1 月已经完成爬坡。\n\n"
        "公司发言人称，由于上游零部件价格下降 8%，定价得以低于上一代产品。\n\n"
        "行业数据显示，同类产品过去一年销量增长 25%，这意味着价格竞争还会持续。\n\n"
        "接下来值得观察的是第二季度产能利用率，以及发改委是否公布新的能效标准。"
    )
    revised, report = ArticleWriter(HeadingOnlyRevision(), Config())._fact_check(original, "【S1】官方公告确认产品已经正式发布。")
    assert revised == original
    assert report["status"] == "completed"


def test_writer_blocks_empty_fact_check_revision():
    evidence = "【证据等级】strong；实质信源 2 条\n\n【S1｜官方公告｜official】\nURL：https://www.gov.cn/a\n官方公告确认产品已发布。"

    class FakeLLM:
        def chat(self, messages, **kwargs):
            prompt = messages[-1]["content"]
            if "生成写作大纲" in prompt:
                return '["一、已确认信息"]'
            if "请编辑下面" in prompt:
                return "## 一、已确认信息\n\n官方公告确认产品已发布。"
            if "发布前事实核验编辑" in prompt:
                return ""
            return "官方公告确认产品已发布。"

    with pytest.raises(Exception, match="没有返回修订正文"):
        ArticleWriter(FakeLLM(), Config()).write("产品发布", evidence)
