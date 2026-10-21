"""Phase 4 独立回归：180 秒持久截止语义拆除（独立测试视角，mock repository，无真实 DB）

计划：docs/plans/plan-recruiting-error-containment-and-input-performance.md Phase 4
与开发自测 tests/unit/local_tools/test_durable_deadline_semantics.py 相互独立，
断言角度来自计划验收清单：
1. 超过 180 秒但执行归属有效的操作不进入错误验证态（保持类默认 timeout_seconds=180，
   用可控 FakeLoop 时钟推进真实超过 180 秒墙钟）；
2. 前端断开再订阅：任务不取消、不重发（adapter 断开 + 重连复用同一 invocation）；
3. 已保存旧 deadline_at 的存量任务兼容（投影生效：poll 层 / can_restore 层 / 绑定透传）；
4. 租约、身份校验、显式取消、效果未知保护未削弱；timeout_seconds 只保留非 durable
   旧路径观察窗语义（180 未被放大）。
"""

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.local_tools.durable_flow import LocalContinuationRequired, original_invocation
from src.local_tools.overlay_flow import poll_operation
from src.local_tools.proxy_tool import BossGotoTool, LocalToolProxyTool
from src.services.agent_runner.local_invocations import LocalPhase
from src.services.agent_runner.local_owner import RunnerLocalLifecycle
from src.services.agent_runner.local_recovery import (
    LocalRecoveryProof,
    cancel_completion_status,
)

pytestmark = pytest.mark.unit

TENANT = "tenant_ind"
USER = "user_ind"
CALL_ID = "call-ind-1"
RUNNER = "runner-ind-1"
UTC = timezone.utc
REPO = "src.local_tools.proxy_tool.repository"


class FakeLoop:
    """可控时钟：替代 _poll_invocation 内 asyncio.get_event_loop() 的 loop.time()"""

    def __init__(self, start=1000.0, step=100.0):
        self._now = start
        self._step = step

    def advance(self):
        self._now += self._step

    def time(self):
        return self._now


def _running(**extra):
    return {"id": "inv-ind", "state": "running", "effect": None, "result_json": {},
            "deadline_at": None, "credit_cost": None, **extra}


def _succeeded(**extra):
    return {"id": "inv-ind", "state": "succeeded", "effect": "applied",
            "result_json": {"success": True, "message": "完成", "data": {}},
            "deadline_at": None, "credit_cost": None, **extra}


def _unknown():
    return {"id": "inv-ind", "state": "unknown", "effect": "unknown", "result_json": {},
            "deadline_at": None, "credit_cost": None}


def _repo_mocks(get_invocation, *, lease_stale=False):
    """patch proxy_tool 命名空间内的 repository；get_invocation 为可调用"""
    mocks = dict(
        list_events=MagicMock(return_value=[]),
        get_invocation=MagicMock(side_effect=get_invocation),
        claim_lease_expired=MagicMock(return_value=lease_stale),
        expire_stale_claims=MagicMock(return_value=1),
        request_cancel=MagicMock(return_value=True),
        observed_invocation=MagicMock(side_effect=lambda inv: inv),
    )
    return patch.multiple(REPO, **mocks), mocks


# ====================================================================
# 验收 1：超过 180 秒但执行归属有效的操作不进错误验证态
# ====================================================================

class TestHealthyExecutionBeyond180Seconds:
    @pytest.fixture(autouse=True)
    def _fast_poll(self, monkeypatch):
        monkeypatch.setattr("src.local_tools.proxy_tool.POLL_INTERVAL_SECONDS", 0.01)

    async def test_wall_clock_far_beyond_180s_keeps_observing_until_terminal(self):
        """timeout_seconds 保持类默认 180；时钟累计推进 500+ 秒远超 180 秒观察窗，
        行为 running 且租约活（claim_lease_expired=False）→ 不停车、不取消、
        不进 LOCAL_DEADLINE_VERIFICATION_REQUIRED，等至 succeeded。"""
        tool = BossGotoTool()
        assert tool.timeout_seconds == 180  # 未放大、未缩小，语义拆除而非数字调整
        fake_loop = FakeLoop(start=1000.0, step=100.0)

        def get_invocation(_inv_id, _tenant):
            # 每次读取推进时钟 100 秒：5 次读取后 fake 时钟 1500 > deadline(1180)
            fake_loop.advance()
            return _running() if fake_loop._now < 1400 else _succeeded()

        patches, mocks = _repo_mocks(get_invocation, lease_stale=False)
        with patches, patch("asyncio.get_event_loop", return_value=fake_loop):
            result = await tool._poll_invocation(
                {"id": "inv-ind", "deadline_at": None}, TENANT, None, durable=True)
        assert fake_loop._now >= 1400  # 确认墙钟确实越过了 1000+180
        assert result["success"] is True
        mocks["request_cancel"].assert_not_called()
        mocks["claim_lease_expired"].assert_called()  # 执行归属以租约活性观察

    async def test_durable_path_never_consumes_loop_wallclock(self):
        """durable 分支不消费 loop.time()：时钟直接给到极端未来值仍健康等待至终态。"""
        fake_loop = FakeLoop(start=10**9, step=0.0)
        tool = BossGotoTool()
        seen = iter([_running(), _succeeded()])
        patches, mocks = _repo_mocks(lambda *_: next(seen))
        with patches, patch("asyncio.get_event_loop", return_value=fake_loop):
            result = await tool._poll_invocation(
                {"id": "inv-ind", "deadline_at": None}, TENANT, None, durable=True)
        assert result["success"] is True
        mocks["request_cancel"].assert_not_called()


# ====================================================================
# 验收 2：前端断开再订阅：任务不取消、不重发
# ====================================================================

class _NullContext:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


class TestDetachAndReconnect:
    @staticmethod
    def _adapter(cancel_on_detach):
        from src.services.agent_runner.runtime.local_tools import LocalToolAdapter

        async def slow_execute(tool_name, execution_args, context=None):
            queue = execution_args["_progress_queue"]
            queue.put_nowait({"type": "started", "invocation_id": "inv-ind", "text": "已下发"})
            await asyncio.Event().wait()  # 本机长执行

        tool_executor = SimpleNamespace(
            execute=slow_execute,
            registry=SimpleNamespace(get_tool=MagicMock(return_value=MagicMock())),
        )
        return LocalToolAdapter(
            session_id="sess-ind", execution_id="exec-ind", subagent_config=None,
            llm=None, tool_executor=tool_executor, cancel_on_detach=cancel_on_detach)

    @pytest.fixture(autouse=True)
    def _fake_context(self, monkeypatch):
        class FakeContextFactory:
            @staticmethod
            def for_agent_call(**kwargs):
                return SimpleNamespace(
                    session_id=kwargs.get("session_id"), tenant_id=kwargs.get("tenant_id"),
                    user_id=kwargs.get("user_id"), tool_call_id=CALL_ID)

        monkeypatch.setattr("src.tools.context.ExecutionContextFactory", FakeContextFactory)
        monkeypatch.setattr("src.tools.context.tool_execution_scope",
                            lambda context: _NullContext())

    async def _first_event_then_close(self, adapter):
        with patch(f"{REPO}.request_cancel") as request_cancel:
            generator = adapter._run_local_required_tool("boss_goto", {"target": "recommend"}, TENANT, USER)
            async for item in generator:
                assert item[0] == "invocation"
                break  # 前端断开：提前结束订阅
            await generator.aclose()
            await asyncio.sleep(0)  # 让 finally 分支跑完
            return request_cancel

    async def test_durable_detach_does_not_cancel(self):
        request_cancel = await self._first_event_then_close(self._adapter(cancel_on_detach=False))
        request_cancel.assert_not_called()

    async def test_legacy_detach_still_cancels_orphan(self):
        request_cancel = await self._first_event_then_close(self._adapter(cancel_on_detach=True))
        request_cancel.assert_called_once_with("inv-ind", TENANT)

    async def test_reconnect_resubscribes_same_invocation_without_redispatch(self):
        """断开（中途取消观察）→ 重连（再次订阅）：saved phase 复用同一 invocation，
        全程无 bind（不重发）、无 request_cancel（不取消），最终等至原调用终态。"""
        terminal = {"flag": False}
        saved_invocation_running = {"id": "inv-keep", "state": "running", "effect": None,
                                    "result_json": {}, "deadline_at": None, "credit_cost": None}
        saved_invocation_done = {"id": "inv-keep", "state": "succeeded", "effect": "applied",
                                 "result_json": {"success": True, "message": "完成", "data": {}},
                                 "deadline_at": None, "credit_cost": None}
        owner = SimpleNamespace(
            saved_phase=AsyncMock(return_value={
                "request": {"arguments": {"target": "recommend"}, "tool_name": "boss_goto",
                            "device_id": "dev-1", "deadline_at": None},
                "invocation": saved_invocation_running,
            }),
            bind_invocation=AsyncMock(),
        )
        operation = SimpleNamespace(owner=owner, branch="main", ordinal=0)
        proxy = BossGotoTool()

        def get_invocation(_inv_id, _tenant):
            return saved_invocation_done if terminal["flag"] else _running()

        # 第一次订阅：观察中途前端断开（协程被取消）
        patches, mocks = _repo_mocks(get_invocation)
        with patches:
            task = asyncio.create_task(poll_operation(
                proxy, operation, {"target": "recommend"},
                tenant_id=TENANT, user_id=USER, session_id="sess-ind", progress_queue=None))
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            # 重连：再次订阅同一 saved phase
            owner.saved_phase.return_value["invocation"] = saved_invocation_done
            terminal["flag"] = True
            result = await poll_operation(
                proxy, operation, {"target": "recommend"},
                tenant_id=TENANT, user_id=USER, session_id="sess-ind", progress_queue=None)
        assert result["success"] is True and result["invocation_id"] == "inv-keep"
        owner.bind_invocation.assert_not_called()  # 不重发
        assert owner.saved_phase.await_count == 2  # 两次订阅均读 saved phase
        mocks["request_cancel"].assert_not_called()  # 不取消

    def test_executor_wires_durable_owner_to_cancel_on_detach_false(self):
        """静态复核（辅助证据）：executor 对 durable owner 构造 cancel_on_detach=False。"""
        source = Path(__file__).resolve().parents[3].joinpath(
            "src/services/agent_runner/runtime/executor.py").read_text(encoding="utf-8")
        assert 'cancel_on_detach=not getattr(self.control, "durable_owner", False)' in source


# ====================================================================
# 验收 3：已保存旧 deadline_at 的存量任务兼容（投影生效）
# ====================================================================

class TestLegacyDeadlineProjection:
    async def test_saved_legacy_deadline_passes_through_binding(self):
        legacy = datetime.now(UTC) + timedelta(seconds=60)
        owner = SimpleNamespace(
            saved_phase=AsyncMock(return_value={
                "request": {"deadline_at": legacy.isoformat(), "tool_name": "boss_goto"},
                "invocation": {"id": "inv-old", "deadline_at": legacy},
            }),
            bind_invocation=AsyncMock(return_value={"id": "inv-old"}),
        )
        await original_invocation(BossGotoTool(), SimpleNamespace(
            owner=owner, branch="main", ordinal=0), {"id": "dev-1"}, {"target": "recommend"})
        assert owner.bind_invocation.await_args.kwargs["deadline_at"] == legacy

    async def test_expired_deadline_parks_via_overlay_poll_operation(self):
        """poll_operation（overlay_flow 层，走 durable=True 全链路）：存量过期 deadline
        抛 LOCAL_DEADLINE_VERIFICATION_REQUIRED——旧持久事实不忽略。"""
        expired = datetime.now(UTC) - timedelta(seconds=1)
        saved = {"id": "inv-old", "state": "running", "effect": None, "result_json": {},
                 "deadline_at": expired, "credit_cost": None}
        owner = SimpleNamespace(saved_phase=AsyncMock(return_value={
            "request": {"arguments": {"target": "recommend"}, "tool_name": "boss_goto",
                        "device_id": "dev-1", "deadline_at": expired.isoformat()},
            "invocation": saved,
        }))
        patches, mocks = _repo_mocks(lambda *_: saved)
        with patches:
            with pytest.raises(LocalContinuationRequired) as raised:
                await poll_operation(BossGotoTool(), SimpleNamespace(
                    owner=owner, branch="main", ordinal=0), {"target": "recommend"},
                    tenant_id=TENANT, user_id=USER, session_id="sess-ind", progress_queue=None)
        assert raised.value.code == "LOCAL_DEADLINE_VERIFICATION_REQUIRED"
        mocks["request_cancel"].assert_not_called()
        mocks["expire_stale_claims"].assert_not_called()

    def _can_restore_setup(self, invocation_row):
        from src.core.agent_engine.contracts import Identity
        from src.tools.executor import normalize_provided_parameters

        arguments = {"target": "recommend"}
        normalized = normalize_provided_parameters(BossGotoTool(), arguments)
        phase = LocalPhase("exec-ind", CALL_ID, "main", 0)
        ref = {"tool_call_id": CALL_ID, "branch": "main", "ordinal": 0,
               "invocation_id": "inv-ind",
               "request": {"tool_name": "boss_goto", "device_id": "dev-1",
                           "arguments": normalized, "deadline_at":
                               invocation_row["deadline_at"].isoformat()
                               if invocation_row.get("deadline_at") else None}}
        state = SimpleNamespace(execution_id="exec-ind",
                                identity=Identity(tenant_id=TENANT, user_id=USER,
                                                  session_id="sess-ind"),
                                resources={"local_invocations": {phase.key(RUNNER): ref}})
        row = {"runner_id": RUNNER, "tenant_id": TENANT}
        fact = SimpleNamespace(call=SimpleNamespace(
            id=CALL_ID, name="boss_goto", arguments=arguments))
        return LocalRecoveryProof(), row, state, fact

    def test_can_restore_expired_deadline_still_rejects_with_verification(self):
        expired = datetime.now(UTC) - timedelta(seconds=1)
        proof, row, state, fact = self._can_restore_setup(
            {"deadline_at": expired, "state": "running"})
        with patch("src.local_tools.repository.get_invocation",
                   MagicMock(return_value={"id": "inv-ind", "state": "running",
                                           "effect": None, "deadline_at": expired})), \
             patch("src.local_tools.repository.observed_invocation",
                   MagicMock(side_effect=lambda inv: inv)), \
             patch.object(RunnerLocalLifecycle, "validate_invocation",
                          staticmethod(lambda *a, **k: None)), \
             patch("src.services.agent_runner.domain_recovery.validate_domain_phases",
                   MagicMock(return_value=True)):
            with pytest.raises(Exception) as raised:
                proof.can_restore(row, state, fact)
        assert getattr(raised.value, "args", [None])[0] == "LOCAL_DEADLINE_VERIFICATION_REQUIRED"

    def test_can_restore_without_deadline_no_longer_blocked_by_tool_timeout(self):
        """新绑定（无 deadline）非终态：恢复不再被工具 timeout 阻断（can_restore=True）。"""
        proof, row, state, fact = self._can_restore_setup({"deadline_at": None})
        with patch("src.local_tools.repository.get_invocation",
                   MagicMock(return_value={"id": "inv-ind", "state": "running",
                                           "effect": None, "deadline_at": None})), \
             patch("src.local_tools.repository.observed_invocation",
                   MagicMock(side_effect=lambda inv: inv)), \
             patch.object(RunnerLocalLifecycle, "validate_invocation",
                          staticmethod(lambda *a, **k: None)), \
             patch("src.services.agent_runner.domain_recovery.validate_domain_phases",
                   MagicMock(return_value=True)):
            assert proof.can_restore(row, state, fact) is True


# ====================================================================
# 验收 4：租约、身份校验、显式取消、效果未知保护未削弱
# ====================================================================

class TestProtectionsPreserved:
    @pytest.fixture(autouse=True)
    def _fast_poll(self, monkeypatch):
        monkeypatch.setattr("src.local_tools.proxy_tool.POLL_INTERVAL_SECONDS", 0.01)

    def test_identity_check_deadline_mismatch_rejected(self):
        """validate_invocation：checkpoint request 与 DB 行的 deadline_at 必须相等，
        不一致仍拒绝（身份/一致性校验未削弱）。"""
        from src.core.agent_engine.contracts import CheckpointFailure, Identity

        identity = Identity(tenant_id=TENANT, user_id=USER, session_id="sess-ind")
        phase = LocalPhase("exec-ind", CALL_ID, "main", 0)
        request = {"device_id": "dev-1", "tool_name": "boss_goto",
                   "arguments": {"target": "recommend"}, "provider_key": None,
                   "authorization_epoch": None, "execution_lane": None}
        fact = {"runner_id": RUNNER, "execution_id": "exec-ind", "tool_call_id": CALL_ID,
                "branch": "main", "ordinal": 0, "request": request, "invocation_id": "inv-ind"}
        reference = dict(runner_id=RUNNER, execution_id="exec-ind", tool_call_id=CALL_ID,
                         branch="main", ordinal=0)
        base_invocation = {"id": "inv-ind", "tenant_id": TENANT, "user_id": USER,
                           "session_id": "sess-ind", "device_id": "dev-1",
                           "tool_name": "boss_goto", "arguments_json": {"target": "recommend"},
                           "provider_key": None, "business_kind": "agent_runner",
                           "business_ref": reference, "dedupe_key": phase.key(RUNNER),
                           "authorization_epoch": None, "execution_lane": None,
                           "deadline_at": None}

        future = datetime.now(UTC) + timedelta(seconds=60)
        # request 声明 deadline 而行上无（或反之）→ 拒绝
        mismatched = {**base_invocation}
        fact_mismatch = {**fact, "request": {**request, "deadline_at": future.isoformat()}}
        with pytest.raises(CheckpointFailure) as raised:
            RunnerLocalLifecycle.validate_invocation(identity, RUNNER, phase, fact_mismatch, mismatched)
        assert raised.value.args[0] == "LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED"
        # 相等（None==None 与 future==future）→ 通过
        RunnerLocalLifecycle.validate_invocation(identity, RUNNER, phase, fact, base_invocation)
        matched = {**base_invocation, "deadline_at": future}
        fact_matched = {**fact, "request": {**request, "deadline_at": future.isoformat()}}
        RunnerLocalLifecycle.validate_invocation(identity, RUNNER, phase, fact_matched, matched)

    def _capture_conn(self, fetchone=None, rowcount=0):
        executed = []

        def execute(sql, *args):
            executed.append(sql)

        cursor = SimpleNamespace(execute=execute, fetchall=lambda: [],
                                 fetchone=lambda: fetchone, rowcount=rowcount)
        conn = SimpleNamespace(cursor=lambda *a, **k: cursor, commit=lambda: None)

        @contextlib.contextmanager
        def fake_get_db_connection():
            yield conn

        return executed, fake_get_db_connection

    def test_expire_stale_claims_sweeps_cancel_requested_with_lease_guard(self):
        """租约清扫 SQL：state 含 cancel_requested，且仅清 lease_expires_at 已过期行。"""
        from src.local_tools import repository

        executed, fake = self._capture_conn(rowcount=3)
        with patch("src.local_tools.repository.get_db_connection", fake):
            assert repository.expire_stale_claims() == 3
        sql = executed[0]
        assert "'cancel_requested'" in sql
        assert "lease_expires_at IS NOT NULL" in sql and "lease_expires_at < NOW()" in sql
        assert "'unknown'" in sql and "effect = 'unknown'" in sql

    def test_claim_lease_expired_guards_state_lease_and_tenant(self):
        from src.local_tools import repository

        executed, fake = self._capture_conn(fetchone={"stale": True})
        with patch("src.local_tools.repository.get_db_connection", fake):
            assert repository.claim_lease_expired("inv-ind", TENANT) is True
        sql = executed[0]
        for state in ("'claimed'", "'running'", "'cancel_requested'"):
            assert state in sql
        assert "lease_expires_at IS NOT NULL" in sql and "lease_expires_at < NOW()" in sql
        assert "tenant_id = %s" in sql

        executed2, fake2 = self._capture_conn(fetchone={"stale": False})
        with patch("src.local_tools.repository.get_db_connection", fake2):
            assert repository.claim_lease_expired("inv-ind", TENANT) is False
        executed3, fake3 = self._capture_conn(fetchone=None)
        with patch("src.local_tools.repository.get_db_connection", fake3):
            assert repository.claim_lease_expired("inv-ind", TENANT) is False

    async def test_stale_lease_waits_for_swept_fact_not_early_verdict(self):
        """租约停续：先经权威清扫器，读到 unknown 事实才判 EXECUTION_UNKNOWN；
        清扫后行仍 running 时不提前下结论、也不取消。"""
        tool = BossGotoTool()
        rows = iter([_running(), _running(), _unknown()])
        patches, mocks = _repo_mocks(lambda *_: next(rows), lease_stale=True)
        with patches:
            result = await tool._poll_invocation(
                {"id": "inv-ind", "deadline_at": None}, TENANT, None, durable=True)
        assert result["success"] is False and result["code"] == "EXECUTION_UNKNOWN"
        assert mocks["expire_stale_claims"].call_count >= 1
        mocks["request_cancel"].assert_not_called()

    async def test_non_durable_timeout_window_regression(self):
        """非 durable 旧路径：timeout_seconds 观察窗到期仍 request_cancel + TIMEOUT。"""
        tool = BossGotoTool()
        tool.timeout_seconds = 0
        patches, mocks = _repo_mocks(lambda *_: _running())
        with patches:
            result = await tool._poll_invocation(
                {"id": "inv-ind", "deadline_at": None}, TENANT, None, durable=False)
        assert result["success"] is False and result["code"] == "TIMEOUT"
        mocks["request_cancel"].assert_called_once_with("inv-ind", TENANT)

    def test_cancel_completion_status_keeps_effect_unknown_and_ack_semantics(self):
        def fact(state, effect=None, deadline=...):
            return {"kind": "local", "holdable": True, "completion_known": False,
                    "id": "inv-ind", "state": state, "effect": effect,
                    "deadline_at": None if deadline is ... else deadline}

        # 效果未知保护：不因 deadline 拆除而放松
        ready, issue = cancel_completion_status([fact("unknown", effect="unknown")])
        assert ready is False and issue == "LOCAL_CANCEL_EFFECT_VERIFICATION_REQUIRED"
        # 显式取消、无 deadline 的未终态行：等待回执（ACK_PENDING），不再判 DEADLINE
        ready, issue = cancel_completion_status([fact("cancel_requested")])
        assert ready is False and issue == "LOCAL_CANCEL_ACK_PENDING"
        # 存量投影：过期 deadline 仍进取消核验家族
        expired = datetime.now(UTC) - timedelta(seconds=1)
        ready, issue = cancel_completion_status([fact("cancel_requested", deadline=expired)])
        assert ready is False and issue == "LOCAL_CANCEL_DEADLINE_VERIFICATION_REQUIRED"
        # 终态 + 已知效果 → 完全就绪
        ready, issue = cancel_completion_status([fact("cancelled", effect="none")])
        assert ready is True and issue is None

    def test_timeout_seconds_semantics_unchanged_for_legacy_path(self):
        """timeout=180 只保留为非 durable 旧路径观察窗；durable 工具子类未放大数字。"""
        assert LocalToolProxyTool.timeout_seconds == 180
        assert BossGotoTool.timeout_seconds == 180
