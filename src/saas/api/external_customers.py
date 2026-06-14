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
from src.db.models import UserDB
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

    from src.channels.session import channel_session_manager

    # 验证渠道会话属于该租户
    session = channel_session_manager.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    result = channel_session_manager.get_messages_paginated(
        session_id=session_id,
        content_search=content_search,
        page=page,
        page_size=page_size,
    )
    return {"success": True, **result}


@router.get("/attachments/{attachment_ref}/download")
async def download_attachment(
    request: Request,
    attachment_ref: str,
):
    """下载已保存的附件（用于外部接待页面回显）

    Args:
        attachment_ref: 格式为 {session_id}/{local_path_basename}（URL-safe 编码）

    Returns:
        文件二进制内容
    """
    import os as _os
    from urllib.parse import unquote
    from fastapi.responses import FileResponse

    if not settings.saas.enabled:
        raise HTTPException(status_code=400, detail="未启用 SaaS 模式无法访问")

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    # 解析 attachment_ref: {session_id}/{filename}
    decoded_ref = unquote(attachment_ref)
    parts = decoded_ref.split("/", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="无效的附件引用格式")

    session_id, filename = parts

    # 验证渠道会话属于该租户
    from src.channels.session import channel_session_manager

    session = channel_session_manager.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    # 从附件目录查找文件
    from src.saas.api.channel_routes import ATTACHMENTS_DIR

    local_path = _os.path.join(ATTACHMENTS_DIR, session_id, filename)
    if not _os.path.exists(local_path):
        raise HTTPException(status_code=404, detail="附件文件不存在")

    # 推断 MIME 类型
    mime_type = "application/octet-stream"
    ext = _os.path.splitext(filename)[1].lower()
    _MIME_TYPE_MAP = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
        ".mp3": "audio/mpeg", ".amr": "audio/amr", ".wav": "audio/wav",
        ".mp4": "video/mp4",
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain",
        ".zip": "application/zip",
    }
    mime_type = _MIME_TYPE_MAP.get(ext, "application/octet-stream")

    return FileResponse(
        path=local_path,
        media_type=mime_type,
        filename=filename,
    )

