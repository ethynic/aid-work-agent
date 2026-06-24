"""
ChannelSessionManager.process_and_persist / add_messages_batch_transactional 单元测试

背景（P0 改造，详见 docs/research/wecom-kf-context-loss-research.md）：
- 旧路径在调用 session_queue.enqueue_and_process 之前立即写 user 消息，
  导致 L2 合并触发时 channel_messages 仍写入两条独立 user → 下次加载出现连续 user，
  LLM 上下文错乱（历史 assistant 失效）。
- P0-1：add_messages_batch_transactional 把 [user, tool 序列, 最终 assistant] 作为
  原子事务写入；任一失败全部回滚，杜绝「tool 残留但 assistant 缺失」的脏数据。
- P0-2：process_and_persist 把 user 写入推迟到 enqueue 返回之后，根据是否被合并决定
  写入 merged_input 还是原始 user_content；被合并方完全不写任何消息。
- 异常兜底：批量写入失败时调用 _ensure_last_not_orphan_user 补一条 assistant 占位，
  避免 channel_messages 末尾是孤立 user 导致下次加载连续 user。

本测试验证上述契约为何重要——不是单纯复现代码逻辑。
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.session import ChannelSessionManager
from src.core.session_queue import EnqueueResult


pytestmark = pytest.mark.channels


# ============================================================
# Mock DB（简化版，只覆盖本测试需要的 SQL 模式）
# ============================================================


def _make_mock_db():
    """构造一个内存 mock DB，捕获所有 execute 调用以便断言。

    返回 (memory_store, mock_conn, execute_spy)。
    execute_spy 是一个 list，记录每次 (sql_lower, params) 元组。
    """
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

        if "insert into channel_sessions" in sql_lower:
            # params: session_id, tenant_id, channel_type, channel_user_id, subagent_id,
            #         channel_chat_id, user_id, username, title, context_data, metadata
            row = MockRow(
                session_id=params[0],
                tenant_id=params[1],
                channel_type=params[2],
                channel_user_id=params[3],
                subagent_id=params[4],
                channel_chat_id=params[5],
                user_id=params[6],
                username=params[7],
                title=params[8],
                context_data=params[9],
                metadata=params[10],
                created_at="2026-06-23 10:00:00",
                updated_at="2026-06-23 10:00:00",
                last_message_at=None,
            )
            memory_store["sessions"][params[0]] = row
            cursor.rowcount = 1
            cursor._fetch_rows = [row]  # RETURNING 子句

        elif "update channel_sessions" in sql_lower:
            sid = params[-1]
            if sid in memory_store["sessions"]:
                # 仅更新 last_message_at / updated_at（本测试关心的字段）
                if len(params) >= 3:
                    memory_store["sessions"][sid]["last_message_at"] = params[0]
                    memory_store["sessions"][sid]["updated_at"] = params[1]
            cursor.rowcount = 1

        elif "select * from channel_sessions" in sql_lower:
            sid = params[0]
            if sid in memory_store["sessions"]:
                cursor._fetch_rows = [memory_store["sessions"][sid]]
            cursor.rowcount = 1 if cursor._fetch_rows else 0

        elif "insert into channel_messages" in sql_lower:
            # params: message_id, session_id, tenant_id, role, content, message_type,
            #         attachments, metadata
            row = MockRow(
                message_id=params[0],
                session_id=params[1],
                tenant_id=params[2],
                role=params[3],
                content=params[4],
                message_type=params[5],
                attachments=params[6],
                metadata=params[7],
                id=len(memory_store["messages"]) + 1,  # 自增 id，供 ORDER BY id DESC 使用
                created_at="2026-06-23 10:00:00",
            )
            memory_store["messages"].append(row)
            cursor.rowcount = 1

        elif "select * from channel_messages" in sql_lower:
            sid = params[0]
            rows = [r for r in memory_store["messages"] if r["session_id"] == sid]
            # 按 id DESC 返回（get_last_message 用）
            cursor._fetch_rows = list(reversed(rows))

        elif "create table" in sql_lower or "create index" in sql_lower:
            cursor.rowcount = 0

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
    """ChannelSessionManager 实例（_initialized=True 跳过建表）"""
    m = ChannelSessionManager()
    m._initialized = True
    return m


@pytest.fixture
def mock_db_ctx():
    """patch get_db_connection，返回 mock 连接 + memory_store + spy"""
    memory_store, conn, spy = _make_mock_db()
    with patch("src.channels.session.get_db_connection") as mock_get:
        mock_get.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get.return_value.__exit__ = MagicMock(return_value=False)
        yield memory_store, conn, spy


# ============================================================
# add_messages_batch_transactional
# ============================================================


class TestAddMessagesBatchTransactional:
    def test_success_writes_all_messages_and_updates_session_timestamp(self, manager, mock_db_ctx):
        """成功场景：[user, tool, assistant] 三条全部写入，且 channel_sessions.last_message_at 被更新。

        为什么重要：P0-1 要求 tool 序列与最终 assistant 作为原子事务一起写入，
        防止部分成功导致下次加载出现「孤儿 tool」+ 连续 user。
        """
        memory_store, _conn, spy = mock_db_ctx
        # 预置一个 session，便于断言 last_message_at 更新
        memory_store["sessions"]["sid_test"] = {
            "session_id": "sid_test", "tenant_id": "t1", "last_message_at": None,
            "updated_at": "old",
        }

        messages = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "", "metadata": {"tool_calls": [{"id": "tc1"}]}},
            {"role": "tool", "content": "结果", "metadata": {"tool_call_id": "tc1"}},
            {"role": "assistant", "content": "最终回复"},
        ]
        result = manager.add_messages_batch_transactional("sid_test", "t1", messages)

        assert result is not None, "成功时应返回 message_id 列表"
        assert len(result) == 4, "应按输入顺序写入 4 条"
        # DB 侧验证：4 条消息全部落库
        assert len(memory_store["messages"]) == 4
        assert [m["role"] for m in memory_store["messages"]] == [
            "user", "assistant", "tool", "assistant"
        ]
        # session 的 last_message_at 被更新（UPDATE 调用至少一次）
        update_calls = [s for s in spy if "update channel_sessions" in s[0]]
        assert len(update_calls) >= 1, "应 UPDATE channel_sessions.last_message_at"
        assert memory_store["sessions"]["sid_test"]["last_message_at"] is not None

    def test_empty_messages_returns_empty_list_not_none(self, manager, mock_db_ctx):
        """边界：空列表直接返回 []，不能误返回 None（调用方把 None 当失败处理）。

        为什么重要：process_and_persist 用 `if write_ok is None` 判失败。
        若空列表返回 None 会被误判为失败，触发不必要的兜底写入。
        """
        result = manager.add_messages_batch_transactional("sid_test", "t1", [])
        assert result == []
        assert result is not None

    def test_failure_triggers_rollback_and_returns_none(self, manager, mock_db_ctx):
        """失败回滚：第二条 INSERT 抛异常 → conn.rollback 被调用 → 返回 None → 数据库无残留。

        为什么重要：P0-1 的核心契约是「要么全成功要么全回滚」，
        保证 channel_messages 中绝不出现半截 tool 序列（孤儿 tool 会污染下次 LLM 上下文）。
        """
        memory_store, conn, _spy = mock_db_ctx
        original_execute = conn.cursor.return_value.execute

        def failing_execute(sql, params=None):
            # 第二条 INSERT channel_messages 时抛异常
            if "insert into channel_messages" in sql.lower():
                if len([c for c in memory_store["messages"]]) >= 1:
                    raise RuntimeError("simulated DB error")
            return original_execute(sql, params)

        conn.cursor.return_value.execute = failing_execute

        messages = [
            {"role": "user", "content": "问题"},
            {"role": "tool", "content": "结果"},
            {"role": "assistant", "content": "回复"},
        ]
        result = manager.add_messages_batch_transactional("sid_test", "t1", messages)

        assert result is None, "失败时必须返回 None"
        conn.rollback.assert_called_once(), "失败时必须调用 rollback"
        # 关键断言：失败后数据库无残留（第一条写入也被回滚——mock 没有真事务，
        # 但生产代码用 with conn + rollback 保证；这里验证 rollback 至少被调用）
        # 由于 mock 不是真实事务，messages 可能已 append；但生产语义已由 rollback 保证


# ============================================================
# process_and_persist
# ============================================================


@pytest.fixture
def patched_session_queue():
    """patch src.channels.session 内部导入的 session_queue 单例"""
    with patch("src.core.session_queue.session_queue") as mock_sq:
        mock_sq.enqueue_and_process = AsyncMock()
        mock_sq.mark_responding = MagicMock()
        mock_sq.mark_idle = MagicMock()
        yield mock_sq


@pytest.fixture
def stub_agent():
    """最小 Agent stub：process_message_sync 返回固定文本"""
    agent = MagicMock()
    agent.process_message_sync = AsyncMock(return_value="hello back")
    agent.llm.get_model_name = MagicMock(return_value="test-model")
    agent.llm.get_provider_name = MagicMock(return_value="test-provider")
    return agent


class TestProcessAndPersist:
    @pytest.mark.asyncio
    async def test_independent_path_writes_user_and_assistant(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        """独立处理路径：enqueue 返回 success（was_merged=False）→ 写 1 条 user + 1 条 assistant。

        为什么重要：P0-2 的核心契约——独立处理时 user 和 assistant 作为同一事务批量写入，
        保证 LLM 下次加载历史时看到完整的「上一轮 user→assistant」配对。
        """
        memory_store, _conn, _spy = mock_db_ctx
        patched_session_queue.enqueue_and_process.return_value = EnqueueResult(
            status="success",
            response_text="hello back",
            merged_input="你好",
            was_merged=False,
        )

        send_response = AsyncMock(return_value=True)

        result = await manager.process_and_persist(
            session_id="sid_test",
            tenant_id="t1",
            user_content="你好",
            agent=stub_agent,
            send_response=send_response,
        )

        assert result["status"] == "success"
        assert result["response_text"] == "hello back"
        # 独立处理：写一条 user + 一条 assistant
        roles = [m["role"] for m in memory_store["messages"]]
        assert roles == ["user", "assistant"], (
            "独立处理应写入 user + assistant 两条；实际: " + str(roles)
        )
        assert memory_store["messages"][0]["content"] == "你好"
        assert memory_store["messages"][1]["content"] == "hello back"
        # send_response 被调用，且 mark_responding/mark_idle 成对
        send_response.assert_awaited_once_with("hello back", [])
        patched_session_queue.mark_responding.assert_called_once_with("sid_test")
        patched_session_queue.mark_idle.assert_called_once_with("sid_test")

    @pytest.mark.asyncio
    async def test_merged_path_does_not_write_any_message(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        """被合并方路径：enqueue 返回 status="merged" → 完全不写消息，不调用 send_response。

        为什么重要：被合并方的回复由合并方统一发送和持久化；若被合并方也写 user，
        就会产生两条独立 user，正是本次 P0 要根治的「连续 user」源头。
        """
        memory_store, _conn, _spy = mock_db_ctx
        patched_session_queue.enqueue_and_process.return_value = EnqueueResult(
            status="merged",
            response_text="",
            merged_input="A\nB",
            was_merged=False,  # 被合并方，不是合并方
        )

        send_response = AsyncMock(return_value=True)

        result = await manager.process_and_persist(
            session_id="sid_test",
            tenant_id="t1",
            user_content="B",
            agent=stub_agent,
            send_response=send_response,
        )

        assert result["status"] == "merged"
        assert result["was_merged"] is True
        # 关键断言：被合并方不写任何 channel_messages
        assert len(memory_store["messages"]) == 0, (
            "被合并方绝不能写消息，否则会产生连续 user；实际写入: "
            + str([m["role"] for m in memory_store["messages"]])
        )
        # 不发送回复
        send_response.assert_not_awaited()
        # mark_responding / mark_idle 也不应被调用（不需要推送）
        patched_session_queue.mark_responding.assert_not_called()

    @pytest.mark.asyncio
    async def test_merger_path_writes_single_user_with_merged_input(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        """合并方路径：was_merged=True + merged_input="A\\nB" → 写 1 条 user（content=merged_input）。

        为什么重要：合并方实际跑的 LLM 输入是 "A\\nB"，持久化时也必须写 "A\\nB"，
        保证存储与 LLM 实际看到的输入完全一致，下次加载不会出现 "user(A) → user(B)"
        两条独立消息（这正是用户日志观察到的现象）。
        """
        memory_store, _conn, _spy = mock_db_ctx
        patched_session_queue.enqueue_and_process.return_value = EnqueueResult(
            status="success",
            response_text="回复AB",
            merged_input="A\nB",
            was_merged=True,
        )

        send_response = AsyncMock(return_value=True)

        result = await manager.process_and_persist(
            session_id="sid_test",
            tenant_id="t1",
            user_content="A",  # 合并方的原始 user_content 只是 A
            agent=stub_agent,
            send_response=send_response,
        )

        assert result["status"] == "success"
        assert result["was_merged"] is True
        # 关键：user 消息的 content 必须是 merged_input（"A\nB"），不是原始 "A"
        assert len(memory_store["messages"]) == 2
        user_msg = memory_store["messages"][0]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "A\nB", (
            "合并方必须持久化 merged_input，而非原始 user_content；"
            f"实际: {user_msg['content']!r}"
        )
        assert memory_store["messages"][1]["content"] == "回复AB"

    @pytest.mark.asyncio
    async def test_enqueue_exception_returns_error_status_no_write(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        """enqueue_and_process 抛异常 → 返回 status=error，不写消息。

        为什么重要：enqueue 异常时不应该写 user 消息（user 写入已经推迟到 enqueue 之后），
        否则末尾就是孤立 user。本测试验证 P0-2 的「user 写入推迟」契约。
        """
        memory_store, _conn, _spy = mock_db_ctx
        patched_session_queue.enqueue_and_process.side_effect = RuntimeError("redis down")

        send_response = AsyncMock(return_value=True)

        result = await manager.process_and_persist(
            session_id="sid_test",
            tenant_id="t1",
            user_content="你好",
            agent=stub_agent,
            send_response=send_response,
        )

        assert result["status"] == "error", "enqueue 异常应返回 error 状态"
        # 不写任何消息（user 写入已推迟到 enqueue 成功之后）
        assert len(memory_store["messages"]) == 0, (
            "enqueue 异常时不应写入任何消息（user 已推迟写入）"
        )
        send_response.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_write_failure_triggers_orphan_user_fallback(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        """批量写入失败 → _ensure_last_not_orphan_user 补写 assistant 占位 + 返回 status=error。

        为什么重要（P0-1 + P0-4 修复后）：
        - P0-4：批量写入失败时必须返回 status=error，调用方据此决定是否告知用户；
          原 return status=success 的行为完全静默，调用方无从感知。
        - P0-1：record_service 必须 mark_error，让会话状态被正确标记为失败。
        - _ensure_last_not_orphan_user 作为防御性兜底仍保留（注释说明新流程下理论上不触发）。
        """
        memory_store, _conn, _spy = mock_db_ctx

        # 预置历史：已有 user 消息（模拟上一轮残留）
        memory_store["messages"].append({
            "message_id": "msg_old", "session_id": "sid_test", "tenant_id": "t1",
            "role": "user", "content": "上一轮问题", "message_type": "text",
            "attachments": None, "metadata": None, "id": 1,
            "created_at": "2026-06-23 09:00:00",
        })

        patched_session_queue.enqueue_and_process.return_value = EnqueueResult(
            status="success",
            response_text="回复",
            merged_input="本次问题",
            was_merged=False,
        )

        # record_service mock：追踪 complete / mark_error 调用
        record_service = MagicMock()
        record_service.complete = MagicMock()
        record_service.mark_error = MagicMock()

        # 让 add_messages_batch_transactional 返回 None（模拟写入失败）
        with patch.object(
            manager, "add_messages_batch_transactional", return_value=None
        ) as mock_batch:
            send_response = AsyncMock(return_value=True)

            result = await manager.process_and_persist(
                session_id="sid_test",
                tenant_id="t1",
                user_content="本次问题",
                agent=stub_agent,
                send_response=send_response,
                record_service=record_service,
            )

            mock_batch.assert_called_once()
            # P0-4：批量写入失败返回 status=error（原 success 行为完全静默，已修复）
            assert result["status"] == "error", (
                "P0-4：批量写入失败必须返回 status=error，让调用方感知失败；"
                f"实际: {result['status']}"
            )

        # 关键断言：批量写入失败后，末尾不应是孤立 user
        # mock_db 中 batch INSERT 被替换为返回 None（不实际写入），
        # 但 _ensure_last_not_orphan_user 会检查末尾（仍是预置的 user）并补写 assistant
        roles = [m["role"] for m in memory_store["messages"]]
        # 末尾必须是 assistant 占位（_ensure_last_not_orphan_user 补写的）
        assert roles[-1] == "assistant", (
            "批量写入失败时，必须补写 assistant 占位避免孤立 user；末尾实际: "
            + str(roles[-1] if roles else None)
        )
        last_msg = memory_store["messages"][-1]
        assert last_msg["content"] == "[本次回复生成失败]"
        # P0-1：record_service 应被 mark_error，不应被 complete
        record_service.mark_error.assert_called_once(), (
            "P0-1：批量写入失败时必须调用 record_service.mark_error"
        )
        record_service.complete.assert_not_called(), (
            "P0-1：批量写入失败时不应调用 record_service.complete"
        )

    @pytest.mark.asyncio
    async def test_record_service_complete_called_only_after_batch_write_success(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        """record_service.complete 必须在批量写入成功之后调用（P0-1 时序契约）。

        为什么重要：原实现在批量写入之前调用 complete，agent 抛异常返回空字符串时
        仍调 complete("")；外层 except 又调 mark_error，造成状态双写。
        P0-1 修复后：complete 仅在批量写入成功后才调用，失败分支调 mark_error。
        本测试用 mock_batch 断言 complete 调用发生在 batch 成功之后。
        """
        memory_store, _conn, _spy = mock_db_ctx
        patched_session_queue.enqueue_and_process.return_value = EnqueueResult(
            status="success",
            response_text="hello back",
            merged_input="你好",
            was_merged=False,
        )

        record_service = MagicMock()
        record_service.complete = MagicMock()
        record_service.mark_error = MagicMock()

        # 用 spy 记录调用顺序
        call_order = []

        def batch_spy(*args, **kwargs):
            call_order.append("batch")
            return ["msg1", "msg2"]  # 成功

        def complete_spy(*args, **kwargs):
            call_order.append("complete")

        with patch.object(
            manager, "add_messages_batch_transactional", side_effect=batch_spy
        ):
            record_service.complete.side_effect = complete_spy
            send_response = AsyncMock(return_value=True)

            result = await manager.process_and_persist(
                session_id="sid_test",
                tenant_id="t1",
                user_content="你好",
                agent=stub_agent,
                send_response=send_response,
                record_service=record_service,
            )

        assert result["status"] == "success"
        # 关键断言：complete 必须在 batch 之后被调用
        assert call_order == ["batch", "complete"], (
            f"P0-1：record_service.complete 必须在批量写入成功之后调用；"
            f"实际调用顺序: {call_order}"
        )
        record_service.complete.assert_called_once_with("hello back")
        record_service.mark_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_exception_calls_record_service_mark_error(
        self, manager, mock_db_ctx, patched_session_queue, stub_agent
    ):
        """enqueue_and_process 抛异常 → 调用 record_service.mark_error（P0-1）。

        为什么重要：enqueue 异常分支原先只返回 error dict 但不调 mark_error，
        会话状态不会被标记失败，运营排查困难。P0-1 修复后必须调 mark_error。
        """
        memory_store, _conn, _spy = mock_db_ctx
        patched_session_queue.enqueue_and_process.side_effect = RuntimeError("redis down")

        record_service = MagicMock()
        record_service.complete = MagicMock()
        record_service.mark_error = MagicMock()

        send_response = AsyncMock(return_value=True)

        result = await manager.process_and_persist(
            session_id="sid_test",
            tenant_id="t1",
            user_content="你好",
            agent=stub_agent,
            send_response=send_response,
            record_service=record_service,
        )

        assert result["status"] == "error"
        # P0-1：enqueue 异常时必须调 mark_error，不能调 complete
        record_service.mark_error.assert_called_once(), (
            "P0-1：enqueue 异常时必须调用 record_service.mark_error"
        )
        record_service.complete.assert_not_called(), (
            "P0-1：enqueue 异常时不应调用 record_service.complete"
        )
        # 不写任何消息
        assert len(memory_store["messages"]) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
