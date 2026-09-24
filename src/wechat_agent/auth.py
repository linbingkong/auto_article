"""用户认证模块：JWT Token 签发/验证、密码哈希。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict

import bcrypt


class AuthError(Exception):
    """认证相关错误，code 供 API 与前端稳定判断。"""

    def __init__(self, message: str, code: str = "AUTH_ERROR"):
        self.code = code
        super().__init__(message)


class JWTManager:
    """简单的 JWT 管理器（不依赖 PyJWT）。"""

    def __init__(self, secret: str, access_expire_hours: int = 2, refresh_expire_days: int = 7):
        self.secret = secret
        self.access_expire = access_expire_hours * 3600
        self.refresh_expire = refresh_expire_days * 86400
        self._revoked: set = set()

    def _b64_encode(self, data: bytes) -> str:
        import base64
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    def _b64_decode(self, data: str) -> bytes:
        import base64
        padding = 4 - len(data) % 4
        if padding != 4:
            data += "=" * padding
        return base64.urlsafe_b64decode(data)

    def _sign(self, header: str, payload: str) -> str:
        msg = f"{header}.{payload}".encode()
        sig = hmac.new(self.secret.encode(), msg, hashlib.sha256).digest()
        return self._b64_encode(sig)

    def create_access_token(self, user_id: str, username: str, role: str) -> str:
        header = self._b64_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload_data = {
            "sub": user_id,
            "username": username,
            "role": role,
            "type": "access",
            "iat": int(time.time()),
            "exp": int(time.time()) + self.access_expire,
            "jti": secrets.token_hex(16),
        }
        payload = self._b64_encode(json.dumps(payload_data).encode())
        sig = self._sign(header, payload)
        return f"{header}.{payload}.{sig}"

    def create_refresh_token(self, user_id: str) -> str:
        header = self._b64_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload_data = {
            "sub": user_id,
            "type": "refresh",
            "iat": int(time.time()),
            "exp": int(time.time()) + self.refresh_expire,
            "jti": secrets.token_hex(16),
        }
        payload = self._b64_encode(json.dumps(payload_data).encode())
        sig = self._sign(header, payload)
        return f"{header}.{payload}.{sig}"

    def verify_token(self, token: str, expected_type: str = "access") -> Dict[str, Any]:
        parts = token.split(".")
        if len(parts) != 3:
            raise AuthError("无效的 Token 格式")
        header, payload, sig = parts
        expected_sig = self._sign(header, payload)
        if not hmac.compare_digest(sig, expected_sig):
            raise AuthError("Token 签名无效")
        try:
            data = json.loads(self._b64_decode(payload))
        except Exception:
            raise AuthError("Token 内容解析失败")
        jti = data.get("jti")
        if jti in self._revoked:
            raise AuthError("Token 已吊销")
        if data.get("type") != expected_type:
            raise AuthError(f"Token 类型错误，期望 {expected_type}")
        if data.get("exp", 0) < time.time():
            raise AuthError("Token 已过期")
        return data

    def revoke_token(self, jti: str) -> None:
        self._revoked.add(jti)


def hash_password(password: str) -> str:
    """密码哈希（bcrypt）。"""
    if len(password) < 8:
        raise AuthError("密码长度至少 8 位")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode(), salt).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码。"""
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def generate_token() -> str:
    """生成随机 Token（用于 API Key 等）。"""
    return secrets.token_urlsafe(32)