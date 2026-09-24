"""Web-only editorial modes. None means the legacy CLI writing path."""

from dataclasses import dataclass
from typing import Literal

ArticleStyle = Literal["story", "news", "deep"]


@dataclass(frozen=True)
class StyleStrategy:
    name: str
    outline: str
    section: str
    refine: str
    ending: str
    ending_title: str
    fallback_outline: tuple[str, ...]


STRATEGIES: dict[ArticleStyle, StyleStrategy] = {
    "story": StyleStrategy(
        name="故事化叙事",
        outline="以可核验的历史节点、关键转折和当前进展构成时间线；每个节点对应来源及发生日期，历史材料来自公开报道或官方资料，不以本站旧文章作证据。不得把报道日期直接当事件日期。先用当前变化提出问题，再回到必要的历史节点，最后回到当下。",
        section="根据有来源的历史节点与当前进展交替推进叙事和解释；注明机构、时间与转折的来源，不虚构现场、对话、人物心理或没有证据的因果。历史节点不能冒充最新进展。",
        refine="保留可核查的历史节点—关键转折—当前进展，逐项核对日期和来源；删掉无来源的桥段、悬念及因果，结尾交代事件今天走到哪里。",
        ending="回到当前进展，解释历史转折如何影响今天；区分已证实、尚未证实与后续观察指标。",
        ending_title="回到当下：结论、未知与观察",
        fallback_outline=("当前进展与问题", "有来源的历史节点与关键转折", "历史如何影响今天", "回到当下：结论、未知与观察"),
    ),
    "news": StyleStrategy(
        name="资讯解读",
        outline="当前进展优先：先交代最新已确认事实及来源，再用必要背景解释其意义、直接影响与尚待核实的信息；不为讲故事而堆历史。",
        section="紧扣当前进展，通俗解释事实、背景和直接影响；区分新闻事实与推断，背景只补足读者理解当前话题的需要。",
        refine="保持资讯解读的及时、准确与简洁；突出当前进展和直接影响，删去与当下无关的历史铺陈及重复议论。",
        ending="概括当下已确认的信息、直接影响与下一步可观察的进展。",
        ending_title="直接影响与后续进展",
        fallback_outline=("当前已确认的信息", "必要背景与直接影响", "未知问题与后续进展"),
    ),
    "deep": StyleStrategy(
        name="深度分析",
        outline="先用简洁语言概括事件现状，再分析证据支持的各方分歧、矛盾和机制，区分事实、立场和推断，讨论影响与边界。没有可靠分歧证据时，转为机制、影响与边界的分析，不得制造对立。",
        section="先明确已证实的现状，再用可归因的多方说法分析分歧与矛盾及其机制；没有可靠分歧证据时，分析机制、影响和边界，不得制造对立或假装各方势均力敌。",
        refine="用简洁语言压缩事件概况，保留证据支持的多方分歧、因果机制和影响边界；删去臆测动机、无证据的反对方和人为制造的冲突。",
        ending="归纳能得到的有限判断、仍有分歧或未知的变量以及后续观察指标；无可靠分歧证据时不得制造对立。",
        ending_title="有限判断、影响边界与观察",
        fallback_outline=("事件现状与证据", "有据可查的分歧或关键机制", "影响、边界与后续观察"),
    ),
}


def guidance(style: ArticleStyle | None, stage: Literal["outline", "section", "refine"]) -> str:
    if style is None:
        return ""
    strategy = STRATEGIES[style]
    return f"【文章模式】{strategy.name}\n【本阶段模式要求】{getattr(strategy, stage)}\n【收束方向】{strategy.ending}"
