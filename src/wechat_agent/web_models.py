"""Web 管理台 API 请求/响应模型。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class LLMSettingsUpdate(BaseModel):
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = Field(default=None, max_length=512)
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=256, le=32768)
    timeout: Optional[int] = Field(default=None, ge=5, le=600)
    input_price_per_million: Optional[float] = Field(default=None, ge=0, le=100000)
    output_price_per_million: Optional[float] = Field(default=None, ge=0, le=100000)
    outline_model: Optional[str] = None
    refine_model: Optional[str] = None


class WechatSettingsUpdate(BaseModel):
    app_id: Optional[str] = Field(default=None, max_length=128)
    app_secret: Optional[str] = Field(default=None, max_length=512)
    publish_mode: Optional[Literal["draft_only", "freepublish"]] = None


class ImageSettingsUpdate(BaseModel):
    # 新界面只写 pillow/custom；其余值仅用于兼容旧客户端和存量配置。
    source: Optional[Literal["pillow", "custom", "bailian_payg", "bailian_token_plan", "openai", "openai_compatible", "coding_plan"]] = None
    provider: Optional[Literal["pillow", "auto", "dashscope", "openai"]] = None
    model: Optional[str] = None
    base_url: Optional[str] = Field(default=None, max_length=1024)
    api_size: Optional[str] = Field(default=None, max_length=32)
    api_key: Optional[str] = Field(default=None, max_length=512)
    inline_images: Optional[int] = Field(default=None, ge=0, le=5)
    cover_bg: Optional[str] = None
    cover_fg: Optional[str] = None


class SearchSettingsUpdate(BaseModel):
    provider: Optional[Literal["tavily"]] = None
    base_url: Optional[str] = Field(default=None, max_length=1024)
    api_key: Optional[str] = Field(default=None, max_length=512)
    search_depth: Optional[Literal["basic", "advanced"]] = None
    timeout: Optional[int] = Field(default=None, ge=5, le=120)


class NotifySettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    webhook_type: Optional[Literal["feishu", "wecom"]] = None
    webhook_url: Optional[str] = Field(default=None, max_length=1024)


class ScheduleSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    daily_time: Optional[str] = None
    timezone: Optional[str] = None

    @field_validator("daily_time")
    @classmethod
    def validate_time(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        parts = value.split(":")
        if len(parts) != 2 or not all(x.isdigit() for x in parts):
            raise ValueError("时间格式应为 HH:MM")
        hour, minute = map(int, parts)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("时间超出有效范围")
        return f"{hour:02d}:{minute:02d}"


class BillingSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    trial_enabled: Optional[bool] = None
    default_trial_count: Optional[int] = Field(default=None, ge=0, le=100)
    price_per_generation_fen: Optional[int] = Field(default=None, ge=0, le=100000)
    platform_include_images: Optional[bool] = None
    max_target_words: Optional[int] = Field(default=None, ge=500, le=8000)
    payment_enabled: Optional[bool] = None
    payment_provider: Optional[Literal["wechat_native"]] = None
    purchase_options: Optional[List[int]] = Field(default=None, min_length=1, max_length=10)
    order_expire_minutes: Optional[int] = Field(default=None, ge=5, le=60)
    payment_notify_url: Optional[str] = Field(default=None, max_length=500)
    wechat_pay_mch_id: Optional[str] = Field(default=None, max_length=32)
    wechat_pay_serial_no: Optional[str] = Field(default=None, max_length=128)
    wechat_pay_private_key_path: Optional[str] = Field(default=None, max_length=500)
    wechat_pay_public_key_id: Optional[str] = Field(default=None, max_length=128)
    wechat_pay_public_key_path: Optional[str] = Field(default=None, max_length=500)
    wechat_pay_api_v3_key: Optional[str] = Field(default=None, max_length=64)


class RegistrationSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    default_role: Optional[Literal["creator"]] = None
    account_name: Optional[str] = Field(default=None, min_length=2, max_length=30)
    follow_instructions: Optional[str] = Field(default=None, max_length=500)
    private_message_instructions: Optional[str] = Field(default=None, max_length=500)
    qr_image: Optional[str] = Field(default=None, max_length=500)
    application_expire_days: Optional[int] = Field(default=None, ge=1, le=30)
    privacy_notice: Optional[str] = Field(default=None, max_length=1000)


class FeatureSettingsUpdate(BaseModel):
    user_api_config: Optional[bool] = None


class WritingSettingsUpdate(BaseModel):
    account_name: Optional[str] = Field(default=None, min_length=2, max_length=30)
    account_position: Optional[str] = Field(default=None, max_length=500)
    audience: Optional[str] = Field(default=None, max_length=500)
    target_words: Optional[int] = Field(default=None, ge=500, le=8000)
    hot_sources: Optional[List[str]] = None
    whitelist: Optional[List[str]] = None
    blacklist: Optional[List[str]] = None
    max_topics: Optional[int] = Field(default=None, ge=1, le=20)
    min_score: Optional[float] = Field(default=None, ge=0, le=100)


class ConfigUpdateRequest(BaseModel):
    llm: Optional[LLMSettingsUpdate] = None
    wechat: Optional[WechatSettingsUpdate] = None
    image: Optional[ImageSettingsUpdate] = None
    search: Optional[SearchSettingsUpdate] = None
    notify: Optional[NotifySettingsUpdate] = None
    schedule: Optional[ScheduleSettingsUpdate] = None
    registration: Optional[RegistrationSettingsUpdate] = None
    billing: Optional[BillingSettingsUpdate] = None
    features: Optional[FeatureSettingsUpdate] = None
    writing: Optional[WritingSettingsUpdate] = None
    clear_secrets: List[
        Literal[
            "llm_api_key",
            "wechat_app_id",
            "wechat_app_secret",
            "image_api_key",
            "search_api_key",
            "notify_webhook_url",
            "wechat_pay_api_v3_key",
        ]
    ] = Field(default_factory=list)


class UserApiConfigUpdate(BaseModel):
    llm: Optional[LLMSettingsUpdate] = None
    search: Optional[SearchSettingsUpdate] = None
    image: Optional[ImageSettingsUpdate] = None


class HotspotInput(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    source: str = Field(default="manual", max_length=50)
    rank: Optional[int] = None
    heat: Optional[Any] = None
    url: str = ""
    summary: str = Field(default="", max_length=5000)
    score: float = 0.0


class GenerateOptions(BaseModel):
    target_words: Optional[int] = Field(default=None, ge=500, le=8000)
    generation_mode: Literal["platform", "personal"] = "personal"
    with_images: bool = True
    style: Literal["deep", "news", "story"] = "deep"
    reference_material: str = Field(default="", max_length=20000)


class GenerateRequest(BaseModel):
    topic: HotspotInput
    options: GenerateOptions = Field(default_factory=GenerateOptions)


class ArticleShareRequest(BaseModel):
    user_id: str = Field(min_length=16, max_length=64)
    permissions: List[Literal["view", "edit", "push"]]


class ArticleUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=64)
    digest: str = Field(default="", max_length=240)
    content_md: str = Field(min_length=1, max_length=100000)


class PushDraftRequest(BaseModel):
    confirm_reviewed: bool
    confirm_ai_disclosure: bool


class MetricsUpdateRequest(BaseModel):
    read_count: Optional[int] = Field(default=None, ge=0)
    like_count: Optional[int] = Field(default=None, ge=0)
    share_count: Optional[int] = Field(default=None, ge=0)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    phone: str = Field(min_length=11, max_length=24)
    follow_confirmed: bool
    privacy_accepted: bool = False


class RegistrationStatusRequest(BaseModel):
    registration_token: str = Field(min_length=20, max_length=256)


class RegistrationResubmitRequest(BaseModel):
    registration_token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=8, max_length=128)
    phone: str = Field(min_length=11, max_length=24)
    follow_confirmed: bool
    privacy_accepted: bool = False


class RegistrationApproveRequest(BaseModel):
    role: Literal["viewer", "creator", "editor"] = "creator"
    wechat_phone_verified: bool
    note: str = Field(default="", max_length=500)


class RegistrationRejectRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class RegistrationQrUploadRequest(BaseModel):
    data_url: str = Field(min_length=100, max_length=3_000_000)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=512)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["admin", "editor", "creator", "viewer"] = "viewer"


class UserUpdateRequest(BaseModel):
    role: Optional[Literal["admin", "editor", "creator", "viewer"]] = None
    status: Optional[Literal["active", "disabled"]] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=24)


class PurchaseOrderRequest(BaseModel):
    credits: int = Field(ge=1, le=1000)


class CreditAdjustmentRequest(BaseModel):
    delta: int = Field(ge=-10000, le=10000)
    reason: str = Field(min_length=2, max_length=500)


class ApiEnvelope(BaseModel):
    ok: bool = True
    data: Any = None
    message: str = ""
