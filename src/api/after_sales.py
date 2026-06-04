"""
售后服务智能体 — API 路由

提供工单列表、工单详情、工单消息、退换货记录等 REST API 端点。
"""

import re
import traceback
from typing import Optional, Any

from fastapi import APIRouter, Request, Query
from pydantic import BaseModel
from loguru import logger

from src.db.database import get_db_connection


def sanitize_error_info(error_msg: str) -> str:
    if not error_msg:
        return error_msg
    patterns = [
        r'password["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'secret["\s:=]+\S+',
    ]
    for p in patterns:
        error_msg = re.sub(p, lambda m: m.group(0).split('=')[0] + '=***', error_msg, flags=re.IGNORECASE)
    return error_msg


def _get_tenant(request: Request) -> Optional[str]:
    return getattr(request.state, 'tenant_id', None) if hasattr(request, 'state') else None


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        result = {}
        for k, v in row.items():
            if hasattr(v, 'isoformat'):
                result[k] = v.isoformat()
            else:
                result[k] = v
        return result
    return dict(row)


router = APIRouter(prefix="/api/after-sales", tags=["售后服务"])


class JsonResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    debug: Optional[str] = None


@router.get("/tickets", response_model=JsonResponse)
async def list_tickets(
    request: Request,
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """查询工单列表（分页）"""
    try:
        tid = tenant_id or _get_tenant(request)
        effective_tid = tid if tid and tid != "demo" else None

        conditions = []
        params: list = []

        if effective_tid:
            conditions.append("t.tenant_id = %s")
            params.append(effective_tid)

        if status:
            conditions.append("t.status = %s")
            params.append(status)

        if category:
            conditions.append("t.category = %s")
            params.append(category)

        if priority:
            conditions.append("t.priority = %s")
            params.append(priority)

        if keyword:
            conditions.append("(t.description ILIKE %s OR t.ticket_id ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw])

        where = " AND ".join(conditions) if conditions else "1=1"
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(f"SELECT COUNT(*) as cnt FROM bs_after_sales_tickets t WHERE {where}", params)
            total = cursor.fetchone()["cnt"]

            cursor.execute(f"""
                SELECT t.ticket_id, t.category, t.status, t.priority,
                       t.description, t.resolution, t.order_id,
                       t.created_at, t.updated_at,
                       (SELECT COUNT(*) FROM bs_after_sales_ticket_messages m WHERE m.ticket_id = t.ticket_id) as message_count
                FROM bs_after_sales_tickets t
                WHERE {where}
                ORDER BY t.created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            items = []
            for row in cursor.fetchall():
                item = _row_to_dict(row)
                if item.get('description') and len(item['description']) > 100:
                    item['description_short'] = item['description'][:100] + '...'
                else:
                    item['description_short'] = item.get('description', '')
                items.append(item)

        return JsonResponse(success=True, data={
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        })
    except Exception as e:
        logger.error(f"查询工单列表失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))


@router.get("/tickets/{ticket_id}", response_model=JsonResponse)
async def get_ticket_detail(
    ticket_id: str,
    tenant_id: Optional[str] = Query(None),
):
    """查询工单详情（含消息记录）"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            conditions = ["ticket_id = %s"]
            params: list = [ticket_id]
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)
            where = " AND ".join(conditions)

            cursor.execute(f"""
                SELECT ticket_id, tenant_id, user_id, session_id, order_id,
                       category, status, priority, description, resolution,
                       external_ticket_id, created_at, updated_at
                FROM bs_after_sales_tickets
                WHERE {where}
            """, params)
            row = cursor.fetchone()
            if not row:
                return JsonResponse(success=False, error="工单不存在")

            ticket = _row_to_dict(row)

            cursor.execute("""
                SELECT id, sender_type, content, created_at
                FROM bs_after_sales_ticket_messages
                WHERE ticket_id = %s
                ORDER BY created_at ASC
            """, (ticket_id,))
            messages = [_row_to_dict(r) for r in cursor.fetchall()]

        ticket['messages'] = messages

        return JsonResponse(success=True, data=ticket)
    except Exception as e:
        logger.error(f"查询工单详情失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))


@router.get("/returns", response_model=JsonResponse)
async def list_returns(
    request: Request,
    status: Optional[str] = Query(None),
    return_type: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """查询退换货记录列表（分页）"""
    try:
        tid = tenant_id or _get_tenant(request)
        effective_tid = tid if tid and tid != "demo" else None

        conditions = []
        params: list = []

        if effective_tid:
            conditions.append("r.tenant_id = %s")
            params.append(effective_tid)

        if status:
            conditions.append("r.status = %s")
            params.append(status)

        if return_type:
            conditions.append("r.type = %s")
            params.append(return_type)

        if keyword:
            conditions.append("(r.reason ILIKE %s OR r.order_id ILIKE %s OR r.return_id ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])

        where = " AND ".join(conditions) if conditions else "1=1"
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(f"SELECT COUNT(*) as cnt FROM bs_after_sales_returns r WHERE {where}", params)
            total = cursor.fetchone()["cnt"]

            cursor.execute(f"""
                SELECT r.return_id, r.order_id, r.type, r.reason, r.status,
                       r.items, r.refund_amount, r.created_at, r.updated_at
                FROM bs_after_sales_returns r
                WHERE {where}
                ORDER BY r.created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            items = [_row_to_dict(r) for r in cursor.fetchall()]

        return JsonResponse(success=True, data={
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        })
    except Exception as e:
        logger.error(f"查询退换货记录失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))
