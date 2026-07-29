"""
数据聚合器

从 chat_records 表按 tenant_id + user_id + 时间范围聚合数据，作为日报/周报/月报的输入。
"""

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from src.db.database import get_db_connection
from src.saas.models.enums import ChatRecordSourceType


def parse_date_range(report_date: date, report_type: str) -> Tuple[datetime, datetime]:
    """根据 report_date 和 report_type 计算聚合时间范围

    - daily: 当日 00:00:00 ~ 次日 00:00:00
    - weekly: 上周一 00:00:00 ~ 本周一 00:00:00（report_date 应为周一）
    - monthly: 上月 1 日 00:00:00 ~ 本月 1 日 00:00:00（report_date 应为月首）
    """
    if report_type == "daily":
        start = datetime.combine(report_date, datetime.min.time())
        end = start + timedelta(days=1)
    elif report_type == "weekly":
        # report_date 视为所在周的周一
        start = datetime.combine(report_date, datetime.min.time())
        end = start + timedelta(days=7)
    elif report_type == "monthly":
        # report_date 视为月首
        start = datetime.combine(report_date, datetime.min.time())
        # 下个月首
        if report_date.month == 12:
            next_month = report_date.replace(year=report_date.year + 1, month=1, day=1)
        else:
            next_month = report_date.replace(month=report_date.month + 1, day=1)
        end = datetime.combine(next_month, datetime.min.time())
    else:
        raise ValueError(f"不支持的 report_type: {report_type}")
    return start, end


def aggregate_personal(
    tenant_id: str,
    user_id: str,
    report_date: date,
    report_type: str = "daily",
) -> Dict[str, Any]:
    """聚合个人报告数据

    返回结构：
    {
        "records": [...],                # chat_records 列表（已过滤 report_*）
        "dialog_count": N,
        "credit_cost": N,
        "subagent_distribution": {subagent_id: count},
        "tool_distribution": {tool_name: count},
        "source_distribution": {source_type: count},
        "saved_minutes": float,
    }
    """
    start, end = parse_date_range(report_date, report_type)
    report_sources = set(ChatRecordSourceType.report_values())

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 排除 report_* 来源的记录，避免报告类 LLM 调用污染业务统计
        cursor.execute(
            """
            SELECT * FROM chat_records
            WHERE tenant_id = %s AND user_id = %s
              AND created_at >= %s AND created_at < %s
              AND source_type NOT IN %s
              AND status = 'completed'
            ORDER BY created_at ASC
            """,
            (tenant_id, user_id, start, end, tuple(report_sources)),
        )
        rows = cursor.fetchall()

    records = [_row_to_record(r) for r in rows if r]

    # 聚合
    subagent_dist: Dict[str, int] = {}
    tool_dist: Dict[str, int] = {}
    source_dist: Dict[str, int] = {}
    total_credit = 0

    for rec in records:
        # subagent 分布（subagent_calls 是 JSON 数组字符串）
        subagent_calls = rec.get("subagent_calls")
        if isinstance(subagent_calls, str):
            try:
                subagent_calls = json.loads(subagent_calls)
            except (json.JSONDecodeError, TypeError):
                subagent_calls = []
        if isinstance(subagent_calls, list):
            for sa in subagent_calls:
                if isinstance(sa, dict):
                    sa_id = sa.get("subagent_id") or sa.get("name") or "unknown"
                else:
                    sa_id = str(sa)
                subagent_dist[sa_id] = subagent_dist.get(sa_id, 0) + 1

        # 工具分布
        execution_details = rec.get("execution_details")
        if isinstance(execution_details, str):
            try:
                execution_details = json.loads(execution_details)
            except (json.JSONDecodeError, TypeError):
                execution_details = {}
        if isinstance(execution_details, dict):
            for t in execution_details.get("tool_executions") or []:
                if isinstance(t, dict):
                    tool_name = t.get("tool_name", "unknown")
                    tool_dist[tool_name] = tool_dist.get(tool_name, 0) + 1

        # 来源分布
        src = rec.get("source_type") or "chat"
        source_dist[src] = source_dist.get(src, 0) + 1

        # 积分累计
        total_credit += float(rec.get("credit_cost") or 0)

    # 节省时间估算（延迟导入避免循环依赖）
    from src.reports.time_saver import estimate_saved_minutes
    saved_minutes = estimate_saved_minutes(records)

    return {
        "records": records,
        "dialog_count": len(records),
        "credit_cost": total_credit,
        "subagent_distribution": subagent_dist,
        "tool_distribution": tool_dist,
        "source_distribution": source_dist,
        "saved_minutes": saved_minutes,
        "time_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
    }


def aggregate_team(
    tenant_id: str,
    report_date: date,
    report_type: str = "daily",
) -> Dict[str, Any]:
    """聚合团队报告数据（按租户全员聚合）

    返回结构：
    {
        "user_stats": [
            {"user_id": ..., "dialog_count": N, "credit_cost": N, "saved_minutes": float},
            ...
        ],
        "total_dialog_count": N,
        "total_credit_cost": N,
        "total_saved_minutes": float,
        "active_user_count": N,
        "subagent_distribution": {...},
        "source_distribution": {...},
        # 采样后的成员对话（用于 LLM 输入，受 80K 字符预算限制）
        "members_dialogs": [
            {"user_id": str, "dialog_count": int, "user_messages": List[str]},
            ...
        ],
        "input_truncated": bool,         # 是否触发 80K 字符预算截断
        "input_member_count": int,       # 进入 LLM 输入的成员数
        "input_dialog_count": int,       # 进入 LLM 输入的对话条数
        "input_char_count": int,         # 实际输入字符数
    }

    注意：user_stats / total_dialog_count / source_distribution 等统计字段基于全量数据，
    不受 80K 字符预算采样影响。仅 members_dialogs 受采样影响。
    """
    start, end = parse_date_range(report_date, report_type)
    report_sources = set(ChatRecordSourceType.report_values())

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 按用户聚合（统计用，全量数据）
        # LEFT JOIN users 带出 username/phone/nickname，避免 N+1 查询；
        # users 表无 FK 约束（项目规范），JOIN 在应用层执行。
        cursor.execute(
            """
            SELECT cr.user_id,
                   COUNT(*) AS dialog_count,
                   COALESCE(SUM(cr.credit_cost), 0) AS credit_cost,
                   SUM(cr.duration_ms) AS total_duration_ms,
                   u.username,
                   u.phone,
                   u.nickname
            FROM chat_records cr
            LEFT JOIN users u ON u.user_id = cr.user_id
            WHERE cr.tenant_id = %s
              AND cr.created_at >= %s AND cr.created_at < %s
              AND cr.source_type NOT IN %s
              AND cr.status = 'completed'
              AND cr.user_id IS NOT NULL
            GROUP BY cr.user_id, u.username, u.phone, u.nickname
            ORDER BY dialog_count DESC
            """,
            (tenant_id, start, end, tuple(report_sources)),
        )
        user_rows = cursor.fetchall() or []

        # 总量
        cursor.execute(
            """
            SELECT COUNT(*) AS dialog_count,
                   COALESCE(SUM(credit_cost), 0) AS credit_cost
            FROM chat_records
            WHERE tenant_id = %s
              AND created_at >= %s AND created_at < %s
              AND source_type NOT IN %s
              AND status = 'completed'
            """,
            (tenant_id, start, end, tuple(report_sources)),
        )
        total_row = cursor.fetchone() or {}

        # 来源分布
        cursor.execute(
            """
            SELECT source_type, COUNT(*) AS cnt
            FROM chat_records
            WHERE tenant_id = %s
              AND created_at >= %s AND created_at < %s
              AND source_type NOT IN %s
              AND status = 'completed'
            GROUP BY source_type
            ORDER BY cnt DESC
            """,
            (tenant_id, start, end, tuple(report_sources)),
        )
        source_rows = cursor.fetchall() or []

        # 拉取所有活跃成员的对话明细（用于 LLM 输入采样）
        # 按 user_id 分组在 Python 层处理，按 created_at 倒序取最近 50 条
        cursor.execute(
            """
            SELECT user_id, user_message, created_at
            FROM chat_records
            WHERE tenant_id = %s
              AND created_at >= %s AND created_at < %s
              AND source_type NOT IN %s
              AND status = 'completed'
              AND user_id IS NOT NULL
              AND user_message IS NOT NULL
              AND user_message != ''
            ORDER BY user_id, created_at DESC
            """,
            (tenant_id, start, end, tuple(report_sources)),
        )
        dialog_rows = cursor.fetchall() or []

    # 构造用户统计列表（基于全量统计，不受采样影响）
    user_stats: List[Dict[str, Any]] = []
    user_dialog_count_map: Dict[str, int] = {}
    for row in user_rows:
        uid = row["user_id"]
        dc = int(row["dialog_count"] or 0)
        user_dialog_count_map[uid] = dc
        user_stats.append({
            "user_id": uid,
            "username": row.get("username") or "",
            "phone": row.get("phone") or "",
            "nickname": row.get("nickname") or "",
            "dialog_count": dc,
            "credit_cost": float(row["credit_cost"] or 0),
            # 节省时间按用户级聚合估算（粗略：用 total_duration_ms 反推）
            # 准确值需要拉取明细，这里给保守估算
            "saved_minutes": _rough_saved_minutes(dc),
        })

    # 按 user_id 分组对话明细（dialog_rows 已按 user_id, created_at DESC 排序）
    user_dialogs_map: Dict[str, List[Dict[str, Any]]] = {}
    for row in dialog_rows:
        uid = row["user_id"]
        user_dialogs_map.setdefault(uid, []).append({
            "user_message": row["user_message"] or "",
            "created_at": row["created_at"],
        })

    # 按 dialog_count 倒序排序成员（活跃成员优先进入采样）
    sorted_users = sorted(
        user_dialog_count_map.items(),
        key=lambda kv: kv[1],
        reverse=True,
    )

    # 80K 字符预算采样
    MAX_CHAR_BUDGET = 80000
    MAX_DIALOGS_PER_MEMBER = 50
    MAX_USER_MESSAGE_CHARS = 200

    members_dialogs: List[Dict[str, Any]] = []
    total_chars = 0
    total_dialogs_sampled = 0
    truncated = False

    for uid, dc in sorted_users:
        # 累计已达预算，停止追加新成员
        if total_chars >= MAX_CHAR_BUDGET:
            truncated = True
            break

        dialogs = user_dialogs_map.get(uid, [])
        if not dialogs:
            continue

        # 取最近 MAX_DIALOGS_PER_MEMBER 条，每条 user_message 截断到 200 字
        recent_dialogs = dialogs[:MAX_DIALOGS_PER_MEMBER]
        truncated_messages: List[str] = []
        member_chars = 0
        for d in recent_dialogs:
            msg = (d.get("user_message") or "").strip()[:MAX_USER_MESSAGE_CHARS]
            if not msg:
                continue
            truncated_messages.append(msg)
            # 每条消息字符数 + 序号和换行开销（保守估算每条多 5 字符）
            member_chars += len(msg) + 5

        if not truncated_messages:
            continue

        # 检查加入该成员是否超出预算
        # 若加入后会超出，仍尝试加入部分对话（按条截断到预算内）
        if total_chars + member_chars > MAX_CHAR_BUDGET:
            # 逐条追加，到预算即停
            partial_messages: List[str] = []
            partial_chars = 0
            for msg in truncated_messages:
                cost = len(msg) + 5
                if total_chars + partial_chars + cost > MAX_CHAR_BUDGET:
                    break
                partial_messages.append(msg)
                partial_chars += cost

            if partial_messages:
                members_dialogs.append({
                    "user_id": uid,
                    "dialog_count": dc,
                    "user_messages": partial_messages,
                })
                total_chars += partial_chars
                total_dialogs_sampled += len(partial_messages)
            truncated = True
            # 继续遍历后续成员，但因为他们都会触发 total_chars >= budget 的检查而 break
            # 实际上后续会直接 break，这里继续是为了语义清晰
            continue

        # 完整加入该成员
        members_dialogs.append({
            "user_id": uid,
            "dialog_count": dc,
            "user_messages": truncated_messages,
        })
        total_chars += member_chars
        total_dialogs_sampled += len(truncated_messages)

    total_dialog = int(total_row.get("dialog_count") or 0)
    total_credit = float(total_row.get("credit_cost") or 0)
    total_saved = sum(u["saved_minutes"] for u in user_stats)

    source_dist = {r["source_type"]: int(r["cnt"]) for r in source_rows}

    logger.info(
        f"团队聚合采样: tenant={tenant_id}, type={report_type}, "
        f"active_users={len(user_stats)}, sampled_members={len(members_dialogs)}, "
        f"sampled_dialogs={total_dialogs_sampled}, chars={total_chars}, truncated={truncated}"
    )

    return {
        "user_stats": user_stats,
        "total_dialog_count": total_dialog,
        "total_credit_cost": total_credit,
        "total_saved_minutes": round(total_saved, 1),
        "active_user_count": len(user_stats),
        "source_distribution": source_dist,
        "members_dialogs": members_dialogs,
        "input_truncated": truncated,
        "input_member_count": len(members_dialogs),
        "input_dialog_count": total_dialogs_sampled,
        "input_char_count": total_chars,
        "time_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
    }


def _rough_saved_minutes(dialog_count: int) -> float:
    """粗略估算节省时间（无明细时用，按每条 3 分钟保守估算）"""
    return round(dialog_count * 3.0, 1)


def _row_to_record(row: Dict[str, Any]) -> Dict[str, Any]:
    """RealDictRow 转 dict，处理 datetime"""
    result = dict(row)
    if result.get("created_at") and hasattr(result["created_at"], "isoformat"):
        result["created_at"] = result["created_at"].isoformat()
    return result
