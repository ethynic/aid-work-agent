"""
企业微信适配器单元测试

测试消息解析、发送逻辑（mock HTTP 调用）。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from src.channels.wecom.adapter import WeComAdapter
from src.models.message import MessageType, UnifiedResponse


# 测试 XML 消息模板
TEXT_XML = """<xml>
<MsgType>text</MsgType>
<Content>你好</Content>
<FromUserName>user123</FromUserName>
<ToUserName>agent456</ToUserName>
<CreateTime>1234567890</CreateTime>
<MsgId>msg_001</MsgId>
</xml>"""

IMAGE_XML = """<xml>
<MsgType>image</MsgType>
<PicUrl>https://example.com/pic.jpg</PicUrl>
<MediaId>media_img_001</MediaId>
<FromUserName>user123</FromUserName>
<ToUserName>agent456</ToUserName>
<CreateTime>1234567890</CreateTime>
<MsgId>msg_002</MsgId>
</xml>"""

FILE_XML = """<xml>
<MsgType>file</MsgType>
<MediaId>media_file_001</MediaId>
<FileName>test.pdf</FileName>
<FromUserName>user123</FromUserName>
<ToUserName>agent456</ToUserName>
<CreateTime>1234567890</CreateTime>
<MsgId>msg_003</MsgId>
</xml>"""

EVENT_XML = """<xml>
<MsgType>event</MsgType>
<Event>subscribe</Event>
<EventKey></EventKey>
<FromUserName>user123</FromUserName>
<ToUserName>agent456</ToUserName>
<CreateTime>1234567890</CreateTime>
<MsgId>msg_004</MsgId>
</xml>"""

VOICE_XML = """<xml>
<MsgType>voice</MsgType>
<MediaId>media_voice_001</MediaId>
<Recognition>语音转文字内容</Recognition>
<Format>amr</Format>
<FromUserName>user123</FromUserName>
<ToUserName>agent456</ToUserName>
<CreateTime>1234567890</CreateTime>
<MsgId>msg_005</MsgId>
</xml>"""


@pytest.fixture
def adapter():
    """创建 WeComAdapter 实例（不依赖真实配置）"""
    with patch("src.channels.wecom.adapter.settings") as mock_settings:
        mock_channels = MagicMock()
        mock_channels.wecom.corp_id = "ww_test"
        mock_channels.wecom.agent_id = "1000001"
        mock_channels.wecom.secret = "test_secret"
        mock_channels.wecom.token = ""
        mock_channels.wecom.encoding_aes_key = ""
        mock_channels.wecom.message.default_type = "markdown"
        mock_channels.wecom.message.max_bytes = 2048
        mock_channels.wecom.message.split_on_paragraph = True
        mock_channels.wecom.media.upload_dir = "/tmp/wecom_test"
        mock_channels.wecom.retry.max_attempts = 3
        mock_channels.wecom.retry.backoff_base = 1.0
        mock_settings.channels = mock_channels

        adapter = WeComAdapter()
        # Mock HTTP client
        adapter._http_client = AsyncMock()
        return adapter


class TestChannelType:
    def test_channel_type(self, adapter):
        assert adapter.channel_type == "wecom"


class TestParseMessage:
    """消息解析测试"""

    @pytest.mark.asyncio
    async def test_parse_text(self, adapter):
        msg = await adapter.parse_message({"body": TEXT_XML})
        assert msg.message_type == MessageType.TEXT
        assert msg.text == "你好"
        assert msg.user_id == "user123"
        assert msg.message_id == "msg_001"

    @pytest.mark.asyncio
    async def test_parse_image(self, adapter):
        msg = await adapter.parse_message({"body": IMAGE_XML})
        assert msg.message_type == MessageType.IMAGE
        assert msg.content["media_id"] == "media_img_001"
        assert msg.content["pic_url"] == "https://example.com/pic.jpg"

    @pytest.mark.asyncio
    async def test_parse_file(self, adapter):
        msg = await adapter.parse_message({"body": FILE_XML})
        assert msg.message_type == MessageType.FILE
        assert msg.content["file_name"] == "test.pdf"
        assert msg.content["media_id"] == "media_file_001"

    @pytest.mark.asyncio
    async def test_parse_event(self, adapter):
        msg = await adapter.parse_message({"body": EVENT_XML})
        assert msg.message_type == MessageType.EVENT
        assert msg.content["event"] == "subscribe"

    @pytest.mark.asyncio
    async def test_parse_voice_with_recognition(self, adapter):
        msg = await adapter.parse_message({"body": VOICE_XML})
        assert msg.text == "语音转文字内容"
        assert msg.content["media_id"] == "media_voice_001"

    @pytest.mark.asyncio
    async def test_parse_preserves_raw_xml(self, adapter):
        msg = await adapter.parse_message({"body": TEXT_XML})
        assert msg.raw_message["xml"] == TEXT_XML


class TestSendMessage:
    """消息发送测试"""

    @pytest.mark.asyncio
    async def test_send_message_calls_send_long_message(self, adapter):
        """send_message 应委托给 send_long_message"""
        adapter.send_long_message = AsyncMock(return_value=True)
        msg = UnifiedResponse.from_text("测试", "user123", "msg_1")

        result = await adapter.send_message(msg)
        assert result is True
        adapter.send_long_message.assert_called_once_with("测试", "user123")

    @pytest.mark.asyncio
    async def test_send_message_empty_text(self, adapter):
        """空文本应返回 True"""
        msg = UnifiedResponse.from_text("", "user123", "msg_1")
        result = await adapter.send_message(msg)
        assert result is True


class TestClose:
    """关闭测试"""

    @pytest.mark.asyncio
    async def test_close_with_client(self, adapter):
        adapter._http_client = AsyncMock()
        adapter._http_client.is_closed = False
        await adapter.close()
        adapter._http_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_without_client(self, adapter):
        adapter._http_client = None
        await adapter.close()  # 不应抛异常
