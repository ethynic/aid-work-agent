"""飞书/钉钉对话服务接入 AgentRunner 的入口级集成测试。

覆盖审核修复点：
1. Runner 失败（RunnerError）经 process_and_persist 显式 error 分支：
   发非空失败提示、不写空 assistant、mark_error、不触发 recap；
2. 成功链路：ChannelRunnerAgent 构造参数、user_input、recap、batch 持久化；
3. merged 结果：不发送任何消息；
4. start_record 后 skip_save=True + end_record 收尾；
5. 钉钉群聊/单聊 reply 目标；
6. worker._agent_user 渠道来源的 name 优先级与渠道字段；
7. worker.channel_verbose_config 渠道 verbose 配置归属。

测试直接调用 `_process_tenant_feishu_background` / `_process_tenant_dingtalk_background`
真实函数体，边界（DB/Redis/Runner 服务）全部 mock。
"""

from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.agent import AgentResponse
from src.core.session_queue import EnqueueResult
from src.services.agent_runner.contracts import RunnerError

FAILURE_NOTICE = "抱歉，处理您的消息时遇到了问题，请稍后重试。"

_CHANNEL_USER_IDS = {"feishu": "ou_user_1", "dingtalk": "staff_1"}


def _entry_function(channel):
    from src.saas.api import channel_routes

    return {
        "feishu": channel_routes._process_tenant_feishu_background,
        "dingtalk": channel_routes._process_tenant_dingtalk_background,
    }[channel]


def _make_message(channel, *, text="你好", conversation_type="1"):
    """构造 parse_message 的返回（UnifiedMessage 形态的子集）。"""
    if channel == "dingtalk":
        content = {
            "conversation_type": conversation_type,
            "conversation_id": "cid-group" if conversation_type == "2" else "",
        }
    else:
        content = {}
    return SimpleNamespace(
        user_id=_CHANNEL_USER_IDS[channel],
        user_name="张三",
        text=text,
        message_id="msg-1",
        message_type="text",
        raw_message={"msgid": "msg-1"},
        content=content,
    )


def _make_event(channel, *, conversation_type="1"):
    if channel == "dingtalk":
        return {
            "msgtype": "text",
            "text": {"content": "你好"},
            "senderId": "staff_1",
            "conversationType": conversation_type,
            "conversationId": "cid-group" if conversation_type == "2" else "",
        }
    return {
        "header": {"event_id": "evt-1", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user_1"}},
            "message": {"message_id": "msg-1", "message_type": "text"},
        },
    }


def _make_enqueue(mode="through"):
    """镜像 session_queue.enqueue_and_process 的成功/异常分流行为。

    mode:
        through: 真实调用 processor，异常转 status='error'（与 session_queue 841-853 一致）
        merged: 直接返回 status='merged'
    """
    async def enqueue_and_process(**kwargs):
        if mode == "merged":
            return EnqueueResult(
                status="merged", was_merged=True, merged_input="你好 合并后"
            )
        try:
            response = await kwargs["processor"](lambda: False, kwargs["user_input"])
        except Exception:
            return EnqueueResult(
                status="error", merged_input=kwargs["user_input"], was_merged=False
            )
        return EnqueueResult(
            status="success",
            response_text=response,
            merged_input=kwargs["user_input"],
            was_merged=False,
            lease_token="lease-1",
        )

    return enqueue_and_process


def _patch_entry_environment(monkeypatch, channel, message, enqueue):
    """为后台入口打满边界 patch，返回可断言的 mock 集合。"""
    from src.saas.api import channel_routes

    adapter = MagicMock()
    adapter.parse_message = AsyncMock(return_value=message)
    adapter.send_message = AsyncMock(return_value=True)
    adapter.get_user_info = AsyncMock(return_value={})
    monkeypatch.setattr(
        channel_routes.ChannelFactory,
        "create_from_tenant_config",
        AsyncMock(return_value=(adapter, None, None)),
    )
    monkeypatch.setattr(
        "src.saas.services.auto_register.ensure_user_registered",
        AsyncMock(return_value="u-1"),
    )
    monkeypatch.setattr(
        channel_routes.channel_session_manager,
        "get_or_create_session",
        MagicMock(return_value={"session_id": "sess-1"}),
    )

    class FakeChannelRunnerAgent:
        behavior = "ok"
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.user_inputs = []
            FakeChannelRunnerAgent.instances.append(self)

        async def process_message_sync(self, *, user_input, session_id, **kwargs):
            self.user_inputs.append(user_input)
            if FakeChannelRunnerAgent.behavior == "fail":
                raise RunnerError("RUNNER_EXECUTION_FAILED", 502)
            return AgentResponse("好的")

    monkeypatch.setattr(
        "src.channels.runner_agent.ChannelRunnerAgent", FakeChannelRunnerAgent
    )

    record = MagicMock()
    record.trace_collector = None  # 避免 trace 回填走真实 DB
    record.skip_save = False
    end_record = MagicMock(return_value=None)
    monkeypatch.setattr(
        channel_routes.SessionRecordManager, "start_record", MagicMock(return_value=record)
    )
    monkeypatch.setattr(channel_routes.SessionRecordManager, "end_record", end_record)

    queue = SimpleNamespace(
        enqueue_and_process=enqueue,
        mark_responding=MagicMock(),
        mark_idle=MagicMock(),
        finish_processing=MagicMock(),
        release_lock=MagicMock(),
    )
    monkeypatch.setattr("src.core.session_queue.session_queue", queue)
    monkeypatch.setattr(
        "src.saas.db.tenant_db.TenantDB.get_by_id", MagicMock(return_value=None)
    )
    recap = MagicMock()
    monkeypatch.setattr("src.services.recap.trigger_recap", recap)

    batch_write = MagicMock(return_value=["mid-user", "mid-assistant"])
    monkeypatch.setattr(
        channel_routes.channel_session_manager,
        "add_messages_batch_transactional",
        batch_write,
    )
    monkeypatch.setattr(
        channel_routes, "resolve_verbose_feedback_config", MagicMock(return_value=None)
    )
    monkeypatch.setattr(
        "src.channels.agent_user_builder.build_agent_user_for_channel",
        AsyncMock(return_value=None),
    )
    return SimpleNamespace(
        adapter=adapter,
        record=record,
        end_record=end_record,
        queue=queue,
        recap=recap,
        batch_write=batch_write,
        agent_cls=FakeChannelRunnerAgent,
    )


# ---------- 1. Runner 失败 → 显式 error 分支 ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["feishu", "dingtalk"])
@pytest.mark.parametrize("conversation_type", ["2", "1"])
async def test_runner_error_sends_failure_notice_without_empty_assistant(
    monkeypatch, channel, conversation_type
):
    """RunnerError 被转为 error 返回：发非空失败提示到正确目标，不写空 assistant。"""
    message = _make_message(channel, conversation_type=conversation_type)
    harness = _patch_entry_environment(monkeypatch, channel, message, _make_enqueue())
    harness.agent_cls.behavior = "fail"

    await _entry_function(channel)("tenant-1", _make_event(channel, conversation_type=conversation_type))

    # 非空失败提示经真实 make_send_response → adapter.send_message
    harness.adapter.send_message.assert_awaited_once()
    response = harness.adapter.send_message.await_args.args[0]
    assert response.content["text"] == FAILURE_NOTICE
    if channel == "dingtalk":
        expected_target = "cid-group" if conversation_type == "2" else "staff_1"
        assert response.content["conversation_type"] == conversation_type
    else:
        expected_target = "ou_user_1"
    assert response.reply_to == expected_target
    # 不写空 assistant、不触发 recap、record 标记失败
    harness.batch_write.assert_not_called()
    harness.recap.assert_not_called()
    harness.record.mark_error.assert_called_once_with("对话处理失败")
    harness.end_record.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["feishu", "dingtalk"])
async def test_runner_error_send_failure_does_not_raise_when_adapter_fails(
    monkeypatch, channel
):
    """失败提示发送本身抛异常时不再上抛（不触发入口外层兜底重复发送）。"""
    message = _make_message(channel)
    harness = _patch_entry_environment(monkeypatch, channel, message, _make_enqueue())
    harness.agent_cls.behavior = "fail"
    harness.adapter.send_message = AsyncMock(side_effect=RuntimeError("adapter down"))

    await _entry_function(channel)("tenant-1", _make_event(channel))

    harness.adapter.send_message.assert_awaited_once()
    harness.record.mark_error.assert_called_once_with("对话处理失败")


# ---------- 2. 成功链路 ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["feishu", "dingtalk"])
@pytest.mark.parametrize(
    "subagent_type,expected_profile", [(None, "main"), ("sales", "sales")]
)
async def test_success_round_trip_constructs_agent_and_persists_batch(
    monkeypatch, channel, subagent_type, expected_profile
):
    message = _make_message(channel)
    harness = _patch_entry_environment(monkeypatch, channel, message, _make_enqueue())

    await _entry_function(channel)(
        "tenant-1",
        _make_event(channel),
        config_id="cfg-1",
        subagent_type=subagent_type,
    )

    agent = harness.agent_cls.instances[-1]
    assert agent.user_inputs == ["你好"]
    assert agent.kwargs == {
        "source": channel,
        "session_id": "sess-1",
        "channel_user_id": _CHANNEL_USER_IDS[channel],
        "channel_chat_id": None,
        "profile_id": expected_profile,
        "config_id": "cfg-1",
    }
    # batch 持久化含 user + assistant
    harness.batch_write.assert_called_once()
    batch = harness.batch_write.call_args.args[2]
    assert [m["role"] for m in batch] == ["user", "assistant"]
    assert batch[0]["content"] == "你好" and batch[1]["content"] == "好的"
    # 回复送达（send_ok=True）→ recap 触发一次
    harness.adapter.send_message.assert_awaited_once()
    assert harness.adapter.send_message.await_args.args[0].content["text"] == "好的"
    harness.recap.assert_called_once()
    harness.queue.finish_processing.assert_called_once_with("sess-1", "lease-1")


# ---------- 3. merged：不发送任何消息 ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["feishu", "dingtalk"])
async def test_merged_result_sends_nothing(monkeypatch, channel):
    message = _make_message(channel)
    harness = _patch_entry_environment(monkeypatch, channel, message, _make_enqueue("merged"))

    await _entry_function(channel)("tenant-1", _make_event(channel))

    harness.adapter.send_message.assert_not_awaited()
    harness.batch_write.assert_not_called()
    harness.record.mark_error.assert_not_called()
    harness.recap.assert_not_called()
    harness.end_record.assert_called_once()


# ---------- 4. skip_save 归属 ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["feishu", "dingtalk"])
async def test_start_record_marks_skip_save_and_keeps_end_record(monkeypatch, channel):
    """渠道侧无独立入口用量：start_record 后 skip_save=True，end_record 仍收尾。"""
    message = _make_message(channel)
    harness = _patch_entry_environment(monkeypatch, channel, message, _make_enqueue())

    await _entry_function(channel)("tenant-1", _make_event(channel))

    assert harness.record.skip_save is True
    harness.end_record.assert_called_once()


# ---------- 5. 钉钉单聊 reply 目标（群聊已在 error 用例覆盖 '2'） ----------


@pytest.mark.asyncio
async def test_dingtalk_single_chat_success_replies_to_user_id(monkeypatch):
    channel = "dingtalk"
    message = _make_message(channel, conversation_type="1")
    harness = _patch_entry_environment(monkeypatch, channel, message, _make_enqueue())

    await _entry_function(channel)("tenant-1", _make_event(channel, conversation_type="1"))

    harness.adapter.send_message.assert_awaited_once()
    response = harness.adapter.send_message.await_args.args[0]
    assert response.reply_to == "staff_1" and response.content["conversation_type"] == "1"


# ---------- 6. worker 用户构造（修复 2） ----------


def _patch_user_record(monkeypatch, record):
    from src.db.models import UserDB

    monkeypatch.setattr(UserDB, "get_by_id", lambda user_id: record)


def test_worker_agent_user_prefers_nickname_for_channel_source(monkeypatch):
    from src.services.agent_runner.worker import RunnerWorker

    _patch_user_record(
        monkeypatch,
        {
            "user_id": "u1",
            "username": "feishu_x1",
            "nickname": "张三",
            "phone": "13800000000",
        },
    )
    user = RunnerWorker._agent_user("u1", "t1", source="feishu", channel_user_id="ou1")
    assert user.name == "张三"
    assert user.channel_type == "feishu" and user.channel_user_id == "ou1"
    assert user.phone == "13800000000" and user.tenant_id == "t1"


@pytest.mark.parametrize("source", ["chat", None])
def test_worker_agent_user_keeps_web_behavior_without_channel_source(monkeypatch, source):
    from src.services.agent_runner.worker import RunnerWorker

    _patch_user_record(
        monkeypatch,
        {
            "user_id": "u1",
            "username": "feishu_x1",
            "nickname": "张三",
            "phone": "13800000000",
        },
    )
    if source is None:
        user = RunnerWorker._agent_user("u1", "t1")
    else:
        user = RunnerWorker._agent_user("u1", "t1", source=source)
    # web 行为不变：username 优先、不带渠道字段
    assert user.name == "feishu_x1"
    assert user.channel_type is None and user.channel_user_id is None


def test_worker_agent_user_falls_back_to_username_without_nickname(monkeypatch):
    from src.services.agent_runner.worker import RunnerWorker

    _patch_user_record(
        monkeypatch,
        {"user_id": "u1", "username": "dingtalk_x1", "nickname": "", "phone": ""},
    )
    user = RunnerWorker._agent_user("u1", "t1", source="dingtalk", channel_user_id="s1")
    assert user.name == "dingtalk_x1"
    assert user.channel_type == "dingtalk" and user.channel_user_id == "s1"


def test_worker_agent_user_raises_for_missing_user(monkeypatch):
    from src.services.agent_runner.worker import RunnerWorker

    _patch_user_record(monkeypatch, None)
    with pytest.raises(RunnerError, match="USER_UNAUTHORIZED"):
        RunnerWorker._agent_user("missing", "t1", source="feishu", channel_user_id="ou1")


# ---------- 7. worker 渠道 verbose 配置归属（修复 4D） ----------


@pytest.mark.parametrize("source", ["wecom_kf", "feishu", "dingtalk"])
def test_channel_verbose_config_uses_channel_config_with_or_merged_force_disabled(source):
    from src.core.verbose_feedback import VerboseFeedbackConfig
    from src.services.agent_runner.worker import channel_verbose_config

    base = VerboseFeedbackConfig(enabled=False, force_disabled=True, max_per_turn=9)
    channel = VerboseFeedbackConfig(
        enabled=True, force_disabled=False, max_per_turn=3, max_text_chars=42
    )
    result = channel_verbose_config(source, {"verbose_feedback": asdict(channel)}, base)
    # 渠道配置生效，force_disabled 与全局取或（kill switch 优先）
    assert result.enabled is True
    assert result.max_per_turn == 3 and result.max_text_chars == 42
    assert result.force_disabled is True


def test_channel_verbose_config_channel_enabled_keeps_channel_values():
    from src.core.verbose_feedback import VerboseFeedbackConfig
    from src.services.agent_runner.worker import channel_verbose_config

    base = VerboseFeedbackConfig(force_disabled=False)
    channel = VerboseFeedbackConfig(enabled=True, force_disabled=False, max_per_turn=3)
    result = channel_verbose_config("feishu", {"verbose_feedback": asdict(channel)}, base)
    assert result.force_disabled is False and result.enabled is True


@pytest.mark.parametrize("source", ["chat", None, "desktop"])
def test_channel_verbose_config_keeps_base_for_web_sources(source):
    from src.core.verbose_feedback import VerboseFeedbackConfig
    from src.services.agent_runner.worker import channel_verbose_config

    base = VerboseFeedbackConfig(enabled=False, force_disabled=True)
    request_data = {"verbose_feedback": asdict(VerboseFeedbackConfig(enabled=True))}
    assert channel_verbose_config(source, request_data, base) is base


def test_channel_verbose_config_keeps_base_without_request_data():
    from src.core.verbose_feedback import VerboseFeedbackConfig
    from src.services.agent_runner.worker import channel_verbose_config

    base = VerboseFeedbackConfig(force_disabled=False)
    for source in ("feishu", "dingtalk", "wecom_kf", "chat"):
        assert channel_verbose_config(source, {}, base) is base
        assert channel_verbose_config(source, None, base) is base
