#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
跟进记录管理脚本

用于客户跟进智能体的跟进记录管理：创建记录、提醒、质量评估、AI外呼记录。

用法:
    python followup_manager.py init_tables
    python followup_manager.py add-record --lead-id ID --user-id UID --type phone --content "..."
    python followup_manager.py list-records --user-id UID [--lead-id ID] [--limit 50]
    python followup_manager.py get-record --record-id ID
    python followup_manager.py evaluate-quality --record-id ID
    python followup_manager.py batch-evaluate --user-id UID
    python followup_manager.py get-reminders --user-id UID
    python followup_manager.py get-overdue
    python followup_manager.py reminder-stats --user-id UID
    python followup_manager.py record-ai-call --lead-id ID --user-id UID --call-id CID --transcript "..."
    python followup_manager.py batch-ai-call-results --results '[...]'
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

script_path = Path(__file__).resolve()
if 'src' in script_path.parts:
    src_index = script_path.parts.index('src')
    project_root = Path(*script_path.parts[:src_index])
else:
    project_root = script_path.parent
sys.path.insert(0, str(project_root))

from loguru import logger

VALID_FOLLOWUP_TYPES = ['phone', 'email', 'visit', 'wechat', 'ai_call', 'other']
VALID_OUTCOMES = ['positive', 'neutral', 'negative', 'no_response']
VALID_SENTIMENTS = ['positive', 'neutral', 'negative']


def get_db():
    from src.db.database import get_db_connection
    return get_db_connection()


def get_tenant_id() -> Optional[str]:
    return os.environ.get("CURRENT_TENANT_ID")


def generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def parse_json_safe(json_str: str) -> Any:
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
    """skill_loader 启动时调用的空初始化（表由 lead-management 创建）"""
    pass


def cmd_init_tables(args):
    """确保跟进相关表存在（表由 lead-management 的 init_tables 创建）"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'bs_customer_followup_records'
                )
            """)
            exists = cursor.fetchone()[0]
            if not exists:
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
                indexes = [
                    "CREATE INDEX IF NOT EXISTS idx_cf_records_tenant ON bs_customer_followup_records(tenant_id)",
                    "CREATE INDEX IF NOT EXISTS idx_cf_records_lead ON bs_customer_followup_records(lead_id)",
                    "CREATE INDEX IF NOT EXISTS idx_cf_records_user ON bs_customer_followup_records(tenant_id, user_id)",
                    "CREATE INDEX IF NOT EXISTS idx_cf_records_date ON bs_customer_followup_records(tenant_id, followup_at)",
                ]
                for idx_sql in indexes:
                    cursor.execute(idx_sql)
                conn.commit()

        output_json(True, {"message": "跟进记录表已就绪"})
    except Exception as e:
        logger.error(f"init_tables 失败: {e}", exc_info=True)
        output_json(False, error="初始化表失败", debug=str(e))


# ============================================================
# add-record
# ============================================================
def cmd_add_record(args):
    """创建跟进记录"""
    try:
        tenant_id = get_tenant_id()
        record_id = generate_id("fcr")

        followup_type = args.type
        if followup_type not in VALID_FOLLOWUP_TYPES:
            output_json(False, error=f"无效跟进类型 '{followup_type}'，有效值: {VALID_FOLLOWUP_TYPES}")
            return

        next_followup_at = getattr(args, 'next_followup_at', None)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bs_customer_followup_records
                    (record_id, tenant_id, lead_id, user_id, followup_type, content,
                     followup_at, duration_minutes, outcome, next_action, next_followup_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s)
            """, (
                record_id, tenant_id, args.lead_id, args.user_id, followup_type,
                args.content, getattr(args, 'duration_minutes', None),
                getattr(args, 'outcome', None), getattr(args, 'next_action', None),
                next_followup_at,
            ))

            # 更新线索的跟进统计
            cursor.execute("""
                UPDATE bs_customer_followup_leads
                SET followup_count = followup_count + 1,
                    last_followup_at = NOW(),
                    next_followup_at = COALESCE(%s, next_followup_at),
                    updated_at = NOW()
                WHERE lead_id = %s
            """, (next_followup_at, args.lead_id))

            conn.commit()

        output_json(True, {"record_id": record_id, "message": "跟进记录创建成功"})
    except Exception as e:
        logger.error(f"add-record 失败: {e}", exc_info=True)
        output_json(False, error="创建跟进记录失败", debug=str(e))


# ============================================================
# list-records
# ============================================================
def cmd_list_records(args):
    """查询跟进记录列表"""
    try:
        tenant_id = get_tenant_id()
        limit = int(getattr(args, 'limit', 50) or 50)

        conditions = []
        params: list = []

        if tenant_id:
            conditions.append("r.tenant_id = %s")
            params.append(tenant_id)
        if args.lead_id:
            conditions.append("r.lead_id = %s")
            params.append(args.lead_id)
        if args.user_id:
            conditions.append("r.user_id = %s")
            params.append(args.user_id)
        if getattr(args, 'date_from', None):
            conditions.append("r.followup_at >= %s")
            params.append(args.date_from)
        if getattr(args, 'date_to', None):
            conditions.append("r.followup_at <= %s")
            params.append(args.date_to)

        where = " AND ".join(conditions) if conditions else "1=1"

        with get_db() as conn:
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

        output_json(True, {"items": items, "total": len(items)})
    except Exception as e:
        logger.error(f"list-records 失败: {e}", exc_info=True)
        output_json(False, error="查询跟进记录失败", debug=str(e))


# ============================================================
# get-record
# ============================================================
def cmd_get_record(args):
    """获取跟进记录详情"""
    try:
        tenant_id = get_tenant_id()

        with get_db() as conn:
            cursor = conn.cursor()
            conditions = ["r.record_id = %s"]
            params: list = [args.record_id]
            if tenant_id:
                conditions.append("r.tenant_id = %s")
                params.append(tenant_id)

            cursor.execute(f"""
                SELECT r.*, l.company_name, l.contact_name, l.phone
                FROM bs_customer_followup_records r
                LEFT JOIN bs_customer_followup_leads l ON r.lead_id = l.lead_id
                WHERE {' AND '.join(conditions)}
            """, params)

            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()
            if not row:
                output_json(False, error=f"跟进记录 {args.record_id} 不存在")
                return

        record = dict(zip(columns, row))
        output_json(True, record)
    except Exception as e:
        logger.error(f"get-record 失败: {e}", exc_info=True)
        output_json(False, error="查询跟进记录详情失败", debug=str(e))


# ============================================================
# evaluate-quality
# ============================================================
def cmd_evaluate_quality(args):
    """评估跟进质量（调用 LLM）"""
    try:
        tenant_id = get_tenant_id()

        with get_db() as conn:
            cursor = conn.cursor()
            conditions = ["record_id = %s"]
            params: list = [args.record_id]
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)

            cursor.execute(f"""
                SELECT r.*, l.company_name, l.contact_name, l.stage
                FROM bs_customer_followup_records r
                LEFT JOIN bs_customer_followup_leads l ON r.lead_id = l.lead_id
                WHERE {' AND '.join(conditions)}
            """, params)

            columns = [desc[0] for desc in cursor.description]
            row = cursor.fetchone()
            if not row:
                output_json(False, error=f"跟进记录 {args.record_id} 不存在")
                return

            record = dict(zip(columns, row))

        # 调用 LLM 评估跟进质量
        score, feedback = _evaluate_with_llm(record)

        # 更新质量评分
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE bs_customer_followup_records
                SET quality_score = %s, updated_at = NOW()
                WHERE record_id = %s
            """, (score, args.record_id))
            conn.commit()

        output_json(True, {
            "record_id": args.record_id,
            "quality_score": score,
            "feedback": feedback,
        })
    except Exception as e:
        logger.error(f"evaluate-quality 失败: {e}", exc_info=True)
        output_json(False, error="评估跟进质量失败", debug=str(e))


def _evaluate_with_llm(record: dict) -> tuple:
    """调用 LLM 评估跟进质量，返回 (score, feedback)"""
    try:
        from src.llm.gateway import llm_gateway

        prompt = f"""请评估以下销售跟进记录的质量，给出 1-10 分的评分和简短反馈。

线索信息：
- 公司：{record.get('company_name', '未知')}
- 联系人：{record.get('contact_name', '未知')}
- 当前阶段：{record.get('stage', '未知')}

跟进记录：
- 类型：{record.get('followup_type')}
- 内容：{record.get('content')}
- 结果：{record.get('outcome', '未记录')}
- 时长：{record.get('duration_minutes', '未记录')} 分钟

请用以下 JSON 格式回复：
{{"score": <1-10整数>, "feedback": "<简短评估反馈>"}}

评分标准：
- 8-10分：跟进内容详细、结果明确、有下一步计划
- 5-7分：跟进内容基本完整，但缺少某些关键信息
- 1-4分：跟进内容过于简略或缺乏实质信息"""

        import asyncio
        result = asyncio.run(llm_gateway.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        ))

        # 补计费：followup_manager 是独立进程，无 SessionRecordService，
        # record_background_llm_usage 走兜底独立落库分支（source_type=background_llm）
        from src.services.session_record import record_background_llm_usage
        record_background_llm_usage(
            result.get("usage") if isinstance(result, dict) else None,
            tenant_id=record.get("tenant_id"),
            user_id=record.get("user_id"),
            source="followup_evaluate",
        )

        content = result.get("content", "")
        # 解析 LLM 返回的 JSON
        eval_data = parse_json_safe(content)
        if eval_data and isinstance(eval_data, dict):
            score = int(eval_data.get("score", 5))
            score = max(1, min(10, score))
            feedback = eval_data.get("feedback", "")
            return score, feedback

        # 解析失败，尝试提取分数
        import re
        score_match = re.search(r'"?score"?\s*:\s*(\d+)', content)
        if score_match:
            score = max(1, min(10, int(score_match.group(1))))
            return score, content[:200]

        return 5, "自动评估完成（解析异常，使用默认评分）"
    except ImportError:
        logger.warning("LLM gateway 不可用，使用默认评分")
        return 5, "LLM 评估不可用，使用默认评分"
    except Exception as e:
        logger.warning(f"LLM 评估异常: {e}")
        return 5, f"评估异常: {str(e)[:100]}"


# ============================================================
# batch-evaluate
# ============================================================
def cmd_batch_evaluate(args):
    """批量评估跟进质量"""
    try:
        tenant_id = get_tenant_id()

        conditions = ["quality_score IS NULL"]
        params: list = []

        if tenant_id:
            conditions.append("tenant_id = %s")
            params.append(tenant_id)
        if args.user_id:
            conditions.append("user_id = %s")
            params.append(args.user_id)
        if getattr(args, 'date_from', None):
            conditions.append("followup_at >= %s")
            params.append(args.date_from)
        if getattr(args, 'date_to', None):
            conditions.append("followup_at <= %s")
            params.append(args.date_to)

        where = " AND ".join(conditions)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT record_id, followup_type, content, outcome, duration_minutes
                FROM bs_customer_followup_records
                WHERE {where}
                LIMIT 50
            """, params)

            columns = [desc[0] for desc in cursor.description]
            records = [dict(zip(columns, row)) for row in cursor.fetchall()]

        if not records:
            output_json(True, {"evaluated": 0, "message": "没有待评估的记录"})
            return

        evaluated = 0
        for record in records:
            score, _ = _evaluate_with_llm(record)
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE bs_customer_followup_records
                    SET quality_score = %s, updated_at = NOW()
                    WHERE record_id = %s
                """, (score, record['record_id']))
                conn.commit()
            evaluated += 1

        output_json(True, {
            "total": len(records),
            "evaluated": evaluated,
            "message": f"已评估 {evaluated} 条跟进记录",
        })
    except Exception as e:
        logger.error(f"batch-evaluate 失败: {e}", exc_info=True)
        output_json(False, error="批量评估失败", debug=str(e))


# ============================================================
# get-reminders
# ============================================================
def cmd_get_reminders(args):
    """获取待跟进提醒"""
    try:
        tenant_id = get_tenant_id()
        due_before = getattr(args, 'due_before', None) or datetime.now().isoformat()

        conditions = ["status = 'active'", "next_followup_at IS NOT NULL"]
        params: list = []

        if tenant_id:
            conditions.append("tenant_id = %s")
            params.append(tenant_id)
        if args.user_id:
            conditions.append("assigned_to = %s")
            params.append(args.user_id)

        conditions.append("next_followup_at <= %s")
        params.append(due_before)

        where = " AND ".join(conditions)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT lead_id, company_name, contact_name, phone,
                       stage, next_followup_at, assigned_to, followup_count
                FROM bs_customer_followup_leads
                WHERE {where}
                ORDER BY next_followup_at ASC
                LIMIT 50
            """, params)

            columns = [desc[0] for desc in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]

        output_json(True, {"items": items, "total": len(items)})
    except Exception as e:
        logger.error(f"get-reminders 失败: {e}", exc_info=True)
        output_json(False, error="获取提醒失败", debug=str(e))


# ============================================================
# get-overdue
# ============================================================
def cmd_get_overdue(args):
    """获取逾期跟进（经理视图）"""
    try:
        tenant_id = get_tenant_id()

        conditions = ["status = 'active'", "next_followup_at < NOW()"]
        params: list = []

        if tenant_id:
            conditions.append("tenant_id = %s")
            params.append(tenant_id)

        where = " AND ".join(conditions)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT l.lead_id, l.company_name, l.contact_name, l.phone,
                       l.stage, l.next_followup_at, l.assigned_to, l.followup_count,
                       sr.name as assigned_name
                FROM bs_customer_followup_leads l
                LEFT JOIN bs_customer_followup_sales_reps sr ON l.assigned_to = sr.user_id
                WHERE {where}
                ORDER BY l.next_followup_at ASC
                LIMIT 100
            """, params)

            columns = [desc[0] for desc in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]

        output_json(True, {"items": items, "total": len(items)})
    except Exception as e:
        logger.error(f"get-overdue 失败: {e}", exc_info=True)
        output_json(False, error="获取逾期跟进失败", debug=str(e))


# ============================================================
# reminder-stats
# ============================================================
def cmd_reminder_stats(args):
    """提醒统计"""
    try:
        tenant_id = get_tenant_id()

        conditions = ["status = 'active'"]
        params: list = []
        if tenant_id:
            conditions.append("tenant_id = %s")
            params.append(tenant_id)
        if args.user_id:
            conditions.append("assigned_to = %s")
            params.append(args.user_id)

        where = " AND ".join(conditions)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT
                    COUNT(*) as total_leads,
                    COUNT(CASE WHEN next_followup_at < NOW() THEN 1 END) as overdue,
                    COUNT(CASE WHEN next_followup_at BETWEEN NOW() AND NOW() + INTERVAL '24 hours' THEN 1 END) as due_today,
                    COUNT(CASE WHEN next_followup_at IS NULL THEN 1 END) as no_plan,
                    AVG(followup_count) as avg_followups
                FROM bs_customer_followup_leads
                WHERE {where}
            """, params)

            row = cursor.fetchone()
            stats = {
                "total_leads": row[0],
                "overdue": row[1],
                "due_today": row[2],
                "no_followup_plan": row[3],
                "avg_followup_count": round(float(row[4] or 0), 1),
            }

        output_json(True, stats)
    except Exception as e:
        logger.error(f"reminder-stats 失败: {e}", exc_info=True)
        output_json(False, error="获取提醒统计失败", debug=str(e))


# ============================================================
# record-ai-call
# ============================================================
def cmd_record_ai_call(args):
    """记录 AI 外呼结果（自动创建跟进记录）"""
    try:
        tenant_id = get_tenant_id()
        record_id = generate_id("fcr")

        sentiment = getattr(args, 'sentiment', 'neutral')
        if sentiment not in VALID_SENTIMENTS:
            sentiment = 'neutral'

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bs_customer_followup_records
                    (record_id, tenant_id, lead_id, user_id, followup_type, content,
                     followup_at, duration_minutes, outcome,
                     call_id, call_transcript, call_sentiment, call_summary)
                VALUES (%s, %s, %s, %s, 'ai_call', %s, NOW(), %s, %s, %s, %s, %s, %s)
            """, (
                record_id, tenant_id, args.lead_id, args.user_id,
                args.summary or "AI 外呼",
                getattr(args, 'duration', None),
                'positive' if sentiment == 'positive' else ('negative' if sentiment == 'negative' else 'neutral'),
                args.call_id, args.transcript, sentiment, args.summary,
            ))

            # 更新线索的跟进统计
            cursor.execute("""
                UPDATE bs_customer_followup_leads
                SET followup_count = followup_count + 1,
                    last_followup_at = NOW(),
                    updated_at = NOW()
                WHERE lead_id = %s
            """, (args.lead_id,))

            conn.commit()

        output_json(True, {
            "record_id": record_id,
            "lead_id": args.lead_id,
            "call_id": args.call_id,
            "sentiment": sentiment,
            "message": "AI 外呼记录已创建",
        })
    except Exception as e:
        logger.error(f"record-ai-call 失败: {e}", exc_info=True)
        output_json(False, error="记录 AI 外呼结果失败", debug=str(e))


# ============================================================
# batch-ai-call-results
# ============================================================
def cmd_batch_ai_call_results(args):
    """批量记录 AI 外呼结果"""
    try:
        tenant_id = get_tenant_id()
        results = parse_json_safe(args.results)
        if not results or not isinstance(results, list):
            output_json(False, error="results 参数必须是有效的 JSON 数组")
            return

        recorded = 0
        errors = []

        for i, item in enumerate(results):
            lead_id = item.get('lead_id')
            user_id = item.get('user_id')
            if not lead_id or not user_id:
                errors.append(f"第 {i+1} 条: 缺少 lead_id 或 user_id")
                continue

            record_id = generate_id("fcr")
            try:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO bs_customer_followup_records
                            (record_id, tenant_id, lead_id, user_id, followup_type, content,
                             followup_at, duration_minutes, outcome,
                             call_id, call_transcript, call_sentiment, call_summary)
                        VALUES (%s, %s, %s, %s, 'ai_call', %s, NOW(), %s, %s, %s, %s, %s, %s)
                    """, (
                        record_id, tenant_id, lead_id, user_id,
                        item.get('summary', 'AI 外呼'),
                        item.get('duration'),
                        item.get('outcome', 'neutral'),
                        item.get('call_id'),
                        item.get('transcript'),
                        item.get('sentiment', 'neutral'),
                        item.get('summary'),
                    ))

                    cursor.execute("""
                        UPDATE bs_customer_followup_leads
                        SET followup_count = followup_count + 1,
                            last_followup_at = NOW(),
                            updated_at = NOW()
                        WHERE lead_id = %s
                    """, (lead_id,))

                    conn.commit()
                recorded += 1
            except Exception as e:
                errors.append(f"第 {i+1} 条: {str(e)[:100]}")

        output_json(True, {
            "total": len(results),
            "recorded": recorded,
            "errors": errors[:10],
            "message": f"已记录 {recorded}/{len(results)} 条外呼结果",
        })
    except Exception as e:
        logger.error(f"batch-ai-call-results 失败: {e}", exc_info=True)
        output_json(False, error="批量记录外呼结果失败", debug=str(e))


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="跟进记录管理工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # init_tables
    subparsers.add_parser("init_tables", help="初始化跟进记录表")

    # add-record
    p_add = subparsers.add_parser("add-record", help="创建跟进记录")
    p_add.add_argument("--lead-id", required=True, help="线索ID")
    p_add.add_argument("--user-id", required=True, help="用户ID")
    p_add.add_argument("--type", required=True, help="跟进类型: phone/email/visit/wechat/ai_call/other")
    p_add.add_argument("--content", required=True, help="跟进内容")
    p_add.add_argument("--outcome", default=None, help="结果: positive/neutral/negative/no_response")
    p_add.add_argument("--next-action", default=None, help="下一步计划")
    p_add.add_argument("--next-followup-at", default=None, help="计划下次跟进时间")
    p_add.add_argument("--duration-minutes", type=int, default=None, help="跟进时长（分钟）")

    # list-records
    p_list = subparsers.add_parser("list-records", help="查询跟进记录")
    p_list.add_argument("--user-id", default=None, help="用户ID")
    p_list.add_argument("--lead-id", default=None, help="线索ID")
    p_list.add_argument("--date-from", default=None, help="开始日期")
    p_list.add_argument("--date-to", default=None, help="结束日期")
    p_list.add_argument("--limit", type=int, default=50, help="限制数量")

    # get-record
    p_get = subparsers.add_parser("get-record", help="获取跟进详情")
    p_get.add_argument("--record-id", required=True, help="记录ID")

    # evaluate-quality
    p_eval = subparsers.add_parser("evaluate-quality", help="评估跟进质量")
    p_eval.add_argument("--record-id", required=True, help="记录ID")

    # batch-evaluate
    p_batch_eval = subparsers.add_parser("batch-evaluate", help="批量质量评估")
    p_batch_eval.add_argument("--user-id", required=True, help="用户ID")
    p_batch_eval.add_argument("--date-from", default=None, help="开始日期")
    p_batch_eval.add_argument("--date-to", default=None, help="结束日期")

    # get-reminders
    p_reminders = subparsers.add_parser("get-reminders", help="获取待跟进提醒")
    p_reminders.add_argument("--user-id", required=True, help="用户ID")
    p_reminders.add_argument("--due-before", default=None, help="截止时间")

    # get-overdue
    subparsers.add_parser("get-overdue", help="获取逾期跟进")

    # reminder-stats
    p_stats = subparsers.add_parser("reminder-stats", help="提醒统计")
    p_stats.add_argument("--user-id", required=True, help="用户ID")

    # record-ai-call
    p_ai_call = subparsers.add_parser("record-ai-call", help="记录 AI 外呼结果")
    p_ai_call.add_argument("--lead-id", required=True, help="线索ID")
    p_ai_call.add_argument("--user-id", required=True, help="用户ID")
    p_ai_call.add_argument("--call-id", required=True, help="通话ID")
    p_ai_call.add_argument("--transcript", required=True, help="通话转写文本")
    p_ai_call.add_argument("--sentiment", default="neutral", help="情感: positive/neutral/negative")
    p_ai_call.add_argument("--summary", default=None, help="AI 总结")
    p_ai_call.add_argument("--duration", type=int, default=None, help="通话时长（秒）")

    # batch-ai-call-results
    p_batch_ai = subparsers.add_parser("batch-ai-call-results", help="批量记录外呼结果")
    p_batch_ai.add_argument("--results", required=True, help="外呼结果 JSON 数组")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "init_tables": cmd_init_tables,
        "add-record": cmd_add_record,
        "list-records": cmd_list_records,
        "get-record": cmd_get_record,
        "evaluate-quality": cmd_evaluate_quality,
        "batch-evaluate": cmd_batch_evaluate,
        "get-reminders": cmd_get_reminders,
        "get-overdue": cmd_get_overdue,
        "reminder-stats": cmd_reminder_stats,
        "record-ai-call": cmd_record_ai_call,
        "batch-ai-call-results": cmd_batch_ai_call_results,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
