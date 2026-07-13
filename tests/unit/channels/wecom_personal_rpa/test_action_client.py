"""wecom_personal_rpa.action_client 单元测试

覆盖：
- 在线分支：先写 outbox，再通过 registry.send 兼容直推完整信封。
- 离线分支：registry.is_online=False 时走 db.enqueue_action。
- 账号不存在分支：db.get_account_for_tenant 返回 None 时记 error + 审计，不抛、不入队。

全 mock 外部依赖（db / registry），符合 tests/unit/ 纯逻辑原则。
"""
import json
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.wecom_personal_rpa.action_client import deliver_actions
from src.channels.wecom_personal_rpa.archive import audit as archive_audit

os.environ.setdefault("RPA_SECRET_KEY", "test-rpa-audit-key-32-bytes-minimum")


def _account_row(client_id):
    return {
        "id": "acct_001",
        "client_id": client_id,
        "status": "online",
        "tenant_id": "t1",
        "wecom_user_id": "self_001",
        "wecom_user_aliases": [],
    }


@pytest.fixture
def patched_db():
    """mock db.get_account_for_tenant / db.enqueue_action / db.write_audit。"""
    with patch(
        "src.channels.wecom_personal_rpa.action_client.db"
    ) as mock_db:
        mock_db.get_account_for_tenant = MagicMock(return_value=_account_row("client_001"))
        mock_db.enqueue_action = MagicMock(return_value={"id": "rpa_act_1"})
        mock_db.write_audit = MagicMock(return_value="audit_1")
        yield mock_db


@pytest.fixture
def patched_registry():
    """mock connection.client_connection_registry。"""
    with patch(
        "src.channels.wecom_personal_rpa.action_client.client_connection_registry"
    ) as mock_reg:
        mock_reg.is_online = MagicMock(return_value=False)
        mock_reg.send = AsyncMock(return_value=True)
        yield mock_reg


# ============================ 在线分支 ============================

@pytest.mark.asyncio
async def test_deliver_online_enqueues_then_sends_compat_envelope(patched_db, patched_registry):
    """在线时也先入权威 outbox，再兼容直推完整动作信封。"""
    patched_registry.is_online.return_value = True

    actions = [{"type": "send_text", "text": "你好"}]

    delivered = await deliver_actions(
        tenant_id="t1",
        account_id="acct_001",
        conversation_id="conv_1",
        request_id="req_1",
        session_id="wecom_personal_rpa:acct_001:conv_1",
        actions=actions,
        reply_context={
            "sender_display_name": "张三",
            "sender_stable_id": "wm_1",
            "inbound_text": "你好",
            "agent_reply_text": "您好",
        },
    )
    assert delivered is True

    patched_db.get_account_for_tenant.assert_called_once_with("t1", "acct_001")
    patched_db.enqueue_action.assert_called_once()
    # 入库成功后兼容旧客户端直推完整信封
    patched_registry.send.assert_awaited_once()
    _, kwargs = patched_registry.send.call_args
    # send 接受位置参数 (client_id, payload)；兼容 args/kwargs 两种写法
    payload = patched_registry.send.call_args.args[1] if patched_registry.send.call_args.args else kwargs["payload"]
    assert payload["type"] == "actions"
    assert payload["request_id"] == "req_1"
    assert payload["actions"] == actions
    assert payload["reply_context"]["inbound_text"] == "你好"


@pytest.mark.asyncio
async def test_deliver_online_compat_push_failure_remains_queued(patched_db, patched_registry):
    """兼容直推失败不影响已经完成的可靠入队。"""
    patched_registry.is_online.return_value = True
    patched_registry.send.side_effect = RuntimeError("ws broken")

    delivered = await deliver_actions(
        tenant_id="t1",
        account_id="acct_001",
        conversation_id="conv_1",
        request_id="req_1",
        session_id="sid_1",
        actions=[{"type": "send_text", "text": "x"}],
    )

    patched_db.enqueue_action.assert_called_once()
    assert delivered is True


# ============================ 离线分支 ============================

@pytest.mark.asyncio
async def test_deliver_offline_enqueues_action(patched_db, patched_registry):
    """意图：离线客户端走 outbox 入队，dedup_key 形如 wecom_personal_rpa:{tenant}:{request}。"""
    patched_registry.is_online.return_value = False

    actions = [{"type": "send_file", "file_url": "https://x/y", "filename": "a.xlsx"}]

    delivered = await deliver_actions(
        tenant_id="t1",
        account_id="acct_001",
        conversation_id="conv_1",
        request_id="req_1",
        session_id="sid_1",
        actions=actions,
    )

    # 不应走在线推送
    patched_registry.send.assert_not_called()

    # 应入队
    patched_db.enqueue_action.assert_called_once()
    call = patched_db.enqueue_action.call_args
    assert call.kwargs["tenant_id"] == "t1"
    assert call.kwargs["account_id"] == "acct_001"
    assert call.kwargs["conversation_id"] == "conv_1"
    assert call.kwargs["request_id"] == "req_1"
    assert call.kwargs["session_id"] == "sid_1"
    # actions_json 必须是合法 JSON 且能还原原内容
    parsed = json.loads(call.kwargs["actions_json"])
    assert parsed == actions
    assert call.kwargs["dedup_key"] == "wecom_personal_rpa:t1:req_1"
    assert call.kwargs["reply_digests"] == [
        archive_audit.digest_reply_component("send_file", "a.xlsx")
    ]


@pytest.mark.asyncio
async def test_reply_digests_follow_split_actions_without_agent_reply_text(
    patched_db, patched_registry
):
    """逐 action HMAC 支持拆分文本和多附件，且不依赖 agent_reply_text。"""
    actions = [
        {"type": "send_text", "text": "第一段"},
        {"type": "send_text", "text": "第二段"},
        {"type": "send_image", "file_url": "https://x/1", "filename": "a.png"},
        {"type": "send_file", "file_url": "https://x/2", "filename": "b.pdf"},
    ]
    assert await deliver_actions(
        tenant_id="t1", account_id="acct_001", conversation_id="peer_1",
        request_id="req_parts", session_id="sid_parts", actions=actions,
        reply_context={"sender_stable_id": "peer_1"},
    )
    assert patched_db.enqueue_action.call_args.kwargs["reply_digests"] == [
        archive_audit.digest_reply_component("send_text", "第一段"),
        archive_audit.digest_reply_component("send_text", "第二段"),
        archive_audit.digest_reply_component("send_image", "a.png"),
        archive_audit.digest_reply_component("send_file", "b.pdf"),
    ]


@pytest.mark.asyncio
async def test_deliver_offline_serializes_pydantic_actions(patched_db, patched_registry):
    """意图：actions 允许传入 pydantic 实例，内部应统一序列化为 dict 后入队。"""
    from src.channels.wecom_personal_rpa.schemas import SendTextAction

    patched_registry.is_online.return_value = False

    delivered = await deliver_actions(
        tenant_id="t1",
        account_id="acct_001",
        conversation_id="conv_1",
        request_id="req_1",
        session_id="sid_1",
        actions=[SendTextAction(text="hello")],
    )

    call = patched_db.enqueue_action.call_args
    parsed = json.loads(call.kwargs["actions_json"])
    assert parsed == [{"type": "send_text", "text": "hello"}]


# ============================ 账号不存在分支 ============================

@pytest.mark.asyncio
async def test_deliver_account_not_found_writes_audit_and_returns(patched_db, patched_registry):
    """意图：账号缺失时记 error + 审计，既不推送也不入队，且不抛异常。"""
    patched_db.get_account_for_tenant.return_value = None

    # 不应抛出
    delivered = await deliver_actions(
        tenant_id="t1",
        account_id="missing",
        conversation_id="conv_1",
        request_id="req_1",
        session_id="sid_1",
        actions=[{"type": "noop"}],
    )

    patched_registry.send.assert_not_called()
    patched_db.enqueue_action.assert_not_called()
    patched_db.write_audit.assert_called_once()
    assert delivered is False
    audit = patched_db.write_audit.call_args
    assert audit.kwargs["category"] == "action_deliver"
    assert audit.kwargs["account_id"] == "missing"
    payload = json.loads(audit.kwargs["payload_json"])
    assert payload["error"] == "account_not_found"


@pytest.mark.asyncio
async def test_deliver_rejects_self_target_before_outbox(patched_db, patched_registry):
    """目标命中账号自身 userid 时必须在入 outbox 前失败关闭。"""
    delivered = await deliver_actions(
        tenant_id="t1",
        account_id="acct_001",
        conversation_id="self_001",
        request_id="req_self",
        session_id="sid_self",
        actions=[{"type": "send_text", "text": "禁止发送"}],
        reply_context={"sender_stable_id": "self_001"},
    )

    assert delivered is False
    patched_db.enqueue_action.assert_not_called()
    assert patched_db.write_audit.call_args.kwargs["category"] == "self_echo_escaped"


@pytest.mark.asyncio
async def test_deliver_audit_failure_still_rejects_self_target(patched_db, patched_registry):
    patched_db.write_audit.side_effect = RuntimeError("audit unavailable")
    delivered = await deliver_actions(
        tenant_id="t1",
        account_id="acct_001",
        conversation_id="self_001",
        request_id="req_self_audit_down",
        session_id="sid_self",
        actions=[{"type": "send_text", "text": "禁止发送"}],
        reply_context={"sender_stable_id": "self_001"},
    )

    assert delivered is False
    patched_db.enqueue_action.assert_not_called()


# ============================ dedup_key / 空入队失败不抛 ============================

@pytest.mark.asyncio
async def test_deliver_enqueue_failure_does_not_raise(patched_db, patched_registry):
    """意图：outbox 入队异常被吞掉，不影响调用方（send_message 已判定逻辑成功）。"""
    patched_registry.is_online.return_value = False
    patched_db.enqueue_action.side_effect = RuntimeError("db down")

    # 不抛
    await deliver_actions(
        tenant_id="t1",
        account_id="acct_001",
        conversation_id="conv_1",
        request_id="req_1",
        session_id="sid_1",
        actions=[{"type": "noop"}],
    )
