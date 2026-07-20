"""
租户余额查询与用量明细 API（#37 租户积分充值与计费）

路由：/api/saas/billing/*
- GET /balance    当前租户积分余额 + 近 7 天日均消耗 + 预估可用天数
- GET /usage      用量明细列表（按日聚合，含 credit_cost）
- GET /recharges  本租户充值记录列表（只读）

权限：tenant_admin / user / platform_admin（代管理需带 X-Tenant-Id）
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Query
from loguru import logger

from src.config.settings import settings
from src.saas.api.tenant_auth import require_admin, sanitize_error_info
from src.saas.db.tenant_db import TenantDB
from src.db.models import TenantRechargesDB
from src.db.database import get_db_connection


router = APIRouter(prefix="/api/saas/billing", tags=["SaaS 余额与用量"])


# ============== API 端点 ==============

@router.get("/balance")
async def get_balance(request: Request):
    """获取当前租户积分余额 + 预估可用天数"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        return {"success": False, "message": "未关联租户"}

    try:
        tenant = TenantDB.get_by_id(tenant_id)
        if not tenant:
            return {"success": False, "message": "租户不存在"}

        credit_balance = int(tenant.get("credit_balance") or 0)

        # 近 7 天日均消耗
        daily_avg_cost_7d = _compute_daily_avg_cost(tenant_id, days=7)
        estimated_days_left: Optional[int]
        if daily_avg_cost_7d > 0:
            estimated_days_left = max(0, credit_balance // daily_avg_cost_7d)
        else:
            # 日均为 0 时返回 -1（前端可显示"暂无数据"）
            estimated_days_left = -1 if credit_balance > 0 else 0

        return {
            "success": True,
            "balance": {
                "credit_balance": credit_balance,
                "daily_avg_cost_7d": daily_avg_cost_7d,
                "estimated_days_left": estimated_days_left,
            },
        }
    except Exception as e:
        logger.error(f"获取积分余额失败: {e}")
        return {"success": False, "message": "查询失败", "debug": sanitize_error_info(str(e))}


@router.get("/usage")
async def get_usage(
    request: Request,
    date_from: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    session_id: Optional[str] = Query(None, description="按会话筛选"),
    model: Optional[str] = Query(None, description="按模型筛选"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页记录数"),
):
    """用量明细（按日聚合，含 credit_cost）

    按 DATE(created_at) 分组，返回每日消耗积分、会话数、消息数。
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        return {"success": False, "message": "未关联租户"}

    try:
        where_clauses: list = ["tenant_id = %s"]
        params: list = [tenant_id]
        if date_from:
            where_clauses.append("created_at >= %s")
            params.append(f"{date_from} 00:00:00")
        if date_to:
            where_clauses.append("created_at <= %s")
            params.append(f"{date_to} 23:59:59")
        if session_id:
            where_clauses.append("session_id = %s")
            params.append(session_id)
        if model:
            where_clauses.append("model = %s")
            params.append(model)
        where_sql = " AND ".join(where_clauses)

        offset = (page - 1) * page_size
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 总数（按日聚合后的天数）
            cursor.execute(
                f"""
                SELECT COUNT(*) AS cnt FROM (
                    SELECT 1 FROM chat_records
                    WHERE {where_sql}
                    GROUP BY DATE(created_at)
                ) AS grouped
                """,
                params,
            )
            total = int(cursor.fetchone()["cnt"] or 0)

            # 按日聚合
            cursor.execute(
                f"""
                SELECT
                    DATE(created_at) AS date,
                    COALESCE(SUM(credit_cost), 0) AS credit_cost,
                    COUNT(DISTINCT session_id) AS session_count,
                    COUNT(*) AS message_count
                FROM chat_records
                WHERE {where_sql}
                GROUP BY DATE(created_at)
                ORDER BY DATE(created_at) DESC
                LIMIT %s OFFSET %s
                """,
                (*params, page_size, offset),
            )
            items = [
                {
                    "date": str(row["date"]) if row.get("date") else None,
                    "credit_cost": int(row.get("credit_cost") or 0),
                    "session_count": int(row.get("session_count") or 0),
                    "message_count": int(row.get("message_count") or 0),
                }
                for row in cursor.fetchall()
            ]

        return {
            "success": True,
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Exception as e:
        logger.error(f"获取用量明细失败: {e}")
        return {"success": False, "message": "查询失败", "debug": sanitize_error_info(str(e))}


@router.get("/recharges")
async def list_my_recharges(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    """本租户充值记录列表（只读，无操作列）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        return {"success": False, "message": "未关联租户"}

    try:
        result = TenantRechargesDB.list(
            tenant_id=tenant_id,
            page=page,
            page_size=page_size,
        )
        # 租户只读视图：不暴露 operator_id 等敏感字段
        for item in result["items"]:
            item.pop("operator_id", None)
            item.pop("payment_order_id", None)
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"获取租户充值记录失败: {e}")
        return {"success": False, "message": "查询失败", "debug": sanitize_error_info(str(e))}


# ============== 辅助函数 ==============

def _compute_daily_avg_cost(tenant_id: str, days: int = 7) -> int:
    """计算近 N 天日均积分消耗（向下取整）

    若 N 天内无消耗记录返回 0。
    """
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
    placeholder = "%s"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT COALESCE(SUM(credit_cost), 0) AS total
            FROM chat_records
            WHERE tenant_id = {placeholder}
              AND created_at >= {placeholder}
            """,
            (tenant_id, start_date),
        )
        row = cursor.fetchone() or {}
        total_cost = int(row.get("total") or 0)
    if total_cost <= 0:
        return 0
    return total_cost // days
