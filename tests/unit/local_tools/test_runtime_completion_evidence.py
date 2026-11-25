"""契约 2.20 候选「正常结束的事实」：BOSS 调用 _runtime_completion 结束证据的服务端核验与消费。

场景（docs/system/agent-application-architecture-design.md「BOSS 本机操作恢复」；原开发计划 Phase 3）：
- Runtime 在受权结果 data 附加 _runtime_completion={version:1}（仅 boss、仅结构化终态停稳），
  服务端在原幂等回执入口 write_result 核验四要素：原设备+领取凭据（FOR UPDATE 行核验）
  + 原调用（本行）+ 设备能力串 runtime_boss_completion_v1 与证据版本。
- 合法证据原样落库为持久证据 → operation_completion_known 成立 → 只解除该调用的资源占用
  （detached_local_invocations hold / 设备 quarantine / public 投影），state/effect/计费/
  原始回执零改写；形状坏、版本错、设备无能力 → 剥离键走原逻辑（旧语义）。
- 迟到回执（unknown 行）带证据 → _late_result 包裹 stopped=true + observed_invocation 可信投影。

断言方式：UPDATE 参数（state/effect/result_json/error_code 与 test_local_wait_recovery 同法）。
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from src.local_tools import repository
from src.local_tools.catalog import declares_boss_completion
from src.services.agent_runner.public_view import with_local_operation_facts

pytestmark = pytest.mark.unit

INVOCATION_ID = "22222222-2222-2222-2222-222222222222"
TENANT = "tenant_rc"
DEVICE = "dev-rc-1"
HASH = "h" * 64
CAPABILITY = "runtime_boss_completion_v1"
EVIDENCE = {"version": 1}


class ScriptedCursor:
    """execute 记录器；fetchone/fetchall 按 SQL 次序出队（write_result：行→设备能力→RETURNING）"""

    def __init__(self, fetchone_rows=(), fetchall_rows=()):
        self.executed = []
        self.fetchone_rows = list(fetchone_rows)
        self.fetchall_rows = list(fetchall_rows)
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetchone_rows.pop(0) if self.fetchone_rows else None

    def fetchall(self):
        return self.fetchall_rows.pop(0) if self.fetchall_rows else []

    def updates(self):
        return [(sql, params) for sql, params in self.executed if sql.lstrip().startswith("UPDATE")]


class ScriptedConn:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, *a, **k):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def invocation_row(**overrides):
    return {"id": INVOCATION_ID, "tenant_id": TENANT, "device_id": DEVICE, "user_id": "user",
        "session_id": "session", "tool_name": "boss_filter", "provider_key": None,
        "arguments_json": {}, "state": "running", "effect": None, "result_json": None,
        "error_code": None, "error_message": None, "claimed_at": "2026-11-14",
        "claim_token_hash": HASH, "credit_cost": None, **overrides}


def run_write_result(current_row, *, success=True, code=None, effect=None,
                     data=None, device_row=None, retryable=None):
    """驱动 write_result：fetchone 次序 = invocation 行 → 设备能力行（契约触发）→ RETURNING 行"""
    has_evidence = isinstance(data, dict) and "_runtime_completion" in data
    fetchone_rows = [current_row] + ([device_row] if has_evidence else []) + [invocation_row()]
    cursor = ScriptedCursor(fetchone_rows)
    conn = ScriptedConn(cursor)

    @contextmanager
    def fake():
        yield conn

    ledger = MagicMock(return_value=99.0)
    with patch("src.local_tools.repository.get_db_connection", fake), \
         patch("src.local_tools.repository.tool_credit_price", return_value=1.0), \
         patch("src.local_tools.repository.ClientUsageLogDB") as usage_db, \
         patch("src.local_tools.repository.invalidate_tenant_cache"):
        usage_db.insert_tool_usage_row = ledger
        repository.write_result(INVOCATION_ID, TENANT, HASH, success, code=code,
            effect=effect, data=data, retryable=retryable)
    sql, values = cursor.updates()[-1]
    persisted = values[2].adapted  # psycopg2 Json 包装的 result_json
    return {"state": values[0], "effect": values[1], "result_json": persisted,
        "error_code": values[3], "error_message": values[4]}, cursor, ledger


def capable_device(capabilities=None):
    return {"id": DEVICE, "tenant_id": TENANT,
        "capabilities_json": {"capabilities": ["local_v2", CAPABILITY]}
        if capabilities is None else capabilities}


def holder_of(result_json):
    """detached_local_wait 持有该调用的 runner checkpoint + invocation 行"""
    row = {**invocation_row(state="unknown", effect="unknown", error_code="LEASE_LOST"),
        "result_json": result_json,
        "checkpoint": {"detached_local_wait": "true", "detached_local_invocations": [INVOCATION_ID]}}
    cursor = MagicMock()
    cursor.fetchall.return_value = [row]
    projection_row = {"tenant_id": TENANT, "user_id": "user", "session_id": "session",
        "checkpoint": {"detached_local_wait": True, "detached_local_invocations": [INVOCATION_ID]}}
    return row, cursor, projection_row


def assert_hold(cursor, projection_row, *, released):
    stored = cursor.fetchall.return_value[0]
    gate = ScriptedCursor(fetchone_rows=[None], fetchall_rows=[[stored]])
    if released:
        repository.assert_device_operations_settled(gate, TENANT, DEVICE)
    else:
        with pytest.raises(repository.DeviceOperationUnresolved) as exc:
            repository.assert_device_operations_settled(gate, TENANT, DEVICE)
        assert str(exc.value) == "LOCAL_DEVICE_STOP_VERIFICATION_REQUIRED"
    unresolved = with_local_operation_facts(cursor, projection_row)
    assert (unresolved["_unresolved_local_invocations"] == []) is released


# ---------- 能力串门禁（catalog，declared_tools_for_device 同款形态） ----------

def test_declares_boss_completion_requires_capability_string():
    assert declares_boss_completion({"capabilities": [CAPABILITY]})
    assert declares_boss_completion({"capabilities": ["local_v2", CAPABILITY]})
    assert not declares_boss_completion({"capabilities": ["local_v2"]})
    assert not declares_boss_completion({"capabilities": CAPABILITY})
    assert not declares_boss_completion({})
    assert not declares_boss_completion(None)
    assert not declares_boss_completion("runtime_boss_completion_v1")


# ---------- 完成判定纯函数：合法形状即完成证据（能力核验在 write 落库前） ----------

@pytest.mark.parametrize("success,code,effect,data,known", [
    (True, None, "unknown", {"_runtime_completion": EVIDENCE}, True),
    (False, "SCRIPT_NEEDS_FILL", "none", {"_runtime_completion": EVIDENCE}, True),
    (False, "INTERNAL_ERROR", "unknown", {"_runtime_completion": EVIDENCE}, True),
    (True, None, "applied", {"_runtime_completion": EVIDENCE}, True),
    (True, None, "unknown", {"_runtime_completion": {"version": 2}}, False),
    (True, None, "unknown", {"_runtime_completion": {"version": True}}, False),
    (True, None, "unknown", {"_runtime_completion": {"version": "1"}}, False),
    (True, None, "unknown", {"_runtime_completion": {"version": 1, "extra": True}}, False),
    (True, None, "unknown", {"_runtime_completion": 1}, False),
    (True, None, "unknown", {"other": 1}, False),
    (True, None, "unknown", None, False),
])
def test_result_proves_completion_recognizes_only_wellformed_evidence(success, code, effect, data, known):
    assert repository.result_proves_completion(success, code, effect, data) is known


# ---------- write_result：核验通过原样落库；不合法/无能力剥离（原逻辑零改写） ----------

def test_valid_evidence_with_capability_persists_and_releases_hold():
    """四要素齐：证据原样落库；state/effect/计费/原始回执保持原映射；完成已知 → hold 解除"""
    business = {"filter_evidence": {"version": 1, "skipped": []}}
    data = {**business, "_runtime_completion": dict(EVIDENCE)}
    written, cursor, ledger = run_write_result(invocation_row(),
        success=True, effect="unknown", data=data, device_row=capable_device())
    assert written["state"] == "succeeded" and written["effect"] == "unknown"
    assert written["result_json"] == {"success": True, "code": None, "message": None,
        "data": data, "retryable": None}, "原始回执（含证据）原样落库"
    ledger.assert_called_once(), "计费按原规则不变"
    device_query = [sql for sql, _ in cursor.executed if "local_tool_devices" in sql]
    assert len(device_query) == 1, "能力核验同事务读设备行（仅当证据出现时）"
    stored = {**invocation_row(state="succeeded", effect="unknown"),
        "result_json": written["result_json"]}
    assert repository.operation_completion_known(stored) is True
    row, cursor, projection_row = holder_of(written["result_json"])
    assert_hold(cursor, projection_row, released=True)


def test_valid_evidence_structured_failure_releases_hold_without_semantics_change():
    """结构化失败（未知占用家族）带证据：state/effect 原映射不变，占用解除"""
    data = {"_runtime_completion": dict(EVIDENCE)}
    written, _cursor, ledger = run_write_result(invocation_row(),
        success=False, code="SCRIPT_NEEDS_FILL", effect="none", data=data,
        device_row=capable_device())
    assert written["state"] == "failed" and written["effect"] == "none"
    assert written["error_code"] == "SCRIPT_NEEDS_FILL"
    ledger.assert_not_called()
    stored = {**invocation_row(state="failed", effect="none", error_code="SCRIPT_NEEDS_FILL"),
        "result_json": written["result_json"]}
    assert repository.operation_completion_known(stored) is True
    row, cursor, projection_row = holder_of(written["result_json"])
    assert_hold(cursor, projection_row, released=True)


@pytest.mark.parametrize("bad_data", [
    {"_runtime_completion": {"version": 2}},
    {"_runtime_completion": {"version": True}},
    {"_runtime_completion": {"version": 1, "forged": 1}},
    {"_runtime_completion": "version-1"},
])
def test_malformed_evidence_stripped_and_old_semantics_kept(bad_data):
    """形状/版本坏：剥离该键防伪造残留，走原逻辑（completion_known=false，hold 保留）"""
    written, cursor, _ledger = run_write_result(invocation_row(),
        success=True, effect="unknown", data=dict(bad_data), device_row=capable_device())
    assert written["result_json"]["data"] is None, "剥除后 data 为空折叠为 None（与空 data 旧形态一致）"
    stored = {**invocation_row(state="succeeded", effect="unknown"),
        "result_json": written["result_json"]}
    assert repository.operation_completion_known(stored) is False
    row, cursor, projection_row = holder_of(written["result_json"])
    assert_hold(cursor, projection_row, released=False)


def test_device_without_capability_strips_evidence():
    """设备未声明能力（旧 Runtime）：剥离走旧语义，无完成事实，其余回执字段保留"""
    data = {"_runtime_completion": dict(EVIDENCE), "kept": 1}
    written, _cursor, _ledger = run_write_result(invocation_row(),
        success=True, effect="unknown", data=data,
        device_row=capable_device({"capabilities": ["local_v2"]}))
    assert written["result_json"]["data"] == {"kept": 1}
    stored = {**invocation_row(state="succeeded", effect="unknown"),
        "result_json": written["result_json"]}
    assert repository.operation_completion_known(stored) is False


def test_device_row_missing_is_treated_as_no_capability():
    written, _cursor, _ledger = run_write_result(invocation_row(),
        success=True, effect="unknown", data={"_runtime_completion": dict(EVIDENCE)},
        device_row=None)
    assert written["result_json"]["data"] is None


def test_receipt_without_evidence_makes_no_device_query():
    """无证据回执零额外查询：现网主路径行为与查询成本不变"""
    _written, cursor, _ledger = run_write_result(invocation_row(),
        success=True, effect="applied", data={"echo_tool": "boss_filter"}, device_row=None)
    assert [sql for sql, _ in cursor.executed if "local_tool_devices" in sql] == []
    assert cursor.fetchone_rows == [], "不应消费设备行队列"


# ---------- 迟到回执：unknown 行收到带证据回执 ----------

def test_late_receipt_with_evidence_stops_and_projects_trusted_view():
    """租约丢失 unknown 行 + 迟到可信回执：_late_result 包裹 stopped=true，
    原 unknown 事实（state/effect/error_code）零改写，observed_invocation 投影可信完成"""
    lost = invocation_row(state="unknown", effect="unknown",
        error_code="LEASE_LOST", error_message="Original lease lost")
    data = {"kept": 1, "_runtime_completion": dict(EVIDENCE)}
    written, _cursor, ledger = run_write_result(lost, success=True, effect="unknown",
        data=data, device_row=capable_device())
    ledger.assert_called_once(), "迟到成功结算一次（原规则）"
    assert written["state"] == "unknown" and written["effect"] == "unknown"
    assert written["error_code"] == "LEASE_LOST"
    late = written["result_json"]["_late_result"]
    assert late["stopped"] is True and late["success"] is True
    assert late["data"]["_runtime_completion"] == EVIDENCE
    assert late["effect"] == "unknown", "业务效果未知照旧保留（不自动重发语义不变）"
    stored = {**lost, "result_json": written["result_json"]}
    observed = repository.observed_invocation(stored)
    assert observed["state"] == "succeeded" and stored["state"] == "unknown"
    assert repository.operation_completion_known(stored) is True
    row, cursor, projection_row = holder_of(written["result_json"])
    assert_hold(cursor, projection_row, released=True)


def test_late_receipt_without_capability_keeps_hold():
    lost = invocation_row(state="unknown", effect="unknown", error_code="LEASE_LOST")
    written, _cursor, _ledger = run_write_result(lost, success=True, effect="unknown",
        data={"_runtime_completion": dict(EVIDENCE)},
        device_row=capable_device({"capabilities": ["local_v2"]}))
    assert written["result_json"]["_late_result"]["stopped"] is False
    stored = {**lost, "result_json": written["result_json"]}
    assert repository.operation_completion_known(stored) is False
