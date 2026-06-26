"""
SessionMessageQueue.enqueue_and_process 状态机单元测试

背景（P0 改造，详见 docs/research/wecom-kf-context-loss-research.md §7、§8）：
本调度器是 L2 合并层，决定「同一 session 第二条消息到达时，合并、排队还是独立处理」。
本次 P0 改造把返回值由 str 改为 EnqueueResult，暴露 status / was_merged / merged_input，
让 channel_session_manager.process_and_persist 能据此决定 channel_messages 的正确写入方式。

本测试覆盖三种核心路径：
- 空闲态首条消息：acquire_lock 成功 → 跑 processor → 返回 success（was_merged 取决于窗口内是否合并）
- 处理中态 + 允许取消：set_cancel + append_merge → 旧请求重跑合并输入，新请求返回 merged
- 处理中态 + 已 mark_responding：set_pending → 旧请求完成后处理 pending，新请求返回 merged
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.session_queue import SessionMessageQueue, EnqueueResult


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

    with patch("src.core.session_queue.redis_client", fr):
        yield fr


# ============================================================
# 路径 1：空闲态首条消息
# ============================================================


class TestIdleFirstMessage:
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
        首条消息处理时读取到的 merged_input 是合并后的 "A\\n\\n[用户追加消息]\\nB"，was_merged=True。

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
        assert result.merged_input == "A\n\n[用户追加消息]\nB", (
            "merged_input 必须是带 [用户追加消息] 分隔标记的合并文本；实际: "
            + repr(result.merged_input)
        )


# ============================================================
# 路径 2：处理中态 + 允许取消（尚未 mark_responding） → 被合并方
# ============================================================


class TestMergingSecondMessage:
    @pytest.mark.asyncio
    async def test_second_message_during_processing_returns_merged(self, q, fake_redis):
        """处理中态：首条 A 持有锁（未 mark_responding），B 到达 → B 立即返回 status="merged"。

        为什么重要：被合并方（B）必须快速返回 merged，调用方据此跳过回复发送和 channel_messages
        写入；否则会产生两条独立 user 和两条独立 assistant 回复（用户体感错乱）。
        """
        processor_A = AsyncMock(return_value="reply-A")

        async def slow_processor_A(cancel_check):
            # 让 A 长时间持有锁，给 B 到达的机会
            await asyncio.sleep(0.6)
            return "reply-A"

        async def run_A():
            return await q.enqueue_and_process(
                session_id="sid_merge2", user_input="A", processor=slow_processor_A
            )

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
            session_id="sid_merge2", user_input="B", processor=processor_B
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

        async def processor_A(cancel_check):
            # A 标记 responding，模拟「已经开始 send_message 推送」
            q.mark_responding("sid_pending")
            responding_ready.set()
            # 在 responding 状态下，B 到达
            await asyncio.sleep(0.5)
            processor_calls.append("A")
            return "reply-A"

        async def run_A():
            return await q.enqueue_and_process(
                session_id="sid_pending", user_input="A", processor=processor_A
            )

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
        )
        assert result_B.status == "merged", (
            "已 mark_responding 后到达的 B 也返回 merged（走 pending 分支，由 A 完成后处理）"
        )

        # 等 A 完成（A 在 finally 中检测 pending 并处理）
        result_A = await task_A
        # A 完成后会处理 pending（B），所以 A 最终返回 success
        assert result_A.status == "success"
        assert "A" in processor_calls


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
        async def failing_processor(cancel_check):
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
        async def failing_processor(cancel_check):
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
