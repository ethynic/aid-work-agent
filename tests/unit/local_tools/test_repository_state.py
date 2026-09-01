"""本地工具 repository 状态机单元测试（mock get_db_connection，无真实 DB）

验证：
- write_result 终态幂等（已终态重复写返回原状态，不报错、不再 UPDATE）
- mark_started 非法状态不迁移（返回当前状态供 API 层判 409）
- claim token 不匹配一律拒绝
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.local_tools import repository

pytestmark = pytest.mark.unit

INVOCATION_ID = "11111111-1111-1111-1111-111111111111"
TENANT = "tenant_test"
HASH = "h" * 64


def _make_conn(fetchone_rows):
    """构造 mock 连接：cursor.fetchone 按序返回 fetchone_rows"""
    cursor = SimpleNamespace(
        execute=lambda *a, **k: None,
        fetchall=lambda: [],
        rowcount=1,
    )
    rows = iter(fetchone_rows)
    cursor.fetchone = lambda: next(rows, None)
    conn = SimpleNamespace(cursor=lambda *a, **k: cursor, commit=lambda: None, rollback=lambda: None)
    return conn


def _patch_conn(fetchone_rows):
    conn = _make_conn(fetchone_rows)

    @contextmanager
    def fake_get_db_connection():
        yield conn

    return patch("src.local_tools.repository.get_db_connection", fake_get_db_connection)


class TestWriteResult:
    def test_terminal_state_idempotent(self):
        """已终态（succeeded）重复写 result：幂等返回原状态，不执行 UPDATE"""
        terminal_row = {"id": INVOCATION_ID, "state": "succeeded", "effect": "applied",
                        "claim_token_hash": HASH}
        conn = _make_conn([terminal_row])
        executed = []
        cursor = conn.cursor()
        orig_execute = cursor.execute
        cursor.execute = lambda *a, **k: executed.append(a[0]) or orig_execute(*a, **k)

        @contextmanager
        def fake():
            yield conn

        with patch("src.local_tools.repository.get_db_connection", fake):
            row = repository.write_result(INVOCATION_ID, TENANT, HASH, True, effect="applied")

        assert row["state"] == "succeeded"
        # 只有一次 SELECT，没有 UPDATE 语句（幂等短路；SELECT 自身的 FOR UPDATE 行锁不算）
        assert len(executed) == 1
        assert not any(sql.lstrip().startswith("UPDATE") for sql in executed)

    def test_wrong_claim_token_rejected(self):
        """claim token 不匹配：返回 None，不迁移状态"""
        row = {"id": INVOCATION_ID, "state": "running", "claim_token_hash": "x" * 64}
        with _patch_conn([row]):
            assert repository.write_result(INVOCATION_ID, TENANT, HASH, True) is None

    def test_success_transitions_to_succeeded(self):
        """running + hash 匹配 + success=True：迁移到 succeeded"""
        current = {"id": INVOCATION_ID, "state": "running", "claim_token_hash": HASH,
                   "tenant_id": TENANT, "tool_name": "boss_goto", "credit_cost": None}
        updated = {"id": INVOCATION_ID, "state": "succeeded", "effect": "applied",
                   "claim_token_hash": HASH}
        with _patch_conn([current, updated]):
            row = repository.write_result(INVOCATION_ID, TENANT, HASH, True, effect="applied")
        assert row["state"] == "succeeded"

    def test_execution_unknown_maps_to_unknown(self):
        """success=False 且 code=EXECUTION_UNKNOWN：state=unknown（执行结果不可知）"""
        current = {"id": INVOCATION_ID, "state": "running", "claim_token_hash": HASH}
        updated = {"id": INVOCATION_ID, "state": "unknown", "effect": "unknown",
                   "claim_token_hash": HASH}
        with _patch_conn([current, updated]):
            row = repository.write_result(
                INVOCATION_ID, TENANT, HASH, False, code="EXECUTION_UNKNOWN"
            )
        assert row["state"] == "unknown"

    def test_not_found_returns_none(self):
        """invocation 不存在：返回 None"""
        with _patch_conn([None]):
            assert repository.write_result(INVOCATION_ID, TENANT, HASH, True) is None


class TestMarkStarted:
    def test_claimed_transitions_to_running(self):
        """claimed + hash 匹配：迁移 running"""
        updated = {"id": INVOCATION_ID, "state": "running", "claim_token_hash": HASH}
        with _patch_conn([updated]):
            row = repository.mark_started(INVOCATION_ID, TENANT, HASH)
        assert row["state"] == "running"

    def test_illegal_state_not_migrated(self):
        """非 claimed 状态（如 queued）：不迁移，返回当前状态供 API 层判 409"""
        current = {"id": INVOCATION_ID, "state": "queued", "claim_token_hash": HASH}
        # 第一次 fetchone：UPDATE 未命中（None）；第二次：SELECT 返回当前状态
        with _patch_conn([None, current]):
            row = repository.mark_started(INVOCATION_ID, TENANT, HASH)
        assert row["state"] == "queued"

    def test_wrong_claim_token_rejected(self):
        """claim token 不匹配：返回 None"""
        with _patch_conn([None, None]):
            assert repository.mark_started(INVOCATION_ID, TENANT, HASH) is None


class TestAppendEvent:
    def test_terminal_state_rejected(self):
        """终态 invocation 拒绝进度上报"""
        with _patch_conn([{"state": "succeeded"}]):
            assert repository.append_event(
                INVOCATION_ID, TENANT, HASH, "stage", 1, 10, "msg", 60
            ) is None

    def test_cancel_requested_returns_cancel_flag(self):
        """cancel_requested 状态：放行上报且 cancel=True（设备借此感知取消）"""
        with _patch_conn([{"state": "cancel_requested"}, {"next_seq": 3}]):
            result = repository.append_event(
                INVOCATION_ID, TENANT, HASH, "stage", 1, 10, "msg", 60
            )
        assert result == (3, True)

    def test_running_seq_increment(self):
        """running 状态：seq 递增，cancel=False"""
        with _patch_conn([{"state": "running"}, {"next_seq": 2}]):
            result = repository.append_event(
                INVOCATION_ID, TENANT, HASH, "stage", 2, 10, "msg", 60
            )
        assert result == (2, False)


class TestRequestCancel:
    """request_cancel 的 state 迁移合法性（取消不能制造永久卡住的非终态行）"""

    def _run_cancel(self, rowcounts):
        """rowcounts：两条 UPDATE 各自的命中行数；返回执行的 SQL 列表与返回值"""
        executed = []
        counts = iter(rowcounts)

        def fake_execute(sql, *a, **k):
            executed.append(sql)
            cursor.rowcount = next(counts)

        cursor = SimpleNamespace(
            execute=fake_execute,
            fetchone=lambda: None,
            fetchall=lambda: [],
            rowcount=0,
        )
        conn = SimpleNamespace(cursor=lambda *a, **k: cursor, commit=lambda: None, rollback=lambda: None)

        @contextmanager
        def fake_get_db_connection():
            yield conn

        with patch("src.local_tools.repository.get_db_connection", fake_get_db_connection):
            ok = repository.request_cancel(INVOCATION_ID, TENANT)
        return executed, ok

    def test_queued_goes_straight_to_cancelled_terminal(self):
        """queued 被取消：直接落终态 cancelled/effect=none

        为什么重要：claim_next 只取 state='queued'、expire_stale_claims 只清扫
        claimed/running。若 queued 被置为 cancel_requested，该行永远不会被领取、
        不会被清扫，永久卡在非终态。
        """
        executed, ok = self._run_cancel([1, 0])
        assert ok is True
        assert "state = 'cancelled'" in executed[0]
        assert "effect = 'none'" in executed[0]
        assert "state = 'queued'" in executed[0]

    def test_claimed_or_running_becomes_cancel_requested(self):
        """claimed/running 被取消：置 cancel_requested，等设备 progress 感知后写终态"""
        executed, ok = self._run_cancel([0, 1])
        assert ok is True
        assert "state = 'cancel_requested'" in executed[1]
        assert "('claimed', 'running')" in executed[1]

    def test_terminal_state_not_cancelable(self):
        """终态 invocation 不可取消（两条 UPDATE 都不命中）"""
        executed, ok = self._run_cancel([0, 0])
        assert ok is False
