"""
渠道会话管理 API

旧的全局渠道回调路由已移除。
渠道配置现通过租户后台管理，回调路由为 /t/{tenant_id}/<channel>/callback。
"""

from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.channels.session import channel_session_manager

router = APIRouter(tags=["渠道回调"])


# ==================== 渠道会话管理 API ====================

@router.get("/api/channels/sessions")
async def list_channel_sessions(
    channel_type: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 50,
):
    """列出会话"""
    sessions = channel_session_manager.list_sessions(
        channel_type=channel_type,
        user_id=user_id,
        limit=limit,
    )
    return JSONResponse({"sessions": sessions, "count": len(sessions)})


@router.get("/api/channels/sessions/{channel_type}/{channel_user_id}")
async def get_channel_session(channel_type: str, channel_user_id: str):
    """获取会话详情"""
    session = channel_session_manager.get_session(channel_type, channel_user_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    messages = channel_session_manager.get_messages(session["session_id"])

    return JSONResponse({
        "session": session,
        "messages": messages,
    })


@router.delete("/api/channels/sessions/{session_id}")
async def delete_channel_session(session_id: str):
    """删除会话"""
    success = channel_session_manager.delete_session(session_id)
    return JSONResponse({"success": success})
