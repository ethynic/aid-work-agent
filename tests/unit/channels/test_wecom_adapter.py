"""
企业微信适配器单元测试

测试消息解析、发送逻辑（mock HTTP 调用）。
"""

import pytest
from unittest.mock import AsyncMock

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
    """创建 WeComAdapter 实例（显式传参，不依赖全局 settings）"""
    adapter = WeComAdapter(
        corp_id="ww_test",
        agent_id="1000001",
        secret="test_secret",
        token="",
        encoding_aes_key="",
        default_type="markdown",
        max_bytes=2048,
        split_on_paragraph=True,
        media_upload_dir="/tmp/wecom_test",
        max_attempts=3,
        backoff_base=1.0,
        rate_limit_enabled=True,
        rate_limit_max=5,
    )
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


# 测试 XML 消息模板（带 @前缀）
TEXT_XML_WITH_AT = """<xml>
<MsgType>text</MsgType>
<Content>@智能助手 你好</Content>
<FromUserName>user123</FromUserName>
<ToUserName>agent456</ToUserName>
<CreateTime>1234567890</CreateTime>
<MsgId>msg_at_001</MsgId>
</xml>"""

TEXT_XML_WITH_AT_MULTI_SPACE = """<xml>
<MsgType>text</MsgType>
<Content>@智能助手   你好</Content>
<FromUserName>user123</FromUserName>
<ToUserName>agent456</ToUserName>
<CreateTime>1234567890</CreateTime>
<MsgId>msg_at_002</MsgId>
</xml>"""


class TestAtPrefixCleaning:
    """群聊 @前缀清洗测试"""

    @pytest.mark.asyncio
    async def test_clean_at_prefix_simple(self, adapter):
        """@应用 消息 -> 消息"""
        msg = await adapter.parse_message({"body": TEXT_XML_WITH_AT})
        assert msg.text == "你好"
        assert msg.message_type == MessageType.TEXT

    @pytest.mark.asyncio
    async def test_clean_at_prefix_multi_space(self, adapter):
        """@应用   消息（多空格）-> 消息"""
        msg = await adapter.parse_message({"body": TEXT_XML_WITH_AT_MULTI_SPACE})
        assert msg.text == "你好"

    @pytest.mark.asyncio
    async def test_no_clean_for_normal_message(self, adapter):
        """普通消息不变"""
        msg = await adapter.parse_message({"body": TEXT_XML})
        assert msg.text == "你好"

    @pytest.mark.asyncio
    async def test_message_without_at_prefix(self, adapter):
        """不以 @ 开头的消息保持不变"""
        xml = """<xml>
        <MsgType>text</MsgType>
        <Content>@开头但没有空格</Content>
        <FromUserName>user123</FromUserName>
        <ToUserName>agent456</ToUserName>
        <CreateTime>1234567890</CreateTime>
        <MsgId>msg_006</MsgId>
        </xml>"""
        msg = await adapter.parse_message({"body": xml})
        # @开头但没有空白字符分隔，不应清洗
        assert msg.text == "@开头但没有空格"


class TestRateLimit:
    """速率限制测试"""

    def test_rate_limit_disabled(self, adapter):
        """限流禁用时始终通过"""
        adapter._rate_limit_enabled = False
        assert adapter._check_rate_limit("user1") is True

    def test_rate_limit_under_threshold(self, adapter):
        """未达到阈值时通过"""
        adapter._rate_limit_max = 5
        adapter._rate_limiter.clear()
        # 发送 4 次，未超限
        for _ in range(4):
            assert adapter._check_rate_limit("user1") is True

    def test_rate_limit_at_threshold(self, adapter):
        """达到阈值时拒绝"""
        adapter._rate_limit_max = 3
        adapter._rate_limiter.clear()
        # 发送 3 次，第 4 次应被拒绝
        for _ in range(3):
            assert adapter._check_rate_limit("user1") is True
        assert adapter._check_rate_limit("user1") is False

    def test_rate_limit_per_user(self, adapter):
        """限流按用户独立计算"""
        adapter._rate_limit_max = 2
        adapter._rate_limiter.clear()
        # user1 达到限流
        adapter._check_rate_limit("user1")
        adapter._check_rate_limit("user1")
        assert adapter._check_rate_limit("user1") is False
        # user2 不受影响
        assert adapter._check_rate_limit("user2") is True

    def test_rate_limit_window_slide(self, adapter):
        """滑动窗口：旧记录过期后恢复通过"""
        from collections import deque
        adapter._rate_limit_max = 2
        adapter._rate_limiter.clear()
        import time
        # 模拟两条 70 秒前的记录
        old_time = time.time() - 70
        adapter._rate_limiter["user1"] = deque([old_time, old_time])
        # 旧记录应在检查时自动清理，新请求通过
        assert adapter._check_rate_limit("user1") is True
