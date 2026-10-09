"""飞书/钉钉后台入口到独立 Runner 服务的真实接线回归（R4.2 固化）。

与 tests/integration/test_channel_agent_runner_entries.py 的区别：入口测试用
FakeChannelRunnerAgent 整体替换真实适配器；本文件恢复**真实 ChannelRunnerAgent +
真实 HTTP client + ASGI AgentRunner API + 真实 RunnerAuthorizer/RunnerManager**
（复用 test_channel_runner_service_independent 的 bridge 假件），把审查者临时
复现的 8 组合（channel × completed/failed/foreign_actor/history_unavailable）
固化为持续回归，另覆盖钉钉群聊 reply 目标。

真实/假件边界：
- 真实：`_process_tenant_feishu_background` / `_process_tenant_dingtalk_background`
  函数体、ChannelRunnerAgent、RunnerServiceClient（httpx → ASGI transport）、
  AgentRunner API 路由、RunnerManager、RunnerAuthorizer（channel_sessions /
  tenants / 租户积分校验）、channel-result 历史脱敏、ChannelSessionManager
  .process_and_persist（含 error 分流）、SessionRecordManager（真实 start_record
  / end_record / mark_error / complete；skip_save=True 时 save 不触库）。
- 假件：ChannelStore/MemoryRunners（内存行 + 提交即终态的 fake 执行，含
  pending_finalization 工具消息与 'test-hidden' 敏感参数）、fake platform
  adapter（parse_message/send_message/send_text 记录调用）、ensure_user_registered
  →'u-1'、get_or_create_session→bridge 会话行、session_queue 契约镜像 fake
  （真实调用 processor，异常转 status='error'）、TenantDB.get_by_id→None、
  add_messages_batch_transactional 记录 batch、trigger_recap 记录调用、
  resolve_verbose_feedback_config→None、build_agent_user_for_channel→None。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# bridge 假件（ChannelStore/MemoryRunners + create_app + ASGI transport patch）
from tests.unit.channels.test_channel_runner_service_independent import bridge  # noqa: F401

from src.core.session_queue import EnqueueResult
from src.services.agent_runner.contracts import RunnerError

FAILURE_NOTICE = "抱歉，处理您的消息时遇到了问题，请稍后重试。"

_CHANNEL_USER_IDS = {"feishu": "ou_user_1", "dingtalk": "staff_1"}


def _make_message(channel, *, conversation_type="1"):
    """parse_message 返回的 UnifiedMessage 子集（与入口测试同构）。"""
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
        text="你好",
        message_id="msg-1",
        message_type="text",
        raw_message={"msgid": "msg-1"},
        content=content,
    )


def _make_event(channel, *, conversation_type="1"):
    """与真实回调解密后的 event_data 同构（钉钉 JSON / 飞书 v2.0 事件）。"""
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


def _mirror_enqueue():
    """session_queue.enqueue_and_process 契约镜像：真实调用 processor，
    异常转 status='error'（与 src/core/session_queue.py 的 error 返回一致）。"""

    async def enqueue_and_process(**kwargs):
        try:
            response = await kwargs["processor"](
                lambda: False, kwargs["user_input"]
            )
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


@pytest.fixture
def entry_env(bridge, monkeypatch):
    """叠加真实入口所需的边界假件：settings 指向 bridge、平台 adapter 假件、
    入口其余边界沿用 entries 测试的 patch 方式（SessionRecordManager 保持真实类）。"""
    from src.config.settings import settings
    from src.saas.api import channel_routes
    from src.services.session_record import SessionRecordManager

    # ChannelRunnerAgent(client=None) 用 settings.agent_runner 构造 client；
    # token 取 env，service id 必须匹配 bridge peer。
    monkeypatch.setenv("AGENT_RUNNER_WEB_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setattr(settings.agent_runner, "web_service_id", "bridge")
    monkeypatch.setattr(settings.agent_runner, "api_url", "http://runner.test")

    adapter = MagicMock()
    adapter.verbose_feedback = None
    adapter.parse_message = AsyncMock()
    adapter.send_message = AsyncMock(return_value=True)
    adapter.send_text = AsyncMock(return_value=True)
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
        MagicMock(return_value={"session_id": "session"}),
    )

    # SessionRecordManager 保持真实类：仅捕获实例以断言 skip_save / mark_error。
    records = []
    original_start = SessionRecordManager.start_record

    def recording_start(**kwargs):
        service = original_start(**kwargs)
        records.append(service)
        return service

    monkeypatch.setattr(SessionRecordManager, "start_record", recording_start)

    queue = SimpleNamespace(
        enqueue_and_process=_mirror_enqueue(),
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

    def install(channel, *, conversation_type="1"):
        """把 bridge 会话行切到对应渠道（actor=消息用户）并安装 fake 消息。"""
        bridge.store.sessions["session"].update(
            channel_type=channel,
            channel_user_id=_CHANNEL_USER_IDS[channel],
            channel_chat_id=None,
            subagent_id="",
        )
        adapter.parse_message = AsyncMock(
            return_value=_make_message(channel, conversation_type=conversation_type)
        )
        return _make_event(channel, conversation_type=conversation_type)

    def entry(channel):
        return {
            "feishu": channel_routes._process_tenant_feishu_background,
            "dingtalk": channel_routes._process_tenant_dingtalk_background,
        }[channel]

    return SimpleNamespace(
        adapter=adapter,
        queue=queue,
        recap=recap,
        batch_write=batch_write,
        records=records,
        install=install,
        entry=entry,
    )


# ---------- completed：真实入口 → 真实 agent → ASGI → bridge 完成 ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("conversation_type", ["1", "2"])
@pytest.mark.parametrize("channel", ["feishu", "dingtalk"])
async def test_real_entry_completes_through_actual_service(
    bridge, entry_env, channel, conversation_type
):
    event = entry_env.install(channel, conversation_type=conversation_type)

    await entry_env.entry(channel)("tenant", event, config_id="config")

    # 回复送达：非空 'answer' + 图片，reply 目标正确（钉钉单聊 userId/群聊 cid）
    entry_env.adapter.send_message.assert_awaited_once()
    response = entry_env.adapter.send_message.await_args.args[0]
    assert response.content["text"] == "answer"
    assert response.content["images"][0]["file_id"] == "image"
    if channel == "dingtalk":
        assert response.reply_to == (
            "cid-group" if conversation_type == "2" else "staff_1"
        )
        assert response.content["conversation_type"] == conversation_type
    else:
        assert response.reply_to == "ou_user_1"
    entry_env.adapter.send_text.assert_not_called()

    # ChannelRunnerAgent 构造参数经真实提交到达 bridge 仓库
    submitted = bridge.repository.submitted
    assert len(submitted) == 1
    request = submitted[0]
    assert request.source == channel and request.text == "你好"
    assert request.channel_user_id == _CHANNEL_USER_IDS[channel]
    assert request.channel_chat_id is None and request.profile_id == "main"
    assert request.request_data["channel_config_id"] == "config"
    # None 保持 None：持久化 intent 不允许退化为空串（授权比对要求）
    assert bridge.repository.rows["runner-1"]["input"]["channel_chat_id"] is None

    # batch 持久化：user + assistant + 脱敏工具消息 + 最终 assistant
    entry_env.batch_write.assert_called_once()
    batch = entry_env.batch_write.call_args.args[2]
    assert [message["role"] for message in batch] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert batch[0]["content"] == "你好"
    assert batch[3]["content"] == "answer"
    assert batch[3]["metadata"]["images"][0]["file_id"] == "image"
    tool_calls = batch[1]["metadata"]["tool_calls"]
    assert tool_calls[0]["function"]["name"] == "probe"
    # channel-result 集成：敏感参数被 mask，原始 'test-hidden' 不落库
    assert "test-hidden" not in str(batch[1])
    assert batch[2]["content"] == "tool result"

    # 回复送达后 recap 恰一次；入口记录 skip_save（无独立入口用量）
    entry_env.recap.assert_called_once()
    assert entry_env.recap.call_args.kwargs["session_id"] == "session"
    assert entry_env.recap.call_args.kwargs["tenant_id"] == "tenant"
    entry_env.queue.finish_processing.assert_called_once_with("session", "lease-1")
    assert entry_env.records[-1].skip_save is True


# ---------- failed：执行失败 → 非空失败提示，不写历史、不触发 recap ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["feishu", "dingtalk"])
async def test_real_entry_runner_failure_sends_notice(bridge, entry_env, channel):
    # 测试设施开关：bridge fake 执行直接以 failed 终态受理
    bridge.repository.status = "failed"
    event = entry_env.install(channel)

    await entry_env.entry(channel)("tenant", event, config_id="config")

    # 已受理后失败（不是未受理），仍必须走失败提示分支
    assert len(bridge.repository.submitted) == 1
    entry_env.adapter.send_message.assert_awaited_once()
    response = entry_env.adapter.send_message.await_args.args[0]
    assert response.content["text"] == FAILURE_NOTICE
    assert response.reply_to == _CHANNEL_USER_IDS[channel]

    # 失败提示只经 error 分流的 send_message 发一次；异常不得再外溢到入口
    # 兜底 send_text 造成双重通知
    entry_env.adapter.send_text.assert_not_called()
    entry_env.batch_write.assert_not_called()
    entry_env.recap.assert_not_called()
    entry_env.queue.finish_processing.assert_not_called()
    record = entry_env.records[-1]
    assert record.status == "failed" and record.error_message == "对话处理失败"
    assert record.skip_save is True


# ---------- foreign_actor：真实授权拒绝，bridge 不受理任何 runner ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["feishu", "dingtalk"])
async def test_real_entry_foreign_actor_rejected_before_acceptance(
    bridge, entry_env, channel
):
    event = entry_env.install(channel)
    # 会话行绑定到其他渠道用户：消息 actor 与持久会话不匹配
    bridge.store.sessions["session"]["channel_user_id"] = "someone-else"

    await entry_env.entry(channel)("tenant", event, config_id="config")

    # 真实 CHANNEL_ACTOR_FORBIDDEN 在任何 runner 受理之前拒绝
    assert not bridge.repository.submitted
    assert not bridge.repository.rows
    entry_env.adapter.send_message.assert_awaited_once()
    response = entry_env.adapter.send_message.await_args.args[0]
    assert response.content["text"] == FAILURE_NOTICE
    assert response.reply_to == _CHANNEL_USER_IDS[channel]

    # 同上：失败提示不与入口兜底 send_text 双发
    entry_env.adapter.send_text.assert_not_called()
    entry_env.batch_write.assert_not_called()
    entry_env.recap.assert_not_called()
    record = entry_env.records[-1]
    assert record.status == "failed" and record.error_message == "对话处理失败"


# ---------- history_unavailable：结果读取失败 → 失败提示，'answer' 不发送 ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["feishu", "dingtalk"])
async def test_real_entry_history_unavailable_sends_notice_without_answer(
    bridge, entry_env, monkeypatch, channel
):
    event = entry_env.install(channel)
    # 服务端语义：channel-result 读取在真实 manager 边界抛存储不可用
    # （不 patch ChannelRunnerAgent 内部）
    def unavailable(runner_id, credentials):
        raise RunnerError("RUNNER_STORAGE_UNAVAILABLE", 503)

    monkeypatch.setattr(bridge.manager, "channel_result", unavailable)

    await entry_env.entry(channel)("tenant", event, config_id="config")

    # runner 已受理并完成，但历史读取失败沿 error 分支：只发失败提示
    assert len(bridge.repository.submitted) == 1
    assert bridge.repository.rows["runner-1"]["status"] == "completed"
    entry_env.adapter.send_message.assert_awaited_once()
    response = entry_env.adapter.send_message.await_args.args[0]
    assert response.content["text"] == FAILURE_NOTICE
    assert response.content["text"] != "answer"

    # 失败提示只经 error 分流的 send_message 发一次；异常不得再外溢到入口
    # 兜底 send_text 造成双重通知
    entry_env.adapter.send_text.assert_not_called()
    entry_env.batch_write.assert_not_called()
    entry_env.recap.assert_not_called()
    entry_env.queue.finish_processing.assert_not_called()
    record = entry_env.records[-1]
    assert record.status == "failed" and record.error_message == "对话处理失败"
