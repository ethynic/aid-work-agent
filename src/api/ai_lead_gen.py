"""
AI获客智能体 — 业务数据查询 API（只读）

提供获客活动、线索公司、联系人、邮件记录、回复记录的只读列表/详情端点，
供租户前台业务数据页面查询使用。数据由 ai-lead-gen 技能脚本写入，本模块不做任何写操作。
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
    """从 request.state 获取 tenant_id（由 TenantContextMiddleware 设置）"""
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


router = APIRouter(prefix="/api/ai-lead-gen", tags=["AI获客"])


class JsonResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    debug: Optional[str] = None


# 合法枚举值（与 src/skills/ai-lead-gen-1.0.0/scripts/lead_db.py 保持一致）
CAMPAIGN_STATUSES = {"draft", "searching", "drafting", "awaiting_review", "sending", "paused", "completed", "failed"}
SEND_MODES = {"review", "auto"}
LEAD_STATUSES = {"new", "qualified", "rejected", "contacted"}
EMAIL_STATUSES = {"draft", "approved", "sending", "sent", "failed", "bounced"}
CONFIDENCE_LEVELS = {"high", "medium", "low"}
EMAIL_SOURCES = {"official_site", "directory", "verified_pattern", "guessed", "manual"}
INTENTS = {"interested", "info_request", "refused", "irrelevant", "unclear"}


def _resolve_tenant(request: Request, tenant_id: Optional[str]) -> Optional[str]:
    """解析租户：无租户上下文返回 None，由调用方拒绝（防止漏 X-Tenant-Id 时查全库）"""
    return tenant_id or _get_tenant(request)


def _enum_error(field: str, value: str, allowed: set) -> JsonResponse:
    return JsonResponse(
        success=False,
        error=f"非法的 {field} 参数: {value}",
        debug=f"allowed={sorted(allowed)}",
    )


@router.get("/campaigns/options", response_model=JsonResponse)
async def get_campaign_options(request: Request, tenant_id: Optional[str] = Query(None)):
    """活动下拉选项（轻量，供其他页面筛选）"""
    try:
        tid = _resolve_tenant(request, tenant_id)
        if not tid:
            return JsonResponse(success=False, error="缺少租户上下文")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, campaign_name, status
                FROM bs_ai_lead_gen_campaigns
                WHERE tenant_id = %s
                ORDER BY created_at DESC
                LIMIT 100
            """, (tid,))
            items = [_row_to_dict(row) for row in cursor.fetchall()]
        return JsonResponse(success=True, data={"items": items})
    except Exception as e:
        logger.error(f"查询活动下拉失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))


@router.get("/campaigns", response_model=JsonResponse)
async def list_campaigns(
    request: Request,
    status: Optional[str] = Query(None),
    send_mode: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """获客活动列表（分页）"""
    try:
        tid = _resolve_tenant(request, tenant_id)
        if not tid:
            return JsonResponse(success=False, error="缺少租户上下文")
        if status and status not in CAMPAIGN_STATUSES:
            return _enum_error("活动状态", status, CAMPAIGN_STATUSES)
        if send_mode and send_mode not in SEND_MODES:
            return _enum_error("发送模式", send_mode, SEND_MODES)

        conditions = ["tenant_id = %s"]
        params: list = [tid]
        if status:
            conditions.append("status = %s")
            params.append(status)
        if send_mode:
            conditions.append("send_mode = %s")
            params.append(send_mode)
        if keyword:
            conditions.append("(campaign_name ILIKE %s OR brief ILIKE %s OR target_region ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])
        where = " AND ".join(conditions)
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) AS cnt FROM bs_ai_lead_gen_campaigns WHERE {where}", params)
            total = cursor.fetchone()["cnt"]
            cursor.execute(f"""
                SELECT id, campaign_name, brief, target_region, send_mode, status,
                       daily_limit, scheduler_task_id, reply_mailbox, send_mailbox_level,
                       product_refs, created_at, updated_at
                FROM bs_ai_lead_gen_campaigns
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])
            items = [_row_to_dict(row) for row in cursor.fetchall()]

        return JsonResponse(success=True, data={"total": total, "page": page, "page_size": page_size, "items": items})
    except Exception as e:
        logger.error(f"查询获客活动列表失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))


@router.get("/leads", response_model=JsonResponse)
async def list_leads(
    request: Request,
    campaign_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """线索公司列表（分页）"""
    try:
        tid = _resolve_tenant(request, tenant_id)
        if not tid:
            return JsonResponse(success=False, error="缺少租户上下文")
        if status and status not in LEAD_STATUSES:
            return _enum_error("线索状态", status, LEAD_STATUSES)

        conditions = ["l.tenant_id = %s"]
        params: list = [tid]
        if campaign_id is not None:
            conditions.append("l.campaign_id = %s")
            params.append(campaign_id)
        if status:
            conditions.append("l.status = %s")
            params.append(status)
        if keyword:
            conditions.append("(l.company_name ILIKE %s OR l.country ILIKE %s OR l.city ILIKE %s OR l.industry ILIKE %s OR l.website ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw] * 5)
        where = " AND ".join(conditions)
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT COUNT(*) AS cnt
                FROM bs_ai_lead_gen_leads l
                WHERE {where}
            """, params)
            total = cursor.fetchone()["cnt"]
            cursor.execute(f"""
                SELECT l.id, l.campaign_id, c.campaign_name, l.company_name, l.country, l.city,
                       l.website, l.industry, l.employees, l.founded, l.source, l.source_url,
                       l.score, l.status, l.reject_reason, l.notes, l.created_at, l.updated_at
                FROM bs_ai_lead_gen_leads l
                LEFT JOIN bs_ai_lead_gen_campaigns c ON c.id = l.campaign_id
                WHERE {where}
                ORDER BY l.created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])
            items = [_row_to_dict(row) for row in cursor.fetchall()]

        return JsonResponse(success=True, data={"total": total, "page": page, "page_size": page_size, "items": items})
    except Exception as e:
        logger.error(f"查询线索公司列表失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))


@router.get("/contacts", response_model=JsonResponse)
async def list_contacts(
    request: Request,
    campaign_id: Optional[int] = Query(None),
    confidence: Optional[str] = Query(None),
    email_source: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """联系人列表（分页）"""
    try:
        tid = _resolve_tenant(request, tenant_id)
        if not tid:
            return JsonResponse(success=False, error="缺少租户上下文")
        if confidence and confidence not in CONFIDENCE_LEVELS:
            return _enum_error("置信度", confidence, CONFIDENCE_LEVELS)
        if email_source and email_source not in EMAIL_SOURCES:
            return _enum_error("邮箱来源", email_source, EMAIL_SOURCES)

        conditions = ["ct.tenant_id = %s"]
        params: list = [tid]
        if campaign_id is not None:
            conditions.append("ct.campaign_id = %s")
            params.append(campaign_id)
        if confidence:
            conditions.append("ct.confidence = %s")
            params.append(confidence)
        if email_source:
            conditions.append("ct.email_source = %s")
            params.append(email_source)
        if keyword:
            conditions.append("(ct.name ILIKE %s OR ct.title ILIKE %s OR ct.email ILIKE %s OR ct.email_function ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw] * 4)
        where = " AND ".join(conditions)
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT COUNT(*) AS cnt
                FROM bs_ai_lead_gen_contacts ct
                WHERE {where}
            """, params)
            total = cursor.fetchone()["cnt"]
            cursor.execute(f"""
                SELECT ct.id, ct.campaign_id, c.campaign_name, ct.lead_id, ld.company_name,
                       ct.name, ct.title, ct.email, ct.email_source, ct.email_type,
                       ct.email_function, ct.email_relevance, ct.confidence, ct.language,
                       ct.phone, ct.linkedin, ct.notes, ct.created_at, ct.updated_at
                FROM bs_ai_lead_gen_contacts ct
                LEFT JOIN bs_ai_lead_gen_campaigns c ON c.id = ct.campaign_id
                LEFT JOIN bs_ai_lead_gen_leads ld ON ld.id = ct.lead_id
                WHERE {where}
                ORDER BY ct.created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])
            items = [_row_to_dict(row) for row in cursor.fetchall()]

        return JsonResponse(success=True, data={"total": total, "page": page, "page_size": page_size, "items": items})
    except Exception as e:
        logger.error(f"查询联系人列表失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))


@router.get("/emails", response_model=JsonResponse)
async def list_emails(
    request: Request,
    campaign_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    ab_group: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """邮件记录列表（分页，正文只返回前 200 字预览）"""
    try:
        tid = _resolve_tenant(request, tenant_id)
        if not tid:
            return JsonResponse(success=False, error="缺少租户上下文")
        if status and status not in EMAIL_STATUSES:
            return _enum_error("邮件状态", status, EMAIL_STATUSES)

        conditions = ["e.tenant_id = %s"]
        params: list = [tid]
        if campaign_id is not None:
            conditions.append("e.campaign_id = %s")
            params.append(campaign_id)
        if status:
            conditions.append("e.status = %s")
            params.append(status)
        if ab_group:
            conditions.append("e.ab_group = %s")
            params.append(ab_group)
        if keyword:
            conditions.append("(e.subject_a ILIKE %s OR e.subject_b ILIKE %s OR e.message_id ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw] * 3)
        where = " AND ".join(conditions)
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT COUNT(*) AS cnt
                FROM bs_ai_lead_gen_emails e
                WHERE {where}
            """, params)
            total = cursor.fetchone()["cnt"]
            cursor.execute(f"""
                SELECT e.id, e.campaign_id, c.campaign_name, e.contact_id,
                       ct.name AS contact_name, ct.email AS contact_email,
                       e.subject_a, e.subject_b, e.ab_group, LEFT(e.body, 200) AS body_preview,
                       e.status, e.message_id, e.sent_at, e.error, e.retry_count,
                       e.created_at, e.updated_at
                FROM bs_ai_lead_gen_emails e
                LEFT JOIN bs_ai_lead_gen_campaigns c ON c.id = e.campaign_id
                LEFT JOIN bs_ai_lead_gen_contacts ct ON ct.id = e.contact_id
                WHERE {where}
                ORDER BY e.created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])
            items = [_row_to_dict(row) for row in cursor.fetchall()]

        return JsonResponse(success=True, data={"total": total, "page": page, "page_size": page_size, "items": items})
    except Exception as e:
        logger.error(f"查询邮件记录列表失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))


@router.get("/emails/{email_id}", response_model=JsonResponse)
async def get_email_detail(
    email_id: int,
    request: Request,
    tenant_id: Optional[str] = Query(None),
):
    """邮件详情（含完整正文）"""
    try:
        tid = _resolve_tenant(request, tenant_id)
        if not tid:
            return JsonResponse(success=False, error="缺少租户上下文")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT e.id, e.campaign_id, c.campaign_name, e.contact_id,
                       ct.name AS contact_name, ct.email AS contact_email,
                       e.subject_a, e.subject_b, e.ab_group, e.body, e.status,
                       e.message_id, e.sent_at, e.error, e.retry_count,
                       e.created_at, e.updated_at
                FROM bs_ai_lead_gen_emails e
                LEFT JOIN bs_ai_lead_gen_campaigns c ON c.id = e.campaign_id
                LEFT JOIN bs_ai_lead_gen_contacts ct ON ct.id = e.contact_id
                WHERE e.id = %s AND e.tenant_id = %s
            """, (email_id, tid))
            row = cursor.fetchone()
        if not row:
            return JsonResponse(success=False, error="邮件不存在")
        return JsonResponse(success=True, data=_row_to_dict(row))
    except Exception as e:
        logger.error(f"查询邮件详情失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))


@router.get("/replies", response_model=JsonResponse)
async def list_replies(
    request: Request,
    campaign_id: Optional[int] = Query(None),
    intent: Optional[str] = Query(None),
    notified: Optional[bool] = Query(None),
    keyword: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """回复记录列表（分页，正文只返回前 200 字预览）"""
    try:
        tid = _resolve_tenant(request, tenant_id)
        if not tid:
            return JsonResponse(success=False, error="缺少租户上下文")
        if intent and intent not in INTENTS:
            return _enum_error("回复意图", intent, INTENTS)

        conditions = ["r.tenant_id = %s"]
        params: list = [tid]
        if campaign_id is not None:
            conditions.append("r.campaign_id = %s")
            params.append(campaign_id)
        if intent:
            conditions.append("r.intent = %s")
            params.append(intent)
        if notified is not None:
            conditions.append("r.notified = %s")
            params.append(notified)
        if keyword:
            conditions.append("(r.from_addr ILIKE %s OR r.subject ILIKE %s OR r.intent_summary ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw] * 3)
        where = " AND ".join(conditions)
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT COUNT(*) AS cnt
                FROM bs_ai_lead_gen_replies r
                WHERE {where}
            """, params)
            total = cursor.fetchone()["cnt"]
            cursor.execute(f"""
                SELECT r.id, r.campaign_id, c.campaign_name, r.contact_id,
                       ct.name AS contact_name, ct.email AS contact_email,
                       r.email_id, r.from_addr, r.subject, LEFT(r.body, 200) AS body_preview,
                       r.received_at, r.intent, r.intent_summary, r.notified,
                       r.message_id, r.created_at
                FROM bs_ai_lead_gen_replies r
                LEFT JOIN bs_ai_lead_gen_campaigns c ON c.id = r.campaign_id
                LEFT JOIN bs_ai_lead_gen_contacts ct ON ct.id = r.contact_id
                WHERE {where}
                ORDER BY r.created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])
            items = [_row_to_dict(row) for row in cursor.fetchall()]

        return JsonResponse(success=True, data={"total": total, "page": page, "page_size": page_size, "items": items})
    except Exception as e:
        logger.error(f"查询回复记录列表失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))


@router.get("/replies/{reply_id}", response_model=JsonResponse)
async def get_reply_detail(
    reply_id: int,
    request: Request,
    tenant_id: Optional[str] = Query(None),
):
    """回复详情（含完整原文）"""
    try:
        tid = _resolve_tenant(request, tenant_id)
        if not tid:
            return JsonResponse(success=False, error="缺少租户上下文")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.id, r.campaign_id, c.campaign_name, r.contact_id,
                       ct.name AS contact_name, ct.email AS contact_email,
                       r.email_id, r.from_addr, r.subject, r.body,
                       r.received_at, r.intent, r.intent_summary, r.notified,
                       r.message_id, r.created_at
                FROM bs_ai_lead_gen_replies r
                LEFT JOIN bs_ai_lead_gen_campaigns c ON c.id = r.campaign_id
                LEFT JOIN bs_ai_lead_gen_contacts ct ON ct.id = r.contact_id
                WHERE r.id = %s AND r.tenant_id = %s
            """, (reply_id, tid))
            row = cursor.fetchone()
        if not row:
            return JsonResponse(success=False, error="回复不存在")
        return JsonResponse(success=True, data=_row_to_dict(row))
    except Exception as e:
        logger.error(f"查询回复详情失败: {e}\n{traceback.format_exc()}")
        return JsonResponse(success=False, error="查询失败", debug=sanitize_error_info(str(e)))
