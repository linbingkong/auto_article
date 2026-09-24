"""用户级 LLM、搜索和文生图配置：SQLite/MySQL + Fernet 加密。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

from .auth import AuthError
from .config import Config, ImageConfig, LLMConfig, SearchConfig
from .database import StorageTarget, connect_database, is_mysql, verify_mysql_tables
from .image_sources import (
    LEGACY_EXTERNAL_SOURCES,
    image_source_catalog,
    infer_image_source,
    legacy_image_base_url,
    resolve_image_source,
)


class UserApiConfigStore:
    SERVICES = {"llm", "search", "image"}

    def __init__(self, db_path: StorageTarget):
        self.db_path = db_path
        if is_mysql(db_path):
            verify_mysql_tables(db_path, ["user_api_configs"])
        else:
            self._init_db()

    def _connect(self):
        return connect_database(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS user_api_configs (
                    user_id TEXT PRIMARY KEY,
                    encrypted_payload TEXT NOT NULL,
                    llm_configured INTEGER NOT NULL DEFAULT 0,
                    search_configured INTEGER NOT NULL DEFAULT 0,
                    image_configured INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )"""
            )

    @staticmethod
    def _fernet() -> Fernet:
        secret = os.environ.get("USER_CONFIG_SECRET", "").strip()
        if len(secret) < 32:
            raise AuthError(
                "用户 API 配置加密密钥未配置：USER_CONFIG_SECRET 至少 32 个字符",
                "USER_CONFIG_SECRET_MISSING",
            )
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        return Fernet(key)

    def _encrypt(self, payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        return self._fernet().encrypt(raw).decode()

    def _decrypt(self, token: str) -> Dict[str, Any]:
        try:
            raw = self._fernet().decrypt(token.encode())
            return json.loads(raw.decode())
        except InvalidToken as exc:
            raise AuthError("用户 API 配置无法解密，请联系管理员检查密钥", "USER_CONFIG_DECRYPT_FAILED") from exc

    def get(self, user_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT encrypted_payload FROM user_api_configs WHERE user_id=?", (user_id,)
            ).fetchone()
        return self._decrypt(row["encrypted_payload"]) if row else {}

    def status(self, user_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT llm_configured,search_configured,image_configured,updated_at "
                "FROM user_api_configs WHERE user_id=?", (user_id,)
            ).fetchone()
        if not row:
            return {"llm": False, "search": False, "image": False, "updated_at": None}
        return {
            "llm": bool(row["llm_configured"]),
            "search": bool(row["search_configured"]),
            "image": bool(row["image_configured"]),
            "updated_at": row["updated_at"],
        }

    def statuses(self, user_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        return {user_id: self.status(user_id) for user_id in user_ids}

    @staticmethod
    def _validate_service(service: str, value: Dict[str, Any], existing: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(existing)
        merged.update({key: item for key, item in value.items() if item is not None and item != ""})
        if service == "llm":
            if not merged.get("base_url") or not merged.get("model") or not merged.get("api_key"):
                raise AuthError("个人 LLM 配置必须包含 Base URL、模型和 API Key", "USER_LLM_CONFIG_INCOMPLETE")
        elif service == "search":
            if not merged.get("base_url") or not merged.get("api_key"):
                raise AuthError("个人搜索配置必须包含 Base URL 和 API Key", "USER_SEARCH_CONFIG_INCOMPLETE")
            merged["provider"] = "tavily"
        elif service == "image":
            requested_source = value.get("source") or merged.get("source", "")
            source = infer_image_source(
                merged.get("provider", "pillow"), merged.get("base_url", ""), requested_source
            )
            profile = next(item for item in image_source_catalog() if item["id"] == source)
            previous_source = infer_image_source(
                existing.get("provider", "pillow"), existing.get("base_url", ""), existing.get("source", "")
            )
            if requested_source in LEGACY_EXTERNAL_SOURCES and not value.get("base_url"):
                merged["base_url"] = merged.get("base_url") or legacy_image_base_url(requested_source)
            address_changed = (
                bool(value.get("base_url"))
                and value["base_url"].strip().rstrip("/")
                != existing.get("base_url", "").strip().rstrip("/")
            )
            if (
                profile["requires_key"]
                and (source != previous_source or address_changed)
                and not value.get("api_key")
            ):
                raise AuthError(
                    "切换自定义图片地址时必须同时填写该服务对应的 API Key",
                    "USER_IMAGE_KEY_REQUIRED",
                )
            merged["source"] = source
            merged["provider"] = profile["protocol"]
            try:
                resolve_image_source(ImageConfig(**merged))
            except (TypeError, ValueError) as exc:
                raise AuthError(str(exc), "USER_IMAGE_CONFIG_INCOMPATIBLE") from exc
        return merged

    def _write_payload(self, user_id: str, payload: Dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        flags = {service: int(bool(payload.get(service))) for service in self.SERVICES}
        encrypted = self._encrypt(payload)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO user_api_configs
                (user_id,encrypted_payload,llm_configured,search_configured,image_configured,updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET encrypted_payload=excluded.encrypted_payload,
                llm_configured=excluded.llm_configured,search_configured=excluded.search_configured,
                image_configured=excluded.image_configured,updated_at=excluded.updated_at""",
                (user_id, encrypted, flags["llm"], flags["search"], flags["image"], now),
            )

    def update(self, user_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        payload = self.get(user_id)
        for service in self.SERVICES:
            value = changes.get(service)
            if value is not None:
                payload[service] = self._validate_service(service, value, payload.get(service, {}))
        self._write_payload(user_id, payload)
        return self.snapshot(user_id)

    def clear(self, user_id: str, service: Optional[str] = None) -> Dict[str, Any]:
        if service and service not in self.SERVICES:
            raise AuthError("不支持的配置类型", "USER_CONFIG_SERVICE_INVALID")
        if not service:
            with self._connect() as conn:
                conn.execute("DELETE FROM user_api_configs WHERE user_id=?", (user_id,))
            return self.snapshot(user_id)
        payload = self.get(user_id)
        payload.pop(service, None)
        if not payload:
            return self.clear(user_id)
        self._write_payload(user_id, payload)
        return self.snapshot(user_id)

    @staticmethod
    def _masked(key: str) -> str:
        if not key:
            return ""
        return "••••••••" + key[-4:]

    def snapshot(self, user_id: str, role: str = "editor") -> Dict[str, Any]:
        payload = self.get(user_id)
        result: Dict[str, Any] = {"services": {}, "fallback_allowed": role == "admin"}
        for service in ("llm", "search", "image"):
            value = dict(payload.get(service, {}))
            api_key = value.pop("api_key", "")
            if service == "image" and payload.get(service):
                raw_image_source = value.pop("source", "")
                image_source = infer_image_source(
                    value.get("provider", "pillow"), value.get("base_url", ""), raw_image_source
                )
            else:
                image_source = ""
            result["services"][service] = {
                **value,
                **({"image_source": image_source} if service == "image" else {}),
                "configured": bool(payload.get(service)),
                "api_key": {"configured": bool(api_key), "masked": self._masked(api_key)},
                "source": "personal" if payload.get(service) else ("system" if role == "admin" else "missing"),
            }
        result["updated_at"] = self.status(user_id)["updated_at"]
        return result

    def effective_config(
        self, base: Config, user: Dict[str, Any], required: Iterable[str]
    ) -> Tuple[Config, Dict[str, str]]:
        cfg = deepcopy(base)
        payload = self.get(user["id"])
        sources: Dict[str, str] = {}
        for service in required:
            if service not in self.SERVICES:
                continue
            personal = payload.get(service)
            if not personal:
                if user["role"] != "admin":
                    labels = {"llm": "LLM", "search": "Tavily 搜索", "image": "文生图"}
                    raise AuthError(
                        f"请先在“我的 API 配置”中配置个人{labels[service]}服务",
                        f"USER_{service.upper()}_CONFIG_REQUIRED",
                    )
                sources[service] = "system"
                continue
            if service == "llm":
                cfg.llm = LLMConfig.from_dict({**cfg.llm.__dict__, **personal})
            elif service == "search":
                cfg.search = SearchConfig(**{**cfg.search.__dict__, **personal})
            elif service == "image":
                cfg.image = ImageConfig(**{**cfg.image.__dict__, **personal})
            sources[service] = "personal"
        return cfg, sources
