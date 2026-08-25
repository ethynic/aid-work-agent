"""
投诉处理核心 CLI 工具。

提供投诉建档、分类、情绪分析、案例匹配、升级处理等命令。
所有投诉数据操作都通过本工具完成。

用法:
    python complaint_tool.py <command> [options]

命令:
    analyze-sentiment    分析客户情绪
    classify-complaint   投诉分类
    create-complaint     创建投诉记录
    update-complaint     更新投诉状态
    match-cases          匹配相似历史案例
    escalate-complaint   升级投诉
    list-complaints      查询投诉列表
    get-complaint        查询单条投诉详情
    create-followup      创建跟进任务
    stats                投诉统计
    add-interaction      记录交互记录
"""

import sys
import os
import json
import argparse
import uuid
from datetime import datetime, timedelta

# 添加项目根目录到 Python 路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from loguru import logger


def get_db_connection():
    """获取数据库连接"""
    from src.db.database import (
        get_db_connection as _get_db,
        get_postgres_pool,
        init_postgres_pool,
    )
    if get_postgres_pool() is None:
        logger.info("[complaint_tool] 子进程中 PostgreSQL 连接池未初始化，正在自动初始化")
        init_postgres_pool()
    return _get_db()


def _get_current_tenant_id():
    """获取当前租户ID"""
    try:
        from src.saas.context import get_current_tenant_id
        tid = get_current_tenant_id()
        if tid:
            return tid
    except Exception:
        pass
    return os.environ.get("CURRENT_TENANT_ID")


def _generate_id(prefix="cpl"):
    """生成唯一ID"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ============================================================
# 表初始化
# ============================================================

def init_tables():
    """初始化投诉处理相关表"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 投诉主表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_complaint_handling_complaints (
                    id SERIAL PRIMARY KEY,
                    complaint_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT NOT NULL,
                    session_id TEXT,
                    order_id TEXT,
                    customer_name TEXT,
                    contact_info TEXT,
                    category TEXT NOT NULL,
                    sub_category TEXT,
                    tags TEXT,
                    customer_emotion TEXT,
                    emotion_intensity REAL,
                    urgency TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL DEFAULT 'open',
                    escalation_level INT DEFAULT 0,
                    description TEXT NOT NULL,
                    resolution TEXT,
                    escalated_to TEXT,
                    escalation_reason TEXT,
                    escalated_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP,
                    first_response_at TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_complaints_tenant_status ON bs_complaint_handling_complaints (tenant_id, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_complaints_user_status ON bs_complaint_handling_complaints (user_id, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_complaints_urgency ON bs_complaint_handling_complaints (urgency, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_complaints_category ON bs_complaint_handling_complaints (category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_complaints_created ON bs_complaint_handling_complaints (created_at DESC)")

            # 投诉交互记录
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_complaint_handling_interactions (
                    id SERIAL PRIMARY KEY,
                    tenant_id TEXT,
                    user_id TEXT,
                    complaint_id TEXT NOT NULL,
                    interaction_type TEXT NOT NULL DEFAULT 'message',
                    sender_type TEXT NOT NULL,
                    sender_name TEXT,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_interactions_complaint ON bs_complaint_handling_interactions (complaint_id, created_at ASC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_interactions_tenant ON bs_complaint_handling_interactions (tenant_id)")

            # 历史案例解决方案库
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_complaint_handling_case_solutions (
                    id SERIAL PRIMARY KEY,
                    tenant_id TEXT,
                    user_id TEXT,
                    complaint_id TEXT UNIQUE NOT NULL,
                    category TEXT NOT NULL,
                    sub_category TEXT,
                    problem_summary TEXT NOT NULL,
                    root_cause TEXT,
                    solution TEXT NOT NULL,
                    resolution_time_hours REAL,
                    customer_satisfied BOOLEAN,
                    compensation_type TEXT,
                    compensation_amount NUMERIC(12,2),
                    effective BOOLEAN DEFAULT true,
                    tags TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_case_solutions_category ON bs_complaint_handling_case_solutions (category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_case_solutions_tenant ON bs_complaint_handling_case_solutions (tenant_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_case_solutions_effective ON bs_complaint_handling_case_solutions (effective, category)")

            # 跟进任务表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_complaint_handling_followups (
                    id SERIAL PRIMARY KEY,
                    tenant_id TEXT,
                    user_id TEXT,
                    complaint_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    assigned_to TEXT,
                    due_date TIMESTAMP,
                    status TEXT NOT NULL DEFAULT 'pending',
                    completed_at TIMESTAMP,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_followups_complaint ON bs_complaint_handling_followups (complaint_id, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_followups_due ON bs_complaint_handling_followups (due_date, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_followups_tenant ON bs_complaint_handling_followups (tenant_id)")

            conn.commit()
            logger.info("[complaint_tool] 投诉处理表初始化完成")
    except Exception as e:
        logger.opt(exception=True).error(f"[complaint_tool] 表初始化失败: {e}")
        raise


# ============================================================
# 情绪分析
# ============================================================

def analyze_sentiment(args):
    """分析客户情绪"""
    try:
        import asyncio
        from src.services.sentiment_service import SentimentService

        service = SentimentService()
        result = asyncio.run(service.analyze(args.text, args.context or ""))

        return {
            "success": True,
            "sentiment": result.sentiment,
            "intensity": result.intensity,
            "urgency": result.urgency,
            "key_emotions": result.key_emotions,
            "confidence": result.confidence,
            "suggested_response_tone": result.suggested_response_tone,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"情绪分析失败: {e}")
        return {"success": False, "error": f"情绪分析失败: {str(e)}"}


# ============================================================
# 投诉分类
# ============================================================

def classify_complaint(args):
    """投诉分类"""
    try:
        import asyncio
        from src.services.classification_service import ClassificationService

        categories = ["产品质量", "服务态度", "物流配送", "虚假宣传", "售后服务", "价格争议", "隐私安全", "其他"]
        sub_categories = {
            "产品质量": ["材质缺陷", "功能故障", "外观瑕疵"],
            "服务态度": ["响应慢", "态度差", "推诿扯皮"],
            "物流配送": ["延迟", "损坏", "丢失", "送错"],
            "虚假宣传": ["夸大宣传", "虚假承诺", "货不对板"],
            "售后服务": ["退款慢", "换货难", "维修不及时"],
            "价格争议": ["隐形消费", "乱收费", "价格不透明"],
            "隐私安全": ["信息泄露", "骚扰电话"],
        }

        service = ClassificationService()
        result = asyncio.run(service.classify(
            text=args.description,
            categories=categories,
            context=args.context or "",
            sub_categories=sub_categories,
        ))

        urgency = _assess_urgency(args.description, result)

        return {
            "success": True,
            "category": result.category,
            "sub_category": result.sub_category,
            "urgency": urgency,
            "confidence": result.confidence,
            "suggested_tags": result.tags,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"投诉分类失败: {e}")
        return {"success": False, "error": f"投诉分类失败: {str(e)}"}


def _assess_urgency(description: str, classification) -> str:
    """评估紧急程度"""
    text = description.lower()
    critical_keywords = ["媒体", "315", "曝光", "监管部门", "律师", "起诉", "法院"]
    urgent_keywords = ["安全", "伤", "毒", "过敏", "多人", "群体", "集体"]

    for kw in critical_keywords:
        if kw in text:
            return "critical"

    for kw in urgent_keywords:
        if kw in text:
            return "urgent"

    if classification.confidence > 0.8 and classification.category != "其他":
        return "normal"

    return "high"


# ============================================================
# 创建投诉记录
# ============================================================

def create_complaint(args):
    """创建投诉记录"""
    try:
        complaint_id = _generate_id("cpl")
        tenant_id = args.tenant_id or _get_current_tenant_id()
        tags_json = json.dumps([], ensure_ascii=False)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now()

            cursor.execute("""
                INSERT INTO bs_complaint_handling_complaints
                (complaint_id, tenant_id, user_id, session_id, order_id,
                 customer_name, contact_info, category, sub_category, tags,
                 customer_emotion, emotion_intensity, urgency, status, description,
                 first_response_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                complaint_id, tenant_id, args.user_id,
                getattr(args, "session_id", None),
                getattr(args, "order_id", None),
                None,  # customer_name
                getattr(args, "contact_info", None),
                args.category,
                getattr(args, "sub_category", None),
                tags_json,
                getattr(args, "customer_emotion", None),
                None,  # emotion_intensity
                getattr(args, "urgency", "normal"),
                "open",
                args.description,
                now,  # first_response_at
                now, now,
            ))

            row = cursor.fetchone()

            # 自动记录创建交互
            cursor.execute("""
                INSERT INTO bs_complaint_handling_interactions
                (tenant_id, user_id, complaint_id, interaction_type, sender_type, content, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                tenant_id, args.user_id, complaint_id,
                "status_change", "system",
                f"投诉已创建，分类：{args.category}，紧急程度：{getattr(args, 'urgency', 'normal')}",
                now,
            ))

            conn.commit()

        return {
            "success": True,
            "complaint_id": complaint_id,
            "category": args.category,
            "sub_category": getattr(args, "sub_category", ""),
            "urgency": getattr(args, "urgency", "normal"),
            "status": "open",
            "created_at": now.isoformat(),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"创建投诉记录失败: {e}")
        return {"success": False, "error": f"创建投诉记录失败: {str(e)}"}


# ============================================================
# 更新投诉状态
# ============================================================

def update_complaint(args):
    """更新投诉状态"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now()

            updates = ["status = %s", "updated_at = %s"]
            params = [args.status, now]

            if getattr(args, "resolution", None):
                updates.append("resolution = %s")
                params.append(args.resolution)

            if getattr(args, "assigned_to", None):
                updates.append("escalated_to = %s")
                params.append(args.assigned_to)

            if args.status in ("resolved", "closed"):
                updates.append("resolved_at = %s")
                params.append(now)

            params.append(args.complaint_id)

            cursor.execute(
                f"UPDATE bs_complaint_handling_complaints SET {', '.join(updates)} WHERE complaint_id = %s",
                params
            )

            if cursor.rowcount == 0:
                return {"success": False, "error": f"投诉记录不存在: {args.complaint_id}"}

            # 记录状态变更交互
            notes = getattr(args, "notes", "")
            content = f"状态变更为: {args.status}"
            if notes:
                content += f"。备注: {notes}"
            if getattr(args, "resolution", None):
                content += f"。处理结果: {args.resolution}"

            tenant_id = _get_current_tenant_id()
            cursor.execute("""
                INSERT INTO bs_complaint_handling_interactions
                (tenant_id, user_id, complaint_id, interaction_type, sender_type, content, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (tenant_id, None, args.complaint_id, "status_change", "agent", content, now))

            # 如果投诉已解决，自动记录到案例解决方案库
            if args.status == "resolved" and getattr(args, "resolution", None):
                _save_case_solution(cursor, conn, args.complaint_id, tenant_id)

            conn.commit()

        return {
            "success": True,
            "complaint_id": args.complaint_id,
            "status": args.status,
            "updated_at": now.isoformat(),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"更新投诉状态失败: {e}")
        return {"success": False, "error": f"更新投诉状态失败: {str(e)}"}


def _save_case_solution(cursor, conn, complaint_id, tenant_id):
    """保存投诉解决方案到案例库"""
    try:
        cursor.execute("""
            SELECT category, sub_category, description, resolution
            FROM bs_complaint_handling_complaints
            WHERE complaint_id = %s
        """, (complaint_id,))
        row = cursor.fetchone()
        if not row:
            return

        category, sub_category, description, resolution = row

        cursor.execute("""
            INSERT INTO bs_complaint_handling_case_solutions
            (tenant_id, user_id, complaint_id, category, sub_category, problem_summary, solution, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (complaint_id) DO UPDATE SET
                problem_summary = EXCLUDED.problem_summary,
                solution = EXCLUDED.solution,
                updated_at = EXCLUDED.updated_at
        """, (
            tenant_id, None, complaint_id, category, sub_category,
            description[:500], resolution,
            datetime.now(), datetime.now(),
        ))
    except Exception as e:
        logger.warning(f"保存案例解决方案失败（非致命）: {e}")


# ============================================================
# 匹配相似历史案例
# ============================================================

def match_cases(args):
    """匹配相似历史案例"""
    try:
        import asyncio
        from src.services.case_matching_service import case_matching_service

        filters = {}
        if getattr(args, "category", None):
            filters["category"] = args.category

        tenant_id = _get_current_tenant_id()
        if tenant_id:
            filters["tenant_id"] = tenant_id

        top_k = getattr(args, "top_k", 5)
        if isinstance(top_k, str):
            top_k = int(top_k)

        results = asyncio.run(case_matching_service.find_similar(
            query=args.description,
            domain="complaint",
            filters=filters,
            top_k=top_k,
        ))

        return {
            "success": True,
            "matches": [
                {
                    "complaint_id": m.complaint_id,
                    "similarity": m.similarity,
                    "category": m.category,
                    "description": m.description,
                    "resolution": m.resolution,
                    "customer_satisfied": m.customer_satisfied,
                    "resolution_time_hours": m.resolution_time_hours,
                }
                for m in results
            ],
            "match_count": len(results),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"案例匹配失败: {e}")
        return {"success": False, "error": f"案例匹配失败: {str(e)}"}


# ============================================================
# 升级投诉
# ============================================================

def escalate_complaint(args):
    """升级投诉"""
    try:
        import asyncio
        from src.services.notification_service import notification_service, NotificationMessage, NotificationChannel

        with get_db_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now()

            # 获取当前投诉信息
            cursor.execute("""
                SELECT urgency, tenant_id FROM bs_complaint_handling_complaints
                WHERE complaint_id = %s
            """, (args.complaint_id,))
            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"投诉记录不存在: {args.complaint_id}"}

            previous_urgency, tenant_id = row[0], row[1]
            new_urgency = getattr(args, "urgency", "urgent")
            escalate_to = getattr(args, "escalate_to", "投诉主管")

            escalation_level = 1
            if new_urgency == "critical":
                escalation_level = 2

            cursor.execute("""
                UPDATE bs_complaint_handling_complaints SET
                    status = 'escalated',
                    urgency = %s,
                    escalation_level = %s,
                    escalated_to = %s,
                    escalation_reason = %s,
                    escalated_at = %s,
                    updated_at = %s
                WHERE complaint_id = %s
            """, (
                new_urgency, escalation_level, escalate_to,
                args.reason, now, now, args.complaint_id,
            ))

            # 记录升级交互
            cursor.execute("""
                INSERT INTO bs_complaint_handling_interactions
                (tenant_id, user_id, complaint_id, interaction_type, sender_type, content, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                tenant_id, None, args.complaint_id,
                "escalation", "system",
                f"投诉已升级至 {escalate_to}，原因: {args.reason}",
                now,
            ))

            conn.commit()

        # 发送通知
        notification_sent = False
        notification_channel = ""
        try:
            from src.config.settings import settings
            notification_cfg = getattr(settings, "notification", None)
            if notification_cfg:
                default_channel = getattr(notification_cfg, "default_channel", "email")
            else:
                default_channel = "email"

            msg = NotificationMessage(
                title=f"投诉升级通知 - {args.complaint_id}",
                content=f"投诉 {args.complaint_id} 已升级处理。\n\n升级原因: {args.reason}\n紧急程度: {new_urgency}\n\n请尽快处理。",
                urgency=new_urgency,
                recipient=getattr(args, "escalate_to", ""),
                channel=NotificationChannel(default_channel),
                metadata={"complaint_id": args.complaint_id},
            )
            notification_sent = asyncio.run(notification_service.send(msg))
            notification_channel = default_channel
        except Exception as e:
            logger.warning(f"升级通知发送失败（非致命）: {e}")

        return {
            "success": True,
            "complaint_id": args.complaint_id,
            "escalated": True,
            "previous_urgency": previous_urgency,
            "new_urgency": new_urgency,
            "escalation_level": escalation_level,
            "escalated_to": escalate_to,
            "notification_sent": notification_sent,
            "notification_channel": notification_channel,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"升级投诉失败: {e}")
        return {"success": False, "error": f"升级投诉失败: {str(e)}"}


# ============================================================
# 查询投诉列表
# ============================================================

def list_complaints(args):
    """查询投诉列表"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            conditions = ["user_id = %s"]
            params = [args.user_id]

            tenant_id = _get_current_tenant_id()
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)

            if getattr(args, "status", None):
                conditions.append("status = %s")
                params.append(args.status)

            if getattr(args, "category", None):
                conditions.append("category = %s")
                params.append(args.category)

            if getattr(args, "urgency", None):
                conditions.append("urgency = %s")
                params.append(args.urgency)

            limit = getattr(args, "limit", 20)
            if isinstance(limit, str):
                limit = int(limit)

            where_clause = " AND ".join(conditions)
            params.append(limit)

            cursor.execute(f"""
                SELECT complaint_id, category, sub_category, urgency, status,
                       customer_emotion, description, resolution,
                       created_at, updated_at, resolved_at
                FROM bs_complaint_handling_complaints
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT %s
            """, params)

            rows = cursor.fetchall()
            complaints = []
            for row in rows:
                complaints.append({
                    "complaint_id": row[0],
                    "category": row[1],
                    "sub_category": row[2],
                    "urgency": row[3],
                    "status": row[4],
                    "customer_emotion": row[5],
                    "description": row[6][:100] + "..." if row[6] and len(row[6]) > 100 else row[6],
                    "resolution": row[7],
                    "created_at": row[8].isoformat() if row[8] else None,
                    "updated_at": row[9].isoformat() if row[9] else None,
                    "resolved_at": row[10].isoformat() if row[10] else None,
                })

        return {
            "success": True,
            "complaints": complaints,
            "total": len(complaints),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"查询投诉列表失败: {e}")
        return {"success": False, "error": f"查询投诉列表失败: {str(e)}"}


# ============================================================
# 查询单条投诉详情
# ============================================================

def get_complaint(args):
    """查询单条投诉详情"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT complaint_id, tenant_id, user_id, session_id, order_id,
                       customer_name, contact_info, category, sub_category, tags,
                       customer_emotion, emotion_intensity, urgency, status,
                       escalation_level, description, resolution,
                       escalated_to, escalation_reason, escalated_at,
                       created_at, updated_at, resolved_at, first_response_at
                FROM bs_complaint_handling_complaints
                WHERE complaint_id = %s
            """, (args.complaint_id,))

            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"投诉记录不存在: {args.complaint_id}"}

            tags = row[9]
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (json.JSONDecodeError, TypeError):
                    tags = []

        return {
            "success": True,
            "complaint_id": row[0],
            "tenant_id": row[1],
            "user_id": row[2],
            "session_id": row[3],
            "order_id": row[4],
            "customer_name": row[5],
            "contact_info": row[6],
            "category": row[7],
            "sub_category": row[8],
            "tags": tags,
            "customer_emotion": row[10],
            "emotion_intensity": row[11],
            "urgency": row[12],
            "status": row[13],
            "escalation_level": row[14],
            "description": row[15],
            "resolution": row[16],
            "escalated_to": row[17],
            "escalation_reason": row[18],
            "escalated_at": row[19].isoformat() if row[19] else None,
            "created_at": row[20].isoformat() if row[20] else None,
            "updated_at": row[21].isoformat() if row[21] else None,
            "resolved_at": row[22].isoformat() if row[22] else None,
            "first_response_at": row[23].isoformat() if row[23] else None,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"查询投诉详情失败: {e}")
        return {"success": False, "error": f"查询投诉详情失败: {str(e)}"}


# ============================================================
# 创建跟进任务
# ============================================================

def create_followup(args):
    """创建跟进任务"""
    try:
        tenant_id = _get_current_tenant_id()

        due_date = None
        if getattr(args, "due_date", None):
            try:
                due_date = datetime.fromisoformat(args.due_date)
            except ValueError:
                due_date = datetime.now() + timedelta(days=1)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now()

            cursor.execute("""
                INSERT INTO bs_complaint_handling_followups
                (tenant_id, user_id, complaint_id, action, assigned_to, due_date, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                tenant_id, None, args.complaint_id, args.action,
                getattr(args, "assigned_to", None),
                due_date, "pending", now,
            ))

            followup_id = cursor.fetchone()[0]

            # 记录交互
            content = f"创建跟进任务: {args.action}"
            if getattr(args, "due_date", None):
                content += f"，截止时间: {args.due_date}"
            if getattr(args, "assigned_to", None):
                content += f"，负责人: {args.assigned_to}"

            cursor.execute("""
                INSERT INTO bs_complaint_handling_interactions
                (tenant_id, user_id, complaint_id, interaction_type, sender_type, content, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (tenant_id, None, args.complaint_id, "note", "agent", content, now))

            conn.commit()

        return {
            "success": True,
            "followup_id": followup_id,
            "complaint_id": args.complaint_id,
            "action": args.action,
            "due_date": due_date.isoformat() if due_date else None,
            "status": "pending",
        }
    except Exception as e:
        logger.opt(exception=True).error(f"创建跟进任务失败: {e}")
        return {"success": False, "error": f"创建跟进任务失败: {str(e)}"}


# ============================================================
# 投诉统计
# ============================================================

def stats(args):
    """投诉统计"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            tenant_id = _get_current_tenant_id()
            period = getattr(args, "period", "30d")
            group_by = getattr(args, "group_by", "category")

            # 计算时间范围
            days = 30
            if period.endswith("d"):
                days = int(period[:-1])
            elif period.endswith("w"):
                days = int(period[:-1]) * 7
            elif period.endswith("m"):
                days = int(period[:-1]) * 30

            since = datetime.now() - timedelta(days=days)

            conditions = ["created_at >= %s"]
            params = [since]
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)

            where_clause = " AND ".join(conditions)

            # 总数统计
            cursor.execute(f"""
                SELECT COUNT(*),
                       COUNT(CASE WHEN status = 'open' THEN 1 END),
                       COUNT(CASE WHEN status = 'in_progress' THEN 1 END),
                       COUNT(CASE WHEN status = 'resolved' THEN 1 END),
                       COUNT(CASE WHEN status = 'escalated' THEN 1 END),
                       COUNT(CASE WHEN status = 'closed' THEN 1 END)
                FROM bs_complaint_handling_complaints
                WHERE {where_clause}
            """, params)
            total_row = cursor.fetchone()

            # 分组统计
            valid_group_by = {"category": "category", "urgency": "urgency", "status": "status"}.get(group_by, "category")
            cursor.execute(f"""
                SELECT {valid_group_by}, COUNT(*)
                FROM bs_complaint_handling_complaints
                WHERE {where_clause}
                GROUP BY {valid_group_by}
                ORDER BY COUNT(*) DESC
            """, params)
            group_rows = cursor.fetchall()

            group_stats = [{"name": row[0], "count": row[1]} for row in group_rows]

        return {
            "success": True,
            "period": period,
            "total": total_row[0],
            "open": total_row[1],
            "in_progress": total_row[2],
            "resolved": total_row[3],
            "escalated": total_row[4],
            "closed": total_row[5],
            "group_by": group_by,
            "group_stats": group_stats,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"投诉统计失败: {e}")
        return {"success": False, "error": f"投诉统计失败: {str(e)}"}


# ============================================================
# 记录交互记录
# ============================================================

def add_interaction(args):
    """记录交互记录"""
    try:
        tenant_id = _get_current_tenant_id()
        now = datetime.now()

        with get_db_connection() as conn:
            cursor = conn.cursor()

            interaction_type = getattr(args, "interaction_type", "message")
            cursor.execute("""
                INSERT INTO bs_complaint_handling_interactions
                (tenant_id, user_id, complaint_id, interaction_type, sender_type, sender_name, content, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                tenant_id, None, args.complaint_id, interaction_type,
                args.sender_type, None, args.content, now,
            ))

            interaction_id = cursor.fetchone()[0]
            conn.commit()

        return {
            "success": True,
            "interaction_id": interaction_id,
            "complaint_id": args.complaint_id,
            "interaction_type": interaction_type,
            "sender_type": args.sender_type,
            "created_at": now.isoformat(),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"记录交互失败: {e}")
        return {"success": False, "error": f"记录交互失败: {str(e)}"}


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="投诉处理核心工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # analyze-sentiment
    p = subparsers.add_parser("analyze-sentiment", help="分析客户情绪")
    p.add_argument("--text", required=True, help="待分析的文本")
    p.add_argument("--context", default="", help="上下文信息")

    # classify-complaint
    p = subparsers.add_parser("classify-complaint", help="投诉分类")
    p.add_argument("--description", required=True, help="投诉描述")
    p.add_argument("--context", default="", help="上下文信息")

    # create-complaint
    p = subparsers.add_parser("create-complaint", help="创建投诉记录")
    p.add_argument("--user-id", required=True, help="用户ID")
    p.add_argument("--description", required=True, help="投诉描述")
    p.add_argument("--category", required=True, help="一级分类")
    p.add_argument("--sub-category", default=None, help="二级分类")
    p.add_argument("--order-id", default=None, help="订单ID")
    p.add_argument("--urgency", default="normal", help="紧急程度")
    p.add_argument("--customer-emotion", default=None, help="客户情绪")
    p.add_argument("--contact-info", default=None, help="联系方式")
    p.add_argument("--tenant-id", default=None, help="租户ID")
    p.add_argument("--session-id", default=None, help="会话ID")

    # update-complaint
    p = subparsers.add_parser("update-complaint", help="更新投诉状态")
    p.add_argument("--complaint-id", required=True, help="投诉ID")
    p.add_argument("--status", required=True, help="新状态")
    p.add_argument("--resolution", default=None, help="处理结果")
    p.add_argument("--assigned-to", default=None, help="指派人")
    p.add_argument("--notes", default=None, help="备注")

    # match-cases
    p = subparsers.add_parser("match-cases", help="匹配相似历史案例")
    p.add_argument("--description", required=True, help="投诉描述")
    p.add_argument("--category", default=None, help="分类过滤")
    p.add_argument("--top-k", default=5, help="返回数量")

    # escalate-complaint
    p = subparsers.add_parser("escalate-complaint", help="升级投诉")
    p.add_argument("--complaint-id", required=True, help="投诉ID")
    p.add_argument("--reason", required=True, help="升级原因")
    p.add_argument("--escalate-to", default="投诉主管", help="升级给谁")
    p.add_argument("--urgency", default="urgent", help="新的紧急程度")

    # list-complaints
    p = subparsers.add_parser("list-complaints", help="查询投诉列表")
    p.add_argument("--user-id", required=True, help="用户ID")
    p.add_argument("--status", default=None, help="状态过滤")
    p.add_argument("--category", default=None, help="分类过滤")
    p.add_argument("--urgency", default=None, help="紧急程度过滤")
    p.add_argument("--limit", default=20, help="返回数量")

    # get-complaint
    p = subparsers.add_parser("get-complaint", help="查询单条投诉详情")
    p.add_argument("--complaint-id", required=True, help="投诉ID")

    # create-followup
    p = subparsers.add_parser("create-followup", help="创建跟进任务")
    p.add_argument("--complaint-id", required=True, help="投诉ID")
    p.add_argument("--action", required=True, help="跟进动作")
    p.add_argument("--due-date", default=None, help="截止时间")
    p.add_argument("--assigned-to", default=None, help="负责人")

    # stats
    p = subparsers.add_parser("stats", help="投诉统计")
    p.add_argument("--period", default="30d", help="统计周期 (如 7d, 30d, 1m)")
    p.add_argument("--group-by", default="category", help="分组维度 (category/urgency/status)")

    # add-interaction
    p = subparsers.add_parser("add-interaction", help="记录交互记录")
    p.add_argument("--complaint-id", required=True, help="投诉ID")
    p.add_argument("--content", required=True, help="交互内容")
    p.add_argument("--sender-type", required=True, help="发送者类型 (customer/agent/system/supervisor)")
    p.add_argument("--interaction-type", default="message", help="交互类型")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    init_tables()

    commands = {
        "analyze-sentiment": analyze_sentiment,
        "classify-complaint": classify_complaint,
        "create-complaint": create_complaint,
        "update-complaint": update_complaint,
        "match-cases": match_cases,
        "escalate-complaint": escalate_complaint,
        "list-complaints": list_complaints,
        "get-complaint": get_complaint,
        "create-followup": create_followup,
        "stats": stats,
        "add-interaction": add_interaction,
    }

    handler = commands.get(args.command)
    if handler:
        result = handler(args)
    else:
        result = {"success": False, "error": f"未知命令: {args.command}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
