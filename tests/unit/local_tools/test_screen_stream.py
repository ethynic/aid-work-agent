"""Runtime 桌面实时画面服务端单元测试（全 mock，无数据库）

契约 2.25、设计 runtime-screen-stream-design.md 第 3、5 节、计划 Phase 1。覆盖：
- supports_screen_stream：能力判定
- evaluate_timeout：六种超时收敛、时限边界、从未访问的空值、已结束会话
- apply_signal：状态流转、判定结果、end 幂等，以及它保留的两条校验（已结束会话拒收、信令条数上限）；
  信令类型、state、path、结束原因是否合法由请求模型的用例覆盖（TestSignalModels）
- 信令模型：type/state/path/reason 字面量校验，payload 必须是对象且序列化后不超过 16 KB
- 数据访问层（mock 连接）：创建会话的拒绝顺序、读取只刷新访问方、end 幂等不写库、心跳查询的语句数
- 数据库层的等待上限：四个数据库函数的第一条语句是 SET LOCAL；会话路径上等锁超时转成 SCREEN_STREAM_BUSY；
  心跳路径上数据库取消语句（55P03、57014）按超时告警，其他异常仍按失败告警
- STUN 配置：只保留 stun: / stuns: 地址，其余丢弃并告警（告警里不写地址）
- 心跳响应：画面通知字段，功能未启用时不带，查询报错或超时不影响心跳
- 设备列表：screen_stream 投影
- 六个接口：功能未启用 503、非法路径 404、鉴权范围参数、类型化错误转换
"""

import asyncio
import copy
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger
from psycopg2 import errors as pg_errors
from pydantic import TypeAdapter, ValidationError

from src.config.settings import ScreenStreamConfig, settings
from src.local_tools import api as local_tools_api
from src.local_tools import screen_stream

pytestmark = pytest.mark.unit

TENANT = "tenant_test"
USER = "user_test"
DEVICE = "11111111-1111-4111-8111-111111111111"
SESSION = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 11, 25, 12, 0, 0)
ONLINE = 30
# PostgreSQL 到点取消语句时抛的异常：55P03 是等锁超时（lock_timeout），57014 是语句超时（statement_timeout）。
# psycopg2 从服务端的错误响应里把 SQLSTATE 填进 pgcode；这个属性只读，手工构造的异常上是 None。
# 测试里用子类把它固定下来。路由层的判断依据是 SQLSTATE，不是异常类
class LockTimeout(pg_errors.LockNotAvailable):
    pgcode = "55P03"


class StatementTimeout(pg_errors.QueryCanceled):
    pgcode = "57014"


# (异常类, 服务端给出的消息)：两种「数据库到点取消了语句」
DATABASE_CANCELS = [
    pytest.param(LockTimeout, "canceling statement due to lock timeout", id="lock-timeout-55P03"),
    pytest.param(StatementTimeout, "canceling statement due to statement timeout", id="statement-timeout-57014"),
]

DEVICE_KEYS = {"device_id", "name", "platform", "runtime_version", "selected", "status", "online",
               "last_seen_at", "created_at"}
SESSION_PATH = f"/api/local-tools/devices/{DEVICE}/screen-sessions/{SESSION}"
RUNTIME_SESSION_BODY = {"session_id": SESSION, "after_seq": 0}
RUNTIME_SIGNAL_BODY = {"session_id": SESSION, "type": "prepared", "payload": {}}
# Python 的 JSON 解析接受、PostgreSQL 的 json 不接受的三种写法
NON_FINITE = [
    pytest.param(float("nan"), id="NaN"),
    pytest.param(float("inf"), id="Infinity"),
    pytest.param(float("-inf"), id="-Infinity"),
]


def _row(**overrides):
    """一个 pending 会话行（带同一事务的 db_now 和设备在线时间）"""
    row = {"id": SESSION, "state": "pending", "end_reason": None, "verdict": None, "path": None,
           "signals": [], "created_at": NOW, "connected_at": None, "started_at": None, "ended_at": None,
           "viewer_seen_at": None, "runtime_seen_at": None, "device_last_seen_at": NOW, "db_now": NOW}
    row.update(overrides)
    return row


def _signals(count, direction="rt"):
    return [{"seq": n, "dir": direction, "type": "candidate", "payload": {"n": n}} for n in range(1, count + 1)]


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------


class TestSupportsScreenStream:
    def test_capability_present(self):
        caps = {"provider_id": "p", "capabilities": ["local_v2", "tool_declarations_v1", "screen_stream_v1"]}
        assert screen_stream.supports_screen_stream(caps) is True

    @pytest.mark.parametrize("capabilities_json", [
        {"capabilities": ["local_v2", "tool_declarations_v1"]},
        {"capabilities": "screen_stream_v1"},
        {"screen_stream_v1": True},
        ["screen_stream_v1"],
        None,
    ])
    def test_capability_absent_or_malformed(self, capabilities_json):
        assert screen_stream.supports_screen_stream(capabilities_json) is False


class TestConstants:
    def test_timeout_table_matches_design(self):
        """时限（秒）是设计第 3 节的契约值"""
        assert screen_stream.TIMEOUT_SECONDS == {
            "pending": 30, "prepared": 60, "connecting": 30, "verifying": 15, "streaming_viewer": 30,
        }
        assert screen_stream.ENDED_NOTICE_SECONDS == 30
        assert screen_stream.MAX_SIGNALS == 128
        assert screen_stream.MAX_PAYLOAD_BYTES == 16 * 1024

    def test_runtime_end_reasons(self):
        assert screen_stream.RUNTIME_END_REASONS == (
            "runtime_stopped", "runtime_rejected", "same_machine", "machine_check_unknown",
            "connect_failed", "renewal_timeout", "resource_busy",
        )

    def test_error_table(self):
        """错误码、HTTP 状态与用户可见文案"""
        expected = {
            "SCREEN_STREAM_DISABLED": (503, "实时画面功能未启用"),
            "DEVICE_NOT_FOUND": (404, "设备不存在"),
            "DEVICE_OFFLINE": (409, "Runtime 电脑不在线"),
            "SCREEN_STREAM_UNSUPPORTED": (409, "当前 Runtime 版本不支持实时画面，请升级 Runtime"),
            "SCREEN_SESSION_ACTIVE": (409, "已有窗口正在查看这台电脑"),
            "SCREEN_SESSION_NOT_FOUND": (404, "会话不存在或无权访问"),
            "SCREEN_SESSION_ENDED": (409, "会话已结束"),
            "SCREEN_SIGNAL_LIMIT": (409, "信令条数超过上限"),
            "SCREEN_STREAM_BUSY": (503, "实时画面暂时繁忙，请稍后重试"),
        }
        assert set(screen_stream.ERRORS) == set(expected)
        for code, (status, message) in expected.items():
            error = screen_stream.ScreenStreamError(code)
            assert (error.status, error.code, error.message) == (status, code, message)
            assert isinstance(error, ValueError)


class TestEvaluateTimeout:
    @pytest.mark.parametrize(("state", "column", "limit", "reason"), [
        ("pending", "created_at", 30, "device_offline"),
        ("prepared", "viewer_seen_at", 60, "viewer_closed"),
        ("connecting", "runtime_seen_at", 30, "connect_failed"),
        ("verifying", "runtime_seen_at", 15, "machine_check_unknown"),
    ])
    def test_anchor_just_past_limit_ends_session(self, state, column, limit, reason):
        row = _row(state=state, **{column: NOW - timedelta(seconds=limit, microseconds=1)})
        assert screen_stream.evaluate_timeout(row, NOW, NOW, ONLINE) == reason

    @pytest.mark.parametrize(("state", "column", "limit"), [
        ("pending", "created_at", 30),
        ("prepared", "viewer_seen_at", 60),
        ("connecting", "runtime_seen_at", 30),
        ("verifying", "runtime_seen_at", 15),
    ])
    def test_anchor_exactly_at_limit_still_active(self, state, column, limit):
        """时限是闭区间：刚好等于时限不算超时"""
        row = _row(state=state, **{column: NOW - timedelta(seconds=limit)})
        assert screen_stream.evaluate_timeout(row, NOW, NOW, ONLINE) is None

    def test_streaming_device_offline_just_past_threshold(self):
        row = _row(state="streaming", started_at=NOW, viewer_seen_at=NOW,
                   device_last_seen_at=NOW - timedelta(seconds=30, microseconds=1))
        assert screen_stream.evaluate_timeout(row, NOW, row["device_last_seen_at"], ONLINE) == "runtime_offline"

    def test_streaming_device_exactly_at_threshold_still_active(self):
        last_seen = NOW - timedelta(seconds=30)
        row = _row(state="streaming", started_at=NOW, viewer_seen_at=NOW, device_last_seen_at=last_seen)
        assert screen_stream.evaluate_timeout(row, NOW, last_seen, ONLINE) is None

    def test_streaming_viewer_just_past_limit(self):
        row = _row(state="streaming", started_at=NOW, viewer_seen_at=NOW - timedelta(seconds=30, microseconds=1))
        assert screen_stream.evaluate_timeout(row, NOW, NOW, ONLINE) == "viewer_closed"

    def test_streaming_viewer_exactly_at_limit_still_active(self):
        row = _row(state="streaming", started_at=NOW, viewer_seen_at=NOW - timedelta(seconds=30))
        assert screen_stream.evaluate_timeout(row, NOW, NOW, ONLINE) is None

    def test_streaming_device_offline_wins_over_viewer(self):
        """传画面期间先判设备离线，再判网页"""
        row = _row(state="streaming", started_at=NOW,
                   viewer_seen_at=NOW - timedelta(seconds=120), device_last_seen_at=NOW - timedelta(seconds=60))
        assert screen_stream.evaluate_timeout(row, NOW, row["device_last_seen_at"], ONLINE) == "runtime_offline"

    def test_streaming_device_threshold_comes_from_caller(self):
        """设备离线阈值由路由层传入（api.ONLINE_THRESHOLD_SECONDS），模块内不写死"""
        last_seen = NOW - timedelta(seconds=45)
        row = _row(state="streaming", started_at=NOW, viewer_seen_at=NOW, device_last_seen_at=last_seen)
        assert screen_stream.evaluate_timeout(row, NOW, last_seen, ONLINE) == "runtime_offline"
        assert screen_stream.evaluate_timeout(row, NOW, last_seen, 120) is None

    def test_other_states_ignore_device_and_viewer_times(self):
        """未出画面前不看设备在线时间和网页访问时间"""
        stale = NOW - timedelta(seconds=3600)
        for state in ("pending", "prepared", "connecting", "verifying"):
            row = _row(state=state, created_at=NOW, viewer_seen_at=NOW, runtime_seen_at=NOW,
                       device_last_seen_at=stale)
            assert screen_stream.evaluate_timeout(row, NOW, stale, ONLINE) is None

    @pytest.mark.parametrize(("state", "reason"), [
        ("prepared", "viewer_closed"),
        ("connecting", "connect_failed"),
        ("verifying", "machine_check_unknown"),
    ])
    def test_never_accessed_counts_as_timed_out(self, state, reason):
        """等待的一方从未访问（时间为空）按超时处理，不抛异常"""
        row = _row(state=state)
        assert screen_stream.evaluate_timeout(row, NOW, NOW, ONLINE) == reason

    def test_streaming_without_device_seen_time_is_runtime_offline(self):
        row = _row(state="streaming", started_at=NOW, viewer_seen_at=NOW, device_last_seen_at=None)
        assert screen_stream.evaluate_timeout(row, NOW, None, ONLINE) == "runtime_offline"

    def test_streaming_without_viewer_seen_time_is_viewer_closed(self):
        row = _row(state="streaming", started_at=NOW, viewer_seen_at=None, device_last_seen_at=NOW)
        assert screen_stream.evaluate_timeout(row, NOW, NOW, ONLINE) == "viewer_closed"

    def test_ended_session_is_never_converged_again(self):
        stale = NOW - timedelta(days=1)
        row = _row(state="ended", end_reason="viewer_closed", created_at=stale, viewer_seen_at=stale,
                   runtime_seen_at=stale, device_last_seen_at=stale)
        assert screen_stream.evaluate_timeout(row, NOW, stale, ONLINE) is None


class TestApplySignal:
    def test_runtime_prepared_moves_pending_to_prepared(self):
        changes = screen_stream.apply_signal(_row(state="pending"), "rt", "prepared", {})
        assert changes == {"state": "prepared",
                           "signals": [{"seq": 1, "dir": "rt", "type": "prepared", "payload": {}}]}

    def test_web_start_moves_prepared_to_connecting(self):
        changes = screen_stream.apply_signal(_row(state="prepared"), "web", "start", {})
        assert changes == {"state": "connecting",
                           "signals": [{"seq": 1, "dir": "web", "type": "start", "payload": {}}]}

    def test_status_connected_writes_connected_at(self):
        changes = screen_stream.apply_signal(_row(state="connecting"), "rt", "status", {"state": "connected"})
        assert changes == {"state": "verifying", "connected_at": NOW,
                           "signals": [{"seq": 1, "dir": "rt", "type": "status", "payload": {"state": "connected"}}]}
        assert "started_at" not in changes and "verdict" not in changes

    def test_status_streaming_writes_started_at_path_and_other_verdict(self):
        payload = {"state": "streaming", "path": "lan"}
        changes = screen_stream.apply_signal(_row(state="verifying", connected_at=NOW), "rt", "status", payload)
        assert changes == {"state": "streaming", "started_at": NOW, "verdict": "other", "path": "lan",
                           "signals": [{"seq": 1, "dir": "rt", "type": "status", "payload": payload}]}

    def test_status_streaming_without_path_leaves_path_unset(self):
        changes = screen_stream.apply_signal(_row(state="verifying"), "rt", "status", {"state": "streaming"})
        assert changes["verdict"] == "other" and changes["state"] == "streaming"
        assert "path" not in changes

    def test_streaming_reached_from_connecting_directly_is_accepted(self):
        """不校验信令顺序：从 connecting 直接到 streaming 也接受并写相应字段"""
        changes = screen_stream.apply_signal(_row(state="connecting"), "rt", "status", {"state": "streaming"})
        assert changes["state"] == "streaming" and changes["started_at"] == NOW and changes["verdict"] == "other"

    @pytest.mark.parametrize("state", ["locked", "unlocked", "degraded", "restored"])
    def test_other_status_states_only_relay(self, state):
        payload = {"state": state}
        row = _row(state="streaming", started_at=NOW, verdict="other", path="p2p")
        changes = screen_stream.apply_signal(row, "rt", "status", payload)
        assert changes == {"signals": [{"seq": 1, "dir": "rt", "type": "status", "payload": payload}]}

    @pytest.mark.parametrize(("direction", "signal_type"), [
        ("rt", "offer"), ("rt", "candidate"), ("web", "answer"), ("web", "candidate"),
    ])
    def test_connection_signals_only_relay(self, direction, signal_type):
        payload = {"sdp": "v=0", "candidate": "1 1 udp 1 192.0.2.1 9 typ host"}
        changes = screen_stream.apply_signal(_row(state="connecting"), direction, signal_type, payload)
        assert changes == {"signals": [{"seq": 1, "dir": direction, "type": signal_type, "payload": payload}]}

    def test_signal_sequence_continues_from_stored_count_in_both_directions(self):
        """序号在会话内单调递增，两个方向共用一个计数"""
        row = _row(state="prepared", signals=_signals(3, "rt") + [{"seq": 4, "dir": "web", "type": "answer", "payload": {}}])
        changes = screen_stream.apply_signal(row, "rt", "offer", {})
        assert changes["signals"][-1] == {"seq": 5, "dir": "rt", "type": "offer", "payload": {}}
        assert [s["seq"] for s in changes["signals"]] == [1, 2, 3, 4, 5]
        assert len(row["signals"]) == 4  # 不改传入的行

    def test_web_end_records_viewer_closed_and_clears_signals(self):
        row = _row(state="streaming", started_at=NOW, verdict="other", signals=_signals(4))
        changes = screen_stream.apply_signal(row, "web", "end", {"reason": "ignored"})
        assert changes == {"state": "ended", "end_reason": "viewer_closed", "ended_at": NOW, "signals": []}

    @pytest.mark.parametrize(("reason", "verdict"), [
        ("runtime_stopped", None), ("runtime_rejected", None), ("connect_failed", None),
        ("renewal_timeout", None), ("resource_busy", None),
    ])
    def test_runtime_end_without_verdict(self, reason, verdict):
        changes = screen_stream.apply_signal(_row(state="streaming", started_at=NOW), "rt", "end", {"reason": reason})
        assert changes == {"state": "ended", "end_reason": reason, "ended_at": NOW, "signals": []}

    def test_same_machine_end_records_same_verdict(self):
        changes = screen_stream.apply_signal(_row(state="verifying"), "rt", "end", {"reason": "same_machine"})
        assert changes == {"state": "ended", "end_reason": "same_machine", "verdict": "same",
                           "ended_at": NOW, "signals": []}

    def test_machine_check_unknown_end_records_unknown_verdict(self):
        changes = screen_stream.apply_signal(_row(state="verifying"), "rt", "end", {"reason": "machine_check_unknown"})
        assert changes == {"state": "ended", "end_reason": "machine_check_unknown", "verdict": "unknown",
                           "ended_at": NOW, "signals": []}

    def test_runtime_stopped_end_overwrites_earlier_other_verdict(self):
        """同机结论只在对应的结束原因上给出；其余结束原因不写判定结果"""
        row = _row(state="streaming", started_at=NOW, verdict="other")
        changes = screen_stream.apply_signal(row, "rt", "end", {"reason": "runtime_stopped"})
        assert "verdict" not in changes and changes["end_reason"] == "runtime_stopped"

    def test_end_is_idempotent_on_ended_session(self):
        """已结束的会话再发 end：不改任何数据（两个方向）"""
        for direction in ("web", "rt"):
            row = _row(state="ended", end_reason="same_machine", verdict="same", ended_at=NOW)
            before = copy.deepcopy(row)
            assert screen_stream.apply_signal(row, direction, "end", {"reason": "runtime_stopped"}) == {}
            assert row == before

    @pytest.mark.parametrize(("direction", "signal_type"), [
        ("web", "start"), ("web", "answer"), ("web", "candidate"),
        ("rt", "prepared"), ("rt", "offer"), ("rt", "candidate"),
        ("rt", "status"),
    ])
    def test_ended_session_rejects_other_signals(self, direction, signal_type):
        payload = {"state": "streaming"} if signal_type == "status" else {}
        with pytest.raises(screen_stream.ScreenStreamError) as caught:
            screen_stream.apply_signal(_row(state="ended", end_reason="viewer_closed"), direction, signal_type, payload)
        assert caught.value.code == "SCREEN_SESSION_ENDED" and caught.value.status == 409

    def test_signal_limit_rejects_the_129th_signal(self):
        row = _row(state="connecting", signals=_signals(screen_stream.MAX_SIGNALS, "web"))
        with pytest.raises(screen_stream.ScreenStreamError) as caught:
            screen_stream.apply_signal(row, "rt", "candidate", {})
        assert caught.value.code == "SCREEN_SIGNAL_LIMIT" and caught.value.status == 409
        assert len(row["signals"]) == screen_stream.MAX_SIGNALS

    def test_last_allowed_signal_gets_seq_128(self):
        row = _row(state="connecting", signals=_signals(screen_stream.MAX_SIGNALS - 1, "web"))
        changes = screen_stream.apply_signal(row, "rt", "offer", {})
        assert changes["signals"][-1]["seq"] == screen_stream.MAX_SIGNALS

    def test_end_still_works_when_signal_storage_is_full(self):
        """end 不占信令存储：满额后仍可结束会话"""
        row = _row(state="connecting", signals=_signals(screen_stream.MAX_SIGNALS, "web"))
        changes = screen_stream.apply_signal(row, "rt", "end", {"reason": "connect_failed"})
        assert changes == {"state": "ended", "end_reason": "connect_failed", "ended_at": NOW, "signals": []}


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class TestSignalModels:
    _viewer = TypeAdapter(screen_stream.ViewerSignalRequest)
    _runtime = TypeAdapter(screen_stream.RuntimeSignalRequest)

    @pytest.mark.parametrize("signal_type", ["start", "answer", "candidate", "end"])
    def test_viewer_signal_types_accepted(self, signal_type):
        body = self._viewer.validate_python({"type": signal_type, "payload": {}})
        assert body.type == signal_type
        assert body.payload.model_dump(exclude_unset=True) == {}

    @pytest.mark.parametrize("signal_type", ["prepared", "offer", "status", "start ", "bogus"])
    def test_viewer_rejects_runtime_signal_types(self, signal_type):
        with pytest.raises(ValidationError):
            self._viewer.validate_python({"type": signal_type, "payload": {}})

    @pytest.mark.parametrize(("signal_type", "payload"), [
        ("prepared", {}), ("offer", {"sdp": "v=0"}), ("candidate", {"candidate": "host", "sdp_mid": "0", "sdp_mline_index": 0}),
    ])
    def test_runtime_signal_types_with_generic_payload_accepted(self, signal_type, payload):
        body = self._runtime.validate_python({"session_id": SESSION, "type": signal_type, "payload": payload})
        assert (str(body.session_id), body.type) == (SESSION, signal_type)
        assert body.payload.model_dump(exclude_unset=True) == payload

    @pytest.mark.parametrize("signal_type", ["start", "answer", "status", "bogus"])
    def test_runtime_rejects_viewer_signal_types(self, signal_type):
        with pytest.raises(ValidationError):
            self._runtime.validate_python({"session_id": SESSION, "type": signal_type, "payload": {}})

    def test_runtime_signal_requires_session_id(self):
        with pytest.raises(ValidationError):
            self._runtime.validate_python({"type": "prepared", "payload": {}})
        with pytest.raises(ValidationError):
            self._runtime.validate_python({"session_id": "not-a-uuid", "type": "prepared", "payload": {}})

    @pytest.mark.parametrize("state", ["connected", "streaming", "locked", "unlocked", "degraded", "restored"])
    def test_status_state_literal(self, state):
        body = self._runtime.validate_python({"session_id": SESSION, "type": "status", "payload": {"state": state}})
        assert body.payload.model_dump(exclude_unset=True) == {"state": state}

    @pytest.mark.parametrize("path", ["lan", "p2p"])
    def test_status_path_literal(self, path):
        payload = {"state": "streaming", "path": path}
        body = self._runtime.validate_python({"session_id": SESSION, "type": "status", "payload": payload})
        assert body.payload.model_dump(exclude_unset=True) == payload

    @pytest.mark.parametrize("payload", [
        {"state": "bogus"}, {}, {"state": "streaming", "path": "wan"}, {"state": "streaming", "path": 7},
        {"state": "Streaming"},
    ])
    def test_status_payload_shape_rejected_with_422_input(self, payload):
        with pytest.raises(ValidationError):
            self._runtime.validate_python({"session_id": SESSION, "type": "status", "payload": payload})

    @pytest.mark.parametrize("reason", [
        "runtime_stopped", "runtime_rejected", "same_machine", "machine_check_unknown",
        "connect_failed", "renewal_timeout", "resource_busy",
    ])
    def test_runtime_end_reason_literal(self, reason):
        payload = {"reason": reason}
        body = self._runtime.validate_python({"session_id": SESSION, "type": "end", "payload": payload})
        assert body.payload.model_dump(exclude_unset=True) == payload

    @pytest.mark.parametrize("payload", [{"reason": "viewer_closed"}, {"reason": "device_offline"},
                                          {"reason": "runtime_offline"}, {"reason": "nope"}, {}])
    def test_runtime_end_reason_outside_runtime_set_rejected(self, payload):
        with pytest.raises(ValidationError):
            self._runtime.validate_python({"session_id": SESSION, "type": "end", "payload": payload})

    @pytest.mark.parametrize("payload", [[], "sdp", 1, None])
    def test_payload_must_be_a_json_object(self, payload):
        with pytest.raises(ValidationError):
            self._viewer.validate_python({"type": "answer", "payload": payload})
        with pytest.raises(ValidationError):
            self._runtime.validate_python({"session_id": SESSION, "type": "offer", "payload": payload})

    def test_payload_missing_is_rejected(self):
        with pytest.raises(ValidationError):
            self._viewer.validate_python({"type": "start"})

    def test_payload_under_16kb_accepted(self):
        payload = {"sdp": "x" * 16000}
        body = self._viewer.validate_python({"type": "answer", "payload": payload})
        assert body.payload.model_dump(exclude_unset=True) == payload

    def test_payload_over_16kb_rejected(self):
        with pytest.raises(ValidationError):
            self._viewer.validate_python({"type": "answer", "payload": {"sdp": "x" * 17000}})

    def test_payload_limit_counts_serialized_bytes_not_characters(self):
        """6000 个汉字是 18000 字节、6000 个字符：按序列化后的字节数判超限"""
        with pytest.raises(ValidationError):
            self._runtime.validate_python({"session_id": SESSION, "type": "offer", "payload": {"sdp": "画" * 6000}})
        ok = {"sdp": "画" * 5000}
        body = self._runtime.validate_python({"session_id": SESSION, "type": "offer", "payload": ok})
        assert body.payload.model_dump(exclude_unset=True) == ok

    @pytest.mark.parametrize("value", NON_FINITE)
    def test_payload_with_non_finite_number_is_rejected(self, value):
        """Python 的 JSON 解析接受 NaN / Infinity / -Infinity，PostgreSQL 的 json 却不接受：
        必须在请求模型这一层就判为校验错误，不能放行到写库那一步（嵌套在列表、字典里的也一样）"""
        for adapter, body in (
            (self._viewer, {"type": "answer", "payload": {"sdp": 1.5, "x": value}}),
            (self._viewer, {"type": "candidate", "payload": {"candidates": [value]}}),
            (self._runtime, {"session_id": SESSION, "type": "offer", "payload": {"sdp": value}}),
            (self._runtime, {"session_id": SESSION, "type": "status",
                             "payload": {"state": "streaming", "stats": {"rtt": value}}}),
        ):
            with pytest.raises(ValidationError) as caught:
                adapter.validate_python(body)
            errors = caught.value.errors()
            assert errors[0]["type"] == "value_error" and errors[0]["loc"][-1] == "payload"
            assert "not JSON compliant" in errors[0]["msg"]

    def test_payload_with_finite_numbers_is_accepted(self):
        payload = {"fps": 15, "ratio": 0.5, "big": 9007199254740993, "neg": -2, "exp": 1e-7, "zero": 0.0,
                   "nested": [-0.0, {"port": 3478}, [1, 2.5]]}
        viewer = self._viewer.validate_python({"type": "answer", "payload": payload})
        runtime = self._runtime.validate_python({"session_id": SESSION, "type": "offer", "payload": payload})
        assert viewer.payload.model_dump(exclude_unset=True) == payload
        assert runtime.payload.model_dump(exclude_unset=True) == payload

    def test_payload_extra_keys_are_relayed_verbatim(self):
        payload = {"state": "degraded", "fps": 5, "note": "任务运行中", "nested": {"a": [1, 2]}}
        body = self._runtime.validate_python({"session_id": SESSION, "type": "status", "payload": payload})
        assert body.payload.model_dump(exclude_unset=True) == payload

    def test_runtime_session_request_after_seq(self):
        request = screen_stream.RuntimeSessionRequest(session_id=SESSION, after_seq=12)
        assert (str(request.session_id), request.after_seq) == (SESSION, 12)
        assert screen_stream.RuntimeSessionRequest(session_id=SESSION).after_seq == 0
        with pytest.raises(ValidationError):
            screen_stream.RuntimeSessionRequest(session_id=SESSION, after_seq=-1)
        with pytest.raises(ValidationError):
            screen_stream.RuntimeSessionRequest(session_id=SESSION, after_seq="abc")


# ---------------------------------------------------------------------------
# 数据访问层（mock 连接）
# ---------------------------------------------------------------------------


class _FakeCursor:
    """按语句给出结果集的 mock 游标：每条语句返回的行依次取自 results（每项是一个行列表）。

    每条画面事务的第一条语句是 SET LOCAL（给等锁设上限），它不返回行，所以不占 results 的位置。
    fail_on 写明「第几条语句抛什么异常」，用来模拟数据库到点取消语句；语句序号从 0 数起。
    """

    def __init__(self, results, executed, fail_on):
        self._results = [list(rows) for rows in results]
        self._executed = executed
        self._fail_on = fail_on
        self._rows = []

    def execute(self, sql, params=None):
        self._executed.append((sql, params))
        error = self._fail_on.get(len(self._executed) - 1)
        if error is not None:
            raise error
        if sql.startswith("SET LOCAL"):
            self._rows = []
            return
        self._rows = self._results.pop(0) if self._results else []

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


def _patch_conn(results, fail_on=None):
    """替换模块里的数据库连接。results 按执行顺序给出每条语句返回的行（每项是一个行列表）；
    同时记录每条语句的 SQL 与参数（含开头的 SET LOCAL），以及 commit 的次数。
    fail_on 是 {语句序号: 要抛的异常}，用来模拟数据库在时限到点时取消语句"""
    executed, commits = [], []
    cursor = _FakeCursor(results, executed, fail_on or {})
    conn = SimpleNamespace(cursor=lambda *a, **k: cursor, commit=lambda: commits.append(True), rollback=lambda: None)

    @contextmanager
    def fake_get_db_connection():
        yield conn

    return executed, commits, patch("src.local_tools.screen_stream.get_db_connection", fake_get_db_connection)


def _updates(executed):
    return [(sql, params) for sql, params in executed if sql.lstrip().startswith("UPDATE")]


def _stored(params):
    """UPDATE 参数里的 signals 实际写入的列表"""
    return params[7].adapted


VIEWER_SCOPE = ("tenant_id = %s AND user_id = %s AND device_id = %s", (TENANT, USER, DEVICE))
RUNTIME_SCOPE = ("tenant_id = %s AND device_id = %s", (TENANT, DEVICE))


class TestScopes:
    def test_viewer_scope_matches_tenant_user_and_device(self):
        assert screen_stream.viewer_scope(TENANT, USER, DEVICE) == VIEWER_SCOPE

    def test_runtime_scope_matches_tenant_and_device(self):
        assert screen_stream.runtime_scope(TENANT, DEVICE) == RUNTIME_SCOPE


class TestCreateSessionWithMockedConnection:
    @staticmethod
    def _device(**overrides):
        device = {"last_seen_at": NOW, "capabilities_json": {"capabilities": ["screen_stream_v1"]}, "db_now": NOW}
        device.update(overrides)
        return device

    def _create(self, device=None, active_sessions=()):
        """语句依次是：设备行、该设备的未结束会话、（每条超时会话一次）收敛 UPDATE、INSERT ... RETURNING id"""
        results = [[self._device() if device is None else device], list(active_sessions)]
        results.extend([[] for _ in active_sessions])
        results.append([{"id": SESSION}])
        executed, commits, conn_patch = _patch_conn(results)
        with conn_patch:
            result = screen_stream.create_session(TENANT, USER, DEVICE, device_offline_seconds=ONLINE)
        return result, executed, commits

    def _create_rejected(self, code, device_rows):
        executed, commits, conn_patch = _patch_conn([device_rows])
        with conn_patch, pytest.raises(screen_stream.ScreenStreamError) as caught:
            screen_stream.create_session(TENANT, USER, DEVICE, device_offline_seconds=ONLINE)
        assert caught.value.code == code
        return executed, commits

    def test_success_locks_device_and_inserts_pending_session(self):
        result, executed, commits = self._create()
        assert result == {"id": SESSION, "state": "pending"}
        select_sql, select_params = executed[1]
        assert "FOR UPDATE" in select_sql and "status = 'active'" in select_sql
        # 库时钟取 NOW()::timestamp：与 TIMESTAMP 列同为不带时区的值，才能在 Python 里比较
        assert "NOW()::timestamp AS db_now" in select_sql
        assert select_params == (DEVICE, TENANT, USER)
        insert_sql, insert_params = executed[3]
        assert insert_sql.lstrip().startswith("INSERT") and "RETURNING id" in insert_sql
        assert "'pending'" in insert_sql and "viewer_seen_at" in insert_sql and "NOW()" in insert_sql
        assert "runtime_seen_at" not in insert_sql
        assert insert_params == (TENANT, USER, DEVICE)
        assert commits == [True]

    def test_device_not_found(self):
        executed, commits = self._create_rejected("DEVICE_NOT_FOUND", [])
        assert len(executed) == 2 and commits == []

    @pytest.mark.parametrize("last_seen_at", [NOW - timedelta(seconds=30, microseconds=1), None])
    def test_device_offline_including_never_seen(self, last_seen_at):
        executed, commits = self._create_rejected(
            "DEVICE_OFFLINE", [self._device(last_seen_at=last_seen_at)])
        assert len(executed) == 2 and commits == []

    def test_device_exactly_at_online_threshold_is_online(self):
        result, _, _ = self._create(device=self._device(last_seen_at=NOW - timedelta(seconds=30)))
        assert result["state"] == "pending"

    def test_online_threshold_comes_from_caller(self):
        """在线阈值由路由层传入（api.ONLINE_THRESHOLD_SECONDS），模块里没有第二份缺省值"""
        aged = self._device(last_seen_at=NOW - timedelta(seconds=45))
        self._create_rejected("DEVICE_OFFLINE", [aged])
        executed, _, conn_patch = _patch_conn([[aged], [], [{"id": SESSION}]])
        with conn_patch:
            screen_stream.create_session(TENANT, USER, DEVICE, device_offline_seconds=120)

    @pytest.mark.parametrize(("overrides", "code"), [
        ({"last_seen_at": None}, "DEVICE_OFFLINE"),
        ({"capabilities_json": None}, "SCREEN_STREAM_UNSUPPORTED"),
        ({"capabilities_json": {"capabilities": ["local_v2"]}}, "SCREEN_STREAM_UNSUPPORTED"),
    ])
    def test_rejections_precede_the_active_session_check(self, overrides, code):
        """离线与不支持先于「已有未结束会话」判断：不会去查会话，也没有任何写入"""
        executed, commits = self._create_rejected(code, [self._device(**overrides)])
        assert len(executed) == 2 and commits == []

    def test_active_session_blocks_creation(self):
        active = _row(state="streaming", started_at=NOW, viewer_seen_at=NOW)
        with pytest.raises(screen_stream.ScreenStreamError) as caught:
            self._create(active_sessions=[active])
        assert caught.value.code == "SCREEN_SESSION_ACTIVE"

    def test_active_session_lookup_is_scoped_to_the_device(self):
        """未结束会话按设备查（走部分索引），不带 tenant/user 条件：设备归属已在设备行上确认"""
        with pytest.raises(screen_stream.ScreenStreamError):
            self._create(active_sessions=[_row(state="pending")])
        executed, _, conn_patch = _patch_conn([[self._device()], [_row(state="pending")]])
        with conn_patch, pytest.raises(screen_stream.ScreenStreamError):
            screen_stream.create_session(TENANT, USER, DEVICE, device_offline_seconds=ONLINE)
        session_sql, session_params = executed[2]
        assert "device_id = %s AND state <> 'ended'" in session_sql and "FOR UPDATE" in session_sql
        assert "tenant_id" not in session_sql and "user_id" not in session_sql
        # 不带排序：规划器才能直接用部分索引，不必扫过这台设备的全部历史会话
        assert "ORDER BY" not in session_sql
        assert session_params == (DEVICE,)

    def test_timed_out_session_is_converged_then_new_one_created(self):
        stale = _row(state="pending", created_at=NOW - timedelta(seconds=31))
        result, executed, commits = self._create(active_sessions=[stale])
        assert result == {"id": SESSION, "state": "pending"}
        update_sql, update_params = _updates(executed)[0]
        assert "viewer_seen_at" not in update_sql and "runtime_seen_at" not in update_sql
        assert update_params[0] == "ended" and update_params[1] == "device_offline"
        assert update_params[6] == NOW and _stored(update_params) == []
        assert executed[-1][0].lstrip().startswith("INSERT")
        assert commits == [True]


class TestLoadSessionWithMockedConnection:
    STORED = [
        {"seq": 1, "dir": "rt", "type": "prepared", "payload": {}},
        {"seq": 2, "dir": "web", "type": "start", "payload": {}},
        {"seq": 3, "dir": "rt", "type": "status", "payload": {"state": "connected"}},
        {"seq": 4, "dir": "web", "type": "candidate", "payload": {"candidate": "host"}},
    ]

    @staticmethod
    def _live(**overrides):
        """一个双方都在时限内访问过的会话行"""
        row = _row(state="verifying", viewer_seen_at=NOW, runtime_seen_at=NOW)
        row.update(overrides)
        return row

    def test_viewer_only_gets_runtime_signals_and_only_refreshes_viewer_seen_at(self):
        executed, commits, conn_patch = _patch_conn([[self._live(signals=self.STORED)]])
        with conn_patch:
            result = screen_stream.load_session(VIEWER_SCOPE, SESSION, viewer=True, device_offline_seconds=ONLINE)
        assert result == {"session": {"id": SESSION, "state": "verifying", "end_reason": None},
                          "signals": [{"seq": 1, "type": "prepared", "payload": {}},
                                      {"seq": 3, "type": "status", "payload": {"state": "connected"}}]}
        select_sql, select_params = executed[1]
        assert "FOR UPDATE OF s" in select_sql and "NOW()::timestamp AS db_now" in select_sql
        assert "last_seen_at" in select_sql
        assert select_params == (SESSION, TENANT, USER, DEVICE)
        assert len(executed) == 3
        touch_sql, touch_params = _updates(executed)[0]
        assert touch_sql == "UPDATE local_tool_screen_sessions SET viewer_seen_at = NOW() WHERE id = %s"
        assert touch_params == (SESSION,)
        assert commits == [True]

    def test_runtime_only_gets_viewer_signals_and_only_refreshes_runtime_seen_at(self):
        executed, commits, conn_patch = _patch_conn([[self._live(state="prepared", signals=self.STORED)]])
        with conn_patch:
            result = screen_stream.load_session(RUNTIME_SCOPE, SESSION, viewer=False, device_offline_seconds=ONLINE)
        assert result == {"session": {"id": SESSION, "state": "prepared", "end_reason": None},
                          "signals": [{"seq": 2, "type": "start", "payload": {}},
                                      {"seq": 4, "type": "candidate", "payload": {"candidate": "host"}}]}
        assert executed[1][1] == (SESSION, TENANT, DEVICE)
        assert len(executed) == 3
        assert _updates(executed)[0] == ("UPDATE local_tool_screen_sessions SET runtime_seen_at = NOW() WHERE id = %s",
                                         (SESSION,))
        assert commits == [True]

    @pytest.mark.parametrize(("after_seq", "expected"), [(0, [1, 3]), (1, [3]), (3, []), (99, [])])
    def test_after_seq_cursor_only_returns_newer_signals(self, after_seq, expected):
        executed, _, conn_patch = _patch_conn([[self._live(signals=self.STORED)]])
        with conn_patch:
            result = screen_stream.load_session(VIEWER_SCOPE, SESSION, viewer=True, after_seq=after_seq,
                                                device_offline_seconds=ONLINE)
        assert [s["seq"] for s in result["signals"]] == expected

    def test_missing_or_foreign_session_returns_none_without_writes(self):
        executed, commits, conn_patch = _patch_conn([[]])
        with conn_patch:
            result = screen_stream.load_session(VIEWER_SCOPE, SESSION, viewer=True, device_offline_seconds=ONLINE)
        assert result is None
        assert len(executed) == 2 and commits == []

    def test_timed_out_session_is_ended_before_being_returned(self):
        stale = self._live(state="connecting", runtime_seen_at=NOW - timedelta(seconds=31), signals=self.STORED)
        executed, commits, conn_patch = _patch_conn([[stale]])
        with conn_patch:
            result = screen_stream.load_session(VIEWER_SCOPE, SESSION, viewer=True, device_offline_seconds=ONLINE)
        assert result == {"session": {"id": SESSION, "state": "ended", "end_reason": "connect_failed"}, "signals": []}
        # 收敛只写这一条 UPDATE，之后不再刷新访问时间
        assert len(executed) == 3
        _, params = _updates(executed)[0]
        assert params[:3] == ("ended", "connect_failed", None)
        assert params[6] == NOW and _stored(params) == []
        assert commits == [True]

    def test_ended_session_is_returned_as_is_without_writes(self):
        """已经结束的会话读取时什么都不写：不收敛，也不刷新访问时间"""
        ended = _row(state="ended", end_reason="viewer_closed", ended_at=NOW, signals=[])
        executed, commits, conn_patch = _patch_conn([[ended]])
        with conn_patch:
            result = screen_stream.load_session(VIEWER_SCOPE, SESSION, viewer=True, device_offline_seconds=ONLINE)
        assert result == {"session": {"id": SESSION, "state": "ended", "end_reason": "viewer_closed"}, "signals": []}
        assert len(executed) == 2 and commits == [True]


class TestAppendSignalWithMockedConnection:
    def test_appends_signal_applies_transition_and_refreshes_caller(self):
        payload = {"state": "streaming", "path": "p2p"}
        row = _row(state="verifying", runtime_seen_at=NOW, signals=_signals(6, "web"))
        executed, commits, conn_patch = _patch_conn([[row]])
        with conn_patch:
            result = screen_stream.append_signal(RUNTIME_SCOPE, SESSION, "rt", "status", payload,
                                                 device_offline_seconds=ONLINE)
        assert result == {"seq": 7, "session": {"id": SESSION, "state": "streaming", "end_reason": None}}
        assert executed[1][1] == (SESSION, TENANT, DEVICE)
        update_sql, params = _updates(executed)[0]
        assert "runtime_seen_at = NOW()" in update_sql and "viewer_seen_at" not in update_sql
        assert params[0] == "streaming" and params[3] == "p2p" and params[5] == NOW
        stored = _stored(params)
        assert len(stored) == 7 and stored[-1] == {"seq": 7, "dir": "rt", "type": "status", "payload": payload}
        assert commits == [True]

    def test_end_writes_reason_and_clears_signals_without_storing_the_end_signal(self):
        row = _row(state="verifying", runtime_seen_at=NOW, signals=_signals(4))
        executed, commits, conn_patch = _patch_conn([[row]])
        with conn_patch:
            result = screen_stream.append_signal(RUNTIME_SCOPE, SESSION, "rt", "end", {"reason": "same_machine"},
                                                 device_offline_seconds=ONLINE)
        assert result == {"seq": None, "session": {"id": SESSION, "state": "ended", "end_reason": "same_machine"}}
        _, params = _updates(executed)[0]
        assert params[:3] == ("ended", "same_machine", "same")
        assert params[6] == NOW and _stored(params) == []
        assert commits == [True]

    def test_idempotent_end_writes_nothing(self):
        ended = _row(state="ended", end_reason="viewer_closed", ended_at=NOW)
        executed, commits, conn_patch = _patch_conn([[ended]])
        with conn_patch:
            result = screen_stream.append_signal(VIEWER_SCOPE, SESSION, "web", "end", {}, device_offline_seconds=ONLINE)
        assert result == {"seq": None, "session": {"id": SESSION, "state": "ended", "end_reason": "viewer_closed"}}
        assert _updates(executed) == []

    def test_signal_limit_is_raised_without_writes(self):
        row = _row(state="connecting", runtime_seen_at=NOW, signals=_signals(128, "web"))
        executed, commits, conn_patch = _patch_conn([[row]])
        with pytest.raises(screen_stream.ScreenStreamError) as caught:
            with conn_patch:
                screen_stream.append_signal(RUNTIME_SCOPE, SESSION, "rt", "candidate", {}, device_offline_seconds=ONLINE)
        assert caught.value.code == "SCREEN_SIGNAL_LIMIT"
        assert _updates(executed) == []

    def test_rejected_signal_persists_the_timeout_convergence(self):
        """信令被拒之前，刚做的超时收敛已经落库：之后没人再读，会话也不会一直挂着未结束"""
        stale = _row(state="connecting", runtime_seen_at=NOW - timedelta(seconds=31))
        executed, commits, conn_patch = _patch_conn([[stale]])
        with pytest.raises(screen_stream.ScreenStreamError) as caught:
            with conn_patch:
                screen_stream.append_signal(RUNTIME_SCOPE, SESSION, "rt", "offer", {"sdp": "v=0"},
                                            device_offline_seconds=ONLINE)
        assert caught.value.code == "SCREEN_SESSION_ENDED"
        _, params = _updates(executed)[0]
        assert params[:2] == ("ended", "connect_failed") and _stored(params) == []
        assert commits == [True]

    def test_end_on_a_session_just_converged_reports_the_timeout_reason(self):
        """超时会话收到 end：幂等成功，结束原因是超时收敛写下的那个，不改任何数据"""
        stale = _row(state="prepared", viewer_seen_at=NOW - timedelta(seconds=61))
        executed, commits, conn_patch = _patch_conn([[stale]])
        with conn_patch:
            result = screen_stream.append_signal(RUNTIME_SCOPE, SESSION, "rt", "end", {"reason": "runtime_stopped"},
                                                 device_offline_seconds=ONLINE)
        assert result == {"seq": None, "session": {"id": SESSION, "state": "ended", "end_reason": "viewer_closed"}}
        assert len(_updates(executed)) == 1
        assert commits == [True]

    def test_missing_or_foreign_session_returns_none_without_writes(self):
        executed, commits, conn_patch = _patch_conn([[]])
        with conn_patch:
            result = screen_stream.append_signal(RUNTIME_SCOPE, SESSION, "rt", "prepared", {}, device_offline_seconds=ONLINE)
        assert result is None
        assert len(executed) == 2 and commits == []


class TestHeartbeatNoticeWithMockedConnection:
    def _notice(self, results, device_offline_seconds=ONLINE):
        executed, commits, conn_patch = _patch_conn(results)
        with conn_patch:
            result = screen_stream.heartbeat_notice(DEVICE, device_offline_seconds=device_offline_seconds)
        return result, executed, commits

    def test_no_relevant_session_is_a_single_indexed_read(self):
        result, executed, commits = self._notice([[]])
        assert result is None
        assert len(executed) == 3 and commits == []
        select_sql, params = executed[2]
        assert "FOR UPDATE" not in select_sql
        assert "device_id = %s" in select_sql and "ORDER BY created_at DESC" in select_sql and "LIMIT 1" in select_sql
        assert "state <> 'ended'" in select_sql and "started_at IS NOT NULL" in select_sql
        assert params[:2] == (DEVICE, DEVICE) and params[2] == 30

    def test_pending_session_is_notified_in_a_single_read(self):
        result, executed, commits = self._notice([[_row(state="pending")]])
        assert result == {"id": SESSION, "state": "pending"}
        assert len(executed) == 3 and commits == []

    @pytest.mark.parametrize("state", ["prepared", "connecting", "verifying"])
    def test_handshake_states_are_not_notified(self, state):
        """建连阶段 Runtime 自己在轮询信令，心跳不带会话"""
        live = _row(state=state, viewer_seen_at=NOW, runtime_seen_at=NOW)
        result, executed, commits = self._notice([[live]])
        assert result is None
        assert len(executed) == 3 and commits == []

    def test_streaming_session_is_notified_and_renews_runtime_seen_at(self):
        live = _row(state="streaming", started_at=NOW, viewer_seen_at=NOW)
        result, executed, commits = self._notice([[live]])
        assert result == {"id": SESSION, "state": "streaming"}
        assert len(executed) == 4 and commits == [True]
        update_sql, params = _updates(executed)[0]
        assert "runtime_seen_at = NOW()" in update_sql and "state = 'streaming'" in update_sql
        assert params == (SESSION,)

    def test_streaming_session_with_gone_viewer_converges_to_ended_notice(self):
        stale = _row(state="streaming", started_at=NOW, viewer_seen_at=NOW - timedelta(seconds=31))
        result, executed, commits = self._notice([[stale]])
        assert result == {"id": SESSION, "state": "ended"}
        assert len(executed) == 4 and commits == [True]
        _, params = _updates(executed)[0]
        assert params[:3] == ("viewer_closed", SESSION, "streaming")

    def test_unanswered_pending_session_converges_to_ended_notice(self):
        stale = _row(state="pending", created_at=NOW - timedelta(seconds=31))
        result, executed, commits = self._notice([[stale]])
        assert result == {"id": SESSION, "state": "ended"}
        assert len(executed) == 4 and commits == [True]
        _, params = _updates(executed)[0]
        assert params[:3] == ("device_offline", SESSION, "pending")

    def test_streaming_session_of_offline_device_uses_passed_threshold(self):
        row = _row(state="streaming", started_at=NOW, viewer_seen_at=NOW,
                   device_last_seen_at=NOW - timedelta(seconds=45))
        result, executed, _ = self._notice([[row]], device_offline_seconds=120)
        assert result == {"id": SESSION, "state": "streaming"}
        result, executed, _ = self._notice([[row]], device_offline_seconds=ONLINE)
        assert result == {"id": SESSION, "state": "ended"}
        assert _updates(executed)[0][1][:3] == ("runtime_offline", SESSION, "streaming")

    def test_recently_ended_session_is_notified_without_writes(self):
        row = _row(state="ended", end_reason="viewer_closed", started_at=NOW, ended_at=NOW - timedelta(seconds=29))
        result, executed, commits = self._notice([[row]])
        assert result == {"id": SESSION, "state": "ended"}
        assert len(executed) == 3 and commits == []


class TestDatabaseWaitLimits:
    """画面这一路的 SQL 在数据库层的等待上限（设计 4.4：画面出问题不拖累设备心跳和任务）。

    应用层 asyncio.wait_for 到 0.5 秒放弃等待时，线程里的 SQL 并不会停，连接也不归还；所以每条画面事务的
    第一条语句是 SET LOCAL，让 PostgreSQL 到点取消语句。SET LOCAL 只在当前事务内有效，连接归还后不影响别人。
    """

    # PostgreSQL 的 SET 一条语句只能设一个参数（写成「a = 400, b = 400」是语法错误），所以是两条
    HEARTBEAT_LOCK_LIMIT = "SET LOCAL lock_timeout = 400"
    HEARTBEAT_STATEMENT_LIMIT = "SET LOCAL statement_timeout = 400"
    SESSION_LIMITS = "SET LOCAL lock_timeout = 1000"

    def test_limit_constants(self):
        assert screen_stream.HEARTBEAT_TIMEOUT_MS == 400
        assert screen_stream.SESSION_LOCK_TIMEOUT_MS == 1000
        assert (screen_stream.PG_LOCK_NOT_AVAILABLE, screen_stream.PG_QUERY_CANCELED) == ("55P03", "57014")

    def test_heartbeat_notice_sets_both_limits_first(self):
        """心跳里的查询：等锁和整条语句都限时 400 毫秒，比应用层的 0.5 秒略短"""
        executed, commits, conn_patch = _patch_conn([[]])
        with conn_patch:
            result = screen_stream.heartbeat_notice(DEVICE, device_offline_seconds=ONLINE)
        assert result is None
        assert executed[:2] == [(self.HEARTBEAT_LOCK_LIMIT, None), (self.HEARTBEAT_STATEMENT_LIMIT, None)]
        assert len(executed) == 3

    def test_create_session_sets_lock_timeout_first(self):
        executed, commits, conn_patch = _patch_conn([[]])
        with conn_patch, pytest.raises(screen_stream.ScreenStreamError) as caught:
            screen_stream.create_session(TENANT, USER, DEVICE, device_offline_seconds=ONLINE)
        assert caught.value.code == "DEVICE_NOT_FOUND"
        assert executed[0] == (self.SESSION_LIMITS, None)
        assert executed[1][1] == (DEVICE, TENANT, USER)
        assert commits == []

    def test_load_session_sets_lock_timeout_first(self):
        executed, commits, conn_patch = _patch_conn([[]])
        with conn_patch:
            result = screen_stream.load_session(VIEWER_SCOPE, SESSION, viewer=True, device_offline_seconds=ONLINE)
        assert result is None
        assert executed[0] == (self.SESSION_LIMITS, None)
        assert executed[1][1] == (SESSION, TENANT, USER, DEVICE)
        assert len(executed) == 2 and commits == []

    def test_append_signal_sets_lock_timeout_first(self):
        executed, commits, conn_patch = _patch_conn([[]])
        with conn_patch:
            result = screen_stream.append_signal(RUNTIME_SCOPE, SESSION, "rt", "candidate", {}, device_offline_seconds=ONLINE)
        assert result is None
        assert executed[0] == (self.SESSION_LIMITS, None)
        assert executed[1][1] == (SESSION, TENANT, DEVICE)
        assert len(executed) == 2 and commits == []

    def test_session_paths_only_limit_lock_wait(self):
        """三条会话路径只限等锁；整条语句的时限仍用连接池的默认值"""
        for sql in (self.SESSION_LIMITS, screen_stream._SESSION_LOCK_TIMEOUT_SQL):
            assert sql == "SET LOCAL lock_timeout = 1000"
            assert "statement_timeout" not in sql and sql.startswith("SET LOCAL ")

    BUSY = ("SCREEN_STREAM_BUSY", 503, "实时画面暂时繁忙，请稍后重试")

    def test_load_session_lock_wait_timeout_is_busy(self):
        executed, commits, conn_patch = _patch_conn([[]], fail_on={1: LockTimeout()})
        with conn_patch, pytest.raises(screen_stream.ScreenStreamError) as caught:
            screen_stream.load_session(VIEWER_SCOPE, SESSION, viewer=True, device_offline_seconds=ONLINE)
        assert (caught.value.code, caught.value.status, caught.value.message) == self.BUSY
        assert commits == []

    def test_append_signal_lock_wait_timeout_is_busy(self):
        executed, commits, conn_patch = _patch_conn([[]], fail_on={1: LockTimeout()})
        with conn_patch, pytest.raises(screen_stream.ScreenStreamError) as caught:
            screen_stream.append_signal(RUNTIME_SCOPE, SESSION, "rt", "candidate", {}, device_offline_seconds=ONLINE)
        assert (caught.value.code, caught.value.status, caught.value.message) == self.BUSY
        assert commits == []

    @pytest.mark.parametrize(("statement", "results"), [
        (1, [[]]),                                   # 等设备行的锁
        (2, [[TestCreateSessionWithMockedConnection._device()]]),  # 设备行拿到了，等未结束会话行的锁
    ], ids=["device-row", "session-row"])
    def test_create_session_lock_wait_timeout_is_busy(self, statement, results):
        executed, commits, conn_patch = _patch_conn(results, fail_on={statement: LockTimeout()})
        with conn_patch, pytest.raises(screen_stream.ScreenStreamError) as caught:
            screen_stream.create_session(TENANT, USER, DEVICE, device_offline_seconds=ONLINE)
        assert (caught.value.code, caught.value.status, caught.value.message) == self.BUSY
        assert commits == []
        assert not [sql for sql, _ in executed if sql.lstrip().startswith(("INSERT", "UPDATE"))]

    @pytest.mark.parametrize("error", [
        pytest.param(psycopg2.OperationalError("connection pool exhausted"), id="pool-exhausted"),
        pytest.param(StatementTimeout(), id="statement-timeout"),
        pytest.param(pg_errors.UndefinedTable("relation does not exist"), id="undefined-table"),
    ])
    def test_other_database_errors_are_not_disguised_as_busy(self, error):
        """只有等锁超时（55P03）才是「稍后重试」；其余数据库异常原样抛出，不当成繁忙"""
        executed, commits, conn_patch = _patch_conn([[]], fail_on={1: error})
        with conn_patch, pytest.raises(type(error)) as caught:
            screen_stream.load_session(VIEWER_SCOPE, SESSION, viewer=True, device_offline_seconds=ONLINE)
        assert caught.value is error
        assert commits == []

    @pytest.mark.parametrize(("statement", "has_row"), [
        (2, False),   # 查询那一条被取消（前两条是 SET LOCAL）
        (3, True),    # 续期 UPDATE 等行锁被取消：真实被挡住的位置
    ])
    @pytest.mark.parametrize(("error_class", "message"), DATABASE_CANCELS)
    def test_heartbeat_notice_lets_database_cancellation_through(self, statement, has_row, error_class, message):
        """心跳路径不做转换：数据库取消语句的异常照常抛出，由路由层按「查询超时」处理；没有提交"""
        error = error_class(message)
        results = [[_row(state="streaming", started_at=NOW, viewer_seen_at=NOW)]] if has_row else [[]]
        executed, commits, conn_patch = _patch_conn(results, fail_on={statement: error})
        with conn_patch, pytest.raises(error_class) as caught:
            screen_stream.heartbeat_notice(DEVICE, device_offline_seconds=ONLINE)
        assert caught.value is error
        assert executed[:2] == [(self.HEARTBEAT_LOCK_LIMIT, None), (self.HEARTBEAT_STATEMENT_LIMIT, None)]
        assert commits == []

    def test_is_database_timeout(self):
        """路由层据此区分「数据库到点取消了语句」和其他异常：看 SQLSTATE（55P03、57014），不看异常类"""
        assert screen_stream.is_database_timeout(LockTimeout()) is True
        assert screen_stream.is_database_timeout(StatementTimeout()) is True
        for other in (psycopg2.OperationalError("connection pool exhausted"),
                      pg_errors.UndefinedTable("relation does not exist"),
                      pg_errors.LockNotAvailable("not raised by PostgreSQL"),   # 没带 SQLSTATE
                      asyncio.TimeoutError(), RuntimeError("boom")):
            assert screen_stream.is_database_timeout(other) is False


class TestStunUrlsConfig:
    """共同契约 2.25：服务端只下发 STUN 服务器地址。配置模型把不是 STUN 的条目丢掉并告警，
    地址本身不进日志（TURN 地址里可能带凭据）；也不因为这种只影响画面功能的配置错误让服务起不来"""

    DROPPED = [
        "turn:relay.example.com:3478", "turns:relay.example.net:5349", "TURN:relay.example.org:3478",
        "Turns:relay.example.org:5349", "relay.example.com", "relay.example.com:3478", "stun.example.com",
        "stun.example.com:3478", "stun", "stuns", "", " ", 3478, None, ["stun:stun.example.net"],
        {"url": "stun:stun.example.net"},
    ]

    @staticmethod
    def _capture_warnings():
        records = []
        handler = logger.add(lambda message: records.append(message.record))
        return records, handler

    def test_stun_and_stuns_are_kept_in_order(self):
        urls = ["stuns:stuns.example.net", "STUN:stun.example.net:3478", "Stun:mixed.example.net:3478",
                "stun:stun.example.org", "stuns:[2001:db8::1]:3478"]
        assert ScreenStreamConfig(stun_urls=urls).stun_urls == urls

    def test_anything_else_is_dropped(self):
        kept = ["stuns:first.example.net", "stun:stun.example.net:3478"]
        assert ScreenStreamConfig(stun_urls=[kept[0], *self.DROPPED, kept[1]]).stun_urls == kept
        assert ScreenStreamConfig(stun_urls=list(self.DROPPED)).stun_urls == []
        assert ScreenStreamConfig().stun_urls == []

    def test_dropped_entries_are_warned_about_without_their_addresses(self):
        records, handler = self._capture_warnings()
        try:
            config = ScreenStreamConfig(stun_urls=[
                "turn:demo-user:demo-pass@relay.example.com:3478",
                "stun:stun.example.net:3478",
                "turns:relay.example.net:5349",
                "relay.example.com",
                3478,
            ])
        finally:
            logger.remove(handler)
        assert config.stun_urls == ["stun:stun.example.net:3478"]
        warnings = [r for r in records if "stun_urls" in r["message"]]
        assert len(warnings) == 1
        assert warnings[0]["level"].name == "WARNING"
        text = warnings[0]["message"]
        assert "4 条" in text
        for address in ("relay.example.com", "relay.example.net", "demo-user", "demo-pass", "stun.example.net", "3478"):
            assert address not in text

    def test_no_warning_when_every_entry_is_stun(self):
        records, handler = self._capture_warnings()
        try:
            ScreenStreamConfig(stun_urls=["stun:stun.example.net:3478", "stuns:stuns.example.net"])
            ScreenStreamConfig()
        finally:
            logger.remove(handler)
        assert not [r for r in records if "stun_urls" in r["message"]]

    def test_value_that_is_not_a_list_is_left_to_pydantic(self):
        """校验器只过滤列表里的条目。整个值不是列表是结构错误，照常由 pydantic 报出来
        （和其他配置项一致），不会被当成一串字符悄悄丢光"""
        for value in ("stun:stun.example.net", None, 3478):
            with pytest.raises(ValidationError):
                ScreenStreamConfig(stun_urls=value)


# ---------------------------------------------------------------------------
# 心跳响应
# ---------------------------------------------------------------------------


def _client(device=None, raise_server_exceptions=True):
    """只挂本地工具路由的最小应用：登录态与设备鉴权换成固定身份，不导入 src.main"""
    app = FastAPI()
    app.include_router(local_tools_api.router)

    @app.middleware("http")
    async def _tenant_context(request, call_next):
        request.state.tenant_id = TENANT
        return await call_next(request)

    app.dependency_overrides[local_tools_api._require_device] = lambda: device or {"id": DEVICE, "tenant_id": TENANT}
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _as_user(_request):
    return {"user_id": USER}


def _heartbeat(client):
    return client.post("/api/local-tools/runtime/heartbeat", json={})


class TestHeartbeatScreenNotice:
    def test_enabled_with_pending_session_carries_both_screen_fields(self):
        notice = {"id": SESSION, "state": "pending"}
        with patch.object(local_tools_api.repository, "touch_device_seen",
                          lambda *a, **k: {"selected": True}), \
             patch.object(screen_stream, "heartbeat_notice", return_value=notice) as query, \
             patch.object(local_tools_api, "get_current_user", _as_user):
            body = _heartbeat(_client()).json()
        assert body == {"success": True, "selected": True, "server_time": body["server_time"], "parallel_claim": True,
                        "screen_stream": {"version": 1}, "screen_session": {"id": SESSION, "state": "pending"}}
        query.assert_called_once_with(DEVICE, device_offline_seconds=local_tools_api.ONLINE_THRESHOLD_SECONDS)

    def test_enabled_without_session_only_carries_support_marker(self):
        with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": False}), \
             patch.object(screen_stream, "heartbeat_notice", return_value=None), \
             patch.object(local_tools_api, "get_current_user", _as_user):
            body = _heartbeat(_client()).json()
        assert body == {"success": True, "selected": False, "server_time": body["server_time"], "parallel_claim": True,
                        "screen_stream": {"version": 1}}

    def test_missing_plugin_manifests_field_is_preserved(self):
        """原有按需字段不受画面扩展影响"""
        with patch.object(local_tools_api.repository, "touch_device_seen",
                          lambda *a, **k: {"selected": True, "missing_plugin_manifests": ["wecom"]}), \
             patch.object(screen_stream, "heartbeat_notice", return_value=None), \
             patch.object(local_tools_api, "get_current_user", _as_user):
            body = _heartbeat(_client()).json()
        assert body["missing_plugin_manifests"] == ["wecom"]
        assert set(body) == {"success", "selected", "server_time", "parallel_claim",
                             "missing_plugin_manifests", "screen_stream"}

    def test_disabled_carries_no_screen_fields_and_does_not_query(self):
        def _must_not_query(*args, **kwargs):
            raise AssertionError("功能未启用时不应查询画面会话")

        with patch.object(settings.screen_stream, "enabled", False), \
             patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": True}), \
             patch.object(screen_stream, "heartbeat_notice", _must_not_query), \
             patch.object(local_tools_api, "get_current_user", _as_user):
            body = _heartbeat(_client()).json()
        assert set(body) == {"success", "selected", "server_time", "parallel_claim"}
        assert body["success"] is True and body["selected"] is True and body["parallel_claim"] is True

    def _capture_warnings(self):
        records = []
        handler = logger.add(lambda message: records.append(message.record), level="WARNING")
        return records, handler

    def test_query_error_only_warns_and_heartbeat_succeeds(self):
        def _boom(*args, **kwargs):
            raise RuntimeError("database is unavailable")

        records, handler = self._capture_warnings()
        try:
            with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": True}), \
                 patch.object(screen_stream, "heartbeat_notice", _boom), \
                 patch.object(local_tools_api, "get_current_user", _as_user):
                response = _heartbeat(_client())
        finally:
            logger.remove(handler)
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"success", "selected", "server_time", "parallel_claim", "screen_stream"}
        assert body["success"] is True and body["screen_stream"] == {"version": 1}
        screen_warnings = [r for r in records if "画面会话查询" in r["message"]]
        assert len(screen_warnings) == 1
        warning = screen_warnings[0]
        assert warning["level"].name == "WARNING" and "画面会话查询失败" in warning["message"]
        assert f"device={DEVICE}" in warning["message"]
        assert warning["exception"] is not None
        assert not [r for r in records if r["level"].name == "ERROR"]

    @pytest.mark.parametrize(("error_class", "message"), DATABASE_CANCELS)
    def test_database_cancelled_screen_query_is_reported_as_timeout(self, error_class, message):
        """PostgreSQL 在库里的等待上限到点、取消了画面查询（55P03 / 57014）：
        按「查询超时」处理，只记一条不带堆栈的告警，不算失败"""
        records, handler = self._capture_warnings()
        try:
            with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": True}), \
                 patch.object(screen_stream, "heartbeat_notice", side_effect=error_class(message)):
                response = _heartbeat(_client())
        finally:
            logger.remove(handler)
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"success", "selected", "server_time", "parallel_claim", "screen_stream"}
        assert body["success"] is True and body["screen_stream"] == {"version": 1}
        screen_warnings = [r for r in records if "画面会话查询" in r["message"]]
        assert len(screen_warnings) == 1
        warning = screen_warnings[0]
        assert warning["level"].name == "WARNING" and "画面会话查询超时" in warning["message"]
        assert f"device={DEVICE}" in warning["message"]
        assert warning["exception"] is None
        assert not [r for r in records if r["level"].name == "ERROR"]

    @pytest.mark.parametrize("error", [
        pytest.param(psycopg2.OperationalError("connection pool exhausted"), id="pool-exhausted"),
        pytest.param(pg_errors.UndefinedTable("relation does not exist"), id="undefined-table"),
    ])
    def test_other_database_errors_are_still_reported_as_failures(self, error):
        """数据库异常里只有 55P03 和 57014 算超时；其余带堆栈记为「查询失败」"""
        records, handler = self._capture_warnings()
        try:
            with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": True}), \
                 patch.object(screen_stream, "heartbeat_notice", side_effect=error):
                response = _heartbeat(_client())
        finally:
            logger.remove(handler)
        assert response.status_code == 200
        assert "screen_session" not in response.json()
        screen_warnings = [r for r in records if "画面会话查询" in r["message"]]
        assert len(screen_warnings) == 1
        assert "画面会话查询失败" in screen_warnings[0]["message"]
        assert screen_warnings[0]["exception"] is not None

    def test_query_timeout_only_warns_and_heartbeat_returns_within_a_second(self):
        def _slow(*args, **kwargs):
            time.sleep(2)
            return {"id": SESSION, "state": "pending"}

        async def scenario():
            # 计量的是处理函数的返回时间：TestClient 在请求结束后关闭事件循环时，
            # 会等默认线程池里仍在睡眠的线程（实测多出约 1.5 秒），那是测试客户端的收尾行为，
            # 生产环境（uvicorn）的请求在 0.5 秒超时后就返回
            started = time.perf_counter()
            result = await local_tools_api.runtime_heartbeat(
                local_tools_api.HeartbeatRequest(), device={"id": DEVICE, "tenant_id": TENANT})
            return result, time.perf_counter() - started

        records, handler = self._capture_warnings()
        try:
            with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": True}), \
                 patch.object(screen_stream, "heartbeat_notice", _slow):
                body, elapsed = asyncio.run(scenario())
        finally:
            logger.remove(handler)
        assert elapsed < 1.0
        assert set(body) == {"success", "selected", "server_time", "parallel_claim", "screen_stream"}
        assert body["success"] is True and body["screen_stream"] == {"version": 1}
        screen_warnings = [r for r in records if "画面会话查询" in r["message"]]
        assert len(screen_warnings) == 1 and "画面会话查询超时" in screen_warnings[0]["message"]
        assert f"device={DEVICE}" in screen_warnings[0]["message"]
        assert not [r for r in records if r["level"].name == "ERROR"]

    def test_query_timeout_does_not_break_the_http_response(self):
        def _slow(*args, **kwargs):
            time.sleep(1.2)
            return {"id": SESSION, "state": "pending"}

        with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": True}), \
             patch.object(screen_stream, "heartbeat_notice", _slow):
            response = _heartbeat(_client())
        assert response.status_code == 200
        assert "screen_session" not in response.json()

    def test_query_failure_does_not_turn_a_404_into_success(self):
        """设备更新失败（查不到设备）仍按原样返回 404，不会走到画面查询"""
        with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: None), \
             patch.object(screen_stream, "heartbeat_notice", return_value={"id": SESSION, "state": "pending"}), \
             patch.object(local_tools_api, "get_current_user", _as_user):
            response = _heartbeat(_client())
        assert response.status_code == 404
        assert response.json() == {"detail": {"error": "设备不存在"}}

    def test_device_update_failure_keeps_the_original_500(self):
        """设备更新这一步出错时仍是原来的 500，不会被画面扩展改写成成功，也不会去查画面会话"""
        def _boom(*args, **kwargs):
            raise RuntimeError("device update failed")

        with patch.object(local_tools_api.repository, "touch_device_seen", _boom), \
             patch.object(screen_stream, "heartbeat_notice", return_value={"id": SESSION, "state": "pending"}) as query:
            response = _heartbeat(_client())
        assert response.status_code == 500
        assert response.json() == {"detail": {"error": "心跳失败，请稍后重试", "debug": "device update failed"}}
        query.assert_not_called()


class TestListDevicesProjection:
    def _devices(self):
        return [
            {"id": DEVICE, "name": "新版本", "platform": "windows", "runtime_version": "2.0.0",
             "capabilities_json": {"provider_id": "p", "capabilities": ["local_v2", "screen_stream_v1"]},
             "selected": True, "status": "active", "last_seen_at": datetime.now(), "created_at": datetime.now()},
            {"id": SESSION, "name": "旧版本", "platform": "windows", "runtime_version": "1.0.0",
             "capabilities_json": {"provider_id": "p", "capabilities": ["local_v2"]},
             "selected": False, "status": "active", "last_seen_at": None, "created_at": datetime.now()},
            {"id": "33333333-3333-4333-8333-333333333333", "name": "无能力体", "platform": "windows",
             "runtime_version": "1.0.0", "capabilities_json": None,
             "selected": False, "status": "active", "last_seen_at": None, "created_at": datetime.now()},
        ]

    def _list(self):
        with patch.object(local_tools_api.repository, "list_devices", return_value=self._devices()), \
             patch.object(local_tools_api, "get_current_user", _as_user):
            response = _client().get("/api/local-tools/devices")
        assert response.status_code == 200
        return response.json()["devices"]

    def test_flag_is_true_only_for_capable_devices(self):
        items = self._list()
        assert [item["screen_stream"] for item in items] == [True, False, False]

    def test_item_keys_are_the_original_nine_plus_screen_stream(self):
        """设备列表只多出一个字段，仍然不下发能力原始内容"""
        for item in self._list():
            assert set(item) == DEVICE_KEYS | {"screen_stream"}
        capable = self._list()[0]
        assert capable["selected"] is True and capable["online"] is True
        assert capable["device_id"] == DEVICE and capable["runtime_version"] == "2.0.0"

    def test_disabled_feature_reports_false_for_every_device(self):
        with patch.object(settings.screen_stream, "enabled", False):
            assert [item["screen_stream"] for item in self._list()] == [False, False, False]


# ---------------------------------------------------------------------------
# 六个接口
# ---------------------------------------------------------------------------

ENDPOINTS = [
    ("post", f"/api/local-tools/devices/{DEVICE}/screen-sessions", None),
    ("get", SESSION_PATH, None),
    ("post", f"{SESSION_PATH}/signals", {"type": "start", "payload": {}}),
    ("post", f"{SESSION_PATH}/end", None),
    ("post", "/api/local-tools/runtime/screen/session", RUNTIME_SESSION_BODY),
    ("post", "/api/local-tools/runtime/screen/signal", RUNTIME_SIGNAL_BODY),
]


def _db_stubs():
    return {
        "create_session": MagicMock(return_value={"id": SESSION, "state": "pending"}),
        "load_session": MagicMock(return_value={
            "session": {"id": SESSION, "state": "prepared", "end_reason": None},
            "signals": [{"seq": 1, "type": "prepared", "payload": {}}],
        }),
        "append_signal": MagicMock(return_value={
            "seq": 2, "session": {"id": SESSION, "state": "prepared", "end_reason": None},
        }),
    }


def _patched_stubs():
    stubs = _db_stubs()
    return stubs, patch.multiple(screen_stream, **stubs)


class TestRoutesDisabled:
    @pytest.mark.parametrize(("method", "path", "json_body"), ENDPOINTS)
    def test_disabled_returns_503_for_all_six_endpoints(self, method, path, json_body):
        stubs, stub_patch = _patched_stubs()
        kwargs = {"json": json_body} if json_body is not None else {}
        with patch.object(settings.screen_stream, "enabled", False), stub_patch, \
             patch.object(local_tools_api, "get_current_user", _as_user):
            response = getattr(_client(), method)(path, **kwargs)
        assert response.status_code == 503
        assert response.json() == {"detail": {"error": "实时画面功能未启用", "code": "SCREEN_STREAM_DISABLED", "debug": ""}}
        for stub in stubs.values():
            stub.assert_not_called()


class TestViewerRoutes:
    def test_create_session_passes_owner_scope_and_online_threshold(self):
        stubs, stub_patch = _patched_stubs()
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user), \
             patch.object(settings.screen_stream, "stun_urls", ["stun:example.test:3478"]):
            response = _client().post(f"/api/local-tools/devices/{DEVICE}/screen-sessions")
        assert response.status_code == 200, response.text
        assert response.json() == {"success": True, "session": {
            "id": SESSION, "state": "pending", "stun_urls": ["stun:example.test:3478"]}}
        stubs["create_session"].assert_called_once_with(
            TENANT, USER, DEVICE, device_offline_seconds=local_tools_api.ONLINE_THRESHOLD_SECONDS)

    @pytest.mark.parametrize("bad_device_id", ["not-a-uuid", "0", f"{DEVICE}-extra"])
    def test_create_session_with_malformed_device_id_is_404(self, bad_device_id):
        stubs, stub_patch = _patched_stubs()
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user):
            response = _client().post(f"/api/local-tools/devices/{bad_device_id}/screen-sessions")
        assert response.status_code == 404
        assert response.json() == {"detail": {"error": "设备不存在", "code": "DEVICE_NOT_FOUND", "debug": ""}}
        stubs["create_session"].assert_not_called()

    def test_get_session_returns_runtime_signals_with_after_seq_cursor(self):
        stubs, stub_patch = _patched_stubs()
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user):
            response = _client().get(SESSION_PATH, params={"after_seq": 7})
        assert response.status_code == 200, response.text
        assert response.json() == {"success": True, "session": {"id": SESSION, "state": "prepared", "end_reason": None},
                                   "signals": [{"seq": 1, "type": "prepared", "payload": {}}]}
        stubs["load_session"].assert_called_once_with(VIEWER_SCOPE, SESSION, viewer=True, after_seq=7,
                                                      device_offline_seconds=local_tools_api.ONLINE_THRESHOLD_SECONDS)

    def test_get_session_after_seq_defaults_to_zero(self):
        stubs, stub_patch = _patched_stubs()
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user):
            response = _client().get(SESSION_PATH)
        assert response.status_code == 200
        stubs["load_session"].assert_called_once_with(VIEWER_SCOPE, SESSION, viewer=True, after_seq=0,
                                                      device_offline_seconds=local_tools_api.ONLINE_THRESHOLD_SECONDS)

    @pytest.mark.parametrize("after_seq", ["-1", "abc", "1.5"])
    def test_get_session_rejects_invalid_after_seq(self, after_seq):
        stubs, stub_patch = _patched_stubs()
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user):
            response = _client().get(SESSION_PATH, params={"after_seq": after_seq})
        assert response.status_code == 422
        stubs["load_session"].assert_not_called()

    @pytest.mark.parametrize(("bad_device_id", "bad_session_id"), [
        ("not-a-uuid", SESSION), (DEVICE, "not-a-uuid"),
    ])
    def test_session_routes_with_malformed_path_are_404(self, bad_device_id, bad_session_id):
        stubs, stub_patch = _patched_stubs()
        base = f"/api/local-tools/devices/{bad_device_id}/screen-sessions/{bad_session_id}"
        not_found = {"detail": {"error": "会话不存在或无权访问", "code": "SCREEN_SESSION_NOT_FOUND", "debug": ""}}
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user), _client() as client:
            read = client.get(base)
            signal = client.post(f"{base}/signals", json={"type": "start", "payload": {}})
            end = client.post(f"{base}/end")
        assert read.status_code == signal.status_code == end.status_code == 404
        assert read.json() == signal.json() == end.json() == not_found
        for stub in stubs.values():
            stub.assert_not_called()

    def test_append_signal_passes_type_and_payload(self):
        stubs, stub_patch = _patched_stubs()
        payload = {"sdp": "v=0", "candidates": ["a", "b"]}
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user):
            response = _client().post(f"{SESSION_PATH}/signals", json={"type": "answer", "payload": payload})
        assert response.status_code == 200, response.text
        assert response.json() == {"success": True, "seq": 2}
        stubs["append_signal"].assert_called_once_with(VIEWER_SCOPE, SESSION, "web", "answer", payload,
                                                      device_offline_seconds=local_tools_api.ONLINE_THRESHOLD_SECONDS)

    @pytest.mark.parametrize("json_body", [
        {"type": "offer", "payload": {}},                       # Runtime 方向的信令
        {"type": "bogus", "payload": {}},                       # 未知信令
        {"type": "start", "payload": ["not", "an", "object"]},  # payload 不是 JSON 对象
        {"type": "start", "payload": {"sdp": "x" * 20000}},     # payload 超过 16 KB
        {"type": "start"},                                      # 缺 payload
        {"payload": {}},                                        # 缺 type
    ])
    def test_append_signal_with_invalid_body_is_422(self, json_body):
        stubs, stub_patch = _patched_stubs()
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user):
            response = _client().post(f"{SESSION_PATH}/signals", json=json_body)
        assert response.status_code == 422
        stubs["append_signal"].assert_not_called()

    def test_end_session_ignores_reason_and_tolerates_missing_body(self):
        stubs, stub_patch = _patched_stubs()
        stubs["append_signal"].return_value = {
            "seq": None, "session": {"id": SESSION, "state": "ended", "end_reason": "viewer_closed"}}
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user), _client() as client:
            with_reason = client.post(f"{SESSION_PATH}/end", json={"reason": "不再观看", "extra": 1})
            without_body = client.post(f"{SESSION_PATH}/end")
        expected = {"success": True, "session": {"id": SESSION, "state": "ended", "end_reason": "viewer_closed"}}
        assert with_reason.status_code == without_body.status_code == 200
        assert with_reason.json() == without_body.json() == expected
        assert stubs["append_signal"].call_args_list == [
            ((VIEWER_SCOPE, SESSION, "web", "end", {}), {"device_offline_seconds": local_tools_api.ONLINE_THRESHOLD_SECONDS}),
        ] * 2

    @pytest.mark.parametrize(("method", "path", "json_body", "stub_name"), [
        ("get", SESSION_PATH, None, "load_session"),
        ("post", f"{SESSION_PATH}/signals", {"type": "candidate", "payload": {"c": 1}}, "append_signal"),
        ("post", f"{SESSION_PATH}/end", None, "append_signal"),
    ])
    def test_session_routes_hide_missing_or_foreign_session(self, method, path, json_body, stub_name):
        """不存在或无权访问一律 404，不区分是哪一种"""
        stubs, stub_patch = _patched_stubs()
        stubs[stub_name].return_value = None
        kwargs = {"json": json_body} if json_body is not None else {}
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user):
            response = getattr(_client(), method)(path, **kwargs)
        assert response.status_code == 404
        assert response.json() == {"detail": {"error": "会话不存在或无权访问", "code": "SCREEN_SESSION_NOT_FOUND", "debug": ""}}

    @pytest.mark.parametrize(("stub_name", "code", "status"), [
        ("create_session", "DEVICE_OFFLINE", 409),
        ("create_session", "SCREEN_STREAM_UNSUPPORTED", 409),
        ("create_session", "SCREEN_SESSION_ACTIVE", 409),
        ("create_session", "DEVICE_NOT_FOUND", 404),
    ])
    def test_typed_errors_become_http_errors_with_code(self, stub_name, code, status):
        stubs, stub_patch = _patched_stubs()
        stubs[stub_name].side_effect = screen_stream.ScreenStreamError(code)
        error = screen_stream.ScreenStreamError(code)
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user):
            response = _client().post(f"/api/local-tools/devices/{DEVICE}/screen-sessions")
        assert response.status_code == status
        assert response.json() == {"detail": {"error": error.message, "code": code, "debug": ""}}

    def test_ended_session_rejects_signals_but_accepts_end(self):
        stubs, stub_patch = _patched_stubs()

        def _append(scope, session_id, direction, signal_type, payload, *, device_offline_seconds):
            if signal_type != "end":
                raise screen_stream.ScreenStreamError("SCREEN_SESSION_ENDED")
            return {"seq": None, "session": {"id": SESSION, "state": "ended", "end_reason": "viewer_closed"}}

        stubs["append_signal"].side_effect = _append
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user), _client() as client:
            rejected = client.post(f"{SESSION_PATH}/signals", json={"type": "candidate", "payload": {}})
            accepted = client.post(f"{SESSION_PATH}/signals", json={"type": "end", "payload": {}})
        assert rejected.status_code == 409
        assert rejected.json() == {"detail": {"error": "会话已结束", "code": "SCREEN_SESSION_ENDED", "debug": ""}}
        assert accepted.status_code == 200
        assert accepted.json() == {"success": True, "seq": None}

    def test_signal_limit_is_reported_as_409(self):
        stubs, stub_patch = _patched_stubs()
        stubs["append_signal"].side_effect = screen_stream.ScreenStreamError("SCREEN_SIGNAL_LIMIT")
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user):
            response = _client().post(f"{SESSION_PATH}/signals", json={"type": "candidate", "payload": {}})
        assert response.status_code == 409
        assert response.json() == {"detail": {"error": "信令条数超过上限", "code": "SCREEN_SIGNAL_LIMIT", "debug": ""}}

    @pytest.mark.parametrize(("method", "path", "json_body", "stub_name"), [
        ("post", f"/api/local-tools/devices/{DEVICE}/screen-sessions", None, "create_session"),
        ("get", SESSION_PATH, None, "load_session"),
        ("post", f"{SESSION_PATH}/signals", {"type": "candidate", "payload": {}}, "append_signal"),
        ("post", "/api/local-tools/runtime/screen/session", RUNTIME_SESSION_BODY, "load_session"),
        ("post", "/api/local-tools/runtime/screen/signal", RUNTIME_SIGNAL_BODY, "append_signal"),
    ])
    def test_busy_is_503_on_every_screen_endpoint(self, method, path, json_body, stub_name):
        """等锁超时：503 SCREEN_STREAM_BUSY，调用方下一次轮询会重试"""
        stubs, stub_patch = _patched_stubs()
        stubs[stub_name].side_effect = screen_stream.ScreenStreamError("SCREEN_STREAM_BUSY")
        kwargs = {"json": json_body} if json_body is not None else {}
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user):
            response = getattr(_client(), method)(path, **kwargs)
        assert response.status_code == 503
        assert response.json() == {"detail": {"error": "实时画面暂时繁忙，请稍后重试",
                                              "code": "SCREEN_STREAM_BUSY", "debug": ""}}


class TestRuntimeRoutes:
    def test_device_routes_use_the_authenticated_device(self):
        stubs, stub_patch = _patched_stubs()
        other_device = {"id": SESSION, "tenant_id": "tenant_other"}
        with stub_patch:
            read = _client(other_device).post("/api/local-tools/runtime/screen/session", json=RUNTIME_SESSION_BODY)
        assert read.status_code == 200, read.text
        stubs["load_session"].assert_called_once_with(
            ("tenant_id = %s AND device_id = %s", ("tenant_other", SESSION)), SESSION,
            viewer=False, after_seq=0, device_offline_seconds=local_tools_api.ONLINE_THRESHOLD_SECONDS)

    def test_get_session_returns_viewer_signals_and_stun_urls(self):
        stubs, stub_patch = _patched_stubs()
        with stub_patch, patch.object(settings.screen_stream, "stun_urls", ["stun:example.test:3478"]):
            response = _client().post("/api/local-tools/runtime/screen/session",
                                      json={"session_id": SESSION, "after_seq": 3})
        assert response.status_code == 200, response.text
        assert response.json() == {"success": True, "signals": [{"seq": 1, "type": "prepared", "payload": {}}],
                                   "session": {"id": SESSION, "state": "prepared", "end_reason": None,
                                               "stun_urls": ["stun:example.test:3478"]}}
        stubs["load_session"].assert_called_once_with(RUNTIME_SCOPE, SESSION, viewer=False, after_seq=3,
                                                      device_offline_seconds=local_tools_api.ONLINE_THRESHOLD_SECONDS)

    def test_append_signal_passes_type_and_payload(self):
        stubs, stub_patch = _patched_stubs()
        payload = {"state": "streaming", "path": "lan"}
        with stub_patch:
            response = _client().post("/api/local-tools/runtime/screen/signal",
                                      json={"session_id": SESSION, "type": "status", "payload": payload})
        assert response.status_code == 200, response.text
        assert response.json() == {"success": True, "seq": 2}
        stubs["append_signal"].assert_called_once_with(RUNTIME_SCOPE, SESSION, "rt", "status", payload,
                                                       device_offline_seconds=local_tools_api.ONLINE_THRESHOLD_SECONDS)

    @pytest.mark.parametrize("json_body", [
        {"session_id": SESSION, "type": "start", "payload": {}},                     # 网页方向的信令
        {"session_id": SESSION, "type": "status", "payload": {"state": "bogus"}},    # 非法 state
        {"session_id": SESSION, "type": "status", "payload": {"state": "streaming", "path": "wan"}},
        {"session_id": SESSION, "type": "end", "payload": {"reason": "viewer_closed"}},  # Runtime 不能报的原因
        {"session_id": SESSION, "type": "end", "payload": {}},
        {"session_id": "not-a-uuid", "type": "prepared", "payload": {}},
        {"session_id": SESSION, "type": "prepared"},                                  # 缺 payload
        {"session_id": SESSION, "type": "offer", "payload": {"sdp": "画" * 6000}},    # 超过 16 KB
        {"type": "prepared", "payload": {}},                                          # 缺 session_id
    ])
    def test_append_signal_with_invalid_body_is_422(self, json_body):
        stubs, stub_patch = _patched_stubs()
        with stub_patch:
            response = _client().post("/api/local-tools/runtime/screen/signal", json=json_body)
        assert response.status_code == 422
        stubs["append_signal"].assert_not_called()

    @pytest.mark.parametrize(("path", "json_body", "stub_name"), [
        ("/api/local-tools/runtime/screen/session", RUNTIME_SESSION_BODY, "load_session"),
        ("/api/local-tools/runtime/screen/signal", RUNTIME_SIGNAL_BODY, "append_signal"),
    ])
    def test_foreign_or_missing_session_is_404(self, path, json_body, stub_name):
        stubs, stub_patch = _patched_stubs()
        stubs[stub_name].return_value = None
        with stub_patch:
            response = _client().post(path, json=json_body)
        assert response.status_code == 404
        assert response.json() == {"detail": {"error": "会话不存在或无权访问", "code": "SCREEN_SESSION_NOT_FOUND", "debug": ""}}

    @pytest.mark.parametrize("json_body", [
        {"session_id": SESSION, "after_seq": -1},
        {"session_id": SESSION, "after_seq": "abc"},
        {"after_seq": 0},
    ])
    def test_get_session_with_invalid_body_is_422(self, json_body):
        stubs, stub_patch = _patched_stubs()
        with stub_patch:
            response = _client().post("/api/local-tools/runtime/screen/session", json=json_body)
        assert response.status_code == 422
        stubs["load_session"].assert_not_called()


class TestNonFiniteNumberAtTheInterface:
    """接口层面：原始请求体里的 NaN / Infinity / -Infinity（Python 的 JSON 解析接受这些写法）在请求模型这一层被拦下。
    测试客户端自己不肯编码这些值，所以请求体用原始文本发。

    这时接口返回的不是 422：FastAPI 默认的校验错误响应会把出错的 payload 原样带回（input 字段），
    而 Starlette 渲染 JSON 响应同样不允许 NaN，于是 422 响应自己渲染失败，最终落到全局兜底的 500。
    这里只固定与状态码无关、无论最终怎么处理都必须成立的三件事：数据库层不会被调用、请求被拒绝、
    响应里不带回信令内容。
    """

    BODIES = [
        (f"{SESSION_PATH}/signals", '{"type": "answer", "payload": {"sdp": "SIGNAL-TEXT-MARKER", "x": TOKEN}}'),
        ("/api/local-tools/runtime/screen/signal",
         '{"session_id": "%s", "type": "offer", "payload": {"sdp": "SIGNAL-TEXT-MARKER", "x": TOKEN}}' % SESSION),
    ]

    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_number_never_reaches_the_database_layer(self, token):
        stubs, stub_patch = _patched_stubs()
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user), \
             _client(raise_server_exceptions=False) as client:
            responses = [client.post(path, content=raw.replace("TOKEN", token),
                                     headers={"Content-Type": "application/json"}) for path, raw in self.BODIES]
        stubs["append_signal"].assert_not_called()
        for response in responses:
            assert response.status_code != 200
            assert "SIGNAL-TEXT-MARKER" not in response.text


class TestAuthentication:
    @pytest.mark.parametrize(("method", "path", "json_body"), ENDPOINTS[:4])
    def test_viewer_routes_require_login(self, method, path, json_body):
        kwargs = {"json": json_body} if json_body is not None else {}
        stubs, stub_patch = _patched_stubs()
        with stub_patch:
            response = getattr(_client(), method)(path, **kwargs)
        assert response.status_code == 401
        for stub in stubs.values():
            stub.assert_not_called()

    @pytest.mark.parametrize(("method", "path", "json_body"), ENDPOINTS)
    def test_authentication_precedes_the_feature_switch(self, method, path, json_body):
        """先鉴权、后看功能开关：未登录或没有设备令牌的调用方即使功能已关闭也拿不到 503"""
        kwargs = {"json": json_body} if json_body is not None else {}
        stubs, stub_patch = _patched_stubs()
        app = FastAPI()
        app.include_router(local_tools_api.router)
        with stub_patch, patch.object(settings.screen_stream, "enabled", False), \
             patch.object(local_tools_api.repository, "get_device_by_token_hash", return_value=None):
            response = getattr(TestClient(app), method)(path, **kwargs)
        assert response.status_code == 401
        for stub in stubs.values():
            stub.assert_not_called()

    @pytest.mark.parametrize(("method", "path", "json_body"), ENDPOINTS[4:])
    def test_runtime_routes_require_device_token(self, method, path, json_body):
        """真实鉴权依赖下：没有设备 token 的调用返回 401（功能启用时同样如此）"""
        app = FastAPI()
        app.include_router(local_tools_api.router)
        stubs, stub_patch = _patched_stubs()
        with stub_patch, patch.object(local_tools_api.repository, "get_device_by_token_hash", return_value=None):
            response = getattr(TestClient(app), method)(path, json=json_body)
        assert response.status_code == 401
        for stub in stubs.values():
            stub.assert_not_called()
