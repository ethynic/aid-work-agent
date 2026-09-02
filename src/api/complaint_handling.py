"""
投诉处理智能体 — API 路由

提供投诉列表、详情、统计、交互记录等 REST API 端点，供前端页面查询使用。
"""

import re
import traceback
from typing import Optional, List, Dict, Any

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
    """从 request.state 获取 tenant_id（由 TenantContextMiddleware 设置）"""
    return getattr(request.state, 'tenant_id', None) if hasattr(request, 'state') else None


def _row_to_dict(row) -> dict:
    """将数据库行转为 JSON 安全的字典（处理 datetime/RealDictRow）"""
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


router = APIRouter(prefix="/api/complaints", tags=["投诉处理"])


class JsonResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    debug: Optional[str] = None


@router.get("/list", response_model=JsonResponse)
async def list_complaints(
    request: Request,
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    urgency: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """查询投诉列表（分页）"""
    try:
        tid = tenant_id or _get_tenant(request)
        effective_tid = tid or None
        logger.info(f"[complaint-list] tid={tid}, effective_tid={effective_tid}")
        conditions = []
        params: list = []

        if effective_tid:
            conditions.append("c.tenant_id = %s")
            params.append(effective_tid)

        if status:
            conditions.append("c.status = %s")
            params.append(status)

        if category:
            conditions.append("c.category = %s")
            params.append(category)

        if urgency:
            conditions.append("c.urgency = %s")
            params.append(urgency)

        if keyword:
            conditions.append("(c.description ILIKE %s OR c.complaint_id ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw])

        where = " AND ".join(conditions) if conditions else "1=1"
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(f"SELECT COUNT(*) as cnt FROM bs_complaint_handling_complaints c WHERE {where}", params)
            total = cursor.fetchone()["cnt"]

            cursor.execute(f"""
                SELECT c.complaint_id, c.category, c.sub_category, c.urgency, c.status,
                       c.customer_emotion, c.emotion_intensity, c.description, c.resolution,
                       c.escalated_to, c.order_id, c.customer_name,
                       c.created_at, c.updated_at, c.resolved_at,
                       (SELECT COUNT(*) FROM bs_complaint_handling_interactions i WHERE i.complaint_id = c.complaint_id) as interaction_count
                FROM bs_complaint_handling_complaints c
                WHERE {where}
                ORDER BY c.created_at DESC
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
        logger.error(f"查询投诉列表失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))


@router.get("/detail/{complaint_id}", response_model=JsonResponse)
async def get_complaint_detail(
    complaint_id: str,
    tenant_id: Optional[str] = Query(None),
):
    """查询投诉详情（含交互记录）"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            conditions = ["complaint_id = %s"]
            params: list = [complaint_id]
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)
            where = " AND ".join(conditions)

            cursor.execute(f"""
                SELECT complaint_id, tenant_id, user_id, session_id, order_id,
                       customer_name, contact_info, category, sub_category, tags,
                       customer_emotion, emotion_intensity, urgency, status,
                       escalation_level, description, resolution,
                       escalated_to, escalation_reason, escalated_at,
                       created_at, updated_at, resolved_at, first_response_at
                FROM bs_complaint_handling_complaints
                WHERE {where}
            """, params)
            row = cursor.fetchone()
            if not row:
                return JsonResponse(success=False, error="投诉记录不存在")

            complaint = _row_to_dict(row)

            if isinstance(complaint.get('tags'), str):
                import json
                try:
                    complaint['tags'] = json.loads(complaint['tags'])
                except (json.JSONDecodeError, TypeError):
                    complaint['tags'] = []

            cursor.execute("""
                SELECT id, interaction_type, sender_type, sender_name, content, created_at
                FROM bs_complaint_handling_interactions
                WHERE complaint_id = %s
                ORDER BY created_at ASC
            """, (complaint_id,))
            interactions = [_row_to_dict(r) for r in cursor.fetchall()]

            cursor.execute("""
                SELECT id, action, assigned_to, due_date, status, notes, created_at
                FROM bs_complaint_handling_followups
                WHERE complaint_id = %s
                ORDER BY created_at DESC
            """, (complaint_id,))
            followups = [_row_to_dict(r) for r in cursor.fetchall()]

        complaint['interactions'] = interactions
        complaint['followups'] = followups

        return JsonResponse(success=True, data=complaint)
    except Exception as e:
        logger.error(f"查询投诉详情失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))


@router.get("/stats", response_model=JsonResponse)
async def get_complaint_stats(
    request: Request,
    period: str = Query("30d"),
    tenant_id: Optional[str] = Query(None),
):
    """投诉统计数据"""
    try:
        tid = tenant_id or _get_tenant(request)
        effective_tid = tid or None

        days = 30
        if period.endswith('d'):
            days = int(period[:-1])
        elif period.endswith('w'):
            days = int(period[:-1]) * 7
        elif period.endswith('m'):
            days = int(period[:-1]) * 30

        from datetime import datetime, timedelta
        since = datetime.now() - timedelta(days=days)

        with get_db_connection() as conn:
            cursor = conn.cursor()

            conditions = ["created_at >= %s"]
            params: list = [since]
            if effective_tid:
                conditions.append("tenant_id = %s")
                params.append(effective_tid)
            where = " AND ".join(conditions)

            # 状态分布
            cursor.execute(f"""
                SELECT status, COUNT(*) as cnt
                FROM bs_complaint_handling_complaints
                WHERE {where}
                GROUP BY status
            """, params)
            status_dist = {r["status"]: r["cnt"] for r in cursor.fetchall()}

            # 分类分布
            cursor.execute(f"""
                SELECT category, COUNT(*) as cnt
                FROM bs_complaint_handling_complaints
                WHERE {where}
                GROUP BY category
                ORDER BY cnt DESC
            """, params)
            category_dist = [{"name": r["category"], "count": r["cnt"]} for r in cursor.fetchall()]

            # 紧急程度分布
            cursor.execute(f"""
                SELECT urgency, COUNT(*) as cnt
                FROM bs_complaint_handling_complaints
                WHERE {where}
                GROUP BY urgency
            """, params)
            urgency_dist = {r["urgency"]: r["cnt"] for r in cursor.fetchall()}

            # 趋势（按天）
            cursor.execute(f"""
                SELECT DATE(created_at) as day, COUNT(*) as cnt
                FROM bs_complaint_handling_complaints
                WHERE {where}
                GROUP BY DATE(created_at)
                ORDER BY day
            """, params)
            trend = []
            for r in cursor.fetchall():
                day_val = r["day"]
                trend.append({
                    "date": day_val.isoformat() if hasattr(day_val, 'isoformat') else str(day_val),
                    "count": r["cnt"],
                })

            # 总数
            total = sum(status_dist.values())

            # 平均处理时长（已解决的）
            cursor.execute(f"""
                SELECT AVG(EXTRACT(EPOCH FROM (resolved_at - created_at))/3600) as avg_h
                FROM bs_complaint_handling_complaints
                WHERE {where} AND resolved_at IS NOT NULL
            """, params)
            avg_row = cursor.fetchone()
            avg_resolution_hours = round(float(avg_row["avg_h"]), 1) if avg_row and avg_row["avg_h"] else 0

        return JsonResponse(success=True, data={
            "total": total,
            "status_distribution": status_dist,
            "category_distribution": category_dist,
            "urgency_distribution": urgency_dist,
            "daily_trend": trend,
            "avg_resolution_hours": avg_resolution_hours,
            "period": period,
        })
    except Exception as e:
        logger.error(f"查询投诉统计失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))


@router.get("/interactions/{complaint_id}", response_model=JsonResponse)
async def list_interactions(
    complaint_id: str,
    tenant_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """查询投诉的交互记录"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, interaction_type, sender_type, sender_name, content, created_at
                FROM bs_complaint_handling_interactions
                WHERE complaint_id = %s
                ORDER BY created_at ASC
                LIMIT %s
            """, (complaint_id, limit))
            items = [_row_to_dict(r) for r in cursor.fetchall()]

        return JsonResponse(success=True, data={"items": items, "total": len(items)})
    except Exception as e:
        logger.error(f"查询交互记录失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))
