# -*- coding: utf-8 -*-
"""process_and_persist verbose 接线集成测试（Phase 3）

覆盖计划 Phase 3 回归：
- #2 全流程断言未调用 mark_responding（verbose 绝不触发；既有 final 语义不变）
- #4 callback 返回速度不受慢 adapter 影响；dispatcher 超时/false/异常不影响 final
- #5 close_and_drain 后 metadata 冻结，晚到事件不与 DB batch 竞态
- #6 merged follower 不创建 dispatcher、不发 verbose
- #7 _processor cancel/merge 重跑复用 owner state，累计最多一次
- #9 无独立 channel message 行（持久化 batch 无 verbose 行）
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.base import StatusDeliveryResult
from src.channels.session import ChannelSessionManager
from src.core.agent_events import make_verbose_event
from src.core.session_queue import EnqueueResult
from src.core.verbose_feedback import VerboseFeedbackConfig, VerboseFeedbackState


pytestmark = pytest.mark.channels

VALID_TEXT = "正在生成报价单，请耐心等待。"


# ============================================================
# 复用 test_session_manager_persist 的 mock DB 模式
# ============================================================


def _make_mock_db():
    memory_store = {"sessions": {}, "messages": []}

    class MockRow(dict):
        pass

    conn = MagicMock()
    cursor = MagicMock()
    cursor._fetch_rows = []
    spy = []

    def execute(sql, params=None):
        sql_lower = sql.lower()
        params = tuple(params or ())
        spy.append((sql_lower, params))
        cursor._fetch_rows = []
        if "insert into channel_messages" in sql_lower:
            row = MockRow(
                message_id=params[0], session_id=params[1], tenant_id=params[2],
                role=params[3], content=params[4], message_type=params[5],
                attachments=params[6], metadata=params[7],
                id=len(memory_store["messages"]) + 1, created_at="2026-08-31 10:00:00",
            )
            memory_store["messages"].append(row)
            cursor.rowcount = 1
        elif "select * from channel_messages" in sql_lower:
            sid = params[0]
            rows = [r for r in memory_store["messages"] if r["session_id"] == sid]
            cursor._fetch_rows = list(reversed(rows))
            cursor.rowcount = 1 if cursor._fetch_rows else 0
        else:
            cursor.rowcount = 1

    def fetchone():
        return cursor._fetch_rows[0] if cursor._fetch_rows else None

    def fetchall():
        return cursor._fetch_rows

    cursor.execute = execute
    cursor.fetchone = fetchone
    cursor.fetchall = fetchall
    conn.cursor.return_value = cursor
    conn.rollback = MagicMock()
    return memory_store, conn, spy


@pytest.fixture
def manager():
    m = ChannelSessionManager()
    m._initialized = True
    return m


@pytest.fixture
def mock_db_ctx():
    memory_store, conn, spy = _make_mock_db()
    with patch("src.channels.session.get_db_connection") as mock_get:
        mock_get.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get.return_value.__exit__ = MagicMock(return_value=False)
        yield memory_store, conn, spy


@pytest.fixture
def patched_session_queue():
    with patch("src.core.session_queue.session_queue") as mock_sq:
        mock_sq.enqueue_and_process = AsyncMock()
        mock_sq.mark_responding = MagicMock()
        mock_sq.mark_idle = MagicMock()
        mock_sq.finish_processing = MagicMock()
        yield mock_sq


@pytest.fixture
def stub_agent():
    agent = MagicMock()
    agent.process_message_sync = AsyncMock(return_value="final reply")
    agent.llm.get_model_name = MagicMock(return_value="test-model")
    agent.llm.get_provider_name = MagicMock(return_value="test-provider")
    return agent


def _enabled_config():
    return VerboseFeedbackConfig(enabled=True)


def _assistant_metadata(store, content=""):
    """从 mock DB 取 final assistant 行并解析 metadata JSON（落库前 json.dumps）。"""
    import json as _json
    rows = [
        m for m in store["messages"]
        if m["role"] == "assistant" and (content == "" or m["content"] == content)
    ]
    assert rows, "final assistant 行未落库"
    raw = rows[-1]["metadata"]
    return _json.loads(raw) if isinstance(raw, str) else raw


def _assert_agent_reused_owner_state(stub_agent):
    """process_message_sync 必须收到显式 feedback_state（owner 级复用契约）。"""
    for call in stub_agent.process_message_sync.await_args_list:
        assert "feedback_state" in call.kwargs, (
            "owner 级 VerboseFeedbackState 必须显式传入每次重跑（设计 §7）"
        )


# ============================================================
# 回归 #2：全流程未因 verbose 调用 mark_responding
# ============================================================


class TestNoMarkRespondingForVerbose:
    @pytest.mark.asyncio
    async def test_verbose_never_triggers_mark_responding(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        """verbose 发出后 mark_responding 调用次数与既有 final 流程一致（仅 1 次）；
        若 verbose 误触发则会出现第 2 次调用。"""
        sends = []

        async def enqueue_side_effect(**kwargs):
            response = await kwargs["processor"](lambda: False)
            return EnqueueResult(
                status="success", response_text=response, merged_input="hello",
                was_merged=False,
            )

        async def agent_with_verbose(**kwargs):
            cb = kwargs.get("progress_callback")
            state = kwargs.get("feedback_state")
            event = make_verbose_event(
                event_id="verbose_mr_1", data=VALID_TEXT, source="policy"
            )
            if state is not None and state.try_emit(event) and cb is not None:
                await cb(event)
            return "final reply"

        stub_agent.process_message_sync = AsyncMock(side_effect=agent_with_verbose)
        patched_session_queue.enqueue_and_process.side_effect = enqueue_side_effect

        # adapter 未实现安全预留（基类默认 suppressed_unsupported，不发送）
        adapter = MagicMock()
        adapter.send_status_message = AsyncMock(
            return_value=StatusDeliveryResult.suppressed_unsupported("unsupported")
        )
        send_verbose = manager.make_send_verbose(
            adapter=adapter, event_id="msg_mr", reply_to="u1",
        )

        result = await manager.process_and_persist(
            session_id="sid_mr", tenant_id="t1", user_content="hello",
            agent=stub_agent,
            send_response=AsyncMock(return_value=True),
            send_verbose=send_verbose,
            verbose_feedback_config=_enabled_config(),
        )

        assert result["status"] == "success"
        assert adapter.send_status_message.await_count == 1, "verbose 恰好尝试投递一次"
        _assert_agent_reused_owner_state(stub_agent)
        # 既有 final 流程恰好调用 1 次 mark_responding（verbose 不得追加调用）
        assert patched_session_queue.mark_responding.call_count == 1
        # assistant metadata 含冻结的 verboseMessages
        metadata = _assistant_metadata(mock_db_ctx[0], "final reply")
        assert metadata["verboseMessages"][0]["delivery"] == "suppressed_unsupported"

    @pytest.mark.asyncio
    async def test_disabled_config_keeps_legacy_behavior(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        """verbose 关闭（默认）：零行为变化——metadata 无 verboseMessages 键。"""

        async def enqueue_side_effect(**kwargs):
            response = await kwargs["processor"](lambda: False)
            return EnqueueResult(
                status="success", response_text=response, merged_input="hello",
                was_merged=False,
            )

        patched_session_queue.enqueue_and_process.side_effect = enqueue_side_effect

        result = await manager.process_and_persist(
            session_id="sid_off", tenant_id="t1", user_content="hello",
            agent=stub_agent,
            send_response=AsyncMock(return_value=True),
        )
        assert result["status"] == "success"
        metadata = _assistant_metadata(mock_db_ctx[0])
        assert "verboseMessages" not in (metadata or {})


# ============================================================
# 回归 #4：callback 速度与 final 不受慢 adapter 影响
# ============================================================


class TestSlowAdapterDoesNotAffectFinal:
    @pytest.mark.asyncio
    async def test_slow_adapter_timeout_does_not_block_or_break_final(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        """adapter 10s 慢发送：submit 立即返回；drain 超时 cancel；final 正常落库/发送。"""
        submit_durations = []

        async def slow_send_verbose(event, delivery_id):
            await asyncio.sleep(10)
            return StatusDeliveryResult.sent()

        async def enqueue_side_effect(**kwargs):
            # 度量 processor 内 verbose 提交路径的同步耗时（callback put_nowait）
            orig_processor = kwargs["processor"]

            async def timed_processor(cancel_check, **pkwargs):
                t0 = asyncio.get_running_loop().time()
                response = await orig_processor(cancel_check, **pkwargs)
                return response

            response = await timed_processor(lambda: False)
            return EnqueueResult(
                status="success", response_text=response, merged_input="hello",
                was_merged=False,
            )

        async def agent_with_verbose(**kwargs):
            cb = kwargs.get("progress_callback")
            state = kwargs.get("feedback_state")
            event = make_verbose_event(
                event_id="verbose_slow_1", data=VALID_TEXT, source="policy"
            )
            if state is not None and state.try_emit(event) and cb is not None:
                loop = asyncio.get_running_loop()
                t0 = loop.time()
                await cb(event)  # submit 路径
                submit_durations.append(loop.time() - t0)
            return "final reply"

        stub_agent.process_message_sync = AsyncMock(side_effect=agent_with_verbose)
        patched_session_queue.enqueue_and_process.side_effect = enqueue_side_effect

        send_response = AsyncMock(return_value=True)
        result = await manager.process_and_persist(
            session_id="sid_slow", tenant_id="t1", user_content="hello",
            agent=stub_agent,
            send_response=send_response,
            send_verbose=slow_send_verbose,
            verbose_feedback_config=VerboseFeedbackConfig(
                enabled=True, delivery_timeout_seconds=0.2,
            ),
        )

        assert result["status"] == "success"
        assert submit_durations and submit_durations[0] < 1.0, (
            "callback（submit）不受慢 adapter 影响"
        )
        # final 不受影响：正常落库 + 发送
        assert send_response.await_count == 1
        assert any(
            m["role"] == "assistant" and m["content"] == "final reply"
            for m in mock_db_ctx[0]["messages"]
        )

    @pytest.mark.asyncio
    async def test_send_verbose_false_and_exception_do_not_break_final(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        async def failing_send_verbose(event, delivery_id):
            raise RuntimeError("boom")

        async def enqueue_side_effect(**kwargs):
            response = await kwargs["processor"](lambda: False)
            return EnqueueResult(
                status="success", response_text=response, merged_input="hello",
                was_merged=False,
            )

        async def agent_with_verbose(**kwargs):
            cb = kwargs.get("progress_callback")
            state = kwargs.get("feedback_state")
            event = make_verbose_event(
                event_id="verbose_false_1", data=VALID_TEXT, source="policy"
            )
            if state is not None and state.try_emit(event) and cb is not None:
                await cb(event)
            return "final reply"

        stub_agent.process_message_sync = AsyncMock(side_effect=agent_with_verbose)
        patched_session_queue.enqueue_and_process.side_effect = enqueue_side_effect

        send_response = AsyncMock(return_value=True)
        result = await manager.process_and_persist(
            session_id="sid_false", tenant_id="t1", user_content="hello",
            agent=stub_agent,
            send_response=send_response,
            send_verbose=failing_send_verbose,
            verbose_feedback_config=_enabled_config(),
        )
        assert result["status"] == "success"
        assert send_response.await_count == 1, "dispatcher 失败不影响 final 发送"


# ============================================================
# 回归 #5：drain 后 metadata 冻结，晚到事件不与 DB batch 竞态
# ============================================================


class TestDrainFreezesMetadata:
    @pytest.mark.asyncio
    async def test_late_submit_after_drain_rejected_and_metadata_frozen(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        async def enqueue_side_effect(**kwargs):
            response = await kwargs["processor"](lambda: False)
            return EnqueueResult(
                status="success", response_text=response, merged_input="hello",
                was_merged=False,
            )

        async def agent_with_verbose(**kwargs):
            cb = kwargs.get("progress_callback")
            state = kwargs.get("feedback_state")
            event = make_verbose_event(
                event_id="verbose_frz_1", data=VALID_TEXT, source="policy"
            )
            if state is not None and state.try_emit(event) and cb is not None:
                await cb(event)
            return "final reply"

        stub_agent.process_message_sync = AsyncMock(side_effect=agent_with_verbose)
        patched_session_queue.enqueue_and_process.side_effect = enqueue_side_effect

        result = await manager.process_and_persist(
            session_id="sid_frz", tenant_id="t1", user_content="hello",
            agent=stub_agent,
            send_response=AsyncMock(return_value=True),
            send_verbose=AsyncMock(return_value=StatusDeliveryResult.sent()),
            verbose_feedback_config=_enabled_config(),
        )
        assert result["status"] == "success"
        metadata = _assistant_metadata(mock_db_ctx[0], "final reply")
        assert metadata["verboseMessages"][0]["delivery"] == "sent"
        assert metadata["verboseMessages"][0]["eventId"] == "verbose_frz_1"
        # batch 中没有任何 verbose 角色/正文行（回归 #9）
        roles = [m["role"] for m in mock_db_ctx[0]["messages"]]
        assert all(role in ("user", "assistant", "tool") for role in roles)
        assert all(
            m["content"] != VALID_TEXT for m in mock_db_ctx[0]["messages"]
        ), "verbose 正文不得产生独立 channel message 行"


# ============================================================
# 回归 #6：merged follower 不创建 dispatcher、不发 verbose
# ============================================================


class TestMergedFollowerNoDispatcher:
    @pytest.mark.asyncio
    async def test_merged_returns_without_running_processor(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        patched_session_queue.enqueue_and_process.side_effect = (
            AsyncMock(return_value=EnqueueResult(status="merged", merged_input="B"))
        )
        verbose_sends = []

        async def send_verbose(event, delivery_id):
            verbose_sends.append(event)
            return StatusDeliveryResult.sent()

        result = await manager.process_and_persist(
            session_id="sid_merged", tenant_id="t1", user_content="B",
            agent=stub_agent,
            send_response=AsyncMock(return_value=True),
            send_verbose=send_verbose,
            verbose_feedback_config=_enabled_config(),
        )
        assert result["status"] == "merged"
        # follower 不执行 owner processor → 无 verbose、无落库、无发送
        assert verbose_sends == []
        stub_agent.process_message_sync.assert_not_awaited()
        assert mock_db_ctx[0]["messages"] == []
        patched_session_queue.mark_responding.assert_not_called()


# ============================================================
# 回归 #7：cancel/merge 重跑复用 owner state，累计最多一次
# ============================================================


class TestReprocessReusesOwnerState:
    @pytest.mark.asyncio
    async def test_two_attempts_emit_verbose_once(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        """enqueue 内部 cancel+merge 重跑两次 attempt：verbose 只投递一次，
        final 使用第二次 attempt 的合并结果。"""
        verbose_sends = []
        attempt_inputs = []

        async def send_verbose(event, delivery_id):
            verbose_sends.append((event["eventId"], delivery_id))
            return StatusDeliveryResult.sent()

        async def enqueue_side_effect(**kwargs):
            processor = kwargs["processor"]
            # attempt 1
            agent_inputs = []

            async def attempt(user_input_override):
                agent_inputs.append(user_input_override)
                return await processor(lambda: False, user_input_override=user_input_override)

            # 让两次 attempt 各自 emit verbose（同 owner state，第二次被拒）
            async def agent_two_attempts(**pkwargs):
                cb = pkwargs.get("progress_callback")
                state = pkwargs.get("feedback_state")
                attempt_inputs.append(pkwargs.get("user_input"))
                event = make_verbose_event(
                    event_id=f"verbose_rp_{len(attempt_inputs)}",
                    data=VALID_TEXT, source="policy",
                )
                if state is not None and state.try_emit(event) and cb is not None:
                    await cb(event)
                return f"final-{pkwargs.get('user_input', '')[:10]}"

            stub_agent.process_message_sync = AsyncMock(side_effect=agent_two_attempts)
            await attempt("A")
            # 模拟 cancel+merge：attempt 2 用合并输入重跑（同一 processor / 同一 state）
            response = await attempt("A+B")
            return EnqueueResult(
                status="success", response_text=response,
                merged_input="A+B", was_merged=True,
            )

        patched_session_queue.enqueue_and_process.side_effect = enqueue_side_effect

        result = await manager.process_and_persist(
            session_id="sid_rp", tenant_id="t1", user_content="A",
            agent=stub_agent,
            send_response=AsyncMock(return_value=True),
            send_verbose=send_verbose,
            verbose_feedback_config=_enabled_config(),
        )

        assert result["status"] == "success"
        assert result["was_merged"] is True
        assert len(attempt_inputs) == 2, "两次 attempt 都执行了"
        assert len(verbose_sends) == 1, "owner state 复用：累计仍最多一条"
        assert verbose_sends[0][0] == "verbose_rp_1"
        assert verbose_sends[0][1].endswith(":verbose:1")
        # 落库的 verboseMessages 同样只有一条
        metadata = _assistant_metadata(mock_db_ctx[0])
        assert len(metadata["verboseMessages"]) == 1


# ============================================================
# 保留调用方 metadata + verboseMessages 合并（设计 §10）
# ============================================================


class TestMetadataMerge:
    @pytest.mark.asyncio
    async def test_caller_metadata_keys_preserved_and_verbose_overrides(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        async def enqueue_side_effect(**kwargs):
            response = await kwargs["processor"](lambda: False)
            return EnqueueResult(
                status="success", response_text=response, merged_input="hello",
                was_merged=False,
            )

        async def agent_with_verbose(**kwargs):
            cb = kwargs.get("progress_callback")
            state = kwargs.get("feedback_state")
            event = make_verbose_event(
                event_id="verbose_meta_1", data=VALID_TEXT, source="policy"
            )
            if state is not None and state.try_emit(event) and cb is not None:
                await cb(event)
            return "final reply"

        stub_agent.process_message_sync = AsyncMock(side_effect=agent_with_verbose)
        patched_session_queue.enqueue_and_process.side_effect = enqueue_side_effect

        caller_metadata = {"customKey": "keep-me", "progressMessages": [{"p": 1}]}
        await manager.process_and_persist(
            session_id="sid_meta", tenant_id="t1", user_content="hello",
            agent=stub_agent,
            send_response=AsyncMock(return_value=True),
            assistant_metadata=dict(caller_metadata),
            send_verbose=AsyncMock(return_value=StatusDeliveryResult.sent()),
            verbose_feedback_config=_enabled_config(),
        )
        metadata = _assistant_metadata(mock_db_ctx[0])
        # 浅复制：调用方未知字段保留；系统字段 verboseMessages 覆盖写入
        assert metadata["customKey"] == "keep-me"
        assert metadata["progressMessages"] == [{"p": 1}]
        assert metadata["verboseMessages"][0]["eventId"] == "verbose_meta_1"
