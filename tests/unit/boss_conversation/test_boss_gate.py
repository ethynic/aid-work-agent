"""BOSS 双闸门定向测试（设计 §5.5.2/§5.5.3 必测清单）。

- effective_count 加一时点与 last_rate_decision_id 去重（同 decision 不重复加一）；
- 跨日原子重置（含 last_rate_decision_id 清理，P2-B 教训）；
- reserved_at 窗口口径：60s 间隔含 reserved 行 / 10min 窗第 3 条最早 reserved_at+
  600s / 日上限（Asia/Shanghai）；
- 同步阻断穿透（blocked → terminal，不调 gate）；畸形 gate 返回原样上交；
- superseded 后旧 decision 不再触发（配合 prepare-send 链路在 e2e 内覆盖）；
- block_binding/check_blocked 契约。

时间口径：一律 DB 侧 clock_timestamp()（隔离库 DB 时钟比宿主机快约 6s，禁宿主机
墙钟细粒度比较）。
"""

import uuid

import pytest

from src.boss_conversation.gate import BossBindingGuard, boss_send_eligibility_gate

from tests.unit.boss_conversation.conftest import create_test_binding

GUARD = BossBindingGuard()


def _decision(decision_id=None):
    decision_id = decision_id or str(uuid.uuid4())
    return {"id": decision_id, "task_id": str(uuid.uuid4()), "status": "ready", "action": "reply"}


def _task(tenant_id, binding_id):
    return {
        "id": str(uuid.uuid4()), "tenant_id": tenant_id,
        "conversation_binding_id": str(binding_id), "control_epoch": 0,
    }


def _binding_row(tenant_id, binding_id, conn):
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM bs_boss_conversation_bindings WHERE tenant_id=%s AND id=%s",
        (tenant_id, str(binding_id)),
    )
    return dict(cursor.fetchone())


def _seed_slot(tenant_id, binding_id, *, age_seconds=None, status="settled", decision_id=None, delivery_id=None):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_boss_conversation_rate_slots
                (tenant_id, binding_id, decision_id, delivery_id, status, reserved_at, settled_at)
            VALUES (%s, %s, %s, %s, %s,
                    COALESCE(%s, clock_timestamp()) - COALESCE(%s, 0) * INTERVAL '1 second',
                    CASE WHEN %s = 'settled' THEN clock_timestamp() ELSE NULL END)
            ON CONFLICT (tenant_id, delivery_id) DO NOTHING
            """,
            (tenant_id, str(binding_id), decision_id or str(uuid.uuid4()),
             delivery_id or str(uuid.uuid4()), status, None, age_seconds, status),
        )
        conn.commit()


def _set_binding(tenant_id, binding_id, **sets):
    from src.db.database import get_db_connection

    clause = ", ".join(f"{k}=%s" for k in sets)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE bs_boss_conversation_bindings SET {clause} WHERE tenant_id=%s AND id=%s",
            (*sets.values(), tenant_id, str(binding_id)),
        )
        conn.commit()


def _run_gate_transaction(tenant_id, binding_id, decision=None, gate=None):
    """单事务调用 guard.gate_transaction（真实连接；模拟通用层 Phase A 形态）。"""
    from src.db.database import get_db_connection

    decision = decision or _decision()
    task = _task(tenant_id, binding_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        outcome = GUARD.gate_transaction(cursor, task, decision, gate or boss_send_eligibility_gate)
        conn.commit()
    return outcome, decision


class TestEffectiveCountAndDedup:
    def test_first_trigger_eligible_count_one_and_persisted(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="甲", job_id="j1",
        )
        decision = _decision()
        outcome, decision = _run_gate_transaction(tenant_id, binding["conversation_binding_id"], decision)
        assert outcome == {"eligible": True, "effective_count": 1}
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            row = _binding_row(tenant_id, binding["conversation_binding_id"], conn)
        assert int(row["rate_trigger_count"]) == 1
        assert str(row["last_rate_decision_id"]) == decision["id"]

    def test_same_decision_does_not_double_count(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="乙", job_id="j1",
        )
        decision = _decision()
        _run_gate_transaction(tenant_id, binding["conversation_binding_id"], decision)
        # 同 decision 重复触发（deferred 重试形态）：effective 停留在 1，不重复加一
        outcome, _ = _run_gate_transaction(tenant_id, binding["conversation_binding_id"], decision)
        assert outcome["effective_count"] == 1
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            row = _binding_row(tenant_id, binding["conversation_binding_id"], conn)
        assert int(row["rate_trigger_count"]) == 1

    def test_effective_count_grows_per_new_decision(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="丙", job_id="j1",
        )
        o1, _ = _run_gate_transaction(tenant_id, binding["conversation_binding_id"], _decision())
        o2, _ = _run_gate_transaction(tenant_id, binding["conversation_binding_id"], _decision())
        assert (o1["effective_count"], o2["effective_count"]) == (1, 2)


class TestCrossDayReset:
    def test_cross_day_reset_clears_count_and_last_decision(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="丁", job_id="j1",
        )
        old_decision = str(uuid.uuid4())
        # 昨日形态：count=9 已顶满、last_rate_decision_id 指向昨日 decision（P2-B 教训）
        _set_binding(
            tenant_id, binding["conversation_binding_id"],
            rate_trigger_count=9, last_rate_decision_id=old_decision,
        )
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bs_boss_conversation_bindings SET rate_trigger_date = "
                "(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date - 1 "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, str(binding["conversation_binding_id"])),
            )
            conn.commit()
        outcome, decision = _run_gate_transaction(tenant_id, binding["conversation_binding_id"])
        assert outcome == {"eligible": True, "effective_count": 1}
        with get_db_connection() as conn:
            row = _binding_row(tenant_id, binding["conversation_binding_id"], conn)
        # 原子重置后计数以本次触发为 1，且 last_rate_decision_id 已切换（旧 id 清理）
        assert int(row["rate_trigger_count"]) == 1
        assert str(row["last_rate_decision_id"]) == decision["id"]
        assert str(row["last_rate_decision_id"]) != old_decision

    def test_cross_day_reset_ignores_stale_old_decision(self, tenant_id, device_row):
        """旧 decision（昨日已计数）在新一天重复触发：归一后视为首次，不得因
        last_rate_decision_id 残留被误判已计数。"""
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="戊", job_id="j1",
        )
        stale_decision = _decision()
        _set_binding(
            tenant_id, binding["conversation_binding_id"],
            rate_trigger_count=3, last_rate_decision_id=stale_decision["id"],
        )
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bs_boss_conversation_bindings SET rate_trigger_date = "
                "(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date - 1 "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, str(binding["conversation_binding_id"])),
            )
            conn.commit()
        outcome, _ = _run_gate_transaction(tenant_id, binding["conversation_binding_id"], stale_decision)
        assert outcome == {"eligible": True, "effective_count": 1}


class TestReservedAtWindows:
    def test_interval_60s_includes_reserved_row(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="己", job_id="j1",
        )
        _seed_slot(tenant_id, binding["conversation_binding_id"], age_seconds=30)
        outcome, decision = _run_gate_transaction(tenant_id, binding["conversation_binding_id"])
        assert outcome["deferred"] is True
        assert outcome["effective_count"] == 1
        assert outcome["retry_after_ms"] > 0
        # deferred 也落库计数（§5.5.2 冻结：eligible/deferred 都落）
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            row = _binding_row(tenant_id, binding["conversation_binding_id"], conn)
        assert int(row["rate_trigger_count"]) == 1
        # 窗口解除（把 reserved 行回拨 120s）→ 同 decision 重试 eligible（已计数不重复加一）
        from src.db.database import get_db_connection as g

        with g() as conn:
            conn.cursor().execute(
                "UPDATE bs_boss_conversation_rate_slots SET reserved_at = reserved_at - INTERVAL '120 seconds' "
                "WHERE tenant_id=%s AND binding_id=%s",
                (tenant_id, str(binding["conversation_binding_id"])),
            )
            conn.commit()
        outcome2, _ = _run_gate_transaction(tenant_id, binding["conversation_binding_id"], decision)
        assert outcome2 == {"eligible": True, "effective_count": 1}  # 已计数不重复加一

    def test_10min_window_third_row_release_time(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="庚", job_id="j1",
        )
        bid = str(binding["conversation_binding_id"])
        # 三条计数行：100s/50s/10s 前 → 窗满（≥3）；解除时刻 = 第 3 条（最早）+600s
        _seed_slot(tenant_id, bid, age_seconds=100)
        _seed_slot(tenant_id, bid, age_seconds=50)
        _seed_slot(tenant_id, bid, age_seconds=10)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT MIN(reserved_at) AS third FROM (
                    SELECT reserved_at FROM bs_boss_conversation_rate_slots
                    WHERE tenant_id=%s AND binding_id=%s AND status IN ('reserved','settled')
                    ORDER BY reserved_at DESC LIMIT 3
                ) t
                """,
                (tenant_id, bid),
            )
            third = cursor.fetchone()["third"]
        from datetime import timedelta, timezone as _tz

        _third_aware = third if third.tzinfo else third.replace(tzinfo=_tz.utc)
        outcome, _ = _run_gate_transaction(tenant_id, bid)
        assert outcome["deferred"] is True
        until = outcome["deferred_until"]
        now_db = outcome["server_now"]
        gap_ms = (until - now_db).total_seconds() * 1000
        assert abs(gap_ms - outcome["retry_after_ms"]) <= 2000
        # 解除时刻 = 窗内第 3 条（最早）计数行 reserved_at + 600s（DB 时钟同源，容差 2s）
        delta = abs((until - _third_aware).total_seconds())
        assert abs(delta - 600) <= 2

    def test_daily_cap_terminal_rate_limit(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="辛", job_id="j1",
        )
        bid = str(binding["conversation_binding_id"])
        for _ in range(10):
            _seed_slot(tenant_id, bid, age_seconds=3600)  # 当日（回拨 1h 内）
        outcome, _ = _run_gate_transaction(tenant_id, bid)
        assert outcome == {"terminal": "human_required", "reason": "rate_limit"}

    def test_daily_cap_counts_by_shanghai_day(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="壬", job_id="j1",
        )
        bid = str(binding["conversation_binding_id"])
        for _ in range(10):
            _seed_slot(tenant_id, bid, age_seconds=48 * 3600)  # 前日 → 不计当日
        outcome, _ = _run_gate_transaction(tenant_id, bid)
        assert outcome["eligible"] is True

    def test_repeated_trigger_backoff_then_terminal(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="癸", job_id="j1",
        )
        bid = str(binding["conversation_binding_id"])
        # stored=3 → 本次 effective=4 → terminal(repeated_rate_trigger)
        _set_binding(tenant_id, bid, rate_trigger_count=3)
        from src.db.database import get_db_connection as g

        with g() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bs_boss_conversation_bindings SET rate_trigger_date = "
                "(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date WHERE tenant_id=%s AND id=%s",
                (tenant_id, bid),
            )
            conn.commit()
        outcome, _ = _run_gate_transaction(tenant_id, bid)
        assert outcome == {"terminal": "human_required", "reason": "repeated_rate_trigger"}

    def test_second_trigger_backoff_2min(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="子", job_id="j1",
        )
        bid = str(binding["conversation_binding_id"])
        _set_binding(tenant_id, bid, rate_trigger_count=1)
        from src.db.database import get_db_connection as g

        with g() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bs_boss_conversation_bindings SET rate_trigger_date = "
                "(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date WHERE tenant_id=%s AND id=%s",
                (tenant_id, bid),
            )
            conn.commit()
        outcome, _ = _run_gate_transaction(tenant_id, bid)
        assert outcome["deferred"] is True
        gap_s = outcome["retry_after_ms"] / 1000
        assert 100 <= gap_s <= 140  # ≈2min 退避（±2000ms 容差内为 120s）


class TestBlockedAndMalformed:
    def test_blocked_binding_terminal_without_gate_call(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="丑", job_id="j1",
        )
        bid = str(binding["conversation_binding_id"])
        _set_binding(tenant_id, bid, automation_blocked=True, automation_block_reason="rate_ledger_anomaly")

        called = []

        def _spy_gate(cursor, task, decision):
            called.append(1)
            return {"eligible": True, "effective_count": 1}

        outcome, _ = _run_gate_transaction(tenant_id, bid, gate=_spy_gate)
        assert outcome == {"terminal": "human_required", "reason": "automation_blocked"}
        assert not called  # blocked → 不调 gate

    def test_malformed_gate_outcome_passes_through_unpersisted(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="寅", job_id="j1",
        )
        outcome, _ = _run_gate_transaction(
            tenant_id, binding["conversation_binding_id"],
            gate=lambda cursor, task, decision: {"eligible": True, "effective_count": True},  # bool 非法
        )
        # 畸形词汇原样上交（通用层 fail-closed），不落计数
        assert outcome == {"eligible": True, "effective_count": True}
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            row = _binding_row(tenant_id, binding["conversation_binding_id"], conn)
        assert int(row["rate_trigger_count"]) == 0

    def test_missing_binding_terminal(self, tenant_id):
        outcome, _ = _run_gate_transaction(tenant_id, str(uuid.uuid4()))
        assert outcome == {"terminal": "human_required", "reason": "binding_missing"}

    def test_block_binding_increments_epoch(self, tenant_id, device_row):
        binding = create_test_binding(
            tenant_id, "user-1", device_id=str(device_row["id"]),
            account_scope_id=str(uuid.uuid4()), candidate_name="卯", job_id="j1",
        )
        bid = str(binding["conversation_binding_id"])
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            task = _task(tenant_id, bid)
            epoch1 = GUARD.block_binding(conn.cursor(), task, "rate_window_race")
            epoch2 = GUARD.block_binding(conn.cursor(), task, "rate_ledger_anomaly")
            conn.commit()
        assert (epoch1, epoch2) == (1, 2)
        with get_db_connection() as conn:
            row = _binding_row(tenant_id, bid, conn)
        assert row["automation_blocked"] is True
        assert row["automation_block_reason"] == "rate_ledger_anomaly"

    def test_check_blocked_contract(self, tenant_id, device_row):
        assert GUARD.check_blocked(None) is None
        assert GUARD.check_blocked({"automation_blocked": False}) is None
        assert GUARD.check_blocked({"automation_blocked": True}) == "automation_blocked"
