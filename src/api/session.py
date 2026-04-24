"""
会话管理API
包括会话CRUD、消息CRUD等
"""

from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from loguru import logger

from src.api.auth import get_current_user
from src.db.models import SessionDB, MessageDB, ChatRecordDB
from src.saas.context import get_current_tenant_id

router = APIRouter(prefix="/api/sessions", tags=["会话管理"])


# ============== 请求/响应模型 ==============

class CreateSessionRequest(BaseModel):
    title: Optional[str] = None
    context_data: Optional[dict] = None


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = None
    context_data: Optional[dict] = None


class CreateMessageRequest(BaseModel):
    role: str
    content: str
    metadata: Optional[dict] = None


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    tenant_id: Optional[str] = None
    title: str
    context_data: Optional[dict] = None
    created_at: str
    updated_at: str


class MessageResponse(BaseModel):
    message_id: str
    session_id: str
    role: str
    content: str
    metadata: Optional[dict] = None
    created_at: str


# ============== API 端点 ==============

@router.get("")
async def list_sessions(request: Request):
    """获取当前用户的会话列表（支持租户隔离）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    tenant_id = get_current_tenant_id()
    sessions = SessionDB.list_by_user(user["user_id"], tenant_id=tenant_id)
    return {"sessions": sessions}


@router.post("")
async def create_session(request: Request, body: CreateSessionRequest = None):
    """创建新会话（支持租户隔离）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    title = body.title if body else None
    context_data = body.context_data if body else None

    # 如果有context_data，添加用户基础信息
    if context_data is None:
        context_data = {}
    context_data["user_info"] = {
        "user_id": user["user_id"],
        "username": user["username"],
        "phone": user.get("phone")
    }

    tenant_id = get_current_tenant_id()
    session = SessionDB.create(user["user_id"], title, context_data, tenant_id=tenant_id)
    if session:
        return session
    raise HTTPException(status_code=500, detail="创建会话失败")


@router.get("/latest")
async def get_latest_session(request: Request):
    """获取当前用户的最近会话（支持租户隔离）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    tenant_id = get_current_tenant_id()
    sessions = SessionDB.list_by_user(user["user_id"], limit=1, tenant_id=tenant_id)
    if not sessions:
        return {"session": None}

    return {"session": sessions[0]}


@router.get("/{session_id}")
async def get_session(request: Request, session_id: str):
    """获取会话详情"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    session = SessionDB.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 验证会话属于当前用户
    if session["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    # 租户隔离：验证会话属于当前租户
    tenant_id = get_current_tenant_id()
    if tenant_id and session.get("tenant_id") and session["tenant_id"] != tenant_id:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    return session


@router.patch("/{session_id}")
async def update_session(request: Request, session_id: str, body: UpdateSessionRequest):
    """更新会话"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    session = SessionDB.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    if body.title:
        SessionDB.update_title(session_id, body.title)
    if body.context_data:
        SessionDB.update_context(session_id, body.context_data)

    # 每次更新时都刷新 updated_at 时间戳
    SessionDB.touch(session_id)

    return SessionDB.get_by_id(session_id)


@router.delete("/{session_id}")
async def delete_session(request: Request, session_id: str):
    """删除会话"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    session = SessionDB.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    if SessionDB.delete(session_id):
        return {"success": True, "message": "会话已删除"}
    raise HTTPException(status_code=500, detail="删除失败")


@router.get("/{session_id}/messages")
async def list_messages(request: Request, session_id: str, limit: int = 100):
    """获取会话的所有消息"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    session = SessionDB.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    messages = MessageDB.list_by_session(session_id, limit)
    return {"messages": messages}


@router.post("/{session_id}/messages")
async def create_message(request: Request, session_id: str, body: CreateMessageRequest):
    """添加消息到会话"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    session = SessionDB.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    message = MessageDB.create(session_id, body.role, body.content, body.metadata)
    if message:
        return message
    raise HTTPException(status_code=500, detail="创建消息失败")


@router.get("/{session_id}/context")
async def get_session_context(request: Request, session_id: str):
    """获取会话上下文（用户信息+聊天历史）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    session = SessionDB.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    messages = MessageDB.list_by_session(session_id)

    return {
        "user_info": session.get("context_data", {}).get("user_info", {}),
        "session_info": {
            "session_id": session["session_id"],
            "title": session["title"],
            "created_at": session["created_at"]
        },
        "messages": messages
    }


# ============== 会话记录相关API ==============

@router.get("/{session_id}/records")
async def list_session_records(request: Request, session_id: str, limit: int = 100):
    """获取会话的所有记录（每次对话的详情）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    session = SessionDB.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    records = ChatRecordDB.list_by_session(session_id, limit)
    return {"records": records}


@router.get("/{session_id}/token-usage")
async def get_session_token_usage(request: Request, session_id: str):
    """获取会话的总token消耗"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    session = SessionDB.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    token_usage = ChatRecordDB.get_total_token_by_session(session_id)
    return {
        "session_id": session_id,
        "total_tokens": token_usage["total_tokens"],
        "prompt_tokens": token_usage["prompt_tokens"],
        "completion_tokens": token_usage["completion_tokens"]
    }


@router.get("/{session_id}/records/{record_id}")
async def get_session_record(request: Request, session_id: str, record_id: str):
    """获取指定的会话记录详情"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    session = SessionDB.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    record = ChatRecordDB.get_by_id(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")

    if record["session_id"] != session_id:
        raise HTTPException(status_code=404, detail="记录不存在于该会话")

    return record
