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
- 占用闸：一个 ASGI 中间件，挂在最外层（租户中间件之前），名额数等于画面连接池的连接数上限。闸满时画面
  请求立即得到 503：不调用下游、不碰数据库、不进入画面作用域；放行时请求在画面作用域里处理，名额恰好归还一次。
  不经过中间件的调用（设备心跳里的画面查询、直接调用）自己拿名额、自己进入画面作用域，拿不到立即让出
  （抛 SCREEN_STREAM_BUSY）
- 占用闸的路径匹配：套到本地工具路由的全部路由上，匹配到的恰好是六个画面接口
- STUN 配置：只保留 stun: / stuns: 地址，其余丢弃并告警（告警里不写地址）；这一节里留空的值按缺省处理
- 心跳响应：画面通知字段，功能未启用时不带，查询报错或超时不影响心跳；不支持画面的设备不查画面会话
  （不拿名额、不借连接），查不查按这次心跳生效的能力体判断
- 设备列表：screen_stream 投影
- 六个接口：功能未启用 503、非法路径 404、鉴权范围参数、类型化错误转换
"""

import asyncio
import copy
import inspect
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from loguru import logger
from psycopg2 import errors as pg_errors
from pydantic import TypeAdapter, ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import get_route_path

from src.config.settings import ScreenStreamConfig, Settings, create_settings, settings
from src.db import database
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
# 设备的能力体，以及鉴权给出的设备行（_require_device 的返回值里带着设备已保存的 capabilities_json）。
# 支持画面与不支持画面（旧 Runtime）的各一份
SCREEN_CAPABILITIES = {"provider_id": "ai.aidwork.boss-recruiting",
                       "capabilities": ["local_v2", screen_stream.SCREEN_STREAM_CAPABILITY]}
LEGACY_CAPABILITIES = {"provider_id": "ai.aidwork.boss-recruiting"}
SCREEN_DEVICE = {"id": DEVICE, "tenant_id": TENANT, "capabilities_json": SCREEN_CAPABILITIES}
LEGACY_DEVICE = {"id": DEVICE, "tenant_id": TENANT, "capabilities_json": LEGACY_CAPABILITIES}
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


def _patch_conn(results, fail_on=None, on_borrow=None):
    """替换模块里的数据库连接。results 按执行顺序给出每条语句返回的行（每项是一个行列表）；
    同时记录每条语句的 SQL 与参数（含开头的 SET LOCAL），以及 commit 的次数。
    fail_on 是 {语句序号: 要抛的异常}，用来模拟数据库在时限到点时取消语句。
    on_borrow 在每次借连接（进入连接上下文）时被调用，用来断言「有没有去借连接」和借连接的先后"""
    executed, commits = [], []
    cursor = _FakeCursor(results, executed, fail_on or {})
    conn = SimpleNamespace(cursor=lambda *a, **k: cursor, commit=lambda: commits.append(True), rollback=lambda: None)

    @contextmanager
    def fake_get_db_connection():
        if on_borrow is not None:
            on_borrow()
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


def _take_all(semaphore):
    """不阻塞地把名额全部借出来（借了不还），返回借到的个数"""
    taken = 0
    while semaphore.acquire(blocking=False):
        taken += 1
    return taken


def _permits_available(semaphore):
    """闸里现在有几个名额：全部借出来数一遍，再全部还回去"""
    taken = _take_all(semaphore)
    for _ in range(taken):
        semaphore.release()
    return taken


def _fresh_gate(monkeypatch, pool_max=5):
    """让闸按给定的画面连接数上限（database.SCREEN_DB_POOL_MAX）重新创建"""
    monkeypatch.setattr(screen_stream, "_gate", None)
    monkeypatch.setattr(database, "SCREEN_DB_POOL_MAX", pool_max)
    return screen_stream._connection_gate()


class _SpySemaphore:
    """包住真的信号量，记下过闸、归还的先后"""

    def __init__(self, real, events):
        self._real = real
        self._events = events

    def acquire(self, blocking=True):
        self._events.append("gate")
        return self._real.acquire(blocking=blocking)

    def release(self):
        self._events.append("release")
        self._real.release()


def _run(call, results, fail_on=None, on_borrow=None):
    """在 mock 连接上执行一个画面数据库函数，返回 (结果, 执行过的语句, commit 次数)"""
    executed, commits, conn_patch = _patch_conn(results, fail_on=fail_on, on_borrow=on_borrow)
    with conn_patch:
        return call(), executed, commits


def _create_session(**kwargs):
    return screen_stream.create_session(TENANT, USER, DEVICE, device_offline_seconds=ONLINE, **kwargs)


def _load_session(**kwargs):
    return screen_stream.load_session(VIEWER_SCOPE, SESSION, viewer=True, device_offline_seconds=ONLINE, **kwargs)


def _append_signal(**kwargs):
    return screen_stream.append_signal(RUNTIME_SCOPE, SESSION, "rt", "candidate", {}, device_offline_seconds=ONLINE, **kwargs)


def _heartbeat_notice():
    return screen_stream.heartbeat_notice(DEVICE, device_offline_seconds=ONLINE)


# 四个数据库函数：正常返回时的结果集、第一条要等锁的语句序号（心跳路径上是续期 UPDATE）
GATE_CASES = [
    ("create_session", _create_session,
     [[TestCreateSessionWithMockedConnection._device()], [], [{"id": SESSION}]], 1),
    ("load_session", _load_session, [[TestLoadSessionWithMockedConnection._live()]], 1),
    ("append_signal", _append_signal, [[TestLoadSessionWithMockedConnection._live()]], 1),
    ("heartbeat_notice", _heartbeat_notice,
     [[_row(state="streaming", started_at=NOW, viewer_seen_at=NOW)]], 3),
]
GATE_CASE_IDS = [case[0] for case in GATE_CASES]
SESSION_CASES = [case for case in GATE_CASES if case[0] != "heartbeat_notice"]
SESSION_CASE_IDS = [case[0] for case in SESSION_CASES]


class TestConnectionGate:
    """画面请求进入下游之前先过一道占用闸（进程内的信号量），名额数等于画面连接池的连接数上限。

    画面请求只用画面专用连接池，这个池很小（缺省 3 个连接）：同时放行的画面请求一多就会把它借空。
    所以拿不到名额的请求立即让出。画面请求不占业务连接池，这道闸只决定画面请求之间谁先谁后，
    不影响核心请求。连接池是每个进程一个，闸也按进程算。
    这里的数据库函数都是直接调用（不在画面作用域里）：它们自己拿名额、自己进入画面作用域；
    经占用闸中间件进来的请求已经在作用域里，见 test_functions_inside_the_scope_do_not_touch_the_gate
    """

    @pytest.mark.parametrize(("screen_db_pool_max", "expected"), [(3, 3), (5, 5), (1, 1)])
    def test_capacity_is_the_screen_pool_limit_and_at_least_one(self, monkeypatch, screen_db_pool_max, expected):
        """容量就是画面连接池的连接数上限 database.SCREEN_DB_POOL_MAX，至少 1 个。
        只读配置、不读连接池对象：画面连接池到第一次用到画面才创建，读它不能触发创建"""

        def reading_the_pool_is_not_allowed():
            raise AssertionError("容量不该去读连接池对象")

        monkeypatch.setattr(database, "get_screen_pool", reading_the_pool_is_not_allowed)
        gate = _fresh_gate(monkeypatch, screen_db_pool_max)
        assert screen_stream.gate_capacity() == expected
        assert _permits_available(gate) == expected

    def test_gate_is_created_on_first_use_and_then_reused(self, monkeypatch):
        """按第一次使用时的画面连接数上限创建；之后配置变了，闸也不重建"""
        gate = _fresh_gate(monkeypatch, pool_max=5)
        monkeypatch.setattr(database, "SCREEN_DB_POOL_MAX", 15)
        assert screen_stream._connection_gate() is gate
        assert _permits_available(gate) == 5

    @pytest.mark.parametrize(("label", "call", "_", "__"), SESSION_CASES, ids=SESSION_CASE_IDS)
    def test_session_paths_yield_at_a_full_gate_without_borrowing(self, monkeypatch, label, call, _, __):
        """闸满：立即抛 SCREEN_STREAM_BUSY（和等锁超时是同一个错误码），不排队、不借连接，也不动名额"""
        gate = _fresh_gate(monkeypatch)
        assert _take_all(gate) == 5
        borrowed = []
        started = time.perf_counter()
        with pytest.raises(screen_stream.ScreenStreamError) as caught:
            _run(call, [[]], on_borrow=lambda: borrowed.append(1))
        assert time.perf_counter() - started < 0.5
        assert (caught.value.code, caught.value.status, caught.value.message) == (
            "SCREEN_STREAM_BUSY", 503, "实时画面暂时繁忙，请稍后重试")
        assert borrowed == []
        assert _permits_available(gate) == 0

    def test_heartbeat_notice_yields_at_a_full_gate_without_borrowing(self, monkeypatch):
        """闸满：心跳里的画面查询向路由层报「画面繁忙」（同一个类型化错误），由路由层告警、不带会话通知"""
        gate = _fresh_gate(monkeypatch)
        assert _take_all(gate) == 5
        borrowed = []
        result, executed, commits = None, [], []
        with pytest.raises(screen_stream.ScreenStreamError) as caught:
            result, executed, commits = _run(_heartbeat_notice, [[]], on_borrow=lambda: borrowed.append(1))
        assert caught.value.code == "SCREEN_STREAM_BUSY"
        assert (result, borrowed, executed, commits) == (None, [], [], [])
        assert _permits_available(gate) == 0

    @pytest.mark.parametrize(("label", "call", "normal_results", "_"), GATE_CASES, ids=GATE_CASE_IDS)
    def test_permit_is_returned_after_a_normal_return(self, monkeypatch, label, call, normal_results, _):
        gate = _fresh_gate(monkeypatch)
        _run(call, normal_results)
        assert _permits_available(gate) == 5

    def test_permit_is_returned_on_early_exit_and_typed_errors(self, monkeypatch):
        gate = _fresh_gate(monkeypatch)
        device = TestCreateSessionWithMockedConnection._device()
        ended = _row(state="ended", end_reason="viewer_closed", ended_at=NOW)

        # create_session：设备不存在；已有未结束的会话（事务里已经写过一行之后才报错）
        with pytest.raises(screen_stream.ScreenStreamError) as caught:
            _run(_create_session, [[]])
        assert caught.value.code == "DEVICE_NOT_FOUND" and _permits_available(gate) == 5
        with pytest.raises(screen_stream.ScreenStreamError) as caught:
            _run(_create_session, [[device], [_row(state="pending")]])
        assert caught.value.code == "SCREEN_SESSION_ACTIVE" and _permits_available(gate) == 5

        # load_session：查不到会话，提前 return
        assert _run(_load_session, [[]])[0] is None and _permits_available(gate) == 5

        # append_signal：查不到会话；已结束的会话又收到别的信令（提交之后才报错）
        assert _run(_append_signal, [[]])[0] is None and _permits_available(gate) == 5
        with pytest.raises(screen_stream.ScreenStreamError) as caught:
            _run(lambda: screen_stream.append_signal(RUNTIME_SCOPE, SESSION, "rt", "prepared", {},
                                                     device_offline_seconds=ONLINE), [[ended]])
        assert caught.value.code == "SCREEN_SESSION_ENDED" and _permits_available(gate) == 5

        # heartbeat_notice：没有有关的会话；建连阶段的会话（这时 Runtime 自己在轮询）
        assert _run(_heartbeat_notice, [[]])[0] is None and _permits_available(gate) == 5
        handshake = _row(state="prepared", viewer_seen_at=NOW, runtime_seen_at=NOW)
        assert _run(_heartbeat_notice, [[handshake]])[0] is None and _permits_available(gate) == 5

    @pytest.mark.parametrize(("label", "call", "locking_statement"), [(c[0], c[1], c[3]) for c in GATE_CASES],
                         ids=GATE_CASE_IDS)
    def test_permit_is_returned_when_waiting_for_the_lock_times_out(self, monkeypatch, label, call, locking_statement):
        gate = _fresh_gate(monkeypatch)
        streaming = [[_row(state="streaming", started_at=NOW, viewer_seen_at=NOW)]]
        results = streaming if label == "heartbeat_notice" else [[]]
        with pytest.raises(screen_stream.ScreenStreamError if label != "heartbeat_notice" else LockTimeout):
            _run(call, results, fail_on={locking_statement: LockTimeout()})
        # 三条会话路径把它转成 SCREEN_STREAM_BUSY；心跳路径原样抛出，由路由层按超时告警。两种情况名额都已归还
        assert _permits_available(gate) == 5

    @pytest.mark.parametrize(("label", "call", "locking_statement"), [(c[0], c[1], c[3]) for c in GATE_CASES],
                         ids=GATE_CASE_IDS)
    @pytest.mark.parametrize("error", [
        pytest.param(psycopg2.OperationalError("server closed the connection unexpectedly"), id="connection-lost"),
        pytest.param(StatementTimeout("canceling statement due to statement timeout"), id="statement-timeout"),
    ])
    def test_permit_is_returned_when_the_database_fails(self, monkeypatch, label, call, locking_statement, error):
        gate = _fresh_gate(monkeypatch)
        streaming = [[_row(state="streaming", started_at=NOW, viewer_seen_at=NOW)]]
        results = streaming if label == "heartbeat_notice" else [[]]
        with pytest.raises(type(error)) as caught:
            _run(call, results, fail_on={locking_statement: error})
        assert caught.value is error
        assert _permits_available(gate) == 5

    def test_next_request_gets_a_permit_once_one_is_given_back(self, monkeypatch):
        gate = _fresh_gate(monkeypatch)
        assert _take_all(gate) == 5
        with pytest.raises(screen_stream.ScreenStreamError) as caught:
            _run(_load_session, [[]])
        assert caught.value.code == "SCREEN_STREAM_BUSY"
        gate.release()  # 另一个画面请求做完，归还了一个名额
        _, executed, _ = _run(_load_session, [[]])
        assert executed[0] == ("SET LOCAL lock_timeout = 1000", None)
        assert _permits_available(gate) == 1  # 其余 4 个名额仍然被这个测试占着

    @pytest.mark.parametrize(("label", "call", "normal_results", "_"), GATE_CASES, ids=GATE_CASE_IDS)
    def test_gate_comes_before_borrowing_and_set_local_is_still_first(self, monkeypatch, label, call, normal_results, _):
        """闸加在数据库层等待上限的外面：先过闸，再借连接；借到连接后的第一条语句仍是 SET LOCAL"""
        gate = _fresh_gate(monkeypatch)
        events = []
        monkeypatch.setattr(screen_stream, "_gate", _SpySemaphore(gate, events))
        _run(call, normal_results, on_borrow=lambda: events.append("connection"))
        assert events == ["gate", "connection", "release"]
        assert _permits_available(gate) == 5

        assert _take_all(gate) == 5
        events.clear()
        with pytest.raises(screen_stream.ScreenStreamError) as caught:
            _run(call, normal_results, on_borrow=lambda: events.append("connection"))
        assert caught.value.code == "SCREEN_STREAM_BUSY"
        assert events == ["gate"]

    @pytest.mark.parametrize(("label", "call", "normal_results", "_"), GATE_CASES, ids=GATE_CASE_IDS)
    def test_a_direct_call_runs_inside_the_screen_scope_only_while_it_runs(self, monkeypatch, label, call,
                                                                           normal_results, _):
        """直接调用不在画面作用域里：这里自己进入作用域，借连接的时候就在作用域里（也就是从画面连接池借）；
        调用结束后作用域随之退出"""
        assert database.in_screen_scope() is False
        seen = []
        _run(call, normal_results, on_borrow=lambda: seen.append(database.in_screen_scope()))
        assert seen == [True]
        assert database.in_screen_scope() is False

    @pytest.mark.parametrize(("label", "call", "normal_results", "_"), GATE_CASES, ids=GATE_CASE_IDS)
    def test_functions_inside_the_scope_do_not_touch_the_gate(self, monkeypatch, label, call, normal_results, _):
        """请求经占用闸中间件进来时已经在画面作用域里（名额由中间件管）：这些函数直接放行，
        不拿名额、不归还，只借连接、执行 SET LOCAL。闸已经满了也不要紧。
        不在作用域里的直接调用（上面的用例）仍然自己拿、自己还"""
        gate = _fresh_gate(monkeypatch)
        events = []
        monkeypatch.setattr(screen_stream, "_gate", _SpySemaphore(gate, events))
        assert _take_all(gate) == 5

        with database.screen_connection_scope():
            _, executed, _ = _run(call, normal_results, on_borrow=lambda: events.append("connection"))
        assert events == ["connection"]
        assert executed[0][0].startswith("SET LOCAL")
        assert _permits_available(gate) == 0


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
        """校验器只过滤列表里的条目（留空的 None 已在这一层之前按缺省处理）。
        整个值是别的类型属于结构错误，照常由 pydantic 报出来（和其他配置项一致），
        不会被当成一串字符悄悄丢光"""
        for value in ("stun:stun.example.net", 3478):
            with pytest.raises(ValidationError):
                ScreenStreamConfig(stun_urls=value)


class TestScreenStreamConfigBlanks:
    """这一节里留空的值按缺省处理：只影响画面功能的配置错误不能拖垮服务启动。

    YAML 里留空的键读出来是 None，pydantic 默认把 None 当成不合法的取值而拒绝加载，
    以前 stun_urls: 留空、enabled: 留空、整个 screen_stream: 节留空都会让服务起不来。
    """

    YAML_FORMS = [
        pytest.param("screen_stream:\n  enabled: true\n  stun_urls:\n", id="stun_urls-blank"),
        pytest.param("screen_stream:\n  enabled:\n  stun_urls: []\n", id="enabled-blank"),
        pytest.param("screen_stream:\n", id="section-blank"),
        pytest.param("screen_stream:\n  enabled: true\n  stun_urls: []\n", id="explicit-empty-list"),
    ]

    @staticmethod
    def _load(tmp_path, yaml_text):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml_text, encoding="utf-8")
        return create_settings(config_path)

    @pytest.mark.parametrize("yaml_text", YAML_FORMS)
    def test_blank_values_load_through_the_real_config_entry(self, tmp_path, yaml_text):
        """评审点名的回归方式：写成临时 YAML 文件，用 create_settings(config_path=…) 加载"""
        loaded = self._load(tmp_path, yaml_text)
        assert loaded.screen_stream.enabled is True
        assert loaded.screen_stream.stun_urls == []

    def test_section_left_out_entirely_still_uses_defaults(self, tmp_path):
        loaded = self._load(tmp_path, "app:\n  name: unit\n")
        assert (loaded.screen_stream.enabled, loaded.screen_stream.stun_urls) == (True, [])

    @pytest.mark.parametrize("kwargs", [
        {}, {"enabled": None}, {"stun_urls": None}, {"enabled": None, "stun_urls": None},
    ], ids=["no-arguments", "enabled-None", "stun_urls-None", "both-None"])
    def test_model_treats_blank_as_default_without_warning(self, kwargs):
        records, handler = TestStunUrlsConfig._capture_warnings()
        try:
            config = ScreenStreamConfig(**kwargs)
            whole_section_blank = Settings(screen_stream=None).screen_stream
        finally:
            logger.remove(handler)
        for config in (config, whole_section_blank):
            assert (config.enabled, config.stun_urls) == (True, [])
        assert not [record for record in records if "stun_urls" in record["message"]]

    def test_non_stun_addresses_are_still_filtered_and_warned(self):
        records, handler = TestStunUrlsConfig._capture_warnings()
        try:
            config = ScreenStreamConfig(stun_urls=["turn:demo:secret@relay.example.com:3478", "stun:stun.example.net"])
        finally:
            logger.remove(handler)
        assert config.stun_urls == ["stun:stun.example.net"]
        warnings = [record for record in records if "stun_urls" in record["message"]]
        assert len(warnings) == 1 and "1 条" in warnings[0]["message"]
        assert "relay.example.com" not in warnings[0]["message"] and "secret" not in warnings[0]["message"]

    def test_values_that_are_wrong_but_not_blank_are_still_rejected(self):
        """留空按缺省处理；写错了的值照常由 pydantic 报错"""
        with pytest.raises(ValidationError):
            ScreenStreamConfig(enabled="maybe")
        with pytest.raises(ValidationError):
            ScreenStreamConfig(stun_urls="stun:stun.example.net")
        with pytest.raises(ValidationError):
            Settings(screen_stream="oops")


# ---------------------------------------------------------------------------
# 心跳响应
# ---------------------------------------------------------------------------


def _client(device=None, raise_server_exceptions=True, device_dependency=None, real_device_auth=False, gated=False):
    """只挂本地工具路由的最小应用：登录态与设备鉴权换成固定身份，不导入 src.main。

    device：代替鉴权给出的设备行，缺省是一台支持画面的设备（SCREEN_DEVICE）；
    device_dependency：用给定的函数代替设备鉴权依赖（放探针，断言它有没有被调用）；
    real_device_auth：不替换设备鉴权，走真实的 _require_device；
    gated：最外面再挂上占用闸（ScreenStreamGateMiddleware）。它排在下面那个中间件的外面，
        和真实应用里「占用闸 → 租户中间件 → 路由」的层次一样
    """
    app = FastAPI()
    app.include_router(local_tools_api.router)

    @app.middleware("http")
    async def _tenant_context(request, call_next):
        request.state.tenant_id = TENANT
        return await call_next(request)

    if gated:
        app.add_middleware(screen_stream.ScreenStreamGateMiddleware)

    if not real_device_auth:
        app.dependency_overrides[local_tools_api._require_device] = device_dependency or (
            lambda: device or SCREEN_DEVICE)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _capture_logs():
    """收下这期间 loguru 的全部日志记录（任何级别）"""
    records = []
    handler = logger.add(lambda message: records.append(message.record), level="TRACE")
    return records, handler


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

    def test_full_gate_only_warns_and_heartbeat_succeeds(self, monkeypatch):
        """画面请求同时借走的连接到了上限（占用闸已满）：这次心跳不带会话通知，只记一条不带堆栈的告警，
        不算失败；心跳里的画面查询过不了闸，也就没有去借连接"""
        assert _take_all(_fresh_gate(monkeypatch)) == 5
        borrowed = []
        records, handler = self._capture_warnings()
        try:
            with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": True}), \
                 _patch_conn([[]], on_borrow=lambda: borrowed.append(1))[2]:
                response = _heartbeat(_client())
        finally:
            logger.remove(handler)
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"success", "selected", "server_time", "parallel_claim", "screen_stream"}
        assert body["success"] is True and body["screen_stream"] == {"version": 1}
        assert borrowed == []
        gate_warnings = [r for r in records if "画面繁忙" in r["message"]]
        assert len(gate_warnings) == 1
        warning = gate_warnings[0]
        assert warning["level"].name == "WARNING" and warning["exception"] is None
        assert "本次心跳不带会话通知" in warning["message"] and f"device={DEVICE}" in warning["message"]
        assert not [r for r in records if r["level"].name == "ERROR"]

    def test_heartbeat_carries_the_notice_when_the_gate_has_room(self, monkeypatch):
        """闸有名额时照常：借连接、查出会话、带回通知，做完归还名额"""
        gate = _fresh_gate(monkeypatch)
        streaming = _row(state="streaming", started_at=NOW, viewer_seen_at=NOW)
        with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": True}), \
             _patch_conn([[streaming]])[2]:
            body = _heartbeat(_client()).json()
        assert body["screen_session"] == {"id": SESSION, "state": "streaming"}
        assert _permits_available(gate) == 5

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
                local_tools_api.HeartbeatRequest(), device=SCREEN_DEVICE)
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

    # ---- 设备支不支持画面，决定心跳查不查画面会话 ----

    def test_legacy_device_carries_the_marker_without_querying(self):
        """不支持画面的设备不可能有画面会话（创建会话时就被拒绝）：心跳不为它查会话；
        响应里仍然带 screen_stream 标记（旧设备对照要求除新增字段外不变）"""
        with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": True}), \
             patch.object(screen_stream, "heartbeat_notice",
                          side_effect=AssertionError("不支持画面的设备不该查画面会话")) as query:
            body = _heartbeat(_client(LEGACY_DEVICE)).json()
        assert body == {"success": True, "selected": True, "server_time": body["server_time"],
                        "parallel_claim": True, "screen_stream": {"version": 1}}
        query.assert_not_called()

    def test_legacy_device_passes_a_full_gate_without_a_warning(self, monkeypatch):
        """不支持画面的设备不去过闸：闸被画面请求占满时，它的心跳照常成功，不借画面连接，也不记「画面繁忙」告警"""
        assert _take_all(_fresh_gate(monkeypatch)) == 5
        records, handler = self._capture_warnings()
        borrowed = []
        try:
            with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": True}), \
                 _patch_conn([[]], on_borrow=lambda: borrowed.append(1))[2]:
                response = _heartbeat(_client(LEGACY_DEVICE))
        finally:
            logger.remove(handler)
        assert response.status_code == 200, response.text
        assert set(response.json()) == {"success", "selected", "server_time", "parallel_claim", "screen_stream"}
        assert response.json()["success"] is True and response.json()["screen_stream"] == {"version": 1}
        assert borrowed == []
        assert not [r for r in records if "画面繁忙" in r["message"] or "画面会话" in r["message"]]

    @pytest.mark.parametrize(("reported", "saved", "queries"), [
        # (这次心跳上报的能力体, 设备已保存的能力, 查几次画面会话)
        pytest.param(SCREEN_CAPABILITIES, LEGACY_CAPABILITIES, 1, id="reported-with-screen"),
        pytest.param(None, SCREEN_CAPABILITIES, 1, id="saved-with-screen-and-nothing-reported"),
        pytest.param(LEGACY_CAPABILITIES, SCREEN_CAPABILITIES, 0, id="reported-without-screen"),
        pytest.param(LEGACY_CAPABILITIES, LEGACY_CAPABILITIES, 0, id="neither-has-screen"),
    ])
    def test_screen_query_follows_the_capabilities_effective_for_this_heartbeat(self, monkeypatch, reported, saved,
                                                                               queries):
        """查不查取决于这次心跳生效的能力体，取法与 touch_device_seen 存库时相同：这次上报的能力体非空就用它
        （哪怕和已保存的不同），否则用设备已保存的。不查的时候不拿名额、不借连接"""
        gate = _fresh_gate(monkeypatch)
        events = []
        monkeypatch.setattr(screen_stream, "_gate", _SpySemaphore(gate, events))
        device = {"id": DEVICE, "tenant_id": TENANT, "capabilities_json": saved}
        with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": True}), \
             _patch_conn([[]], on_borrow=lambda: events.append("connection"))[2]:
            response = _client(device).post("/api/local-tools/runtime/heartbeat",
                                            json={} if reported is None else {"capabilities": reported})
        assert response.status_code == 200, response.text
        assert set(response.json()) == {"success", "selected", "server_time", "parallel_claim", "screen_stream"}
        assert events == ["gate", "connection", "release"] * queries
        assert _permits_available(gate) == 5

    @pytest.mark.parametrize(("normalized", "saved", "queries"), [
        # (上报的能力体归一化之后的结果, 设备已保存的能力, 查几次画面会话)
        pytest.param(SCREEN_CAPABILITIES, LEGACY_CAPABILITIES, 1, id="normalized-wins-over-saved"),
        pytest.param(None, SCREEN_CAPABILITIES, 1, id="empty-falls-back-to-saved"),
        pytest.param({}, SCREEN_CAPABILITIES, 1, id="empty-dict-falls-back-to-saved"),
    ])
    def test_decision_uses_the_reported_capabilities_after_normalization(self, monkeypatch, normalized, saved,
                                                                         queries):
        """与 touch_device_seen 存库时的取法一致：上报的能力体先经 client_capabilities 归一化，结果非空就用它，
        是空的就用设备已保存的"""
        reported = {"provider_id": "ai.aidwork.boss-recruiting", "capabilities": ["local_v2"]}
        seen = []
        monkeypatch.setattr(local_tools_api, "client_capabilities",
                            lambda capabilities: seen.append(capabilities) or normalized)
        with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: {"selected": True}), \
             patch.object(screen_stream, "heartbeat_notice", return_value=None) as query:
            response = _client({"id": DEVICE, "tenant_id": TENANT, "capabilities_json": saved}).post(
                "/api/local-tools/runtime/heartbeat", json={"capabilities": reported})
        assert response.status_code == 200, response.text
        assert seen == [reported]
        assert query.call_count == queries


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
            ((VIEWER_SCOPE, SESSION, "web", "end", {}),
             {"device_offline_seconds": local_tools_api.ONLINE_THRESHOLD_SECONDS}),
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


# 六个画面接口：(方法, 路径, json 请求体, 接口调用的数据库函数, 鉴权方式)
SCREEN_ENDPOINTS = [
    ("post", f"/api/local-tools/devices/{DEVICE}/screen-sessions", None, "create_session", "web"),
    ("get", SESSION_PATH, None, "load_session", "web"),
    ("post", f"{SESSION_PATH}/signals", {"type": "candidate", "payload": {}}, "append_signal", "web"),
    ("post", f"{SESSION_PATH}/end", None, "append_signal", "web"),
    ("post", "/api/local-tools/runtime/screen/session", RUNTIME_SESSION_BODY, "load_session", "device"),
    ("post", "/api/local-tools/runtime/screen/signal", RUNTIME_SIGNAL_BODY, "append_signal", "device"),
]
SCREEN_ENDPOINT_IDS = ["viewer-create", "viewer-read", "viewer-signal", "viewer-end", "runtime-read", "runtime-signal"]
BUSY_DETAIL = {"detail": {"error": "实时画面暂时繁忙，请稍后重试", "code": "SCREEN_STREAM_BUSY", "debug": ""}}


def _screen_error(code):
    error = screen_stream.ScreenStreamError(code)
    return {"detail": {"error": error.message, "code": code, "debug": ""}}


def _stub_returns(value):
    return lambda stub: setattr(stub, "return_value", value)


def _stub_raises(error):
    return lambda stub: setattr(stub, "side_effect", error)


def _send(client, method, path, json_body):
    kwargs = {"json": json_body} if json_body is not None else {}
    return getattr(client, method)(path, **kwargs)


def _stub_sees_scope(events, value):
    """数据库函数的桩：被调用时记下这一刻是否在画面作用域里，然后返回 value"""
    def call(*args, **kwargs):
        events.append(("db", database.in_screen_scope()))
        return value
    return call


async def _never_called(scope, receive, send):
    raise AssertionError("闸满时不应该调用下游")


async def _asgi_noop(scope, receive, send):
    pass


class TestScreenGateMiddleware:
    """画面请求的占用闸是一个 ASGI 中间件，挂在本应用的最外面（真实应用里排在租户中间件之前）：
    占用闸 → 租户中间件 → 鉴权 → 功能开关 → 业务。

    画面请求只用画面专用连接池，名额数等于这个池的连接数上限。闸满时立即 503 SCREEN_STREAM_BUSY：
    不调用下游，所以不做鉴权、不碰数据库、不进入画面作用域，也不记日志。未登录或没有设备令牌的调用方
    在闸满时得到的也是 503 而不是 401，这是有意的：只暴露「画面繁忙」。
    这里的应用没有真实应用里的租户中间件和 CORS；中间件的层次（占用闸 → BaseHTTPMiddleware → 路由）
    与真实应用一致，真实应用整体在集成测试里通过 src.main.app 验证。
    """

    @staticmethod
    def _auth_spies(events=None):
        """网页鉴权和设备鉴权各放一个探针；被调用时在 events 里记下 (鉴权, 这一刻是否在画面作用域里)。
        两个探针都是同步函数：网页鉴权经 asyncio.to_thread 执行，设备鉴权是同步依赖、由 FastAPI 放在线程池里执行"""
        def viewer_auth(_request):
            if events is not None:
                events.append(("auth", database.in_screen_scope()))
            return {"user_id": USER}

        def device_auth():
            if events is not None:
                events.append(("auth", database.in_screen_scope()))
            return {"id": DEVICE, "tenant_id": TENANT}

        return viewer_auth, device_auth

    def _send_under_spy(self, monkeypatch, endpoint, stub_setup=None, anonymous=False, json_body=None,
                        **client_options):
        """把闸换成带记录的探针后发一个画面请求，返回 (响应, 探针记下的事件, 闸, 数据库函数的桩)。
        anonymous=True 时不给登录态、也不替换设备鉴权：请求走到鉴权就会被拒；
        stub_setup 为空时数据库函数的桩记下它被调用那一刻是否在画面作用域里"""
        method, path, default_body, stub_name, _ = endpoint
        gate = _fresh_gate(monkeypatch)
        events = []
        monkeypatch.setattr(screen_stream, "_gate", _SpySemaphore(gate, events))
        viewer_auth, device_auth = self._auth_spies(events)
        stubs, stub_patch = _patched_stubs()
        if stub_setup is not None:
            stub_setup(stubs[stub_name])
        else:
            stubs[stub_name].side_effect = _stub_sees_scope(events, stubs[stub_name].return_value)
        body = default_body if json_body is None else json_body
        if anonymous:
            with stub_patch:
                response = _send(_client(real_device_auth=True, gated=True, **client_options), method, path, body)
        else:
            with stub_patch, patch.object(local_tools_api, "get_current_user", viewer_auth):
                response = _send(_client(device_dependency=device_auth, gated=True, **client_options), method, path, body)
        return response, events, gate, stubs

    @pytest.mark.parametrize(("method", "path", "json_body", "_", "__"), SCREEN_ENDPOINTS, ids=SCREEN_ENDPOINT_IDS)
    def test_full_gate_answers_503_before_anything_else(self, monkeypatch, method, path, json_body, _, __):
        """闸满：立即 503 SCREEN_STREAM_BUSY。不调用下游：不做鉴权、不借数据库连接、不进入画面作用域、
        不记日志，也不动名额"""
        gate = _fresh_gate(monkeypatch)
        assert _take_all(gate) == 5
        viewer_auth, device_auth = self._auth_spies()
        viewer_auth, device_auth = MagicMock(side_effect=viewer_auth), MagicMock(side_effect=device_auth)
        stubs, stub_patch = _patched_stubs()
        records, handler = _capture_logs()
        try:
            with stub_patch, patch.object(local_tools_api, "get_current_user", viewer_auth), \
                 patch.object(database, "screen_connection_scope") as enter_scope, \
                 patch.object(database, "get_pooled_connection",
                              side_effect=AssertionError("闸满时不该借数据库连接")) as borrow:
                response = _send(_client(device_dependency=device_auth, gated=True), method, path, json_body)
        finally:
            logger.remove(handler)
        assert response.status_code == 503
        assert response.json() == BUSY_DETAIL
        viewer_auth.assert_not_called()
        device_auth.assert_not_called()
        for stub in stubs.values():
            stub.assert_not_called()
        enter_scope.assert_not_called()
        assert borrow.call_count == 0
        assert not [record for record in records if record["name"].startswith("src.local_tools")]
        assert _permits_available(gate) == 0

    @pytest.mark.parametrize(("method", "path", "json_body", "_", "__"), SCREEN_ENDPOINTS, ids=SCREEN_ENDPOINT_IDS)
    def test_full_gate_comes_before_the_feature_switch_and_authentication(self, monkeypatch, method, path,
                                                                        json_body, _, __):
        """闸排在功能开关和鉴权之前（功能已关闭、没有登录态也没有设备令牌时发请求）：
        闸满时得到的是「画面繁忙」（503），不是 401，也不是「功能未启用」；闸有名额时过了闸才轮到鉴权，
        在这里被拒（401），还没到功能开关"""
        gate = _fresh_gate(monkeypatch)
        stubs, stub_patch = _patched_stubs()
        with stub_patch, patch.object(settings.screen_stream, "enabled", False):
            assert _take_all(gate) == 5
            turned_away = _send(_client(real_device_auth=True, gated=True), method, path, json_body)
            gate.release()
            past_the_gate = _send(_client(real_device_auth=True, gated=True), method, path, json_body)
        assert turned_away.status_code == 503 and turned_away.json() == BUSY_DETAIL
        assert past_the_gate.status_code == 401
        for stub in stubs.values():
            stub.assert_not_called()

    @pytest.mark.parametrize("endpoint", SCREEN_ENDPOINTS, ids=SCREEN_ENDPOINT_IDS)
    def test_passed_request_runs_inside_the_screen_scope(self, monkeypatch, endpoint):
        """放行的请求在画面作用域里处理：中间件拿到名额之后，鉴权和数据库函数（各自在别的线程里执行）
        看到的都是「在画面作用域里」；请求结束后归还一次名额"""
        response, events, gate, _ = self._send_under_spy(monkeypatch, endpoint)
        assert response.status_code == 200, response.text
        assert events == ["gate", ("auth", True), ("db", True), "release"]
        assert _permits_available(gate) == 5

    @pytest.mark.parametrize("endpoint", SCREEN_ENDPOINTS, ids=SCREEN_ENDPOINT_IDS)
    @pytest.mark.parametrize(("label", "stub_setup", "expected_status", "expected_events"), [
        ("正常返回", None, 200, ["gate", ("auth", True), ("db", True), "release"]),
        ("类型化错误", _stub_raises(screen_stream.ScreenStreamError("SCREEN_SESSION_ENDED")), 409,
         ["gate", ("auth", True), "release"]),
        ("其他异常", _stub_raises(RuntimeError("boom")), 500, ["gate", ("auth", True), "release"]),
    ], ids=["normal-return", "screen-stream-error", "other-exception"])
    def test_permit_is_released_once_however_the_request_ends(self, monkeypatch, endpoint, label, stub_setup,
                                                             expected_status, expected_events):
        response, events, gate, _ = self._send_under_spy(monkeypatch, endpoint, stub_setup, raise_server_exceptions=False)
        assert response.status_code == expected_status, response.text
        assert events == expected_events
        assert _permits_available(gate) == 5

    @pytest.mark.parametrize("endpoint", SCREEN_ENDPOINTS[1:], ids=SCREEN_ENDPOINT_IDS[1:])
    def test_permit_is_released_when_the_handler_returns_early(self, monkeypatch, endpoint):
        """处理函数提前返回（没有这条会话，404）：名额照样归还"""
        response, events, gate, _ = self._send_under_spy(monkeypatch, endpoint, _stub_returns(None))
        assert response.status_code == 404
        assert response.json() == _screen_error("SCREEN_SESSION_NOT_FOUND")
        assert events == ["gate", ("auth", True), "release"]
        assert _permits_available(gate) == 5

    def test_permit_is_released_when_create_exits_before_touching_the_database(self, monkeypatch):
        create = ("post", "/api/local-tools/devices/not-a-uuid/screen-sessions", None, "create_session", "web")
        response, events, gate, _ = self._send_under_spy(monkeypatch, create)
        assert response.status_code == 404
        assert response.json() == _screen_error("DEVICE_NOT_FOUND")
        assert events == ["gate", ("auth", True), "release"]
        assert _permits_available(gate) == 5

    @pytest.mark.parametrize("endpoint", SCREEN_ENDPOINTS, ids=SCREEN_ENDPOINT_IDS)
    def test_permit_is_released_when_authentication_rejects_the_request(self, monkeypatch, endpoint):
        """顺序：占用闸 → 鉴权 → …。没有登录态、没有设备令牌的请求在鉴权处被拒（401），名额照样归还"""
        response, events, gate, _ = self._send_under_spy(monkeypatch, endpoint, anonymous=True)
        assert response.status_code == 401
        assert events == ["gate", "release"]
        assert _permits_available(gate) == 5

    @pytest.mark.parametrize("endpoint", SCREEN_ENDPOINTS, ids=SCREEN_ENDPOINT_IDS)
    def test_permit_is_released_when_the_feature_is_disabled(self, monkeypatch, endpoint):
        """顺序：占用闸 → 鉴权 → 功能开关。闸有名额时功能未启用照常是 503 SCREEN_STREAM_DISABLED，名额照样归还"""
        with patch.object(settings.screen_stream, "enabled", False):
            response, events, gate, _ = self._send_under_spy(monkeypatch, endpoint)
        assert response.status_code == 503
        assert response.json() == _screen_error("SCREEN_STREAM_DISABLED")
        assert events == ["gate", ("auth", True), "release"]
        assert _permits_available(gate) == 5

    @pytest.mark.parametrize(("endpoint", "json_body", "expected_events"), [
        # 网页接口的鉴权在函数体里：请求体校验失败时还没走到鉴权
        (SCREEN_ENDPOINTS[2], {"type": "bogus", "payload": {}}, ["gate", "release"]),
        # 设备接口的鉴权是依赖：先鉴权，然后才校验请求体
        (SCREEN_ENDPOINTS[4], {"session_id": "not-a-uuid", "after_seq": 0}, ["gate", ("auth", True), "release"]),
        (SCREEN_ENDPOINTS[5], {"session_id": SESSION, "type": "start", "payload": {}}, ["gate", ("auth", True), "release"]),
    ], ids=["viewer-signal", "runtime-read", "runtime-signal"])
    def test_permit_is_released_when_the_body_is_rejected(self, monkeypatch, endpoint, json_body, expected_events):
        """请求体不合法（422）：名额在中间件里已经拿到，校验失败后照样归还"""
        response, events, gate, _ = self._send_under_spy(monkeypatch, endpoint, json_body=json_body)
        assert response.status_code == 422
        assert events == expected_events
        assert _permits_available(gate) == 5

    def test_permit_is_released_once_when_the_request_is_cancelled(self, monkeypatch):
        """处理函数还没返回时请求被取消（比如服务关闭）：名额照样归还一次"""
        import threading

        import anyio
        import httpx

        gate = _fresh_gate(monkeypatch)
        events = []
        monkeypatch.setattr(screen_stream, "_gate", _SpySemaphore(gate, events))
        stubs, stub_patch = _patched_stubs()
        entered, let_go = threading.Event(), threading.Event()

        def hang(*args, **kwargs):
            entered.set()
            let_go.wait(timeout=10)

        stubs["load_session"].side_effect = hang

        async def start_then_cancel():
            transport = httpx.ASGITransport(app=_client(gated=True).app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as live:
                async def request():
                    await live.get(SESSION_PATH)

                async with anyio.create_task_group() as group:
                    group.start_soon(request)
                    # 等请求进了数据库函数再取消
                    assert await asyncio.get_running_loop().run_in_executor(None, entered.wait, 10)
                    group.cancel_scope.cancel()
            let_go.set()  # 让卡在数据库函数里的线程结束

        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user):
            asyncio.run(start_then_cancel())
        assert events == ["gate", "release"]
        assert _permits_available(gate) == 5

    def test_next_request_gets_the_permit_once_one_is_given_back(self, monkeypatch):
        gate = _fresh_gate(monkeypatch)
        assert _take_all(gate) == 5
        _, device_auth = self._auth_spies()
        stubs, stub_patch = _patched_stubs()
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user), \
             _client(device_dependency=device_auth, gated=True) as client:
            refused = _send(client, "get", SESSION_PATH, None)
            gate.release()  # 另一个画面请求做完，归还了一个名额
            served = _send(client, "get", SESSION_PATH, None)
        assert refused.status_code == 503 and refused.json() == BUSY_DETAIL
        assert served.status_code == 200
        stubs["load_session"].assert_called_once()
        assert _permits_available(gate) == 1  # 其余 4 个名额仍然被这个测试占着

    def test_requests_that_are_not_screen_requests_pass_through(self, monkeypatch):
        """不是画面接口的请求原样交给下游：不拿名额、不进入画面作用域，闸满了也一样。
        不是 HTTP 的 scope（启动、关闭、WebSocket）同样原样透传"""
        gate = _fresh_gate(monkeypatch)
        assert _take_all(gate) == 5
        seen = []

        async def downstream(scope, receive, send):
            path = get_route_path(scope) if scope["type"] == "http" else None
            seen.append((scope["type"], path, database.in_screen_scope()))

        middleware = screen_stream.ScreenStreamGateMiddleware(downstream)
        paths = ("/", "/api/local-tools/runtime/heartbeat", "/api/local-tools/devices", "/api/saas/tenants")

        async def run():
            await middleware({"type": "lifespan"}, None, None)
            await middleware({"type": "websocket", "path": "/ws"}, None, None)
            for path in paths:
                await middleware({"type": "http", "method": "POST", "path": path, "root_path": "", "headers": []},
                                 None, None)

        asyncio.run(run())
        assert seen == [("lifespan", None, False), ("websocket", None, False)] + [("http", path, False) for path in paths]
        assert _permits_available(gate) == 0

    def test_screen_path_is_recognized_without_the_root_path_and_refused_by_the_busy_app(self, monkeypatch):
        """请求路径带着反向代理的前缀（root_path）时，按去掉前缀后的路由路径判断是画面接口；
        闸满时响应由构造时传入的 busy_app 产生，下游不被调用"""
        gate = _fresh_gate(monkeypatch)
        assert _take_all(gate) == 5
        downstream, busy = MagicMock(side_effect=_never_called), MagicMock(side_effect=_asgi_noop)
        middleware = screen_stream.ScreenStreamGateMiddleware(downstream, busy_app=busy)
        scope = {"type": "http", "method": "POST", "path": "/prefix/api/local-tools/runtime/screen/session",
                 "root_path": "/prefix", "headers": []}
        asyncio.run(middleware(scope, None, None))
        downstream.assert_not_called()
        busy.assert_called_once_with(scope, None, None)
        assert _permits_available(gate) == 0

    def test_busy_app_wrapped_in_cors_adds_the_allow_origin_header_only_for_an_allowed_origin(self, monkeypatch):
        """main.py 给拒绝响应包的那层 CORS：带允许的 Origin 时带 access-control-allow-origin，不带（或不被允许）时不加"""
        gate = _fresh_gate(monkeypatch)
        assert _take_all(gate) == 5
        busy_app = CORSMiddleware(screen_stream.busy_response, allow_origins=["http://viewer.test"],
                                  allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
        client = TestClient(screen_stream.ScreenStreamGateMiddleware(_never_called, busy_app=busy_app))
        path = "/api/local-tools/runtime/screen/session"
        allowed = client.post(path, headers={"Origin": "http://viewer.test"})
        other = client.post(path, headers={"Origin": "http://other.test"})
        bare = client.post(path)
        for response in (allowed, other, bare):
            assert response.status_code == 503 and response.json() == BUSY_DETAIL
        assert allowed.headers["access-control-allow-origin"] == "http://viewer.test"
        assert "access-control-allow-origin" not in other.headers
        assert "access-control-allow-origin" not in bare.headers

    def test_busy_response_is_identical_to_the_503_raised_by_a_screen_endpoint(self, monkeypatch):
        """中间件回的 503 与画面接口自己抛出 SCREEN_STREAM_BUSY（等锁超时）时路由层的响应逐字节相同"""
        gate = _fresh_gate(monkeypatch)
        stubs, stub_patch = _patched_stubs()
        stubs["load_session"].side_effect = screen_stream.ScreenStreamError("SCREEN_STREAM_BUSY")
        with stub_patch, patch.object(local_tools_api, "get_current_user", _as_user):
            client = _client(gated=True)
            raised_by_route = client.get(SESSION_PATH)
            assert _take_all(gate) == 5
            refused_by_gate = client.get(SESSION_PATH)
        assert refused_by_gate.status_code == raised_by_route.status_code == 503
        assert refused_by_gate.content == raised_by_route.content
        assert refused_by_gate.headers["content-type"] == raised_by_route.headers["content-type"]
        assert refused_by_gate.json() == BUSY_DETAIL


class TestScopeInheritance:
    def test_scope_reaches_every_thread_that_runs_a_screen_request(self):
        """画面作用域靠 ContextVar 传递，值随上下文复制。中间件放行后：路由所在的协程、线程池里执行的同步依赖、
        asyncio.to_thread 启动的线程，看到的都是「在画面作用域里」；中间件和路由之间隔着一层
        BaseHTTPMiddleware（真实应用里是租户中间件）。不是画面接口的请求，三处都不在"""
        class InBetween(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                return await call_next(request)

        def sync_dependency():
            return database.in_screen_scope()

        app = FastAPI()

        @app.get(f"/api/local-tools/devices/{DEVICE}/screen-sessions/{SESSION}")
        @app.post("/api/local-tools/runtime/heartbeat")
        async def report(in_dependency: bool = Depends(sync_dependency)):
            return {"handler": database.in_screen_scope(), "dependency": in_dependency,
                    "to_thread": await asyncio.to_thread(database.in_screen_scope)}

        app.add_middleware(InBetween)
        app.add_middleware(screen_stream.ScreenStreamGateMiddleware)
        client = TestClient(app)
        assert client.get(f"/api/local-tools/devices/{DEVICE}/screen-sessions/{SESSION}").json() == {
            "handler": True, "dependency": True, "to_thread": True}
        assert client.post("/api/local-tools/runtime/heartbeat").json() == {
            "handler": False, "dependency": False, "to_thread": False}


class TestScreenPathMatching:
    """占用闸只认六个画面接口的路径。匹配规则是独立写的，所以拿本地工具路由的全部路由来核对"""

    def test_exactly_the_six_screen_endpoints_match(self):
        """套到本地工具路由的全部路由上，匹配到的恰好是六个画面接口（按路由表里的写法逐一列出）：
        以后新增画面接口而忘了改匹配规则时，这里会失败"""
        all_routes = {(method, route.path) for route in local_tools_api.router.routes for method in route.methods}
        assert {(method, path) for method, path in all_routes if screen_stream.is_screen_path(path)} == {
            ("POST", "/api/local-tools/devices/{device_id}/screen-sessions"),
            ("GET", "/api/local-tools/devices/{device_id}/screen-sessions/{session_id}"),
            ("POST", "/api/local-tools/devices/{device_id}/screen-sessions/{session_id}/signals"),
            ("POST", "/api/local-tools/devices/{device_id}/screen-sessions/{session_id}/end"),
            ("POST", "/api/local-tools/runtime/screen/session"),
            ("POST", "/api/local-tools/runtime/screen/signal"),
        }

    @pytest.mark.parametrize(("path", "expected"), [
        (f"/api/local-tools/devices/{DEVICE}/screen-sessions", True),
        (SESSION_PATH, True),
        (f"{SESSION_PATH}/signals", True),
        (f"{SESSION_PATH}/end", True),
        ("/api/local-tools/runtime/screen/session", True),
        ("/api/local-tools/runtime/screen/signal", True),
        # 编号不限形态（合不合法由接口自己判断），但不能是空的
        ("/api/local-tools/devices/any-id/screen-sessions/any-session/end", True),
        ("/api/local-tools/devices//screen-sessions", False),
        # 不能多出一段，也不能是别的后缀
        (f"{SESSION_PATH}/", False),
        (f"{SESSION_PATH}/signals/extra", False),
        (f"{SESSION_PATH}/bogus", False),
        # 本地工具的其他接口不是画面接口
        ("/api/local-tools/devices", False),
        (f"/api/local-tools/devices/{DEVICE}", False),
        ("/api/local-tools/runtime/heartbeat", False),
        ("/api/local-tools/runtime/screen", False),
        ("/api/local-tools/runtime/screen/sessionx", False),
        # 别的模块下的同名路径不是画面接口
        ("/api/other/runtime/screen/session", False),
    ])
    def test_path_shapes(self, path, expected):
        assert screen_stream.is_screen_path(path) is expected


class TestScreenRoutesCarryNoGateDependency:
    def test_screen_endpoints_carry_only_their_authentication_dependency(self):
        """占用闸不再是路由依赖：四个网页接口没有依赖（登录态在函数体里），两个设备接口只有 _require_device；
        三个会话函数也不再带 permit_held 参数"""
        assert not hasattr(local_tools_api, "_screen_permit")
        routes = [route for route in local_tools_api.router.routes if screen_stream.is_screen_path(route.path)]
        assert len(routes) == 6
        for route in routes:
            names = [dependency.call.__name__ for dependency in route.dependant.dependencies]
            assert names == (["_require_device"] if route.path.startswith("/api/local-tools/runtime/") else []), route.path
        for name in ("create_session", "load_session", "append_signal"):
            assert "permit_held" not in inspect.signature(getattr(screen_stream, name)).parameters


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
