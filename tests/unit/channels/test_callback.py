"""
渠道回调路由单元测试

测试企业微信回调处理：subscribe 欢迎消息、ToUserName 校验等。
"""

import pytest
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request

from src.models.message import UnifiedMessage, MessageType, ChannelType


# 测试 XML 模板
SUBSCRIBE_XML = """<xml>
<ToUserName>ww_test_corp</ToUserName>
<FromUserName>user123</FromUserName>
<CreateTime>1234567890</CreateTime>
<MsgType>event</MsgType>
<Event>subscribe</Event>
<EventKey></EventKey>
<AgentID>1000001</AgentID>
</xml>"""

TEXT_XML = """<xml>
<ToUserName>ww_test_corp</ToUserName>
<FromUserName>user123</FromUserName>
<CreateTime>1234567890</CreateTime>
<MsgType>text</MsgType>
<Content>你好</Content>
<MsgId>msg_001</MsgId>
<AgentID>1000001</AgentID>
</xml>"""

WRONG_TO_USER_XML = """<xml>
<ToUserName>ww_other_corp</ToUserName>
<FromUserName>user123</FromUserName>
<CreateTime>1234567890</CreateTime>
<MsgType>text</MsgType>
<Content>你好</Content>
<MsgId>msg_002</MsgId>
<AgentID>1000001</AgentID>
</xml>"""


@pytest.fixture
def mock_adapter():
    """创建 mock WeComAdapter"""
    adapter = MagicMock()
    adapter.corp_id = "ww_test_corp"
    adapter.agent_id = "1000001"
    adapter.crypto = None  # 无加密模式，简化测试
    adapter.send_text = AsyncMock(return_value=True)
    adapter.parse_message = AsyncMock()
    adapter._check_rate_limit = MagicMock(return_value=True)
    return adapter


@pytest.fixture
def mock_request():
    """创建 mock Request"""
    req = MagicMock(spec=Request)
    req.query_params = {}
    return req


class TestWeComCallback:
    """企业微信回调测试"""

    @pytest.mark.asyncio
    async def test_subscribe_event_sends_welcome(self, mock_adapter, mock_request):
        """subscribe 事件触发欢迎消息发送"""
        from src.channels.callback import wecom_callback_post
        from src.channels.callback import _wecom_dedup

        # 构造 subscribe 消息
        subscribe_msg = UnifiedMessage(
            message_id="msg_sub_001",
            channel_type=ChannelType.WECOM,
            user_id="user123",
            message_type=MessageType.EVENT,
            content={"event": "subscribe", "event_key": ""},
            raw_message={"xml": SUBSCRIBE_XML},
        )
        mock_adapter.parse_message.return_value = subscribe_msg

        mock_request.body = AsyncMock(return_value=SUBSCRIBE_XML.encode())

        with patch("src.channels.callback.channel_manager.get_adapter", return_value=mock_adapter):
            with patch.object(_wecom_dedup, "is_duplicate", AsyncMock(return_value=False)):
                with patch("src.channels.callback.settings") as mock_settings:
                    mock_settings.channels.wecom.welcome_message = "欢迎测试消息"
                    response = await wecom_callback_post(mock_request)

        assert response.body == b"success"
        mock_adapter.send_text.assert_called_once()
        # 验证发送的是欢迎消息
        call_args = mock_adapter.send_text.call_args
        assert "欢迎测试消息" in call_args[0][0]
        assert call_args[0][1] == "user123"

    @pytest.mark.asyncio
    async def test_subscribe_event_default_welcome(self, mock_adapter, mock_request):
        """subscribe 事件使用默认欢迎消息"""
        from src.channels.callback import wecom_callback_post
        from src.channels.callback import _wecom_dedup

        subscribe_msg = UnifiedMessage(
            message_id="msg_sub_002",
            channel_type=ChannelType.WECOM,
            user_id="user123",
            message_type=MessageType.EVENT,
            content={"event": "subscribe", "event_key": ""},
            raw_message={"xml": SUBSCRIBE_XML},
        )
        mock_adapter.parse_message.return_value = subscribe_msg
        mock_request.body = AsyncMock(return_value=SUBSCRIBE_XML.encode())

        with patch("src.channels.callback.channel_manager.get_adapter", return_value=mock_adapter):
            with patch.object(_wecom_dedup, "is_duplicate", AsyncMock(return_value=False)):
                with patch("src.channels.callback.settings") as mock_settings:
                    mock_settings.channels.wecom.welcome_message = None
                    response = await wecom_callback_post(mock_request)

        assert response.body == b"success"
        call_args = mock_adapter.send_text.call_args
        # 验证使用的是默认文案
        assert "智能助手" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_tousename_match(self, mock_adapter, mock_request):
        """正确的 ToUserName 正常通过"""
        from src.channels.callback import wecom_callback_post
        from src.channels.callback import _wecom_dedup

        text_msg = UnifiedMessage(
            message_id="msg_001",
            channel_type=ChannelType.WECOM,
            user_id="user123",
            message_type=MessageType.TEXT,
            content={"text": "你好"},
            raw_message={"xml": TEXT_XML},
        )
        mock_adapter.parse_message.return_value = text_msg
        mock_request.body = AsyncMock(return_value=TEXT_XML.encode())

        with patch("src.channels.callback.channel_manager.get_adapter", return_value=mock_adapter):
            with patch.object(_wecom_dedup, "is_duplicate", AsyncMock(return_value=False)):
                response = await wecom_callback_post(mock_request)

        assert response.body == b"success"
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_tousename_mismatch_returns_403(self, mock_adapter, mock_request):
        """错误的 ToUserName 返回 403"""
        from src.channels.callback import wecom_callback_post

        mock_request.body = AsyncMock(return_value=WRONG_TO_USER_XML.encode())

        with patch("src.channels.callback.channel_manager.get_adapter", return_value=mock_adapter):
            response = await wecom_callback_post(mock_request)

        assert response.status_code == 403
        assert b"Invalid receiver" in response.body

    @pytest.mark.asyncio
    async def test_tousename_empty_passes(self, mock_adapter, mock_request):
        """空的 ToUserName 允许通过（兼容旧消息格式）"""
        from src.channels.callback import wecom_callback_post
        from src.channels.callback import _wecom_dedup

        xml_no_touser = """<xml>
        <ToUserName></ToUserName>
        <FromUserName>user123</FromUserName>
        <CreateTime>1234567890</CreateTime>
        <MsgType>text</MsgType>
        <Content>你好</Content>
        <MsgId>msg_003</MsgId>
        </xml>"""

        text_msg = UnifiedMessage(
            message_id="msg_003",
            channel_type=ChannelType.WECOM,
            user_id="user123",
            message_type=MessageType.TEXT,
            content={"text": "你好"},
            raw_message={"xml": xml_no_touser},
        )
        mock_adapter.parse_message.return_value = text_msg
        mock_request.body = AsyncMock(return_value=xml_no_touser.encode())

        with patch("src.channels.callback.channel_manager.get_adapter", return_value=mock_adapter):
            with patch.object(_wecom_dedup, "is_duplicate", AsyncMock(return_value=False)):
                response = await wecom_callback_post(mock_request)

        assert response.body == b"success"
