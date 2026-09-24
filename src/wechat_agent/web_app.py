"""FastAPI Web 管理台入口。运行：wechat-agent web 或 uvicorn wechat_agent.web_app:app。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging

import markdown as markdown_lib
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

from .article_access import ArticleAccessStore
from .article_store import ArticleNotFoundError, ArticleStore
from .audit import AuditStore
from .auth import AuthError, JWTManager, hash_password
from .billing import BillingStore
from .config import PROJECT_ROOT, load_dotenv
from .database import backend_name, database_target_from_env
from .history import HistoryStore
from .images import ImageGenerator
from .image_sources import image_source_catalog, infer_image_source
from .llm import LLMClient
from .notify import Notifier
from .permissions import extract_user_from_request
from .pipeline import Pipeline
from .search import SearchError, TavilySearchService
from .token_usage import TokenUsageStore
from .user import UserStore
from .user_api_config import UserApiConfigStore
from .wechat_pay import WechatNativePay, payment_ready, payload_hash, qr_data_url
from .web_core import ConfigService, HotspotService, TaskManager
from .web_models import (
    ArticleShareRequest,
    ArticleUpdateRequest,
    ConfigUpdateRequest,
    CreditAdjustmentRequest,
    GenerateRequest,
    LoginRequest,
    MetricsUpdateRequest,
    PurchaseOrderRequest,
    PushDraftRequest,
    RefreshRequest,
    RegisterRequest,
    RegistrationApproveRequest,
    RegistrationQrUploadRequest,
    RegistrationRejectRequest,
    RegistrationResubmitRequest,
    RegistrationStatusRequest,
    UserApiConfigUpdate,
    UserCreateRequest,
    UserUpdateRequest,
)
from .wechat_api import WechatClient

logger = logging.getLogger(__name__)
# 认证对象在模块导入期初始化，因此必须先加载项目 .env。
load_dotenv()
WEB_ROOT = PROJECT_ROOT / "web"

config_service = ConfigService()
DATABASE_TARGET = database_target_from_env(PROJECT_ROOT / "output" / "auth.db")
HISTORY_DATABASE_TARGET = database_target_from_env(PROJECT_ROOT / "output" / "history.db")
history_store = HistoryStore(HISTORY_DATABASE_TARGET)
task_manager = TaskManager(max_workers=2)
hotspot_service = HotspotService(config_service)
article_store = ArticleStore(PROJECT_ROOT / "output" / "web_articles", history_store)

# 认证、审计与计费使用同一个可配置数据库目标。
AUTH_DB_PATH = PROJECT_ROOT / "output" / "auth.db"  # SQLite 兼容路径；生产由 DATABASE_TARGET 覆盖
user_store = UserStore(DATABASE_TARGET)
audit_store = AuditStore(DATABASE_TARGET)
user_api_config_store = UserApiConfigStore(DATABASE_TARGET)
article_access_store = ArticleAccessStore(DATABASE_TARGET)
billing_store = BillingStore(DATABASE_TARGET)
token_usage_store = TokenUsageStore(DATABASE_TARGET)
billing_store.release_stale(120)
_payment_query_cache: Dict[str, float] = {}
_payment_query_lock = threading.Lock()
jwt_manager = JWTManager(os.environ.get("JWT_SECRET", "wechat-agent-dev-secret-change-in-production"))
legacy_admin_token = os.environ.get("WEB_ADMIN_TOKEN", "")

# 公开 API 路径（无需登录）
PUBLIC_API_PATHS = {
    "/api/health", "/api/auth/login", "/api/auth/refresh",
    "/api/auth/register", "/api/auth/registration-status",
    "/api/auth/registration-resubmit", "/api/registration/public-config",
    "/api/payments/wechat/notify",
}
# 仅管理员可写的路径前缀
ADMIN_ONLY_PREFIXES = ("/api/users", "/api/audit", "/api/registrations", "/api/billing/orders", "/api/token-usage")
ADMIN_ONLY_PATHS = {
    ("PUT", "/api/config"),
    ("POST", "/api/config/test/llm"),
    ("POST", "/api/config/test/image"),
    ("POST", "/api/config/test/search"),
    ("POST", "/api/config/test/wechat"),
    ("POST", "/api/config/test/notify"),
    ("POST", "/api/notify/test"),
    ("POST", "/api/schedule/reload"),
}


def check_access(path: str, method: str, role: str) -> bool:
    """按路径与角色检查访问权限。"""
    if path.startswith(ADMIN_ONLY_PREFIXES):
        return role == "admin"
    if method == "GET":
        return True
    if role == "admin":
        return True
    if role == "viewer":
        return False
    if role not in {"editor", "creator"}:
        return False
    # editor/creator：拒绝管理员专属操作；creator 额外不能推送公众号草稿。
    if (method, path) in ADMIN_ONLY_PATHS or path.startswith("/api/config/test/"):
        return False
    if role == "creator" and method == "POST" and path.startswith("/api/articles/") and path.endswith("/push"):
        return False
    return True


class ScheduleController:
    def __init__(self) -> None:
        self.scheduler: Optional[Any] = None
        self.error = ""

    def reload(self) -> Dict[str, Any]:
        if self.scheduler:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
            self.scheduler = None
        cfg = config_service.load()
        self.error = ""
        if not cfg.schedule.enabled:
            return self.status()
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger

            hour, minute = cfg.schedule.daily_time.split(":", 1)
            scheduler = BackgroundScheduler(timezone=cfg.schedule.timezone)
            scheduler.add_job(
                self._daily_job,
                CronTrigger(
                    hour=int(hour), minute=int(minute), timezone=cfg.schedule.timezone
                ),
                id="daily_publish",
                replace_existing=True,
                max_instances=1,
            )
            scheduler.start()
            self.scheduler = scheduler
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            logger.exception("failed to start web scheduler")
        return self.status()

    @staticmethod
    def _daily_job() -> None:
        cfg = config_service.load()
        Pipeline(cfg).run(max_articles=1, dry_run=False)

    def status(self) -> Dict[str, Any]:
        cfg = config_service.load()
        jobs = []
        if self.scheduler:
            jobs = [
                {
                    "id": job.id,
                    "next_run_time": (
                        job.next_run_time.isoformat(timespec="seconds")
                        if job.next_run_time
                        else ""
                    ),
                }
                for job in self.scheduler.get_jobs()
            ]
        return {
            "configured": cfg.schedule.enabled,
            "running": self.scheduler is not None,
            "daily_time": cfg.schedule.daily_time,
            "timezone": cfg.schedule.timezone,
            "jobs": jobs,
            "error": self.error,
        }


schedule_controller = ScheduleController()
_rate_events: Dict[str, List[float]] = {}
_rate_lock = threading.Lock()


def rate_allowed(key: str, limit: int = 5, window: int = 60) -> bool:
    """限制昂贵操作，避免连续点击耗尽模型/公众号配额。"""
    now = time.monotonic()
    with _rate_lock:
        events = [stamp for stamp in _rate_events.get(key, []) if now - stamp < window]
        if len(events) >= limit:
            _rate_events[key] = events
            return False
        events.append(now)
        _rate_events[key] = events
        return True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        bootstrap = user_store.ensure_admin_exists()
        if bootstrap and bootstrap.get("generated_password"):
            logger.warning(
                "initial admin created; username=admin one-time-password=%s (change it immediately)",
                bootstrap["generated_password"],
            )
        else:
            logger.info("auth database ready; active admin available")
        admin = next((item for item in user_store.list_users(limit=1000) if item["role"] == "admin" and item["status"] == "active"), None)
        if admin:
            migrated = article_store.assign_legacy_owner(admin["id"], admin["username"])
            if migrated:
                logger.info("assigned %s legacy articles to admin", migrated)
    except Exception:  # noqa: BLE001
        logger.exception("failed to initialize auth database")
    schedule_controller.reload()
    yield
    if schedule_controller.scheduler:
        schedule_controller.scheduler.shutdown(wait=False)


app = FastAPI(
    title="观思辩明 · 公众号智能体管理台",
    version="0.2.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


def ok(data: Any = None, message: str = "") -> Dict[str, Any]:
    return {"ok": True, "data": data, "message": message}


def _asset_signature(article_id: str, name: str, user_id: str, expires: int) -> str:
    message = f"{article_id}:{name}:{user_id}:{expires}".encode()
    return hmac.new(jwt_manager.secret.encode(), message, hashlib.sha256).hexdigest()


def _signed_asset_url(article_id: str, name: str, user_id: str) -> str:
    expires = int(time.time()) + 900
    sig = _asset_signature(article_id, name, user_id, expires)
    return f"/api/articles/{article_id}/assets/{name}?user_id={user_id}&expires={expires}&sig={sig}"


def _signed_asset_user(request: Request) -> Optional[Dict[str, Any]]:
    parts = request.url.path.split("/")
    if len(parts) != 6 or parts[1:3] != ["api", "articles"] or parts[4] != "assets":
        return None
    article_id, name = parts[3], parts[5]
    user_id = request.query_params.get("user_id", "")
    try: expires = int(request.query_params.get("expires", "0"))
    except ValueError: return None
    sig = request.query_params.get("sig", "")
    if expires < time.time() or not hmac.compare_digest(sig, _asset_signature(article_id, name, user_id, expires)):
        return None
    user = user_store.get_user(user_id)
    if not user or user["status"] != "active": return None
    return {"id": user["id"], "username": user["username"], "role": user["role"]}


def _require_article_access(article_id: str, user: Dict[str, Any], action: str) -> Dict[str, Any]:
    try:
        meta = article_store.metadata(article_id)
    except ArticleNotFoundError:
        raise HTTPException(status_code=404, detail="文章不存在") from None
    if not article_access_store.allowed(meta, user, action):
        raise HTTPException(status_code=403, detail=f"无权{ {'view':'查看','edit':'编辑','push':'推送','delete':'删除'}.get(action, action) }该文章")
    return meta


def _visible_articles(user: Dict[str, Any], limit: int = 200) -> List[Dict[str, Any]]:
    result = []
    for item in article_store.list(limit=100000):
        if article_access_store.allowed(item, user, "view"):
            copy = dict(item)
            copy["access"] = {action: article_access_store.allowed(item, user, action) for action in ("view", "edit", "push", "delete")}
            copy["cover_url"] = _signed_asset_url(item["id"], "cover.png", user["id"])
            result.append(copy)
    return result[:limit]


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    signed_asset_user = _signed_asset_user(request)
    if signed_asset_user:
        request.state.user = signed_asset_user
    if path.startswith("/api/") and path not in PUBLIC_API_PATHS and not signed_asset_user:
        admin_token = os.environ.get("WEB_ADMIN_TOKEN", "")
        user = extract_user_from_request(request, jwt_manager, admin_token)
        if not user and not admin_token and os.environ.get("AUTH_TEST_BYPASS") == "1":
            user = {"id": "test-admin", "username": "test-admin", "role": "admin"}
        if not user:
            return JSONResponse(
                status_code=401,
                content={
                    "ok": False,
                    "code": "AUTH_REQUIRED",
                    "message": "未登录或凭证无效，请重新登录",
                },
            )
        if user["id"] not in {"legacy-admin", "test-admin"}:
            current = user_store.get_user(user["id"])
            if not current or current["status"] != "active":
                return JSONResponse(
                    status_code=401,
                    content={"ok": False, "code": "ACCOUNT_UNAVAILABLE", "message": "账号不可用，请重新登录"},
                )
            user = {"id": current["id"], "username": current["username"], "role": current["role"]}
        request.state.user = user
        if not check_access(path, request.method, user["role"]):
            return JSONResponse(
                status_code=403,
                content={
                    "ok": False,
                    "message": f"权限不足：当前角色 {user['role']} 无权执行该操作",
                },
            )
    if request.method == "POST" and (
        request.url.path == "/api/generate" or request.url.path.endswith("/push")
    ):
        client_ip = request.client.host if request.client else "unknown"
        if not rate_allowed(f"{client_ip}:{request.url.path}"):
            return JSONResponse(
                status_code=429,
                content={"ok": False, "message": "操作过于频繁，请一分钟后再试"},
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "clipboard-read=(self), clipboard-write=(self)"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'self'"
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"ok": False, "message": "请求参数校验失败", "details": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_error(_request: Request, exc: Exception):
    logger.exception("web api error")
    return JSONResponse(
        status_code=500,
        content={"ok": False, "message": str(exc)[:500] or "服务器内部错误"},
    )


@app.get("/api/health")
def health() -> Dict[str, Any]:
    cfg = config_service.load()
    return ok(
        {
            "service": "wechat-agent-web",
            "version": "0.2.0",
            "admin_token_required": bool(legacy_admin_token),
            "auth_required": True,
            "database_backend": backend_name(DATABASE_TARGET),
            "llm_configured": bool(cfg.llm.base_url and cfg.llm.model),
            "wechat_configured": bool(cfg.wechat.app_id and cfg.wechat.app_secret),
        }
    )


@app.get("/api/help/manual")
def help_manual() -> Dict[str, Any]:
    """读取仓库中的使用手册，并转换为站内可展示的 HTML。"""
    manual_path = PROJECT_ROOT / "docs" / "USER_MANUAL.md"
    if not manual_path.exists():
        raise HTTPException(status_code=404, detail="使用手册尚未生成")
    source = manual_path.read_text(encoding="utf-8-sig")
    source = source.replace("(assets/user-manual/", "(/manual-assets/")
    renderer = markdown_lib.Markdown(
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        output_format="html5",
    )
    content = renderer.convert(source)
    return ok(
        {
            "html": content,
            "toc": renderer.toc,
            "updated_at": manual_path.stat().st_mtime,
        }
    )


@app.get("/manual-assets/{name}")
def manual_asset(name: str):
    if not name.lower().endswith(".png") or Path(name).name != name:
        raise HTTPException(status_code=404, detail="手册图片不存在")
    path = PROJECT_ROOT / "docs" / "assets" / "user-manual" / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="手册图片不存在")
    return FileResponse(path, media_type="image/png")


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _record_audit(request: Request, action: str, resource: Optional[str] = None,
                  resource_id: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> None:
    user = getattr(request.state, "user", None)
    audit_store.record(
        user_id=user.get("id") if user else None,
        username=user.get("username") if user else None,
        action=action,
        resource=resource,
        resource_id=resource_id,
        detail=detail,
        ip=_client_ip(request),
        user_agent=request.headers.get("User-Agent", "")[:300],
    )


@app.get("/api/registration/public-config")
def registration_public_config() -> Dict[str, Any]:
    cfg = config_service.load().registration
    return ok(
        {
            "enabled": cfg.enabled,
            "account_name": cfg.account_name,
            "follow_instructions": cfg.follow_instructions,
            "private_message_instructions": cfg.private_message_instructions,
            "qr_image": cfg.qr_image,
        }
    )


@app.post("/api/auth/register")
def auth_register(body: RegisterRequest, request: Request):
    cfg = config_service.load().registration
    if not cfg.enabled:
        return JSONResponse(
            status_code=403,
            content={"ok": False, "code": "REGISTRATION_DISABLED", "message": "公开注册尚未开启"},
        )
    if not body.follow_confirmed:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": "FOLLOW_NOT_CONFIRMED", "message": "请先扫码关注“观思辩明”公众号"},
        )
    ip = _client_ip(request)
    if not rate_allowed(f"register:{ip}", limit=3, window=3600):
        return JSONResponse(
            status_code=429,
            content={"ok": False, "code": "REGISTER_RATE_LIMIT", "message": "注册操作过于频繁，请稍后再试"},
        )
    try:
        data = user_store.create_registration(body.username, body.password, body.phone)
    except AuthError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": exc.code, "message": str(exc)},
        )
    audit_store.record(
        user_id=data["id"], username=data["username"], action="registration.created",
        resource="registration", resource_id=data["id"],
        detail={"phone_last4": data["masked_phone"][-4:]}, ip=ip,
        user_agent=request.headers.get("User-Agent", "")[:300],
    )
    data.update(
        {
            "account_name": cfg.account_name,
            "follow_instructions": cfg.follow_instructions,
            "private_message_instructions": cfg.private_message_instructions,
            "qr_image": cfg.qr_image,
        }
    )
    return ok(data, "注册申请已提交，请关注公众号并私信注册手机号")


@app.post("/api/auth/registration-status")
def auth_registration_status(body: RegistrationStatusRequest, request: Request):
    try:
        data = user_store.registration_status(body.registration_token)
    except AuthError as exc:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "code": exc.code, "message": str(exc)},
        )
    audit_store.record(
        username=data["username"], action="registration.status_viewed",
        resource="registration", detail={"status": data["status"]},
        ip=_client_ip(request), user_agent=request.headers.get("User-Agent", "")[:300],
    )
    return ok(data)


@app.post("/api/auth/registration-resubmit")
def auth_registration_resubmit(body: RegistrationResubmitRequest, request: Request):
    if not body.follow_confirmed:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": "FOLLOW_NOT_CONFIRMED", "message": "请先扫码关注“观思辩明”公众号"},
        )
    try:
        data = user_store.resubmit_registration(
            body.registration_token, body.phone, body.password
        )
    except AuthError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": exc.code, "message": str(exc)},
        )
    audit_store.record(
        user_id=data["id"], username=data["username"], action="registration.resubmitted",
        resource="registration", resource_id=data["id"],
        detail={"phone_last4": data["masked_phone"][-4:]}, ip=_client_ip(request),
        user_agent=request.headers.get("User-Agent", "")[:300],
    )
    return ok(data, "申请已重新提交")


@app.post("/api/auth/login")
def auth_login(body: LoginRequest, request: Request) -> Dict[str, Any]:
    try:
        user = user_store.authenticate(body.username, body.password)
    except AuthError as exc:
        audit_store.record(
            username=body.username, action="login.failed", resource="auth",
            detail={"reason": str(exc)}, ip=_client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:300],
        )
        return JSONResponse(
            status_code=401,
            content={"ok": False, "code": exc.code, "message": str(exc)},
        )
    access = jwt_manager.create_access_token(user["id"], user["username"], user["role"])
    refresh = jwt_manager.create_refresh_token(user["id"])
    audit_store.record(
        user_id=user["id"], username=user["username"], action="login", resource="auth",
        ip=_client_ip(request), user_agent=request.headers.get("User-Agent", "")[:300],
    )
    return ok(
        {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": jwt_manager.access_expire,
            "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
        },
        "登录成功",
    )


@app.post("/api/auth/refresh")
def auth_refresh(body: RefreshRequest) -> Dict[str, Any]:
    try:
        payload = jwt_manager.verify_token(body.refresh_token, expected_type="refresh")
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    user = user_store.get_user(payload["sub"])
    if not user or user["status"] != "active":
        raise HTTPException(status_code=401, detail="账号不可用，请重新登录")
    access = jwt_manager.create_access_token(user["id"], user["username"], user["role"])
    refresh = jwt_manager.create_refresh_token(user["id"])
    return ok(
        {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": jwt_manager.access_expire,
            "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
        }
    )


@app.post("/api/auth/logout")
def auth_logout(request: Request, body: RefreshRequest) -> Dict[str, Any]:
    try:
        payload = jwt_manager.verify_token(body.refresh_token, expected_type="refresh")
        jwt_manager.revoke_token(payload["jti"])
    except AuthError:
        pass
    _record_audit(request, "logout", resource="auth")
    return ok(message="已退出登录")


@app.get("/api/auth/me")
def auth_me(request: Request) -> Dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return ok({
        "id": user["id"], "username": user["username"], "role": user["role"],
        "features": {"user_api_config": bool(config_service.load().features.user_api_config)},
    })


@app.post("/api/registrations/qr")
def upload_registration_qr(
    body: RegistrationQrUploadRequest, request: Request
) -> Dict[str, Any]:
    try:
        header, encoded = body.data_url.split(",", 1)
        if header not in {
            "data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"
        }:
            raise ValueError("unsupported type")
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("too large")
        with Image.open(io.BytesIO(raw)) as source:
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            if source.width < 100 or source.height < 100 or source.width > 4096 or source.height > 4096:
                raise ValueError("invalid dimensions")
            image = source.convert("RGBA" if source.mode == "RGBA" else "RGB")
            target = WEB_ROOT / "assets" / "account-qr-custom.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, format="PNG", optimize=True)
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": "INVALID_QR_IMAGE", "message": "请上传 100-4096 像素、2MB 以内的 PNG/JPEG/WebP 图片"},
        )
    _record_audit(request, "registration.qr_updated", resource="registration")
    return ok({"url": "/assets/account-qr-custom.png"}, "公众号二维码已上传")


@app.get("/api/registrations")
def list_registrations(
    request: Request,
    status: str = Query(default="pending_approval", pattern="^(pending_approval|rejected|active)$"),
    phone: str = Query(default="", max_length=24),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    try:
        if phone.strip():
            match = user_store.find_registration_by_phone(phone)
            items = [match] if match else []
            _record_audit(
                request, "registration.phone_searched", resource="registration",
                detail={"matched": bool(match), "phone_last4": user_store.normalize_phone(phone)[-4:]},
            )
        else:
            items = user_store.list_registrations(status=status, limit=limit, offset=offset)
    except AuthError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": exc.code, "message": str(exc)},
        )
    return ok(
        {
            "items": items,
            "total": len(items) if phone.strip() else user_store.count_registrations(status),
            "pending_total": user_store.count_registrations("pending_approval"),
        }
    )


@app.post("/api/registrations/{user_id}/approve")
def approve_registration(
    user_id: str, body: RegistrationApproveRequest, request: Request
) -> Dict[str, Any]:
    if not body.wechat_phone_verified:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "code": "WECHAT_PHONE_NOT_VERIFIED",
                "message": "批准前必须确认已在公众号私信中核对手机号",
            },
        )
    actor = request.state.user
    registration = user_store.get_user(user_id)
    try:
        user = user_store.approve_registration(
            user_id, actor["id"], role=body.role, note=body.note
        )
        cfg = config_service.load()
        trial_count = cfg.billing.default_trial_count if cfg.billing.enabled and cfg.billing.trial_enabled and body.role in {"creator", "editor"} else 0
        quota = billing_store.grant_trial(user_id, (registration or {}).get("phone_hash", ""), trial_count)
    except AuthError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": exc.code, "message": str(exc)},
        )
    _record_audit(
        request, "registration.approved", resource="registration", resource_id=user_id,
        detail={"username": user["username"], "role": user["role"], "phone_last4": user.get("phone_last4"), "trial_granted": quota["trial_total"]},
    )
    user["billing"] = quota
    return ok(user, "注册申请已批准")


@app.post("/api/registrations/{user_id}/reject")
def reject_registration(
    user_id: str, body: RegistrationRejectRequest, request: Request
) -> Dict[str, Any]:
    actor = request.state.user
    try:
        user = user_store.reject_registration(user_id, actor["id"], body.reason)
    except AuthError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": exc.code, "message": str(exc)},
        )
    _record_audit(
        request, "registration.rejected", resource="registration", resource_id=user_id,
        detail={"username": user["username"], "phone_last4": user.get("phone_last4"), "reason": body.reason[:200]},
    )
    return ok(user, "注册申请已拒绝")


@app.get("/api/me/api-config")
def get_my_api_config(request: Request) -> Dict[str, Any]:
    user = request.state.user
    if user["role"] != "admin" and not config_service.load().features.user_api_config:
        return JSONResponse(status_code=403, content={"ok": False, "code": "PERSONAL_API_DISABLED", "message": "管理员已关闭「我的 API 配置」功能，请使用平台生成"})
    try:
        snapshot = user_api_config_store.snapshot(user["id"], user["role"])
    except AuthError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "code": exc.code, "message": str(exc)})
    base = config_service.load()
    snapshot["defaults"] = {
        "llm": {"base_url": base.llm.base_url, "model": base.llm.model, "temperature": base.llm.temperature, "max_tokens": base.llm.max_tokens, "timeout": base.llm.timeout},
        "search": {"provider": "tavily", "base_url": base.search.base_url, "search_depth": base.search.search_depth, "timeout": base.search.timeout},
        "image": {"source": infer_image_source(base.image.provider, base.image.base_url, base.image.source), "provider": base.image.provider, "model": base.image.model, "base_url": base.image.base_url, "inline_images": base.image.inline_images, "source_catalog": image_source_catalog()}, 
    }
    return ok(snapshot)


@app.put("/api/me/api-config")
def update_my_api_config(body: UserApiConfigUpdate, request: Request) -> Dict[str, Any]:
    user = request.state.user
    if user["role"] not in {"admin", "editor", "creator"}:
        return JSONResponse(status_code=403, content={"ok": False, "code": "ROLE_FORBIDDEN", "message": "只读账号不能配置外部服务"})
    if user["role"] != "admin" and not config_service.load().features.user_api_config:
        return JSONResponse(status_code=403, content={"ok": False, "code": "PERSONAL_API_DISABLED", "message": "管理员已关闭「我的 API 配置」功能，请使用平台生成"})
    changes = body.model_dump(exclude_none=True)
    try:
        snapshot = user_api_config_store.update(user["id"], changes)
        snapshot = user_api_config_store.snapshot(user["id"], user["role"])
    except AuthError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "code": exc.code, "message": str(exc)})
    _record_audit(request, "user_api_config.update", resource="user_api_config", resource_id=user["id"], detail={"services": list(changes)})
    return ok(snapshot, "个人 API 配置已加密保存")


@app.delete("/api/me/api-config/{service}")
def clear_my_api_config(service: str, request: Request) -> Dict[str, Any]:
    user = request.state.user
    if user["role"] != "admin" and not config_service.load().features.user_api_config:
        return JSONResponse(status_code=403, content={"ok": False, "code": "PERSONAL_API_DISABLED", "message": "管理员已关闭「我的 API 配置」功能，请使用平台生成"})
    try:
        user_api_config_store.clear(user["id"], service)
        snapshot = user_api_config_store.snapshot(user["id"], user["role"])
    except AuthError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "code": exc.code, "message": str(exc)})
    _record_audit(request, "user_api_config.clear", resource="user_api_config", resource_id=user["id"], detail={"service": service})
    return ok(snapshot, "个人配置已清除")


@app.post("/api/me/api-config/test/{service}")
def test_my_api_config(service: str, request: Request) -> Dict[str, Any]:
    user = request.state.user
    if service not in {"llm", "search", "image"}:
        raise HTTPException(status_code=404, detail="不支持的服务")
    if user["role"] != "admin" and not config_service.load().features.user_api_config:
        return JSONResponse(status_code=403, content={"ok": False, "code": "PERSONAL_API_DISABLED", "message": "管理员已关闭「我的 API 配置」功能，请使用平台生成"})
    try:
        cfg, sources = user_api_config_store.effective_config(config_service.load(), user, [service])
        if service == "llm":
            result = {"reply": LLMClient(cfg.llm).chat([{"role": "user", "content": "仅回复 OK"}], temperature=0, max_tokens=64)[:100]}
        elif service == "search":
            result = TavilySearchService(cfg.search).validate_connection()
        else:
            result = ImageGenerator(cfg).validate_connection()
        _record_audit(request, "user_api_config.test", resource="user_api_config", resource_id=user["id"], detail={"service": service, "source": sources.get(service)})
        return ok({**result, "source": sources.get(service)}, "个人服务连接成功")
    except (AuthError, SearchError, ValueError, RuntimeError, OSError) as exc:
        code = exc.code if isinstance(exc, AuthError) else "USER_CONFIG_TEST_FAILED"
        return JSONResponse(status_code=400, content={"ok": False, "code": code, "message": str(exc)})


@app.get("/api/users/{user_id}/api-config-status")
def get_user_api_config_status(user_id: str, request: Request) -> Dict[str, Any]:
    if not user_store.get_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    _record_audit(request, "user_api_config.status_view", resource="user_api_config", resource_id=user_id)
    return ok(user_api_config_store.status(user_id))


@app.delete("/api/users/{user_id}/api-config")
def admin_clear_user_api_config(user_id: str, request: Request) -> Dict[str, Any]:
    if not user_store.get_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        user_api_config_store.clear(user_id)
    except AuthError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "code": exc.code, "message": str(exc)})
    _record_audit(request, "user_api_config.admin_clear", resource="user_api_config", resource_id=user_id)
    return ok(user_api_config_store.status(user_id), "该用户的个人 API 配置已清除")


def _public_order(order: Dict[str, Any]) -> Dict[str, Any]:
    return {key: order.get(key) for key in ("order_no", "amount_fen", "credit_count", "unit_price_fen", "status", "provider", "provider_transaction_id", "created_at", "expires_at", "paid_at", "updated_at")}


@app.get("/api/me/billing")
def get_my_billing(request: Request) -> Dict[str, Any]:
    cfg = config_service.load()
    pay_ready, _ = payment_ready(cfg)
    user_id = request.state.user["id"]
    account = billing_store.account(user_id)
    return ok({"account": account, "usage": billing_store.usage(user_id), "orders": [_public_order(item) for item in billing_store.list_orders(user_id=user_id, limit=30)], "pricing": {"enabled": cfg.billing.enabled, "price_per_generation_fen": cfg.billing.price_per_generation_fen, "platform_include_images": cfg.billing.platform_include_images, "max_target_words": cfg.billing.max_target_words, "payment_enabled": bool(cfg.billing.payment_enabled and pay_ready), "purchase_options": cfg.billing.purchase_options}})


@app.post("/api/me/billing/orders")
def create_payment_order(body: PurchaseOrderRequest, request: Request) -> Dict[str, Any]:
    cfg = config_service.load()
    ready, reason = payment_ready(cfg)
    if not cfg.billing.payment_enabled or not ready:
        return JSONResponse(status_code=503, content={"ok": False, "code": "WECHAT_PAY_DISABLED", "message": "在线支付暂未开放" if not reason else f"在线支付暂未就绪：{reason}"})
    if body.credits not in cfg.billing.purchase_options:
        return JSONResponse(status_code=400, content={"ok": False, "code": "PURCHASE_OPTION_INVALID", "message": "请选择管理员配置的购买次数"})
    user_id = request.state.user["id"]
    pending = [item for item in billing_store.list_orders(user_id=user_id, status="pending", limit=10)]
    if len(pending) >= 3:
        return JSONResponse(status_code=429, content={"ok": False, "code": "TOO_MANY_PENDING_ORDERS", "message": "未支付订单过多，请完成支付或等待订单过期"})
    order = billing_store.create_order(user_id, body.credits, cfg.billing.price_per_generation_fen, cfg.billing.order_expire_minutes)
    try:
        code_url = WechatNativePay(cfg).create_native(order["order_no"], order["amount_fen"], f"观思辩明文章生成额度×{body.credits}")
        order = billing_store.set_order_code_url(order["order_no"], code_url)
        qr_image = qr_data_url(code_url)
    except AuthError as exc:
        billing_store.fail_order(order["order_no"])
        return JSONResponse(status_code=502, content={"ok": False, "code": exc.code, "message": str(exc)})
    _record_audit(request, "payment.order_created", resource="payment_order", resource_id=order["order_no"], detail={"credits": body.credits, "amount_fen": order["amount_fen"]})
    return ok({"order": _public_order(order), "qr_image": qr_image}, "微信支付订单已创建")


def _reconcile_payment_order(order: Dict[str, Any]) -> Dict[str, Any]:
    if order.get("status") != "pending":
        return order
    now = time.time()
    with _payment_query_lock:
        if now - _payment_query_cache.get(order["order_no"], 0) < 8:
            return order
        _payment_query_cache[order["order_no"]] = now
    cfg = config_service.load()
    if not payment_ready(cfg)[0]:
        return order
    try:
        transaction = WechatNativePay(cfg).query(order["order_no"])
        if transaction.get("out_trade_no") != order["order_no"] or transaction.get("mchid") != cfg.billing.wechat_pay_mch_id or transaction.get("appid") != cfg.wechat.app_id:
            raise AuthError("微信支付查单结果身份不匹配", "PAYMENT_QUERY_IDENTITY_MISMATCH")
        state = transaction.get("trade_state")
        if state == "SUCCESS":
            amount = transaction.get("amount") or {}
            raw = json.dumps(transaction, ensure_ascii=False, sort_keys=True).encode("utf-8")
            return billing_store.mark_order_paid(order["order_no"], str(transaction.get("transaction_id", "")), int(amount.get("total", -1)), f"query:{transaction.get('transaction_id', '')}", payload_hash(raw))
        if state in {"CLOSED", "REVOKED"}:
            return billing_store.close_order(order["order_no"])
        if state == "PAYERROR":
            return billing_store.fail_order(order["order_no"])
    except AuthError as exc:
        logger.warning("微信支付主动查单失败：%s", str(exc)[:300])
    return billing_store.get_order(order["order_no"]) or order


@app.get("/api/me/billing/orders/{order_no}")
def get_payment_order(order_no: str, request: Request) -> Dict[str, Any]:
    order = billing_store.get_order(order_no, request.state.user["id"])
    if not order:
        raise HTTPException(status_code=404, detail="支付订单不存在")
    return ok(_public_order(_reconcile_payment_order(order)))


@app.post("/api/me/billing/orders/{order_no}/resume")
def resume_payment_order(order_no: str, request: Request) -> Dict[str, Any]:
    order = billing_store.get_order(order_no, request.state.user["id"])
    if not order:
        raise HTTPException(status_code=404, detail="支付订单不存在")
    if order["status"] != "pending" or not order.get("code_url"):
        return JSONResponse(status_code=409, content={"ok": False, "code": "PAYMENT_ORDER_NOT_PAYABLE", "message": "该订单已不能继续支付"})
    try:
        image = qr_data_url(order["code_url"])
    except AuthError as exc:
        return JSONResponse(status_code=500, content={"ok": False, "code": exc.code, "message": str(exc)})
    return ok({"order": _public_order(order), "qr_image": image})


@app.post("/api/me/billing/orders/{order_no}/close")
def close_payment_order(order_no: str, request: Request) -> Dict[str, Any]:
    order = billing_store.get_order(order_no, request.state.user["id"])
    if not order:
        raise HTTPException(status_code=404, detail="支付订单不存在")
    if order["status"] == "pending":
        cfg = config_service.load()
        try:
            if payment_ready(cfg)[0]:
                WechatNativePay(cfg).close(order_no)
        except AuthError as exc:
            return JSONResponse(status_code=502, content={"ok": False, "code": exc.code, "message": str(exc)})
        order = billing_store.close_order(order_no)
    return ok(_public_order(order), "订单已关闭")


@app.post("/api/payments/wechat/notify")
async def wechat_payment_notify(request: Request):
    raw = await request.body()
    try:
        cfg = config_service.load()
        envelope, transaction = WechatNativePay(cfg).verify_notification(request.headers, raw)
        if envelope.get("event_type") != "TRANSACTION.SUCCESS" or transaction.get("trade_state") != "SUCCESS":
            return JSONResponse(status_code=200, content={"code": "SUCCESS", "message": "非成功交易无需入账"})
        if transaction.get("mchid") != cfg.billing.wechat_pay_mch_id or transaction.get("appid") != cfg.wechat.app_id:
            raise AuthError("支付通知商户号或 AppID 不匹配", "PAYMENT_MERCHANT_MISMATCH")
        if not envelope.get("id") or not transaction.get("out_trade_no") or not transaction.get("transaction_id"):
            raise AuthError("支付通知缺少订单或交易标识", "PAYMENT_IDENTIFIERS_MISSING")
        amount = transaction.get("amount") or {}
        order = billing_store.mark_order_paid(str(transaction["out_trade_no"]), str(transaction["transaction_id"]), int(amount.get("total", -1)), str(envelope["id"]), payload_hash(raw))
        audit_store.record(user_id=order["user_id"], username="wechat-pay", action="payment.paid", resource="payment_order", resource_id=order["order_no"], detail={"amount_fen": order["amount_fen"], "credits": order["credit_count"], "transaction_id": order.get("provider_transaction_id", "")}, ip=_client_ip(request), user_agent="WechatPay-Callback")
        return JSONResponse(status_code=200, content={"code": "SUCCESS", "message": "成功"})
    except (AuthError, ValueError, KeyError) as exc:
        logger.warning("微信支付通知处理失败：%s", str(exc)[:300])
        return JSONResponse(status_code=500, content={"code": "FAIL", "message": "支付通知处理失败"})


@app.get("/api/billing/orders")
def admin_list_payment_orders(request: Request, status: str = Query(default="", max_length=20), limit: int = Query(default=100, ge=1, le=500)) -> Dict[str, Any]:
    users = {item["id"]: item["username"] for item in user_store.list_users(limit=1000)}
    items = [{**_public_order(item), "user_id": item["user_id"], "username": users.get(item["user_id"], "未知用户")} for item in billing_store.list_orders(status=status, limit=limit)]
    return ok({"items": items, "total": len(items)})


@app.get("/api/users/{user_id}/billing")
def get_user_billing(user_id: str, request: Request) -> Dict[str, Any]:
    if not user_store.get_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return ok({"account": billing_store.account(user_id), "usage": billing_store.usage(user_id), "adjustments": billing_store.adjustments(user_id)})


@app.post("/api/users/{user_id}/billing/credits")
def adjust_user_credits(user_id: str, body: CreditAdjustmentRequest, request: Request) -> Dict[str, Any]:
    if not user_store.get_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        account = billing_store.adjust_paid(user_id, body.delta, request.state.user["id"], body.reason)
    except AuthError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "code": exc.code, "message": str(exc)})
    _record_audit(request, "billing.credits_adjusted", resource="billing", resource_id=user_id, detail={"delta": body.delta, "balance_after": account["paid_credits"], "reason": body.reason[:200]})
    return ok(account, "付费生成额度已调整")


@app.get("/api/users")
def list_users(request: Request) -> Dict[str, Any]:
    users = user_store.list_users()
    statuses = user_api_config_store.statuses(user["id"] for user in users)
    for user in users:
        user["api_config"] = statuses[user["id"]]
        user["billing"] = billing_store.account(user["id"])
    _record_audit(request, "user.list", resource="user")
    return ok({"users": users, "total": user_store.count_users()})


@app.post("/api/users")
def create_user(body: UserCreateRequest, request: Request) -> Dict[str, Any]:
    try:
        user = user_store.create_user(body.username, body.password, role=body.role)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _record_audit(request, "user.create", resource="user", resource_id=user["id"],
                  detail={"username": user["username"], "role": user["role"]})
    return ok(user, "用户已创建")


@app.put("/api/users/{user_id}")
def update_user(user_id: str, body: UserUpdateRequest, request: Request) -> Dict[str, Any]:
    try:
        user = user_store.update_user(
            user_id, role=body.role, status=body.status, password=body.password,
            phone=body.phone,
        )
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _record_audit(request, "user.update", resource="user", resource_id=user_id,
                  detail={"role": body.role, "status": body.status,
                          "password_changed": bool(body.password),
                          "phone_last4": (body.phone or "").strip()[-4:] or None})
    return ok(
        {key: user[key] for key in ("id", "username", "role", "status", "created_at", "updated_at")},
        "用户信息已更新",
    )


@app.delete("/api/users/{user_id}")
def delete_user_endpoint(user_id: str, request: Request) -> Dict[str, Any]:
    actor = request.state.user
    if user_id == actor["id"]:
        return JSONResponse(status_code=400, content={"ok": False, "code": "SELF_DELETE_FORBIDDEN", "message": "不能删除当前登录的账号"})
    try:
        user = user_store.delete_user(user_id)
    except AuthError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "code": exc.code, "message": str(exc)})
    try:
        user_api_config_store.clear(user_id)
    except AuthError:
        pass
    article_access_store.revoke_user(user_id)
    try:
        billing_store.delete_account(user_id)
    except Exception:  # noqa: BLE001
        logger.exception("failed to remove billing account for deleted user %s", user_id)
    reassigned = article_store.reassign_owner(user_id, actor["id"], actor["username"])
    _record_audit(request, "user.delete", resource="user", resource_id=user_id,
                  detail={"username": user["username"], "role": user["role"], "reassigned_articles": reassigned})
    return ok({"id": user_id, "username": user["username"], "reassigned_articles": reassigned}, "用户已删除")


@app.get("/api/audit")
def list_audit_logs(
    request: Request,
    action: str = Query(default="", max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    logs = audit_store.list_logs(
        action=action or None, limit=limit, offset=offset
    )
    total = audit_store.count_logs(action=action or None)
    _record_audit(request, "audit.list", resource="audit")
    return ok({"logs": logs, "total": total, "limit": limit, "offset": offset})


@app.get("/api/dashboard")
def dashboard(request: Request) -> Dict[str, Any]:
    user = request.state.user
    articles = _visible_articles(user, 8)
    history = history_store.latest(limit=8) if user["role"] == "admin" else []
    all_tasks = task_manager.list(100)
    tasks = (all_tasks if user["role"] == "admin" else [task for task in all_tasks if task.get("owner_user_id") == user["id"]])[:8]
    cfg = config_service.load()
    personal_status = user_api_config_store.status(user["id"])
    return ok(
        {
            "stats": {
                "articles": len(_visible_articles(user, 100000)),
                "drafts": sum(x.get("status") == "draft_created" for x in articles),
                "running_tasks": sum(x.get("status") == "running" for x in tasks),
                "configured_sources": len(cfg.hot_sources),
            },
            "articles": articles,
            "history": history,
            "tasks": tasks,
            "schedule": schedule_controller.status(),
            "configuration": {
                "llm": personal_status["llm"] or (user["role"] == "admin" and bool(cfg.llm.base_url and cfg.llm.model)),
                "llm_key": personal_status["llm"] or (user["role"] == "admin" and bool(cfg.llm.api_key)),
                "wechat": bool(cfg.wechat.app_id and cfg.wechat.app_secret),
                "search": personal_status["search"] or (user["role"] == "admin" and bool(cfg.search.api_key)),
                "image": personal_status["image"] or user["role"] == "admin",
                "notify": cfg.notify.enabled and bool(cfg.notify.webhook_url),
            },
        }
    )


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    snapshot = config_service.snapshot()
    ready, reason = payment_ready(config_service.load())
    snapshot.setdefault("billing", {})["payment_ready"] = ready
    snapshot["billing"]["payment_readiness_reason"] = reason
    return ok(snapshot)


@app.put("/api/config")
def update_config(body: ConfigUpdateRequest, request: Request) -> Dict[str, Any]:
    try:
        snapshot = config_service.save(body)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "code": "CONFIG_INCOMPATIBLE", "message": str(exc)})
    schedule_controller.reload()
    _record_audit(request, "config.update", resource="config",
                  detail={"sections": [k for k, v in body.model_dump().items() if v]})
    return ok(snapshot, "配置已安全保存；密钥不会返回浏览器")


@app.post("/api/config/test/llm")
def test_llm() -> Dict[str, Any]:
    cfg = config_service.load()
    if not cfg.llm.base_url or not cfg.llm.model:
        raise HTTPException(status_code=400, detail="请先配置模型地址和模型名称")
    from .llm import LLMError

    try:
        text = LLMClient(cfg.llm).chat(
            [{"role": "user", "content": "仅回复 OK"}], temperature=0, max_tokens=128
        )
        return ok({"reply": text[:100]}, "模型连接成功")
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@app.post("/api/config/test/image")
def test_image() -> Dict[str, Any]:
    cfg = config_service.load()
    try:
        result = ImageGenerator(cfg).validate_connection()
        message = "图片服务连接成功"
        if result.get("chargeable"):
            message += "；已提交最小测试任务，服务商可能计费"
        return ok(result, message)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@app.post("/api/config/test/search")
def test_search() -> Dict[str, Any]:
    cfg = config_service.load()
    try:
        result = TavilySearchService(cfg.search).validate_connection()
        return ok(result, "Tavily 搜索连接成功；测试会消耗一次搜索额度")
    except SearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@app.post("/api/config/test/wechat")
def test_wechat() -> Dict[str, Any]:
    cfg = config_service.load()
    if not cfg.wechat.app_id or not cfg.wechat.app_secret:
        raise HTTPException(status_code=400, detail="请先配置公众号 AppID 和 AppSecret")
    token = WechatClient(cfg).access_token
    return ok({"token_received": bool(token)}, "公众号凭证验证成功")


@app.post("/api/config/test/notify")
def test_notify() -> Dict[str, Any]:
    cfg = config_service.load()
    if not cfg.notify.webhook_url:
        raise HTTPException(status_code=400, detail="请先配置通知 Webhook")
    Notifier(cfg.notify).send_text(f"{cfg.account_name}公众号智能体：通知连接测试成功")
    return ok(message="通知已发送")


@app.get("/api/hotspots")
def get_hotspots(
    sources: str = Query(default=""),
    refresh: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=100),
    keywords: str = Query(default="", max_length=200),
) -> Dict[str, Any]:
    cached = hotspot_service.cached()
    if not refresh and cached.get("items"):
        payload = cached
    else:
        selected_sources = [x.strip() for x in sources.split(",") if x.strip()]
        payload = hotspot_service.fetch(selected_sources, limit=limit)
    return ok(hotspot_service.filter_keywords(payload, keywords))


@app.get("/api/search/topics")
def search_topics(
    request: Request,
    q: str = Query(min_length=2, max_length=200),
    topic: str = Query(default="news", pattern="^(news|general)$"),
    time_range: str = Query(default="7d", pattern="^(24h|7d|30d|year|all)$"),
    limit: int = Query(default=20, ge=1, le=20),
    prefer_chinese: bool = Query(default=True),
) -> Dict[str, Any]:
    try:
        cfg, sources = user_api_config_store.effective_config(
            config_service.load(), request.state.user, ["search"]
        )
        result = TavilySearchService(cfg.search).search(
            q, topic=topic, time_range=time_range, limit=limit,
            prefer_chinese=prefer_chinese,
        )
        result["config_source"] = sources.get("search")
        return ok(result)
    except AuthError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "code": exc.code, "message": str(exc)})
    except SearchError as exc:
        status = 400 if "未配置" in str(exc) or "至少" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from None


@app.get("/api/token-usage")
def token_usage_dashboard(
    request: Request,
    days: int = Query(default=30, ge=1, le=3650),
    limit: int = Query(default=100, ge=1, le=500),
    user_id: str = Query(default="", max_length=64),
    model: str = Query(default="", max_length=128),
) -> Dict[str, Any]:
    return ok(token_usage_store.dashboard(days=days, limit=limit, user_id=user_id, model=model))


@app.get("/api/token-usage/tasks/{task_id}")
def token_usage_task_detail(task_id: str, request: Request) -> Dict[str, Any]:
    return ok(token_usage_store.task_detail(task_id))


@app.post("/api/generate")
def generate_article(body: GenerateRequest, request: Request) -> Dict[str, Any]:
    actor = dict(request.state.user)
    base_cfg = config_service.load()
    mode = body.options.generation_mode
    required = ["llm", "search"] + (["image"] if body.options.with_images else [])
    try:
        if mode == "personal":
            if actor["role"] != "admin" and not base_cfg.features.user_api_config:
                raise AuthError("管理员已关闭个人 API 生成，请使用平台生成", "PERSONAL_API_DISABLED")
            effective_cfg, sources = user_api_config_store.effective_config(base_cfg, actor, required)
        else:
            if actor["role"] != "admin" and not base_cfg.billing.enabled:
                raise AuthError("平台按次生成当前未开放，请使用个人 API", "PLATFORM_GENERATION_DISABLED")
            if not base_cfg.llm.api_key or not base_cfg.llm.base_url or not base_cfg.llm.model:
                raise AuthError("系统大模型尚未配置，暂时无法使用平台生成", "SYSTEM_LLM_CONFIG_REQUIRED")
            if not base_cfg.search.api_key:
                raise AuthError("系统搜索服务尚未配置，暂时无法使用平台生成", "SYSTEM_SEARCH_CONFIG_REQUIRED")
            effective_cfg = deepcopy(base_cfg)
            sources = {"llm": "system", "search": "system"}
            if not base_cfg.billing.platform_include_images:
                effective_cfg.image.source = "pillow"
                effective_cfg.image.provider = "pillow"
                effective_cfg.image.api_key = ""
                effective_cfg.image.inline_images = 0
                body = body.model_copy(update={"options": body.options.model_copy(update={"with_images": False})})
            if body.options.target_words and body.options.target_words > base_cfg.billing.max_target_words:
                raise AuthError(f"平台生成最多支持 {base_cfg.billing.max_target_words} 字", "PLATFORM_WORD_LIMIT_EXCEEDED")
    except AuthError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "code": exc.code, "message": str(exc)})

    task_id = uuid.uuid4().hex
    reservation = None
    if mode == "platform" and actor["role"] != "admin":
        try:
            reservation = billing_store.reserve(actor["id"], task_id, base_cfg.billing.price_per_generation_fen)
        except AuthError as exc:
            return JSONResponse(status_code=402, content={"ok": False, "code": exc.code, "message": str(exc)})

    def job(progress):
        def record_token_usage(usage: Dict[str, Any]) -> None:
            token_usage_store.record(
                task_id=task_id,
                article_id=str(usage.get("article_id") or ""),
                user_id=actor["id"],
                username=actor.get("username", ""),
                generation_mode=mode,
                usage=usage,
                input_price_per_million=effective_cfg.llm.input_price_per_million,
                output_price_per_million=effective_cfg.llm.output_price_per_million,
            )
        try:
            result = article_store.generate(
                body, effective_cfg, progress, actor,
                llm_usage_callback=record_token_usage,
                task_id=task_id,
            )
            token_usage_store.link_article(task_id, result.get("article_id", ""))
            if reservation:
                task_state = task_manager.get(task_id)
                if task_state and task_state.get("status") == "failed":
                    billing_store.settle(task_id, False, reason="任务已被停滞看门狗判定失败")
                else:
                    billing_store.settle(task_id, True, result.get("article_id", ""))
            return result
        except Exception as exc:
            if reservation:
                billing_store.settle(task_id, False, reason=str(exc))
            raise

    try:
        task_manager.submit("generate", job, owner_user=actor, task_id=task_id)
    except Exception as exc:
        if reservation:
            billing_store.settle(task_id, False, reason=f"任务提交失败：{exc}")
        raise
    _record_audit(request, "article.generate", resource="task", resource_id=task_id,
                  detail={"topic": body.topic.title[:100], "style": body.options.style, "generation_mode": mode, "billing_source": reservation and reservation["source"], "config_sources": sources})
    return ok({"task_id": task_id, "billing_source": reservation and reservation["source"]}, "文章生成任务已创建")


@app.get("/api/tasks")
def list_tasks(request: Request, limit: int = 30) -> Dict[str, Any]:
    tasks = task_manager.list(100)
    if request.state.user["role"] != "admin":
        tasks = [task for task in tasks if task.get("owner_user_id") == request.state.user["id"]]
    return ok(tasks[:min(max(limit, 1), 100)])


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> Dict[str, Any]:
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if request.state.user["role"] != "admin" and task.get("owner_user_id") != request.state.user["id"]:
        raise HTTPException(status_code=403, detail="无权访问该任务")
    return ok(task)


def _control_task(task_id: str, action: str, request: Request) -> Dict[str, Any]:
    try:
        existing = task_manager.get(task_id)
        if not existing:
            raise KeyError(task_id)
        if request.state.user["role"] != "admin" and existing.get("owner_user_id") != request.state.user["id"]:
            raise HTTPException(status_code=403, detail="无权控制该任务")
        task = getattr(task_manager, action)(task_id)
        if action == "cancel" and existing.get("kind") == "generate":
            try:
                billing_store.settle(task_id, False, reason="用户取消生成任务")
            except AuthError as exc:
                if exc.code != "GENERATION_RESERVATION_NOT_FOUND":
                    raise
        messages = {
            "pause": "暂停请求已提交，将在当前模型/API 调用结束后生效",
            "resume": "任务已继续执行",
            "cancel": "取消请求已提交",
            "delete": "任务日志已删除",
        }
        _record_audit(request, f"task.{action}", resource="task", resource_id=task_id)
        return ok(task, messages[action])
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: str, request: Request) -> Dict[str, Any]:
    return _control_task(task_id, "pause", request)


@app.post("/api/tasks/{task_id}/resume")
def resume_task(task_id: str, request: Request) -> Dict[str, Any]:
    return _control_task(task_id, "resume", request)


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str, request: Request) -> Dict[str, Any]:
    return _control_task(task_id, "cancel", request)


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, request: Request, confirm: bool = Query(default=False)) -> Dict[str, Any]:
    if not confirm:
        raise HTTPException(status_code=400, detail="删除任务日志需要明确确认")
    return _control_task(task_id, "delete", request)


@app.get("/api/articles")
def list_articles(request: Request, limit: int = 50) -> Dict[str, Any]:
    return ok(_visible_articles(request.state.user, min(max(limit, 1), 200)))


@app.get("/api/articles/{article_id}/shares")
def list_article_shares(article_id: str, request: Request) -> Dict[str, Any]:
    _require_article_access(article_id, request.state.user, "view")
    if request.state.user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可以管理文章共享")
    grants = article_access_store.grants(article_id)
    users = {user["id"]: user for user in user_store.list_users(limit=1000)}
    return ok([{**grant, "username": users.get(grant["user_id"], {}).get("username", "未知用户")} for grant in grants])


@app.put("/api/articles/{article_id}/shares")
def set_article_share(article_id: str, body: ArticleShareRequest, request: Request) -> Dict[str, Any]:
    _require_article_access(article_id, request.state.user, "view")
    if request.state.user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可以管理文章共享")
    target = user_store.get_user(body.user_id)
    if not target or target["status"] != "active" or target["role"] == "admin":
        raise HTTPException(status_code=400, detail="共享目标必须是有效的 editor/creator/viewer")
    if target["role"] == "viewer" and any(value in body.permissions for value in ("edit", "push")):
        raise HTTPException(status_code=400, detail="viewer 只能获得查看权限")
    if target["role"] == "creator" and "push" in body.permissions:
        raise HTTPException(status_code=400, detail="creator 不能获得公众号推送权限")
    grant = article_access_store.set_grant(article_id, body.user_id, body.permissions, request.state.user["id"])
    _record_audit(request, "article.share", resource="article", resource_id=article_id, detail={"user_id": body.user_id, "permissions": body.permissions})
    return ok(grant, "文章共享权限已保存")


@app.delete("/api/articles/{article_id}/shares/{user_id}")
def revoke_article_share(article_id: str, user_id: str, request: Request) -> Dict[str, Any]:
    _require_article_access(article_id, request.state.user, "view")
    if request.state.user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可以管理文章共享")
    article_access_store.revoke(article_id, user_id)
    _record_audit(request, "article.share_revoke", resource="article", resource_id=article_id, detail={"user_id": user_id})
    return ok(message="共享权限已撤销")


@app.get("/api/articles/{article_id}")
def get_article(article_id: str, request: Request) -> Dict[str, Any]:
    try:
        meta = _require_article_access(article_id, request.state.user, "view")
        data = article_store.get(article_id)
        data["access"] = {action: article_access_store.allowed(meta, request.state.user, action) for action in ("view", "edit", "push", "delete")}
        for name in data.get("assets", []):
            data["preview_html"] = data["preview_html"].replace(
                f"/api/articles/{article_id}/assets/{name}", _signed_asset_url(article_id, name, request.state.user["id"])
            )
        data["cover_url"] = _signed_asset_url(article_id, "cover.png", request.state.user["id"])
        return ok(data)
    except ArticleNotFoundError:
        raise HTTPException(status_code=404, detail="文章不存在") from None


@app.put("/api/articles/{article_id}")
def update_article(article_id: str, body: ArticleUpdateRequest, request: Request) -> Dict[str, Any]:
    try:
        _require_article_access(article_id, request.state.user, "edit")
        data = article_store.update(article_id, body.title, body.digest, body.content_md)
        return ok(data, "文章已保存并生成新版本")
    except ArticleNotFoundError:
        raise HTTPException(status_code=404, detail="文章不存在") from None


@app.delete("/api/articles/{article_id}")
def delete_article(article_id: str, request: Request, confirm: bool = Query(default=False)) -> Dict[str, Any]:
    if not confirm:
        raise HTTPException(status_code=400, detail="删除文章需要明确确认")
    try:
        _require_article_access(article_id, request.state.user, "delete")
        data = article_store.delete(article_id)
        article_access_store.clear_article(article_id)
        message = "本地文章已删除"
        if data["remote_draft_preserved"]:
            message += "；已推送到微信公众号的远端草稿不受影响"
        return ok(data, message)
    except ArticleNotFoundError:
        raise HTTPException(status_code=404, detail="文章不存在") from None


@app.get("/api/articles/{article_id}/versions")
def article_versions(article_id: str, request: Request) -> Dict[str, Any]:
    try:
        _require_article_access(article_id, request.state.user, "view")
        return ok(article_store.versions(article_id))
    except ArticleNotFoundError:
        raise HTTPException(status_code=404, detail="文章不存在") from None


@app.get("/api/articles/{article_id}/assets/{name}")
def article_asset(article_id: str, name: str, request: Request):
    try:
        _require_article_access(article_id, request.state.user, "view")
        return FileResponse(article_store.asset(article_id, name))
    except ArticleNotFoundError:
        raise HTTPException(status_code=404, detail="素材不存在") from None


@app.post("/api/articles/{article_id}/push")
def push_article(article_id: str, body: PushDraftRequest, request: Request) -> Dict[str, Any]:
    if not body.confirm_reviewed or not body.confirm_ai_disclosure:
        raise HTTPException(status_code=400, detail="推送前必须完成人工审核与 AI 内容声明确认")
    try:
        _require_article_access(article_id, request.state.user, "push")
        article_store.get(article_id)
    except ArticleNotFoundError:
        raise HTTPException(status_code=404, detail="文章不存在") from None

    def job(progress):
        return article_store.push_draft(article_id, config_service.load(), progress)

    task_id = task_manager.submit("push_draft", job, owner_user=request.state.user)
    return ok({"task_id": task_id}, "草稿推送任务已创建")


@app.get("/api/history")
def get_history(request: Request, limit: int = 50) -> Dict[str, Any]:
    if request.state.user["role"] != "admin":
        return ok([])
    return ok(history_store.latest(min(max(limit, 1), 200)))


@app.put("/api/history/{record_id}/metrics")
def update_metrics(record_id: int, body: MetricsUpdateRequest) -> Dict[str, Any]:
    history_store.update_metrics(record_id, **body.model_dump())
    return ok(message="复盘数据已更新")


@app.get("/api/schedule")
def get_schedule() -> Dict[str, Any]:
    return ok(schedule_controller.status())


@app.post("/api/schedule/reload")
def reload_schedule() -> Dict[str, Any]:
    return ok(schedule_controller.reload(), "调度配置已重新加载")


@app.get("/")
def index():
    index_path = WEB_ROOT / "index.html"
    if not index_path.exists():
        return JSONResponse(
            status_code=503,
            content={"ok": False, "message": "Web 前端资源尚未安装"},
        )
    return FileResponse(
        index_path,
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@app.get("/{file_path:path}")
def static_files(file_path: str):
    if file_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API 不存在")
    requested = (WEB_ROOT / file_path).resolve()
    try:
        requested.relative_to(WEB_ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="资源不存在") from None
    no_cache = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
    if requested.is_file():
        return FileResponse(requested, headers=no_cache)
    index_path = WEB_ROOT / "index.html"
    return (
        FileResponse(index_path, headers=no_cache)
        if index_path.exists()
        else JSONResponse(status_code=404, content={})
    )
