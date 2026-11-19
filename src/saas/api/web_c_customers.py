"""官网接待客户 API（web_c 渠道租户侧只读查看页，设计 §5.1）

路由：/api/saas/web-c-customers/*

纯只读查看：无 kf 账号/引流员工/留资体系裁剪维度，权限 require_admin。
留资线索复用 bs_lead_capture_leads，web_c 过滤 = 线索 session_id 归属
channel_type='web_c' 的渠道会话（EXISTS join，零 DDL）。
附件下载复用既有端点 /api/saas/external-customers/attachments/download
（session_id 归属校验同口径，不重复建端点）。
"""

import json
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


def _web_c_configs(tenant_id: str) -> list:
    """租户 web_c 渠道配置（按创建时间正序；最早创建的为存量无归属会话的兜底渠道）。"""
    from src.saas.db.channel_config_db import ChannelConfigDB

    configs = ChannelConfigDB.list_by_tenant(tenant_id, channel_type=_CHANNEL_TYPE)
    rows = [
        {
            "config_id": c.get("config_id"),
            "name": c.get("name") or "未命名渠道",
            "created_at": str(c.get("created_at") or ""),
        }
        for c in configs
        if c.get("config_id")
    ]
    rows.reverse()  # list_by_tenant 按 id DESC 返回，反转为创建先后正序
    return rows


@router.get("/channels")
async def list_channels(request: Request):
    """租户的 web_c 渠道配置列表（官网接待页渠道下拉筛选 + 渠道名称展示用）。"""
    tenant_id = _require_tenant(request)
    return {"success": True, "channels": _web_c_configs(tenant_id)}


def _user_channel_map(tenant_id: str, user_ids: list, fallback_config_id: Optional[str]) -> dict:
    """user_id -> 生效渠道 config_id。

    优先取该用户最近一条有归属的 web_c 会话（metadata.channel_config_id）；
    全部会话均无归属（渠道归属功能上线前的存量数据）时回退兜底渠道
    （租户最早创建的 web_c 配置——存量会话产生时不晚于其后创建的配置）。
    """
    result = {uid: (fallback_config_id or "") for uid in user_ids if uid}
    if not result:
        return result
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT ON (user_id) user_id, metadata->>'channel_config_id' AS config_id
            FROM channel_sessions
            WHERE tenant_id = %s AND channel_type = %s AND user_id = ANY(%s)
              AND metadata->>'channel_config_id' IS NOT NULL
            ORDER BY user_id, updated_at DESC NULLS LAST
            """,
            (tenant_id, _CHANNEL_TYPE, list(result.keys())),
        )
        for row in cursor.fetchall():
            result[row["user_id"]] = row["config_id"] or (fallback_config_id or "")
    return result


def _session_config_id(session: dict, fallback_config_id: Optional[str]) -> str:
    """会话的生效渠道 config_id（归属缺失时回退兜底渠道）。"""
    metadata = session.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = None
    if isinstance(metadata, dict) and metadata.get("channel_config_id"):
        return str(metadata["channel_config_id"])
    return fallback_config_id or ""


@router.get("/users")
async def list_users(request: Request, username: Optional[str] = None,
                     config_id: Optional[str] = None,
                     page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=200)):
    """官网客服客户列表（用户 × web_c 渠道会话，每条会话一行，最近活跃倒序）。

    行字段：uid 徽标（channel_user_id，ext_*/uid_* 实名、anon_* 匿名，前缀区分身份模式）、
    名称（签名 name / 官网访客xxxx）、会话起止时间、session_id、归属渠道
    （channel_config_id / channel_name，存量无归属会话回退最早渠道）。
    按会话拆行（group_by_session）：uid 模式访客每次进入即新会话（设计 §3.5），
    按用户折叠会让「前面的问答」看似消失（2026-11-19 测试反馈裁决）。
    config_id：渠道下拉筛选（可选）。
    """
    tenant_id = _require_tenant(request)
    configs = _web_c_configs(tenant_id)
    fallback_id = configs[0]["config_id"] if configs else None
    result = UserDB.list_external_users(
        tenant_id=tenant_id,
        username=username,
        channel_type=_CHANNEL_TYPE,
        group_by_session=True,
        channel_config_id=config_id or None,
        channel_config_fallback_id=fallback_id,
        page=page,
        page_size=page_size,
    )
    # 渠道侧用户 ID 反查：uid 徽标 + 展示名归一（签名 name / 占位用户名）
    users = result.get("users", [])
    if users:
        user_ids = [u.get("user_id") for u in users if u.get("user_id")]
        uid_map, name_map = _lookup_channel_users(tenant_id, user_ids)
        ch_map = _user_channel_map(tenant_id, user_ids, fallback_id)
        name_by_id = {c["config_id"]: c["name"] for c in configs}
        for u in users:
            u["channel_user_id"] = uid_map.get(u.get("user_id"), "")
            nickname = u.get("nickname") or ""
            if nickname and not nickname.startswith("官网客服用户"):
                u["display_name"] = nickname
            else:
                u["display_name"] = name_map.get(u.get("user_id")) or u.get("username") or "官网访客"
            cid = ch_map.get(u.get("user_id"), "")
            u["channel_config_id"] = cid
            u["channel_name"] = name_by_id.get(cid, "")
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
                            config_id: Optional[str] = None,
                            page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=200)):
    """指定客户的 web_c 会话列表（日期段/子智能体/渠道筛选，updated_at DESC）。"""
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
    if config_id:
        # 渠道筛选与列表页口径一致：归属缺失的存量会话回退最早渠道
        fallback_id = next(iter(_web_c_configs(tenant_id)), {}).get("config_id")
        sessions = [s for s in sessions if _session_config_id(s, fallback_id) == config_id]
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
