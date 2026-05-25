"""
客户跟进智能体 — API 路由

提供线索管理、跟进记录、销售人员、分配规则、分析与漏斗等 REST API 端点。
"""

import re
import json
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, Field
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


router = APIRouter(prefix="/api/followup", tags=["客户跟进管理"])


# --- 请求/响应模型 ---

class LeadCreateRequest(BaseModel):
    company_name: Optional[str] = Field(None, description="公司名称")
    contact_name: Optional[str] = Field(None, description="联系人")
    phone: Optional[str] = Field(None, description="电话")
    email: Optional[str] = Field(None, description="邮箱")
    source: Optional[str] = Field("manual", description="来源")
    industry: Optional[str] = Field(None, description="行业")
    region: Optional[str] = Field(None, description="地区")
    product_interest: Optional[str] = Field(None, description="感兴趣的产品")
    budget_range: Optional[str] = Field(None, description="预算范围")
    estimated_deal_amount: Optional[float] = Field(None, description="预计成交金额")
    description: Optional[str] = Field(None, description="备注")
    tags: Optional[List[str]] = Field(default_factory=list, description="标签")

class LeadUpdateRequest(BaseModel):
    company_name: Optional[str] = None
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    industry: Optional[str] = None
    region: Optional[str] = None
    product_interest: Optional[str] = None
    budget_range: Optional[str] = None
    estimated_deal_amount: Optional[float] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    score: Optional[int] = None
    next_followup_at: Optional[str] = None

class StageUpdateRequest(BaseModel):
    stage: str = Field(..., description="目标阶段")
    note: Optional[str] = Field(None, description="变更备注")

class AssignRequest(BaseModel):
    assigned_to: Optional[str] = Field(None, description="目标销售人员 user_id")
    rule: Optional[str] = Field(None, description="分配规则")

class FollowupRecordRequest(BaseModel):
    lead_id: str = Field(..., description="关联线索ID")
    followup_type: str = Field(..., description="跟进类型: phone/email/visit/wechat/ai_call/other")
    content: str = Field(..., description="跟进内容")
    outcome: Optional[str] = Field(None, description="结果: positive/neutral/negative/no_response")
    next_action: Optional[str] = Field(None, description="下一步计划")
    next_followup_at: Optional[str] = Field(None, description="计划下次跟进时间")
    duration_minutes: Optional[int] = Field(None, description="跟进时长（分钟）")

class SalesRepCreateRequest(BaseModel):
    user_id: str = Field(..., description="系统用户ID")
    name: str = Field(..., description="姓名")
    department: Optional[str] = None
    role: Optional[str] = Field("sales", description="角色: sales/manager/director")
    max_leads: Optional[int] = Field(50, description="最大线索配额")
    region: Optional[str] = None
    skills: Optional[List[str]] = Field(default_factory=list, description="擅长领域")

class SalesRepUpdateRequest(BaseModel):
    name: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = None
    max_leads: Optional[int] = None
    region: Optional[str] = None
    skills: Optional[List[str]] = None
    is_active: Optional[bool] = None

class AssignRuleCreateRequest(BaseModel):
    name: str = Field(..., description="规则名称")
    rule_type: str = Field(..., description="round_robin/load_balance/region_based/skill_based/manual")
    priority: Optional[int] = Field(0, description="优先级")
    conditions: Optional[Dict[str, Any]] = Field(default_factory=dict, description="匹配条件")
    target_rep_ids: Optional[List[str]] = Field(default_factory=list, description="目标销售ID")
    auto_assign: Optional[bool] = Field(True, description="是否自动分配")

class AssignRuleUpdateRequest(BaseModel):
    name: Optional[str] = None
    rule_type: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None
    conditions: Optional[Dict[str, Any]] = None
    target_rep_ids: Optional[List[str]] = None
    auto_assign: Optional[bool] = None

class AICallRequest(BaseModel):
    call_purpose: str = Field("first_contact", description="外呼目的")
    script_hint: Optional[str] = Field(None, description="话术提示")
    max_duration: Optional[int] = Field(180, description="最大通话时长（秒）")

class BatchAssignRequest(BaseModel):
    rule: str = Field(..., description="分配规则类型")
    unassigned_only: Optional[bool] = Field(True, description="仅分配未分配的线索")


class JsonResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    debug: Optional[str] = None


def _get_tenant(request) -> Optional[str]:
    """从 request.state 获取 tenant_id"""
    return getattr(request.state, 'tenant_id', None) if hasattr(request, 'state') else None


# ============================================================
# 线索管理
# ============================================================

@router.get("/leads", response_model=JsonResponse)
async def list_leads(
    user_id: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tenant_id: Optional[str] = Query(None),
):
    try:
        conditions = []
        params: list = []

        if tenant_id:
            conditions.append("tenant_id = %s")
            params.append(tenant_id)
        if user_id:
            conditions.append("user_id = %s")
            params.append(user_id)
        if stage:
            conditions.append("stage = %s")
            params.append(stage)
        if status:
            conditions.append("status = %s")
            params.append(status)
        else:
            conditions.append("status != 'recycled'")
        if assigned_to:
            conditions.append("assigned_to = %s")
            params.append(assigned_to)
        if keyword:
            conditions.append("(company_name ILIKE %s OR contact_name ILIKE %s OR phone ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])

        where = " AND ".join(conditions) if conditions else "1=1"
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM bs_customer_followup_leads WHERE {where}", params)
            total = cursor.fetchone()[0]

            cursor.execute(f"""
                SELECT lead_id, company_name, contact_name, phone, email, source,
                       industry, region, stage, status, score, assigned_to,
                       next_followup_at, followup_count, created_at
                FROM bs_customer_followup_leads
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            columns = [desc[0] for desc in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return JsonResponse(success=True, data={"total": total, "page": page, "page_size": page_size, "items": items})
    except Exception as e:
        logger.error(f"list_leads 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="查询线索失败", debug=sanitize_error_info(str(e)))


@router.get("/leads/{lead_id}", response_model=JsonResponse)
async def get_lead(lead_id: str, tenant_id: Optional[str] = Query(None)):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = ["lead_id = %s"]
            params: list = [lead_id]
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)

            cursor.execute(f"""
                SELECT * FROM bs_customer_followup_leads WHERE {' AND '.join(conditions)}
            """, params)

            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()
            if not row:
                return JsonResponse(success=False, error="线索不存在")

            lead = dict(zip(columns, row))

            cursor.execute("""
                SELECT record_id, followup_type, content, outcome, quality_score,
                       followup_at, duration_minutes, call_sentiment
                FROM bs_customer_followup_records
                WHERE lead_id = %s ORDER BY followup_at DESC LIMIT 10
            """, (lead_id,))

            rec_cols = [desc[0] for desc in cursor.description]
            lead["recent_records"] = [dict(zip(rec_cols, r)) for r in cursor.fetchall()]

        return JsonResponse(success=True, data=lead)
    except Exception as e:
        logger.error(f"get_lead 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="查询线索详情失败", debug=sanitize_error_info(str(e)))


@router.post("/leads", response_model=JsonResponse)
async def create_lead(lead: LeadCreateRequest, user_id: str = Query(...), tenant_id: Optional[str] = Query(None)):
    try:
        import uuid
        lead_id = f"lead_{uuid.uuid4().hex[:12]}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bs_customer_followup_leads
                    (lead_id, tenant_id, user_id, company_name, contact_name, phone, email,
                     source, industry, region, product_interest, budget_range,
                     estimated_deal_amount, description, stage, stage_entered_at, tags)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            """, (
                lead_id, tenant_id, user_id, lead.company_name, lead.contact_name,
                lead.phone, lead.email, lead.source, lead.industry, lead.region,
                lead.product_interest, lead.budget_range, lead.estimated_deal_amount,
                lead.description, 'new', lead.tags,
            ))
            conn.commit()

        return JsonResponse(success=True, data={"lead_id": lead_id})
    except Exception as e:
        logger.error(f"create_lead 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="创建线索失败", debug=sanitize_error_info(str(e)))


@router.put("/leads/{lead_id}", response_model=JsonResponse)
async def update_lead(lead_id: str, body: LeadUpdateRequest, tenant_id: Optional[str] = Query(None)):
    try:
        allowed = {
            'company_name', 'contact_name', 'phone', 'email', 'industry', 'region',
            'product_interest', 'budget_range', 'estimated_deal_amount', 'description',
            'tags', 'score', 'next_followup_at'
        }

        updates = []
        params: list = []
        for field, value in body.model_dump(exclude_none=True).items():
            if field in allowed:
                updates.append(f"{field} = %s")
                params.append(value)

        if not updates:
            return JsonResponse(success=False, error="没有可更新的字段")

        updates.append("updated_at = NOW()")
        conditions = ["lead_id = %s"]
        params.append(lead_id)
        if tenant_id:
            conditions.append("tenant_id = %s")
            params.append(tenant_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE bs_customer_followup_leads SET {', '.join(updates)}
                WHERE {' AND '.join(conditions)}
            """, params)
            if cursor.rowcount == 0:
                return JsonResponse(success=False, error="线索不存在或无权限")
            conn.commit()

        return JsonResponse(success=True, data={"lead_id": lead_id, "message": "更新成功"})
    except Exception as e:
        logger.error(f"update_lead 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="更新线索失败", debug=sanitize_error_info(str(e)))


@router.post("/leads/{lead_id}/stage", response_model=JsonResponse)
async def update_lead_stage(lead_id: str, body: StageUpdateRequest, tenant_id: Optional[str] = Query(None)):
    try:
        valid_stages = ['new', 'contacting', 'qualified', 'proposal', 'negotiation', 'won', 'lost']
        if body.stage not in valid_stages:
            return JsonResponse(success=False, error=f"无效阶段 '{body.stage}'，有效值: {valid_stages}")

        import uuid
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conds = ["lead_id = %s"]
            params: list = [lead_id]
            if tenant_id:
                conds.append("tenant_id = %s")
                params.append(tenant_id)

            cursor.execute(f"""
                SELECT stage, stage_entered_at FROM bs_customer_followup_leads WHERE {' AND '.join(conds)}
            """, params)
            row = cursor.fetchone()
            if not row:
                return JsonResponse(success=False, error="线索不存在")

            from_stage = row[0]
            if from_stage == body.stage:
                return JsonResponse(success=True, data={"lead_id": lead_id, "stage": from_stage, "message": "阶段未变化"})

            from datetime import datetime
            stage_entered = row[1]
            days_prev = None
            if stage_entered:
                try:
                    prev = stage_entered if hasattr(stage_entered, 'days') else datetime.fromisoformat(str(stage_entered))
                    days_prev = (datetime.now() - prev).days if hasattr(prev, 'days') else None
                except Exception:
                    pass

            now = datetime.now()
            cursor.execute("""
                UPDATE bs_customer_followup_leads
                SET stage = %s, stage_entered_at = %s, updated_at = NOW(),
                    status = CASE WHEN %s = 'won' THEN 'converted' WHEN %s = 'lost' THEN 'lost' ELSE status END
                WHERE lead_id = %s
            """, (body.stage, now, body.stage, body.stage, lead_id))

            funnel_id = f"funnel_{uuid.uuid4().hex[:12]}"
            cursor.execute("""
                INSERT INTO bs_customer_followup_conversion_funnel
                    (funnel_id, tenant_id, lead_id, from_stage, to_stage, changed_at, days_in_previous_stage, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (funnel_id, tenant_id, lead_id, from_stage, body.stage, now, days_prev, body.note))

            conn.commit()

        return JsonResponse(success=True, data={
            "lead_id": lead_id, "from_stage": from_stage, "to_stage": body.stage,
            "days_in_previous_stage": days_prev,
        })
    except Exception as e:
        logger.error(f"update_lead_stage 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="变更阶段失败", debug=sanitize_error_info(str(e)))


@router.delete("/leads/{lead_id}", response_model=JsonResponse)
async def delete_lead(lead_id: str, reason: Optional[str] = Query(None), tenant_id: Optional[str] = Query(None)):
    try:
        conds = ["lead_id = %s"]
        params: list = [lead_id]
        if tenant_id:
            conds.append("tenant_id = %s")
            params.append(tenant_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE bs_customer_followup_leads SET status = 'lost', lost_reason = %s, updated_at = NOW()
                WHERE {' AND '.join(conds)}
            """, [reason or ""] + params)
            if cursor.rowcount == 0:
                return JsonResponse(success=False, error="线索不存在或无权限")
            conn.commit()

        return JsonResponse(success=True, data={"lead_id": lead_id, "message": "线索已标记为丢失"})
    except Exception as e:
        logger.error(f"delete_lead 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="删除线索失败", debug=sanitize_error_info(str(e)))


@router.post("/leads/{lead_id}/assign", response_model=JsonResponse)
async def assign_lead(lead_id: str, body: AssignRequest, tenant_id: Optional[str] = Query(None)):
    try:
        if not body.assigned_to and (not body.rule or body.rule == 'manual'):
            return JsonResponse(success=False, error="手动分配必须指定 assigned_to")

        from datetime import datetime
        assigned_to = body.assigned_to
        rule = body.rule or 'manual'

        # 自动分配：根据规则选择销售人员
        if not assigned_to and rule != 'manual':
            assigned_to = await _auto_assign_rep_api(tenant_id, rule, lead_id)
            if not assigned_to:
                return JsonResponse(success=False, error=f"自动分配失败：没有可用的销售人员（规则: {rule}）")

        conds = ["lead_id = %s"]
        params: list = [lead_id]
        if tenant_id:
            conds.append("tenant_id = %s")
            params.append(tenant_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE bs_customer_followup_leads
                SET assigned_to = %s, assigned_at = %s, assignment_rule = %s, updated_at = NOW()
                WHERE {' AND '.join(conds)}
            """, [assigned_to, datetime.now(), rule] + params)
            if cursor.rowcount == 0:
                return JsonResponse(success=False, error="线索不存在或无权限")

            # 更新销售人员活跃线索数
            cursor.execute("""
                UPDATE bs_customer_followup_sales_reps
                SET active_lead_count = active_lead_count + 1, updated_at = NOW()
                WHERE user_id = %s AND (tenant_id = %s OR %s IS NULL)
            """, [assigned_to, tenant_id, tenant_id])

            conn.commit()

        return JsonResponse(success=True, data={"lead_id": lead_id, "assigned_to": assigned_to, "rule": rule})
    except Exception as e:
        logger.error(f"assign_lead 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="分配线索失败", debug=sanitize_error_info(str(e)))


# ============================================================
# 跟进记录
# ============================================================

@router.get("/records", response_model=JsonResponse)
async def list_records(
    lead_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    tenant_id: Optional[str] = Query(None),
):
    try:
        conditions = []
        params: list = []
        if tenant_id:
            conditions.append("r.tenant_id = %s")
            params.append(tenant_id)
        if lead_id:
            conditions.append("r.lead_id = %s")
            params.append(lead_id)
        if user_id:
            conditions.append("r.user_id = %s")
            params.append(user_id)
        if date_from:
            conditions.append("r.followup_at >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("r.followup_at <= %s")
            params.append(date_to)

        where = " AND ".join(conditions) if conditions else "1=1"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT r.*, l.company_name, l.contact_name
                FROM bs_customer_followup_records r
                LEFT JOIN bs_customer_followup_leads l ON r.lead_id = l.lead_id
                WHERE {where}
                ORDER BY r.followup_at DESC
                LIMIT %s
            """, params + [limit])

            columns = [desc[0] for desc in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return JsonResponse(success=True, data={"items": items})
    except Exception as e:
        logger.error(f"list_records 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="查询跟进记录失败", debug=sanitize_error_info(str(e)))


@router.post("/records", response_model=JsonResponse)
async def create_record(body: FollowupRecordRequest, user_id: str = Query(...), tenant_id: Optional[str] = Query(None)):
    try:
        import uuid
        record_id = f"fcr_{uuid.uuid4().hex[:12]}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bs_customer_followup_records
                    (record_id, tenant_id, lead_id, user_id, followup_type, content,
                     followup_at, duration_minutes, outcome, next_action, next_followup_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s)
            """, (
                record_id, tenant_id, body.lead_id, user_id, body.followup_type,
                body.content, body.duration_minutes, body.outcome, body.next_action,
                body.next_followup_at,
            ))

            # 更新线索的跟进统计
            cursor.execute("""
                UPDATE bs_customer_followup_leads
                SET followup_count = followup_count + 1,
                    last_followup_at = NOW(),
                    next_followup_at = COALESCE(%s, next_followup_at),
                    updated_at = NOW()
                WHERE lead_id = %s
            """, (body.next_followup_at, body.lead_id))

            conn.commit()

        return JsonResponse(success=True, data={"record_id": record_id})
    except Exception as e:
        logger.error(f"create_record 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="创建跟进记录失败", debug=sanitize_error_info(str(e)))


# ============================================================
# 销售人员管理
# ============================================================

@router.get("/reps", response_model=JsonResponse)
async def list_reps(
    active_only: bool = Query(True),
    tenant_id: Optional[str] = Query(None),
):
    try:
        conditions = []
        params: list = []
        if tenant_id:
            conditions.append("tenant_id = %s")
            params.append(tenant_id)
        if active_only:
            conditions.append("is_active = TRUE")

        where = " AND ".join(conditions) if conditions else "1=1"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT rep_id, user_id, name, department, role,
                       active_lead_count, max_leads, is_active, skills, region, created_at
                FROM bs_customer_followup_sales_reps
                WHERE {where} ORDER BY name
            """, params)
            columns = [desc[0] for desc in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return JsonResponse(success=True, data={"items": items, "total": len(items)})
    except Exception as e:
        logger.error(f"list_reps 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="查询销售人员失败", debug=sanitize_error_info(str(e)))


@router.post("/reps", response_model=JsonResponse)
async def create_rep(body: SalesRepCreateRequest, tenant_id: Optional[str] = Query(None)):
    try:
        import uuid
        rep_id = f"rep_{uuid.uuid4().hex[:12]}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bs_customer_followup_sales_reps
                    (rep_id, tenant_id, user_id, name, department, role, max_leads, skills, region)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                rep_id, tenant_id, body.user_id, body.name, body.department,
                body.role, body.max_leads, body.skills, body.region,
            ))
            conn.commit()

        return JsonResponse(success=True, data={"rep_id": rep_id})
    except Exception as e:
        logger.error(f"create_rep 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="创建销售人员失败", debug=sanitize_error_info(str(e)))


@router.put("/reps/{rep_id}", response_model=JsonResponse)
async def update_rep(rep_id: str, body: SalesRepUpdateRequest, tenant_id: Optional[str] = Query(None)):
    try:
        allowed = {'name', 'department', 'role', 'max_leads', 'region', 'skills', 'is_active'}
        updates = []
        params: list = []
        for field, value in body.model_dump(exclude_none=True).items():
            if field in allowed:
                updates.append(f"{field} = %s")
                params.append(value)

        if not updates:
            return JsonResponse(success=False, error="没有可更新的字段")

        updates.append("updated_at = NOW()")
        conds = ["rep_id = %s"]
        params.append(rep_id)
        if tenant_id:
            conds.append("tenant_id = %s")
            params.append(tenant_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE bs_customer_followup_sales_reps SET {', '.join(updates)}
                WHERE {' AND '.join(conds)}
            """, params)
            if cursor.rowcount == 0:
                return JsonResponse(success=False, error="销售人员不存在或无权限")
            conn.commit()

        return JsonResponse(success=True, data={"rep_id": rep_id, "message": "更新成功"})
    except Exception as e:
        logger.error(f"update_rep 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="更新销售人员失败", debug=sanitize_error_info(str(e)))


# ============================================================
# 分配规则管理
# ============================================================

@router.get("/assign-rules", response_model=JsonResponse)
async def list_assign_rules(
    active_only: bool = Query(True),
    tenant_id: Optional[str] = Query(None),
):
    try:
        conditions = []
        params: list = []
        if tenant_id:
            conditions.append("tenant_id = %s")
            params.append(tenant_id)
        if active_only:
            conditions.append("is_active = TRUE")

        where = " AND ".join(conditions) if conditions else "1=1"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT rule_id, name, rule_type, priority, is_active,
                       conditions, target_rep_ids, auto_assign, created_at
                FROM bs_customer_followup_assign_rules
                WHERE {where} ORDER BY priority DESC, created_at
            """, params)
            columns = [desc[0] for desc in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return JsonResponse(success=True, data={"items": items, "total": len(items)})
    except Exception as e:
        logger.error(f"list_assign_rules 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="查询分配规则失败", debug=sanitize_error_info(str(e)))


@router.post("/assign-rules", response_model=JsonResponse)
async def create_assign_rule(body: AssignRuleCreateRequest, tenant_id: Optional[str] = Query(None)):
    try:
        import uuid
        rule_id = f"arule_{uuid.uuid4().hex[:12]}"

        valid_types = ['round_robin', 'load_balance', 'region_based', 'skill_based', 'manual']
        if body.rule_type not in valid_types:
            return JsonResponse(success=False, error=f"无效规则类型 '{body.rule_type}'")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bs_customer_followup_assign_rules
                    (rule_id, tenant_id, name, rule_type, priority,
                     is_active, conditions, target_rep_ids, auto_assign)
                VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s, %s)
            """, (
                rule_id, tenant_id, body.name, body.rule_type, body.priority,
                json.dumps(body.conditions), body.target_rep_ids, body.auto_assign,
            ))
            conn.commit()

        return JsonResponse(success=True, data={"rule_id": rule_id})
    except Exception as e:
        logger.error(f"create_assign_rule 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="创建分配规则失败", debug=sanitize_error_info(str(e)))


@router.put("/assign-rules/{rule_id}", response_model=JsonResponse)
async def update_assign_rule(rule_id: str, body: AssignRuleUpdateRequest, tenant_id: Optional[str] = Query(None)):
    try:
        allowed = {'name', 'rule_type', 'priority', 'is_active', 'conditions', 'target_rep_ids', 'auto_assign'}
        updates = []
        params: list = []
        for field, value in body.model_dump(exclude_none=True).items():
            if field in allowed:
                if field == 'conditions' and isinstance(value, dict):
                    value = json.dumps(value)
                updates.append(f"{field} = %s")
                params.append(value)

        if not updates:
            return JsonResponse(success=False, error="没有可更新的字段")

        updates.append("updated_at = NOW()")
        conds = ["rule_id = %s"]
        params.append(rule_id)
        if tenant_id:
            conds.append("tenant_id = %s")
            params.append(tenant_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE bs_customer_followup_assign_rules SET {', '.join(updates)}
                WHERE {' AND '.join(conds)}
            """, params)
            if cursor.rowcount == 0:
                return JsonResponse(success=False, error="分配规则不存在或无权限")
            conn.commit()

        return JsonResponse(success=True, data={"rule_id": rule_id, "message": "更新成功"})
    except Exception as e:
        logger.error(f"update_assign_rule 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="更新分配规则失败", debug=sanitize_error_info(str(e)))


# ============================================================
# 批量分配
# ============================================================

@router.post("/leads/batch-assign", response_model=JsonResponse)
async def batch_assign_leads(body: BatchAssignRequest, tenant_id: Optional[str] = Query(None)):
    try:
        if body.rule == 'manual':
            return JsonResponse(success=False, error="批量分配必须指定非 manual 规则")

        with get_db_connection() as conn:
            cursor = conn.cursor()

            conditions = ["status = 'active'"]
            params: list = []
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)
            if body.unassigned_only:
                conditions.append("assigned_to IS NULL")
            where = " AND ".join(conditions)

            cursor.execute(f"""
                SELECT lead_id, region FROM bs_customer_followup_leads
                WHERE {where}
            """, params)
            leads = cursor.fetchall()

            if not leads:
                return JsonResponse(success=True, data={"assigned": 0, "message": "没有待分配的线索"})

            from datetime import datetime
            now = datetime.now()
            assigned = 0
            skipped = 0

            for lead_id, lead_region in leads:
                rep_user_id = await _auto_assign_rep_api(tenant_id, body.rule, lead_id, lead_region)
                if not rep_user_id:
                    skipped += 1
                    continue

                cursor.execute("""
                    UPDATE bs_customer_followup_leads
                    SET assigned_to = %s, assigned_at = %s, assignment_rule = %s, updated_at = NOW()
                    WHERE lead_id = %s
                """, (rep_user_id, now, body.rule, lead_id))

                cursor.execute("""
                    UPDATE bs_customer_followup_sales_reps
                    SET active_lead_count = active_lead_count + 1, updated_at = NOW()
                    WHERE user_id = %s AND (tenant_id = %s OR %s IS NULL)
                """, (rep_user_id, tenant_id, tenant_id))
                assigned += 1

            conn.commit()

        return JsonResponse(success=True, data={
            "total": len(leads), "assigned": assigned, "skipped": skipped, "rule": body.rule,
        })
    except Exception as e:
        logger.error(f"batch_assign_leads 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="批量分配失败", debug=sanitize_error_info(str(e)))


# ============================================================
# AI 外呼
# ============================================================

@router.post("/leads/{lead_id}/call", response_model=JsonResponse)
async def call_lead(
    lead_id: str,
    body: AICallRequest,
    tenant_id: Optional[str] = Query(None),
):
    try:
        # 获取线索电话号码
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conds = ["lead_id = %s"]
            params: list = [lead_id]
            if tenant_id:
                conds.append("tenant_id = %s")
                params.append(tenant_id)

            cursor.execute(f"""
                SELECT phone, contact_name FROM bs_customer_followup_leads
                WHERE {' AND '.join(conds)}
            """, params)
            row = cursor.fetchone()
            if not row:
                return JsonResponse(success=False, error="线索不存在")
            phone, contact_name = row

        if not phone:
            return JsonResponse(success=False, error="该线索没有联系电话")

        # 调用 AI 外呼工具
        from src.tools.phone.ai_call_tool import AICallTool
        tool = AICallTool()
        result = await tool.execute(
            phone=phone,
            lead_id=lead_id,
            call_purpose=body.call_purpose,
            script_hint=body.script_hint or f"联系 {contact_name or '客户'}",
            max_duration=body.max_duration,
        )

        return JsonResponse(success=result.get("success", False), data=result)
    except Exception as e:
        logger.error(f"call_lead 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="发起外呼失败", debug=sanitize_error_info(str(e)))


@router.get("/call-records", response_model=JsonResponse)
async def list_call_records(
    lead_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    tenant_id: Optional[str] = Query(None),
):
    try:
        conditions = ["r.followup_type = 'ai_call'"]
        params: list = []
        if tenant_id:
            conditions.append("r.tenant_id = %s")
            params.append(tenant_id)
        if lead_id:
            conditions.append("r.lead_id = %s")
            params.append(lead_id)

        where = " AND ".join(conditions)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT r.*, l.company_name, l.contact_name
                FROM bs_customer_followup_records r
                LEFT JOIN bs_customer_followup_leads l ON r.lead_id = l.lead_id
                WHERE {where}
                ORDER BY r.followup_at DESC LIMIT %s
            """, params + [limit])
            columns = [desc[0] for desc in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return JsonResponse(success=True, data={"items": items})
    except Exception as e:
        logger.error(f"list_call_records 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="查询外呼记录失败", debug=sanitize_error_info(str(e)))


# ============================================================
# 跟进质量评估
# ============================================================

@router.post("/records/{record_id}/evaluate", response_model=JsonResponse)
async def evaluate_record(record_id: str, tenant_id: Optional[str] = Query(None)):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conds = ["r.record_id = %s"]
            params: list = [record_id]
            if tenant_id:
                conds.append("r.tenant_id = %s")
                params.append(tenant_id)

            cursor.execute(f"""
                SELECT r.*, l.company_name, l.contact_name, l.stage
                FROM bs_customer_followup_records r
                LEFT JOIN bs_customer_followup_leads l ON r.lead_id = l.lead_id
                WHERE {' AND '.join(conds)}
            """, params)
            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()
            if not row:
                return JsonResponse(success=False, error="跟进记录不存在")
            record = dict(zip(columns, row))

        # 简单评分逻辑（不调用 LLM，避免 API 依赖）
        score = _simple_quality_score(record)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE bs_customer_followup_records
                SET quality_score = %s, updated_at = NOW()
                WHERE record_id = %s
            """, (score, record_id))
            conn.commit()

        return JsonResponse(success=True, data={"record_id": record_id, "quality_score": score})
    except Exception as e:
        logger.error(f"evaluate_record 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="评估失败", debug=sanitize_error_info(str(e)))


# ============================================================
# 逾期跟进
# ============================================================

@router.get("/overdue", response_model=JsonResponse)
async def get_overdue(tenant_id: Optional[str] = Query(None)):
    try:
        conditions = ["status = 'active'", "next_followup_at < NOW()"]
        params: list = []
        if tenant_id:
            conditions.append("tenant_id = %s")
            params.append(tenant_id)

        where = " AND ".join(conditions)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT l.lead_id, l.company_name, l.contact_name, l.phone,
                       l.stage, l.next_followup_at, l.assigned_to, l.followup_count,
                       sr.name as assigned_name
                FROM bs_customer_followup_leads l
                LEFT JOIN bs_customer_followup_sales_reps sr ON l.assigned_to = sr.user_id
                    AND (sr.tenant_id = l.tenant_id OR sr.tenant_id IS NULL)
                WHERE {where}
                ORDER BY l.next_followup_at ASC LIMIT 100
            """, params)
            columns = [desc[0] for desc in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return JsonResponse(success=True, data={"items": items, "total": len(items)})
    except Exception as e:
        logger.error(f"get_overdue 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="获取逾期跟进失败", debug=sanitize_error_info(str(e)))


# ============================================================
# 自动分配辅助函数
# ============================================================

async def _auto_assign_rep_api(
    tenant_id: Optional[str], rule_type: str, lead_id: str = None, lead_region: str = None
) -> Optional[str]:
    """API 层的自动分配逻辑"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 查找活跃规则
            cursor.execute("""
                SELECT rule_id, conditions, target_rep_ids
                FROM bs_customer_followup_assign_rules
                WHERE tenant_id = %s AND is_active = TRUE AND rule_type = %s
                ORDER BY priority DESC LIMIT 1
            """, [tenant_id, rule_type])
            rule_row = cursor.fetchone()
            target_rep_ids = []

            if rule_row:
                _, conditions_json, target_rep_ids = rule_row
                conditions = json.loads(conditions_json) if isinstance(conditions_json, str) else (conditions_json or {})
            else:
                conditions = {}

            # 查找活跃销售人员
            if target_rep_ids:
                placeholders = ','.join(['%s'] * len(target_rep_ids))
                cursor.execute(f"""
                    SELECT user_id, active_lead_count, max_leads, region, skills
                    FROM bs_customer_followup_sales_reps
                    WHERE tenant_id = %s AND is_active = TRUE
                      AND rep_id IN ({placeholders})
                """, [tenant_id] + list(target_rep_ids))
            else:
                cursor.execute("""
                    SELECT user_id, active_lead_count, max_leads, region, skills
                    FROM bs_customer_followup_sales_reps
                    WHERE tenant_id = %s AND is_active = TRUE
                """, [tenant_id])

            columns = [desc[0] for desc in cursor.description]
            reps = [dict(zip(columns, row)) for row in cursor.fetchall()]

            if not reps:
                return None

            available = [r for r in reps if r['active_lead_count'] < r['max_leads']]
            if not available:
                available = reps

            import random

            if rule_type == 'load_balance':
                return min(available, key=lambda r: r['active_lead_count'])['user_id']
            elif rule_type == 'round_robin':
                return random.choice(available)['user_id']
            elif rule_type == 'region_based':
                if lead_region:
                    matches = [r for r in available if r.get('region') == lead_region]
                    if matches:
                        return min(matches, key=lambda r: r['active_lead_count'])['user_id']
                return min(available, key=lambda r: r['active_lead_count'])['user_id']
            elif rule_type == 'skill_based':
                required_skills = conditions.get('required_skills', [])
                if required_skills:
                    matches = [r for r in available
                               if r.get('skills') and any(s in (r['skills'] or []) for s in required_skills)]
                    if matches:
                        return min(matches, key=lambda r: r['active_lead_count'])['user_id']
                return min(available, key=lambda r: r['active_lead_count'])['user_id']

            return min(available, key=lambda r: r['active_lead_count'])['user_id']
    except Exception as e:
        logger.error(f"_auto_assign_rep_api 失败: {e}", exc_info=True)
        return None


def _simple_quality_score(record: dict) -> int:
    """简单跟进质量评分（不依赖 LLM）"""
    score = 5
    content = record.get('content', '')
    outcome = record.get('outcome')
    duration = record.get('duration_minutes')

    if content and len(content) > 50:
        score += 1
    if content and len(content) > 200:
        score += 1
    if outcome and outcome != 'no_response':
        score += 1
    if duration and duration > 5:
        score += 1
    if record.get('next_action'):
        score += 1

    return max(1, min(10, score))


# ============================================================
# 仪表板与分析（基础版，Phase 3 扩展）
# ============================================================

@router.get("/dashboard", response_model=JsonResponse)
async def get_dashboard(tenant_id: Optional[str] = Query(None), user_id: Optional[str] = Query(None)):
    try:
        conditions = []
        params: list = []
        if tenant_id:
            conditions.append("tenant_id = %s")
            params.append(tenant_id)
        if user_id:
            conditions.append("user_id = %s")
            params.append(user_id)
        conditions.append("status = 'active'")

        where = " AND ".join(conditions)

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 各阶段计数
            cursor.execute(f"""
                SELECT stage, COUNT(*) FROM bs_customer_followup_leads
                WHERE {where} GROUP BY stage
            """, params)
            by_stage = {row[0]: row[1] for row in cursor.fetchall()}

            # 总数
            cursor.execute(f"SELECT COUNT(*) FROM bs_customer_followup_leads WHERE {where}", params)
            total = cursor.fetchone()[0]

            # 逾期跟进数
            cursor.execute(f"""
                SELECT COUNT(*) FROM bs_customer_followup_leads
                WHERE {where} AND next_followup_at < NOW()
            """, params)
            overdue = cursor.fetchone()[0]

            # 来源分布
            cursor.execute(f"""
                SELECT source, COUNT(*) FROM bs_customer_followup_leads
                WHERE {where} GROUP BY source ORDER BY COUNT(*) DESC LIMIT 5
            """, params)
            by_source = {row[0] or 'unknown': row[1] for row in cursor.fetchall()}

        return JsonResponse(success=True, data={
            "total_active": total,
            "by_stage": by_stage,
            "by_source": by_source,
            "overdue_followups": overdue,
        })
    except Exception as e:
        logger.error(f"get_dashboard 失败: {e}", exc_info=True)
        return JsonResponse(success=False, error="获取仪表板数据失败", debug=sanitize_error_info(str(e)))
