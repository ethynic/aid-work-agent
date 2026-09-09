"""desktop_automation runs（执行账本：短事务 lease/fence + §5.4 聚合）

- worker 领取 run 只占短事务，记录 lease/fence（fence_token 每次领取 +1，
  后续推进带 fence 校验，旧租约写入被拒——不能在 API request 或长 DB 事务里等整个 RPA 过程）。
- §5.4 聚合（R10）：全部 applied+verified→succeeded；任一 unknown 且已有成功→partial、
  否则 unknown（unknown 优先于 success）；全部未提交且取消→cancelled；
  部分已发送后截止→partial+剩余 expired；纯等待超时→expired。
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.desktop_automation import audit
from src.desktop_automation.constants import (
    DELIVERY_STATE_DISPATCHED,
    DELIVERY_STATE_FAILED,
    DELIVERY_STATE_SUCCEEDED,
    DELIVERY_STATE_UNKNOWN,
    DELIVERY_TERMINAL_STATES,
    EFFECT_APPLIED,
    EFFECT_UNKNOWN,
    PHASE_MAY_HAVE_STARTED,
    PHASE_UNKNOWN,
    PHASE_VERIFIED,
    RUN_TERMINAL_STATES,
)

_RUN_COLUMNS = """
    id, tenant_id, occurrence_id, scenario_key, task_ref, revision_ref, user_id,
    state, device_id, lease_expires_at, fence_token, authorization_epoch,
    due_at, expires_at, result_json, claimed_at, started_at, finished_at, created_at
"""


# ==================== §5.4 聚合（纯函数，表驱动可测） ====================


def delivery_is_success(delivery: Dict[str, Any]) -> bool:
    """applied 还必须有本次验证证据（phase=verified）才成功（R10/设计 §4）"""
    return delivery.get("effect") == EFFECT_APPLIED and delivery.get("phase") == PHASE_VERIFIED


def delivery_is_unknown(delivery: Dict[str, Any]) -> bool:
    return (
        delivery.get("effect") == EFFECT_UNKNOWN
        or delivery.get("phase") == PHASE_UNKNOWN
        or delivery.get("state") == DELIVERY_STATE_UNKNOWN
    )


def delivery_is_started(delivery: Dict[str, Any]) -> bool:
    """「已发送」= 已越过 prepared（may_have_started/verified/unknown，或已派发）"""
    phase = delivery.get("phase")
    if phase in (PHASE_MAY_HAVE_STARTED, PHASE_UNKNOWN, PHASE_VERIFIED):
        return True
    return delivery.get("state") in (
        DELIVERY_STATE_DISPATCHED, DELIVERY_STATE_SUCCEEDED,
        DELIVERY_STATE_FAILED, DELIVERY_STATE_UNKNOWN,
    )


def compute_run_terminal_state(
    deliveries: List[Dict[str, Any]],
    *,
    cancelled: bool = False,
    deadline_exceeded: bool = False,
) -> Optional[str]:
    """按 §5.4 计算终态；未到终态返回 None。

    分支优先级（R10）：
    1. 任一 unknown：已有成功→partial，否则 unknown（unknown 优先于 success）；
    2. 全部 applied+verified → succeeded；
    3. 全部未提交且取消 → cancelled；
    4. 截止：部分已发送→partial（剩余条目由调用方置 expired）；纯等待→expired；
    5. 无 unknown 且全部终态：有成功有失败→partial（本轮部分副作用）；全失败→failed。
    """
    if not deliveries:
        return None
    has_unknown = any(delivery_is_unknown(d) for d in deliveries)
    has_success = any(delivery_is_success(d) for d in deliveries)
    all_terminal = all(d.get("state") in DELIVERY_TERMINAL_STATES for d in deliveries)
    any_started = any(delivery_is_started(d) for d in deliveries)

    if has_unknown:
        return "partial" if has_success else "unknown"
    if all_terminal and all(delivery_is_success(d) for d in deliveries):
        return "succeeded"
    if cancelled and not any_started:
        return "cancelled"
    if deadline_exceeded:
        return "partial" if any_started else "expired"
    if all_terminal:
        return "partial" if has_success else "failed"
    return None


# ==================== 行访问 ====================


def create_run(
    cursor,
    tenant_id: str,
    occurrence_id: str,
    scenario_key: str,
    task_ref: str,
    revision_ref: str,
    user_id: str,
    *,
    due_at: datetime,
    expires_at: Optional[datetime],
    authorization_epoch: Optional[int],
) -> str:
    """在接纳事务内创建 run（state=pending；UNIQUE(tenant_id, occurrence_id) 幂等）"""
    cursor.execute(
        """
        INSERT INTO desktop_automation_runs
            (tenant_id, occurrence_id, scenario_key, task_ref, revision_ref, user_id,
             state, due_at, expires_at, authorization_epoch, fence_token)
        VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, 0)
        ON CONFLICT (tenant_id, occurrence_id) DO NOTHING
        RETURNING id
        """,
        (tenant_id, occurrence_id, scenario_key, task_ref, revision_ref, user_id,
         due_at, expires_at, authorization_epoch),
    )
    row = cursor.fetchone()
    return str(row["id"]) if row else ""


def get_run(run_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_RUN_COLUMNS} FROM desktop_automation_runs WHERE id = %s AND tenant_id = %s",
            (run_id, tenant_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def claim_run(
    lease_seconds: int = 120,
    limit: int = 10,
    *,
    tenant_id: Optional[str] = None,
    scenario_keys: Optional[List[str]] = None,
    expected_run_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """worker 领取到期 pending run（短事务 lease/fence，SKIP LOCKED 防重复领取）。

    tenant_id / scenario_keys 可选过滤：调用方限定处理范围（如仅领取本进程已注册适配器
    的场景），避免领取后因适配器缺失而卡死；不传为全局领取。
    expected_run_id（R50 定向领取）：仅尝试领取该 run——非目标/已被领/未到期/非 pending
    一律返回空（FOR UPDATE SKIP LOCKED，确定性领取，outbox 驱动按条目目标定向）。
    """
    filters = ""
    params: List[Any] = []
    if expected_run_id is not None:
        filters += " AND id = %s"
        params.append(expected_run_id)
    if tenant_id is not None:
        filters += " AND tenant_id = %s"
        params.append(tenant_id)
    if scenario_keys is not None:
        filters += " AND scenario_key::text = ANY(%s)"
        params.append(list(scenario_keys))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id FROM desktop_automation_runs
            WHERE state = 'pending' AND due_at <= NOW(){filters}
            ORDER BY due_at
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (*params, limit),
        )
        ids = [str(r["id"]) for r in cursor.fetchall()]
        if not ids:
            conn.commit()
            return []
        cursor.execute(
            """
            UPDATE desktop_automation_runs
            SET state = 'running',
                lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                fence_token = fence_token + 1,
                claimed_at = NOW()
            WHERE id::text = ANY(%s) AND state = 'pending'
            RETURNING id, tenant_id, occurrence_id, scenario_key, task_ref, revision_ref,
                      user_id, state, fence_token, due_at, expires_at, authorization_epoch
            """,
            (lease_seconds, ids),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.commit()
        return rows


def renew_lease(run_id: str, tenant_id: str, fence_token: int, lease_seconds: int) -> bool:
    """续租（fence 校验：旧租约/被重新领取后拒绝写入）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE desktop_automation_runs
            SET lease_expires_at = NOW() + (%s * INTERVAL '1 second')
            WHERE id = %s AND tenant_id = %s AND fence_token = %s AND state = 'running'
            """,
            (lease_seconds, run_id, tenant_id, fence_token),
        )
        ok = cursor.rowcount > 0
        conn.commit()
        return ok


def finish_run(
    cursor,
    run_id: str,
    tenant_id: str,
    state: str,
    detail: Optional[dict] = None,
    *,
    fence_token: Optional[int] = None,
) -> bool:
    """落终态（在聚合事务内；终态幂等——已终态不再改判）。

    fence_token 提供时入 WHERE（P1-4）：旧租约持有者的推进写入被拒（fence 每次领取 +1），
    与模块 docstring 的 lease/fence 承诺一致。
    """
    sql = """
        UPDATE desktop_automation_runs
        SET state = %s,
            result_json = COALESCE(%s, result_json),
            finished_at = NOW(),
            lease_expires_at = NULL
        WHERE id = %s AND tenant_id = %s AND state NOT IN %s
    """
    params: List[Any] = [state, _json(detail), run_id, tenant_id, RUN_TERMINAL_STATES]
    if fence_token is not None:
        sql += " AND fence_token = %s"
        params.append(fence_token)
    cursor.execute(sql, tuple(params))
    return cursor.rowcount > 0


def expire_overdue_runs(now: Optional[datetime] = None) -> int:
    """截止清扫：超过 expires_at 的未终态 run 按 §5.4 落终态
    （部分已发送→partial+剩余 delivery 置 expired；纯等待→expired；未开始条目置 expired）

    P2-1（登记不改，2026-09-09 评审）：本函数 deliveries 更新先于 run 终态写
    （理论锁序倒置，与 R49 各取消路径 run→deliveries 序相反）；当前无生产调用方
    （微信域截止收敛走 weixin dispatch.runs_reclaim_tick 的同序实现）。
    未来接线生产调度前须先改为 run 行 FOR UPDATE 先行。
    """
    from src.desktop_automation import deliveries as deliveries_module

    now = now or datetime.now(timezone.utc)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tenant_id, user_id, scenario_key, task_ref FROM desktop_automation_runs
            WHERE state IN ('pending', 'running')
              AND expires_at IS NOT NULL AND expires_at < %s
            ORDER BY created_at
            LIMIT 200
            """,
            (now,),
        )
        overdue = [dict(r) for r in cursor.fetchall()]
        finished = 0
        for run in overdue:
            # 未提交条目一律 expired（「部分已发送后截止→剩余条目标 expired」）
            deliveries_module.expire_unstarted_deliveries(
                cursor, run["tenant_id"], str(run["id"])
            )
            rows = deliveries_module.list_run_deliveries(str(run["id"]), run["tenant_id"])
            state = compute_run_terminal_state(rows, deadline_exceeded=True)
            if state and finish_run(cursor, str(run["id"]), run["tenant_id"], state):
                audit.insert_audit(
                    cursor, run["tenant_id"], "run_deadline_exceeded", "run", str(run["id"]),
                    user_id=run.get("user_id"), scenario_key=run.get("scenario_key"),
                    detail={"state": state},
                )
                finished += 1
        conn.commit()
        if finished:
            logger.warning(
                f"后端日志：desktop_automation 截止清扫 {finished} 个 run 落终态（部分已发送→partial+剩余 expired）"
            )
        return finished


def _json(detail: Optional[dict]):
    if detail is None:
        return None
    from psycopg2.extras import Json

    return Json(detail)
