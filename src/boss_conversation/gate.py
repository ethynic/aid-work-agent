"""BOSS 发送频控双闸门（B2，设计 §5.5.1–§5.5.3 冻结）。

职责切分（V1.9 冻结）：BindingGuard 只做锁/检查/落库（gate_transaction）；纯计算
判断在模块级 boss_send_eligibility_gate（send_eligibility_gate 契约，零写入），
由 guard 在锁内调用。时间口径一律 DB 侧 clock_timestamp()/NOW()（隔离库 DB 时钟
比宿主机快约 6s，禁止宿主机墙钟参与判定）。

窗口口径（§5.5.3 冻结，全部以 bs_boss_conversation_rate_slots.reserved_at 为准）：
- 计数行 = status IN ('reserved','settled')（released 不占）；
- 60s 间隔含 reserved 行：最近一条计数行 reserved_at + 60s 为最早可发时刻；
- 10min 窗 ≤3：窗内（reserved_at > now-600s）第 3 条计数行的 reserved_at + 600s
  为窗解除时刻；
- 日上限 ≤10：当日（Asia/Shanghai，按 reserved_at 本地日）reserved+settled ≥10
  → terminal(rate_limit)；
- 退避附加按 effective_count：≤1→+0；2→+2min；3→+5min；≥4→terminal
  human_required(repeated_rate_trigger)。
"""
from __future__ import annotations

import math
from datetime import timedelta, timezone as _tz
from typing import Any, Dict, Optional

from .bindings import normalize_rate_day
from .constants import (
    BUSINESS_TIMEZONE,
    RATE_BACKOFF_SECONDS_BY_COUNT,
    RATE_DAILY_CAP,
    RATE_INTERVAL_SECONDS,
    RATE_WINDOW_MAX,
    RATE_WINDOW_SECONDS,
)

_COUNTABLE_STATUSES = "('reserved', 'settled')"


def _countable_rows_sql() -> str:
    return f"""
        SELECT id, reserved_at, status
        FROM bs_boss_conversation_rate_slots
        WHERE tenant_id=%s AND binding_id=%s AND status IN {_COUNTABLE_STATUSES}
    """


def _count_windows(cursor, tenant_id: str, binding_id: str) -> Dict[str, Any]:
    """一次查询取回三窗口判定所需事实（guard 已持 binding 锁，同事务一致读）。"""
    cursor.execute(
        f"""
        SELECT
            clock_timestamp() AS now_ts,
            (clock_timestamp() AT TIME ZONE %s)::date AS today,
            (SELECT MAX(reserved_at) FROM bs_boss_conversation_rate_slots
              WHERE tenant_id=%s AND binding_id=%s AND status IN {_COUNTABLE_STATUSES}) AS last_reserved_at,
            (SELECT COUNT(*) FROM bs_boss_conversation_rate_slots
              WHERE tenant_id=%s AND binding_id=%s AND status IN {_COUNTABLE_STATUSES}
                AND reserved_at > clock_timestamp() - (%s * INTERVAL '1 second')) AS window_count,
            (SELECT reserved_at FROM bs_boss_conversation_rate_slots
              WHERE tenant_id=%s AND binding_id=%s AND status IN {_COUNTABLE_STATUSES}
                AND reserved_at > clock_timestamp() - (%s * INTERVAL '1 second')
              ORDER BY reserved_at DESC OFFSET %s LIMIT 1) AS window_third_reserved_at,
            (SELECT COUNT(*) FROM bs_boss_conversation_rate_slots
              WHERE tenant_id=%s AND binding_id=%s AND status IN {_COUNTABLE_STATUSES}
                AND (reserved_at AT TIME ZONE %s)::date = (clock_timestamp() AT TIME ZONE %s)::date) AS today_count
        """,
        (
            BUSINESS_TIMEZONE,
            tenant_id, str(binding_id),
            tenant_id, str(binding_id), RATE_WINDOW_SECONDS,
            tenant_id, str(binding_id), RATE_WINDOW_SECONDS, RATE_WINDOW_MAX - 1,
            tenant_id, str(binding_id), BUSINESS_TIMEZONE, BUSINESS_TIMEZONE,
        ),
    )
    return dict(cursor.fetchone())


def _to_aware_utc(value) -> Any:  # noqa: ANN001
    return value.astimezone(_tz.utc) if value.tzinfo else value.replace(tzinfo=_tz.utc)


def boss_send_eligibility_gate(cursor, task: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    """纯计算发送门禁（send_eligibility_gate 契约，V1.9 严格判别联合）。

    只读零写入（复用 guard 已锁定的 binding 行与同一 cursor/事务一致读）；返回
    {"eligible": True, "effective_count": int}
    | {"deferred": True, ...冻结字段...}
    | {"terminal": "human_required", "reason": 受控码}。
    effective_count 语义（§5.5.3 冻结公式）：当前 decision 已计数过 → stored；
    首次触发 → stored+1（跨日归一后 stored 以 0 计）。"""
    binding_id = str(task.get("conversation_binding_id") or "")
    if not binding_id:
        return {"terminal": "human_required", "reason": "binding_missing"}
    cursor.execute(
        """
        SELECT rate_trigger_date, rate_trigger_count, last_rate_decision_id
        FROM bs_boss_conversation_bindings WHERE tenant_id=%s AND id=%s
        """,
        (task["tenant_id"], binding_id),
    )
    binding = cursor.fetchone()
    if binding is None:
        return {"terminal": "human_required", "reason": "binding_missing"}
    windows = _count_windows(cursor, task["tenant_id"], binding_id)
    now_ts = windows["now_ts"]
    stored = int(binding["rate_trigger_count"] or 0)
    if binding["rate_trigger_date"] is None or str(binding["rate_trigger_date"]) != str(windows["today"]):
        stored = 0  # 跨日归一（guard 落库路径会先原子重置；此处同口径兜底）
    already_counted = str(binding["last_rate_decision_id"] or "") == str(decision["id"])
    effective_count = stored if already_counted else stored + 1
    if type(effective_count) is not int or effective_count < 1:  # 防御：畸形 stored
        return {"terminal": "human_required", "reason": "rate_ledger_anomaly"}

    def _deferred(until) -> Dict[str, Any]:  # noqa: ANN001
        until_utc = _to_aware_utc(until)
        now_utc = _to_aware_utc(now_ts)
        retry_ms = max(int(math.ceil((until_utc - now_utc).total_seconds() * 1000)), 1)
        return {
            "deferred": True,
            "effective_count": effective_count,
            "server_now": now_utc,
            "deferred_until": until_utc,
            "retry_after_ms": retry_ms,
            "deferred_reason": "rate_window",
            "response_revision": int(task.get("control_epoch") or 0) * 1000 + effective_count,
        }

    # 1) 日上限（Asia/Shanghai 当日 reserved+settled ≥10）→ terminal
    if int(windows["today_count"]) >= RATE_DAILY_CAP:
        return {"terminal": "human_required", "reason": "rate_limit"}
    # 2) 退避附加：≥4 次触发保守转人工（§5.5.3 冻结）
    backoff_extra = RATE_BACKOFF_SECONDS_BY_COUNT.get(effective_count)
    if backoff_extra is None:
        if effective_count >= 4:
            return {"terminal": "human_required", "reason": "repeated_rate_trigger"}
        backoff_extra = 0
    constraints = []
    # 3) 60s 间隔（含 reserved 行）：最近一条计数行 reserved_at + 60s。已计数
    #    （deferred 重试）不跳过——重试早于解除时刻时保持 deferred（软重试），
    #    而非放进 Phase B 被授权复判以 rate_window_race 硬升级。
    last_reserved = windows["last_reserved_at"]
    if last_reserved is not None:
        constraints.append(_to_aware_utc(last_reserved) + timedelta(seconds=RATE_INTERVAL_SECONDS))
    # 4) 10min 窗 ≤3：窗内第 3 条计数行 reserved_at + 600s
    if int(windows["window_count"]) >= RATE_WINDOW_MAX and windows["window_third_reserved_at"] is not None:
        constraints.append(
            _to_aware_utc(windows["window_third_reserved_at"]) + timedelta(seconds=RATE_WINDOW_SECONDS)
        )
    now_utc = _to_aware_utc(now_ts)
    if backoff_extra > 0:
        constraints.append(now_utc + timedelta(seconds=backoff_extra))
    future = [c for c in constraints if c > now_utc]
    if future:
        return _deferred(max(future))
    return {"eligible": True, "effective_count": effective_count}


class BossBindingGuard:
    """BOSS binding 同步门禁（BindingGuard 契约；设计 §5.5.2/§5.5.5）。

    锁序约束：调用方已持 subject/task（或 invocation/delivery）锁后调用——本类
    只锁场景 binding 行，绝不反向获取 subject/task。"""

    def lock_binding(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        cursor.execute(
            "SELECT * FROM bs_boss_conversation_bindings WHERE tenant_id=%s AND id=%s FOR UPDATE",
            (tenant_id, str(binding_id)),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def check_blocked(self, binding_row: Optional[Dict[str, Any]]) -> Optional[str]:  # noqa: ANN001
        """已锁定行上的同步阻断检查（幂等重 prepare 只锁不计数路径）：
        blocked 返回受控码（V1.9 terminal reason 契约），未阻断/缺行 None。"""
        if binding_row is not None and binding_row.get("automation_blocked"):
            return "automation_blocked"
        return None

    def gate_transaction(self, cursor, task: Dict[str, Any], decision: Dict[str, Any],
                         gate) -> Dict[str, Any]:  # noqa: ANN001
        """prepare-send Phase A 门禁（同一事务/同一 cursor；V1.9 冻结职责切分）：
        锁 binding → 检查 automation_blocked（blocked → terminal，不调 gate）→
        跨日归一（原子重置 count + 清 last_rate_decision_id）→ 调用注入的纯计算
        gate → eligible/deferred 落库 effective_count（last_rate_decision_id 去重）→
        原样返回 gate 的 V1.9 判别联合之一。terminal/畸形词汇原样上交由通用层处理。"""
        binding_id = str(task.get("conversation_binding_id") or "")
        row = self.lock_binding(cursor, task["tenant_id"], binding_id) if binding_id else None
        if row is None:
            return {"terminal": "human_required", "reason": "binding_missing"}
        blocked = self.check_blocked(row)
        if blocked is not None:
            return {"terminal": "human_required", "reason": blocked}
        # 跨日归一（原子；P2-B：重置计数同时清 last_rate_decision_id）
        normalize_rate_day(cursor, task["tenant_id"], binding_id, row)
        # 纯计算 send_eligibility_gate（只读，零写入）
        outcome = gate(cursor, task, decision)
        name = (
            "eligible" if isinstance(outcome, dict) and outcome.get("eligible") is True else
            "deferred" if isinstance(outcome, dict) and outcome.get("deferred") is True else
            "terminal" if isinstance(outcome, dict) and "terminal" in outcome else None
        )
        if name not in ("eligible", "deferred"):
            # terminal 或畸形词汇原样上交（落库职责只覆盖 eligible/deferred；畸形
            # 由通用层 V1.9 严格判别校验 fail-closed）
            return outcome
        effective_count = outcome.get("effective_count")
        if type(effective_count) is not int or effective_count < 1:
            return outcome  # 畸形 eligible/deferred 原样上交，通用层 fail-closed
        cursor.execute(
            """
            UPDATE bs_boss_conversation_bindings
            SET rate_trigger_count=%s, last_rate_decision_id=%s, updated_at=NOW()
            WHERE tenant_id=%s AND id=%s
            """,
            (effective_count, str(decision["id"]), task["tenant_id"], binding_id),
        )
        return outcome

    def block_binding(self, cursor, task: Dict[str, Any], reason: str) -> int:  # noqa: ANN001
        """置 automation_blocked=true 且 block_epoch+1（permits 拒绝副作用与
        operation_result 异常升级复用）；返回新 block epoch。禁止任何自动清除。"""
        cursor.execute(
            """
            UPDATE bs_boss_conversation_bindings
            SET automation_blocked=TRUE, automation_block_reason=%s,
                automation_block_epoch=automation_block_epoch+1, automation_blocked_at=clock_timestamp(),
                updated_at=NOW()
            WHERE tenant_id=%s AND id=%s
            RETURNING automation_block_epoch
            """,
            (reason, task["tenant_id"], str(task["conversation_binding_id"])),
        )
        row = cursor.fetchone()
        if row is None:
            from src.session_tasks.constants import SessionTaskError

            raise SessionTaskError("场景绑定不存在，无法置同步阻断", "CONFLICT", 409)
        return int(row["automation_block_epoch"])
