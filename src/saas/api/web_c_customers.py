"""官网接待客户 API（web_c 渠道租户侧只读查看页，设计 §5.1）

路由：/api/saas/web-c-customers/*

纯只读查看：无 kf 账号/引流员工/留资体系裁剪维度，权限 require_admin。
留资线索复用 bs_lead_capture_leads，web_c 过滤 = 线索 session_id 归属
channel_type='web_c' 的渠道会话（EXISTS join，零 DDL）。
附件下载复用既有端点 /api/saas/external-customers/attachments/download
（session_id 归属校验同口径，不重复建端点）。
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from src.db.database import get_db_connection
from src.db.models import UserDB
from src.saas.api.tenant_auth import require_admin
from src.saas.models.enums import BehaviorAction, BehaviorResourceType
from src.services.behavior_log import audit_action

router = APIRouter(prefix="/api/saas/web-c-customers", tags=["官网接待客户"])

_CHANNEL_TYPE = "web_c"


def _require_tenant(request: Request) -> str:
    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")
    return tenant_id


@router.get("/users")
async def list_users(request: Request, username: Optional[str] = None,
                     page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=200)):
    """官网客服客户列表（users × web_c 渠道会话组合，最近活跃倒序）。

    行字段：uid 徽标（channel_user_id，ext_*/anon_* 前缀区分身份模式）、
    名称（签名 name / 官网访客xxxx）、会话日期区间。
    """
    tenant_id = _require_tenant(request)
    result = UserDB.list_external_users(
        tenant_id=tenant_id,
        username=username,
        channel_type=_CHANNEL_TYPE,
        page=page,
        page_size=page_size,
    )
    # 渠道侧用户 ID 反查：uid 徽标 + 展示名归一（签名 name / 占位用户名）
    users = result.get("users", [])
    if users:
        user_ids = [u.get("user_id") for u in users if u.get("user_id")]
        uid_map, name_map = _lookup_channel_users(tenant_id, user_ids)
        for u in users:
            u["channel_user_id"] = uid_map.get(u.get("user_id"), "")
            nickname = u.get("nickname") or ""
            if nickname and not nickname.startswith("官网客服用户"):
                u["display_name"] = nickname
            else:
                u["display_name"] = name_map.get(u.get("user_id")) or u.get("username") or "官网访客"
    return {"success": True, **result}


def _lookup_channel_users(tenant_id: str, user_ids: list) -> tuple:
    """按 users.id 反查 channel_sessions 的 channel_user_id 与最近用户名。"""
    uid_map: dict = {}
    name_map: dict = {}
    if not user_ids:
        return uid_map, name_map
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT ON (user_id) user_id, channel_user_id, username
            FROM channel_sessions
            WHERE tenant_id = %s AND channel_type = %s AND user_id = ANY(%s)
            ORDER BY user_id, updated_at DESC NULLS LAST
            """,
            (tenant_id, _CHANNEL_TYPE, user_ids),
        )
        for row in cursor.fetchall():
            uid_map[row["user_id"]] = row.get("channel_user_id") or ""
            name_map[row["user_id"]] = row.get("username") or ""
    return uid_map, name_map


@router.get("/users/{user_id}/sessions")
async def get_user_sessions(request: Request, user_id: str,
                            start_date: Optional[str] = None,
                            end_date: Optional[str] = None,
                            subagent_id: Optional[str] = None,
                            page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=200)):
    """指定客户的 web_c 会话列表（日期段/子智能体筛选，updated_at DESC）。"""
    tenant_id = _require_tenant(request)
    user = UserDB.get_by_id(user_id)
    if not user or user.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="用户不存在或不属于该租户")

    from src.channels.session import channel_session_manager

    sessions = channel_session_manager.list_sessions(
        channel_type=_CHANNEL_TYPE,
        user_id=user_id,
        tenant_id=tenant_id,
        subagent_id=subagent_id,
        limit=200,
    )
    if start_date:
        sessions = [s for s in sessions if (s.get("updated_at") or "") >= start_date]
    if end_date:
        # end_date 含当日：< 次日零点语义，字符串比较 + 'z' 后缀兜底
        sessions = [s for s in sessions if (s.get("updated_at") or "") < _next_day(end_date)]
    sessions.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    total = len(sessions)
    start = (page - 1) * page_size
    return {
        "success": True,
        "sessions": sessions[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _next_day(date_str: str) -> str:
    """YYYY-MM-DD -> 次日零点字符串（字符串比较口径）。"""
    from datetime import datetime, timedelta

    try:
        base = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return (base + timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="日期格式应为 YYYY-MM-DD")


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(request: Request, session_id: str,
                              content_search: Optional[str] = None,
                              page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200)):
    """会话消息分页查询（web_c 渠道会话租户校验；无 wecom_kf 特例，直接透传）。"""
    tenant_id = _require_tenant(request)
    from src.channels.session import channel_session_manager

    session = channel_session_manager.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="无权访问此会话")
    if session.get("channel_type") != _CHANNEL_TYPE:
        raise HTTPException(status_code=403, detail="无权访问此会话")
    result = channel_session_manager.get_messages_paginated(
        session_id=session_id, content_search=content_search,
        page=page, page_size=page_size,
    )
    return {"success": True, **result}


@router.get("/stats")
async def get_stats(request: Request, start_date: Optional[str] = None,
                    end_date: Optional[str] = None):
    """日期段统计：会话数、消息数、活跃客户数，按绑定子智能体分组。"""
    tenant_id = _require_tenant(request)
    end_exclusive = _next_day(end_date) if end_date else None

    session_cond, params = ["tenant_id = %s", "channel_type = %s"], [tenant_id, _CHANNEL_TYPE]
    if start_date:
        session_cond.append("updated_at >= %s")
        params.append(start_date)
    if end_exclusive:
        session_cond.append("updated_at < %s")
        params.append(end_exclusive)
    where = " AND ".join(session_cond)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT COUNT(*) AS cnt, COUNT(DISTINCT channel_user_id) AS users "
            f"FROM channel_sessions WHERE {where}", params)
        row = cursor.fetchone()
        total_sessions, active_users = row["cnt"], row["users"]

        msg_cond, msg_params = ["s.tenant_id = %s", "s.channel_type = %s"], [tenant_id, _CHANNEL_TYPE]
        if start_date:
            msg_cond.append("m.created_at >= %s")
            msg_params.append(start_date)
        if end_exclusive:
            msg_cond.append("m.created_at < %s")
            msg_params.append(end_exclusive)
        cursor.execute(
            f"SELECT COUNT(*) AS cnt FROM channel_messages m "
            f"JOIN channel_sessions s ON s.session_id = m.session_id "
            f"WHERE {' AND '.join(msg_cond)}", msg_params)
        total_messages = cursor.fetchone()["cnt"]

        cursor.execute(
            f"SELECT COALESCE(subagent_id, '') AS subagent_id, COUNT(*) AS sessions "
            f"FROM channel_sessions WHERE {where} "
            f"GROUP BY COALESCE(subagent_id, '') ORDER BY sessions DESC", params)
        by_subagent = [dict(r) for r in cursor.fetchall()]

    if by_subagent:
        from src.saas.services.agent_name_resolver import resolve_agent_names

        agent_ids = [r["subagent_id"] for r in by_subagent if r.get("subagent_id")]
        name_map = resolve_agent_names(tenant_id, agent_ids)
        for r in by_subagent:
            r["subagent_name"] = name_map.get(r.get("subagent_id"), "") if r.get("subagent_id") else "未绑定"
    return {
        "success": True,
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "active_users": active_users,
        "by_subagent": by_subagent,
    }


# ============== 留资线索（web_c 口径：EXISTS join 渠道会话过滤） ==============


@router.get("/lead-stats")
async def get_lead_stats(request: Request, start_date: Optional[str] = None,
                         end_date: Optional[str] = None):
    """web_c 留资统计：总留资 + 按联系方式类型分组。"""
    tenant_id = _require_tenant(request)
    from src.saas.db.lead_capture_db import LeadCaptureDB

    stats = LeadCaptureDB.stats(tenant_id, start_date=start_date, end_date=end_date,
                                channel_type=_CHANNEL_TYPE)
    return {"success": True, **stats}


@router.get("/leads")
async def list_leads(request: Request, start_date: Optional[str] = None,
                     end_date: Optional[str] = None, stage: Optional[str] = None,
                     page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=200)):
    """web_c 留资线索列表（created_at DESC，日期段/阶段筛选）。"""
    tenant_id = _require_tenant(request)
    from src.saas.db.lead_capture_db import LeadCaptureDB

    result = LeadCaptureDB.list_by_tenant(
        tenant_id, page=page, page_size=page_size,
        start_date=start_date, end_date=end_date, stage=stage,
        channel_type=_CHANNEL_TYPE,
    )
    return {"success": True, **result}


class LeadStageUpdate(BaseModel):
    stage: str = Field(..., description="目标阶段：new/contacting/converted/abandoned")


@router.patch("/leads/{lead_id}")
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.EXTERNAL_CUSTOMER, id_arg="lead_id")
async def update_lead_stage(request: Request, lead_id: str, body: LeadStageUpdate):
    """更新 web_c 线索阶段（new -> contacting -> converted / abandoned）。"""
    tenant_id = _require_tenant(request)
    from src.saas.db.lead_capture_db import LEAD_STAGES, LeadCaptureDB

    if body.stage not in LEAD_STAGES:
        raise HTTPException(status_code=400, detail="非法阶段值")
    lead = LeadCaptureDB.get_by_id(lead_id, tenant_id)
    if not lead:
        raise HTTPException(status_code=404, detail="线索不存在")
    ok = LeadCaptureDB.update_stage(lead_id, body.stage, tenant_id)
    if not ok:
        raise HTTPException(status_code=500, detail="更新线索阶段失败")
    return {"success": True, "lead": LeadCaptureDB.get_by_id(lead_id, tenant_id)}
