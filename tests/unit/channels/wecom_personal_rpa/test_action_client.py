"""wecom_personal_rpa.action_client 单元测试

覆盖：
- 在线分支：registry.is_online=True 时走 registry.send，不写 outbox。
- 离线分支：registry.is_online=False 时走 db.enqueue_action。
- 账号不存在分支：db.get_account 返回 None 时记 error + 审计，不抛、不入队。

全 mock 外部依赖（db / registry），符合 tests/unit/ 纯逻辑原则。
"""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.wecom_personal_rpa.action_client import deliver_actions


def _account_row(client_id):
    return {
        "id": "acct_001",
        "client_id": client_id,
        "status": "online",
    }


@pytest.fixture
def patched_db():
    """mock db.get_account / db.enqueue_action / db.write_audit。"""
    with patch(
        "src.channels.wecom_personal_rpa.action_client.db"
    ) as mock_db:
        mock_db.get_account = MagicMock(return_value=_account_row("client_001"))
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
async def test_deliver_online_uses_registry_send_and_skips_outbox(patched_db, patched_registry):
    """意图：在线时直接推送，不写 outbox（离线降级路径不应触发）。"""
    patched_registry.is_online.return_value = True

    actions = [{"type": "send_text", "text": "你好"}]

    await deliver_actions(
        tenant_id="t1",
        account_id="acct_001",
        conversation_id="conv_1",
        request_id="req_1",
        session_id="wecom_personal_rpa:acct_001:conv_1",
        actions=actions,
    )

    # 在线推送：send 收到完整 envelope dict
    patched_registry.send.assert_awaited_once()
    _, kwargs = patched_registry.send.call_args
    # send 接受位置参数 (client_id, payload)；兼容 args/kwargs 两种写法
    payload = patched_registry.send.call_args.args[1] if patched_registry.send.call_args.args else kwargs["payload"]
    assert payload["request_id"] == "req_1"
    assert payload["account_id"] == "acct_001"
    assert payload["conversation_id"] == "conv_1"
    assert payload["session_id"] == "wecom_personal_rpa:acct_001:conv_1"
    assert payload["actions"] == actions

    # 在线时不应写 outbox
    patched_db.enqueue_action.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_online_send_failure_falls_back_to_outbox(patched_db, patched_registry):
    """意图：在线推送异常时降级入队，保证不丢投递。"""
    patched_registry.is_online.return_value = True
    patched_registry.send.side_effect = RuntimeError("ws broken")

    await deliver_actions(
        tenant_id="t1",
        account_id="acct_001",
        conversation_id="conv_1",
        request_id="req_1",
        session_id="sid_1",
        actions=[{"type": "send_text", "text": "x"}],
    )

    patched_db.enqueue_action.assert_called_once()


# ============================ 离线分支 ============================

@pytest.mark.asyncio
async def test_deliver_offline_enqueues_action(patched_db, patched_registry):
    """意图：离线客户端走 outbox 入队，dedup_key 形如 wecom_personal_rpa:{tenant}:{request}。"""
    patched_registry.is_online.return_value = False

    actions = [{"type": "send_file", "file_url": "https://x/y", "filename": "a.xlsx"}]

    await deliver_actions(
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


@pytest.mark.asyncio
async def test_deliver_offline_serializes_pydantic_actions(patched_db, patched_registry):
    """意图：actions 允许传入 pydantic 实例，内部应统一序列化为 dict 后入队。"""
    from src.channels.wecom_personal_rpa.schemas import SendTextAction

    patched_registry.is_online.return_value = False

    await deliver_actions(
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
    patched_db.get_account.return_value = None

    # 不应抛出
    await deliver_actions(
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
    audit = patched_db.write_audit.call_args
    assert audit.kwargs["category"] == "action_deliver"
    assert audit.kwargs["account_id"] == "missing"
    payload = json.loads(audit.kwargs["payload_json"])
    assert payload["error"] == "account_not_found"


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
