"""desktop_automation 审计事件（底座层：操作/许可/效果/接纳审计）

只存 operation/许可/效果/调度审计与受控引用/摘要，不写场景正文、群名
（AGENTS 安全原则与【计划 §2】：通用表不存场景正文）。
user_id：HTTP 上下文缺失的后台写入允许 NULL（database_dev.md 例外）。
"""

from typing import Any, Dict, Optional

from loguru import logger
from psycopg2.extras import Json

from src.db.database import get_db_connection


def insert_audit(
    cursor,
    tenant_id: str,
    kind: str,
    aggregate_type: str,
    aggregate_ref: str,
    *,
    user_id: Optional[str] = None,
    scenario_key: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """在既有事务游标上追加审计行（与业务变更同事务提交，不单独开连接）"""
    cursor.execute(
        """
        INSERT INTO desktop_automation_audit_events
            (tenant_id, user_id, scenario_key, kind, aggregate_type, aggregate_ref, detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (tenant_id, user_id, scenario_key, kind, aggregate_type, aggregate_ref, Json(detail or {})),
    )


def log_audit(
    tenant_id: str,
    kind: str,
    aggregate_type: str,
    aggregate_ref: str,
    *,
    user_id: Optional[str] = None,
    scenario_key: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """独立短事务写审计（无业务变更伴随时使用；失败仅告警不影响调用方）"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            insert_audit(
                cursor, tenant_id, kind, aggregate_type, aggregate_ref,
                user_id=user_id, scenario_key=scenario_key, detail=detail,
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001 审计失败不阻断主流程
        logger.opt(exception=True).warning(
            f"后端日志：desktop_automation 审计写入失败 tenant={tenant_id} kind={kind}: {e}"
        )


def list_audits(
    tenant_id: str,
    aggregate_type: Optional[str] = None,
    aggregate_ref: Optional[str] = None,
    limit: int = 100,
) -> list:
    """按租户查询审计（对账/测试用；必带 tenant_id）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        sql = """
            SELECT id, tenant_id, user_id, scenario_key, kind, aggregate_type, aggregate_ref,
                   detail, created_at
            FROM desktop_automation_audit_events
            WHERE tenant_id = %s
        """
        params: list = [tenant_id]
        if aggregate_type:
            sql += " AND aggregate_type = %s"
            params.append(aggregate_type)
        if aggregate_ref:
            sql += " AND aggregate_ref = %s"
            params.append(aggregate_ref)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(limit)
        cursor.execute(sql, tuple(params))
        return [dict(r) for r in cursor.fetchall()]
