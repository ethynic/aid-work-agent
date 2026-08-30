"""
租户上下文中间件

根据请求 URL 和认证信息解析 tenant_id，设置到 request.state 和 ContextVar。

路由分发逻辑：
- /api/saas/*  → 从 Authorization header 取管理员 token → 查 tokens + users → 得 tenant_id
- /api/chat/*  → 从 Authorization header 取用户 token → 查 users.tenant_id → 得 tenant_id
- /api/sessions/* → 从 Authorization header 取 token → 优先用 X-Tenant-Id header → 得 tenant_id
- /t/{tenant_id}/*/callback → 从 URL path 取 tenant_id

注意：platform_admin (平台管理员) tenant_id 为空，可以访问所有租户

X-Tenant-Id 头采纳规则（防伪造）：仅在完整认证链（Bearer token → verify_token
→ 查 users 行）通过后生效：
- platform_admin：可指定任意「存在」的租户（代管理）；目标租户不存在（如拼写
  错误）同样抛 TenantHeaderDenied 由 dispatch 拒绝（403），绝不静默回退全局
  视图——否则「指定租户」的意图会被扩大为「全部租户」
- 其他角色：仅当 header 值等于自身 tenant_id 时采纳；不一致抛 TenantHeaderDenied
  由 dispatch 拒绝（403）
- 无有效 token：header 一律不采纳，按原回退逻辑处理（匿名语义不变）
- platform_admin 无 X-Tenant-Id 头：保持全局语义（合法入口，不 403）
"""

import re
from typing import Optional

from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from src.saas.context import set_tenant_context, clear_tenant_context
from src.db.database import get_db_connection


# 匹配租户级回调路由：/t/{tenant_id}/...
_TENANT_CALLBACK_PATTERN = re.compile(r"^/t/([^/]+)/")


class TenantHeaderDenied(Exception):
    """X-Tenant-Id 头与请求者身份不匹配（已认证用户试图指定他人/不存在的租户）

    覆盖两种情形，行为统一由 dispatch 外层捕获并返回 403 固定文案：
    - 已认证的非 platform_admin 指定他人租户
    - 已认证的 platform_admin 指定不存在的租户（不回退全局视图）
    携带 user_id 供日志记录（不记租户值，不泄漏租户存在性）。
    """

    def __init__(self, user_id: Optional[str] = None):
        super().__init__("X-Tenant-Id 与请求者身份不匹配")
        self.user_id = user_id


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

        # 认证成功时由各解析器写入角色（未认证保持 None），
        # 供下游（如知识库平台管理员全局视图）判定请求者身份
        request.state.user_role = None

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

        except TenantHeaderDenied as e:
            # 已认证用户伪造他人租户：拒绝整个请求，不进入业务处理
            # 日志只记请求路径与 user_id，不记租户值
            logger.warning(
                f"[TenantMiddleware] Denied cross-tenant X-Tenant-Id: "
                f"path={request.url.path}, user_id={e.user_id}"
            )
            return JSONResponse(status_code=403, content={"detail": "无权访问指定租户"})
        except Exception as e:
            logger.warning(f"[TenantMiddleware] Error resolving tenant context: {e}")

        # 设置上下文
        request.state.tenant_id = tenant_id
        request.state.user_id = user_id
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
            # 认证成功暴露角色给下游（未认证保持 None）
            request.state.user_role = role

            # 平台管理员：优先使用 X-Tenant-Id Header（代管理）
            if role == "platform_admin":
                x_tenant_id = request.headers.get("X-Tenant-Id")
                if x_tenant_id:
                    # 验证目标租户存在；不存在（如拼写错误）拒绝请求，
                    # 与 _adopt_header_tenant 行为统一，防止静默回退全局视图
                    from src.saas.db.tenant_db import TenantDB
                    target_tenant = TenantDB.get_by_id(x_tenant_id)
                    if not target_tenant:
                        raise TenantHeaderDenied(user_id=user_id)
                    return x_tenant_id, user_id
                # 无 header：保持全局语义（合法入口，不 403）
                return None, user_id

            # tenant_admin 返回其 tenant_id
            return row["tenant_id"], user_id

    def _authenticate_user(self, request: Request) -> tuple:
        """完整认证链：Bearer token → verify_token → 查 users 行获取 role 与 tenant_id

        Returns: (user_id, role, user_tenant_id)
        - 无 Authorization 头 / token 无效：三者均为 None
        - token 有效但 users 行不存在：(user_id, None, None)
        """
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None, None, None

        token = auth_header[7:]

        from src.api.auth import verify_token
        user_id = verify_token(token)
        if not user_id:
            return None, None, None

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT role, tenant_id FROM users WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()

        if not row:
            return user_id, None, None
        return user_id, row["role"], row["tenant_id"]

    def _adopt_header_tenant(
        self,
        x_tenant_id: str,
        user_id: Optional[str],
        role: str,
        user_tenant_id: Optional[str],
    ) -> Optional[str]:
        """在已认证前提下裁决 X-Tenant-Id 是否可采纳（调用方保证 role 非空）

        - platform_admin：任意「存在」的租户可采纳（代管理）；目标租户不存在
          （如拼写错误）抛 TenantHeaderDenied 拒绝请求，不静默回退全局视图
        - 其他角色：仅当 header 值等于自身 tenant_id 时采纳；不一致抛 TenantHeaderDenied
        """
        if role == "platform_admin":
            from src.saas.db.tenant_db import TenantDB
            target_tenant = TenantDB.get_by_id(x_tenant_id)
            if not target_tenant:
                # 指定租户的意图不得静默扩大为全部租户：不存在即拒绝
                raise TenantHeaderDenied(user_id=user_id)
            return x_tenant_id

        if x_tenant_id == user_tenant_id:
            return x_tenant_id

        raise TenantHeaderDenied(user_id=user_id)

    async def _resolve_user_tenant(self, request: Request) -> tuple:
        """
        从请求解析 tenant_id 和 user_id

        优先级：
        1. X-Tenant-Id Header：仅完整认证链通过后可采纳（platform_admin 可指定
           任意存在租户；其他角色仅当与自身 tenant_id 一致，否则拒绝请求）
        2. 用户自身的 tenant_id（租户管理员/普通用户）

        Returns: (tenant_id, user_id)
        """
        # 完整认证链是 X-Tenant-Id 采纳的前置条件（防伪造）
        user_id, role, user_tenant_id = self._authenticate_user(request)
        # 认证成功暴露角色给下游（未认证保持 None）
        request.state.user_role = role

        x_tenant_id = request.headers.get("X-Tenant-Id")
        if x_tenant_id and role:
            adopted = self._adopt_header_tenant(x_tenant_id, user_id, role, user_tenant_id)
            if adopted:
                return adopted, user_id

        # 回退到用户自身的 tenant_id
        if not user_id:
            return None, None
        if user_tenant_id:
            return user_tenant_id, user_id

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
        1. X-Tenant-Id Header：仅完整认证链通过后可采纳（platform_admin 可指定
           任意存在租户；其他角色仅当与自身 tenant_id 一致，否则拒绝请求）
        2. 用户自身的 tenant_id（租户管理员/普通用户）

        Returns: (tenant_id, user_id)
        """
        # 完整认证链是 X-Tenant-Id 采纳的前置条件（防伪造）
        user_id, role, user_tenant_id = self._authenticate_user(request)
        # 认证成功暴露角色给下游（未认证保持 None）
        request.state.user_role = role

        x_tenant_id = request.headers.get("X-Tenant-Id")
        if x_tenant_id and role:
            adopted = self._adopt_header_tenant(x_tenant_id, user_id, role, user_tenant_id)
            if adopted:
                return adopted, user_id

        # 回退到用户自身的 tenant_id
        if not user_id:
            return None, None
        if user_tenant_id:
            return user_tenant_id, user_id
        return None, user_id
