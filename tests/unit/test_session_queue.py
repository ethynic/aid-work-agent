"""
SessionMessageQueue.enqueue_and_process 状态机单元测试

背景（P0 改造，详见 docs/incidents/wecom-kf-context-loss-research.md §7、§8）：
本调度器是 L2 合并层，决定「同一 session 第二条消息到达时，合并、排队还是独立处理」。
本次 P0 改造把返回值由 str 改为 EnqueueResult，暴露 status / was_merged / merged_input，
让 channel_session_manager.process_and_persist 能据此决定 channel_messages 的正确写入方式。

本测试覆盖三种核心路径：
- 空闲态首条消息：acquire_lock 成功 → 跑 processor → 返回 success（was_merged 取决于窗口内是否合并）
- 处理中态 + 允许取消：set_cancel + append_merge → 旧请求重跑合并输入，新请求返回 merged
- 处理中态 + 已 mark_responding：set_pending → 旧请求完成后处理 pending，新请求返回 merged
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.session_queue import SessionMessageQueue, EnqueueResult
from src.core.redis_client import RedisClient


pytestmark = pytest.mark.agent


@pytest.fixture
def q():
    """新鲜的 SessionMessageQueue 实例（不依赖 Redis 单例）"""
    return SessionMessageQueue()


@pytest.fixture
def fake_redis():
    """patch redis_client，提供内存级 fake redis，可断言 set/get/delete/exists 调用。

    存储语义尽量贴近真实 Redis：
    - acquire_lock(key, value, ex) → SET NX；返回 True/False
    - exists(key) → True/False
    - get(key) / set(key, value, ex) / delete(key)
    """
    store = {}

    fr = MagicMock()
    fr._store = store
    fr.make_key = MagicMock(side_effect=lambda prefix, session_id: f"{prefix}:{session_id}")

    def acquire_lock(key, value, ex=None):
        if key in store:
            return False
        store[key] = value
        return True

    def release_lock(key, value):
        if store.get(key) == value:
            store.pop(key, None)
            return True
        return False

    def exists(key):
        return key in store

    def get(key):
        return store.get(key)

    def set_(key, value, ex=None):
        store[key] = value

    def delete(key):
        store.pop(key, None)

    fr.acquire_lock = MagicMock(side_effect=acquire_lock)
    fr.release_lock = MagicMock(side_effect=release_lock)
    fr.exists = MagicMock(side_effect=exists)
    fr.get = MagicMock(side_effect=get)
    fr.set = MagicMock(side_effect=set_)
    fr.delete = MagicMock(side_effect=delete)

    def session_finalize_if_quiet(
        lock_key, lock_value, cancel_key, merge_key, pending_key,
        finalizing_key, state_guard_key, expected_input, ttl,
    ):
        if store.get(lock_key) != lock_value or state_guard_key in store:
            return False
        if pending_key in store:
            return False
        raw = store.get(merge_key) or {}
        data = json.loads(raw) if isinstance(raw, str) else raw
        if cancel_key in store and data.get("text", "") != expected_input:
            return False
        store[finalizing_key] = "1"
        return True

    fr.session_finalize_if_quiet = MagicMock(side_effect=session_finalize_if_quiet)

    def session_release_finalized(
        lock_key, lock_value, cancel_key, merge_key, responding_key, finalizing_key
    ):
        if store.get(lock_key) != lock_value:
            return False
        for key in (
            cancel_key, merge_key, responding_key, finalizing_key, lock_key
        ):
            store.pop(key, None)
        return True

    fr.session_release_finalized = MagicMock(side_effect=session_release_finalized)

    with patch("src.core.session_queue.redis_client", fr):
        yield fr


# ============================================================
# 路径 1：空闲态首条消息
# ============================================================


class TestCrossWorkerStateBoundary:
    """两个 queue 实例模拟不同 Gunicorn/archive worker。"""

    def test_redis_backed_state_is_shared_but_cancel_callbacks_are_process_local(
        self, fake_redis
    ):
        """锁、cancel、merge、pending、responding 跨实例共享；回调注册表不共享。"""
        worker_a = SessionMessageQueue()
        worker_b = SessionMessageQueue()
        sid = "sid_cross_worker"

        lock_value = worker_a.acquire_lock(sid)
        assert lock_value is not None
        assert worker_b.is_locked(sid) is True

        worker_a.set_cancel(sid)
        worker_a.set_merge(sid, "第一条", [{"type": "image"}], msgid="evt_1")
        worker_a.set_pending(sid, "第三条")
        worker_a.mark_responding(sid)

        assert worker_b.is_cancelled(sid) is True
        assert worker_b.get_merged_input(sid, "") == "第一条"
        assert worker_b.get_merged_attachments_meta(sid) == [{"type": "image"}]
        assert worker_b.has_pending(sid) is True
        assert worker_b.is_responding(sid) is True

        worker_a.register_cancel_check(sid, lambda: True)
        worker_a._clear_cancel(sid)
        assert worker_a.check_cancel(sid) is True
        assert worker_b.check_cancel(sid) is False

    def test_pending_preserves_attachment_and_msgid_and_reads_legacy_format(
        self, q, fake_redis
    ):
        q.set_pending("sid_pending_meta", "附件", [{"type": "file"}], msgid="evt_2")
        assert q.get_pending_payload("sid_pending_meta") == {
            "text": "附件",
            "attachments_meta": [{"type": "file"}],
            "msgid": "evt_2",
            "segments": [{"msgid": "evt_2", "text": "附件"}],
        }

        legacy_key = q._key("session_pending", "sid_pending_legacy")
        fake_redis.set(legacy_key, '{"text":"旧消息","timestamp":1}', ex=30)
        assert q.get_pending_payload("sid_pending_legacy") == {
            "text": "旧消息",
            "attachments_meta": None,
            "msgid": "",
            "segments": None,
        }

    def test_pending_accumulates_text_metadata_and_both_attachment_channels(
        self, q
    ):
        q.set_pending(
            "sid_pending_many", "B", [{"event_id": "evt_2"}], msgid="evt_2",
            agent_attachments=[{"name": "b.pdf"}],
        )
        q.set_pending(
            "sid_pending_many", "C", [{"event_id": "evt_3"}], msgid="evt_3",
            agent_attachments=[{"name": "c.png"}],
        )
        assert q.get_pending_payload("sid_pending_many") == {
            "text": "B\n\n[用户追加消息] C",
            "attachments_meta": [{"event_id": "evt_2"}, {"event_id": "evt_3"}],
            "msgid": "evt_3",
            "segments": [
                {"msgid": "evt_2", "text": "B"},
                {"msgid": "evt_3", "text": "C"},
            ],
            "agent_attachments": [{"name": "b.pdf"}, {"name": "c.png"}],
        }


class TestIdleFirstMessage:
    @pytest.mark.asyncio
    async def test_real_redis_client_fallback_holds_and_releases_lease(self):
        client = RedisClient()
        client._connected = False
        client._client = None
        queue = SessionMessageQueue()
        queue.MERGE_WINDOW = 0

        async def processor(_cancel, user_input_override=None):
            return user_input_override

        with patch("src.core.session_queue.redis_client", client):
            result = await queue.enqueue_and_process(
                session_id="sid_real_fallback", user_input="A", processor=processor
            )
            assert result.lease_token
            assert queue.is_locked("sid_real_fallback")
            assert queue.is_finalizing("sid_real_fallback")
            queue.finish_processing("sid_real_fallback", result.lease_token)
            assert not queue.is_locked("sid_real_fallback")
            assert not queue.is_finalizing("sid_real_fallback")

    @pytest.mark.asyncio
    async def test_idle_first_message_returns_success_not_merged(self, q, fake_redis):
        """空闲态：首条消息拿到锁 → 跑 processor → 返回 success，was_merged=False。

        为什么重要：独立处理是默认路径，必须返回 success 让调用方继续发送回复和持久化。
        若返回 merged，调用方会跳过写入 channel_messages，导致用户消息完全丢失。
        """
        processor = AsyncMock(return_value="reply")

        result = await q.enqueue_and_process(
            session_id="sid_idle",
            user_input="你好",
            processor=processor,
        )

        assert isinstance(result, EnqueueResult)
        assert result.status == "success"
        assert result.response_text == "reply"
        assert result.was_merged is False, "首条消息无合并，was_merged 应为 False"
        assert result.merged_input == "你好"
        processor.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_merge_window_picks_up_append_before_processing(
        self, q, fake_redis
    ):
        """合并窗口：首条消息在 _wait_merge_window 期间，外部往 merge buffer 写入第二条 →
        首条消息处理时读取到的 merged_input 是合并后的 "A\\n\\n[用户追加消息] B"，was_merged=True。

        为什么重要：这是「合并方」路径——LLM 实际看到带 [用户追加消息] 分隔标记的合并输入，
        process_and_persist 必须把这个合并后的输入写入 channel_messages（而非原始 "A"），
        否则存储与 LLM 输入不一致。分隔标记用于让 LLM 识别多条独立消息，避免只处理最后一个意图。
        """
        processor = AsyncMock(return_value="reply-AB")

        # 在 _wait_merge_window 期间注入 append_merge
        async def inject_during_window():
            # 等 enqueue 进入 wait_merge_window（0.2s 循环），再 append
            await asyncio.sleep(0.3)
            q.append_merge("sid_merge", "B")

        inject_task = asyncio.create_task(inject_during_window())

        result = await q.enqueue_and_process(
            session_id="sid_merge",
            user_input="A",
            processor=processor,
        )
        await inject_task

        assert result.status == "success"
        assert result.was_merged is True, (
            "窗口期内有 append，was_merged 必须为 True，否则合并方无法识别自己是合并方"
        )
        assert result.merged_input == "A\n\n[用户追加消息] B", (
            "merged_input 必须是带 [用户追加消息] 分隔标记的合并文本；实际: "
            + repr(result.merged_input)
        )

    @pytest.mark.asyncio
    async def test_merge_window_set_cancel_does_not_blank_processing(
        self, q, fake_redis
    ):
        """合并窗口内第二条消息 set_cancel + append_merge（真实场景）→
        首条消息处理时 cancel 标记必须已清除，processor 正常执行并返回非空回复。

        为什么重要：真实场景下第二条消息会同时 set_cancel（通知首条消息重跑合并输入）
        和 append_merge。首条消息的 _wait_merge_window 检测到取消后 break，
        若不清除取消标记就调 processor，agent 第一轮迭代 cancel_check() 立即返回 True，
        直接 return 空 -> 用户收到空回复（2026-08-25 生产事故根因）。
        回归锁定：窗口期取消后本次处理不能被取消标记打断。
        """
        q.MERGE_WINDOW = 0.5  # 加速：注入窗口缩短
        seen_cancel_check = []

        async def processor(cancel_check, user_input_override=None):
            seen_cancel_check.append(cancel_check() if cancel_check else None)
            return user_input_override or "reply"

        async def inject_during_window():
            await asyncio.sleep(0.15)
            # 模拟真实场景：第二条消息到达 -> set_cancel + append_merge
            q.set_cancel("sid_merge_cancel")
            q.append_merge("sid_merge_cancel", "B")

        inject_task = asyncio.create_task(inject_during_window())

        result = await q.enqueue_and_process(
            session_id="sid_merge_cancel",
            user_input="A",
            processor=processor,
        )
        await inject_task

        assert result.status == "success"
        assert result.response_text != "", (
            "窗口内 set_cancel 后，本次处理不应被取消标记打断而返回空回复"
        )
        assert result.merged_input == "A\n\n[用户追加消息] B"
        assert result.was_merged is True
        assert seen_cancel_check and seen_cancel_check[0] is False, (
            "处理时 cancel_check 必须为 False（取消标记已被清除），"
            "否则 agent 第一轮迭代即退出，返回空响应"
        )


# ============================================================
# 路径 2：处理中态 + 允许取消（尚未 mark_responding） → 被合并方
# ============================================================


class TestMergingSecondMessage:
    @pytest.mark.asyncio
    async def test_follower_injected_at_final_check_is_reprocessed_not_lost(
        self, q, fake_redis
    ):
        calls = []

        async def processor(cancel_check, user_input_override=None):
            calls.append(user_input_override)
            return user_input_override

        original_finalize = q._try_mark_finalizing
        injected = False

        def inject_then_finalize(session_id, lock_value, expected_input):
            nonlocal injected
            if not injected:
                injected = True
                q.set_cancel(session_id)
                q.append_merge(session_id, "B", new_msgid="evt_b")
                return False
            return original_finalize(session_id, lock_value, expected_input)

        with patch.object(q, "_try_mark_finalizing", side_effect=inject_then_finalize):
            result = await q.enqueue_and_process(
                session_id="sid_final_race", user_input="A", processor=processor,
                msgid="evt_a",
            )

        assert result.status == "success"
        assert result.merged_input == "A\n\n[用户追加消息] B"
        assert calls[-1] == result.merged_input
        assert result.merged_from_msgids == ["evt_a", "evt_b"]
        q.finish_processing("sid_final_race", result.lease_token)

    @pytest.mark.asyncio
    async def test_second_message_during_processing_returns_merged(self, q, fake_redis):
        """处理中态：首条 A 持有锁（未 mark_responding），B 到达 → B 立即返回 status="merged"。

        为什么重要：被合并方（B）必须快速返回 merged，调用方据此跳过回复发送和 channel_messages
        写入；否则会产生两条独立 user 和两条独立 assistant 回复（用户体感错乱）。
        """
        processor_A = AsyncMock(return_value="reply-A")

        processor_attachments = []

        async def slow_processor_A(
            cancel_check, user_input_override=None, agent_attachments_override=None
        ):
            # 让 A 长时间持有锁，给 B 到达的机会
            await asyncio.sleep(0.6)
            processor_attachments.append(agent_attachments_override)
            return user_input_override or "reply-A"

        async def run_A():
            result = await q.enqueue_and_process(
                session_id="sid_merge2", user_input="A", processor=slow_processor_A,
                agent_attachments=[{"name": "a.png"}],
            )
            q.finish_processing("sid_merge2", result.lease_token)
            return result

        task_A = asyncio.create_task(run_A())

        # 等 A 拿到锁
        await asyncio.sleep(0.25)
        # 此时 A 还在处理中，mark_responding 未设置 → is_cancel_allowed=True
        assert q.is_locked("sid_merge2"), "A 应持有锁"
        assert q.is_cancel_allowed("sid_merge2"), (
            "未 mark_responding 时应允许取消（这是合并分支的触发条件）"
        )

        # B 到达
        processor_B = AsyncMock(return_value="should-not-run")
        result_B = await q.enqueue_and_process(
            session_id="sid_merge2", user_input="B", processor=processor_B,
            agent_attachments=[{"name": "b.pdf"}],
        )

        # B 的契约
        assert result_B.status == "merged", (
            "被合并方（B）必须返回 status=merged，让调用方跳过回复发送和 channel_messages 写入"
        )
        # B 的 processor 不应被调用（合并语义：A 跑了合并输入，B 不跑）
        processor_B.assert_not_awaited()

        # 等 A 完成（A 会在 finally 中检测 cancel + merge，重跑合并输入）
        result_A = await task_A
        assert result_A.status == "success"
        assert result_A.was_merged is True, (
            "A 在 cancel + merge 后重跑，was_merged 必须为 True，"
            "process_and_persist 才会把 merged_input 写入 channel_messages"
        )
        assert "A" in result_A.merged_input and "B" in result_A.merged_input
        assert processor_attachments[-1] == [
            {"name": "a.png"}, {"name": "b.pdf"}
        ]


# ============================================================
# 路径 3：处理中态 + 已 mark_responding（不允许取消） → 排队分支
# ============================================================


class TestPendingAfterResponding:
    @pytest.mark.asyncio
    async def test_message_after_mark_responding_returns_merged_via_pending(
        self, q, fake_redis
    ):
        """已 mark_responding 时 B 到达 → B 返回 status="merged"，但走 pending 分支（不是 cancel+merge）。

        为什么重要：mark_responding 表示 A 已经开始向渠道推送 SSE（不可中断），
        此时不允许取消 A；B 进入 pending，由 A 完成后统一处理。但 B 的调用方契约
        与合并分支一致——返回 merged，不发送回复（A 完成后会处理 pending 并发送）。
        """
        processor_calls = []
        # 用 event 让 processor_A 在 mark_responding 后发信号，确保 B 到达时 responding 已置位
        responding_ready = asyncio.Event()

        processor_attachment_calls = []

        async def processor_A(
            cancel_check, user_input_override=None, agent_attachments_override=None
        ):
            # A 标记 responding，模拟「已经开始 send_message 推送」
            q.mark_responding("sid_pending")
            responding_ready.set()
            # 在 responding 状态下，B 到达
            await asyncio.sleep(0.5)
            processor_calls.append("A")
            processor_attachment_calls.append(agent_attachments_override)
            return user_input_override or "reply-A"

        async def run_A():
            result = await q.enqueue_and_process(
                session_id="sid_pending", user_input="A", processor=processor_A,
                agent_attachments=[{"name": "owner.png"}],
            )
            q.finish_processing("sid_pending", result.lease_token)
            return result

        task_A = asyncio.create_task(run_A())
        # 等 A 进入 processor 并 mark_responding；A 要先过 _wait_merge_window（2s）才会进 processor
        await asyncio.wait_for(responding_ready.wait(), timeout=5.0)

        # 验证 A 已 mark_responding
        assert q.is_responding("sid_pending"), "A 应已 mark_responding"
        assert not q.is_cancel_allowed("sid_pending"), (
            "mark_responding 后不允许取消，B 必须走 pending 分支"
        )

        # B 到达
        result_B = await q.enqueue_and_process(
            session_id="sid_pending",
            user_input="B",
            processor=AsyncMock(return_value="never"),
            agent_attachments=[{"name": "pending.pdf"}],
        )
        assert result_B.status == "merged", (
            "已 mark_responding 后到达的 B 也返回 merged（走 pending 分支，由 A 完成后处理）"
        )

        # 等 A 完成（A 在 finally 中检测 pending 并处理）
        result_A = await task_A
        # A 完成后会处理 pending（B），所以 A 最终返回 success
        assert result_A.status == "success"
        assert "A" in processor_calls
        assert processor_attachment_calls[-1] == [{"name": "pending.pdf"}]


# ============================================================
# EnqueueResult 数据契约
# ============================================================


class TestEnqueueResultContract:
    def test_default_fields(self):
        """EnqueueResult 默认值必须明确，不能让调用方猜。

        为什么重要：process_and_persist 用 `if result.status == "merged"` 区分路径，
        用 `result.was_merged` 决定写 merged_input 还是原始 user_content。
        默认值必须语义清晰（status 必填，其余可空），否则调用方容易写错分支。
        """
        r = EnqueueResult(status="success")
        assert r.response_text == ""
        assert r.merged_input == ""
        assert r.was_merged is False

    def test_merged_result_carries_merged_input_for_diagnostics(self):
        """被合并方的 EnqueueResult 也带 merged_input（即使不写消息），便于日志诊断。

        为什么重要：被合并方虽然不写消息也不发送回复，但调用方仍可能想记录「实际合并了什么」，
        用于调试合并机制是否按预期工作。merged_input 是关键诊断信息。
        """
        r = EnqueueResult(status="merged", merged_input="A\nB")
        assert r.merged_input == "A\nB"
        assert r.was_merged is False, "被合并方 was_merged 应为 False（它不是合并方）"


# ============================================================
# 路径 4：processor 异常 → 返回 status="error"（P0-5）
# ============================================================


class TestProcessorError:
    @pytest.mark.asyncio
    async def test_processor_exception_returns_error_status(self, q, fake_redis):
        """processor 抛异常 → enqueue_and_process 返回 status="error"。

        为什么重要（P0-5）：processor 异常时若 fall through 返回 status="merged"，
        调用方（process_and_persist）会据此跳过 user 写入 / 不调 mark_error，
        失败完全静默——既不告知用户，也不持久化失败状态。
        返回 status="error" 让调用方走错误路径（mark_error + 返回 error dict）。
        """
        async def failing_processor(cancel_check, user_input_override=None):
            raise RuntimeError("agent internal error")

        result = await q.enqueue_and_process(
            session_id="sid_p0_5",
            user_input="问题",
            processor=failing_processor,
        )

        assert result.status == "error", (
            "processor 异常必须返回 status=error，让调用方走错误路径；"
            f"实际 status={result.status}"
        )
        assert result.response_text == "", "异常路径 response_text 应为空"
        assert result.merged_input == "问题", (
            "异常路径仍应回传原始 user_input 作为 merged_input，便于日志诊断"
        )
        # 锁应已释放，不阻塞下一轮处理
        assert not q.is_locked("sid_p0_5"), "异常后应释放会话锁"

    @pytest.mark.asyncio
    async def test_processor_exception_not_silently_merged(self, q, fake_redis):
        """processor 异常时绝不能返回 status="merged"（P0-5 核心回归）。

        为什么重要：review 发现原实现 finally 后 fall through 到 return merged，
        调用方拿不到错误信号，user 消息也不写入，失败完全静默。
        本测试锁定「异常 ≠ merged」这一核心契约。
        """
        async def failing_processor(cancel_check, user_input_override=None):
            raise ValueError("simulated agent failure")

        result = await q.enqueue_and_process(
            session_id="sid_not_merged",
            user_input="hi",
            processor=failing_processor,
        )

        assert result.status != "merged", (
            "processor 异常时绝不能返回 status=merged，否则失败会被静默吞掉"
        )
        assert result.status == "error"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
