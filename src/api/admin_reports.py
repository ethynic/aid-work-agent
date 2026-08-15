"""
Token消耗报表API

提供平台级Token消耗统计报表，仅平台管理员可访问。
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, Query, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.config.settings import settings
from src.saas.permissions.checker import is_platform_admin
from src.saas.services.renewal import enrich_tenants_with_renewal
from src.db.models import ChatRecordDB
from src.db.database import get_db_connection
from src.saas.db.tenant_db import TenantDB

router = APIRouter(prefix="/api/admin", tags=["平台报表"])


# ============== 请求/响应模型 ==============

class PlatformTokenUsageResponse(BaseModel):
    """平台Token消耗报表响应"""
    success: bool
    month: str
    summary: Dict[str, Any]
    data: List[Dict[str, Any]]
    message: Optional[str] = None


class DashboardStatsResponse(BaseModel):
    """管理后台仪表盘统计响应"""
    success: bool
    tenant_count: int = Field(0, description="正常租户数量（status=active）")
    monthly_token_usage: int = Field(0, description="本月Token用量（全平台 prompt+completion 总和）")
    today_conversation_count: int = Field(0, description="今日对话数量（全平台 chat_records 记录数）")
    renewal_pending_count: int = Field(0, description="待续费租户数量（积分余额不足 7 天用量）")
    month: str = Field("", description="统计月份，格式 YYYY-MM")
    message: Optional[str] = None


# ============== 工具函数 ==============


# ============== API端点 ==============

@router.get("/token-usage", response_model=PlatformTokenUsageResponse)
async def get_platform_token_usage(
    request: Request,
    month: str = Query(..., description="月份，格式 YYYY-MM，如 2026-05")
):
    """
    获取平台Token消耗报表

    仅平台管理员可访问，显示所有租户在指定月份的Token消耗汇总。
    """
    # 1. 验证用户身份和权限
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if not is_platform_admin(user):
        logger.warning(f"User is not platform admin: {user}")
        raise HTTPException(status_code=403, detail="仅平台管理员可访问此报表")

    logger.info(f"平台管理员 {user.get('user_id')} 请求Token消耗报表，月份: {month}")

    # 2. 调用 ChatRecordDB 方法获取平台Token消耗汇总
    try:
        result = ChatRecordDB.get_platform_token_usage(month)
    except ValueError as e:
        return PlatformTokenUsageResponse(
            success=False,
            month=month,
            summary={},
            data=[],
            message=str(e)
        )
    except Exception as e:
        logger.error(f"获取平台Token消耗报表失败: {e}")
        return PlatformTokenUsageResponse(
            success=False,
            month=month,
            summary={},
            data=[],
            message="获取报表数据时发生内部错误"
        )

    # 3. 获取租户名称信息
    tenant_data = []
    for tenant_item in result["data"]:
        tenant_id = tenant_item["tenant_id"]
        # 查询租户名称
        tenant_info = TenantDB.get_by_id(tenant_id)
        if tenant_id == "demo":
            company_name = "演示用户"
            tenant_code = "demo"
        else:
            company_name = tenant_info.get("company_name", "未知公司") if tenant_info else "未知公司"
            tenant_code = tenant_info.get("tenant_code", tenant_id) if tenant_info else tenant_id

        tenant_data.append({
            "tenant_id": tenant_id,
            "tenant_code": tenant_code,
            "company_name": company_name,
            "input_tokens": tenant_item["input_tokens"],
            "output_tokens": tenant_item["output_tokens"],
            "conversation_count": tenant_item["conversation_count"],
            "input_cost": tenant_item["input_cost"],
            "output_cost": tenant_item["output_cost"],
            "total_cost": tenant_item["total_cost"],
            "credit_cost": float(tenant_item["credit_cost"] or 0),
            "has_unpriced_tokens": tenant_item["has_unpriced_tokens"]
        })

    # 4. 构建响应（使用 ChatRecordDB 返回的汇总信息）
    return PlatformTokenUsageResponse(
        success=True,
        month=result["month"],
        summary=result["summary"],
        data=tenant_data,
        message=f"共 {len(tenant_data)} 个租户有数据"
    )


@router.get("/dashboard_stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(request: Request):
    """
    获取管理后台仪表盘统计数据

    仅平台管理员可访问，返回四个核心指标：
    - 正常租户数量（status=active）
    - 本月Token用量（全平台 prompt+completion 总和）
    - 今日对话数量（全平台 chat_records 记录数）
    - 待续费租户数量（积分余额不足 7 天用量）
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if not is_platform_admin(user):
        logger.warning(f"User is not platform admin: {user}")
        raise HTTPException(status_code=403, detail="仅平台管理员可访问此报表")

    now = datetime.now()
    month_str = now.strftime("%Y-%m")

    # 1. 正常租户数量：复用 TenantDB.list_tenants，按 status=active 过滤取 total
    tenant_result = TenantDB.list_tenants(status="active", page=1, page_size=1)
    tenant_count = tenant_result.get("total", 0)

    # 2. 本月Token用量：复用 get_platform_token_usage 的 summary（含缓存）
    monthly_token_usage = 0
    try:
        token_result = ChatRecordDB.get_platform_token_usage(month_str)
        summary = token_result.get("summary", {})
        monthly_token_usage = int(summary.get("total_input_tokens", 0)) + int(summary.get("total_output_tokens", 0))
    except Exception as e:
        logger.error(f"获取本月Token用量失败: {e}", exc_info=True)

    # 3. 今日对话数量：查询 chat_records 表今日（按 created_at）的记录数
    today_conversation_count = 0
    try:
        today_start = now.strftime("%Y-%m-%d 00:00:00")
        today_end = now.strftime("%Y-%m-%d 23:59:59")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) as cnt
                FROM chat_records
                WHERE created_at >= %s AND created_at <= %s
                """,
                (today_start, today_end),
            )
            today_conversation_count = cursor.fetchone()["cnt"]
    except Exception as e:
        logger.error(f"获取今日对话数量失败: {e}", exc_info=True)

    # 4. 待续费租户数量：扫描全部 active 租户，统计积分余额不足 7 天用量的租户
    renewal_pending_count = 0
    try:
        renewal_result = TenantDB.list_tenants(status="active", page=1, page_size=10000)
        active_tenants = renewal_result.get("tenants", [])
        enrich_tenants_with_renewal(active_tenants)
        renewal_pending_count = sum(1 for t in active_tenants if t.get("renewal_pending"))
    except Exception as e:
        logger.error(f"获取待续费租户数量失败: {e}", exc_info=True)

    return DashboardStatsResponse(
        success=True,
        tenant_count=tenant_count,
        monthly_token_usage=monthly_token_usage,
        today_conversation_count=today_conversation_count,
        renewal_pending_count=renewal_pending_count,
        month=month_str,
    )
