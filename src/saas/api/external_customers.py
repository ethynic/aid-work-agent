"""
外部接待客户 API

路由：/api/saas/external-customers/*
- 外部用户列表查询（source 不为空的外部用户）
- 外部用户会话列表
- 会话消息查询

外部用户：指 users.source 不为空的客户，如 wecom_kf（企业微信客服）
"""

from typing import Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.saas.api.tenant_auth import require_admin
from src.db.models import UserDB, SessionDB, MessageDB
from src.config.settings import settings

router = APIRouter(prefix="/api/saas/external-customers", tags=["外部接待客户"])


# ============== 请求模型 ==============

class ExternalUserQuery(BaseModel):
    username: Optional[str] = Field(None, description="用户名/昵称搜索")
    source: Optional[str] = Field(None, description="用户来源")
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")


class SessionQuery(BaseModel):
    instance_id: Optional[str] = Field(None, description="数字员工实例ID")
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")


class MessageQuery(BaseModel):
    content_search: Optional[str] = Field(None, description="聊天内容搜索")
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(50, ge=1, le=200, description="每页数量")


# ============== API 端点 ==============

@router.get("/users")
async def list_external_users(
    request: Request,
    username: Optional[str] = None,
    source: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """获取外部用户列表（source 不为空的租户用户）

    Args:
        username: 用户名/昵称搜索（可选）
        source: 用户来源筛选（可选）
        page: 页码
        page_size: 每页数量
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    result = UserDB.list_external_users(
        tenant_id=tenant_id,
        username=username,
        source=source,
        page=page,
        page_size=page_size,
    )
    return {"success": True, **result}


@router.get("/users/{user_id}/sessions")
async def get_user_sessions(
    request: Request,
    user_id: str,
    instance_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """获取外部用户的会话列表

    Args:
        user_id: 用户ID
        instance_id: 数字员工实例ID筛选（可选）
        page: 页码
        page_size: 每页数量
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    # 验证用户属于该租户
    user = UserDB.get_by_id(user_id)
    if not user or user.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="用户不存在或不属于该租户")

    # 查询 channel_sessions（渠道会话表）
    from src.channels.session import channel_session_manager

    sessions = channel_session_manager.list_sessions(
        channel_type=None,
        user_id=user_id,
        tenant_id=tenant_id,
        limit=page_size,
    )

    # 按 updated_at 降序排序并分页
    sessions.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    total = len(sessions)
    start = (page - 1) * page_size
    end = start + page_size
    paginated_sessions = sessions[start:end]

    return {"success": True, "sessions": paginated_sessions, "total": total, "page": page, "page_size": page_size}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    request: Request,
    session_id: str,
    content_search: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    """获取会话的消息列表（支持聊天内容搜索）

    Args:
        session_id: 会话ID
        content_search: 聊天内容搜索（可选）
        page: 页码
        page_size: 每页数量
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    # 验证会话属于该租户
    session = SessionDB.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    result = UserDB.get_session_messages(
        session_id=session_id,
        content_search=content_search,
        page=page,
        page_size=page_size,
    )
    return {"success": True, **result}

