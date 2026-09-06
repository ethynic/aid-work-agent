#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
销售线索管理脚本

用于客户跟进智能体的核心数据管理：线索导入、增删改查、阶段流转、分配、统计。

用法:
    # 初始化数据表
    python lead_manager.py init_tables

    # Excel 导入线索
    python lead_manager.py import-leads --user-id USER --file-path PATH [--mapping JSON]

    # 手动添加线索
    python lead_manager.py add-lead --user-id USER --lead JSON

    # 查询线索列表
    python lead_manager.py list-leads --user-id USER [--stage STAGE] [--status STATUS] [--keyword KW] [--page N] [--page-size N]

    # 查看线索详情
    python lead_manager.py get-lead --lead-id ID

    # 更新线索
    python lead_manager.py update-lead --lead-id ID --fields JSON

    # 变更阶段
    python lead_manager.py update-stage --lead-id ID --stage STAGE [--note NOTE]

    # 删除线索（软删除）
    python lead_manager.py delete-lead --lead-id ID [--reason REASON]

    # 分配线索
    python lead_manager.py assign-lead --lead-id ID [--assigned-to USER_ID] [--rule RULE]

    # 批量分配
    python lead_manager.py batch-assign [--rule RULE] [--unassigned-only]

    # 线索统计
    python lead_manager.py stats --user-id USER

    # 导出线索
    python lead_manager.py export-leads --user-id USER [--stage STAGE] [--status STATUS] [--format FMT]
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

# 添加项目根目录到路径
script_path = Path(__file__).resolve()
if 'src' in script_path.parts:
    src_index = script_path.parts.index('src')
    project_root = Path(*script_path.parts[:src_index])
else:
    project_root = script_path.parent
sys.path.insert(0, str(project_root))

from loguru import logger

# --- 常量 ---
VALID_STAGES = ['new', 'contacting', 'qualified', 'proposal', 'negotiation', 'won', 'lost']
VALID_STATUSES = ['active', 'converted', 'lost', 'recycled']
VALID_SOURCES = ['import', 'api', 'manual', 'referral', 'website', 'exhibition']
VALID_REP_ROLES = ['sales', 'manager', 'director']
VALID_RULE_TYPES = ['round_robin', 'load_balance', 'region_based', 'skill_based', 'manual']

# Excel 列名自动映射
COLUMN_ALIASES = {
    'company_name': ['company_name', '公司名称', '公司', '企业名称', 'company'],
    'contact_name': ['contact_name', '联系人', '姓名', '联系姓名', 'contact'],
    'phone': ['phone', '电话', '手机', '联系电话', 'tel', 'mobile'],
    'email': ['email', '邮箱', '邮件', '电子邮件'],
    'industry': ['industry', '行业', '所属行业'],
    'region': ['region', '地区', '区域', '所在地区'],
    'source': ['source', '来源', '线索来源'],
    'product_interest': ['product_interest', '产品', '感兴趣的产品', '意向产品'],
    'budget_range': ['budget_range', '预算', '预算范围'],
    'description': ['description', '备注', '说明', '描述'],
}


def get_db():
    """获取数据库连接池"""
    from src.db.database import get_db_connection
    return get_db_connection()


def get_tenant_id() -> Optional[str]:
    """从环境变量获取当前租户ID（skill_executor 注入 AID_TENANT_ID）"""
    return os.environ.get("AID_TENANT_ID") or os.environ.get("CURRENT_TENANT_ID")


def generate_id(prefix: str) -> str:
    """生成带前缀的唯一ID"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def parse_json_safe(json_str: str) -> Any:
    """安全解析 JSON 字符串"""
    if not json_str:
        return None
    json_str = json_str.strip()
    if json_str.startswith("'") and json_str.endswith("'"):
        json_str = json_str[1:-1].strip()
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    try:
        import re
        fixed = re.sub(r"(?<!\\)'", '"', json_str)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    return None


def output_json(success: bool, data: Any = None, error: str = None, debug: str = None):
    """统一输出 JSON 结果"""
    result = {"success": success}
    if success and data is not None:
        result["data"] = data
    if not success:
        result["error"] = error or "操作失败"
        if debug:
            result["debug"] = debug
    print(json.dumps(result, ensure_ascii=False, default=str))


# ============================================================
# init_tables
# ============================================================
def init_tables():
    """skill_loader 启动时调用的空初始化（表通过 CLI init_tables 命令创建）"""
    pass


def cmd_init_tables(args):
    """创建所有业务表"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()

            # 表1: 线索主表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_customer_followup_leads (
                    id SERIAL PRIMARY KEY,
                    lead_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT,
                    company_name TEXT,
                    contact_name TEXT,
                    phone TEXT,
                    email TEXT,
                    source TEXT,
                    industry TEXT,
                    region TEXT,
                    address TEXT,
                    product_interest TEXT,
                    budget_range TEXT,
                    estimated_deal_amount NUMERIC(12,2),
                    description TEXT,
                    stage TEXT DEFAULT 'new',
                    stage_entered_at TIMESTAMP,
                    score INTEGER DEFAULT 0,
                    assigned_to TEXT,
                    assigned_at TIMESTAMP,
                    assignment_rule TEXT,
                    status TEXT DEFAULT 'active',
                    lost_reason TEXT,
                    next_followup_at TIMESTAMP,
                    last_followup_at TIMESTAMP,
                    followup_count INTEGER DEFAULT 0,
                    import_batch TEXT,
                    external_id TEXT,
                    tags TEXT[] DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 表2: 跟进记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_customer_followup_records (
                    id SERIAL PRIMARY KEY,
                    record_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT,
                    lead_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    followup_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    followup_at TIMESTAMP DEFAULT NOW(),
                    duration_minutes INTEGER,
                    outcome TEXT,
                    next_action TEXT,
                    next_followup_at TIMESTAMP,
                    quality_score INTEGER,
                    call_id TEXT,
                    call_transcript TEXT,
                    call_sentiment TEXT,
                    call_summary TEXT,
                    attachments JSONB DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 表3: 销售人员表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_customer_followup_sales_reps (
                    id SERIAL PRIMARY KEY,
                    rep_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    department TEXT,
                    role TEXT DEFAULT 'sales',
                    active_lead_count INTEGER DEFAULT 0,
                    max_leads INTEGER DEFAULT 50,
                    is_active BOOLEAN DEFAULT TRUE,
                    skills TEXT[] DEFAULT '{}',
                    region TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 表4: 分配规则表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_customer_followup_assign_rules (
                    id SERIAL PRIMARY KEY,
                    rule_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT,
                    name TEXT NOT NULL,
                    rule_type TEXT NOT NULL,
                    priority INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    conditions JSONB DEFAULT '{}',
                    target_rep_ids TEXT[] DEFAULT '{}',
                    auto_assign BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # 表5: 转化漏斗表（仅追加）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_customer_followup_conversion_funnel (
                    id SERIAL PRIMARY KEY,
                    funnel_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT,
                    lead_id TEXT NOT NULL,
                    from_stage TEXT,
                    to_stage TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    changed_by TEXT,
                    days_in_previous_stage INTEGER,
                    note TEXT
                )
            """)

            # 创建索引
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_cf_leads_tenant ON bs_customer_followup_leads(tenant_id)",
                "CREATE INDEX IF NOT EXISTS idx_cf_leads_assigned ON bs_customer_followup_leads(tenant_id, assigned_to)",
                "CREATE INDEX IF NOT EXISTS idx_cf_leads_stage ON bs_customer_followup_leads(tenant_id, stage)",
                "CREATE INDEX IF NOT EXISTS idx_cf_leads_status ON bs_customer_followup_leads(tenant_id, status)",
                "CREATE INDEX IF NOT EXISTS idx_cf_leads_next_followup ON bs_customer_followup_leads(tenant_id, next_followup_at)",
                "CREATE INDEX IF NOT EXISTS idx_cf_leads_external ON bs_customer_followup_leads(external_id)",
                "CREATE INDEX IF NOT EXISTS idx_cf_leads_tags ON bs_customer_followup_leads USING GIN(tags)",
                "CREATE INDEX IF NOT EXISTS idx_cf_records_tenant ON bs_customer_followup_records(tenant_id)",
                "CREATE INDEX IF NOT EXISTS idx_cf_records_lead ON bs_customer_followup_records(lead_id)",
                "CREATE INDEX IF NOT EXISTS idx_cf_records_user ON bs_customer_followup_records(tenant_id, user_id)",
                "CREATE INDEX IF NOT EXISTS idx_cf_records_date ON bs_customer_followup_records(tenant_id, followup_at)",
                "CREATE INDEX IF NOT EXISTS idx_cf_reps_tenant ON bs_customer_followup_sales_reps(tenant_id)",
                "CREATE INDEX IF NOT EXISTS idx_cf_reps_user ON bs_customer_followup_sales_reps(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_cf_reps_active ON bs_customer_followup_sales_reps(tenant_id, is_active)",
                "CREATE INDEX IF NOT EXISTS idx_cf_rules_tenant ON bs_customer_followup_assign_rules(tenant_id)",
                "CREATE INDEX IF NOT EXISTS idx_cf_funnel_tenant ON bs_customer_followup_conversion_funnel(tenant_id)",
                "CREATE INDEX IF NOT EXISTS idx_cf_funnel_lead ON bs_customer_followup_conversion_funnel(lead_id)",
                "CREATE INDEX IF NOT EXISTS idx_cf_funnel_stage ON bs_customer_followup_conversion_funnel(tenant_id, to_stage)",
                "CREATE INDEX IF NOT EXISTS idx_cf_funnel_date ON bs_customer_followup_conversion_funnel(tenant_id, created_at)",
            ]
            for idx_sql in indexes:
                cursor.execute(idx_sql)

            conn.commit()

        output_json(True, {"message": "所有表创建成功", "tables": [
            "bs_customer_followup_leads",
            "bs_customer_followup_records",
            "bs_customer_followup_sales_reps",
            "bs_customer_followup_assign_rules",
            "bs_customer_followup_conversion_funnel",
        ]})
    except Exception as e:
        logger.opt(exception=True).error(f"init_tables 失败: {e}")
        output_json(False, error="创建表失败", debug=str(e))


# ============================================================
# add-lead
# ============================================================
def cmd_add_lead(args):
    """手动添加单条线索"""
    try:
        lead_data = parse_json_safe(args.lead)
        if not lead_data or not isinstance(lead_data, dict):
            output_json(False, error="lead 参数必须是有效的 JSON 对象")
            return

        tenant_id = get_tenant_id()
        lead_id = generate_id("lead")

        with get_db() as conn:
            cursor = conn.cursor()

            fields = {
                "lead_id": lead_id,
                "tenant_id": tenant_id,
                "user_id": args.user_id,
                "company_name": lead_data.get("company_name", ""),
                "contact_name": lead_data.get("contact_name", ""),
                "phone": lead_data.get("phone", ""),
                "email": lead_data.get("email", ""),
                "source": lead_data.get("source", "manual"),
                "industry": lead_data.get("industry", ""),
                "region": lead_data.get("region", ""),
                "address": lead_data.get("address", ""),
                "product_interest": lead_data.get("product_interest", ""),
                "budget_range": lead_data.get("budget_range", ""),
                "estimated_deal_amount": lead_data.get("estimated_deal_amount"),
                "description": lead_data.get("description", ""),
                "stage": lead_data.get("stage", "new"),
                "stage_entered_at": datetime.now().isoformat(),
                "tags": lead_data.get("tags", []),
            }

            # 校验阶段
            if fields["stage"] not in VALID_STAGES:
                output_json(False, error=f"无效阶段 '{fields['stage']}'，有效值: {VALID_STAGES}")
                return

            columns = list(fields.keys())
            placeholders = ", ".join(["%s"] * len(columns))
            values = [fields[c] for c in columns]

            cursor.execute(
                f"INSERT INTO bs_customer_followup_leads ({', '.join(columns)}) VALUES ({placeholders})",
                values
            )
            conn.commit()

        output_json(True, {"lead_id": lead_id, "message": "线索创建成功"})
    except Exception as e:
        logger.opt(exception=True).error(f"add-lead 失败: {e}")
        output_json(False, error="创建线索失败", debug=str(e))


# ============================================================
# list-leads
# ============================================================
def cmd_list_leads(args):
    """查询线索列表"""
    try:
        tenant_id = get_tenant_id()
        page = int(getattr(args, 'page', 1) or 1)
        page_size = int(getattr(args, 'page_size', 20) or 20)
        offset = (page - 1) * page_size

        conditions = ["tenant_id = %s"]
        params = [tenant_id]

        # 可选：按 user_id 过滤（如果提供了的话）
        if args.user_id:
            conditions.append("user_id = %s")
            params.append(args.user_id)

        if args.stage:
            conditions.append("stage = %s")
            params.append(args.stage)

        if args.status:
            conditions.append("status = %s")
            params.append(args.status)
        else:
            conditions.append("status != 'recycled'")

        if args.assigned_to:
            conditions.append("assigned_to = %s")
            params.append(args.assigned_to)

        if args.keyword:
            conditions.append("(company_name ILIKE %s OR contact_name ILIKE %s OR phone ILIKE %s)")
            kw = f"%{args.keyword}%"
            params.extend([kw, kw, kw])

        where_clause = " AND ".join(conditions)

        with get_db() as conn:
            cursor = conn.cursor()

            # 总数
            cursor.execute(f"SELECT COUNT(*) FROM bs_customer_followup_leads WHERE {where_clause}", params)
            total = cursor.fetchone()[0]

            # 分页数据
            cursor.execute(f"""
                SELECT lead_id, company_name, contact_name, phone, email, source,
                       industry, region, stage, status, score, assigned_to,
                       next_followup_at, followup_count, created_at
                FROM bs_customer_followup_leads
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        output_json(True, {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": rows,
        })
    except Exception as e:
        logger.opt(exception=True).error(f"list-leads 失败: {e}")
        output_json(False, error="查询线索失败", debug=str(e))


# ============================================================
# get-lead
# ============================================================
def cmd_get_lead(args):
    """查看线索详情"""
    try:
        tenant_id = get_tenant_id()

        with get_db() as conn:
            cursor = conn.cursor()

            # 线索详情
            cursor.execute("""
                SELECT * FROM bs_customer_followup_leads
                WHERE lead_id = %s AND (tenant_id = %s OR %s IS NULL)
            """, (args.lead_id, tenant_id, tenant_id))

            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()
            if not row:
                output_json(False, error=f"线索 {args.lead_id} 不存在")
                return

            lead = dict(zip(columns, row))

            # 最近跟进记录
            cursor.execute("""
                SELECT record_id, followup_type, content, outcome, quality_score,
                       followup_at, duration_minutes, call_sentiment
                FROM bs_customer_followup_records
                WHERE lead_id = %s
                ORDER BY followup_at DESC
                LIMIT 10
            """, (args.lead_id,))

            rec_columns = [desc[0] for desc in cursor.description]
            records = [dict(zip(rec_columns, r)) for r in cursor.fetchall()]

        lead["recent_records"] = records
        output_json(True, lead)
    except Exception as e:
        logger.opt(exception=True).error(f"get-lead 失败: {e}")
        output_json(False, error="查询线索详情失败", debug=str(e))


# ============================================================
# update-lead
# ============================================================
def cmd_update_lead(args):
    """更新线索字段"""
    try:
        fields = parse_json_safe(args.fields)
        if not fields or not isinstance(fields, dict):
            output_json(False, error="fields 参数必须是有效的 JSON 对象")
            return

        tenant_id = get_tenant_id()

        # 允许更新的字段
        allowed_fields = {
            'company_name', 'contact_name', 'phone', 'email', 'industry',
            'region', 'address', 'product_interest', 'budget_range',
            'estimated_deal_amount', 'description', 'tags', 'score',
            'next_followup_at'
        }

        update_parts = []
        params = []
        for key, value in fields.items():
            if key in allowed_fields:
                update_parts.append(f"{key} = %s")
                params.append(value)

        if not update_parts:
            output_json(False, error="没有可更新的字段")
            return

        update_parts.append("updated_at = NOW()")
        params.extend([args.lead_id, tenant_id, tenant_id])

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE bs_customer_followup_leads
                SET {', '.join(update_parts)}
                WHERE lead_id = %s AND (tenant_id = %s OR %s IS NULL)
            """, params)

            if cursor.rowcount == 0:
                output_json(False, error=f"线索 {args.lead_id} 不存在或无权限")
                return
            conn.commit()

        output_json(True, {"lead_id": args.lead_id, "message": "更新成功"})
    except Exception as e:
        logger.opt(exception=True).error(f"update-lead 失败: {e}")
        output_json(False, error="更新线索失败", debug=str(e))


# ============================================================
# update-stage
# ============================================================
def cmd_update_stage(args):
    """变更线索阶段"""
    try:
        if args.stage not in VALID_STAGES:
            output_json(False, error=f"无效阶段 '{args.stage}'，有效值: {VALID_STAGES}")
            return

        tenant_id = get_tenant_id()
        note = args.note or ""

        with get_db() as conn:
            cursor = conn.cursor()

            # 获取当前阶段
            cursor.execute("""
                SELECT stage, stage_entered_at FROM bs_customer_followup_leads
                WHERE lead_id = %s AND (tenant_id = %s OR %s IS NULL)
            """, (args.lead_id, tenant_id, tenant_id))

            row = cursor.fetchone()
            if not row:
                output_json(False, error=f"线索 {args.lead_id} 不存在")
                return

            from_stage = row[0]
            stage_entered_at = row[1]

            if from_stage == args.stage:
                output_json(True, {"lead_id": args.lead_id, "message": "阶段未变化", "stage": from_stage})
                return

            # 计算在上一阶段停留天数
            days_in_previous = None
            if stage_entered_at:
                try:
                    if isinstance(stage_entered_at, str):
                        prev = datetime.fromisoformat(stage_entered_at)
                    else:
                        prev = stage_entered_at
                    days_in_previous = (datetime.now() - prev).days
                except Exception:
                    pass

            # 更新线索阶段
            now = datetime.now()
            cursor.execute("""
                UPDATE bs_customer_followup_leads
                SET stage = %s, stage_entered_at = %s, updated_at = NOW(),
                    status = CASE
                        WHEN %s = 'won' THEN 'converted'
                        WHEN %s = 'lost' THEN 'lost'
                        ELSE status
                    END
                WHERE lead_id = %s
            """, (args.stage, now, args.stage, args.stage, args.lead_id))

            # 插入漏斗记录
            funnel_id = generate_id("funnel")
            cursor.execute("""
                INSERT INTO bs_customer_followup_conversion_funnel
                    (funnel_id, tenant_id, user_id, lead_id, from_stage, to_stage, created_at, changed_by, days_in_previous_stage, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (funnel_id, tenant_id, None, args.lead_id, from_stage, args.stage, now, None, days_in_previous, note))

            conn.commit()

        output_json(True, {
            "lead_id": args.lead_id,
            "from_stage": from_stage,
            "to_stage": args.stage,
            "days_in_previous_stage": days_in_previous,
            "message": f"阶段已从 {from_stage} 变更为 {args.stage}",
        })
    except Exception as e:
        logger.opt(exception=True).error(f"update-stage 失败: {e}")
        output_json(False, error="变更阶段失败", debug=str(e))


# ============================================================
# delete-lead
# ============================================================
def cmd_delete_lead(args):
    """软删除线索"""
    try:
        tenant_id = get_tenant_id()
        reason = args.reason or ""

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE bs_customer_followup_leads
                SET status = 'lost', lost_reason = %s, updated_at = NOW()
                WHERE lead_id = %s AND (tenant_id = %s OR %s IS NULL)
            """, (reason, args.lead_id, tenant_id, tenant_id))

            if cursor.rowcount == 0:
                output_json(False, error=f"线索 {args.lead_id} 不存在或无权限")
                return
            conn.commit()

        output_json(True, {"lead_id": args.lead_id, "message": "线索已标记为丢失"})
    except Exception as e:
        logger.opt(exception=True).error(f"delete-lead 失败: {e}")
        output_json(False, error="删除线索失败", debug=str(e))


# ============================================================
# assign-lead
# ============================================================
def cmd_assign_lead(args):
    """分配线索给销售人员（支持手动和自动规则分配）"""
    try:
        tenant_id = get_tenant_id()
        assigned_to = args.assigned_to
        rule = getattr(args, 'rule', None) or 'manual'
        now = datetime.now()

        if not assigned_to and rule == 'manual':
            output_json(False, error="手动分配必须指定 --assigned-to")
            return

        # 自动分配：根据规则选择销售人员
        if not assigned_to and rule != 'manual':
            assigned_to = _auto_assign_rep(tenant_id, rule, lead_id=args.lead_id)
            if not assigned_to:
                output_json(False, error=f"自动分配失败：没有可用的销售人员（规则: {rule}）")
                return

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE bs_customer_followup_leads
                SET assigned_to = %s, assigned_at = %s, assignment_rule = %s, updated_at = NOW()
                WHERE lead_id = %s AND (tenant_id = %s OR %s IS NULL)
            """, (assigned_to, now, rule, args.lead_id, tenant_id, tenant_id))

            if cursor.rowcount == 0:
                output_json(False, error=f"线索 {args.lead_id} 不存在或无权限")
                return

            # 更新销售人员的活跃线索数
            _increment_rep_lead_count(cursor, tenant_id, assigned_to)
            conn.commit()

        output_json(True, {
            "lead_id": args.lead_id,
            "assigned_to": assigned_to,
            "assignment_rule": rule,
            "message": f"线索已分配给 {assigned_to}",
        })
    except Exception as e:
        logger.opt(exception=True).error(f"assign-lead 失败: {e}")
        output_json(False, error="分配线索失败", debug=str(e))


# ============================================================
# batch-assign
# ============================================================
def cmd_batch_assign(args):
    """批量分配线索（基于规则）"""
    try:
        tenant_id = get_tenant_id()
        rule = getattr(args, 'rule', 'manual') or 'manual'
        unassigned_only = getattr(args, 'unassigned_only', False)

        if rule == 'manual':
            output_json(False, error="批量分配必须指定非 manual 规则（如 load_balance）")
            return

        with get_db() as conn:
            cursor = conn.cursor()

            # 查找待分配线索
            conditions = ["tenant_id = %s", "status = 'active'"]
            params: list = [tenant_id]
            if unassigned_only:
                conditions.append("assigned_to IS NULL")
            where = " AND ".join(conditions)

            cursor.execute(f"""
                SELECT lead_id, region FROM bs_customer_followup_leads
                WHERE {where}
            """, params)
            leads = cursor.fetchall()

            if not leads:
                output_json(True, {"assigned": 0, "message": "没有待分配的线索"})
                return

            now = datetime.now()
            assigned = 0
            skipped = 0
            for lead_id, lead_region in leads:
                target_rule = rule
                rep_id = _auto_assign_rep(tenant_id, target_rule, lead_id=lead_id, lead_region=lead_region)
                if not rep_id:
                    skipped += 1
                    continue

                cursor.execute("""
                    UPDATE bs_customer_followup_leads
                    SET assigned_to = %s, assigned_at = %s, assignment_rule = %s, updated_at = NOW()
                    WHERE lead_id = %s
                """, (rep_id, now, target_rule, lead_id))
                _increment_rep_lead_count(cursor, tenant_id, rep_id)
                assigned += 1

            conn.commit()

        output_json(True, {
            "total": len(leads),
            "assigned": assigned,
            "skipped": skipped,
            "rule": rule,
            "message": f"已分配 {assigned} 条线索，跳过 {skipped} 条",
        })
    except Exception as e:
        logger.opt(exception=True).error(f"batch-assign 失败: {e}")
        output_json(False, error="批量分配失败", debug=str(e))


# ============================================================
# stats
# ============================================================
def cmd_stats(args):
    """获取线索统计"""
    try:
        tenant_id = get_tenant_id()

        with get_db() as conn:
            cursor = conn.cursor()

            base_where = "tenant_id = %s"
            base_params = [tenant_id]

            if args.user_id:
                base_where += " AND user_id = %s"
                base_params.append(args.user_id)

            # 各阶段数量
            cursor.execute(f"""
                SELECT stage, COUNT(*) FROM bs_customer_followup_leads
                WHERE {base_where} AND status = 'active'
                GROUP BY stage ORDER BY stage
            """, base_params)
            stage_stats = {row[0]: row[1] for row in cursor.fetchall()}

            # 总数
            cursor.execute(f"""
                SELECT COUNT(*) FROM bs_customer_followup_leads
                WHERE {base_where} AND status = 'active'
            """, base_params)
            total_active = cursor.fetchone()[0]

            # 来源分布
            cursor.execute(f"""
                SELECT source, COUNT(*) FROM bs_customer_followup_leads
                WHERE {base_where} AND status = 'active'
                GROUP BY source ORDER BY COUNT(*) DESC
            """, base_params)
            source_stats = {row[0] or 'unknown': row[1] for row in cursor.fetchall()}

            # 跟进统计
            cursor.execute(f"""
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN next_followup_at < NOW() THEN 1 END) as overdue,
                    COUNT(CASE WHEN next_followup_at IS NULL THEN 1 END) as no_plan,
                    AVG(followup_count) as avg_followups
                FROM bs_customer_followup_leads
                WHERE {base_where} AND status = 'active'
            """, base_params)
            row = cursor.fetchone()
            followup_stats = {
                "total": row[0],
                "overdue": row[1],
                "no_followup_plan": row[2],
                "avg_followup_count": round(float(row[3] or 0), 1),
            }

        output_json(True, {
            "total_active": total_active,
            "by_stage": stage_stats,
            "by_source": source_stats,
            "followup": followup_stats,
        })
    except Exception as e:
        logger.opt(exception=True).error(f"stats 失败: {e}")
        output_json(False, error="获取统计失败", debug=str(e))


# ============================================================
# import-leads (Phase 1.3 将补充)
# ============================================================
def cmd_import_leads(args):
    """Excel 导入线索"""
    try:
        file_path = args.file_path
        if not os.path.exists(file_path):
            output_json(False, error=f"文件不存在: {file_path}")
            return

        tenant_id = get_tenant_id()
        user_id = args.user_id

        # 读取 Excel
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True)
            ws = wb.active
        except ImportError:
            output_json(False, error="缺少 openpyxl 依赖，请安装: pip install openpyxl")
            return
        except Exception as e:
            output_json(False, error="读取 Excel 失败", debug=str(e))
            return

        # 获取表头
        header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        headers = [str(h).strip() if h else "" for h in header_row]

        # 构建列映射
        mapping = parse_json_safe(getattr(args, 'mapping', None) or '{}') or {}
        if not mapping:
            # 自动匹配列名
            for field_name, aliases in COLUMN_ALIASES.items():
                for i, h in enumerate(headers):
                    if h.lower() in [a.lower() for a in aliases]:
                        mapping[field_name] = i
                        break

        # 将列名映射转为列索引映射
        col_index_map = {}
        for field_name, col_ref in mapping.items():
            if isinstance(col_ref, int):
                col_index_map[field_name] = col_ref
            elif isinstance(col_ref, str):
                # 列名查找
                for i, h in enumerate(headers):
                    if h == col_ref or h.lower() == col_ref.lower():
                        col_index_map[field_name] = i
                        break

        # 读取数据行
        batch_id = generate_id("batch")
        imported = 0
        skipped = 0
        errors = []

        rows = list(ws.iter_rows(min_row=2, values_only=True))
        wb.close()

        leads_to_insert = []
        for row_idx, row in enumerate(rows, start=2):
            record = {}
            for field_name, col_idx in col_index_map.items():
                if col_idx < len(row):
                    val = row[col_idx]
                    record[field_name] = str(val).strip() if val else ""

            # 至少需要 company_name 或 contact_name
            if not record.get('company_name') and not record.get('contact_name'):
                skipped += 1
                errors.append(f"第 {row_idx} 行: 缺少公司名称和联系人")
                continue

            record.setdefault('source', 'import')
            record.setdefault('stage', 'new')
            leads_to_insert.append(record)

        # 批量插入
        with get_db() as conn:
            cursor = conn.cursor()
            for record in leads_to_insert:
                lead_id = generate_id("lead")
                try:
                    cursor.execute("""
                        INSERT INTO bs_customer_followup_leads
                            (lead_id, tenant_id, user_id, company_name, contact_name,
                             phone, email, source, industry, region, address,
                             product_interest, budget_range, description,
                             stage, stage_entered_at, import_batch, tags)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        lead_id, tenant_id, user_id,
                        record.get('company_name', ''),
                        record.get('contact_name', ''),
                        record.get('phone', ''),
                        record.get('email', ''),
                        record.get('source', 'import'),
                        record.get('industry', ''),
                        record.get('region', ''),
                        record.get('address', ''),
                        record.get('product_interest', ''),
                        record.get('budget_range', ''),
                        record.get('description', ''),
                        record.get('stage', 'new'),
                        datetime.now().isoformat(),
                        batch_id,
                        record.get('tags', []),
                    ))
                    imported += 1
                except Exception as e:
                    skipped += 1
                    errors.append(f"第 {row_idx} 行插入失败: {str(e)[:100]}")

            conn.commit()

        output_json(True, {
            "batch_id": batch_id,
            "total_rows": len(rows),
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:10],  # 最多返回 10 条错误
        })
    except Exception as e:
        logger.opt(exception=True).error(f"import-leads 失败: {e}")
        output_json(False, error="导入线索失败", debug=str(e))


# ============================================================
# export-leads
# ============================================================
def cmd_export_leads(args):
    """导出线索"""
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment

        tenant_id = get_tenant_id()
        fmt = getattr(args, 'format', 'xlsx') or 'xlsx'

        conditions = ["tenant_id = %s", "status = 'active'"]
        params = [tenant_id]

        if args.user_id:
            conditions.append("user_id = %s")
            params.append(args.user_id)
        if args.stage:
            conditions.append("stage = %s")
            params.append(args.stage)
        if args.status:
            conditions[-1] = f"status = %s"
            params.append(args.status)

        where_clause = " AND ".join(conditions)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT lead_id, company_name, contact_name, phone, email, source,
                       industry, region, stage, score, assigned_to,
                       next_followup_at, followup_count, created_at
                FROM bs_customer_followup_leads
                WHERE {where_clause}
                ORDER BY created_at DESC
            """, params)

            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()

        # 生成 Excel
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "销售线索"

        # 表头
        headers_cn = ['线索ID', '公司名称', '联系人', '电话', '邮箱', '来源', '行业', '地区',
                       '阶段', '评分', '分配给', '下次跟进', '跟进次数', '创建时间']
        for col, header in enumerate(headers_cn, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = Font(bold=True)

        # 数据
        for row_idx, row in enumerate(rows, 2):
            for col_idx, val in enumerate(row):
                ws.cell(row=row_idx, column=col_idx + 1, value=str(val) if val else "")

        # 保存（遵循租户附件存储规范：storage/tenants/{tenant_id}/export/）
        from src.core.storage import get_tenant_storage_dir
        tid = get_tenant_id() or "_anonymous"
        export_dir = Path(project_root) / get_tenant_storage_dir(tid, "export")
        export_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"leads_export_{timestamp}.xlsx"
        file_path = export_dir / file_name
        wb.save(str(file_path))

        output_json(True, {
            "file_path": str(file_path),
            "file_name": file_name,
            "total": len(rows),
            "message": f"已导出 {len(rows)} 条线索",
        })
    except Exception as e:
        logger.opt(exception=True).error(f"export-leads 失败: {e}")
        output_json(False, error="导出线索失败", debug=str(e))


# ============================================================
# 销售人员管理
# ============================================================

def cmd_add_sales_rep(args):
    """添加销售人员"""
    try:
        tenant_id = get_tenant_id()
        rep_id = generate_id("rep")

        role = args.role or 'sales'
        if role not in VALID_REP_ROLES:
            output_json(False, error=f"无效角色 '{role}'，有效值: {VALID_REP_ROLES}")
            return

        skills = parse_json_safe(getattr(args, 'skills', None) or '[]') or []

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bs_customer_followup_sales_reps
                    (rep_id, tenant_id, user_id, name, department, role,
                     max_leads, skills, region)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                rep_id, tenant_id, args.user_id, args.name,
                getattr(args, 'department', None), role,
                int(getattr(args, 'max_leads', 50) or 50),
                skills, getattr(args, 'region', None),
            ))
            conn.commit()

        output_json(True, {"rep_id": rep_id, "message": "销售人员添加成功"})
    except Exception as e:
        logger.opt(exception=True).error(f"add-sales-rep 失败: {e}")
        output_json(False, error="添加销售人员失败", debug=str(e))


def cmd_list_sales_reps(args):
    """查询销售人员列表"""
    try:
        tenant_id = get_tenant_id()

        conditions = ["tenant_id = %s"]
        params: list = [tenant_id]

        active_only = getattr(args, 'active_only', True)
        if active_only:
            conditions.append("is_active = TRUE")

        where = " AND ".join(conditions)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT rep_id, user_id, name, department, role,
                       active_lead_count, max_leads, is_active,
                       skills, region, created_at
                FROM bs_customer_followup_sales_reps
                WHERE {where}
                ORDER BY name
            """, params)

            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        output_json(True, {"items": rows, "total": len(rows)})
    except Exception as e:
        logger.opt(exception=True).error(f"list-sales-reps 失败: {e}")
        output_json(False, error="查询销售人员失败", debug=str(e))


def cmd_update_sales_rep(args):
    """更新销售人员"""
    try:
        tenant_id = get_tenant_id()
        fields = parse_json_safe(args.fields)
        if not fields or not isinstance(fields, dict):
            output_json(False, error="fields 参数必须是有效的 JSON 对象")
            return

        allowed = {'name', 'department', 'role', 'max_leads', 'region', 'skills', 'is_active'}
        if 'role' in fields and fields['role'] not in VALID_REP_ROLES:
            output_json(False, error=f"无效角色 '{fields['role']}'")
            return

        updates = []
        params = []
        for key, value in fields.items():
            if key in allowed:
                updates.append(f"{key} = %s")
                params.append(value)

        if not updates:
            output_json(False, error="没有可更新的字段")
            return

        updates.append("updated_at = NOW()")
        params.extend([args.rep_id, tenant_id, tenant_id])

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE bs_customer_followup_sales_reps
                SET {', '.join(updates)}
                WHERE rep_id = %s AND (tenant_id = %s OR %s IS NULL)
            """, params)

            if cursor.rowcount == 0:
                output_json(False, error=f"销售人员 {args.rep_id} 不存在或无权限")
                return
            conn.commit()

        output_json(True, {"rep_id": args.rep_id, "message": "更新成功"})
    except Exception as e:
        logger.opt(exception=True).error(f"update-sales-rep 失败: {e}")
        output_json(False, error="更新销售人员失败", debug=str(e))


def cmd_deactivate_sales_rep(args):
    """停用销售人员"""
    try:
        tenant_id = get_tenant_id()

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE bs_customer_followup_sales_reps
                SET is_active = FALSE, updated_at = NOW()
                WHERE rep_id = %s AND (tenant_id = %s OR %s IS NULL)
            """, (args.rep_id, tenant_id, tenant_id))

            if cursor.rowcount == 0:
                output_json(False, error=f"销售人员 {args.rep_id} 不存在或无权限")
                return
            conn.commit()

        output_json(True, {"rep_id": args.rep_id, "message": "销售人员已停用"})
    except Exception as e:
        logger.opt(exception=True).error(f"deactivate-sales-rep 失败: {e}")
        output_json(False, error="停用销售人员失败", debug=str(e))


# ============================================================
# 分配规则管理
# ============================================================

def cmd_add_assign_rule(args):
    """创建分配规则"""
    try:
        tenant_id = get_tenant_id()
        rule_id = generate_id("arule")

        rule_type = args.rule_type
        if rule_type not in VALID_RULE_TYPES:
            output_json(False, error=f"无效规则类型 '{rule_type}'，有效值: {VALID_RULE_TYPES}")
            return

        conditions = parse_json_safe(getattr(args, 'conditions', None) or '{}') or {}
        target_rep_ids = parse_json_safe(getattr(args, 'target_rep_ids', None) or '[]') or []

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bs_customer_followup_assign_rules
                    (rule_id, tenant_id, user_id, name, rule_type, priority,
                     is_active, conditions, target_rep_ids, auto_assign)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
            """, (
                rule_id, tenant_id, None, args.name, rule_type,
                int(getattr(args, 'priority', 0) or 0),
                json.dumps(conditions), target_rep_ids,
                getattr(args, 'auto_assign', 'true').lower() != 'false',
            ))
            conn.commit()

        output_json(True, {"rule_id": rule_id, "message": "分配规则创建成功"})
    except Exception as e:
        logger.opt(exception=True).error(f"add-assign-rule 失败: {e}")
        output_json(False, error="创建分配规则失败", debug=str(e))


def cmd_list_assign_rules(args):
    """查询分配规则列表"""
    try:
        tenant_id = get_tenant_id()

        conditions = ["tenant_id = %s"]
        params: list = [tenant_id]

        active_only = getattr(args, 'active_only', True)
        if active_only:
            conditions.append("is_active = TRUE")

        where = " AND ".join(conditions)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT rule_id, name, rule_type, priority, is_active,
                       conditions, target_rep_ids, auto_assign, created_at
                FROM bs_customer_followup_assign_rules
                WHERE {where}
                ORDER BY created_at DESC
            """, params)

            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        output_json(True, {"items": rows, "total": len(rows)})
    except Exception as e:
        logger.opt(exception=True).error(f"list-assign-rules 失败: {e}")
        output_json(False, error="查询分配规则失败", debug=str(e))


def cmd_update_assign_rule(args):
    """更新分配规则"""
    try:
        tenant_id = get_tenant_id()
        fields = parse_json_safe(args.fields)
        if not fields or not isinstance(fields, dict):
            output_json(False, error="fields 参数必须是有效的 JSON 对象")
            return

        allowed = {'name', 'rule_type', 'priority', 'is_active', 'conditions', 'target_rep_ids', 'auto_assign'}
        if 'rule_type' in fields and fields['rule_type'] not in VALID_RULE_TYPES:
            output_json(False, error=f"无效规则类型 '{fields['rule_type']}'")
            return

        updates = []
        params = []
        for key, value in fields.items():
            if key in allowed:
                if key in ('conditions', 'target_rep_ids') and not isinstance(value, str):
                    value = json.dumps(value) if key == 'conditions' else value
                updates.append(f"{key} = %s")
                params.append(value)

        if not updates:
            output_json(False, error="没有可更新的字段")
            return

        updates.append("updated_at = NOW()")
        params.extend([args.rule_id, tenant_id, tenant_id])

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE bs_customer_followup_assign_rules
                SET {', '.join(updates)}
                WHERE rule_id = %s AND (tenant_id = %s OR %s IS NULL)
            """, params)

            if cursor.rowcount == 0:
                output_json(False, error=f"分配规则 {args.rule_id} 不存在或无权限")
                return
            conn.commit()

        output_json(True, {"rule_id": args.rule_id, "message": "更新成功"})
    except Exception as e:
        logger.opt(exception=True).error(f"update-assign-rule 失败: {e}")
        output_json(False, error="更新分配规则失败", debug=str(e))


# ============================================================
# 自动分配辅助函数
# ============================================================

def _auto_assign_rep(tenant_id: str, rule_type: str, lead_id: str = None, lead_region: str = None) -> Optional[str]:
    """根据规则自动选择销售人员返回 user_id"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()

            # 查找活跃规则
            cursor.execute("""
                SELECT rule_id, rule_type, conditions, target_rep_ids
                FROM bs_customer_followup_assign_rules
                WHERE tenant_id = %s AND is_active = TRUE AND rule_type = %s
                ORDER BY priority DESC
                LIMIT 1
            """, (tenant_id, rule_type))
            rule_row = cursor.fetchone()

            if rule_type == 'manual':
                return None

            if rule_row:
                _, r_type, conditions_json, target_rep_ids = rule_row
                conditions = json.loads(conditions_json) if isinstance(conditions_json, str) else (conditions_json or {})
            else:
                target_rep_ids = []
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

            # 过滤: 只选择未满配额的
            available = [r for r in reps if r['active_lead_count'] < r['max_leads']]
            if not available:
                available = reps  # 都满了就选最少的

            if rule_type == 'load_balance':
                selected = min(available, key=lambda r: r['active_lead_count'])
                return selected['user_id']

            elif rule_type == 'round_robin':
                import random
                return random.choice(available)['user_id']

            elif rule_type == 'region_based':
                if lead_region:
                    region_matches = [r for r in available if r.get('region') == lead_region]
                    if region_matches:
                        selected = min(region_matches, key=lambda r: r['active_lead_count'])
                        return selected['user_id']
                # 回退到负载均衡
                selected = min(available, key=lambda r: r['active_lead_count'])
                return selected['user_id']

            elif rule_type == 'skill_based':
                required_skills = conditions.get('required_skills', [])
                if required_skills:
                    skill_matches = [r for r in available
                                     if r.get('skills') and any(s in (r['skills'] or []) for s in required_skills)]
                    if skill_matches:
                        selected = min(skill_matches, key=lambda r: r['active_lead_count'])
                        return selected['user_id']
                selected = min(available, key=lambda r: r['active_lead_count'])
                return selected['user_id']

            return min(available, key=lambda r: r['active_lead_count'])['user_id']

    except Exception as e:
        logger.opt(exception=True).error(f"_auto_assign_rep 失败: {e}")
        return None


def _increment_rep_lead_count(cursor, tenant_id: str, user_id: str):
    """递增销售人员的活跃线索数"""
    try:
        cursor.execute("""
            UPDATE bs_customer_followup_sales_reps
            SET active_lead_count = active_lead_count + 1, updated_at = NOW()
            WHERE user_id = %s AND (tenant_id = %s OR %s IS NULL)
        """, (user_id, tenant_id, tenant_id))
    except Exception as e:
        logger.warning(f"_increment_rep_lead_count 失败: {e}")


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="销售线索管理工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # init_tables
    subparsers.add_parser("init_tables", help="初始化数据表")

    # import-leads
    p_import = subparsers.add_parser("import-leads", help="Excel 导入线索")
    p_import.add_argument("--user-id", required=True, help="用户ID")
    p_import.add_argument("--file-path", required=True, help="Excel 文件路径")
    p_import.add_argument("--mapping", help="列名映射 JSON")

    # add-lead
    p_add = subparsers.add_parser("add-lead", help="手动添加线索")
    p_add.add_argument("--user-id", required=True, help="用户ID")
    p_add.add_argument("--lead", required=True, help="线索信息 JSON")

    # list-leads
    p_list = subparsers.add_parser("list-leads", help="查询线索列表")
    p_list.add_argument("--user-id", default=None, help="用户ID（可选，不传则查全部）")
    p_list.add_argument("--stage", default=None, help="阶段筛选")
    p_list.add_argument("--status", default=None, help="状态筛选")
    p_list.add_argument("--assigned-to", default=None, help="分配给谁")
    p_list.add_argument("--keyword", default=None, help="关键词搜索")
    p_list.add_argument("--page", type=int, default=1, help="页码")
    p_list.add_argument("--page-size", type=int, default=20, help="每页数量")

    # get-lead
    p_get = subparsers.add_parser("get-lead", help="查看线索详情")
    p_get.add_argument("--lead-id", required=True, help="线索ID")

    # update-lead
    p_update = subparsers.add_parser("update-lead", help="更新线索")
    p_update.add_argument("--lead-id", required=True, help="线索ID")
    p_update.add_argument("--fields", required=True, help="更新字段 JSON")

    # update-stage
    p_stage = subparsers.add_parser("update-stage", help="变更线索阶段")
    p_stage.add_argument("--lead-id", required=True, help="线索ID")
    p_stage.add_argument("--stage", required=True, help="目标阶段")
    p_stage.add_argument("--note", default=None, help="变更备注")

    # delete-lead
    p_del = subparsers.add_parser("delete-lead", help="删除线索（软删除）")
    p_del.add_argument("--lead-id", required=True, help="线索ID")
    p_del.add_argument("--reason", default=None, help="删除原因")

    # assign-lead
    p_assign = subparsers.add_parser("assign-lead", help="分配线索")
    p_assign.add_argument("--lead-id", required=True, help="线索ID")
    p_assign.add_argument("--assigned-to", default=None, help="目标销售人员 user_id")
    p_assign.add_argument("--rule", default=None, help="分配规则")

    # batch-assign
    p_batch = subparsers.add_parser("batch-assign", help="批量分配线索")
    p_batch.add_argument("--rule", default=None, help="分配规则")
    p_batch.add_argument("--unassigned-only", action="store_true", help="仅分配未分配的线索")

    # stats
    p_stats = subparsers.add_parser("stats", help="线索统计")
    p_stats.add_argument("--user-id", required=True, help="用户ID")

    # export-leads
    p_export = subparsers.add_parser("export-leads", help="导出线索")
    p_export.add_argument("--user-id", required=True, help="用户ID")
    p_export.add_argument("--stage", default=None, help="阶段筛选")
    p_export.add_argument("--status", default=None, help="状态筛选")
    p_export.add_argument("--format", default="xlsx", help="导出格式")

    # add-sales-rep
    p_add_rep = subparsers.add_parser("add-sales-rep", help="添加销售人员")
    p_add_rep.add_argument("--user-id", required=True, help="系统用户ID")
    p_add_rep.add_argument("--name", required=True, help="姓名")
    p_add_rep.add_argument("--department", default=None, help="部门")
    p_add_rep.add_argument("--role", default="sales", help="角色: sales/manager/director")
    p_add_rep.add_argument("--max-leads", type=int, default=50, help="最大线索配额")
    p_add_rep.add_argument("--region", default=None, help="负责区域")
    p_add_rep.add_argument("--skills", default=None, help="擅长领域 JSON 数组")

    # list-sales-reps
    p_list_reps = subparsers.add_parser("list-sales-reps", help="查询销售人员列表")
    p_list_reps.add_argument("--active-only", action="store_true", default=True, help="仅活跃的")

    # update-sales-rep
    p_update_rep = subparsers.add_parser("update-sales-rep", help="更新销售人员")
    p_update_rep.add_argument("--rep-id", required=True, help="销售人员ID")
    p_update_rep.add_argument("--fields", required=True, help="更新字段 JSON")

    # deactivate-sales-rep
    p_deact_rep = subparsers.add_parser("deactivate-sales-rep", help="停用销售人员")
    p_deact_rep.add_argument("--rep-id", required=True, help="销售人员ID")

    # add-assign-rule
    p_add_rule = subparsers.add_parser("add-assign-rule", help="创建分配规则")
    p_add_rule.add_argument("--name", required=True, help="规则名称")
    p_add_rule.add_argument("--rule-type", required=True, help="规则类型: round_robin/load_balance/region_based/skill_based")
    p_add_rule.add_argument("--priority", type=int, default=0, help="优先级")
    p_add_rule.add_argument("--conditions", default=None, help="匹配条件 JSON")
    p_add_rule.add_argument("--target-rep-ids", default=None, help="目标销售ID JSON 数组")
    p_add_rule.add_argument("--auto-assign", default="true", help="是否自动分配")

    # list-assign-rules
    p_list_rules = subparsers.add_parser("list-assign-rules", help="查询分配规则列表")
    p_list_rules.add_argument("--active-only", action="store_true", default=True, help="仅活跃的")

    # update-assign-rule
    p_update_rule = subparsers.add_parser("update-assign-rule", help="更新分配规则")
    p_update_rule.add_argument("--rule-id", required=True, help="规则ID")
    p_update_rule.add_argument("--fields", required=True, help="更新字段 JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "init_tables": cmd_init_tables,
        "import-leads": cmd_import_leads,
        "add-lead": cmd_add_lead,
        "list-leads": cmd_list_leads,
        "get-lead": cmd_get_lead,
        "update-lead": cmd_update_lead,
        "update-stage": cmd_update_stage,
        "delete-lead": cmd_delete_lead,
        "assign-lead": cmd_assign_lead,
        "batch-assign": cmd_batch_assign,
        "stats": cmd_stats,
        "export-leads": cmd_export_leads,
        "add-sales-rep": cmd_add_sales_rep,
        "list-sales-reps": cmd_list_sales_reps,
        "update-sales-rep": cmd_update_sales_rep,
        "deactivate-sales-rep": cmd_deactivate_sales_rep,
        "add-assign-rule": cmd_add_assign_rule,
        "list-assign-rules": cmd_list_assign_rules,
        "update-assign-rule": cmd_update_assign_rule,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
