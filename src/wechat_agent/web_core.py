"""Web 管理台基础服务：安全配置、后台任务和热点缓存。"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from .config import DEFAULT_CONFIG_PATH, DEFAULT_ENV_PATH, Config, ImageConfig
from .hot_topics import HotTopicFetcher
from .image_sources import (
    LEGACY_EXTERNAL_SOURCES,
    image_source_catalog,
    infer_image_source,
    legacy_image_base_url,
    resolve_image_source,
)
from .topic_selector import TopicSelector
from .web_models import ConfigUpdateRequest

logger = logging.getLogger(__name__)


SECRET_KEYS = {
    "llm_api_key": "DEEPSEEK_API_KEY",
    "wechat_app_id": "WECHAT_APP_ID",
    "wechat_app_secret": "WECHAT_APP_SECRET",
    "image_api_key": "IMAGE_API_KEY",
    "search_api_key": "TAVILY_API_KEY",
    "notify_webhook_url": "NOTIFY_WEBHOOK_URL",
    "wechat_pay_api_v3_key": "WECHAT_PAY_API_V3_KEY",
}


class ConfigService:
    """安全读写 YAML 与 .env；API 永不返回密钥明文。"""

    def __init__(
        self,
        config_path: Path = DEFAULT_CONFIG_PATH,
        env_path: Path = DEFAULT_ENV_PATH,
    ):
        self.config_path = config_path
        self.env_path = env_path
        self._lock = threading.RLock()

    def load(self) -> Config:
        return Config.load(self.config_path, self.env_path)

    def _read_env(self) -> Dict[str, str]:
        values: Dict[str, str] = {}
        if not self.env_path.exists():
            return values
        for raw_line in self.env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        # 兼容旧版本：图片密钥曾固定使用 DASHSCOPE_API_KEY。
        if not values.get("IMAGE_API_KEY") and values.get("DASHSCOPE_API_KEY"):
            values["IMAGE_API_KEY"] = values["DASHSCOPE_API_KEY"]
        return values

    @staticmethod
    def _mask(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "••••••••"
        return value[:4] + "••••••••" + value[-4:]

    def snapshot(self) -> Dict[str, Any]:
        cfg = self.load()
        env = self._read_env()

        def secret(name: str, resolved: str) -> Dict[str, Any]:
            value = os.environ.get(SECRET_KEYS[name], env.get(SECRET_KEYS[name], resolved))
            return {"configured": bool(value), "masked": self._mask(value)}

        billing = asdict(cfg.billing)
        billing["wechat_pay_api_v3_key"] = secret("wechat_pay_api_v3_key", cfg.billing.wechat_pay_api_v3_key)
        return {
            "llm": {
                "base_url": cfg.llm.base_url,
                "model": cfg.llm.model,
                "temperature": cfg.llm.temperature,
                "max_tokens": cfg.llm.max_tokens,
                "timeout": cfg.llm.timeout,
                "input_price_per_million": cfg.llm.input_price_per_million,
                "output_price_per_million": cfg.llm.output_price_per_million,
                "outline_model": cfg.llm.outline_model or "",
                "refine_model": cfg.llm.refine_model or "",
                "api_key": secret("llm_api_key", cfg.llm.api_key),
            },
            "wechat": {
                "app_id": cfg.wechat.app_id,
                "app_id_state": secret("wechat_app_id", cfg.wechat.app_id),
                "app_secret": secret("wechat_app_secret", cfg.wechat.app_secret),
                "publish_mode": cfg.wechat.publish_mode,
            },
            "image": {
                "source": infer_image_source(cfg.image.provider, cfg.image.base_url, cfg.image.source),
                "source_catalog": image_source_catalog(),
                "provider": cfg.image.provider,
                "model": cfg.image.model,
                "base_url": cfg.image.base_url,
                "api_size": cfg.image.api_size or "",
                "inline_images": cfg.image.inline_images,
                "cover_bg": cfg.image.cover_bg,
                "cover_fg": cfg.image.cover_fg,
                "api_key": secret("image_api_key", cfg.image.api_key),
            },
            "search": {
                "provider": cfg.search.provider,
                "base_url": cfg.search.base_url,
                "search_depth": cfg.search.search_depth,
                "timeout": cfg.search.timeout,
                "api_key": secret("search_api_key", cfg.search.api_key),
            },
            "notify": {
                "enabled": cfg.notify.enabled,
                "webhook_type": cfg.notify.webhook_type,
                "webhook_url": secret("notify_webhook_url", cfg.notify.webhook_url),
            },
            "schedule": asdict(cfg.schedule),
            "registration": asdict(cfg.registration),
            "billing": billing,
            "features": asdict(cfg.features),
            "writing": {
                "account_name": cfg.account_name,
                "account_position": cfg.account_position,
                "audience": cfg.audience,
                "target_words": cfg.target_words,
                "hot_sources": cfg.hot_sources,
                "whitelist": cfg.topic_filter.whitelist,
                "blacklist": cfg.topic_filter.blacklist,
                "max_topics": cfg.topic_filter.max_topics,
                "min_score": cfg.topic_filter.min_score,
            },
        }

    def save(self, request: ConfigUpdateRequest) -> Dict[str, Any]:
        with self._lock:
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8-sig")) or {}
            env = self._read_env()
            payload = request.model_dump(exclude_none=True)
            current_cfg = self.load()
            current_image_source = infer_image_source(
                current_cfg.image.provider, current_cfg.image.base_url, current_cfg.image.source
            )

            llm = payload.get("llm", {})
            self._update_secret(llm, "api_key", "llm_api_key", env)
            self._merge(raw, "llm", llm)

            wechat = payload.get("wechat", {})
            self._update_secret(wechat, "app_id", "wechat_app_id", env)
            self._update_secret(wechat, "app_secret", "wechat_app_secret", env)
            self._merge(raw, "wechat", wechat)

            image = payload.get("image", {})
            if image.get("source"):
                requested_source = image["source"]
                canonical_source = infer_image_source(
                    image.get("provider", current_cfg.image.provider),
                    image.get("base_url", current_cfg.image.base_url),
                    requested_source,
                )
                if requested_source in LEGACY_EXTERNAL_SOURCES and not image.get("base_url"):
                    image["base_url"] = legacy_image_base_url(requested_source)
                image["source"] = canonical_source
                profile = next(item for item in image_source_catalog() if item["id"] == canonical_source)
                address_changed = (
                    "base_url" in image
                    and image["base_url"].strip().rstrip("/")
                    != current_cfg.image.base_url.strip().rstrip("/")
                )
                if (
                    profile["requires_key"]
                    and (canonical_source != current_image_source or address_changed)
                    and not image.get("api_key")
                ):
                    raise ValueError("切换自定义图片地址时必须同时填写该服务对应的 API Key")
                image["provider"] = profile["protocol"]
                if canonical_source == "custom":
                    candidate = {**current_cfg.image.__dict__, **image}
                    candidate["api_key"] = image.get("api_key") or current_cfg.image.api_key
                    resolve_image_source(ImageConfig(**candidate))
            if "api_size" in image:
                from .images import ImageGenerator

                normalized = image["api_size"].strip().lower().replace("×", "x") if isinstance(image["api_size"], str) else ""
                if normalized:
                    parsed = ImageGenerator._parse_size(normalized)
                    if not parsed:
                        raise ValueError("生成尺寸格式应为 宽x高（例如 2560x1440）")
                    if parsed[0] < 256 or parsed[1] < 256:
                        raise ValueError("生成尺寸太小：宽高至少 256 像素")
                    image["api_size"] = f"{parsed[0]}x{parsed[1]}"
                else:
                    image["api_size"] = ""
            self._update_secret(image, "api_key", "image_api_key", env)
            self._merge(raw, "image", image)

            search = payload.get("search", {})
            self._update_secret(search, "api_key", "search_api_key", env)
            self._merge(raw, "search", search)

            notify = payload.get("notify", {})
            self._update_secret(notify, "webhook_url", "notify_webhook_url", env)
            self._merge(raw, "notify", notify)
            self._merge(raw, "schedule", payload.get("schedule", {}))
            registration = payload.get("registration", {})
            if registration:
                registration["default_role"] = "creator"
            self._merge(raw, "registration", registration)
            billing = payload.get("billing", {})
            self._update_secret(billing, "wechat_pay_api_v3_key", "wechat_pay_api_v3_key", env)
            self._merge(raw, "billing", billing)
            self._merge(raw, "features", payload.get("features", {}))

            writing = payload.get("writing", {})
            for name in (
                "account_name", "account_position", "audience", "target_words", "hot_sources"
            ):
                if name in writing:
                    raw[name] = writing[name]
            tf = raw.setdefault("topic_filter", {})
            for name in ("whitelist", "blacklist", "max_topics", "min_score"):
                if name in writing:
                    tf[name] = writing[name]

            for logical in request.clear_secrets:
                env_name = SECRET_KEYS[logical]
                env[env_name] = ""
                os.environ[env_name] = ""

            # 确保密钥字段始终是环境变量占位符，而不是明文 YAML。
            raw.setdefault("llm", {})["api_key"] = "${DEEPSEEK_API_KEY}"
            raw.setdefault("wechat", {})["app_id"] = "${WECHAT_APP_ID}"
            raw.setdefault("wechat", {})["app_secret"] = "${WECHAT_APP_SECRET}"
            raw.setdefault("image", {})["api_key"] = "${IMAGE_API_KEY}"
            raw.setdefault("search", {})["api_key"] = "${TAVILY_API_KEY}"
            raw.setdefault("notify", {})["webhook_url"] = "${NOTIFY_WEBHOOK_URL}"
            raw.setdefault("billing", {})["wechat_pay_api_v3_key"] = "${WECHAT_PAY_API_V3_KEY}"

            self.config_path.write_text(
                yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            self.env_path.parent.mkdir(parents=True, exist_ok=True)
            self.env_path.write_text(
                "\n".join(f"{key}={value}" for key, value in env.items()) + "\n",
                encoding="utf-8",
            )
            return self.snapshot()

    @staticmethod
    def _merge(raw: Dict[str, Any], section: str, values: Dict[str, Any]) -> None:
        if not values:
            return
        raw.setdefault(section, {}).update(values)

    @staticmethod
    def _update_secret(
        section: Dict[str, Any], field_name: str, logical_name: str, env: Dict[str, str]
    ) -> None:
        if field_name not in section:
            return
        value = section.pop(field_name)
        if value:
            env_name = SECRET_KEYS[logical_name]
            env[env_name] = value
            os.environ[env_name] = value


class TaskCancelled(RuntimeError):
    """任务收到协作式取消请求。"""


class TaskManager:
    """轻量后台任务队列，支持协作式暂停、继续、取消、日志删除与停滞看门狗。"""

    STALL_LOG_MESSAGE = "任务停滞超时，已自动终止（长时间没有任何日志或进度更新；常见原因是模型服务无响应或网络链路异常）。请重新生成。"

    def __init__(
        self,
        max_workers: int = 2,
        stall_timeout_seconds: float = 600.0,
        watchdog_interval_seconds: float = 30.0,
    ):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="web-agent")
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._futures: Dict[str, Any] = {}
        self._controls: Dict[str, Dict[str, threading.Event]] = {}
        self._lock = threading.RLock()
        self._stall_timeout_seconds = max(1.0, float(stall_timeout_seconds))
        self._watchdog_interval_seconds = max(1.0, float(watchdog_interval_seconds))
        self._watchdog = threading.Thread(
            target=self._watchdog_loop,
            name="task-stall-watchdog",
            daemon=True,
        )
        self._watchdog.start()

    @staticmethod
    def _stamp(message: str) -> str:
        return f"[{datetime.now().strftime('%H:%M:%S')}] {message}"

    def _watchdog_loop(self) -> None:
        while True:
            time.sleep(self._watchdog_interval_seconds)
            try:
                self._fail_stalled_tasks()
            except Exception:  # noqa: BLE001
                logger.exception("task stall watchdog failed")

    def _fail_stalled_tasks(self) -> None:
        now = datetime.now()
        with self._lock:
            for task in self._tasks.values():
                if task["status"] != "running":
                    continue
                try:
                    updated = datetime.fromisoformat(task["updated_at"])
                except (TypeError, ValueError):
                    continue
                stalled = (now - updated).total_seconds() > self._stall_timeout_seconds
                future = self._futures.get(task["id"])
                still_executing = bool(future and future.running())
                if stalled and still_executing:
                    task.update(
                        status="failed",
                        error=self.STALL_LOG_MESSAGE,
                        updated_at=now.isoformat(timespec="seconds"),
                    )
                    task["logs"].append(self._stamp(self.STALL_LOG_MESSAGE))
                    task["logs"] = task["logs"][-100:]

    def submit(
        self,
        kind: str,
        function: Callable[[Callable[[int, str], None]], Any],
        owner_user: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> str:
        task_id = task_id or uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self._tasks[task_id] = {
                "id": task_id,
                "kind": kind,
                "owner_user_id": (owner_user or {}).get("id", ""),
                "owner_username": (owner_user or {}).get("username", ""),
                "status": "pending",
                "progress": 0,
                "logs": [self._stamp("任务已进入队列")],
                "result": None,
                "error": "",
                "created_at": now,
                "updated_at": now,
            }
            resume_event = threading.Event()
            resume_event.set()
            self._controls[task_id] = {
                "resume": resume_event,
                "cancel": threading.Event(),
            }
        future = self._executor.submit(self._run, task_id, function)
        with self._lock:
            self._futures[task_id] = future
        return task_id

    def _run(
        self,
        task_id: str,
        function: Callable[[Callable[[int, str], None]], Any],
    ) -> None:
        try:
            self._checkpoint(task_id)
            self.update(task_id, 2, "任务开始执行", status="running")
            result = function(
                lambda progress, message: self._progress(task_id, progress, message)
            )
            self._checkpoint(task_id)
            with self._lock:
                task = self._tasks[task_id]
                task.update(
                    status="success",
                    progress=100,
                    result=result,
                    updated_at=datetime.now().isoformat(timespec="seconds"),
                )
                task["logs"].append(self._stamp("任务执行完成"))
        except TaskCancelled:
            with self._lock:
                task = self._tasks.get(task_id)
                if task:
                    task.update(
                        status="cancelled",
                        updated_at=datetime.now().isoformat(timespec="seconds"),
                    )
                    if not task["logs"] or not task["logs"][-1].endswith("任务已取消"):
                        task["logs"].append(self._stamp("任务已取消"))
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    return
                if task["status"] == "failed" and task.get("error") == self.STALL_LOG_MESSAGE:
                    # 看门狗已判定停滞并终止展示状态；迟到的底层结果不再覆盖。
                    task["logs"].append(self._stamp(f"底层调用迟来结束（已被看门狗终止）：{str(exc)[:200]}"))
                    task["logs"] = task["logs"][-100:]
                    return
                task.update(
                    status="failed",
                    error=str(exc)[:1000],
                    updated_at=datetime.now().isoformat(timespec="seconds"),
                )
                task["logs"].append(self._stamp(f"任务失败：{str(exc)[:500]}"))
                task["debug"] = traceback.format_exc(limit=8)

    def _progress(self, task_id: str, progress: int, message: str) -> None:
        self._checkpoint(task_id)
        self.update(task_id, progress, message)
        self._checkpoint(task_id)

    def _checkpoint(self, task_id: str) -> None:
        control = self._controls[task_id]
        if control["cancel"].is_set():
            raise TaskCancelled()
        if control["resume"].is_set():
            return
        with self._lock:
            task = self._tasks[task_id]
            if task["status"] != "paused":
                task["status"] = "paused"
                task["logs"].append(self._stamp("任务已在安全检查点暂停"))
                task["updated_at"] = datetime.now().isoformat(timespec="seconds")
        while not control["resume"].wait(timeout=0.2):
            if control["cancel"].is_set():
                raise TaskCancelled()
        if control["cancel"].is_set():
            raise TaskCancelled()
        with self._lock:
            task = self._tasks[task_id]
            task["status"] = "running"
            task["logs"].append(self._stamp("任务已继续执行"))
            task["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def update(
        self, task_id: str, progress: int, message: str, status: Optional[str] = None
    ) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task["progress"] = max(task["progress"], min(99, int(progress)))
            task["updated_at"] = datetime.now().isoformat(timespec="seconds")
            if status:
                task["status"] = status
            if message:
                task["logs"].append(self._stamp(str(message)[:500]))
                task["logs"] = task["logs"][-100:]

    def pause(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task["status"] not in {"pending", "running", "pausing"}:
                raise ValueError("只有排队中或执行中的任务可以暂停")
            self._controls[task_id]["resume"].clear()
            task["status"] = "paused" if task["status"] == "pending" else "pausing"
            task["logs"].append(self._stamp("已请求暂停；当前模型/API 调用结束后生效"))
            task["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return self.get(task_id)  # type: ignore[return-value]

    def resume(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task["status"] not in {"paused", "pausing"}:
                raise ValueError("任务当前不是暂停状态")
            self._controls[task_id]["resume"].set()
            task["status"] = "running"
            task["logs"].append(self._stamp("已请求继续执行"))
            task["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return self.get(task_id)  # type: ignore[return-value]

    def cancel(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task["status"] in {"success", "failed", "cancelled"}:
                raise ValueError("任务已经结束，不能再次取消")
            control = self._controls[task_id]
            control["cancel"].set()
            control["resume"].set()
            future = self._futures.get(task_id)
            cancelled_before_start = bool(future and future.cancel())
            task["status"] = "cancelled" if cancelled_before_start else "cancelling"
            task["logs"].append(
                self._stamp(
                    "排队任务已取消" if cancelled_before_start else "已请求取消；当前模型/API 调用结束后生效"
                )
            )
            task["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return self.get(task_id)  # type: ignore[return-value]

    def delete(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task["status"] not in {"success", "failed", "cancelled"}:
                raise ValueError("运行中、暂停中或正在取消的任务不能删除")
            result = deepcopy(task)
            self._tasks.pop(task_id, None)
            self._futures.pop(task_id, None)
            self._controls.pop(task_id, None)
            result.pop("debug", None)
            return result

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self._tasks.get(task_id)
            if not item:
                return None
            result = deepcopy(item)
            result.pop("debug", None)
            return result

    def list(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            items = sorted(
                self._tasks.values(), key=lambda x: x["created_at"], reverse=True
            )[:limit]
            return [self.get(item["id"]) for item in items if self.get(item["id"])]


class HotspotService:
    """多源热点抓取与评分缓存。"""

    ALLOWED_SOURCES = {"weibo", "zhihu", "toutiao", "cls", "bili", "baidu"}

    def __init__(self, config_service: ConfigService):
        self.config_service = config_service
        self.fetcher = HotTopicFetcher()
        self._cache: Dict[str, Any] = {"items": [], "updated_at": ""}
        self._lock = threading.RLock()

    def fetch(self, sources: List[str], limit: int = 50) -> Dict[str, Any]:
        clean_sources = [s for s in sources if s in self.ALLOWED_SOURCES]
        if not clean_sources:
            clean_sources = self.config_service.load().hot_sources
        topics = self.fetcher.fetch_all(clean_sources)
        cfg = self.config_service.load()
        selector_cfg = replace(
            cfg.topic_filter,
            max_candidates=max(limit, 10),
            max_topics=limit,
            min_score=0.0,
        )
        selected = TopicSelector(selector_cfg).select(topics)
        items = []
        for item in selected[:limit]:
            topic = item.topic
            items.append(
                {
                    "id": uuid.uuid5(
                        uuid.NAMESPACE_URL, f"{topic.source}:{topic.title}"
                    ).hex,
                    "title": topic.title,
                    "source": topic.source,
                    "rank": topic.rank,
                    "heat": topic.heat,
                    "url": topic.url,
                    "summary": str(topic.extra.get("content", ""))[:1000],
                    "score": round(item.score, 1),
                    "reasons": item.reasons,
                    "matched_keywords": item.matched_keywords,
                }
            )
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self._cache = {"items": items, "updated_at": now, "sources": clean_sources}
        return deepcopy(self._cache)

    @staticmethod
    def filter_keywords(payload: Dict[str, Any], query: str) -> Dict[str, Any]:
        """按标题、摘要和领域标签进行多关键词 OR 查询，并按相关度排序。"""
        result = deepcopy(payload)
        keywords = []
        for token in re.split(r"[\s,，、;；]+", (query or "").strip()):
            token = token.strip()[:40]
            if len(token) >= 1 and token.casefold() not in {x.casefold() for x in keywords}:
                keywords.append(token)
            if len(keywords) >= 10:
                break
        result["query"] = (query or "").strip()[:200]
        result["query_keywords"] = keywords
        result["total_before_filter"] = len(result.get("items") or [])
        if not keywords:
            return result

        matched = []
        for item in result.get("items") or []:
            fields = " ".join(
                [
                    str(item.get("title") or ""),
                    str(item.get("summary") or ""),
                    " ".join(str(x) for x in item.get("matched_keywords") or []),
                    " ".join(str(x) for x in item.get("reasons") or []),
                ]
            ).casefold()
            hits = [keyword for keyword in keywords if keyword.casefold() in fields]
            if not hits:
                continue
            item["matched_query_keywords"] = hits
            item["query_relevance"] = len(hits)
            matched.append(item)
        matched.sort(
            key=lambda item: (item.get("query_relevance", 0), item.get("score", 0)),
            reverse=True,
        )
        result["items"] = matched
        return result

    def cached(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._cache)
