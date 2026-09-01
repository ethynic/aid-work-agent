"""
飞书适配器单元测试

锁定 implementation_plan.md §2.6.2 的 10 个测试点：
1. test_parse_text_message_v2_event_structure    — v2.0 事件解析
2. test_parse_group_message_filters_mentions     — 群聊 @占位符清理
3. test_parse_group_message_ignores_if_not_mentioned — 群聊未 @机器人 → 忽略
4. test_parse_image_message_extracts_image_key   — image_key 提取
5. test_send_message_content_is_json_string      — P1 避坑：content 是 JSON 字符串
6. test_send_long_message_three_tier_split       — 长消息三级拆分
7. test_access_token_refresh_lock                — 并发 100 次只触发 1 次 HTTP
8. test_rate_limit_per_user_sliding_window       — 每 user 独立滑动窗口
9. test_bot_open_id_lazy_loaded_and_cached       — /open-apis/bot/v3/info 只调一次
10. test_format_response_implemented             — format_response 有实现
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.feishu.adapter import FEISHU_BASE_URL, FeishuAdapter
from src.models.message import ChannelType, MessageType, UnifiedResponse


# ---------- fixtures ----------


def _v2_event(
    text: str = "hello",
    chat_type: str = "p2p",
    message_type: str = "text",
    content: dict = None,
    mentions: list = None,
    message_id: str = "msg_v2_test",
    sender_open_id: str = "ou_sender_001",
    create_time: str = "1700000000000",
) -> dict:
    """构造飞书 v2.0 im.message.receive_v1 事件"""
    if content is None:
        content = {"text": text}
    event = {
        "header": {
            "event_id": "evt_test",
            "event_type": "im.message.receive_v1",
            "create_time": create_time,
            "token": "test_token",
            "app_id": "cli_test",
            "tenant_key": "tenant_test",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": sender_open_id, "user_id": "", "union_id": ""},
                "sender_type": "user",
            },
            "message": {
                "message_id": message_id,
                "root_id": "",
                "parent_id": "",
                "create_time": create_time,
                "chat_id": "oc_test",
                "chat_type": chat_type,
                "message_type": message_type,
                "content": json.dumps(content, ensure_ascii=False),
                "mentions": mentions or [],
            },
        },
    }
    return event


class MockResponse:
    def __init__(self, status_code: int = 200, json_data: dict = None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class MockAsyncClient:
    """记录所有 post/get 调用的 mock 客户端"""

    def __init__(self, response: MockResponse = None):
        self.response = response or MockResponse()
        self.post_calls = []
        self.get_calls = []
        self.is_closed = False

    async def post(self, url, **kwargs):
        self.post_calls.append({"url": url, "kwargs": kwargs})
        return self.response

    async def get(self, url, **kwargs):
        self.get_calls.append({"url": url, "kwargs": kwargs})
        return self.response

    async def aclose(self):
        self.is_closed = True


@pytest.fixture
def adapter():
    """创建 FeishuAdapter 实例（无加密模式）"""
    return FeishuAdapter(
        app_id="cli_test",
        app_secret="secret_test",
        verification_token="vt_test",
        encrypt_key="",  # 关闭加密，简化测试
        welcome_message="欢迎",
        max_bytes=4000,
        rate_limit_window=60,
        rate_limit_max=10,
    )


# ---------- 1. v2.0 事件解析 ----------


class TestParseMessageV2:
    def test_parse_text_message_v2_event_structure(self, adapter):
        """从 event.message.content（JSON 字符串）中提取 text"""
        raw = _v2_event(text="你好飞书", message_id="msg_001")

        async def run():
            return await adapter.parse_message(raw)

        msg = asyncio.run(run())

        assert msg is not None
        assert msg.message_id == "msg_001"
        assert msg.channel_type == ChannelType.FEISHU
        assert msg.user_id == "ou_sender_001"
        assert msg.message_type == MessageType.TEXT
        assert msg.content == {"text": "你好飞书"}

    def test_parse_ignores_non_message_event(self, adapter):
        raw = _v2_event()
        raw["header"]["event_type"] = "im.chat.disbanded_v1"

        msg = asyncio.run(adapter.parse_message(raw))
        assert msg is None

    def test_parse_malformed_content_returns_none(self, adapter):
        raw = _v2_event()
        raw["event"]["message"]["content"] = "not-a-json{"

        msg = asyncio.run(adapter.parse_message(raw))
        assert msg is None


# ---------- 2/3. 群聊 @机器人处理 ----------


class TestGroupMentions:
    def test_parse_group_message_filters_mentions(self, adapter):
        """群聊消息清理 @_user_X 占位符，还原成真实文字"""
        # 预先注入 bot_open_id，避免触发 _init_bot_open_id 的 HTTP 调用
        adapter._bot_open_id = "ou_bot_001"

        mentions = [
            {"key": "@_user_1", "name": "机器人", "id": {"open_id": "ou_bot_001"}},
            {"key": "@_user_2", "name": "张三", "id": {"open_id": "ou_zhang"}},
        ]
        raw = _v2_event(
            text="@_user_1 @_user_2 帮我查下订单",
            chat_type="group",
            mentions=mentions,
        )

        msg = asyncio.run(adapter.parse_message(raw))

        assert msg is not None
        # @_user_1 → @机器人，@_user_2 → @张三
        assert msg.content["text"] == "@机器人 @张三 帮我查下订单"

    def test_parse_group_message_ignores_if_not_mentioned(self, adapter):
        """群聊中没 @ 机器人的消息直接忽略"""
        adapter._bot_open_id = "ou_bot_001"

        mentions = [
            {"key": "@_user_1", "name": "张三", "id": {"open_id": "ou_zhang"}},
        ]
        raw = _v2_event(
            text="@_user_1 你好",
            chat_type="group",
            mentions=mentions,
        )

        msg = asyncio.run(adapter.parse_message(raw))
        assert msg is None

    def test_parse_group_no_mentions_at_all(self, adapter):
        """群聊消息无任何 mention 也忽略"""
        adapter._bot_open_id = "ou_bot_001"
        raw = _v2_event(text="今天天气如何", chat_type="group", mentions=[])

        msg = asyncio.run(adapter.parse_message(raw))
        assert msg is None


# ---------- 4. 图片消息 ----------


class TestImageMessage:
    def test_parse_image_message_extracts_image_key(self, adapter):
        raw = _v2_event(
            content={"image_key": "img_v2_abc123"},
            message_type="image",
        )

        msg = asyncio.run(adapter.parse_message(raw))

        assert msg is not None
        assert msg.message_type == MessageType.IMAGE
        assert msg.content["image_key"] == "img_v2_abc123"

    def test_parse_file_message(self, adapter):
        raw = _v2_event(
            content={"file_key": "file_v2_xyz", "file_name": "report.pdf"},
            message_type="file",
        )

        msg = asyncio.run(adapter.parse_message(raw))

        assert msg is not None
        assert msg.message_type == MessageType.FILE
        assert msg.content["file_key"] == "file_v2_xyz"
        assert msg.content["file_name"] == "report.pdf"


# ---------- 5. P1 避坑：content 是 JSON 字符串 ----------


class TestSendMessageContentIsJsonString:
    @pytest.mark.asyncio
    async def test_send_message_content_is_json_string(self, adapter):
        """关键避坑：发送请求体 content 字段必须是 JSON 字符串，不能是对象"""
        mock_client = MockAsyncClient(MockResponse(json_data={"code": 0}))
        adapter._http_client = mock_client
        adapter._access_token = "valid_token"
        adapter._token_expires = time.time() + 3600

        result = await adapter.send_text("hello", "ou_user_001")

        assert result is True
        assert len(mock_client.post_calls) == 1
        call = mock_client.post_calls[0]
        # URL 正确
        assert call["url"] == f"{FEISHU_BASE_URL}/open-apis/im/v1/messages"
        # receive_id_type 参数
        assert call["kwargs"]["params"]["receive_id_type"] == "open_id"
        # Authorization
        assert call["kwargs"]["headers"]["Authorization"] == "Bearer valid_token"
        # 关键 P1：content 字段必须是字符串
        body = call["kwargs"]["json"]
        assert body["receive_id"] == "ou_user_001"
        assert body["msg_type"] == "text"
        assert isinstance(body["content"], str), "content 必须是字符串（P1 避坑）"
        # 字符串反序列化后应为 {"text": "hello"}
        parsed = json.loads(body["content"])
        assert parsed == {"text": "hello"}
        # uuid 幂等字段
        assert "uuid" in body


# ---------- 6. 长消息三级拆分 ----------


class TestSendLongMessage:
    @pytest.mark.asyncio
    async def test_send_long_message_three_tier_split(self, adapter):
        """超长消息按段落/行/字节拆分，每条都发出"""
        # 两段各 3000 字节，单段不超 4000，合并 6001 > 4000 → 拆为 2 段
        para1 = "a" * 3000
        para2 = "b" * 3000
        long_text = f"{para1}\n\n{para2}"

        mock_client = MockAsyncClient(MockResponse(json_data={"code": 0}))
        adapter._http_client = mock_client
        adapter._access_token = "tok"
        adapter._token_expires = time.time() + 3600

        result = await adapter.send_long_message(long_text, "ou_user_001")

        assert result is True
        # 应发送 2 条
        assert len(mock_client.post_calls) == 2
        # 每条的 content 都不超过 4000 字节
        for call in mock_client.post_calls:
            content_str = call["kwargs"]["json"]["content"]
            assert len(content_str.encode("utf-8")) <= 4000


# ---------- 7. Token 刷新并发锁 ----------


class TestTokenRefreshLock:
    @pytest.mark.asyncio
    async def test_access_token_refresh_lock(self, adapter):
        """并发 100 次 get_access_token 只触发 1 次 HTTP 请求"""
        call_count = 0

        class CountingClient:
            is_closed = False  # _get_client 检查此属性

            async def post(self, url, **kwargs):
                nonlocal call_count
                call_count += 1
                # 模拟网络延迟，确保并发真的并发
                await asyncio.sleep(0.05)
                return MockResponse(json_data={
                    "code": 0,
                    "tenant_access_token": "tok_shared",
                    "expire": 7200,
                })

        adapter._http_client = CountingClient()
        # 强制所有 100 个任务都走刷新路径
        adapter._access_token = None
        adapter._token_expires = 0

        # 并发 100 次
        results = await asyncio.gather(*[adapter.get_access_token() for _ in range(100)])

        # 全部返回相同 token
        assert all(r == "tok_shared" for r in results)
        # 只触发 1 次 HTTP 请求（双重检查锁定生效）
        assert call_count == 1


# ---------- 8. 速率限制 ----------


class TestRateLimit:
    def test_rate_limit_per_user_sliding_window(self, adapter):
        """60s 内超出 10 条后触发限流，不同 user 互不影响"""
        user_a = "ou_a"
        user_b = "ou_b"

        # user_a 用满 10 条配额
        for _ in range(10):
            assert adapter._check_rate_limit(user_a) is True
        # 第 11 条被限流
        assert adapter._check_rate_limit(user_a) is False

        # user_b 独立，不受影响
        assert adapter._check_rate_limit(user_b) is True

    def test_rate_limit_window_eviction(self, adapter):
        """旧记录过期后配额恢复"""
        adapter._rate_limit_window = 1  # 1 秒窗口
        adapter._rate_limit_max = 2

        user = "ou_x"
        assert adapter._check_rate_limit(user) is True
        assert adapter._check_rate_limit(user) is True
        assert adapter._check_rate_limit(user) is False  # 满

        # 手动把窗口内的时间戳都改到过期
        adapter._rate_limiter[user] = deque_item = type(adapter._rate_limiter[user])()
        # 直接清空并插入过期时间戳
        adapter._rate_limiter[user].clear()
        adapter._rate_limiter[user].append(time.time() - 10)

        # 过期后配额恢复
        assert adapter._check_rate_limit(user) is True


# ---------- 9. bot_open_id 懒加载 ----------


class TestBotOpenIdLazyLoad:
    @pytest.mark.asyncio
    async def test_bot_open_id_lazy_loaded_and_cached(self, adapter):
        """_init_bot_open_id 只调用一次 /open-apis/bot/v3/info，结果缓存"""
        call_count = 0

        class CountingClient:
            is_closed = False  # _get_client 检查此属性

            async def get(self, url, **kwargs):
                nonlocal call_count
                call_count += 1
                return MockResponse(json_data={
                    "code": 0,
                    "bot": {"open_id": "ou_bot_cached", "app_name": "TestBot"},
                })

            async def post(self, url, **kwargs):
                return MockResponse(json_data={})

        adapter._http_client = CountingClient()
        adapter._access_token = "tok"
        adapter._token_expires = time.time() + 3600

        # 多次调用
        await adapter._init_bot_open_id()
        await adapter._init_bot_open_id()
        await adapter._init_bot_open_id()

        # 只调用 1 次 /open-apis/bot/v3/info
        assert call_count == 1
        assert adapter._bot_open_id == "ou_bot_cached"


# ---------- 10. format_response ----------


class TestFormatResponse:
    def test_format_response_implemented(self, adapter):
        """format_response 有实现（继承自基类）"""
        result = adapter.format_response("你好", extra_key="value")

        assert isinstance(result, dict)
        assert result["text"] == "你好"
        assert result["extra_key"] == "value"


# ---------- 补充：token 刷新重试 ----------


class TestTokenRefreshRetry:
    @pytest.mark.asyncio
    async def test_refresh_retries_on_failure(self, adapter):
        """前两次失败、第三次成功，最终拿到 token"""
        attempts = []

        class FlakyClient:
            is_closed = False  # _get_client 检查此属性

            async def post(self, url, **kwargs):
                attempts.append(1)
                if len(attempts) < 3:
                    raise RuntimeError("network error")
                return MockResponse(json_data={
                    "code": 0,
                    "tenant_access_token": "tok_after_retry",
                    "expire": 7200,
                })

        adapter._http_client = FlakyClient()
        adapter._access_token = None

        token = await adapter.get_access_token()
        assert token == "tok_after_retry"
        assert len(attempts) == 3

    @pytest.mark.asyncio
    async def test_send_invalidates_token_on_expiry_code(self, adapter):
        """发送时收到 99991663（token 过期），应刷新后重试"""
        post_calls = []

        class TokenExpiringClient:
            is_closed = False  # _get_client 检查此属性

            async def post(self, url, **kwargs):
                post_calls.append(kwargs["json"].get("uuid"))
                if len(post_calls) == 1:
                    # 第一次：token 过期
                    return MockResponse(json_data={"code": 99991663, "msg": "token expired"})
                # 第二次：成功
                return MockResponse(json_data={"code": 0})

        adapter._http_client = TokenExpiringClient()
        adapter._access_token = "expired_token"
        adapter._token_expires = time.time() + 3600

        # 注入假 token 刷新路径，避免真实 HTTP 调用
        async def fake_refresh():
            adapter._access_token = "new_token"
            adapter._token_expires = time.time() + 3600
            return "new_token"

        adapter._refresh_access_token = fake_refresh

        result = await adapter.send_text("hello", "ou_user")
        assert result is True
        # 发了 2 次（第一次 token 过期，第二次用新 token）
        assert len(post_calls) == 2


# ---------- 补充：欢迎消息 / 速率限制绕过 ----------


