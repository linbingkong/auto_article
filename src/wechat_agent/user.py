"""用户、公开注册申请与人工审批存储。"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .auth import AuthError, hash_password, verify_password
from .database import StorageTarget, connect_database, is_mysql, verify_mysql_tables


class UserStore:
    """SQLite/MySQL 用户存储；手机号只保存 HMAC 和末四位。"""

    VALID_ROLES = {"admin", "editor", "creator", "viewer"}
    VALID_STATUSES = {"pending_approval", "active", "rejected", "disabled"}
    PUBLIC_ROLES = {"editor", "creator", "viewer"}

    def __init__(self, db_path: StorageTarget):
        self.db_path = db_path
        if is_mysql(db_path):
            verify_mysql_tables(db_path, ["users"])
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _connect(self) -> Any:
        return connect_database(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'viewer',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            migrations = {
                "phone_hash": "TEXT",
                "phone_last4": "TEXT",
                "phone": "TEXT DEFAULT ''",
                "registration_token_hash": "TEXT",
                "applied_at": "TEXT",
                "reviewed_at": "TEXT",
                "reviewed_by": "TEXT",
                "review_note": "TEXT",
            }
            for name, sql_type in migrations.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {name} {sql_type}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_status_applied ON users(status, applied_at)")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_hash "
                "ON users(phone_hash) WHERE phone_hash IS NOT NULL AND phone_hash <> ''"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_registration_token "
                "ON users(registration_token_hash) "
                "WHERE registration_token_hash IS NOT NULL AND registration_token_hash <> ''"
            )

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _validate_username(username: str) -> str:
        username = (username or "").strip()
        if len(username) < 3 or len(username) > 32:
            raise AuthError("用户名长度 3-32 位", "INVALID_USERNAME")
        if not username.isalnum():
            raise AuthError("用户名只能包含字母和数字", "INVALID_USERNAME")
        return username

    @staticmethod
    def normalize_phone(phone: str) -> str:
        value = re.sub(r"[\s()-]", "", phone or "")
        if value.startswith("+86"):
            value = value[3:]
        elif value.startswith("0086"):
            value = value[4:]
        if not re.fullmatch(r"1[3-9]\d{9}", value):
            raise AuthError("请输入有效的中国大陆手机号", "INVALID_PHONE")
        return value

    @staticmethod
    def mask_phone(last4: str) -> str:
        return f"*******{last4}" if last4 else ""

    @staticmethod
    def _phone_secret() -> bytes:
        secret = os.environ.get("PHONE_HASH_SECRET", "").strip()
        if len(secret) < 32:
            raise AuthError(
                "公开注册未安全配置：PHONE_HASH_SECRET 至少需要 32 个字符",
                "REGISTRATION_SECRET_MISSING",
            )
        return secret.encode("utf-8")

    @classmethod
    def phone_hash(cls, phone: str) -> Tuple[str, str]:
        normalized = cls.normalize_phone(phone)
        digest = hmac.new(cls._phone_secret(), normalized.encode(), hashlib.sha256).hexdigest()
        return digest, normalized[-4:]

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _public_user(row: Dict) -> Dict:
        return {
            key: row.get(key)
            for key in (
                "id", "username", "role", "status", "phone_last4", "applied_at",
                "reviewed_at", "reviewed_by", "review_note", "created_at", "updated_at",
            )
        }

    def create_user(
        self,
        username: str,
        password: str,
        role: str = "viewer",
        status: str = "active",
    ) -> Dict:
        """管理员创建正式用户。"""
        username = self._validate_username(username)
        if role not in self.VALID_ROLES:
            raise AuthError(f"无效角色：{role}", "INVALID_ROLE")
        if status not in self.VALID_STATUSES:
            raise AuthError(f"无效状态：{status}", "INVALID_STATUS")
        user_id, now = uuid.uuid4().hex, self._now()
        with self._connect() as conn:
            try:
                conn.execute(
                    """INSERT INTO users
                    (id, username, password_hash, role, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, username, hash_password(password), role, status, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise AuthError("用户名已存在", "USERNAME_EXISTS") from exc
        return self._public_user(self.get_user(user_id) or {})

    def create_registration(self, username: str, password: str, phone: str) -> Dict:
        username = self._validate_username(username)
        normalized_phone = self.normalize_phone(phone)
        phone_digest, last4 = self.phone_hash(normalized_phone)
        user_id, now = uuid.uuid4().hex, self._now()
        token = secrets.token_urlsafe(32)
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO users
                    (id, username, password_hash, role, status, phone_hash, phone_last4, phone,
                     registration_token_hash, applied_at, created_at, updated_at)
                    VALUES (?, ?, ?, 'viewer', 'pending_approval', ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id, username, hash_password(password), phone_digest, last4,
                        normalized_phone,
                        self._token_hash(token), now, now, now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            message = str(exc).lower()
            code = "PHONE_EXISTS" if "phone_hash" in message else "USERNAME_EXISTS"
            raise AuthError("该用户名或手机号已有账号/申请", code) from exc
        return {
            "id": user_id,
            "username": username,
            "status": "pending_approval",
            "masked_phone": self.mask_phone(last4),
            "registration_token": token,
            "applied_at": now,
        }

    def get_user(self, user_id: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def authenticate(self, username: str, password: str) -> Dict:
        user = self.get_user_by_username(username)
        if not user or not verify_password(password, user["password_hash"]):
            raise AuthError("用户名或密码错误", "INVALID_CREDENTIALS")
        status = user["status"]
        if status == "pending_approval":
            raise AuthError("账号正在等待管理员审核", "PENDING_APPROVAL")
        if status == "rejected":
            raise AuthError(user.get("review_note") or "注册申请未通过", "REGISTRATION_REJECTED")
        if status == "disabled":
            raise AuthError("账号已被禁用", "ACCOUNT_DISABLED")
        if status != "active":
            raise AuthError("账号状态异常", "ACCOUNT_UNAVAILABLE")
        return {"id": user["id"], "username": user["username"], "role": user["role"], "status": status}

    def registration_status(self, token: str) -> Dict:
        token_hash = self._token_hash(token or "")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE registration_token_hash = ?", (token_hash,)
            ).fetchone()
        if not row:
            raise AuthError("申请状态凭证无效", "INVALID_REGISTRATION_TOKEN")
        user = dict(row)
        return {
            "username": user["username"],
            "status": user["status"],
            "masked_phone": self.mask_phone(user.get("phone_last4") or ""),
            "applied_at": user.get("applied_at"),
            "reviewed_at": user.get("reviewed_at"),
            "review_note": user.get("review_note") if user["status"] == "rejected" else "",
        }

    def resubmit_registration(self, token: str, phone: str, password: str) -> Dict:
        token_hash = self._token_hash(token or "")
        normalized_phone = self.normalize_phone(phone)
        phone_digest, last4 = self.phone_hash(normalized_phone)
        new_token = secrets.token_urlsafe(32)
        now = self._now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE registration_token_hash = ?", (token_hash,)
            ).fetchone()
            if not row:
                raise AuthError("申请状态凭证无效", "INVALID_REGISTRATION_TOKEN")
            if row["status"] != "rejected":
                raise AuthError("只有已拒绝的申请可以重新提交", "INVALID_REGISTRATION_STATE")
            try:
                conn.execute(
                    """UPDATE users SET password_hash=?, phone_hash=?, phone_last4=?, phone=?,
                    registration_token_hash=?, status='pending_approval', applied_at=?,
                    reviewed_at=NULL, reviewed_by=NULL, review_note=NULL, updated_at=?
                    WHERE id=?""",
                    (
                        hash_password(password), phone_digest, last4, normalized_phone,
                        self._token_hash(new_token), now, now, row["id"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise AuthError("该手机号已有账号/申请", "PHONE_EXISTS") from exc
        return {
            "id": row["id"], "username": row["username"],
            "status": "pending_approval", "masked_phone": self.mask_phone(last4),
            "registration_token": new_token, "applied_at": now,
        }

    def find_registration_by_phone(self, phone: str) -> Optional[Dict]:
        phone_digest, _ = self.phone_hash(phone)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE phone_hash = ? AND status IN ('pending_approval','rejected')",
                (phone_digest,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        return self._public_user(data) | {"masked_phone": self.mask_phone(data.get("phone_last4") or ""), "phone": data.get("phone") or ""}

    def list_registrations(self, status: str = "pending_approval", limit: int = 100, offset: int = 0) -> List[Dict]:
        allowed = {"pending_approval", "rejected", "active"}
        if status not in allowed:
            raise AuthError("不支持的申请状态", "INVALID_STATUS")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE status = ? AND phone_hash IS NOT NULL "
                "ORDER BY COALESCE(applied_at, created_at) DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        return [self._public_user(dict(row)) | {"masked_phone": self.mask_phone(row["phone_last4"]), "phone": row["phone"] or ""} for row in rows]

    def count_registrations(self, status: str = "pending_approval") -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM users WHERE status=? AND phone_hash IS NOT NULL",
                (status,),
            ).fetchone()
        return int(row["cnt"])

    def approve_registration(
        self,
        user_id: str,
        reviewer_id: str,
        role: str = "creator",
        note: str = "",
    ) -> Dict:
        if role not in self.PUBLIC_ROLES:
            raise AuthError("公开注册申请只能批准为只读、创作者或编辑", "INVALID_ROLE")
        now = self._now()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise AuthError("申请不存在", "REGISTRATION_NOT_FOUND")
            if row["status"] != "pending_approval" or not row["phone_hash"]:
                raise AuthError("申请状态不允许批准", "INVALID_REGISTRATION_STATE")
            conn.execute(
                """UPDATE users SET status='active', role=?, reviewed_at=?, reviewed_by=?,
                review_note=?, updated_at=? WHERE id=?""",
                (role, now, reviewer_id, note.strip()[:500], now, user_id),
            )
        return self._public_user(self.get_user(user_id) or {})

    def reject_registration(self, user_id: str, reviewer_id: str, reason: str) -> Dict:
        reason = (reason or "").strip()
        if not reason:
            raise AuthError("拒绝原因不能为空", "REVIEW_REASON_REQUIRED")
        now = self._now()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise AuthError("申请不存在", "REGISTRATION_NOT_FOUND")
            if row["status"] != "pending_approval" or not row["phone_hash"]:
                raise AuthError("申请状态不允许拒绝", "INVALID_REGISTRATION_STATE")
            conn.execute(
                """UPDATE users SET status='rejected', reviewed_at=?, reviewed_by=?,
                review_note=?, updated_at=? WHERE id=?""",
                (now, reviewer_id, reason[:500], now, user_id),
            )
        return self._public_user(self.get_user(user_id) or {})

    def update_user(
        self,
        user_id: str,
        *,
        role: Optional[str] = None,
        status: Optional[str] = None,
        password: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Dict:
        user = self.get_user(user_id)
        if not user:
            raise AuthError("用户不存在", "USER_NOT_FOUND")
        if role is not None and role not in self.VALID_ROLES:
            raise AuthError(f"无效角色：{role}", "INVALID_ROLE")
        if status is not None and status not in self.VALID_STATUSES:
            raise AuthError(f"无效状态：{status}", "INVALID_STATUS")
        removes_admin = user["role"] == "admin" and user["status"] == "active" and (
            (role is not None and role != "admin") or (status is not None and status != "active")
        )
        if removes_admin and self._active_admin_count() <= 1:
            raise AuthError("不能禁用或降级最后一个管理员", "LAST_ADMIN_PROTECTED")
        phone_update: Optional[tuple] = None
        if phone is not None and phone.strip():
            normalized = self.normalize_phone(phone)
            digest, last4 = self.phone_hash(normalized)
            with self._connect() as conn:
                conflict = conn.execute(
                    "SELECT username FROM users WHERE phone_hash = ? AND id <> ?",
                    (digest, user_id),
                ).fetchone()
            if conflict:
                raise AuthError(f"该手机号已绑定账号 {conflict['username']}", "PHONE_EXISTS")
            phone_update = (normalized, digest, last4)
        updates, values = [], []
        if role is not None:
            updates.append("role = ?"); values.append(role)
        if status is not None:
            updates.append("status = ?"); values.append(status)
        if password is not None:
            updates.append("password_hash = ?"); values.append(hash_password(password))
        if not updates and phone_update is None:
            return self._public_user(user)
        updates.append("updated_at = ?"); values.append(self._now())
        if phone_update is not None:
            updates.extend(["phone = ?", "phone_hash = ?", "phone_last4 = ?"])
            values.extend(phone_update)
        values.append(user_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", values)
        return self._public_user(self.get_user(user_id) or {})

    def delete_user(self, user_id: str) -> Dict:
        """删除账号；最后一个有效管理员受保护。"""
        user = self.get_user(user_id)
        if not user:
            raise AuthError("用户不存在", "USER_NOT_FOUND")
        if user["role"] == "admin" and user["status"] == "active" and self._active_admin_count() <= 1:
            raise AuthError("不能删除最后一个管理员", "LAST_ADMIN_PROTECTED")
        with self._connect() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return self._public_user(user)

    def _active_admin_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM users WHERE role='admin' AND status='active'"
            ).fetchone()
        return int(row["cnt"])

    def list_users(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE status IN ('active','disabled') "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [self._public_user(dict(row)) | {"masked_phone": self.mask_phone(row["phone_last4"] or ""), "phone": row["phone"] or ""} for row in rows]

    def count_users(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM users WHERE status IN ('active','disabled')"
            ).fetchone()
        return int(row["cnt"])

    def ensure_admin_exists(self) -> Optional[Dict[str, str]]:
        """首次启动创建管理员；未提供初始密码时生成一次性随机密码。"""
        if self._active_admin_count() > 0:
            return None
        configured = os.environ.get("INITIAL_ADMIN_PASSWORD", "").strip()
        password = configured or secrets.token_urlsafe(18)
        self.create_user("admin", password, role="admin")
        return {"username": "admin", "generated_password": "" if configured else password}
