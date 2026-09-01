"""write_result 落库侧同事务计费单元测试（2026-09-01 计费时机迁移，mock DB 无真实库）

设计：docs/design/billing/client-billing-integration-design.md §4.1

覆盖：
- succeeded 首次落终态：收费工具台账 INSERT 恰一次 + UPDATE 回写 credit_cost + 失效租户缓存
- 免费工具（price=0）：不落台账，仅回写 credit_cost=0 占位（对账可判）
- 已回写过 credit_cost：不重复计费（幂等闸门）
- failed / unknown（EXECUTION_UNKNOWN）：不计费不回写
- 租户不存在（balance_after=None）：台账仍落（对账可见）+ 正常提交（告警在 repository 日志）
- 计费异常：回滚计费半程，降级为只落终态（credit_cost 留 NULL），工具结果不丢
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import json

from src.db.client_binding_db import (
    TOOL_USAGE_ARGS_MAX_CHARS,
    _compact_arguments,
    _build_tool_usage_detail,
)
from src.local_tools import repository

pytestmark = pytest.mark.unit

INVOCATION_ID = "11111111-1111-1111-1111-111111111111"
TENANT = "tenant_t1"
DEVICE = "dev-1"
HASH = "h" * 64


class TestCompactArguments:
    """台账 detail.arguments 规整（P2 §4.2）：命令参数免 join invocation 即可对账"""

    def test_none_passthrough(self):
        assert _compact_arguments(None) is None

    def test_normal_json_passthrough(self):
        args = {"salary": "3-5K", "educations": ["本科"], "limit": 1}
        assert _compact_arguments(args) == args

    def test_oversized_truncated_to_valid_text(self):
        big = {"k": "x" * (TOOL_USAGE_ARGS_MAX_CHARS * 2)}
        out = _compact_arguments(big)
        assert set(out.keys()) == {"_truncated"}
        assert out["_truncated"].endswith("…(截断)")
        # 降级结果必须仍是可序列化 JSON（detail 整体合法）
        json.dumps(out, ensure_ascii=False)

    def test_unserializable_degrades_to_str(self):
        out = _compact_arguments({1, 2})  # set 不可 JSON 序列化
        assert out == "{1, 2}"
        json.dumps(out, ensure_ascii=False)


class TestBuildToolUsageDetail:
    def test_detail_shape(self):
        detail = json.loads(_build_tool_usage_detail(
            invocation_id=INVOCATION_ID, device_id=DEVICE,
            command="boss_filter", arguments={"salary": "3-5K"}, user_id="user-9",
        ))
        assert detail == {"invocation_id": INVOCATION_ID, "device_id": DEVICE,
                          "command": "boss_filter", "arguments": {"salary": "3-5K"},
                          "user_id": "user-9"}


class _FakeCursor:
    """execute 记录器 + fetchone 按序返回"""

    def __init__(self, fetchone_rows):
        self.executed = []
        self._rows = iter(fetchone_rows)
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return next(self._rows, None)

    def fetchall(self):
        return []

    def updates(self):
        return [(sql, params) for sql, params in self.executed
                if sql.lstrip().startswith("UPDATE")]


class _FakeConn:
    def __init__(self, fetchone_rows):
        self.cursor_obj = _FakeCursor(fetchone_rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, *a, **k):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _run_write_result(current_row, updated_row, *, success=True, price=1.0,
                      ledger_return=99.0, ledger_error=None):
    """驱动 write_result：mock 取价/台账插入/缓存失效，返回 (结果行, conn, ledger_mock, cache_mock)"""
    conn = _FakeConn([current_row, updated_row])

    @contextmanager
    def fake():
        yield conn

    ledger = MagicMock(return_value=ledger_return)
    if ledger_error is not None:
        ledger.side_effect = ledger_error
    cache = MagicMock()
    with patch("src.local_tools.repository.get_db_connection", fake), \
         patch("src.local_tools.repository.tool_credit_price", return_value=price), \
         patch("src.local_tools.repository.ClientUsageLogDB") as usage_db, \
         patch("src.local_tools.repository.invalidate_tenant_cache", cache):
        usage_db.insert_tool_usage_row = ledger
        row = repository.write_result(INVOCATION_ID, TENANT, HASH, success, effect="applied")
    return row, conn, ledger, cache


class TestSucceededBilling:
    def test_priced_tool_bills_once(self):
        """succeeded + 收价工具：台账恰一次（带 session/命令/参数/user）+ 回写 + 失效缓存"""
        updated = {"id": INVOCATION_ID, "state": "succeeded", "credit_cost": 1.0}
        row, conn, ledger, cache = _run_write_result(
            {"id": INVOCATION_ID, "state": "running", "claim_token_hash": HASH,
             "tenant_id": TENANT, "tool_name": "boss_greet", "device_id": DEVICE,
             "user_id": "user-9", "session_id": "sess-9", "arguments_json": {"limit": 1},
             "credit_cost": None},
            updated,
        )
        assert row["state"] == "succeeded"
        ledger.assert_called_once()
        kwargs = ledger.call_args.kwargs
        assert kwargs["tenant_id"] == TENANT
        assert kwargs["tool_name"] == "boss_greet"
        assert kwargs["credit_cost"] == 1.0
        assert kwargs["invocation_id"] == INVOCATION_ID
        assert kwargs["device_id"] == DEVICE
        # P2：台账行会话/用户/命令参数归属
        assert kwargs["session_id"] == "sess-9"
        assert kwargs["user_id"] == "user-9"
        assert kwargs["arguments"] == {"limit": 1}
        sql, params = conn.cursor_obj.updates()[-1]
        assert "credit_cost = %s" in sql
        assert 1.0 in params
        assert conn.commits == 1
        assert conn.rollbacks == 0
        cache.assert_called_once_with(TENANT)

    def test_free_tool_writes_zero_marker_without_ledger(self):
        """免费工具：不落台账，仅 credit_cost=0 占位（succeeded 未回写=计费降级，对账可判）"""
        updated = {"id": INVOCATION_ID, "state": "succeeded", "credit_cost": 0.0}
        row, conn, ledger, cache = _run_write_result(
            {"id": INVOCATION_ID, "state": "running", "claim_token_hash": HASH,
             "tenant_id": TENANT, "tool_name": "boss_goto", "device_id": DEVICE,
             "credit_cost": None},
            updated,
            price=0.0,
        )
        assert row["state"] == "succeeded"
        ledger.assert_not_called()
        sql, params = conn.cursor_obj.updates()[-1]
        assert "credit_cost = %s" in sql
        assert 0.0 in params
        cache.assert_not_called()

    def test_already_credited_never_bills_again(self):
        """credit_cost 已回写（非终态行防御）：不重复落台账、不重复回写"""
        updated = {"id": INVOCATION_ID, "state": "succeeded", "credit_cost": 1.0}
        row, conn, ledger, cache = _run_write_result(
            {"id": INVOCATION_ID, "state": "running", "claim_token_hash": HASH,
             "tenant_id": TENANT, "tool_name": "boss_greet", "device_id": DEVICE,
             "credit_cost": 1.0},
            updated,
        )
        assert row["state"] == "succeeded"
        ledger.assert_not_called()
        sql, _params = conn.cursor_obj.updates()[-1]
        assert "credit_cost" not in sql
        cache.assert_not_called()

    def test_missing_tenant_still_records_ledger_and_commits(self):
        """租户不存在（balance_after=None）：台账仍落（对账可见）、正常提交，不抛异常"""
        updated = {"id": INVOCATION_ID, "state": "succeeded", "credit_cost": 1.0}
        row, conn, ledger, _cache = _run_write_result(
            {"id": INVOCATION_ID, "state": "running", "claim_token_hash": HASH,
             "tenant_id": TENANT, "tool_name": "boss_greet", "device_id": DEVICE,
             "credit_cost": None},
            updated,
            ledger_return=None,
        )
        assert row["state"] == "succeeded"
        ledger.assert_called_once()
        assert conn.commits == 1
        sql, params = conn.cursor_obj.updates()[-1]
        assert "credit_cost = %s" in sql


class TestNonBillingTerminals:
    def test_failed_never_bills(self):
        updated = {"id": INVOCATION_ID, "state": "failed", "credit_cost": None}
        row, conn, ledger, cache = _run_write_result(
            {"id": INVOCATION_ID, "state": "running", "claim_token_hash": HASH,
             "tenant_id": TENANT, "tool_name": "boss_greet", "device_id": DEVICE,
             "credit_cost": None},
            updated,
            success=False,
        )
        assert row["state"] == "failed"
        ledger.assert_not_called()
        sql, _params = conn.cursor_obj.updates()[-1]
        assert "credit_cost" not in sql
        cache.assert_not_called()

    def test_execution_unknown_never_bills(self):
        """EXECUTION_UNKNOWN（结果不可知）：unknown 终态不计费不回写（宁可少收不可错收）"""
        conn = _FakeConn([
            {"id": INVOCATION_ID, "state": "running", "claim_token_hash": HASH,
             "tenant_id": TENANT, "tool_name": "boss_greet", "device_id": DEVICE,
             "credit_cost": None},
            {"id": INVOCATION_ID, "state": "unknown", "credit_cost": None},
        ])

        @contextmanager
        def fake():
            yield conn

        ledger = MagicMock()
        with patch("src.local_tools.repository.get_db_connection", fake), \
             patch("src.local_tools.repository.tool_credit_price", return_value=1.0), \
             patch("src.local_tools.repository.ClientUsageLogDB") as usage_db, \
             patch("src.local_tools.repository.invalidate_tenant_cache"):
            usage_db.insert_tool_usage_row = ledger
            row = repository.write_result(
                INVOCATION_ID, TENANT, HASH, False, code="EXECUTION_UNKNOWN"
            )
        assert row["state"] == "unknown"
        ledger.assert_not_called()
        sql, _params = conn.cursor_obj.updates()[-1]
        assert "credit_cost" not in sql

    def test_terminal_idempotent_short_circuits_before_billing(self):
        """已终态重复写：幂等短路，SELECT 后无 UPDATE、无台账"""
        conn = _FakeConn([{"id": INVOCATION_ID, "state": "succeeded",
                           "claim_token_hash": HASH, "tool_name": "boss_greet",
                           "credit_cost": 1.0}])

        @contextmanager
        def fake():
            yield conn

        ledger = MagicMock()
        with patch("src.local_tools.repository.get_db_connection", fake), \
             patch("src.local_tools.repository.tool_credit_price", return_value=1.0), \
             patch("src.local_tools.repository.ClientUsageLogDB") as usage_db:
            usage_db.insert_tool_usage_row = ledger
            row = repository.write_result(INVOCATION_ID, TENANT, HASH, True, effect="applied")
        assert row["state"] == "succeeded"
        ledger.assert_not_called()
        assert conn.cursor_obj.updates() == []


class TestBillingFailureDegradation:
    def test_ledger_failure_degrades_to_state_only(self):
        """台账落账异常：回滚计费半程 → 重取行加锁 → 只落终态（credit_cost 留 NULL）→ 结果不丢"""
        running = {"id": INVOCATION_ID, "state": "running", "claim_token_hash": HASH,
                   "tenant_id": TENANT, "tool_name": "boss_greet", "device_id": DEVICE,
                   "credit_cost": None}
        updated = {"id": INVOCATION_ID, "state": "succeeded", "credit_cost": None}
        conn = _FakeConn([running, running, updated])

        @contextmanager
        def fake():
            yield conn

        ledger = MagicMock(side_effect=RuntimeError("db down"))
        cache = MagicMock()
        with patch("src.local_tools.repository.get_db_connection", fake), \
             patch("src.local_tools.repository.tool_credit_price", return_value=1.0), \
             patch("src.local_tools.repository.ClientUsageLogDB") as usage_db, \
             patch("src.local_tools.repository.invalidate_tenant_cache", cache):
            usage_db.insert_tool_usage_row = ledger
            row = repository.write_result(INVOCATION_ID, TENANT, HASH, True, effect="applied")

        assert row["state"] == "succeeded"  # 工具结果保留
        ledger.assert_called_once()
        assert conn.rollbacks == 1
        assert conn.commits == 1
        sql, params = conn.cursor_obj.updates()[-1]
        assert "credit_cost" not in sql  # 降级：不回写，留给对账补账
        cache.assert_not_called()
