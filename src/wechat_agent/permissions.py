"""权限控制模块：角色装饰器和权限检查。"""

from __future__ import annotations

from functools import wraps
from typing import Callable, Optional, Set

from fastapi import HTTPException, Request, status

from .auth import AuthError, JWTManager


# 角色权限矩阵
ROLE_PERMISSIONS = {
    "admin": {
        "article.view", "article.create", "article.edit", "article.delete",
        "article.push", "task.control", "config.edit", "user.manage", "audit.view",
    },
    "editor": {
        "article.view", "article.create", "article.edit",
        "article.push", "task.control",
    },
    "creator": {
        "article.view", "article.create", "article.edit", "article.delete", "task.control",
    },
    "viewer": {
        "article.view",
    },
}


def has_permission(role: str, permission: str) -> bool:
    """检查角色是否有指定权限。"""
    perms = ROLE_PERMISSIONS.get(role, set())
    return permission in perms


def require_permission(permission: str) -> Callable:
    """装饰器：要求请求用户具有指定权限。"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request: Optional[Request] = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if request is None:
                for kwarg in kwargs.values():
                    if isinstance(kwarg, Request):
                        request = kwarg
                        break
            
            if request is None:
                raise HTTPException(status_code=500, detail="无法获取请求对象")
            
            user = getattr(request.state, "user", None)
            if not user:
                raise HTTPException(status_code=401, detail="未登录")
            
            if not has_permission(user["role"], permission):
                raise HTTPException(
                    status_code=403,
                    detail=f"权限不足：需要 {permission}，当前角色 {user['role']}"
                )
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def require_role(*roles: str) -> Callable:
    """装饰器：要求请求用户具有指定角色之一。"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request: Optional[Request] = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if request is None:
                for kwarg in kwargs.values():
                    if isinstance(kwarg, Request):
                        request = kwarg
                        break
            
            if request is None:
                raise HTTPException(status_code=500, detail="无法获取请求对象")
            
            user = getattr(request.state, "user", None)
            if not user:
                raise HTTPException(status_code=401, detail="未登录")
            
            if user["role"] not in roles:
                raise HTTPException(
                    status_code=403,
                    detail=f"角色不足：需要 {roles}，当前角色 {user['role']}"
                )
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def extract_user_from_request(request: Request, jwt_manager: JWTManager, legacy_token: Optional[str] = None) -> Optional[dict]:
    """从请求中提取用户信息（JWT 或兼容旧 Token）。"""
    auth_header = request.headers.get("Authorization", "")
    
    # JWT Bearer Token
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            payload = jwt_manager.verify_token(token, expected_type="access")
            return {
                "id": payload["sub"],
                "username": payload["username"],
                "role": payload["role"],
            }
        except AuthError:
            return None
    
    # 兼容旧 X-Admin-Token（映射为 admin）
    if legacy_token and request.headers.get("X-Admin-Token") == legacy_token:
        return {
            "id": "legacy-admin",
            "username": "legacy-admin",
            "role": "admin",
        }
    
    return None