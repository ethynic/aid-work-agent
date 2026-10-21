"""Phase 4：180 秒持久截止语义拆除（单元层，mock repository，无真实 DB）

计划：docs/plans/plan-recruiting-error-containment-and-input-performance.md Phase 4
- 新 durable 绑定不再把工具 timeout 持久化为 deadline_at（观察与执行解耦）
- durable 观察无 Runner 侧墙钟：超过 timeout_seconds 的健康执行不进错误验证态
- 存量投影：已保存 deadline_at 的行到期仍进 LOCAL_DEADLINE_VERIFICATION_REQUIRED
- 租约停续 → 权威清扫器收敛 unknown → EXECUTION_UNKNOWN（效果未知保护保留）
- 显式取消：无 deadline 的未终态行等待回执（ACK_PENDING），不再判 DEADLINE
- 前端断开（durable owner cancel_on_detach=False）不请求取消；恢复复用原调用不重发
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.local_tools.durable_flow import LocalContinuationRequired, original_invocation
from src.local_tools.overlay_flow import poll_operation
from src.local_tools.proxy_tool import BossGotoTool
from src.services.agent_runner.local_recovery import (
    cancel_completion_status,
    stored_deadline_expired,
)

pytestmark = pytest.mark.unit

TENANT = "tenant_t1"
USER = "user_t1"
REPO = "src.local_tools.proxy_tool.repository"
UTC = timezone.utc


def _operation(owner, branch="main", ordinal=0):
    return SimpleNamespace(owner=owner, branch=branch, ordinal=ordinal)


class TestOriginalInvocationBinding:
    """新绑定不持久化工具 timeout；存量 deadline 原样透传（复用判定在真实 owner 内）"""

    async def test_new_binding_persists_no_deadline(self):
        owner = SimpleNamespace(
            saved_phase=AsyncMock(return_value=None),
            bind_invocation=AsyncMock(return_value={"id": "inv-new", "deadline_at": None}),
        )
        result = await original_invocation(
            BossGotoTool(), _operation(owner), {"id": "dev-1"}, {"a": 1})
        assert result["id"] == "inv-new"
        owner.bind_invocation.assert_awaited_once()
        assert owner.bind_invocation.await_args.kwargs["deadline_at"] is None

    async def test_saved_legacy_deadline_passes_through_unchanged(self):
        """存量投影：saved phase 的旧 deadline 原值透传（由 owner 侧复用，不重排队）"""
        legacy = datetime.now(UTC) + timedelta(seconds=60)
        owner = SimpleNamespace(
            saved_phase=AsyncMock(return_value={
                "request": {"deadline_at": legacy.isoformat(), "tool_name": "boss_goto"},
                "invocation": {"id": "inv-old", "deadline_at": legacy},
            }),
            bind_invocation=AsyncMock(return_value={"id": "inv-old"}),
        )
        result = await original_invocation(BossGotoTool(), _operation(owner), {"id": "dev-1"}, {})
        assert result["id"] == "inv-old"
        owner.bind_invocation.assert_awaited_once()
        assert owner.bind_invocation.await_args.kwargs["deadline_at"] == legacy

    async def test_saved_phase_without_deadline_binds_none(self):
        owner = SimpleNamespace(
            saved_phase=AsyncMock(return_value={
                "request": {"deadline_at": None}, "invocation": {"id": "inv-resume"},
            }),
            bind_invocation=AsyncMock(return_value={"id": "inv-resume"}),
        )
        result = await original_invocation(BossGotoTool(), _operation(owner), {"id": "dev-1"}, {})
        assert result["id"] == "inv-resume"
        owner.bind_invocation.assert_awaited_once()
        assert owner.bind_invocation.await_args.kwargs["deadline_at"] is None


def _running_invocation(**extra):
    return {"id": "inv-1", "state": "running", "effect": None,
            "result_json": {}, "deadline_at": None, "credit_cost": None, **extra}


def _succeeded_invocation():
    return {"id": "inv-1", "state": "succeeded", "effect": "applied",
            "result_json": {"success": True, "message": "完成", "data": {}},
            "deadline_at": None, "credit_cost": None}


def _patch_poll(invocations, *, lease_stale=False):
    """invocations：get_invocation 依次返回的行；claim_lease_expired 由 lease_stale 决定"""
    mocks = dict(
        list_events=MagicMock(return_value=[]),
        get_invocation=MagicMock(side_effect=list(invocations)),
        claim_lease_expired=MagicMock(return_value=lease_stale),
        expire_stale_claims=MagicMock(return_value=1),
        request_cancel=MagicMock(return_value=True),
        observed_invocation=MagicMock(side_effect=lambda inv: inv),
    )
    return patch.multiple(REPO, **mocks), mocks


class TestDurablePollObservation:
    """durable 轮询：无墙钟总时限；存量 deadline 投影；租约活性收敛"""

    @pytest.fixture(autouse=True)
    def _fast_poll(self, monkeypatch):
        monkeypatch.setattr("src.local_tools.proxy_tool.POLL_INTERVAL_SECONDS", 0.01)

    async def test_healthy_execution_beyond_timeout_keeps_waiting(self):
        """timeout_seconds=0 且执行归属有效（租约活）→ 不进错误验证态，等至终态

        旧逻辑下 durable 无 deadline 落到本地观察窗：timeout_seconds=0 会立即
        request_cancel+TIMEOUT；此用例证明观察窗与工具 timeout 解耦。
        """
        tool = BossGotoTool()
        tool.timeout_seconds = 0
        patches, mocks = _patch_poll([
            _running_invocation(), _running_invocation(), _succeeded_invocation(),
        ])
        with patches:
            result = await tool._poll_invocation(
                {"id": "inv-1", "deadline_at": None}, TENANT, None, durable=True)
        assert result["success"] is True
        mocks["request_cancel"].assert_not_called()
        mocks["claim_lease_expired"].assert_called()

    async def test_legacy_deadline_expiry_still_parks_verification(self):
        """存量投影：行上保存的旧 deadline 到期仍抛 LOCAL_DEADLINE_VERIFICATION_REQUIRED"""
        expired = datetime.now(UTC) - timedelta(seconds=1)
        tool = BossGotoTool()
        patches, mocks = _patch_poll([_running_invocation(deadline_at=expired)])
        with patches:
            with pytest.raises(LocalContinuationRequired) as raised:
                await tool._poll_invocation(
                    {"id": "inv-1", "deadline_at": expired}, TENANT, None, durable=True)
        assert raised.value.code == "LOCAL_DEADLINE_VERIFICATION_REQUIRED"
        mocks["request_cancel"].assert_not_called()
        mocks["expire_stale_claims"].assert_not_called()

    async def test_legacy_deadline_in_future_does_not_park(self):
        future = datetime.now(UTC) + timedelta(seconds=60)
        tool = BossGotoTool()
        tool.timeout_seconds = 0
        patches, _ = _patch_poll([
            _running_invocation(deadline_at=future), _succeeded_invocation(),
        ])
        with patches:
            result = await tool._poll_invocation(
                {"id": "inv-1", "deadline_at": future}, TENANT, None, durable=True)
        assert result["success"] is True

    async def test_stale_lease_converges_through_sweeper_to_unknown(self):
        """租约停续且无回执 → 权威清扫器收敛 unknown → EXECUTION_UNKNOWN（保护保留）"""
        unknown = {"id": "inv-1", "state": "unknown", "effect": "unknown",
                   "result_json": {}, "deadline_at": None, "credit_cost": None}
        tool = BossGotoTool()
        patches, mocks = _patch_poll([_running_invocation(), unknown], lease_stale=True)
        with patches:
            result = await tool._poll_invocation(
                {"id": "inv-1", "deadline_at": None}, TENANT, None, durable=True)
        assert result["success"] is False
        assert result["code"] == "EXECUTION_UNKNOWN"
        mocks["expire_stale_claims"].assert_called_once()
        mocks["request_cancel"].assert_not_called()

    async def test_queued_without_lease_keeps_waiting_without_sweep(self):
        """queued 尚无执行归属（无租约）：不触发租约清扫，等待领取/显式取消"""
        queued = _running_invocation(state="queued")
        tool = BossGotoTool()
        patches, mocks = _patch_poll([queued, queued, _succeeded_invocation()])
        with patches:
            result = await tool._poll_invocation(
                {"id": "inv-1", "deadline_at": None}, TENANT, None, durable=True)
        assert result["success"] is True
        mocks["claim_lease_expired"].assert_not_called()
        mocks["expire_stale_claims"].assert_not_called()

    async def test_legacy_non_durable_timeout_keeps_request_cancel(self):
        """非 durable 旧路径回归：本地观察窗到期仍 request_cancel + TIMEOUT"""
        tool = BossGotoTool()
        tool.timeout_seconds = 0
        patches, mocks = _patch_poll([_running_invocation(deadline_at=None)])
        with patches:
            result = await tool._poll_invocation(
                {"id": "inv-1", "deadline_at": None}, TENANT, None, durable=False)
        assert result["success"] is False
        assert result["code"] == "TIMEOUT"
        mocks["request_cancel"].assert_called_once_with("inv-1", TENANT)


class TestStoredDeadlineProjection:
    def test_no_deadline_is_not_expired(self):
        assert stored_deadline_expired({"deadline_at": None, "state": "running"}) is False

    def test_expired_deadline_with_pending_state_is_expired(self):
        expired = datetime.now(UTC) - timedelta(seconds=1)
        assert stored_deadline_expired(
            {"deadline_at": expired, "state": "running"}) is True

    def test_terminal_state_never_counts_as_expired(self):
        expired = datetime.now(UTC) - timedelta(seconds=1)
        assert stored_deadline_expired(
            {"deadline_at": expired, "state": "succeeded"}) is False

    def test_future_deadline_is_not_expired(self):
        future = datetime.now(UTC) + timedelta(seconds=60)
        assert stored_deadline_expired(
            {"deadline_at": future, "state": "running"}) is False


class TestCancelCompletionStatus:
    def _local_fact(self, state, effect=None, deadline=...):
        return {"kind": "local", "holdable": True, "completion_known": False,
                "id": "inv-1", "state": state, "effect": effect,
                "deadline_at": None if deadline is ... else deadline}

    def test_pending_without_deadline_waits_for_ack(self):
        ready, issue = cancel_completion_status([self._local_fact("cancel_requested")])
        assert ready is False and issue == "LOCAL_CANCEL_ACK_PENDING"

    def test_pending_with_future_deadline_waits_for_ack(self):
        future = datetime.now(UTC) + timedelta(seconds=60)
        ready, issue = cancel_completion_status(
            [self._local_fact("cancel_requested", deadline=future)])
        assert ready is False and issue == "LOCAL_CANCEL_ACK_PENDING"

    def test_pending_with_expired_deadline_keeps_verification(self):
        expired = datetime.now(UTC) - timedelta(seconds=1)
        ready, issue = cancel_completion_status(
            [self._local_fact("cancel_requested", deadline=expired)])
        assert ready is False and issue == "LOCAL_CANCEL_DEADLINE_VERIFICATION_REQUIRED"

    def test_unknown_effect_keeps_verification_protection(self):
        ready, issue = cancel_completion_status(
            [self._local_fact("unknown", effect="unknown")])
        assert ready is False and issue == "LOCAL_CANCEL_EFFECT_VERIFICATION_REQUIRED"


class _NullContext:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


class TestFrontendDetachDoesNotCancelOrRedispatch:
    """durable 运行断开（生成器提前关闭）不取消 invocation；恢复复用原调用

    durable 构造来源：runtime/executor.py `cancel_on_detach=not durable_owner`。
    """

    @staticmethod
    def _adapter(cancel_on_detach):
        from src.services.agent_runner.runtime.local_tools import LocalToolAdapter

        async def slow_execute(tool_name, execution_args, context=None):
            queue = execution_args["_progress_queue"]
            queue.put_nowait({"type": "started", "invocation_id": "inv-1", "text": "已下发"})
            await asyncio.Event().wait()  # 模拟本机长执行

        tool_executor = SimpleNamespace(
            execute=slow_execute,
            registry=SimpleNamespace(get_tool=MagicMock(return_value=object())),
        )
        return LocalToolAdapter(
            session_id="sess-1", execution_id="exec-1", subagent_config=None,
            llm=None, tool_executor=tool_executor, cancel_on_detach=cancel_on_detach)

    @pytest.fixture(autouse=True)
    def _fake_context(self, monkeypatch):
        class FakeContextFactory:
            @staticmethod
            def for_agent_call(**kwargs):
                return SimpleNamespace(
                    session_id=kwargs.get("session_id"),
                    tenant_id=kwargs.get("tenant_id"),
                    user_id=kwargs.get("user_id"),
                    tool_call_id="call-1",
                )
        monkeypatch.setattr("src.tools.context.ExecutionContextFactory", FakeContextFactory)
        monkeypatch.setattr("src.tools.context.tool_execution_scope",
                            lambda context: _NullContext())

    async def _first_event_then_close(self, adapter):
        with patch(f"{REPO}.request_cancel") as request_cancel:
            generator = adapter._run_local_required_tool("boss_goto", {}, TENANT, USER)
            seen = []
            async for kind, payload in generator:
                seen.append((kind, payload))
                break  # 前端断开：提前结束订阅
            await generator.aclose()
            return seen, request_cancel

    async def test_durable_detach_does_not_request_cancel(self):
        seen, request_cancel = await self._first_event_then_close(
            self._adapter(cancel_on_detach=False))
        assert seen[0] == ("invocation", "inv-1")
        request_cancel.assert_not_called()

    async def test_legacy_detach_still_requests_cancel(self):
        _, request_cancel = await self._first_event_then_close(
            self._adapter(cancel_on_detach=True))
        request_cancel.assert_called_once_with("inv-1", TENANT)

    async def test_saved_phase_poll_reuses_original_invocation(self):
        """恢复（重连）走 saved phase：同一 invocation 轮询（durable），不新建、不重发"""
        saved_invocation = {"id": "inv-keep", "state": "succeeded", "effect": "applied",
                            "result_json": {"success": True, "message": "完成", "data": {}},
                            "deadline_at": None, "credit_cost": None}
        owner = SimpleNamespace(saved_phase=AsyncMock(return_value={
            "request": {"arguments": {}, "tool_name": "boss_goto",
                        "device_id": "dev-1", "deadline_at": None},
            "invocation": saved_invocation,
        }))
        proxy = BossGotoTool()
        proxy._poll_invocation = AsyncMock(return_value={
            "success": True, "code": None, "message": "完成", "effect": "applied",
            "data": {}, "invocation_id": "inv-keep"})
        result = await poll_operation(proxy, _operation(owner), {},
                                      tenant_id=TENANT, user_id=USER, session_id="sess-1",
                                      progress_queue=None)
        assert result["invocation_id"] == "inv-keep"
        proxy._poll_invocation.assert_awaited_once()
        assert proxy._poll_invocation.await_args.args[0] is saved_invocation
        assert proxy._poll_invocation.await_args.kwargs.get("durable") is True
