"""
外贸客户信息管理 API
"""

import re
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from loguru import logger

from src.db.database import get_db_connection, get_date_offset
from src.saas.context import get_current_tenant_id


# 敏感信息过滤
def sanitize_error_info(error_msg: str) -> str:
    """过滤敏感信息"""
    if not error_msg:
        return error_msg
    sensitive_patterns = [
        r'password["\s:=]+\S+',
        r'passwd["\s:=]+\S+',
        r'secret["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'api[_-]%skey["\s:=]+\S+',
        r'access[_-]%skey["\s:=]+\S+',
        r'private[_-]%skey["\s:=]+\S+',
        r'auth[_-]%stoken["\s:=]+\S+',
    ]
    sanitized = error_msg
    for pattern in sensitive_patterns:
        sanitized = re.sub(
            pattern,
            lambda m: m.group(0).split('=')[0] + '=***',
            sanitized,
            flags=re.IGNORECASE
        )
    return sanitized


router = APIRouter(prefix="/api/trade-specialist", tags=["外贸获客-客户信息管理"])


class CustomerListResponse(BaseModel):
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    debug: Optional[str] = None


class CustomerDetailResponse(BaseModel):
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    debug: Optional[str] = None


class EmailListResponse(BaseModel):
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    debug: Optional[str] = None


class StatsResponse(BaseModel):
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    debug: Optional[str] = None


@router.get("/customers", response_model=CustomerListResponse)
async def list_customers(
    user_id: str = Query(..., description="用户ID"),
    session_id: Optional[str] = Query(None, description="会话ID")
):
    """获取客户的客户列表"""
    try:
        tenant_id = get_current_tenant_id()

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # SaaS 模式下按 tenant_id 隔离；非 SaaS 模式（tenant_id 为 None）保持原逻辑
            if tenant_id:
                if session_id:
                    cursor.execute("""
                        SELECT * FROM bs_trade_specialist_matched_customers
                        WHERE tenant_id = %s AND user_id = %s AND session_id = %s
                        ORDER BY created_at DESC
                    """, (tenant_id, user_id, session_id))
                else:
                    cursor.execute("""
                        SELECT * FROM bs_trade_specialist_matched_customers
                        WHERE tenant_id = %s AND user_id = %s
                        ORDER BY created_at DESC
                    """, (tenant_id, user_id))
            else:
                if session_id:
                    cursor.execute("""
                        SELECT * FROM bs_trade_specialist_matched_customers
                        WHERE user_id = %s AND session_id = %s
                        ORDER BY created_at DESC
                    """, (user_id, session_id))
                else:
                    cursor.execute("""
                        SELECT * FROM bs_trade_specialist_matched_customers
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                    """, (user_id,))

            rows = cursor.fetchall()
            customers = [dict(row) for row in rows]

            logger.info(f"后端日志：查询用户 {user_id} 的客户列表，tenant={tenant_id}")

            return {
                "success": True,
                "data": {
                    "count": len(customers),
                    "customers": customers
                }
            }
    except Exception as e:
        logger.error(f"后端日志：查询客户列表失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "查询客户列表失败",
            "debug": sanitize_error_info(str(e))
        }


@router.get("/customers/{customer_id}", response_model=CustomerDetailResponse)
async def get_customer(customer_id: str):
    """获取客户详情，包含邮件历史"""
    try:
        tenant_id = get_current_tenant_id()

        with get_db_connection() as conn:
            cursor = conn.cursor()

            if tenant_id:
                cursor.execute("""
                    SELECT * FROM bs_trade_specialist_matched_customers
                    WHERE tenant_id = %s AND customer_id = %s
                """, (tenant_id, customer_id))
            else:
                cursor.execute("""
                    SELECT * FROM bs_trade_specialist_matched_customers WHERE customer_id = %s
                """, (customer_id,))

            row = cursor.fetchone()
            if not row:
                return {
                    "success": False,
                    "error": f"客户 {customer_id} 不存在"
                }

            customer = dict(row)

            # 查询邮件历史（同租户隔离）
            if tenant_id:
                cursor.execute("""
                    SELECT * FROM bs_trade_specialist_customer_emails
                    WHERE tenant_id = %s AND customer_id = %s
                    ORDER BY created_at DESC
                """, (tenant_id, customer_id))
            else:
                cursor.execute("""
                    SELECT * FROM bs_trade_specialist_customer_emails
                    WHERE customer_id = %s
                    ORDER BY created_at DESC
                """, (customer_id,))

            email_rows = cursor.fetchall()
            customer["emails"] = [dict(r) for r in email_rows]

            logger.info(f"后端日志：查询客户详情 {customer_id}，tenant={tenant_id}")

            return {
                "success": True,
                "data": customer
            }
    except Exception as e:
        logger.error(f"后端日志：查询客户详情失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "查询客户详情失败",
            "debug": sanitize_error_info(str(e))
        }


@router.get("/emails", response_model=EmailListResponse)
async def list_emails(
    customer_id: Optional[str] = Query(None, description="客户ID"),
    user_id: Optional[str] = Query(None, description="用户ID"),
    limit: int = Query(100, description="返回数量限制")
):
    """查询邮件历史"""
    if not customer_id and not user_id:
        return {
            "success": False,
            "error": "customer_id 或 user_id 至少需要提供一个"
        }

    try:
        tenant_id = get_current_tenant_id()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            placeholder = "%s"
            tenant_clause = "AND ce.tenant_id = %s" if tenant_id else ""
            tenant_params = (tenant_id,) if tenant_id else ()

            if customer_id:
                # PostgreSQL 不支持 LIMIT %s，需要直接拼接
                cursor.execute(f"""
                    SELECT ce.*, mc.company_name, mc.contact_name
                    FROM bs_trade_specialist_customer_emails ce
                    LEFT JOIN bs_trade_specialist_matched_customers mc ON ce.customer_id = mc.customer_id
                    WHERE ce.customer_id = {placeholder} {tenant_clause}
                    ORDER BY ce.created_at DESC
                    LIMIT {limit}
                """, (customer_id, *tenant_params))
            else:
                cursor.execute(f"""
                    SELECT ce.*, mc.company_name, mc.contact_name
                    FROM bs_trade_specialist_customer_emails ce
                    LEFT JOIN bs_trade_specialist_matched_customers mc ON ce.customer_id = mc.customer_id
                    WHERE ce.user_id = {placeholder} {tenant_clause}
                    ORDER BY ce.created_at DESC
                    LIMIT {limit}
                """, (user_id, *tenant_params))

            rows = cursor.fetchall()
            emails = [dict(row) for row in rows]

            logger.info(f"后端日志：查询邮件历史，tenant={tenant_id}")

            return {
                "success": True,
                "data": {
                    "count": len(emails),
                    "emails": emails
                }
            }
    except Exception as e:
        logger.error(f"后端日志：查询邮件历史失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "查询邮件历史失败",
            "debug": sanitize_error_info(str(e))
        }


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    user_id: str = Query(..., description="用户ID")
):
    """获取用户统计信息"""
    try:
        tenant_id = get_current_tenant_id()

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # SaaS 模式下按 tenant_id 隔离；非 SaaS 模式（tenant_id 为 None）保持原逻辑
            tenant_clause = "AND tenant_id = %s" if tenant_id else ""
            tenant_params = (tenant_id,) if tenant_id else ()

            # 客户总数
            cursor.execute(f"""
                SELECT COUNT(*) as total FROM bs_trade_specialist_matched_customers
                WHERE user_id = %s {tenant_clause}
            """, (user_id, *tenant_params))
            total_customers = cursor.fetchone()["total"]

            # 邮件发送总数
            cursor.execute(f"""
                SELECT COUNT(*) as total FROM bs_trade_specialist_customer_emails
                WHERE user_id = %s {tenant_clause}
            """, (user_id, *tenant_params))
            total_emails = cursor.fetchone()["total"]

            # 成功发送数
            cursor.execute(f"""
                SELECT COUNT(*) as total FROM bs_trade_specialist_customer_emails
                WHERE user_id = %s AND send_status = 'success' {tenant_clause}
            """, (user_id, *tenant_params))
            success_emails = cursor.fetchone()["total"]

            # 发送失败的邮件数
            cursor.execute(f"""
                SELECT COUNT(*) as total FROM bs_trade_specialist_customer_emails
                WHERE user_id = %s AND send_status = 'failed' {tenant_clause}
            """, (user_id, *tenant_params))
            failed_emails = cursor.fetchone()["total"]

            # 最近7天的客户新增数
            seven_days_ago = get_date_offset(-7)
            cursor.execute(f"""
                SELECT COUNT(*) as total FROM bs_trade_specialist_matched_customers
                WHERE user_id = %s AND created_at >= {seven_days_ago} {tenant_clause}
            """, (user_id, *tenant_params))
            recent_customers = cursor.fetchone()["total"]

            # 最近7天的邮件发送数
            cursor.execute(f"""
                SELECT COUNT(*) as total FROM bs_trade_specialist_customer_emails
                WHERE user_id = %s AND created_at >= {seven_days_ago} {tenant_clause}
            """, (user_id, *tenant_params))
            recent_emails = cursor.fetchone()["total"]

            # 按国家分布
            cursor.execute(f"""
                SELECT country, COUNT(*) as count
                FROM bs_trade_specialist_matched_customers
                WHERE user_id = %s {tenant_clause}
                GROUP BY country
                ORDER BY count DESC
                LIMIT 10
            """, (user_id, *tenant_params))
            country_dist = [dict(row) for row in cursor.fetchall()]

            stats = {
                "total_customers": total_customers,
                "total_emails": total_emails,
                "success_emails": success_emails,
                "failed_emails": failed_emails,
                "recent_customers": recent_customers,
                "recent_emails": recent_emails,
                "country_distribution": country_dist
            }

            logger.info(f"后端日志：用户 {user_id} 统计信息，tenant={tenant_id}")

            return {
                "success": True,
                "data": stats
            }
    except Exception as e:
        logger.error(f"后端日志：获取统计信息失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "获取统计信息失败",
            "debug": sanitize_error_info(str(e))
        }


@router.get("/session/{session_id}/customers", response_model=CustomerListResponse)
async def get_session_customers(session_id: str):
    """获取特定会话的客户列表"""
    try:
        tenant_id = get_current_tenant_id()

        with get_db_connection() as conn:
            cursor = conn.cursor()

            if tenant_id:
                cursor.execute("""
                    SELECT * FROM bs_trade_specialist_matched_customers
                    WHERE tenant_id = %s AND session_id = %s
                    ORDER BY created_at DESC
                """, (tenant_id, session_id))
            else:
                cursor.execute("""
                    SELECT * FROM bs_trade_specialist_matched_customers
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                """, (session_id,))

            rows = cursor.fetchall()
            customers = [dict(row) for row in rows]

            logger.info(f"后端日志：查询会话 {session_id} 的客户列表，tenant={tenant_id}")

            return {
                "success": True,
                "data": {
                    "session_id": session_id,
                    "count": len(customers),
                    "customers": customers
                }
            }
    except Exception as e:
        logger.error(f"后端日志：查询会话客户列表失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "查询会话客户列表失败",
            "debug": sanitize_error_info(str(e))
        }
