"""wecom_personal_rpa.adapter 单元测试

覆盖：
- channel_type 返回 wecom_personal_rpa。
- parse_message 委托 message.parse_rpa_message（mock）。
- send_message：未 set_reply_context 返回 False。
- send_message：text 与 downloadable_files 转成 send_text / send_file / send_image 并调用 deliver_actions（mock）。
- 文本超 2000 字分段、空内容降级 noop。

全 mock 外部依赖（message.parse_rpa_message / action_client.deliver_actions / build_public_url），
符合 tests/unit/ 纯逻辑原则。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.message import Attachment, DownloadableFileInfo, UnifiedResponse

from src.channels.wecom_personal_rpa.adapter import WeComPersonalRpaAdapter


@pytest.fixture
def adapter():
    return WeComPersonalRpaAdapter(client_id="client_001", tenant_id="t1")


@pytest.fixture
def patched_deliver():
    """mock deliver_actions，断言 adapter 调用了它而非真实投递。"""
    with patch(
        "src.channels.wecom_personal_rpa.adapter.deliver_actions",
        new_callable=AsyncMock,
    ) as mock_deliver:
        mock_deliver.return_value = True
        yield mock_deliver


# ============================ channel_type ============================

def test_channel_type_returns_wecom_personal_rpa(adapter):
    """意图：渠道类型字符串与协议 channel_type 字面量一致，供路由分发使用。"""
    assert adapter.channel_type == "wecom_personal_rpa"


# ============================ parse_message 委托 ============================

@pytest.mark.asyncio
async def test_parse_message_delegates_to_parse_rpa_message(adapter):
    """意图：parse_message 仅做转发，真正的解析逻辑在 message 模块，adapter 不重复实现。"""
    raw = {"event_id": "e1", "payload": {"conversation_id": "c1"}}
    fake = MagicMock(name="UnifiedMessage")
    with patch(
        "src.channels.wecom_personal_rpa.adapter.parse_rpa_message",
        return_value=fake,
    ) as mock_parse:
        result = await adapter.parse_message(raw)

    mock_parse.assert_called_once_with(raw)
    assert result is fake


# ============================ send_message：无上下文 ============================

@pytest.mark.asyncio
async def test_send_message_without_reply_context_returns_false(adapter, patched_deliver):
    """意图：Wire 阶段路由未调用 set_reply_context 时，send_message 必须安全失败（不调用 deliver）。"""
    resp = UnifiedResponse.from_text("hi", reply_to="msg_1")
    ok = await adapter.send_message(resp)

    assert ok is False
    patched_deliver.assert_not_called()


# ============================ send_message：text + files ============================

@pytest.mark.asyncio
async def test_send_message_converts_text_and_files_to_actions(patched_deliver):
    """意图：UnifiedResponse 的 text 转成 send_text，downloadable_files 按 mime 转 send_image / send_file。"""
    adapter = WeComPersonalRpaAdapter(client_id="client_001", tenant_id="t1")
    adapter.set_reply_context(
        account_id="acct_001",
        conversation_id="conv_1",
        session_id="wecom_personal_rpa:acct_001:conv_1",
        request_id="req_1",
        sender_display_name="张三",
        sender_stable_id="wm_1",
        inbound_text="用户问题",
    )

    resp = UnifiedResponse(
        message_id="m1",
        reply_to="in_1",
        content={"text": "你好"},
        downloadable_files=[
            DownloadableFileInfo(
                file_id="f1",
                file_name="pic.png",
                file_size=10,
                download_url="/files/p1",
                mime_type="image/png",
            ),
            DownloadableFileInfo(
                file_id="f2",
                file_name="doc.xlsx",
                file_size=20,
                download_url="/files/p2",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        ],
    )

    with patch(
        "src.channels.wecom_personal_rpa.adapter.build_public_url",
        side_effect=lambda u: f"https://pub{u}",
    ):
        ok = await adapter.send_message(resp)

    assert ok is True
    patched_deliver.assert_awaited_once()
    kwargs = patched_deliver.call_args.kwargs
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["account_id"] == "acct_001"
    assert kwargs["conversation_id"] == "conv_1"
    assert kwargs["request_id"] == "req_1"
    assert kwargs["session_id"] == "wecom_personal_rpa:acct_001:conv_1"
    assert kwargs["reply_context"] == {
        "sender_display_name": "张三",
        "sender_stable_id": "wm_1",
        "conversation_search_name": None,
        "inbound_text": "用户问题",
        "agent_reply_text": "你好",
    }

    # 校验 action 序列：send_text + send_image + send_file
    actions = kwargs["actions"]
    types = [a.type for a in actions]
    assert types == ["send_text", "send_image", "send_file"]
    assert actions[0].text == "你好"
    assert actions[1].file_url == "https://pub/files/p1"
    assert actions[1].filename == "pic.png"
    assert actions[2].file_url == "https://pub/files/p2"
    assert actions[2].filename == "doc.xlsx"


@pytest.mark.asyncio
async def test_send_message_converts_agent_attachments_to_actions(adapter, patched_deliver):
    """Agent 直接返回 attachments 时也应下发图片/文件动作。"""
    adapter.set_reply_context("acct", "conv", "sid", "req")
    resp = UnifiedResponse(
        message_id="m", reply_to="in", attachments=[
            Attachment(type="image", url="/files/photo", name="photo.jpg", mime_type="image/jpeg"),
            Attachment(type="file", url="/files/report", name="report.pdf", mime_type="application/pdf"),
        ],
    )
    with patch("src.channels.wecom_personal_rpa.adapter.build_public_url", side_effect=lambda u: "https://api" + u):
        assert await adapter.send_message(resp) is True
    actions = patched_deliver.call_args.kwargs["actions"]
    assert [a.type for a in actions] == ["send_image", "send_file"]
    assert actions[0].filename == "photo.jpg"
    assert actions[1].filename == "report.pdf"


# ============================ send_message：长文本分段 ============================

@pytest.mark.asyncio
async def test_send_message_segments_long_text_into_multiple_send_text(adapter, patched_deliver):
    """意图：企微单条文本上限 2000 字，超长文本必须拆成多个 send_text action 顺序发送。"""
    adapter.set_reply_context(
        account_id="acct_001",
        conversation_id="conv_1",
        session_id="sid_1",
        request_id="req_1",
    )

    long_text = "A" * 4500  # 应拆成 3 段（2000 + 2000 + 500）
    resp = UnifiedResponse.from_text(long_text, reply_to="in_1")

    ok = await adapter.send_message(resp)

    assert ok is True
    actions = patched_deliver.call_args.kwargs["actions"]
    send_texts = [a for a in actions if a.type == "send_text"]
    assert len(send_texts) == 3
    assert len(send_texts[0].text) == 2000
    assert len(send_texts[1].text) == 2000
    assert len(send_texts[2].text) == 500
    assert "".join(a.text for a in send_texts) == long_text


# ============================ send_message：空内容降级 noop ============================

@pytest.mark.asyncio
async def test_send_message_empty_content_uses_noop(adapter, patched_deliver):
    """意图：空文本且无附件时，仍要保证信封非空（noop 占位），避免客户端收到空 actions。"""
    adapter.set_reply_context(
        account_id="acct_001",
        conversation_id="conv_1",
        session_id="sid_1",
        request_id="req_1",
    )

    resp = UnifiedResponse(message_id="m1", reply_to="in_1", content={})

    ok = await adapter.send_message(resp)

    assert ok is True
    actions = patched_deliver.call_args.kwargs["actions"]
    assert len(actions) == 1
    assert actions[0].type == "noop"


# ============================ get_user_info / verify_signature ============================

@pytest.mark.asyncio
async def test_get_user_info_returns_user_id_passthrough(adapter):
    """意图：RPA 渠道无统一用户信息接口，仅保证 user_id 回传（非空、不报错）。"""
    info = await adapter.get_user_info("u123")
    assert info == {"user_id": "u123"}


@pytest.mark.asyncio
async def test_verify_signature_without_get_secret_returns_true(adapter):
    """意图：未配置 get_secret 时（如内部回调已由上层鉴权），适配器放行。"""
    assert await adapter.verify_signature("sig", "ts", "nonce", "body") is True


@pytest.mark.asyncio
async def test_verify_signature_delegates_to_auth_when_get_secret_configured():
    """意图：提供 get_secret 时，签名校验委托 auth.verify_request，结果由其决定。"""
    adapter = WeComPersonalRpaAdapter(
        client_id="client_001",
        get_secret=lambda cid: b"secret-bytes",
    )

    fake_result = MagicMock(ok=False)
    with patch(
        "src.channels.wecom_personal_rpa.auth.verify_request",
        return_value=fake_result,
    ) as mock_verify:
        ok = await adapter.verify_signature("sig", "ts", "nonce", "body")

    assert ok is False
    mock_verify.assert_called_once()
    headers_arg = mock_verify.call_args.kwargs["headers"]
    assert headers_arg["X-Client-Id"] == "client_001"
