"""
租户上下文中间件

根据请求 URL 和认证信息解析 tenant_id，设置到 request.state 和 ContextVar。

路由分发逻辑：
- /api/saas/*  → 从 Authorization header 取管理员 token → 查 tokens + users → 得 tenant_id
- /api/chat/*  → 从 Authorization header 取用户 token → 查 users.tenant_id → 得 tenant_id
- /api/sessions/* → 从 Authorization header 取 token → 优先用 X-Tenant-Id header → 得 tenant_id
- /t/{tenant_id}/*/callback → 从 URL path 取 tenant_id

注意：platform_admin (平台管理员) tenant_id 为空，可以访问所有租户
"""

import re
from typing import Optional

from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from src.saas.context import set_tenant_context, clear_tenant_context
from src.db.database import get_db_connection


# 匹配租户级回调路由：/t/{tenant_id}/...
_TENANT_CALLBACK_PATTERN = re.compile(r"^/t/([^/]+)/")


class TenantContextMiddleware(BaseHTTPMiddleware):
    """
    租户上下文中间件

    在每个请求开始时解析 tenant_id 并设置到：
    - request.state.tenant_id
    - ContextVar (current_tenant_id)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        tenant_id = None
        user_id = None

        try:
            path = request.url.path

            # 1. 租户级回调路由：/t/{tenant_id}/...
            tenant_match = _TENANT_CALLBACK_PATTERN.match(path)
            if tenant_match:
                tenant_id = tenant_match.group(1)
                logger.debug(f"[TenantMiddleware] Tenant callback: tenant_id={tenant_id}")

            # 2. SaaS 管理 API：/api/saas/*
            elif path.startswith("/api/saas/"):
                tenant_id, user_id = await self._resolve_admin_tenant(request)
                if tenant_id:
                    logger.debug(f"[TenantMiddleware] SaaS admin: tenant_id={tenant_id}")

            # 3. 会话 API：/api/sessions/*
            elif path.startswith("/api/sessions"):
                tenant_id, user_id = await self._resolve_session_tenant(request)
                if tenant_id:
                    logger.debug(f"[TenantMiddleware] Sessions: tenant_id={tenant_id}")

            # 4. 普通 Chat API：/api/chat/*
            elif path.startswith("/api/chat"):
                tenant_id, user_id = await self._resolve_user_tenant(request)
                if tenant_id:
                    logger.debug(f"[TenantMiddleware] Chat user: tenant_id={tenant_id}")

            # 5. 其他 API 路径（凭据、知识库、定时任务、客户管理等）：从用户 token 解析 tenant_id
            elif path.startswith("/api/"):
                tenant_id, user_id = await self._resolve_user_tenant(request)
                if tenant_id:
                    logger.debug(f"[TenantMiddleware] API fallback: tenant_id={tenant_id}")

        except Exception as e:
            logger.warning(f"[TenantMiddleware] Error resolving tenant context: {e}")

        # 设置上下文
        request.state.tenant_id = tenant_id
        set_tenant_context(tenant_id, user_id=user_id)

        try:
            response = await call_next(request)
        finally:
            # 清理上下文
            clear_tenant_context()

        return response

    async def _resolve_admin_tenant(self, request: Request) -> tuple:
        """
        从管理员 token 解析 tenant_id 和 user_id

        平台管理员 (role=platform_admin) 可通过 X-Tenant-Id Header 指定目标租户
        租户管理员 (role=tenant_admin) 返回其 tenant_id

        Returns: (tenant_id, user_id)
        """
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None, None

        token = auth_header[7:]

        # 复用统一的 token 验证服务
        from src.api.auth import verify_token
        user_id = verify_token(token)
        if not user_id:
            return None, None

        # 查询用户信息获取 role 和 tenant_id
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT role, tenant_id FROM users WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            if not row:
                return None, user_id

            role = row["role"]

            # 平台管理员：优先使用 X-Tenant-Id Header（代管理）
            if role == "platform_admin":
                x_tenant_id = request.headers.get("X-Tenant-Id")
                if x_tenant_id:
                    # 验证目标租户存在
                    from src.saas.db.tenant_db import TenantDB
                    target_tenant = TenantDB.get_by_id(x_tenant_id)
                    if target_tenant:
                        return x_tenant_id, user_id
                return None, user_id

            # tenant_admin 返回其 tenant_id
            return row["tenant_id"], user_id

    async def _resolve_user_tenant(self, request: Request) -> tuple:
        """
        从请求解析 tenant_id 和 user_id

        优先级：
        1. X-Tenant-Id Header（平台管理员代管理时由前端传入）
        2. 用户自身的 tenant_id（租户管理员/普通用户）

        Returns: (tenant_id, user_id)
        """
        # 先尝试 X-Tenant-Id Header（平台管理员代租户操作）
        x_tenant_id = request.headers.get("X-Tenant-Id")
        if x_tenant_id:
            # 验证目标租户存在
            from src.saas.db.tenant_db import TenantDB
            target_tenant = TenantDB.get_by_id(x_tenant_id)
            if target_tenant:
                # 仍需解析 user_id
                user_id = self._resolve_user_id_from_request(request)
                return x_tenant_id, user_id

        # 回退到用户自身的 tenant_id
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None, None

        token = auth_header[7:]

        # 先验证用户 token
        from src.api.auth import verify_token
        from src.db.database import get_db_connection

        user_id = verify_token(token)
        if not user_id:
            return None, None

        # 查询用户的 tenant_id
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT tenant_id FROM users WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            if row and row["tenant_id"]:
                return row["tenant_id"], user_id

        # 演示模式用户没有 tenant_id 时，使用演示租户
        from src.config.settings import settings
        demo_enabled = getattr(settings, "demo", None) and getattr(settings.demo, "enabled", False)
        if demo_enabled:
            return "demo", user_id

        return None, user_id

    async def _resolve_session_tenant(self, request: Request) -> tuple:
        """
        解析会话 API 的 tenant_id 和 user_id

        优先级：
        1. X-Tenant-Id Header（平台管理员代管理时由前端传入）
        2. 用户自身的 tenant_id（租户管理员/普通用户）

        Returns: (tenant_id, user_id)
        """
        # 先尝试 X-Tenant-Id Header
        x_tenant_id = request.headers.get("X-Tenant-Id")
        if x_tenant_id:
            # 验证目标租户存在
            from src.saas.db.tenant_db import TenantDB
            target_tenant = TenantDB.get_by_id(x_tenant_id)
            if target_tenant:
                user_id = self._resolve_user_id_from_request(request)
                return x_tenant_id, user_id

        # 回退到用户自身的 tenant_id
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None, None

        token = auth_header[7:]
        from src.api.auth import verify_token
        user_id = verify_token(token)
        if not user_id:
            return None, None

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT role, tenant_id FROM users WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            if row and row["tenant_id"]:
                return row["tenant_id"], user_id
        return None, user_id

    def _resolve_user_id_from_request(self, request: Request) -> Optional[str]:
        """从请求的 Authorization header 解析 user_id"""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[7:]
        from src.api.auth import verify_token
        return verify_token(token)
