"""BOSS 沟通日志投影（B2，设计 §5.6）。

发送 verified（或 unknown 人工判定后，B4 接入）入队 bs_boss_comm_log_projection_queue；
后台 job 幂等 upsert 到 bs_recruiting_operator_resume_comm_logs（binding.resume_id
关联，**不按姓名匹配**；source_delivery_id 唯一判重）；失败退避重试仅补投影，
不触发旧回写、禁止双写。队列只存错误码，不落敏感详情。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

PROCESSING_LEASE_SECONDS = 60  # processing 行租约：到期才可被其他 tick 重认领（非阻断 a）
MAX_RETRIES = 10
RETRY_BACKOFF_BASE_SECONDS = 10
RETRY_BACKOFF_MAX_SECONDS = 300


def enqueue_projection(
    cursor,
    tenant_id: str,
    *,
    delivery_id: str,
    binding_id: str,
    resume_id: Optional[int],
    user_id: Optional[str] = None,
) -> bool:
    """幂等入队（UNIQUE(tenant_id, delivery_id)；已在队列/已投影 → False）。

    在调用方事务内执行（结算事务同事务入队：回执提交即入队，无双写窗口）。"""
    cursor.execute(
        """
        INSERT INTO bs_boss_comm_log_projection_queue
            (tenant_id, user_id, delivery_id, binding_id, resume_id, status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
        ON CONFLICT (tenant_id, delivery_id) DO NOTHING
        RETURNING id
        """,
        (tenant_id, user_id, str(delivery_id), str(binding_id), resume_id),
    )
    return cursor.fetchone() is not None


def run_projection_tick(*, limit: int = 50) -> Dict[str, Any]:
    """投影 job tick（scheduler 挂载；boss_conversation.enabled 门控）：
    认领 pending/租约过期的 processing → 逐条独立事务投影 → done/retry/failed。"""
    stats: Dict[str, Any] = {"claimed": 0, "done": 0, "retried": 0, "failed": 0, "skipped": 0}
    rows = _claim_rows(limit)
    stats["claimed"] = len(rows)
    for row in rows:
        try:
            outcome = _project_one(row)
        except Exception as exc:  # noqa: BLE001 单条隔离
            logger.warning(f"BOSS 投影处理异常 tenant={row['tenant_id']} queue={row['id']}: {exc!r}")
            outcome = _mark_retry(row, "projection_error")
        stats[outcome] = stats.get(outcome, 0) + 1
    return stats


def _claim_rows(limit: int) -> list:
    """认领（非阻断 a，六审）：pending 或**租约过期**的 processing 行——
    PROCESSING_LEASE_SECONDS 内被其他 worker 持有的 processing 行不再被立即重复
    认领（原实现 updated_at 空置导致 processing 行任何 tick 都可被重复 CLAIM，
    双 worker 双写投影）。updated_at 即租约令牌：认领置 NOW()，完成/退避更新以
    `WHERE ... AND updated_at=%s` CAS 提交，丢失租约（被超时重认领）的一方
    rowcount=0 自动弃权，不覆盖新 owner 的结果。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bs_boss_comm_log_projection_queue
            SET status='processing', updated_at=NOW()
            WHERE id IN (
                SELECT id FROM bs_boss_comm_log_projection_queue
                WHERE (status='pending' AND (next_retry_at IS NULL OR next_retry_at <= NOW()))
                   OR (status='processing'
                       AND updated_at <= NOW() - (%s * INTERVAL '1 second'))
                ORDER BY created_at, id
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, tenant_id, user_id, delivery_id, binding_id, resume_id,
                      retry_count, updated_at
            """,
            (PROCESSING_LEASE_SECONDS, limit),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.commit()
    return rows


def _project_one(row: Dict[str, Any]) -> str:
    """单条投影事务：正文解析 → 幂等 upsert comm_logs（binding.resume_id 关联）。

    完成更新带租约 CAS（updated_at=认领时刻令牌）；租约丢失（处理超时被重认领）
    → skipped，不覆盖新 owner 结果。"""
    from src.db.database import get_db_connection
    from src.session_tasks.texts import load_text

    tenant_id = row["tenant_id"]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT invocation_id FROM desktop_automation_attempts
            WHERE tenant_id=%s AND delivery_id=%s
            ORDER BY attempt_no DESC LIMIT 1
            """,
            (tenant_id, str(row["delivery_id"])),
        )
        attempt = cursor.fetchone()
        invocation_id = str(attempt["invocation_id"]) if attempt and attempt["invocation_id"] else None
        decision_id = None
        if invocation_id:
            cursor.execute(
                "SELECT business_ref FROM local_tool_invocations WHERE tenant_id=%s AND id=%s",
                (tenant_id, invocation_id),
            )
            inv = cursor.fetchone()
            decision_id = str(((inv["business_ref"] or {}).get("decision_id")) or "") if inv else ""
        content: Optional[str] = None
        task_id = None
        if decision_id:
            cursor.execute(
                "SELECT task_id, reply_text_id FROM session_task_decisions WHERE tenant_id=%s AND id=%s",
                (tenant_id, decision_id),
            )
            decision = cursor.fetchone()
            if decision is not None and decision["reply_text_id"]:
                task_id = decision["task_id"]
                try:
                    payload = load_text(conn, tenant_id, decision["task_id"],
                                        decision["reply_text_id"], expected_purpose="decision")
                    text = payload.get("text") if isinstance(payload, dict) else None
                    if isinstance(text, str) and text.strip():
                        content = text
                except Exception:  # noqa: BLE001 解密失败按缺内容退避重试
                    content = None
        if not content:
            conn.rollback()
            return _mark_retry(row, "reply_text_unresolvable")
        # resume_id 以队列为准，缺失时回读绑定（绑定可后补简历关联）
        resume_id = row.get("resume_id")
        if resume_id is None:
            cursor.execute(
                "SELECT resume_id FROM bs_boss_conversation_bindings WHERE tenant_id=%s AND id=%s",
                (tenant_id, str(row["binding_id"])),
            )
            binding = cursor.fetchone()
            resume_id = binding["resume_id"] if binding else None
        if resume_id is None:
            conn.rollback()
            return _mark_retry(row, "resume_id_missing")
        cursor.execute(
            """
            INSERT INTO bs_recruiting_operator_resume_comm_logs
                (tenant_id, resume_id, direction, channel, content, user_id,
                 source_delivery_id, source_message_id, created_at)
            VALUES (%s, %s, 'out', 'boss', %s, %s, %s, %s, NOW())
            ON CONFLICT (tenant_id, source_delivery_id) DO NOTHING
            """,
            (tenant_id, int(resume_id), content, row.get("user_id"),
             str(row["delivery_id"]), decision_id),
        )
        projected = cursor.rowcount == 1
        cursor.execute(
            """
            UPDATE bs_boss_comm_log_projection_queue
            SET status='done', updated_at=NOW()
            WHERE id=%s AND status='processing' AND updated_at=%s
            """,
            (row["id"], row.get("updated_at")),
        )
        if cursor.rowcount != 1:
            # 租约丢失（处理超时被另一 worker 重认领）：放弃本次结果，不覆盖新 owner
            conn.rollback()
            return "skipped"
        conn.commit()
    if not projected:
        # 幂等重放（已投影过）：队列置 done 即可，不重复写日志
        return "skipped"
    return "done"


def _mark_retry(row: Dict[str, Any], error_code: str) -> str:
    """暂时失败：退避重试（仅补投影，不影响发送链路）；达上限 failed。
    更新带租约 CAS（updated_at=认领时刻令牌）：丢失租约返回 skipped。"""
    from src.db.database import get_db_connection

    retry_count = int(row.get("retry_count") or 0) + 1
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if retry_count >= MAX_RETRIES:
            cursor.execute(
                """
                UPDATE bs_boss_comm_log_projection_queue
                SET status='failed', retry_count=%s, last_error_code=%s, updated_at=NOW()
                WHERE id=%s AND status='processing' AND updated_at=%s
                """,
                (retry_count, error_code, row["id"], row.get("updated_at")),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return "skipped"
            conn.commit()
            logger.error(
                f"后端日志：BOSS 投影达重试上限转 failed queue={row['id']} delivery={row['delivery_id']} "
                f"error_code={error_code}"
            )
            return "failed"
        backoff = min(RETRY_BACKOFF_MAX_SECONDS, RETRY_BACKOFF_BASE_SECONDS * (2 ** retry_count))
        cursor.execute(
            """
            UPDATE bs_boss_comm_log_projection_queue
            SET status='pending', retry_count=%s, last_error_code=%s,
                next_retry_at=NOW() + (%s * INTERVAL '1 second'), updated_at=NOW()
            WHERE id=%s AND status='processing' AND updated_at=%s
            """,
            (retry_count, error_code, backoff, row["id"], row.get("updated_at")),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return "skipped"
        conn.commit()
    return "retried"
