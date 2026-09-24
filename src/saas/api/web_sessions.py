"""网页端会话 API

路由：/api/saas/web-sessions/*
- 有网页端会话的用户列表（用户名搜索）
- 用户的网页端会话列表（智能体名称搜索）
- 会话消息查询

数据源：chat_sessions / chat_messages（web 端专用表，与渠道表分离）
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from src.saas.api.tenant_auth import require_admin
from src.db.models import SessionDB, UserDB
from src.db.subagent_definition_db import SubagentDefinitionDB

router = APIRouter(prefix="/api/saas/web-sessions", tags=["网页端会话"])


@router.get("/users")
async def list_web_session_users(
    request: Request,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """获取有网页端会话的用户列表

    Args:
        keyword: 用户名/昵称搜索（可选，ILIKE 模糊匹配）
        page: 页码
        page_size: 每页数量
    """
    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    try:
        result = UserDB.list_web_session_users(
            tenant_id=tenant_id,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
        return {"success": True, **result}
    except Exception as e:
        logger.opt(exception=True).error(f"网页端会话用户列表查询失败: {e}")
        raise HTTPException(status_code=500, detail="查询用户列表失败")


@router.get("/users/{user_id}/sessions")
async def get_user_web_sessions(
    request: Request,
    user_id: str,
    agent_keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """获取用户的网页端会话列表

    Args:
        user_id: 用户ID
        agent_keyword: 智能体名称搜索（可选，按智能体名称/ID 模糊匹配）
        page: 页码
        page_size: 每页数量
    """
    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    # 仅校验用户存在。不用 users.tenant_id 校验归属：与列表接口的 chat_sessions.tenant_id
    # 口径保持一致（测试环境共享账号的 users.tenant_id 可能为 NULL，会话却挂在多个租户下），
    # 租户隔离由下方会话查询的 tenant_id 过滤保证
    user = UserDB.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    try:
        subagent_ids = None
        if agent_keyword:
            # 智能体先按名称/ID 翻译成 agent_id 集合再匹配（与办公软件会话 keyword 口径一致）
            from src.db.database import get_db_connection

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT agent_id FROM subagent_definitions WHERE name ILIKE %s OR agent_id ILIKE %s",
                    (f"%{agent_keyword}%", f"%{agent_keyword}%"),
                )
                subagent_ids = [row["agent_id"] for row in cursor.fetchall()]
            if not subagent_ids:
                return {"success": True, "sessions": [], "total": 0, "page": page, "page_size": page_size}

        result = UserDB.get_user_sessions(
            user_id=user_id,
            tenant_id=tenant_id,
            subagent_ids=subagent_ids,
            page=page,
            page_size=page_size,
        )

        # 批量反查智能体名称
        agent_ids = [s["subagent_id"] for s in result["sessions"] if s.get("subagent_id")]
        name_map = SubagentDefinitionDB.get_name_map(list(set(agent_ids))) if agent_ids else {}
        for s in result["sessions"]:
            if s.get("subagent_id"):
                s["subagent_name"] = name_map.get(s["subagent_id"])

        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"网页端会话列表查询失败: {e}")
        raise HTTPException(status_code=500, detail="查询会话列表失败")


@router.get("/sessions/{session_id}/messages")
async def get_web_session_messages(
    request: Request,
    session_id: str,
    content_search: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    """获取网页端会话的消息列表

    Args:
        session_id: 会话ID
        content_search: 聊天内容搜索（可选）
        page: 页码
        page_size: 每页数量
    """
    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    # chat_messages 无 tenant_id 列，通过先校验会话归属实现租户隔离
    session = SessionDB.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    try:
        result = UserDB.get_session_messages(
            session_id=session_id,
            content_search=content_search,
            page=page,
            page_size=page_size,
            exclude_roles=["tool"],
        )
        return {"success": True, **result}
    except Exception as e:
        logger.opt(exception=True).error(f"网页端会话消息查询失败: {e}")
        raise HTTPException(status_code=500, detail="查询消息失败")
