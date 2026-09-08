"""
recap runner 队列化改造单元测试

覆盖：
1. RecapPayload.to_dict / from_dict 往返（不含 record_service）
2. rebuild_tasks 从序列化 task_config 重建任务列表（含 when 白名单校验）
3. trigger_recap 入队成功 -> 不再进程内 create_task（dict 直传，无双层编码）
4. trigger_recap 入队失败（Redis 不可用）-> 降级进程内 create_task
5. _handle_recap_message 消费侧：dict/双重编码旧消息/陈旧消息/非法消息
6. trace_persist append_recap_span / append_recap_summary best-effort（DB 异常不上抛）
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from src.services.recap.runner import (
    RECAP_MAX_QUEUE_AGE_SECONDS,
    RecapPayload,
    RecapTaskConfig,
    rebuild_tasks,
    trigger_recap,
)


def _make_payload(**overrides) -> RecapPayload:
    base = dict(
        tenant_id="tenant_abc",
        session_id="tenant_abc_wecom_kf_kf1_user1_pre-sales",
        subagent_name="pre-sales",
        round_message_id="msg_123",
        user_content="你们卖什么",
        assistant_reply="我们卖……",
        record_service=None,
        user_id="user_1",
        trace_id="tr_abc123",
        enqueued_at=time.time(),
    )
    base.update(overrides)
    return RecapPayload(**base)


# ============== 序列化往返 ==============

class TestPayloadSerialization:
    def test_round_trip(self):
        payload = _make_payload()
        payload.task_config = [{"name": "external_push", "when": "every_round", "enabled": True}]
        data = payload.to_dict()
        assert "record_service" not in data
        rebuilt = RecapPayload.from_dict(data)
        assert rebuilt.tenant_id == payload.tenant_id
        assert rebuilt.session_id == payload.session_id
        assert rebuilt.subagent_name == payload.subagent_name
        assert rebuilt.round_message_id == payload.round_message_id
        assert rebuilt.user_content == payload.user_content
        assert rebuilt.assistant_reply == payload.assistant_reply
        assert rebuilt.user_id == payload.user_id
        assert rebuilt.trace_id == payload.trace_id
        assert rebuilt.task_config == payload.task_config
        assert rebuilt.enqueued_at == payload.enqueued_at
        assert rebuilt.record_service is None

    def test_from_dict_missing_fields(self):
        rebuilt = RecapPayload.from_dict({})
        assert rebuilt.tenant_id == ""
        assert rebuilt.round_message_id is None
        assert rebuilt.task_config is None
        assert rebuilt.user_id is None
        assert rebuilt.trace_id is None


# ============== 任务列表重建 ==============

class TestRebuildTasks:
    def test_rebuild_from_config(self):
        tasks = rebuild_tasks([
            {"name": "external_push", "when": "every_round", "enabled": True},
        ])
        assert len(tasks) == 1
        assert isinstance(tasks[0], RecapTaskConfig)
        assert tasks[0].name == "external_push"
        assert tasks[0].enabled is True

    def test_rebuild_skips_invalid(self):
        tasks = rebuild_tasks([{"when": "every_round"}, "bad", {"name": " "}, None])
        assert tasks == []

    def test_rebuild_skips_unknown_when(self):
        """when 不在白名单的任务跳过（与 parse_recap_tasks 对齐）"""
        tasks = rebuild_tasks([
            {"name": "external_push", "when": "every_round", "enabled": True},
            {"name": "bad_task", "when": "on_session_close", "enabled": True},
        ])
        assert len(tasks) == 1
        assert tasks[0].name == "external_push"

    def test_rebuild_empty(self):
        assert rebuild_tasks(None) == []
        assert rebuild_tasks([]) == []


# ============== 触发侧入队/降级 ==============

class TestTriggerRecapEnqueue:
    def _make_agent(self):
        agent = MagicMock()
        agent.subagent_config.recap = {
            "tasks": [{"name": "external_push", "when": "every_round", "enabled": True}],
        }
        agent.subagent_config.name = "pre-sales"
        return agent

    def test_enqueue_success_no_inline_task(self):
        """入队成功时不走进程内 create_task"""
        record_service = MagicMock()
        record_service.user_id = "user_1"
        collector = MagicMock()
        collector.trace_id = "tr_xyz"
        record_service.trace_collector = collector

        with patch("src.services.recap.runner.redis_client") as mock_redis, \
                patch("src.services.recap.runner.asyncio.create_task") as mock_create:
            mock_redis.rpush.return_value = True
            mock_redis.acquire_lock.return_value = True
            trigger_recap(
                agent=self._make_agent(),
                session_id="tenant_abc_wecom_kf_kf1_user1_pre-sales",
                tenant_id="tenant_abc",
                user_content="hi",
                assistant_reply="hello",
                round_message_id="msg_1",
                record_service=record_service,
            )
            assert mock_redis.rpush.called is True
            assert mock_create.called is False

    def test_enqueue_failure_falls_back_inline(self):
        """Redis 不可用（rpush 返回 False）时降级进程内 create_task"""
        with patch("src.services.recap.runner.redis_client") as mock_redis, \
                patch("src.services.recap.runner._run_tasks") as mock_run, \
                patch("src.services.recap.runner.asyncio.create_task") as mock_create:
            mock_redis.rpush.return_value = False
            trigger_recap(
                agent=self._make_agent(),
                session_id="tenant_abc_wecom_kf_kf1_user1_pre-sales",
                tenant_id="tenant_abc",
                user_content="hi",
                assistant_reply="hello",
                round_message_id="msg_2",
                record_service=None,
            )
            assert mock_create.called is True
            tasks_arg, payload_arg = mock_run.call_args[0]
            assert tasks_arg[0].name == "external_push"
            assert payload_arg.round_message_id == "msg_2"

    def test_enqueue_payload_content(self):
        """入队 payload 为 dict（rpush 内部统一编码，无双层编码）且含 trace_id/user_id/task_config"""
        record_service = MagicMock()
        record_service.user_id = "user_9"
        collector = MagicMock()
        collector.trace_id = "tr_999"
        record_service.trace_collector = collector

        with patch("src.services.recap.runner.redis_client") as mock_redis:
            mock_redis.rpush.return_value = True
            mock_redis.make_key.side_effect = lambda prefix, identifier="": (
                f"{prefix}:{identifier}" if identifier else prefix
            )
            trigger_recap(
                agent=self._make_agent(),
                session_id="tenant_abc_wecom_kf_kf1_user1_pre-sales",
                tenant_id="tenant_abc",
                user_content="hi",
                assistant_reply="hello",
                round_message_id="msg_3",
                record_service=record_service,
            )
            queue_key = mock_redis.rpush.call_args[0][0]
            body = mock_redis.rpush.call_args[0][1]
        assert "recap_task_queue" in queue_key
        assert isinstance(body, dict)
        assert body["trace_id"] == "tr_999"
        assert body["user_id"] == "user_9"
        assert body["task_config"] == [{"name": "external_push", "when": "every_round", "enabled": True}]
        assert body["round_message_id"] == "msg_3"
        assert body["enqueued_at"] > 0


# ============== 消费侧消息处理 ==============

class TestHandleRecapMessage:
    """background_runner._handle_recap_message（真实序列化语义，非 mock 返回值）"""

    def _make_msg_dict(self, **overrides) -> dict:
        payload = _make_payload()
        payload.task_config = [{"name": "external_push", "when": "every_round", "enabled": True}]
        msg = payload.to_dict()
        msg.update(overrides)
        return msg

    @pytest.mark.asyncio
    async def test_dispatch_dict_message(self):
        """dict 消息（lpop 单层解码后的正常形态）正常派发"""
        import src.background_runner as br

        with patch("src.services.recap.runner._run_tasks") as mock_run, \
                patch("src.background_runner.asyncio.create_task") as mock_create:
            br._handle_recap_message(self._make_msg_dict())
            assert mock_create.called is True
        tasks_arg, payload_arg = mock_run.call_args[0]
        assert tasks_arg[0].name == "external_push"
        assert payload_arg.trace_id == "tr_abc123"
        assert payload_arg.record_service is None

    @pytest.mark.asyncio
    async def test_double_encoded_legacy_message(self):
        """发布窗口期在途的旧版双重编码消息（字符串）兼容派发"""
        import src.background_runner as br

        inner = json.dumps(self._make_msg_dict(), ensure_ascii=False)
        msg = json.loads(json.dumps(inner))  # 模拟 lpop 解一层后得到内层字符串
        assert isinstance(msg, str)

        with patch("src.services.recap.runner._run_tasks"), \
                patch("src.background_runner.asyncio.create_task") as mock_create:
            br._handle_recap_message(msg)
            assert mock_create.called is True

    @pytest.mark.asyncio
    async def test_stale_message_dropped(self):
        """滞留超过 RECAP_MAX_QUEUE_AGE_SECONDS 的陈旧消息丢弃"""
        import src.background_runner as br

        msg = self._make_msg_dict(enqueued_at=time.time() - RECAP_MAX_QUEUE_AGE_SECONDS - 60)
        with patch("src.services.recap.runner._run_tasks"), \
                patch("src.background_runner.asyncio.create_task") as mock_create:
            br._handle_recap_message(msg)
            assert mock_create.called is False

    @pytest.mark.asyncio
    async def test_message_without_enqueued_at_dispatched(self):
        """旧消息无 enqueued_at 字段时不做年龄检查，正常派发"""
        import src.background_runner as br

        msg = self._make_msg_dict(enqueued_at=None)
        with patch("src.services.recap.runner._run_tasks"), \
                patch("src.background_runner.asyncio.create_task") as mock_create:
            br._handle_recap_message(msg)
            assert mock_create.called is True

    @pytest.mark.asyncio
    async def test_invalid_message_dropped(self):
        import src.background_runner as br

        with patch("src.services.recap.runner._run_tasks"), \
                patch("src.background_runner.asyncio.create_task") as mock_create:
            br._handle_recap_message("not-json{{{")
            br._handle_recap_message(12345)
            br._handle_recap_message({"task_config": []})
            assert mock_create.called is False


# ============== trace 追加 best-effort ==============

class TestTraceAppendBestEffort:
    def test_append_span_db_error_swallowed(self):
        from src.core.trace_persist import append_recap_span

        with patch("src.db.database.get_logs_connection", side_effect=RuntimeError("db down")):
            append_recap_span(
                trace_id="tr_x", name="recap:test", span_type="generation",
                model="m", usage={"prompt_tokens": 1, "completion_tokens": 2},
                start_time=time.time(), end_time=time.time(),
            )  # 不应抛异常

    def test_append_summary_db_error_swallowed(self):
        from src.core.trace_persist import append_recap_summary

        with patch("src.db.database.get_logs_connection", side_effect=RuntimeError("db down")):
            append_recap_summary("tr_x", {"recap": {"status": "ok"}})  # 不应抛异常

    def test_append_skips_empty_trace_id(self):
        from src.core.trace_persist import append_recap_span, append_recap_summary

        with patch("src.db.database.get_logs_connection") as mock_conn:
            append_recap_span(trace_id="", name="recap:test")
            append_recap_summary("", {"recap": {}})
            assert mock_conn.called is False
