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


class TestMultiWeComAppCallback:
    """
    多企业微信应用回调路由测试

    验证不同 config_id 对应不同的渠道配置和数字员工
    """

    def test_create_from_tenant_config_by_config_id_returns_subagent(self):
        """
        ChannelFactory 按 config_id 查询时返回正确的 subagent_type
        """
        from unittest.mock import patch

        from src.saas.services.channel_factory import ChannelFactory

        mock_cfg = {
            "config_id": "chan_app1",
            "tenant_id": "tenant_test",
            "channel_type": "wecom",
            "config": {},
            "subagent_type": "travel-consultant",
            "verified": 1,
        }

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.return_value = mock_cfg

            with patch.object(ChannelFactory, "create_adapter") as mock_create:
                mock_adapter = MagicMock()
                mock_create.return_value = mock_adapter

                adapter, cfg_id, subagent = ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_test",
                    channel_type="wecom",
                    config_id="chan_app1",
                )

                mock_db.get_by_id.assert_called_once_with("chan_app1")
                assert cfg_id == "chan_app1"
                assert subagent == "travel-consultant"

    def test_create_from_tenant_config_wrong_tenant_returns_none(self):
        """
        config_id 不属于目标租户时返回 None
        """
        from unittest.mock import patch

        from src.saas.services.channel_factory import ChannelFactory

        mock_cfg = {
            "config_id": "chan_app1",
            "tenant_id": "tenant_other",  # 不是目标租户
            "channel_type": "wecom",
            "config": {},
            "subagent_type": "travel-consultant",
            "verified": 1,
        }

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.return_value = mock_cfg

            adapter, cfg_id, subagent = ChannelFactory.create_from_tenant_config(
                tenant_id="tenant_test",
                channel_type="wecom",
                config_id="chan_app1",
            )

            assert adapter is None
            assert cfg_id is None
            assert subagent is None

    def test_create_from_tenant_config_not_found_returns_none(self):
        """
        config_id 不存在时返回 None
        """
        from unittest.mock import patch

        from src.saas.services.channel_factory import ChannelFactory

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.return_value = None

            adapter, cfg_id, subagent = ChannelFactory.create_from_tenant_config(
                tenant_id="tenant_test",
                channel_type="wecom",
                config_id="chan_nonexistent",
            )

            assert adapter is None
            assert cfg_id is None
            assert subagent is None

    def test_multiple_configs_same_tenant_return_different_subagents(self):
        """
        同一租户的不同 config_id 返回不同的 subagent_type
        """
        from unittest.mock import patch

        from src.saas.services.channel_factory import ChannelFactory

        mock_cfg_travel = {
            "config_id": "chan_travel",
            "tenant_id": "tenant_test",
            "channel_type": "wecom",
            "config": {},
            "subagent_type": "travel-consultant",
            "verified": 1,
        }

        mock_cfg_trade = {
            "config_id": "chan_trade",
            "tenant_id": "tenant_test",
            "channel_type": "wecom",
            "config": {},
            "subagent_type": "trade-specialist",
            "verified": 1,
        }

        def mock_get_by_id(config_id):
            if config_id == "chan_travel":
                return mock_cfg_travel
            elif config_id == "chan_trade":
                return mock_cfg_trade
            return None

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.side_effect = mock_get_by_id

            with patch.object(ChannelFactory, "create_adapter") as mock_create:
                mock_adapter = MagicMock()
                mock_create.return_value = mock_adapter

                # 查询第一个配置
                adapter1, id1, sub1 = ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_test",
                    channel_type="wecom",
                    config_id="chan_travel",
                )

                # 查询第二个配置
                adapter2, id2, sub2 = ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_test",
                    channel_type="wecom",
                    config_id="chan_trade",
                )

                # 验证两个配置返回不同的 subagent_type
                assert id1 == "chan_travel"
                assert sub1 == "travel-consultant"
                assert id2 == "chan_trade"
                assert sub2 == "trade-specialist"

    def test_tenant_channel_config_returns_subagent_type(self):
        """
        ChannelConfigDB.list_by_tenant 返回的配置包含 subagent_type 字段
        """
        from unittest.mock import patch

        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_configs = [
            {
                "config_id": "chan_travel",
                "tenant_id": "tenant_test",
                "channel_type": "wecom",
                "config": '{"corp_id": "wx123"}',
                "subagent_type": "travel-consultant",
                "verified": 1,
            },
            {
                "config_id": "chan_trade",
                "tenant_id": "tenant_test",
                "channel_type": "wecom",
                "config": '{"corp_id": "wx456"}',
                "subagent_type": "trade-specialist",
                "verified": 1,
            },
        ]

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = mock_configs
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            results = ChannelConfigDB.list_by_tenant("tenant_test")

            assert len(results) == 2
            # 验证每个配置都包含 subagent_type 字段
            assert results[0]["subagent_type"] in ["travel-consultant", "trade-specialist"]
            assert results[1]["subagent_type"] in ["travel-consultant", "trade-specialist"]
            # 两个配置的 subagent_type 不同
            assert results[0]["subagent_type"] != results[1]["subagent_type"]
