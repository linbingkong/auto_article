"""AI 写作模块：证据约束的多阶段写作流水线。

流程：大纲生成 → 分节写作 → 润色 → 事实核验与修订 → 标题优化。
各阶段使用保留来源编号的紧凑证据视图，减少重复输入且避免脱离素材自由发挥。
"""

from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .article_styles import ArticleStyle, STRATEGIES, guidance
from .config import Config
from .formatter import normalize_markdown
from .llm import LLMClient, LLMError

logger = logging.getLogger(__name__)

EDITORIAL_METHOD = """原创融合型编辑方法（只采用通用方法，不模仿或复刻任何账号、媒体或作者措辞）：
- 开头钩子：前 120 字放入证据中最具体、反常或有现实后果的事实，随即提出全文要解释的问题；不用空洞背景、虚构现场或故意卖关子。
- 双线推进：叙事线用人物/机构、时间节点和关键转折回答“发生了什么”；解释线用利益、制度、产业链或行为约束回答“为什么”，两条线交替而不是先堆背景再集中评论。
- 论证单元：重要段落遵循“可核实事实或数据 → 来源归因 → 通俗机制解释 → 对谁产生什么影响”；类比只能帮助理解，随后必须交代边界和例外。
- 多方视角：至少呈现主要参与方各自的目标、资源、成本和约束；时间先后、利益一致和个人动机都不能自动写成因果证明。
- 结尾闭环：回到开头的问题，分别写明已知结论、未知变量和一个可继续观察的指标；不喊口号，不用强行升华代替答案。
- 语言节奏：短段落承载关键事实，中段落完成解释；句子清晰直接、专业词随文解释，克制使用设问和口语过渡，不用粗俗标签、群体刻板印象或煽动表达。
- 禁止空洞开场、情绪铺陈、反复说“值得关注/待核实”、先承认不确定却继续替传闻下结论，也不得在正文提及参考账号。"""


def _topic_story_framework(topic: str, evidence: str = "") -> str:
    """按题材选择原创叙事骨架，避免用单一模板处理所有新闻。"""
    text = f"{topic} {evidence[:3000]}".casefold()
    if re.search(r"金融|财经|经济|货币|利率|汇率|债务|楼市|股市|价格|消费|财政|美联储", text):
        return "财经机制线：事件/数据变化 → 政策或市场传导链 → 受益与承压群体 → 边界条件 → 后续观察指标；不作投资建议或无条件涨跌预测。"
    if re.search(r"国际|战争|外交|国家|总统|政府|制裁|关税|地缘|选举|军队|历史", text):
        return "国际与历史线：当前事件 → 必要历史节点 → 主要参与方的利益、能力与约束 → 关键转折 → 回到当下的有限判断；避免单一阴谋或民族性格解释。"
    if re.search(r"科技|人工智能|模型|芯片|软件|平台|公司|产业|制造|能源|汽车", text):
        return "科技产业线：新变化 → 与旧方案的可比差异 → 技术/供应链/商业机制 → 用户和行业影响 → 尚未验证的变量；参数和性能必须同口径比较。"
    return "社会议题线：具体事件 → 各方可核实说法 → 规则与行为激励 → 影响到哪些人 → 同类材料与反例 → 可讨论但不过度外推的结论。"


def _evidence_grade(evidence: str) -> str:
    match = re.search(r"【证据等级】(strong|medium|weak)", evidence or "")
    return match.group(1) if match else "weak"


_SLOGAN_RE = re.compile(r"时代洪流|历史的车轮|必将|注定|拭目以待|让我们共同期待|时间会证明一切|伟大概率|胜利属于")
_UNKNOWN_RE = re.compile(r"未知|尚未(?:明确|清楚|公布|得到回应)|仍(?:不明确|待观察)|有待(?:确认|观察)")
_OBSERVE_RE = re.compile(r"值得继续观察|后续(?:可|值得)?观察|观察指标|下一个关键|关注(?:其|该|后续)?(?:数据|进展|动向)|是否能够")


AIGC_CLICHE_RE = re.compile(
    r"在(?:这个|当今)(?:时代|背景下)|随着.{0,12}的(?:发展|推进)|不难发现|值得注意的是|总的来说|综上所述|总而言之|"
    r"与此同时|无独有偶|一波未平一波又起|让我们|拭目以待|拭目以俟|引发了广泛(?:关注|讨论)|在.{0,8}看来|"
    r"众说纷纭|见仁见智|仁者见仁|智者见智|时代的(?:洪流|浪潮)|社会(?:的)?缩影|折射出|映射出|值得深思|发人深省|"
    r"未来(?:可期|已来)|指日可待|任重道远|道阻且长|行则将至"
)
MECHANISM_RE = re.compile(r"因为|由于|导致|使得|机制|传导|约束|激励|成本|逻辑在于|原因在于|这意味着|带来的是|结果就是")
NEWS_RE = re.compile(r"称|表示|披露|公布|发布|通报|回应|援引|数据显示|报告显示|文件显示|记者了解|据.{2,12}(?:报道|消息)")
ENTITY_RE = re.compile(
    r"[\u4e00-\u9fa5A-Za-z]{2,12}(?:公司|集团|银行|基金|大学|研究院|研究所|委员会|部门|政府|法院|协会|交易所|平台|机构|企业|厂商|门店|工厂|实验室|医院|学校)"
)
ATTRIBUTION_RE = re.compile(r"[\u4e00-\u9fa5]{2,10}(?:表示|称|指出|强调|认为|回应|宣布|透露|介绍|解释|提醒|分析)")


def _paragraph_information(text: str) -> Dict[str, Any]:
    """评估单段是否携带新增信息：数字、命名实体、归因、机制或新闻动作。

    中文长文里的转场段、人物动作段和引语段常常没有数字，却包含实质信息，
    因此把机构实体、有主语的归因和直接引语一并视为有效信息。
    """
    chars = len(re.sub(r"\s", "", text))
    has_number = bool(re.search(r"\d", text))
    has_anchor = bool(re.search(r"\d|年|月|日|发布|通报|公告|文件|条例|办法|规定|回应|数据显示|报告显示|法院|政府|委员会|部门|公司|银行|协会|基金|平台", text))
    has_news_verb = bool(NEWS_RE.search(text))
    has_mechanism = bool(MECHANISM_RE.search(text))
    has_entity = bool(ENTITY_RE.search(text))
    has_attribution = bool(ATTRIBUTION_RE.search(text))
    has_quote = bool(re.search(r"[“”\"]", text))
    informative = chars >= 25 and (
        has_number or has_news_verb or has_mechanism or has_entity or has_attribution or has_quote
    )
    return {
        "chars": chars,
        "has_number": has_number,
        "has_anchor": has_anchor,
        "has_news_verb": has_news_verb,
        "has_mechanism": has_mechanism,
        "has_entity": has_entity,
        "has_attribution": has_attribution,
        "has_quote": has_quote,
        "informative": informative,
    }


def _content_paragraphs(markdown: str) -> List[str]:
    """取出正文段落，跳过标题行、图片与文末来源清单。"""
    text = re.sub(r"!\[[^]]*]\([^)]*\)", "", markdown or "")
    text = _strip_reference_sections(text)
    paragraphs: List[str] = []
    for block in re.split(r"\n\s*\n", text):
        body = "\n".join(
            line for line in block.splitlines() if not line.lstrip().startswith("#")
        ).strip()
        if body:
            paragraphs.append(body)
    return paragraphs


def _static_quality(markdown: str) -> Dict[str, Any]:
    text = re.sub(r"!\[[^]]*]\([^)]*\)", "", markdown or "")
    paragraphs = _content_paragraphs(markdown)
    hedge_re = re.compile(r"待核实|尚未证实|网传|传闻|据说|真假难辨|可能|或许")
    generic_re = AIGC_CLICHE_RE
    anchor_re = re.compile(r"\d|年|月|日|发布|通报|公告|文件|条例|办法|规定|回应|数据显示|报告显示|法院|政府|委员会|部门")
    anchored = sum(bool(anchor_re.search(p)) for p in paragraphs)
    density = round(anchored / max(1, len(paragraphs)), 2)
    infos = [_paragraph_information(p) for p in paragraphs]
    thin_paragraphs = sum(1 for item in infos if not item["informative"])
    mechanism_paragraphs = sum(1 for item in infos if item["has_mechanism"])
    mechanism_ratio = round(mechanism_paragraphs / max(1, len(paragraphs)), 2)
    opening = re.sub(r"\s+", "", next((p for p in paragraphs if p.strip()), ""))
    closing = re.sub(r"\s+", "", next((p.strip() for p in reversed(paragraphs) if p.strip()), ""))
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    repeated_number_ratio = 0.0
    if numbers:
        from collections import Counter

        counts = Counter(numbers)
        repeated_number_ratio = round(sum(c - 1 for c in counts.values() if c > 1) / len(numbers), 2)
    thin_ratio = round(thin_paragraphs / max(1, len(paragraphs)), 2)
    hedge_count = len(hedge_re.findall(text))
    cliche_count = len(generic_re.findall(text))
    slogan_count = len(_SLOGAN_RE.findall(text))

    # 硬门槛：只拦截平台明确打击的低创作度特征。
    hard_failed: List[str] = []
    if thin_ratio > 0.4:
        hard_failed.append(f"超过四成段落没有实际信息（薄弱段落 {thin_paragraphs}/{len(paragraphs)}）")
    if density < 0.35:
        hard_failed.append(f"含具体事实或来源的段落比例过低（{density}）")
    if cliche_count > 2:
        hard_failed.append(f"AIGC 套话 {cliche_count} 处")
    if slogan_count > 0:
        hard_failed.append(f"口号式表达 {slogan_count} 处")

    # 软指标：作为改进建议展示，不阻止保存。
    warnings: List[str] = []
    if len(paragraphs) < 5:
        warnings.append(f"正文只有 {len(paragraphs)} 个段落，篇幅偏短")
    if mechanism_ratio < 0.25:
        warnings.append(f"机制解释段落占比 {mechanism_ratio}，建议为关键变化补充因果链")
    if len(set(numbers)) < 6:
        warnings.append(f"独立数字只有 {len(set(numbers))} 个，建议补充数据或时间节点")
    if repeated_number_ratio > 0.3:
        warnings.append(f"数字重复率 {repeated_number_ratio}，避免反复使用同一数字")
    if hedge_count > 5:
        warnings.append(f"不确定表述 {hedge_count} 处，建议集中说明一次")

    return {
        "paragraph_count": len(paragraphs),
        "anchored_paragraph_count": anchored,
        "anchored_paragraph_ratio": density,
        "thin_paragraph_count": thin_paragraphs,
        "thin_paragraph_ratio": thin_ratio,
        "mechanism_paragraph_ratio": mechanism_ratio,
        "distinct_number_count": len(set(numbers)),
        "repeated_number_ratio": repeated_number_ratio,
        "opening_hook_chars": len(opening[:120]),
        "opening_concrete": bool(opening and (re.search(r"\d", opening[:120]) or "【" in markdown[:2000] or anchor_re.search(opening[:120]))),
        "closing_substance": bool(re.search(r"\d", closing) or len(closing) >= 30),
        "slogan_count": slogan_count,
        "cliche_count": cliche_count,
        "closing_unknown_variable": bool(_UNKNOWN_RE.search(text[-800:])),
        "closing_observation_indicator": bool(_OBSERVE_RE.search(text[-800:])),
        "placeholder_leak": "本节生成失败" in text or "生成失败：" in text,
        "editorial_reasoning": _looks_like_editorial_reasoning(text),
        "hard_failed": hard_failed,
        "warnings": warnings,
        "passed": not hard_failed and not warnings,
    }


@dataclass
class Article:
    """生成的文章。"""

    title: str
    digest: str
    content_md: str
    outline: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def word_count(self) -> int:
        return len(re.sub(r"\s", "", self.content_md))


def _strip_code_fence(text: str) -> str:
    text = (text or "").strip().lstrip("\ufeff")
    match = re.fullmatch(r"```(?:json|python|markdown|md)?\s*([\s\S]*?)\s*```", text, re.I)
    return match.group(1).strip() if match else text


CITATION_MARKER_RE = re.compile(r"\s*【S\d+(?:\s*[、,，;；/]\s*S?\d+)*】\s*")


def _strip_citation_markers(text: str) -> str:
    """删除模型从证据包带入正文的【S1】【S3、S5】类引用标识，保留自然语言归因。"""
    cleaned = CITATION_MARKER_RE.sub(" ", text or "")
    cleaned = re.sub(r"[ \t]+([，。；、！？])", r"\1", cleaned)
    return re.sub(r"([（(])\s+([）)])", r"\1\2", cleaned).replace("  ", " ").strip()


REFERENCE_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s*)?(?:\*\*|__)?(?:主要信源|参考资料|参考来源|信息来源|信源列表|来源列表|来源链接|References?)(?:\*\*|__)?[：:]?\s*$",
    re.I,
)


def _strip_reference_sections(text: str) -> str:
    """删除面向内部核验的文末来源清单，并让正文不携带可点击链接。"""
    lines = normalize_markdown(text).splitlines()
    kept: List[str] = []
    skip_level = 0
    for line in lines:
        heading = re.match(r"^(#{1,6})\s+", line.strip())
        if REFERENCE_HEADING_RE.match(line.strip()):
            skip_level = len(heading.group(1)) if heading else 6
            continue
        if skip_level:
            if heading and len(heading.group(1)) <= skip_level:
                skip_level = 0
            else:
                continue
        kept.append(line)
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\[([^]]+)]\(https?://[^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"(?m)^\s*(?:[-*+]\s*)?https?://\S+\s*$", "", cleaned)
    cleaned = re.sub(r"https?://[^\s)）>]+", "", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _prepare_publish_markdown(text: str) -> str:
    return normalize_markdown(_strip_reference_sections(_strip_citation_markers(text)))


def _claim_still_present(claim: str, markdown: str) -> bool:
    """修复后判断某条无法溯源断言是否仍留在正文中。"""
    needle = re.sub(r"[\s，。；：、（）()“”\"'—\-]", "", claim or "")[:8]
    if len(needle) < 4:
        return False
    haystack = re.sub(r"[\s，。；：、（）()“”\"'—\-]", "", markdown or "")
    return needle in haystack


def _loads_loose(text: str) -> Any:
    """兼容 JSON、Python 字面量以及被代码围栏包裹的模型输出。"""
    cleaned = _strip_code_fence(text)
    attempts = [cleaned]
    for left, right in (("[", "]"), ("{", "}")):
        start, end = cleaned.find(left), cleaned.rfind(right)
        if start >= 0 and end > start:
            attempts.append(cleaned[start : end + 1])
    for candidate in dict.fromkeys(attempts):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            try:
                return ast.literal_eval(candidate)
            except (ValueError, SyntaxError):
                continue
    raise ValueError("模型没有返回可解析的 JSON")


def _clean_title(value: Any, fallback: str = "") -> str:
    """从字符串/字典标题中提取纯标题，移除 Markdown 与风格标签。"""
    if isinstance(value, dict):
        for key in ("标题", "title", "headline", "name"):
            if value.get(key):
                return _clean_title(value[key], fallback)
        return fallback
    if isinstance(value, (list, tuple)):
        return _clean_title(value[0], fallback) if value else fallback

    text = str(value or "").strip()
    if text.startswith(("{", "[")):
        try:
            return _clean_title(_loads_loose(text), fallback)
        except ValueError:
            pass
    text = _strip_code_fence(text).splitlines()[0].strip() if text else ""
    text = re.sub(r"^\s*(?:[-*+]\s+|#{1,6}\s*)", "", text)
    text = re.sub(r"^(?:标题|title|headline)\s*[:：]\s*", "", text, flags=re.I)
    text = text.strip(" \t\r\n'\"`《》")
    return (text or fallback).strip()[:64]


_EDITORIAL_REASONING_MARKERS = (
    "我们需要输出标准 Markdown",
    "分析原文结构",
    "开始编辑：",
    "编辑要求：",
    "不要代码围栏",
    "现在，重新构建文章",
    "我们逐段：",
    "可以润色：",
)


def _looks_like_editorial_reasoning(text: str) -> bool:
    """识别模型泄露的编辑思路，避免将 reasoning 当作公众号正文。"""
    sample = str(text or "")[:6000]
    if any(marker in sample for marker in _EDITORIAL_REASONING_MARKERS):
        return True
    generic = ("原文：", "可以改：", "注意标题要求", "需要润色", "输出时")
    return sum(marker in sample for marker in generic) >= 3


def _build_digest(markdown: str, limit: int = 110) -> str:
    """从正文提取摘要，兼容标题与段落之间没有空行的模型输出。"""
    normalized = _prepare_publish_markdown(markdown)
    paragraphs: List[str] = []
    current: List[str] = []

    def flush() -> None:
        if not current:
            return
        block = " ".join(current).strip()
        current.clear()
        if not block or _looks_like_editorial_reasoning(block):
            return
        text = re.sub(r"!\[[^]]*]\([^)]*\)", "", block)
        text = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"^\s*(?:[-+*]|\d+[.)])\s+", "", text)
        text = re.sub(r"[`*_>#\[\]]", "", text)
        text = re.sub(r"\s+", " ", text).strip(" -—：:")
        if len(text) >= 12:
            paragraphs.append(text)

    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if re.match(r"^#{1,6}\s+", line) or re.fullmatch(r"!\[[^]]*]\([^)]*\)", line):
            flush()
            continue
        current.append(line)
    flush()

    digest = "".join(paragraphs).strip()
    if not digest:
        # 极短句逐行排版时也应能生成摘要，只有确实没有可见正文才返回空。
        fallback_lines = []
        for raw_line in normalized.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or re.fullmatch(r"!\[[^]]*]\([^)]*\)", line):
                continue
            if _looks_like_editorial_reasoning(line):
                continue
            line = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", line)
            line = re.sub(r"<[^>]+>|[`*_>#\[\]]", "", line).strip(" -—：:")
            if line:
                fallback_lines.append(line)
        digest = "".join(fallback_lines).strip()
    if len(digest) <= limit:
        return digest
    clipped = digest[:limit]
    boundary = max(clipped.rfind(mark) for mark in "。！？；")
    if boundary >= max(40, limit // 2):
        return clipped[: boundary + 1]
    return clipped


TITLE_METHOD = """标题方法（在事实边界内提供明确阅读收益，不照搬任何参考账号）：
1. 数字钩子：使用口径明确且已核实的数字、时间或金额，让读者知道变化规模。
2. 冲突对立：呈现证据确有的目标张力，如增长与成本、组织与个体、承诺与执行，不人为制造敌我。
3. 悬念提问：用“为何、如何、意味着什么”指向正文真正回答的机制问题，不能空悬。
4. 反差错位：并列两个可验证但超出预期的事实，让反差本身构成钩子。
5. 结果前置：先交代已发生的关键结果，再提出“为何走到这一步”。
6. 时间纵深：用明确时间跨度连接行业、企业或政策的关键转折，不用无关年代堆砌宏大感。
7. 机制解释：采用“具体对象＋核心变量/难题”的结构，直接承诺解释产业链、制度或利益传导。
8. 归因不丢：单方说法必须带“称/被指/曝”；预测使用条件式表达，未证实议题保留“网传/？”标记。
避免“突然、终于、果然、崩了、炸了、罪魁祸首、太妙了、一切结束”等先行煽动或绝对化词汇，除非它们是可核实的直接引语。"""

CLICKBAIT_TITLE_RE = re.compile(r"震惊|惊呆|惊爆|删前速看|速看|细思极恐|看完惊出|99%的人|转发就|不转不是|紧急扩散|必看|罪魁祸首|太妙了|一切结束|彻底崩了")

_TITLE_TYPES = ("数字钩子", "冲突对立", "悬念提问", "反差错位", "结果前置", "时间纵深", "机制解释")


def _lint_titles(titles: List[str], topic_title: str) -> List[str]:
    """过滤标题党词、事实升级与超长标题，保持类型多样性。"""
    rumor_re = re.compile(r"网传|传闻|爆料|据说")
    attribution_re = re.compile(r"网传|传闻|爆料|据说|疑似|被指|涉嫌|曝|称")
    rumor_topic = bool(rumor_re.search(topic_title))
    result: List[str] = []
    for raw in titles:
        title = (raw or "").strip()
        if not title or title in result:
            continue
        if CLICKBAIT_TITLE_RE.search(title):
            logger.warning("title rejected (clickbait): %s", title)
            continue
        if rumor_topic and not attribution_re.search(title) and "？" not in title and "?" not in title and not title.rstrip("？?").endswith(("吗", "呢")):
            logger.warning("title rejected (rumor escalation): %s", title)
            continue
        result.append(title)
    return result[:6]


class EvidenceViews:
    """从完整证据包构造按阶段、按章节的可追溯紧凑视图。"""

    SOURCE_RE = re.compile(r"(?=【S\d+｜)")
    NOISE_RE = re.compile(r"^(?:首页|财经|股票|基金|行情|数据|搜索|登录|注册|分享|微信扫一扫|小 中 大)$")
    FACT_RE = re.compile(r"\d|年|月|日|称|表示|发布|公告|规定|报告|数据|调查|回应|记者|显示")

    def __init__(self, evidence: str, topic: str):
        self.full = (evidence or "").strip()
        self.topic = topic
        marker = re.search(r"(?=【S\d+｜)", self.full)
        self.preamble = self.full[: marker.start()].strip() if marker else self.full[:2000]
        tail = self.full[marker.start():] if marker else ""
        self.blocks = [part.strip() for part in self.SOURCE_RE.split(tail) if part.strip()]

    @staticmethod
    def _keywords(text: str) -> List[str]:
        words = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9._+-]{2,}", text or "")
        stop = {"已经确认", "什么", "主体", "时间", "来源", "事情如何", "关键节点", "各方回应", "信息", "判断", "延伸讨论"}
        return list(dict.fromkeys(word.casefold() for word in words if word not in stop))[:20]

    def _compact_block(self, block: str, query: str, budget: int) -> str:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            return ""
        header = lines[0]
        url = next((line for line in lines[1:5] if line.startswith("URL：")), "")
        body_lines = []
        for line in lines[1:]:
            if line == url or self.NOISE_RE.match(line):
                continue
            if line.count("](") >= 3 or line.count("http") >= 3:
                continue
            body_lines.append(line)
        sentences = []
        seen_sentences = set()
        for line in body_lines:
            for part in re.split(r"(?<=[。！？；.!?])\s*|\n+", line):
                sentence = part.strip()
                key = re.sub(r"\s+", "", sentence).casefold()
                if len(sentence) < 8 or key in seen_sentences:
                    continue
                seen_sentences.add(key)
                sentences.append(sentence)
        keywords = self._keywords(query)
        ranked = []
        for index, sentence in enumerate(sentences):
            folded = sentence.casefold()
            score = sum(5 for keyword in keywords if keyword in folded)
            score += 3 if self.FACT_RE.search(sentence) else 0
            score += 4 if index < 4 else 0
            score += 1 if 20 <= len(sentence) <= 280 else 0
            ranked.append((score, index, sentence))
        selected_indexes = set()
        used = len(header) + len(url) + 4
        for _, index, sentence in sorted(ranked, key=lambda item: (-item[0], item[1])):
            if used + len(sentence) + 1 > budget:
                continue
            selected_indexes.add(index)
            used += len(sentence) + 1
            if used >= budget * 0.9:
                break
        chosen = [sentence for index, sentence in enumerate(sentences) if index in selected_indexes]
        return "\n".join(item for item in ([header, url] + chosen) if item)[:budget]

    def _view(self, query: str, budget: int, max_sources: int | None = None) -> str:
        if not self.blocks:
            return self.full[:budget]
        keywords = self._keywords(query)
        ranked = []
        for index, block in enumerate(self.blocks):
            folded = block.casefold()
            score = sum(folded.count(keyword) for keyword in keywords)
            score += 2 if "official" in folded or "官方" in block else 0
            ranked.append((score, index, block))
        source_limit = min(len(ranked), max_sources or len(ranked))
        picked = sorted(sorted(ranked, key=lambda item: (-item[0], item[1]))[:source_limit], key=lambda item: item[1])
        preamble = self.preamble[:1600]
        remaining = max(1000, budget - len(preamble) - 2)
        per_source = max(900, remaining // max(1, len(picked)))
        blocks = [self._compact_block(block, query, per_source) for _, _, block in picked]
        return (preamble + "\n\n" + "\n\n".join(block for block in blocks if block))[:budget]

    def outline(self) -> str:
        return self._view(self.topic, 14_000)

    def section(self, heading: str) -> str:
        return self._view(f"{self.topic} {heading}", 8_500, max_sources=4)

    def refine(self) -> str:
        return self._view(self.topic, 5_000, max_sources=4)

    def fact_revision(self, headings: Optional[List[str]] = None) -> str:
        query = " ".join([self.topic] + list(headings or []))
        return self._view(query, 22_000)

    def fact_audit(self, headings: Optional[List[str]] = None) -> str:
        query = " ".join([self.topic] + list(headings or []))
        return self._view(query, 14_000)

    def titles(self) -> str:
        return self._view(self.topic, 4_000, max_sources=4)


class ArticleWriter:
    """以信源证据为边界的多阶段文章写作器。"""

    def __init__(self, llm: LLMClient, config: Config):
        self.llm = llm
        self.cfg = config

    def _gen_outline(self, topic_title: str, evidence: str = "", style: ArticleStyle | None = None) -> List[str]:
        grade = _evidence_grade(evidence)
        target_words = self.cfg.target_words if grade == "strong" else min(self.cfg.target_words, 1600)
        closing_rule = (f"5. 最后一章：{STRATEGIES[style].ending}" if style else
            "5. 最后一章必须是“延伸与讨论”：用同类案例、调查或数据检验普遍性，回答开头问题，并分别给出未知变量与后续观察指标；不能从单一个案直接跳到宏大结论")
        mode_line = guidance(style, "outline") + "\n" if style else ""
        prompt = f"""请为一篇新闻解释型微信公众号文章生成写作大纲。

{EDITORIAL_METHOD}
【本题叙事骨架】{_topic_story_framework(topic_title, evidence)}
{mode_line}
【热点话题】{topic_title}
【账号定位】{self.cfg.account_position}
【目标读者】{self.cfg.audience}
【目标字数】约 {target_words} 字
【证据等级】{grade}
【可用信源证据】
{evidence or '当前没有可核实素材，只能做观点分析，不得补写具体事实、数字、日期或引语。'}

要求：
1. 输出 JSON 数组，每项是一个章节标题（3-6 个章节）
2. 第一章必须直接交代已确认的新信息和来源，不用悬念、情绪或宏大议论开场
3. 若涉及制度，必须安排“制度原文是什么、制定/执行主体、适用范围、执行机制”章节；没有官方原文则不得设计制度效果结论
4. 后续章节让“人物/机构与时间转折”的叙事线和“利益/制度/产业约束”的解释线交替推进；只保留改变因果链的历史节点，不堆砌年代
{closing_rule}
6. 每个章节标题都必须对应证据包中的具体材料，不能用空泛的‘深层影响’凑结构
7. 只输出 JSON 数组，不要其他文字。"""
        try:
            text = self.llm.chat(
                [
                    {"role": "system", "content": "你是严谨的公众号主编，只依据给定信源设计结构。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.45,
                model=self.cfg.llm.outline_model,
                max_tokens=1024,
                thinking=False,
                usage_label="outline",
            )
            arr = _loads_loose(text)
            if isinstance(arr, dict):
                arr = arr.get("大纲") or arr.get("outline") or arr.get("sections")
            if isinstance(arr, list) and arr:
                result = []
                for item in arr:
                    if isinstance(item, dict):
                        item = item.get("标题") or item.get("title") or item.get("section") or ""
                    title = _clean_title(item)
                    if title:
                        result.append(title)
                if result:
                    extension = STRATEGIES[style].ending_title if style else "延伸与讨论：同类现象背后的价值选择与社会变化"
                    ending_pattern = r"结论|观察|进展|影响|边界|延伸|讨论" if style else r"延伸|同类|社会|价值|讨论"
                    if not any(re.search(ending_pattern, item) for item in result[-2:]):
                        result = result[:5] + [extension]
                    return result[:6]
            raise ValueError("not a list")
        except Exception as exc:  # noqa: BLE001
            logger.warning("outline gen failed, fallback to simple outline: %s", exc)
            if style:
                return list(STRATEGIES[style].fallback_outline)
            return [
                "已经确认了什么：主体、时间与来源",
                "事情如何发展：关键节点与各方回应",
                "规则如何运作：适用范围与执行机制",
                "信息能支持哪些判断，不能支持哪些判断",
                "延伸与讨论：同类现象背后的价值选择与社会变化",
            ]

    def _write_section(
        self,
        topic_title: str,
        outline: List[str],
        idx: int,
        section: str,
        evidence: str,
        style: ArticleStyle | None = None,
    ) -> str:
        target_words = self.cfg.target_words if _evidence_grade(evidence) == "strong" else min(self.cfg.target_words, 1600)
        section_words = max(280, target_words // max(1, len(outline)))
        opening_guidance = """
本章是全文开头，必须遵循：
- 前 120 字直接使用证据中最具体的事实、数字、时间节点或结果反差，并自然注明来源
- 紧接着提出全文真正要回答的机制问题；不能用“最近一件事引发关注”“在这个时代”等泛化句开场
- 若证据没有现场细节，不得虚构人物动作、对话、气氛或亲历感
""" if idx == 0 else ""
        extension_guidance = """
本章是全文最后的“延伸与讨论”，必须遵循：
- 先回答“这是孤立个案还是同类现象”：至少引用证据包中的同类案例、调查或统计之一；没有材料就明确只能提出观察问题，不能声称社会趋势
- 从具体现象提炼可解释的机制，例如技术改变组织关系、效率与人的尊严冲突、平台规则与个体权利失衡、代际价值变化；每一层推论都写明由哪些事实推出
- 再讨论价值观和社会发展：呈现不同价值之间真实的取舍，不做单向道德审判，也不使用“时代洪流、社会缩影、值得深思”之类无信息套话
- 给出制度、组织或个人层面的可讨论方向，但不虚构政策建议已经有效
- 回答开头的核心问题，再依次交代一个仍未知的变量和一个可持续观察的指标；可以用具体问题邀请讨论，但不喊口号、不强行升华
""" if idx == len(outline) - 1 else ""
        if style and idx == len(outline) - 1:
            extension_guidance = f"本章为收束：{STRATEGIES[style].ending}"
        mode_line = guidance(style, "section") + "\n" if style else ""
        prompt = f"""请撰写新闻解释型公众号文章的一个章节。

{EDITORIAL_METHOD}
【本题叙事骨架】{_topic_story_framework(topic_title, evidence)}
{mode_line}
【文章话题】{topic_title}
【全文大纲】{' | '.join(outline)}
【本章节标题】{section}
【章节位置】第 {idx + 1} 章 / 共 {len(outline)} 章
【可用信源证据】
{evidence or '没有可核实素材。'}
{opening_guidance}
{extension_guidance}
硬性事实规则：
1. 具体人物、日期、数字、型号参数、报价、机构结论、直接引语，只能来自上面的信源证据
2. 证据没有写明的内容不得补全、推测成事实，也不得用“据公开报道”“业内人士称”掩盖无来源
3. 每项事实在首次出现时自然归因，例如“某部门在某文件中规定”“某媒体援引某机构数据称”；不要使用无指向的“有消息称”；严禁把证据包里的【S1】【S3、S5】等编号标记写进正文，读者看到的必须是完整来源描述
4. 解释与推断必须紧跟其事实依据，说明推理链；不能只加“可能”二字就把无证据断言保留下来
5. 多个信源冲突时并列写明各自主张与来源，只在全文一个位置集中说明未决信息
6. 涉及制度时，必须从官方原文提取名称、发布/实施时间、制定和执行主体、适用对象、关键条款；缺任何一项就明确不讨论该项，不凭常识补齐

写作要求：
1. 输出该章节的 Markdown 正文，不要重复章节标题；小节统一使用“### 标题”
2. 约 {section_words} 字；每一段必须提供一个此前章节未出现过的新事实、数字、制度细节、来源归因或机制解释；重复已知信息或不足 40 字的段落直接删除
3. 信息增量自查：写下每段前先问“读者读完这段新知道了什么”；只是换句式复述、堆背景、贴标签的内容一律不写
4. 专业但通俗，采用自然节奏；禁止“在当今时代”“随着发展”“值得注意的是”“综上所述”“折射出”“值得深思”“引发广泛关注”等 AIGC 套话
5. 不要把“热点为何引发关注”当新闻，不要复述同一个传闻来凑篇幅
6. 不要输出 JSON、代码围栏或写作说明。"""
        try:
            result = self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": "你是事实优先的中文作者。只输出最终章节正文，不输出分析、计划或写作说明。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                model=self.cfg.llm.model,
                max_tokens=min(max(self.cfg.llm.max_tokens, 2048), 3072),
                thinking=False,
                usage_label=f"section_{idx + 1}",
            )
            if _looks_like_editorial_reasoning(result):
                raise LLMError("章节输出包含编辑思路，已拒绝写入正文")
            return result
        except LLMError as exc:
            logger.warning("section %d failed: %s", idx, exc)
            raise LLMError(f"章节“{section}”生成失败：{exc}；已停止生成，不允许输出半成品文章") from exc

    def _refine(self, full_md: str, evidence: str, topic_title: str = "", style: ArticleStyle | None = None) -> str:
        mode_line = guidance(style, "refine") + "\n" if style else ""
        extension_rule = (f"8. {STRATEGIES[style].ending}" if style else
            "8. 保留并强化最后的“延伸与讨论”：用同类案例、调查、数据或反例检验普遍性，严禁从单一个案直接推出社会趋势")
        prompt = f"""请按新闻编辑标准重写下面的公众号文章。编辑不能增加任何新事实。

{EDITORIAL_METHOD}
【本题叙事骨架】{_topic_story_framework(topic_title, evidence)}
{mode_line}
【唯一可用信源证据】
{evidence or '没有可核实素材。'}

编辑要求：
1. 首段前 120 字保留最具体、反常或有现实后果的已核实事实，并迅速提出全文要解释的问题；删除空洞背景式开场
2. 保持结构与观点，但删除证据中不存在的具体数字、日期、参数、引语和确定性结论，不得新增虚构现场或人物动作
3. 让叙事线与解释线交替：每出现一个关键节点，紧接着说明利益、制度、技术或产业机制，而不是连续堆砌背景
4. 重要段落改成“事实/数据—来源归因—机制解释—影响与边界”；短段落突出转折，中段落完成解释，删除没有新增信息的段落
5. 全文对同一未决事实最多集中说明一次；不要在每节重复“待核实”“可能”“如果”，类比之后必须说明不适用的边界
6. 制度议题逐项核对官方原文；没有原文支持的制度名称、条款、适用对象和效果判断全部删除
7. 涉及多方博弈时分别交代目标、资源、成本与约束；不得用性格、阴谋或群体标签替代多因分析
{extension_rule}
9. 结尾回到开头问题，依次给出有证据的结论、仍未知的变量和一个可观察指标；可以提出具体讨论问题，但不做警句式强行升华
10. 信息增量终审：若整段没有新增事实、数据、来源或机制，直接删除；不得保留任何“总结式复读”段落；禁止“在当今时代”“随着发展”“综上所述”“折射出”“值得深思”“引发广泛关注”等 AIGC 套话
11. 文章结尾不要添加“主要信源”“参考资料”“来源列表”等任何链接或出处清单章节；来源信息只以正文自然归因的方式出现
12. 统一输出标准 Markdown：标题标记后必须有空格，不要代码围栏，不要解释

文章内容：
---
{full_md}
---"""
        try:
            result = self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": "你是严谨的中文事实编辑。只输出最终 Markdown 正文，不输出分析、计划或编辑说明。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.35,
                model=self.cfg.llm.refine_model,
                max_tokens=min(max(self.cfg.llm.max_tokens, 3072), 4096),
                thinking=False,
                usage_label="refine",
            )
            if _looks_like_editorial_reasoning(result):
                logger.warning("refine output rejected: editorial reasoning detected")
                return full_md
            return result
        except LLMError as exc:
            logger.warning("refine failed: %s", exc)
            return full_md

    def _repair_quality(
        self,
        markdown: str,
        flagged_paragraphs: List[str],
        unsupported_claims: List[str],
        evidence: str,
    ) -> str:
        """对薄弱段落和无法溯源断言做一次定点修复，避免整篇文章直接失败。"""
        flagged_block = "\n\n".join(
            f"【薄弱段落 {index}】{text[:400]}" for index, text in enumerate(flagged_paragraphs, start=1)
        ) or "无"
        claim_block = "\n".join(f"- {claim[:200]}" for claim in unsupported_claims[:10]) or "无"
        prompt = f"""你是发布前内容质量修复编辑。只做定点修复，其余段落保持原样。

【信源证据】
{evidence or '没有可核实素材。'}

【需要修复的薄弱段落】
{flagged_block}

【无法溯源的断言】
{claim_block}

【全文】
{markdown}

修复规则：
1. 每个薄弱段落二选一：用证据包里的具体事实、数字、时间、来源归因或机制解释改写充实；证据不足就整段删除
2. 无法溯源的断言二选一：改为证据支持的表述；没有证据就直接删除该断言
3. 不得新增证据包之外的数字、引语、机构名称或因果关系
4. 保留原有标题层级、章节顺序和结尾的结论与观察指标；不要新增“主要信源”“参考资料”等链接清单
5. 不要输出修改说明，只输出修复后的完整 Markdown 正文"""
        try:
            repaired = normalize_markdown(self.llm.chat(
                [
                    {"role": "system", "content": "你是保守的内容修复编辑。只输出修复后的完整 Markdown 正文。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=min(max(self.cfg.llm.max_tokens, 3072), 4096),
                thinking=False,
                usage_label="quality_repair",
            ))
        except LLMError as exc:
            logger.warning("quality repair failed: %s", exc)
            return markdown
        if not repaired.strip() or _looks_like_editorial_reasoning(repaired):
            logger.warning("quality repair output rejected; keeping original draft")
            return markdown
        if not _build_digest(repaired):
            logger.warning("quality repair dropped all body text; keeping original draft")
            return markdown
        return repaired

    def _fact_check(self, full_md: str, evidence: str, audit_evidence: Optional[str] = None) -> tuple[str, Dict[str, Any]]:
        """先输出纯 Markdown 修订稿，再输出紧凑 JSON 报告，避免长正文嵌入 JSON 导致转义失败。"""
        revision_prompt = f"""你是发布前事实核验编辑。仅依据证据修订文章，不使用自身记忆补充事实。

【信源证据】
{evidence or '没有可核实素材。'}

【待核验文章】
{full_md}

修订规则：
- 删除所有没有来源支持的事实、数字、引语、因果和制度结论；不能只加“可能/待核实”后保留
- 制度内容逐项核对官方原文；延伸讨论中的社会趋势必须有同类案例、调查或数据支持
- 删除空话和从单一个案直接推出的宏观结论；删除文末“主要信源”“参考资料”“来源列表”等出处清单章节，URL 只保留在证据包与审计报告里
- 删除正文中残留的【S1】【S3、S5】等证据编号标记，换成“据某来源”“某公告显示”等自然归因；来源编号只允许出现在 JSON 审计字段里
- 删除只换句式复述、没有新增事实/数据/来源/机制的段落；同时清除“在当今时代”“随着发展”“综上所述”“折射出”“值得深思”“引发广泛关注”等套话
- 清除正文中所有【S1】【S3、S5】类引用编号标记，改写为自然语言来源描述
- 保持完整文章结构、标题层级和“延伸与讨论”章节

只输出修订后的完整 Markdown 正文；不要 JSON、代码围栏、核验说明或修改清单。"""
        try:
            revised = normalize_markdown(self.llm.chat(
                [
                    {"role": "system", "content": "你是保守的事实核验员。证据不足时删除，不猜测；只返回最终 Markdown。"},
                    {"role": "user", "content": revision_prompt},
                ],
                temperature=0.1,
                max_tokens=min(max(self.cfg.llm.max_tokens, 3072), 4096),
                thinking=False,
                usage_label="fact_revision",
            ))
            if not revised.strip():
                raise ValueError("事实核验没有返回修订正文")
            if _looks_like_editorial_reasoning(revised):
                raise ValueError("事实核验修订稿包含模型编辑思路")
            if not _build_digest(revised):
                logger.warning("fact revision returned headings without substantive body; falling back to refined draft")
                revised = normalize_markdown(full_md)
                if not _build_digest(revised):
                    raise ValueError("事实核验修订稿和润色稿均没有有效正文")

            audit_prompt = f"""仅依据下列信源证据，审计修订后的文章。不要再次输出文章正文。

【信源证据】
{audit_evidence or evidence or '没有可核实素材。'}

【修订后文章】
{revised}

返回一个简短 JSON 对象：
{{"risk_level":"low|medium|high","information_score":0,"issues":["问题"],"low_information_paragraphs":["信息量不足段落的位置和原因"],"claim_audit":[{{"claim":"关键断言的简短摘要","type":"fact|interpretation","supporting_source_ids":["S1"],"status":"supported|unsupported"}}]}}

要求：
1. information_score 按信息增量评估：独立事实、数字、来源和机制解释越多越高；换句式复述、空洞过渡不加分；低于 60 表示内容偏薄
2. low_information_paragraphs 列出只有泛化叙述、没有新增信息的段落位置及原因；没有则空数组
3. claim_audit 必须逐项覆盖正文中的关键断言（含数字、事件、机制判断），最多 20 项，每项给出证据包中的来源编号；无法溯源标记 unsupported
4. 不要在 JSON 中包含 revised_markdown、整篇文章、Markdown 或解释文字。"""
            raw_report = self.llm.chat(
                [
                    {"role": "system", "content": "你是事实核验审计员。只输出紧凑、合法、可解析的 JSON 对象。"},
                    {"role": "user", "content": audit_prompt},
                ],
                temperature=0.0,
                max_tokens=min(max(self.cfg.llm.max_tokens, 2048), 3072),
                json_mode=True,
                thinking=False,
                usage_label="fact_audit",
            )
            try:
                result = _loads_loose(raw_report)
            except ValueError:
                logger.warning("fact audit JSON parse failed; retrying without response_format")
                raw_report = self.llm.chat(
                    [
                        {"role": "system", "content": "只输出单行合法 JSON，不要代码围栏、Markdown 或任何解释。"},
                        {"role": "user", "content": audit_prompt + "\n\n上一次格式无效。本次必须输出单行合法 JSON，字符串中的换行必须转义。"},
                    ],
                    temperature=0.0,
                    max_tokens=min(max(self.cfg.llm.max_tokens, 2048), 3072),
                    json_mode=False,
                    thinking=False,
                    usage_label="fact_audit_retry",
                )
                result = _loads_loose(raw_report)
            if not isinstance(result, dict):
                raise ValueError("模型没有返回 JSON 对象")

            issues = result.get("issues") if isinstance(result.get("issues"), list) else []
            low_info = result.get("low_information_paragraphs") if isinstance(result.get("low_information_paragraphs"), list) else []
            risk = str(result.get("risk_level") or "medium").lower()
            if risk not in {"low", "medium", "high"}:
                risk = "medium"
            claims = result.get("claim_audit") if isinstance(result.get("claim_audit"), list) else []
            try:
                information_score = int(result.get("information_score") or 0)
            except (TypeError, ValueError):
                information_score = 0
            quality = _static_quality(revised)
            unsupported = sum(1 for item in claims if isinstance(item, dict) and item.get("status") == "unsupported")
            report = {
                "status": "completed",
                "risk_level": risk,
                "information_score": max(0, min(100, information_score)),
                "unsupported_claims": unsupported,
                "issues": [str(x)[:300] for x in issues[:20]],
                "low_information_paragraphs": [str(x)[:200] for x in low_info[:10]],
                "claim_audit": claims[:20],
                "quality_metrics": quality,
            }
            if quality["placeholder_leak"] or quality["editorial_reasoning"]:
                raise LLMError("正文包含失败占位文本或模型编辑思路，已阻止保存")

            unsupported_claims = [
                str(item.get("claim"))[:200]
                for item in claims
                if isinstance(item, dict) and item.get("status") == "unsupported" and item.get("claim")
            ]
            flagged = [
                paragraph
                for paragraph in _content_paragraphs(revised)
                if not _paragraph_information(paragraph)["informative"]
            ]

            needs_repair = bool(quality["hard_failed"]) or bool(unsupported_claims) or len(flagged) >= 3
            if needs_repair:
                logger.info(
                    "quality repair triggered: thin=%d unsupported=%d hard=%s",
                    len(flagged),
                    len(unsupported_claims),
                    quality["hard_failed"],
                )
                repaired = self._repair_quality(revised, flagged, unsupported_claims, evidence)
                if repaired != revised:
                    revised = repaired
                    quality = _static_quality(revised)
                    still_present = [
                        claim for claim in unsupported_claims if _claim_still_present(claim, revised)
                    ]
                    unsupported_claims = still_present
                    report["quality_metrics"] = quality
                    if quality["placeholder_leak"] or quality["editorial_reasoning"]:
                        raise LLMError("正文包含失败占位文本或模型编辑思路，已阻止保存")

            if quality["hard_failed"] or unsupported_claims:
                # 修复后仍有残留：不阻断任务，改为高风险标记（推送会被阻止）并给出明确人工修改清单。
                risk = "high"
                if quality["hard_failed"]:
                    issues = list(issues) + [f"低创作度风险：{item}" for item in quality["hard_failed"]]
                if unsupported_claims:
                    issues = list(issues) + [
                        f"无法溯源断言 {len(unsupported_claims)} 项，需要人工核对：{unsupported_claims[0]}"
                    ]
            report.update(
                risk_level=risk,
                unsupported_claims=len(unsupported_claims),
                issues=[str(x)[:300] for x in issues[:20]],
                quality_metrics=quality,
            )
            report["style_warnings"] = list(quality["warnings"])
            if flagged and not needs_repair:
                report["style_warnings"].append(f"建议补充信息量的段落 {len(flagged)} 段")
            return revised, report
        except Exception as exc:  # noqa: BLE001
            logger.warning("fact check failed: %s", exc)
            raise LLMError(f"事实核验未完成，已阻止保存半成品文章：{exc}") from exc

    def _gen_titles(self, topic_title: str, digest: str, evidence: str = "") -> List[str]:
        grade = _evidence_grade(evidence)
        # Python 3.10 的 f-string 表达式内不允许反斜杠，先在表达式外完成清洗。
        source_digest = re.sub(r"【S\d+｜[^】]+】", "", evidence)[:4000] or "没有可核实素材。"
        prompt = f"""请为这篇新闻解释型公众号文章生成 8 个候选标题。

{TITLE_METHOD}

【证据等级】{grade}
【可用信源摘要】
{source_digest}

【内容主题】{topic_title}
【内容摘要】{digest}

硬性规则：
1. 每个标题 10-30 字；断言必须有信源支持，不得编造双方回应、数据或冲突，不得把“网传/传闻”升级成确定事实
2. 证据等级为 medium 时，全部标题必须是疑问式或带“传闻/网传/待证实”明确标记
3. 8 个标题尽量覆盖不同类型；同一类型最多 2 个
4. 至少包含 1 个“时间纵深”或“机制解释”标题；只输出 JSON 数组，元素为对象：{{"type":"数字钩子|冲突对立|悬念提问|反差错位|结果前置|时间纵深|机制解释","标题":"标题文本"}}；不要输出 Markdown、序号或代码围栏。"""
        try:
            text = self.llm.chat(
                [
                    {"role": "system", "content": "你是克制但锋利的公众号标题编辑：吸引人靠具体、冲突和悬念，不靠夸张。只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=1024,
                thinking=False,
                usage_label="titles",
            )
            arr = _loads_loose(text)
            if isinstance(arr, dict):
                arr = arr.get("标题") or arr.get("titles") or arr.get("候选标题") or [arr]
            if not isinstance(arr, list):
                arr = [arr]
            titles: List[str] = []
            typed: Dict[str, int] = {}
            for item in arr:
                title = _clean_title(item, topic_title)
                if not title or title in titles:
                    continue
                kind = item.get("type", "") if isinstance(item, dict) else ""
                if kind in _TITLE_TYPES and typed.get(kind, 0) >= 2:
                    logger.warning("title dropped for type balance (%s): %s", kind, title)
                    continue
                typed[kind] = typed.get(kind, 0) + 1
                titles.append(title)
            titles = _lint_titles(titles, topic_title)
            if titles:
                return titles
        except Exception as exc:  # noqa: BLE001
            logger.warning("title gen failed: %s", exc)
        fallback = _clean_title(topic_title, "未命名文章")
        return _lint_titles([fallback], topic_title) or [f"{fallback}？"]

    def write(self, topic_title: str, context: str = "", progress: Optional[Callable[[str], None]] = None, *, style: ArticleStyle | None = None) -> Article:
        notify = progress if progress is not None else (lambda message: None)
        logger.info("writing article for topic: %s", topic_title)
        topic_title = _clean_title(topic_title, "未命名话题")
        evidence = (context or "").strip()
        grade = _evidence_grade(evidence)
        if grade == "weak":
            raise LLMError("实质信源不足两条，已停止生成空泛长文；请配置 Tavily 或补充官方文件、权威报道后重试")
        if style == "story" and not ("【历史节点】" in evidence and "【当前进展】" in evidence):
            raise LLMError("故事化叙事缺少可核验的历史节点或当前进展；请补充带日期和链接的历史报道、官方资料及最新报道")

        views = EvidenceViews(evidence, topic_title)
        notify("正在生成证据约束的大纲")
        outline = self._gen_outline(topic_title, views.outline(), style=style)
        sections = []
        total = max(1, len(outline))
        for i, section in enumerate(outline):
            notify(f"正在撰写第 {i + 1}/{total} 章：{section}")
            body = self._write_section(topic_title, outline, i, section, views.section(section), style=style)
            sections.append(f"## {section}\n\n{body}")

        notify("正在全文润色")
        full_md = normalize_markdown("\n\n".join(sections))
        refined = normalize_markdown(self._refine(full_md, views.refine(), topic_title, style=style))
        notify("正在两阶段事实核验（修订正文 + 生成审计报告）")
        verified, fact_report = self._fact_check(refined, views.fact_revision(outline), views.fact_audit(outline))
        final_md = _prepare_publish_markdown(verified)
        if _looks_like_editorial_reasoning(final_md):
            raise LLMError("文章包含模型编辑思路，已阻止保存；请重新生成")

        digest = _build_digest(final_md)
        if not digest:
            raise LLMError("没有从正文中提取到有效摘要，已阻止保存")
        notify("正在生成候选标题")
        titles = self._gen_titles(topic_title, digest, views.titles())
        quality = fact_report.get("quality_metrics", {})
        if not quality.get("passed", False):
            logger.warning("quality gate soft-fail: %s", quality)

        article = Article(
            title=titles[0],
            digest=digest,
            content_md=final_md,
            outline=outline,
            meta={
                "title_candidates": titles,
                "topic": topic_title,
                "fact_check": fact_report,
            },
        )
        logger.info("article done: %d chars, %d words", len(final_md), article.word_count)
        return article
