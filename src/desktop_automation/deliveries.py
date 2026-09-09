"""desktop_automation deliveries（这一次执行的第几条内容）

UNIQUE(tenant_id, run_id, position)；hash 用于一致性，不作为跨天去重键；
不存 block_id 或群字段（场景正文只在 payload_ref 引用背后）。
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.desktop_automation.constants import (
    DELIVERY_STATE_DISPATCHED,
    DELIVERY_STATE_PENDING,
    DELIVERY_STATE_SKIPPED,
    DELIVERY_TERMINAL_STATES,
)

_DELIVERY_COLUMNS = """
    id, tenant_id, run_id, scenario_key, task_ref, revision_ref, user_id, position,
    operation, provider_key, target_ref, target_handle, target_version,
    payload_ref, payload_hash, state, effect, phase, created_at, updated_at, finished_at
"""


def insert_deliveries(cursor, run: Dict[str, Any], compiled: List[Dict[str, Any]]) -> List[str]:
    """按场景编译结果初始化 deliveries（事务内；run 维度全量插入，幂等由调用方保证）"""
    ids: List[str] = []
    for op in compiled:
        cursor.execute(
            """
            INSERT INTO desktop_automation_deliveries
                (tenant_id, run_id, scenario_key, task_ref, revision_ref, user_id, position,
                 operation, provider_key, target_ref, target_handle, target_version,
                 payload_ref, payload_hash, state)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
            ON CONFLICT (tenant_id, run_id, position) DO NOTHING
            RETURNING id
            """,
            (
                run["tenant_id"], str(run["id"]), run.get("scenario_key"), run.get("task_ref"),
                run.get("revision_ref"), run.get("user_id"), op["position"],
                op["operation"], op.get("provider_key"), op.get("target_ref"),
                op.get("target_handle"), op.get("target_version"),
                op.get("payload_ref"), op.get("payload_hash"),
            ),
        )
        row = cursor.fetchone()
        if row:
            ids.append(str(row["id"]))
    return ids


def get_delivery(delivery_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_DELIVERY_COLUMNS} FROM desktop_automation_deliveries "
            "WHERE id = %s AND tenant_id = %s",
            (delivery_id, tenant_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_run_deliveries(run_id: str, tenant_id: str) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_DELIVERY_COLUMNS} FROM desktop_automation_deliveries "
            "WHERE run_id = %s AND tenant_id = %s ORDER BY position",
            (run_id, tenant_id),
        )
        return [dict(r) for r in cursor.fetchall()]


def lock_delivery(cursor, delivery_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """锁定 delivery 行（许可事务等使用；租约/许可与推进都以行锁串行化）"""
    cursor.execute(
        f"SELECT {_DELIVERY_COLUMNS} FROM desktop_automation_deliveries "
        "WHERE id = %s AND tenant_id = %s FOR UPDATE",
        (delivery_id, tenant_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def mark_dispatched(cursor, delivery_id: str, tenant_id: str) -> None:
    """enqueue attempt 后置 dispatched（等待设备执行）"""
    cursor.execute(
        """
        UPDATE desktop_automation_deliveries
        SET state = 'dispatched', updated_at = NOW()
        WHERE id = %s AND tenant_id = %s AND state = 'pending'
        """,
        (delivery_id, tenant_id),
    )


def mark_may_have_started(cursor, delivery_id: str, tenant_id: str) -> None:
    """许可发放后置 may_have_started（旧租约过期也不重新分配该条，§5.2）"""
    cursor.execute(
        """
        UPDATE desktop_automation_deliveries
        SET phase = 'may_have_started', updated_at = NOW()
        WHERE id = %s AND tenant_id = %s
        """,
        (delivery_id, tenant_id),
    )


def apply_operation_outcome(
    cursor,
    delivery_id: str,
    tenant_id: str,
    *,
    state: Optional[str] = None,
    effect: Optional[str] = None,
    phase: Optional[str] = None,
) -> None:
    """operation-result 回调侧推进 delivery（终态幂等：终态行不再改判）"""
    sets = ["updated_at = NOW()"]
    params: List[Any] = []
    if state is not None:
        sets.append("state = %s")
        params.append(state)
    if effect is not None:
        sets.append("effect = %s")
        params.append(effect)
    if phase is not None:
        sets.append("phase = %s")
        params.append(phase)
    if state in DELIVERY_TERMINAL_STATES:
        sets.append("finished_at = NOW()")
    params.extend([delivery_id, tenant_id])
    cursor.execute(
        f"""
        UPDATE desktop_automation_deliveries
        SET {", ".join(sets)}
        WHERE id = %s AND tenant_id = %s AND state NOT IN %s
        """,
        (*params, DELIVERY_TERMINAL_STATES),
    )


def skip_remaining_deliveries(cursor, run_id: str, tenant_id: str, reason: str) -> int:
    """前序 unknown/取消后停止后续条目（置 skipped；已有进度的条目不动）"""
    cursor.execute(
        """
        UPDATE desktop_automation_deliveries
        SET state = 'skipped', finished_at = NOW(), updated_at = NOW()
        WHERE run_id = %s AND tenant_id = %s AND state = 'pending'
        """,
        (run_id, tenant_id),
    )
    count = cursor.rowcount
    if count:
        logger.info(
            f"后端日志：desktop_automation run {run_id} 停止后续 {count} 条 delivery（{reason}）"
        )
    return count


def expire_unstarted_deliveries(cursor, tenant_id: str, run_id: str) -> int:
    """截止清扫（§5.4「部分已发送后截止→partial，剩余条目标 expired」）：

    - pending / phase 仍为 prepared（或 NULL）的 dispatched 行 → expired（未提交）；
    - dispatched 且 phase ∈ may_have_started/unknown 的行 → unknown（已越过提交边界，
      无法证明未提交，绝不能当未提交的 expired 处理；§5.3）。
    """
    cursor.execute(
        """
        UPDATE desktop_automation_deliveries
        SET state = 'expired', finished_at = NOW(), updated_at = NOW()
        WHERE run_id = %s AND tenant_id = %s
          AND (state = 'pending'
               OR (state = 'dispatched' AND (phase IS NULL OR phase = 'prepared')))
        """,
        (run_id, tenant_id),
    )
    expired = cursor.rowcount
    cursor.execute(
        """
        UPDATE desktop_automation_deliveries
        SET state = 'unknown', effect = 'unknown', phase = 'unknown',
            finished_at = NOW(), updated_at = NOW()
        WHERE run_id = %s AND tenant_id = %s AND state = 'dispatched'
          AND phase IN ('may_have_started', 'unknown')
        """,
        (run_id, tenant_id),
    )
    return expired


def next_executable_delivery(run_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """下一条可执行 delivery：全部前序 applied+verified 才能开始（§4 顺序规则）"""
    rows = list_run_deliveries(run_id, tenant_id)
    for row in rows:
        if row["state"] == DELIVERY_STATE_PENDING:
            return row
        if row["state"] == DELIVERY_STATE_DISPATCHED:
            return None  # 有在途条目，等待结果
        if row["state"] in (DELIVERY_STATE_SKIPPED,):
            continue
        # 已终态：applied+verified 才放行下一条
        if row["effect"] == "applied" and row["phase"] == "verified":
            continue
        return None  # failed/unknown/expired：停后续
    return None
