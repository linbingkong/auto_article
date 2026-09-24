"""配置加载模块。

支持 YAML 配置 + 环境变量覆盖。所有密钥类配置应通过环境变量注入
（.env 文件或系统环境变量），不要硬编码在代码或 YAML 中。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# 项目根目录（src/wechat_agent/config.py -> 项目根）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
DEFAULT_ENV_PATH = PROJECT_ROOT / "config" / ".env"


def load_dotenv(path: Optional[Path] = None) -> None:
    """极简 .env 加载器（避免引入 python-dotenv 依赖，也可换用）。"""
    path = path or DEFAULT_ENV_PATH
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # 不覆盖已存在的环境变量（真实环境优先）
        os.environ.setdefault(key, value)


def _resolve(value: Any) -> Any:
    """把字符串中的 ${ENV_VAR} 占位符解析为环境变量值。"""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            var = value[2:-1]
            if var in os.environ:
                return os.environ[var]
            return ""
        return value
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    return value


@dataclass
class LLMConfig:
    """LLM 配置：OpenAI 兼容接口，本地/外部模型可切换。"""

    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.8
    max_tokens: int = 4096
    timeout: float = 120.0
    # 成本估算单价（元/百万 Token）；按服务商账单填写，不硬编码模型价格。
    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0
    # 多阶段流水线可给不同阶段配不同模型（可选）
    outline_model: Optional[str] = None
    refine_model: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LLMConfig":
        return cls(
            base_url=d.get("base_url", cls.base_url),
            api_key=d.get("api_key", ""),
            model=d.get("model", cls.model),
            temperature=float(d.get("temperature", cls.temperature)),
            max_tokens=int(d.get("max_tokens", cls.max_tokens)),
            timeout=float(d.get("timeout", cls.timeout)),
            input_price_per_million=float(d.get("input_price_per_million", 0) or 0),
            output_price_per_million=float(d.get("output_price_per_million", 0) or 0),
            outline_model=d.get("outline_model"),
            refine_model=d.get("refine_model"),
        )


@dataclass
class ImageConfig:
    """配图配置。公开 source 仅 pillow/custom，provider 由后端自动解析。"""

    source: str = ""
    provider: str = "pillow"
    # 文生图 API 所需
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    # API 生成尺寸（宽x高），原样传给服务商；留空使用服务商/协议默认
    api_size: str = ""
    # 封面尺寸
    cover_width: int = 900
    cover_height: int = 383
    thumb_size: int = 200
    # Pillow 合成时的背景色/文字色（可被 YAML 覆盖）
    cover_bg: str = "#0B2947"
    cover_fg: str = "#FFFFFF"
    # 正文配图数量
    inline_images: int = 1


@dataclass
class SearchConfig:
    """榜单外网页/新闻搜索配置。"""

    provider: str = "tavily"
    base_url: str = "https://api.tavily.com"
    api_key: str = ""
    search_depth: str = "advanced"
    timeout: float = 30.0


@dataclass
class TopicFilterConfig:
    """选题筛选配置。"""

    enabled: bool = True
    max_candidates: int = 10
    max_topics: int = 3
    min_rank: int = 50          # 只保留榜单前 N
    min_score: float = 60.0     # 综合评分阈值
    whitelist: List[str] = field(default_factory=list)   # 账号垂直词（必须命中其一）
    blacklist: List[str] = field(default_factory=list)   # 过滤词
    hot_hours: int = 48         # 热点时效窗口（小时）


@dataclass
class WechatConfig:
    """公众号 API 配置。"""

    app_id: str = ""
    app_secret: str = ""
    # draft_only: 投递草稿箱+通知人工发表（推荐，合规）
    # freepublish: 全自动正式发表（有可见性异常+平台打击风险）
    publish_mode: str = "draft_only"
    # 草稿接口所需的图片域名白名单提示等
    token_cache_file: str = ""


@dataclass
class NotifyConfig:
    """通知配置（企业微信/飞书 webhook）。"""

    enabled: bool = False
    webhook_url: str = ""
    webhook_type: str = "feishu"   # feishu | wecom


@dataclass
class ScheduleConfig:
    """定时调度配置。"""

    enabled: bool = False
    daily_time: str = "08:30"
    timezone: str = "Asia/Shanghai"


@dataclass
class BillingConfig:
    """平台托管生成的试用与按次计费配置。"""

    enabled: bool = True
    trial_enabled: bool = True
    default_trial_count: int = 3
    price_per_generation_fen: int = 200
    platform_include_images: bool = False
    max_target_words: int = 3000
    payment_enabled: bool = False
    payment_provider: str = "wechat_native"
    purchase_options: List[int] = field(default_factory=lambda: [1, 5, 10])
    order_expire_minutes: int = 15
    payment_notify_url: str = ""
    wechat_pay_mch_id: str = ""
    wechat_pay_serial_no: str = ""
    wechat_pay_private_key_path: str = ""
    wechat_pay_public_key_id: str = ""
    wechat_pay_public_key_path: str = ""
    wechat_pay_api_v3_key: str = ""


@dataclass
class RegistrationConfig:
    """公开注册与公众号私信人工审批配置。"""

    enabled: bool = False
    default_role: str = "creator"
    account_name: str = "观思辩明"
    follow_instructions: str = "请使用微信扫码关注“观思辩明”公众号。"
    private_message_instructions: str = "关注后，请在公众号私信中发送注册时填写的完整手机号，管理员核对后批准。"
    qr_image: str = "/assets/account-qr-custom.jpg"
    application_expire_days: int = 7
    privacy_notice: str = "手机号仅用于公众号私信人工核对，不保存明文。"


@dataclass
class FeatureConfig:
    """面向用户的功能开关。"""

    user_api_config: bool = True


@dataclass
class Config:
    """聚合配置对象。"""

    wechat: WechatConfig = field(default_factory=WechatConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    image: ImageConfig = field(default_factory=ImageConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    topic_filter: TopicFilterConfig = field(default_factory=TopicFilterConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    registration: RegistrationConfig = field(default_factory=RegistrationConfig)
    billing: BillingConfig = field(default_factory=BillingConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    account_name: str = "观思辩明"
    account_position: str = "科技资讯与深度分析"
    audience: str = "科技从业者、产品经理"
    target_words: int = 2500
    hot_sources: List[str] = field(default_factory=lambda: ["weibo", "zhihu", "toutiao", "cls"])
    output_dir: str = "output"

    @classmethod
    def load(cls, path: Optional[Path] = None, env_path: Optional[Path] = None) -> "Config":
        load_dotenv(env_path)
        path = path or DEFAULT_CONFIG_PATH
        raw: Dict[str, Any] = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = _resolve(raw)

        cfg = cls()
        if "wechat" in raw:
            w = raw["wechat"]
            cfg.wechat = WechatConfig(
                app_id=w.get("app_id", ""),
                app_secret=w.get("app_secret", ""),
                publish_mode=w.get("publish_mode", "draft_only"),
                token_cache_file=w.get("token_cache_file", ""),
            )
        if "llm" in raw:
            cfg.llm = LLMConfig.from_dict(raw["llm"])
        if "image" in raw:
            img = raw["image"]
            cfg.image = ImageConfig(
                source=img.get("source", ""),
                provider=img.get("provider", "pillow"),
                api_key=img.get("api_key", ""),
                model=img.get("model", ""),
                base_url=img.get("base_url", ""),
                api_size=img.get("api_size", ""),
                cover_width=int(img.get("cover_width", 900)),
                cover_height=int(img.get("cover_height", 383)),
                thumb_size=int(img.get("thumb_size", 200)),
                cover_bg=img.get("cover_bg", "#0B2947"),
                cover_fg=img.get("cover_fg", "#FFFFFF"),
                inline_images=int(img.get("inline_images", 1)),
            )
        if "search" in raw:
            search = raw["search"]
            cfg.search = SearchConfig(
                provider=search.get("provider", "tavily"),
                base_url=search.get("base_url", "https://api.tavily.com"),
                api_key=search.get("api_key", ""),
                search_depth=search.get("search_depth", "advanced"),
                timeout=float(search.get("timeout", 30)),
            )
        if "topic_filter" in raw:
            tf = raw["topic_filter"]
            cfg.topic_filter = TopicFilterConfig(
                enabled=bool(tf.get("enabled", True)),
                max_candidates=int(tf.get("max_candidates", 10)),
                max_topics=int(tf.get("max_topics", 3)),
                min_rank=int(tf.get("min_rank", 50)),
                min_score=float(tf.get("min_score", 60.0)),
                whitelist=list(tf.get("whitelist", [])),
                blacklist=list(tf.get("blacklist", [])),
                hot_hours=int(tf.get("hot_hours", 48)),
            )
        if "notify" in raw:
            n = raw["notify"]
            cfg.notify = NotifyConfig(
                enabled=bool(n.get("enabled", False)),
                webhook_url=n.get("webhook_url", ""),
                webhook_type=n.get("webhook_type", "feishu"),
            )
        if "schedule" in raw:
            s = raw["schedule"]
            cfg.schedule = ScheduleConfig(
                enabled=bool(s.get("enabled", False)),
                daily_time=s.get("daily_time", "08:30"),
                timezone=s.get("timezone", "Asia/Shanghai"),
            )
        if "billing" in raw:
            b = raw["billing"]
            cfg.billing = BillingConfig(
                enabled=bool(b.get("enabled", True)),
                trial_enabled=bool(b.get("trial_enabled", True)),
                default_trial_count=max(0, int(b.get("default_trial_count", 3))),
                price_per_generation_fen=max(0, int(b.get("price_per_generation_fen", 200))),
                platform_include_images=bool(b.get("platform_include_images", False)),
                max_target_words=max(500, int(b.get("max_target_words", 3000))),
                payment_enabled=bool(b.get("payment_enabled", False)),
                payment_provider=str(b.get("payment_provider", "wechat_native")),
                purchase_options=[max(1, int(x)) for x in b.get("purchase_options", [1, 5, 10])][:10],
                order_expire_minutes=max(5, min(60, int(b.get("order_expire_minutes", 15)))),
                payment_notify_url=str(b.get("payment_notify_url", "")),
                wechat_pay_mch_id=str(b.get("wechat_pay_mch_id", "")),
                wechat_pay_serial_no=str(b.get("wechat_pay_serial_no", "")),
                wechat_pay_private_key_path=str(b.get("wechat_pay_private_key_path", "")),
                wechat_pay_public_key_id=str(b.get("wechat_pay_public_key_id", "")),
                wechat_pay_public_key_path=str(b.get("wechat_pay_public_key_path", "")),
                wechat_pay_api_v3_key=str(b.get("wechat_pay_api_v3_key", "")),
            )
        if "registration" in raw:
            r = raw["registration"]
            cfg.registration = RegistrationConfig(
                enabled=bool(r.get("enabled", False)),
                default_role=str(r.get("default_role", "creator")),
                account_name=str(r.get("account_name", "观思辩明")),
                follow_instructions=str(r.get("follow_instructions", RegistrationConfig.follow_instructions)),
                private_message_instructions=str(r.get("private_message_instructions", RegistrationConfig.private_message_instructions)),
                qr_image=str(r.get("qr_image", "")),
                application_expire_days=int(r.get("application_expire_days", 7)),
                privacy_notice=str(r.get("privacy_notice", RegistrationConfig.privacy_notice)),
            )
        if "features" in raw:
            f = raw["features"]
            cfg.features = FeatureConfig(
                user_api_config=bool(f.get("user_api_config", True)),
            )
        cfg.account_name = raw.get("account_name", cfg.account_name)
        cfg.account_position = raw.get("account_position", cfg.account_position)
        cfg.audience = raw.get("audience", cfg.audience)
        cfg.target_words = int(raw.get("target_words", cfg.target_words))
        cfg.hot_sources = list(raw.get("hot_sources", cfg.hot_sources))
        cfg.output_dir = raw.get("output_dir", cfg.output_dir)
        return cfg

    @property
    def output_path(self) -> Path:
        p = PROJECT_ROOT / self.output_dir
        p.mkdir(parents=True, exist_ok=True)
        return p
