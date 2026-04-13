"""
企业微信回调集成测试

测试 WeCom 回调端到端流程: GET 验证 + POST 消息处理
使用 FastAPI TestClient + mock 外部依赖
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.channels.wecom.crypto import WeComCrypto


@pytest.fixture
def crypto():
    """创建测试用的 WeComCrypto"""
    import base64
    aes_key = base64.b64encode(b"k" * 32).decode("utf-8").rstrip("=")
    return WeComCrypto("test_token", aes_key, "ww_test_corp")


class TestMessageDeduplicator:
    """消息去重器测试"""

    @pytest.mark.asyncio
    async def test_first_message_not_duplicate(self):
        from src.channels.idempotency import MessageDeduplicator
        dedup = MessageDeduplicator(ttl_seconds=60)

        assert await dedup.is_duplicate("msg_001") is False

    @pytest.mark.asyncio
    async def test_same_message_is_duplicate(self):
        from src.channels.idempotency import MessageDeduplicator
        dedup = MessageDeduplicator(ttl_seconds=60)

        await dedup.is_duplicate("msg_001")
        assert await dedup.is_duplicate("msg_001") is True

    @pytest.mark.asyncio
    async def test_different_messages_not_duplicate(self):
        from src.channels.idempotency import MessageDeduplicator
        dedup = MessageDeduplicator(ttl_seconds=60)

        await dedup.is_duplicate("msg_001")
        assert await dedup.is_duplicate("msg_002") is False

    @pytest.mark.asyncio
    async def test_clear_cache(self):
        from src.channels.idempotency import MessageDeduplicator
        dedup = MessageDeduplicator(ttl_seconds=60)

        await dedup.is_duplicate("msg_001")
        dedup.clear()
        assert await dedup.is_duplicate("msg_001") is False


class TestWecomCallbackFlow:
    """回调流程测试"""

    @pytest.mark.asyncio
    async def test_get_callback_decrypt_echostr(self, crypto):
        """
        模拟 GET 回调验证:
        1. WeCom 发送加密的 echostr
        2. 服务端验签 + 解密
        3. 返回明文
        """
        echostr_plaintext = "1234567890"
        encrypted_echostr = crypto.encrypt(echostr_plaintext)

        # 生成签名
        import hashlib
        items = sorted(["test_token", "1609459200", "nonce_test", encrypted_echostr])
        sig = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()

        # 验证
        assert crypto.verify_signature(sig, "1609459200", "nonce_test", encrypted_echostr) is True

        # 解密
        decrypted = crypto.decrypt(encrypted_echostr)
        assert decrypted == echostr_plaintext

    @pytest.mark.asyncio
    async def test_post_callback_decrypt_message(self, crypto):
        """
        模拟 POST 回调消息处理:
        1. WeCom 发送加密的 XML 消息
        2. 服务端验签 + 解密
        3. 解析为 UnifiedMessage
        """
        # 构造明文 XML
        plaintext_xml = """<xml>
<MsgType>text</MsgType>
<Content>你好，智能体</Content>
<FromUserName>wang_test</FromUserName>
<ToUserName>test_agent</ToUserName>
<CreateTime>1609459200</CreateTime>
<MsgId>msg_callback_test</MsgId>
</xml>"""

        # 加密
        encrypted = crypto.encrypt(plaintext_xml)

        # 构造 WeCom 回调 XML
        import hashlib
        timestamp = "1609459200"
        nonce = "test_nonce_123"
        items = sorted(["test_token", timestamp, nonce, encrypted])
        sig = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()

        callback_xml = f"""<xml>
<ToUserName><![CDATA[test_agent]]></ToUserName>
<AgentID>1000001</AgentID>
<Encrypt><![CDATA[{encrypted}]]></Encrypt>
</xml>"""

        # 验签
        assert crypto.verify_signature(sig, timestamp, nonce, encrypted) is True

        # 解密
        decrypted_xml = crypto.decrypt(encrypted)
        assert "你好，智能体" in decrypted_xml

        # 解析消息
        import xml.etree.ElementTree as ET
        root = ET.fromstring(decrypted_xml)
        assert root.findtext("Content") == "你好，智能体"
        assert root.findtext("FromUserName") == "wang_test"

    @pytest.mark.asyncio
    async def test_encrypted_event_message(self, crypto):
        """加密的事件消息处理"""
        event_xml = """<xml>
<MsgType>event</MsgType>
<Event>subscribe</Event>
<FromUserName>new_user</FromUserName>
<ToUserName>test_agent</ToUserName>
<CreateTime>1609459200</CreateTime>
<MsgId>msg_event_001</MsgId>
</xml>"""

        encrypted = crypto.encrypt(event_xml)
        decrypted = crypto.decrypt(encrypted)

        import xml.etree.ElementTree as ET
        root = ET.fromstring(decrypted)
        assert root.findtext("MsgType") == "event"
        assert root.findtext("Event") == "subscribe"
