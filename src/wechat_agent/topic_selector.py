"""选题筛选与评分模块。

对采集到的热点做多维度过滤与评分：
1. 黑名单过滤（敏感词/不相关词）
2. 白名单偏好（账号垂直领域词，命中加权）
3. 排名与热度归一化评分
4. 时效性判断
5. 去重（跨平台相同话题合并，保留最高热度）
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List

from .config import TopicFilterConfig
from .hot_topics import HotTopic

logger = logging.getLogger(__name__)


@dataclass
class ScoredTopic:
    """带评分的热点候选。"""

    topic: HotTopic
    score: float
    reasons: List[str] = field(default_factory=list)
    matched_keywords: List[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.topic.title


class TopicSelector:
    """选题筛选器：过滤 + 评分 + 去重。"""

    def __init__(self, config: TopicFilterConfig):
        self.config = config
        self._black_re = None
        self._white_re = None
        if config.blacklist:
            self._black_re = re.compile(
                "|".join(re.escape(w) for w in config.blacklist), re.I
            )
        if config.whitelist:
            self._white_re = re.compile(
                "|".join(re.escape(w) for w in config.whitelist), re.I
            )

    # ------------------------------------------------------------------
    def _passes_blacklist(self, topic: HotTopic) -> bool:
        if not self._black_re:
            return True
        return not self._black_re.search(topic.title)

    def _whitelist_hit(self, topic: HotTopic) -> List[str]:
        if not self._white_re:
            return []
        return [w for w in self.config.whitelist if w.lower() in topic.title.lower()]

    # ------------------------------------------------------------------
    def _rank_score(self, topic: HotTopic) -> float:
        """排名分：第1名100分，线性衰减到0。"""
        rank = topic.rank or 100
        return max(0.0, 100.0 - (rank - 1) * 2.0)

    def _heat_score(self, topic: HotTopic) -> float:
        """热度分：各平台口径不同，做归一化近似（取 log 压缩）。"""
        heat = topic.heat
        if heat is None:
            return 30.0  # 无热度信息给中性分
        try:
            hv = float(heat)
        except (TypeError, ValueError):
            return 30.0
        if hv <= 0:
            return 20.0
        return min(100.0, 20.0 + 15.0 * (hv ** 0.3))

    # ------------------------------------------------------------------
    def score(self, topic: HotTopic) -> ScoredTopic:
        reasons: List[str] = []
        s = self._rank_score(topic) * 0.5 + self._heat_score(topic) * 0.3
        hits = self._whitelist_hit(topic)
        if hits:
            s += 15.0
            reasons.append(f"白名单命中: {','.join(hits)}")
        # 标题长度适中加分（过短信息量不足，过长像营销号）
        ln = len(topic.title)
        if 8 <= ln <= 40:
            s += 5.0
            reasons.append("标题长度适中")
        return ScoredTopic(topic=topic, score=round(s, 1), reasons=reasons, matched_keywords=hits)

    # ------------------------------------------------------------------
    def select(self, topics: List[HotTopic]) -> List[ScoredTopic]:
        """主入口：过滤→评分→跨平台去重→按分排序→截断。"""
        cfg = self.config
        if not cfg.enabled:
            return [self.score(t) for t in topics]

        # 1. 过滤
        kept: List[HotTopic] = []
        for t in topics:
            if cfg.min_rank and t.rank and t.rank > cfg.min_rank:
                continue
            if not self._passes_blacklist(t):
                continue
            kept.append(t)

        # 2. 评分
        scored = [self.score(t) for t in kept]

        # 3. 跨平台去重（按规范化标题）
        buckets: Dict[str, List[ScoredTopic]] = defaultdict(list)
        for s in scored:
            key = self._normalize(s.title)
            buckets[key].append(s)
        merged: List[ScoredTopic] = []
        for key, group in buckets.items():
            best = max(group, key=lambda x: x.score)
            if len(group) > 1:
                best.reasons.append(f"跨平台合并 {len(group)} 条")
                best.score = round(min(100.0, best.score + 2.0), 1)
            merged.append(best)

        # 4. 排序截断
        merged.sort(key=lambda x: x.score, reverse=True)
        cutoff = cfg.max_topics
        selected = [s for s in merged if s.score >= cfg.min_score][:cutoff]
        if not selected and merged:  # 阈值过严时回退到分数最高者
            logger.warning("阈值过严，回退取分数最高的 %d 条", cfg.max_topics)
            selected = merged[:cfg.max_topics]
        return selected

    @staticmethod
    def _normalize(title: str) -> str:
        """简单规范化用于去重：去标点/空白/小写。"""
        t = re.sub(r"[\s\W_]+", "", title).lower()
        return t[:30]
