"""
SaaS 使用报告 API

路由：
- /api/saas/reports/* — 企业租户报告
- /api/usage/my/daily — 公共用户个人用量
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from loguru import logger

from src.saas.api.tenant_auth import require_admin, get_current_admin
from src.saas.db.usage_log_db import UsageLogDB
from src.saas.db.tenant_user_db import TenantUserDB
from src.api.auth import get_current_user

router = APIRouter(prefix="/api/saas/reports", tags=["SaaS 使用报告"])


@router.get("/summary")
async def get_usage_summary(
    request: Request,
    period: str = Query("month", description="统计维度：day/week/month"),
):
    """获取企业用量汇总"""
    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    # 计算时间范围
    end_date = datetime.now().strftime("%Y-%m-%d 23:59:59")
    if period == "day":
        start_date = datetime.now().strftime("%Y-%m-%d 00:00:00")
    elif period == "week":
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
    else:
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d 00:00:00")

    usage = UsageLogDB.get_tenant_usage(tenant_id, start_date, end_date)
    return {
        "success": True,
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "summary": usage,
    }


@router.get("/tokens")
async def get_token_trend(
    request: Request,
    days: int = Query(30, description="天数", ge=1, le=365),
):
    """获取 Token 用量趋势"""
    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    end_date = datetime.now().strftime("%Y-%m-%d 23:59:59")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")

    trend = UsageLogDB.get_token_trend(tenant_id, start_date, end_date)
    return {
        "success": True,
        "start_date": start_date,
        "end_date": end_date,
        "trend": trend,
    }


@router.get("/users")
async def get_user_usage(
    request: Request,
    days: int = Query(30, description="天数", ge=1, le=365),
):
    """获取每用户使用明细"""
    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    end_date = datetime.now().strftime("%Y-%m-%d 23:59:59")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")

    user_details = UsageLogDB.get_user_usage_detail(tenant_id, start_date, end_date)
    return {
        "success": True,
        "start_date": start_date,
        "end_date": end_date,
        "users": user_details,
    }


@router.get("/sessions")
async def get_session_stats(
    request: Request,
    days: int = Query(30, description="天数", ge=1, le=365),
):
    """获取会话统计"""
    admin = require_admin(request)

    # 复用 summary 数据
    tenant_id = admin["tenant_id"]
    end_date = datetime.now().strftime("%Y-%m-%d 23:59:59")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")

    usage = UsageLogDB.get_tenant_usage(tenant_id, start_date, end_date)
    return {
        "success": True,
        "total_sessions": usage.get("total_sessions", 0),
        "active_users": usage.get("active_users", 0),
        "period_days": days,
    }


@router.get("/export")
async def export_usage_report(
    request: Request,
    days: int = Query(30, description="天数", ge=1, le=365),
):
    """导出使用报告（JSON 格式）"""
    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    end_date = datetime.now().strftime("%Y-%m-%d 23:59:59")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")

    summary = UsageLogDB.get_tenant_usage(tenant_id, start_date, end_date)
    trend = UsageLogDB.get_token_trend(tenant_id, start_date, end_date)
    users = UsageLogDB.get_user_usage_detail(tenant_id, start_date, end_date)

    return {
        "success": True,
        "tenant_id": tenant_id,
        "start_date": start_date,
        "end_date": end_date,
        "summary": summary,
        "trend": trend,
        "user_details": users,
    }


# ==================== 公共用户用量 ====================

public_router = APIRouter(tags=["公共用户用量"])


@public_router.get("/api/usage/my/daily")
async def get_my_daily_usage(request: Request, days: int = Query(30, ge=1, le=365)):
    """获取个人每日 token 用量（公共用户）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    usage = UsageLogDB.get_personal_daily_usage(user["user_id"], days)
    return {"success": True, "user_id": user["user_id"], "usage": usage}
