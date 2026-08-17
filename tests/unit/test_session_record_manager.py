"""SessionRecordManager 并发隔离回归测试

背景：SessionRecordManager 曾用 threading.local() 单槽存储 record_service。
wecom_kf / web SSE 等多条消息通过 asyncio.create_task 并发处理时共享同一
事件循环线程，threading.local() 在并发 task 间互相覆盖 record_service，
导致 end_record 保存错误实例、真实发生的 LLM/ASR 计费丢失。
改用 contextvars.ContextVar 后，每个 asyncio task 拥有独立 context，互不干扰。

覆盖：
- asyncio 并发 task 的 start_record/get_current_record/end_record 隔离
- 并发下各自 save 落库，asr_calls / token 不串号
- 同步场景（无 asyncio）start/get/end 行为正常
- 无 record 时 end_record 返回 None
"""

import asyncio
from unittest.mock import patch

import pytest

from src.services.session_record import SessionRecordManager


@pytest.fixture(autouse=True)
def _reset_record_state():
    """每个用例前清理 ContextVar，避免测试间残留"""
    SessionRecordManager.end_record()
    yield
    SessionRecordManager.end_record()


@pytest.fixture
def mock_chat_record_create():
    """mock ChatRecordDB.create，记录每次落库参数，避免真实数据库"""
    saved = []

    def _fake_create(**kwargs):
        saved.append({
            "session_id": kwargs.get("session_id"),
            "user_message": kwargs.get("user_message"),
            "asr_calls": kwargs.get("asr_calls") or 0,
            "total_token_count": kwargs.get("total_token_count") or 0,
            "prompt_tokens": kwargs.get("prompt_tokens") or 0,
            "completion_tokens": kwargs.get("completion_tokens") or 0,
        })
        return {"record_id": f"rec_{len(saved)}"}

    with patch("src.db.models.ChatRecordDB.create", side_effect=_fake_create) as mock_create:
        mock_create.saved = saved
        yield mock_create


class TestAsyncConcurrency:
    """核心回归：asyncio 并发 task 下 record 不得互相覆盖（曾导致计费丢失）"""

    @pytest.mark.asyncio
    async def test_concurrent_tasks_do_not_overwrite(self, mock_chat_record_create):
        """多个并发 handler（模拟 wecom_kf 多语音回调）各自 record 独立保存"""
        n = 5
        all_started = asyncio.Event()
        release = asyncio.Event()

        async def handler(idx):
            # 模拟 channel_routes 单条消息 handler
            record = SessionRecordManager.start_record(
                session_id=f"sess_{idx}",
                user_id=f"u{idx}",
                user_message=f"语音{idx}",
                tenant_id=f"t{idx}",
                source_type="wecom_kf",
            )
            record.add_asr_usage(calls=1)
            # 让所有 task 都 start_record 后再校验，制造并发覆盖窗口
            if idx == 0:
                all_started.set()
            await release.wait()
            # 关键断言：当前 task 拿到的必须是自己的 record（ContextVar 隔离）
            current = SessionRecordManager.get_current_record()
            assert current is record, f"task {idx} 的 record 被其他 task 覆盖"
            # 模拟 agent 主循环累加 LLM token
            record.add_llm_usage({
                "prompt_tokens": 10 * idx,
                "completion_tokens": 5 * idx,
                "total_tokens": 15 * idx,
            })
            return SessionRecordManager.end_record()

        tasks = [asyncio.create_task(handler(i)) for i in range(n)]
        await asyncio.wait_for(all_started.wait(), timeout=5)
        release.set()
        results = await asyncio.gather(*tasks)

        # 每个 task 都成功落库（修复前只有最后一个 start 的 record 被保存）
        assert all(r is not None for r in results), "存在 record 未落库"
        assert len(mock_chat_record_create.saved) == n, \
            f"应保存 {n} 条，实际 {len(mock_chat_record_create.saved)} 条"

        # 每个 record 独立携带自己的 asr_calls 与 token（不串号）
        by_session = {s["session_id"]: s for s in mock_chat_record_create.saved}
        for i in range(n):
            rec = by_session[f"sess_{i}"]
            assert rec["user_message"] == f"语音{i}"
            assert rec["asr_calls"] == 1, f"sess_{i} asr_calls 应为 1"
            assert rec["total_token_count"] == 15 * i, f"sess_{i} token 串号"
            assert rec["prompt_tokens"] == 10 * i
            assert rec["completion_tokens"] == 5 * i

    @pytest.mark.asyncio
    async def test_concurrent_start_after_first_end(self, mock_chat_record_create):
        """第一个 task end 后，第二个 task 的 record 不受影响（能独立 start/end）"""
        async def first():
            SessionRecordManager.start_record(
                session_id="s1", user_id="u1", user_message="m1"
            ).add_asr_usage(calls=1)
            await asyncio.sleep(0.01)
            return SessionRecordManager.end_record()

        async def second():
            # 在 first 尚未 end 时 start，制造覆盖窗口
            await asyncio.sleep(0.005)
            SessionRecordManager.start_record(
                session_id="s2", user_id="u2", user_message="m2"
            ).add_asr_usage(calls=2)
            await asyncio.sleep(0.02)
            return SessionRecordManager.end_record()

        r1, r2 = await asyncio.gather(first(), second())
        assert r1 is not None and r2 is not None

        by_session = {s["session_id"]: s for s in mock_chat_record_create.saved}
        assert by_session["s1"]["asr_calls"] == 1
        assert by_session["s2"]["asr_calls"] == 2


class TestSyncCompatibility:
    """无 asyncio 场景（gradio/CLI/测试）行为与 threading.local 一致"""

    def test_sync_start_get_end(self, mock_chat_record_create):
        record = SessionRecordManager.start_record(
            session_id="sync_s", user_id="sync_u", user_message="sync_m",
            tenant_id="sync_t", source_type="chat",
        )
        record.add_llm_usage({"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7})
        assert SessionRecordManager.get_current_record() is record

        saved = SessionRecordManager.end_record()
        assert saved is not None
        assert len(mock_chat_record_create.saved) == 1
        rec = mock_chat_record_create.saved[0]
        assert rec["session_id"] == "sync_s"
        assert rec["total_token_count"] == 7

    def test_end_record_empty_returns_none(self):
        assert SessionRecordManager.end_record() is None
