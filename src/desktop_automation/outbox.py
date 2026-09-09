"""desktop_automation outbox（可靠投递记账，dedupe_key 确定性幂等）

UNIQUE(tenant_id, kind, dedupe_key) + INSERT ... ON CONFLICT DO NOTHING：
重跑不重复接纳。投递器本体（重试/lease 推进）由后续 background 工作包接线，
本模块只提供库函数。
"""

from typing import Optional

from loguru import logger

from src.db.database import get_db_connection
from src.desktop_automation.constants import OUTBOX_STATE_PENDING


def enqueue_outbox(
    cursor,
    tenant_id: str,
    kind: str,
    aggregate_ref: str,
    dedupe_key: str,
    *,
    user_id: Optional[str] = None,
    payload_ref: Optional[str] = None,
) -> Optional[str]:
    """在既有事务游标上入队（ON CONFLICT DO NOTHING 幂等）。返回 id；已存在返回 None。

    dedupe_key 必须确定性（如 run:{occurrence_id}），保证唯一键去重语义。
    只存受控引用（payload_ref），不存场景正文。
    """
    cursor.execute(
        """
        INSERT INTO desktop_automation_outbox
            (tenant_id, user_id, kind, aggregate_ref, dedupe_key, state, payload_ref)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, kind, dedupe_key) DO NOTHING
        RETURNING id
        """,
        (
            tenant_id, user_id, kind, aggregate_ref, dedupe_key,
            OUTBOX_STATE_PENDING, payload_ref,
        ),
    )
    row = cursor.fetchone()
    return str(row["id"]) if row else None


def run_due_dedupe_key(occurrence_id: str) -> str:
    """occurrence → run 执行投递的确定性 dedupe_key（【计划 §3.1】）"""
    return f"run:{occurrence_id}"


def claim_pending_outbox(limit: int = 100, lease_seconds: int = 60) -> list:
    """领取待投递条目（FOR UPDATE SKIP LOCKED 防重复领取），置 processing + lease"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id FROM desktop_automation_outbox
            WHERE state = 'pending' AND available_at <= NOW()
            ORDER BY created_at
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (limit,),
        )
        ids = [str(r["id"]) for r in cursor.fetchall()]
        if not ids:
            conn.commit()
            return []
        cursor.execute(
            """
            UPDATE desktop_automation_outbox
            SET state = 'processing',
                lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                attempt_count = attempt_count + 1,
                updated_at = NOW()
            WHERE id::text = ANY(%s)
            RETURNING id, tenant_id, kind, aggregate_ref, dedupe_key, attempt_count
            """,
            (lease_seconds, ids),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.commit()
        return rows


def mark_outbox_done(outbox_id: str, tenant_id: str) -> bool:
    """投递完成 → done（幂等：非 pending/processing 也接受，终态收敛）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE desktop_automation_outbox
            SET state = 'done', lease_expires_at = NULL, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s
            """,
            (outbox_id, tenant_id),
        )
        ok = cursor.rowcount > 0
        conn.commit()
        return ok


def requeue_stale_processing(lease_grace_seconds: int = 300) -> int:
    """lease 过期的 processing → pending 重投（清扫路径，幂等）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE desktop_automation_outbox
            SET state = 'pending', lease_expires_at = NULL, updated_at = NOW()
            WHERE state = 'processing'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < NOW() - (%s * INTERVAL '1 second')
            """,
            (lease_grace_seconds,),
        )
        count = cursor.rowcount
        conn.commit()
        if count:
            logger.info(f"后端日志：desktop_automation outbox 重投 {count} 条（lease 过期）")
        return count
