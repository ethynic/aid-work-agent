"""
钉钉适配器单元测试

测试 DingTalkAdapter 的核心功能：
- 消息解析（text/image/file/richText/empty）
- 消息发送（单聊/群聊分流）
- 长消息拆分
- access_token 缓存与并发刷新锁
- 速率限制（每用户滑动窗口）
- 签名验证集成
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.dingtalk.adapter import DINGTALK_API_BASE_URL, DingTalkAdapter
from src.models.message import ChannelType, MessageType, UnifiedResponse


# ---------- fixtures ----------


def _text_message(
    text: str = "hello",
    sender_id: str = "user_001",
    sender_staff_id: str = "staff_001",
    sender_nick: str = "测试用户",
    conversation_type: str = "1",
    conversation_id: str = "cid_test",
    msg_id: str = "msg_test_001",
) -> dict:
    """构造钉钉文本消息"""
    return {
        "msgtype": "text",
        "text": {"content": text},
        "msgId": msg_id,
        "createAt": int(time.time() * 1000),
        "conversationType": conversation_type,
        "conversationId": conversation_id,
        "senderId": sender_id,
        "senderStaffId": sender_staff_id,
        "senderNick": sender_nick,
    }


def _picture_message(
    download_code: str = "dl_code_123",
    sender_id: str = "user_001",
    sender_staff_id: str = "staff_001",
) -> dict:
    """构造钉钉图片消息"""
    return {
        "msgtype": "picture",
        "picture": {"downloadCode": download_code},
        "msgId": "msg_pic_001",
        "createAt": int(time.time() * 1000),
        "conversationType": "1",
        "conversationId": "cid_test",
        "senderId": sender_id,
        "senderStaffId": sender_staff_id,
        "senderNick": "用户",
    }


def _file_message(
    download_code: str = "dl_file_456",
    file_name: str = "report.pdf",
) -> dict:
    """构造钉钉文件消息"""
    return {
        "msgtype": "file",
        "file": {"downloadCode": download_code, "fileName": file_name},
        "msgId": "msg_file_001",
        "createAt": int(time.time() * 1000),
        "conversationType": "1",
        "conversationId": "cid_test",
        "senderId": "user_001",
        "senderStaffId": "staff_001",
        "senderNick": "用户",
    }


class MockResponse:
    def __init__(self, status_code: int = 200, json_data: dict = None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class MockAsyncClient:
    """记录所有 HTTP 调用的 mock 客户端"""

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
    return DingTalkAdapter(
        app_key="test_app_key",
        app_secret="test_app_secret_xxxxxxxxxxxxxxxx",
        robot_code="test_robot",
        rate_limit_window=60,
        rate_limit_max=5,
    )


# ---------- 消息解析测试 ----------


class TestDingTalkAdapterParseMessage:
    """parse_message 方法测试"""

    @pytest.mark.asyncio
    async def test_parse_text_message(self, adapter):
        """文本消息解析"""
        raw = _text_message(text="你好")
        msg = await adapter.parse_message(raw)

        assert msg is not None
        assert msg.message_id == "msg_test_001"
        assert msg.channel_type == ChannelType.DINGTALK
        assert msg.user_id == "staff_001"
        assert msg.user_name == "测试用户"
        assert msg.message_type == MessageType.TEXT
        assert msg.content["text"] == "你好"
        assert msg.content["conversation_type"] == "1"
        assert msg.content["conversation_id"] == "cid_test"

    @pytest.mark.asyncio
    async def test_parse_text_message_strips_whitespace(self, adapter):
        """文本消息自动去除首尾空白"""
        raw = _text_message(text="  hello  ")
        msg = await adapter.parse_message(raw)
        assert msg.content["text"] == "hello"

    @pytest.mark.asyncio
    async def test_parse_text_message_prefers_sender_staff_id(self, adapter):
        """user_id 优先取 senderStaffId（oToMessages 接口要求 staffId）"""
        raw = _text_message(
            text="你好",
            sender_id="$:LWCP_v1:$lwcp_session_id",
            sender_staff_id="manager81",
        )
        msg = await adapter.parse_message(raw)
        assert msg.user_id == "manager81"
        # 原始 senderId 仍保留在 raw_message 中以便排查
        assert msg.raw_message["senderId"] == "$:LWCP_v1:$lwcp_session_id"

    @pytest.mark.asyncio
    async def test_parse_text_message_falls_back_to_sender_id(self, adapter):
        """senderStaffId 缺失时回退 senderId（向后兼容）"""
        raw = _text_message(sender_id="legacy_user_id", sender_staff_id="")
        raw.pop("senderStaffId")
        msg = await adapter.parse_message(raw)
        assert msg.user_id == "legacy_user_id"

    @pytest.mark.asyncio
    async def test_parse_picture_message(self, adapter):
        """图片消息解析"""
        raw = _picture_message(download_code="dl_code_xyz")
        msg = await adapter.parse_message(raw)

        assert msg is not None
        assert msg.message_type == MessageType.IMAGE
        assert msg.content["download_code"] == "dl_code_xyz"

    @pytest.mark.asyncio
    async def test_parse_file_message(self, adapter):
        """文件消息解析"""
        raw = _file_message(download_code="dl_file_abc", file_name="report.pdf")
        msg = await adapter.parse_message(raw)

        assert msg is not None
        assert msg.message_type == MessageType.FILE
        assert msg.content["download_code"] == "dl_file_abc"
        assert msg.content["file_name"] == "report.pdf"

    @pytest.mark.asyncio
    async def test_parse_rich_text_message(self, adapter):
        """富文本消息解析（拼接多个 text 部分）"""
        raw = {
            "msgtype": "richText",
            "richText": [{"text": "第一部分"}, {"text": "第二部分"}, {"other": "skip"}],
            "msgId": "msg_rich_001",
            "createAt": int(time.time() * 1000),
            "conversationType": "1",
            "senderId": "user_001",
            "senderNick": "用户",
        }
        msg = await adapter.parse_message(raw)
        assert msg is not None
        assert msg.message_type == MessageType.TEXT
        assert msg.content["text"] == "第一部分第二部分"

    @pytest.mark.asyncio
    async def test_parse_video_message(self, adapter):
        """视频消息解析"""
        raw = {
            "msgtype": "video",
            "video": {"downloadCode": "dl_video", "fileName": "test.mp4"},
            "msgId": "msg_video",
            "createAt": int(time.time() * 1000),
            "conversationType": "1",
            "senderId": "user_001",
            "senderNick": "用户",
        }
        msg = await adapter.parse_message(raw)
        assert msg.message_type == MessageType.FILE
        assert msg.content["download_code"] == "dl_video"
        assert msg.content["file_name"] == "test.mp4"

    @pytest.mark.asyncio
    async def test_parse_audio_message(self, adapter):
        """音频消息解析"""
        raw = {
            "msgtype": "audio",
            "audio": {"downloadCode": "dl_audio", "fileName": "test.mp3"},
            "msgId": "msg_audio",
            "createAt": int(time.time() * 1000),
            "conversationType": "1",
            "senderId": "user_001",
            "senderNick": "用户",
        }
        msg = await adapter.parse_message(raw)
        assert msg.message_type == MessageType.FILE

    @pytest.mark.asyncio
    async def test_parse_empty_message_returns_none(self, adapter):
        """空消息（仅 @机器人无内容）返回 None"""
        raw = {
            "msgtype": "empty",
            "msgId": "msg_empty",
            "createAt": int(time.time() * 1000),
            "conversationType": "1",
            "senderId": "user_001",
            "senderNick": "用户",
        }
        msg = await adapter.parse_message(raw)
        assert msg is None

    @pytest.mark.asyncio
    async def test_parse_unknown_message_type(self, adapter):
        """未知消息类型降级为文本提示"""
        raw = {
            "msgtype": "unknown_type",
            "msgId": "msg_unknown",
            "createAt": int(time.time() * 1000),
            "conversationType": "1",
            "senderId": "user_001",
            "senderNick": "用户",
        }
        msg = await adapter.parse_message(raw)
        assert msg.message_type == MessageType.TEXT
        assert "[unknown_type 消息暂不支持]" in msg.content["text"]

    @pytest.mark.asyncio
    async def test_parse_message_generates_msg_id_if_missing(self, adapter):
        """缺失 msgId 时自动生成"""
        raw = _text_message()
        del raw["msgId"]
        msg = await adapter.parse_message(raw)
        assert msg.message_id.startswith("dingtalk_")

    @pytest.mark.asyncio
    async def test_parse_message_preserves_raw_message(self, adapter):
        """raw_message 字段保留原始数据"""
        raw = _text_message()
        msg = await adapter.parse_message(raw)
        assert msg.raw_message == raw

    @pytest.mark.asyncio
    async def test_parse_group_message(self, adapter):
        """群聊消息解析"""
        raw = _text_message(conversation_type="2", conversation_id="group_cid")
        msg = await adapter.parse_message(raw)
        assert msg.content["conversation_type"] == "2"
        assert msg.content["conversation_id"] == "group_cid"


# ---------- access_token 测试 ----------


class TestDingTalkAdapterToken:
    """access_token 管理测试"""

    @pytest.mark.asyncio
    async def test_get_access_token_success(self, adapter):
        """成功获取 access_token"""
        mock_client = MockAsyncClient(
            MockResponse(200, {"accessToken": "token_123", "expireIn": 7200})
        )
        adapter._http_client = mock_client

        token = await adapter.get_access_token()
        assert token == "token_123"
        assert adapter._access_token == "token_123"
        assert adapter._token_expires > time.time()

    @pytest.mark.asyncio
    async def test_get_access_token_cached(self, adapter):
        """缓存有效期内直接返回"""
        adapter._access_token = "cached_token"
        adapter._token_expires = time.time() + 3600

        # 不需要 HTTP 调用
        token = await adapter.get_access_token()
        assert token == "cached_token"

    @pytest.mark.asyncio
    async def test_get_access_token_expired_refreshes(self, adapter):
        """token 过期后重新获取"""
        adapter._access_token = "old_token"
        adapter._token_expires = time.time() - 100  # 已过期

        mock_client = MockAsyncClient(
            MockResponse(200, {"accessToken": "new_token", "expireIn": 7200})
        )
        adapter._http_client = mock_client

        token = await adapter.get_access_token()
        assert token == "new_token"

    @pytest.mark.asyncio
    async def test_get_access_token_concurrent_lock(self, adapter):
        """并发调用 100 次只触发 1 次 HTTP 请求"""
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)
            return MockResponse(200, {"accessToken": "token", "expireIn": 7200})

        mock_client = MagicMock()
        mock_client.post = mock_post
        mock_client.is_closed = False
        adapter._http_client = mock_client

        # 并发 100 次
        results = await asyncio.gather(*[adapter.get_access_token() for _ in range(100)])
        # 只触发 1 次 HTTP
        assert call_count == 1
        # 所有结果相同
        assert all(r == "token" for r in results)

    @pytest.mark.asyncio
    async def test_get_access_token_failure_raises(self, adapter):
        """获取失败抛出异常"""
        mock_client = MockAsyncClient(MockResponse(400, {"message": "invalid appKey"}))
        adapter._http_client = mock_client

        with pytest.raises(RuntimeError, match="获取 access_token 失败"):
            await adapter.get_access_token()

    def test_invalidate_token(self, adapter):
        """_invalidate_token 清空缓存"""
        adapter._access_token = "token"
        adapter._token_expires = time.time() + 3600
        adapter._invalidate_token()
        assert adapter._access_token is None
        assert adapter._token_expires == 0


# ---------- 速率限制测试 ----------


class TestDingTalkAdapterRateLimit:
    """速率限制测试"""

    def test_rate_limit_allows_initial_requests(self, adapter):
        """初始请求应通过"""
        assert adapter._check_rate_limit("user_001") is True
        assert adapter._check_rate_limit("user_001") is True

    def test_rate_limit_blocks_after_max(self, adapter):
        """超过 max 后拦截"""
        # rate_limit_max=5
        for _ in range(5):
            assert adapter._check_rate_limit("user_001") is True
        assert adapter._check_rate_limit("user_001") is False

    def test_rate_limit_per_user_independent(self, adapter):
        """不同用户独立计数"""
        for _ in range(5):
            adapter._check_rate_limit("user_001")
        # user_001 已用尽，user_002 仍可发送
        assert adapter._check_rate_limit("user_001") is False
        assert adapter._check_rate_limit("user_002") is True

    def test_rate_limit_sliding_window(self, adapter):
        """滑动窗口：旧请求过期后新请求可通过"""
        # 先填充窗口
        for _ in range(5):
            adapter._check_rate_limit("user_001")
        assert adapter._check_rate_limit("user_001") is False

        # 手动将窗口中的时间戳移到过去
        adapter._rate_limiter["user_001"].clear()
        adapter._rate_limiter["user_001"].append(time.time() - 100)

        # 旧请求已过期，新请求通过
        assert adapter._check_rate_limit("user_001") is True


# ---------- 消息发送测试 ----------


class TestDingTalkAdapterSendMessage:
    """send_message 方法测试"""

    @pytest.mark.asyncio
    async def test_send_text_message_single_chat(self, adapter):
        """单聊发送文本消息"""
        mock_client = MockAsyncClient()
        adapter._http_client = mock_client
        adapter._access_token = "token"
        adapter._token_expires = time.time() + 3600

        response = UnifiedResponse(
            message_id="msg_resp_001",
            reply_to="user_001",
            content={"text": "回复消息", "conversation_type": "1"},
        )
        result = await adapter.send_message(response)

        assert result is True
        assert len(mock_client.post_calls) >= 1
        call = mock_client.post_calls[0]
        assert "oToMessages/batchSend" in call["url"]
        payload = call["kwargs"]["json"]
        assert payload["userIds"] == ["user_001"]

    @pytest.mark.asyncio
    async def test_send_text_message_group_chat(self, adapter):
        """群聊发送文本消息"""
        mock_client = MockAsyncClient()
        adapter._http_client = mock_client
        adapter._access_token = "token"
        adapter._token_expires = time.time() + 3600

        response = UnifiedResponse(
            message_id="msg_resp_002",
            reply_to="group_cid_123",
            content={"text": "群聊回复", "conversation_type": "2"},
        )
        result = await adapter.send_message(response)

        assert result is True
        call = mock_client.post_calls[0]
        assert "groupMessages/send" in call["url"]
        payload = call["kwargs"]["json"]
        assert payload["openConversationId"] == "group_cid_123"

    @pytest.mark.asyncio
    async def test_send_long_message_splits(self, adapter):
        """超长消息自动拆分"""
        mock_client = MockAsyncClient()
        adapter._http_client = mock_client
        adapter._access_token = "token"
        adapter._token_expires = time.time() + 3600

        long_text = "a" * 5000
        result = await adapter.send_long_message(long_text, "user_001")

        assert result is True
        # 至少拆分成了多条
        assert len(mock_client.post_calls) >= 2

    @pytest.mark.asyncio
    async def test_send_message_rate_limited(self, adapter):
        """速率限制拦截发送"""
        # 先消耗掉所有配额
        for _ in range(adapter._rate_limit_max):
            adapter._check_rate_limit("user_001")

        result = await adapter.send_long_message("text", "user_001")
        assert result is False


# ---------- 签名验证集成测试 ----------


class TestDingTalkAdapterVerifySignature:
    """verify_signature 方法集成测试"""

    @pytest.mark.asyncio
    async def test_verify_signature_valid(self, adapter):
        """有效签名通过验证"""
        import base64
        import hashlib
        import hmac

        timestamp = str(int(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{adapter.app_secret}"
        hmac_code = hmac.new(
            adapter.app_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        sign = base64.b64encode(hmac_code).decode("utf-8")

        result = await adapter.verify_signature(sign, timestamp, "", "")
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_signature_invalid(self, adapter):
        """无效签名返回 False"""
        timestamp = str(int(time.time() * 1000))
        result = await adapter.verify_signature("wrong_sign", timestamp, "", "")
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_signature_expired_timestamp(self, adapter):
        """时间戳过期返回 False"""
        import base64
        import hashlib
        import hmac

        # 2 小时前的时间戳
        old_timestamp = str(int((time.time() - 7200) * 1000))
        string_to_sign = f"{old_timestamp}\n{adapter.app_secret}"
        hmac_code = hmac.new(
            adapter.app_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        sign = base64.b64encode(hmac_code).decode("utf-8")

        result = await adapter.verify_signature(sign, old_timestamp, "", "")
        assert result is False


# ---------- 其他方法测试 ----------


class TestDingTalkAdapterMisc:
    """其他方法测试"""

    def test_channel_type(self, adapter):
        """channel_type 属性"""
        assert adapter.channel_type == "dingtalk"

    @pytest.mark.asyncio
    async def test_close_http_client(self, adapter):
        """关闭 HTTP 客户端"""
        mock_client = MockAsyncClient()
        adapter._http_client = mock_client

        await adapter.close()
        assert mock_client.is_closed is True

    @pytest.mark.asyncio
    async def test_close_when_no_client(self, adapter):
        """没有客户端时关闭不报错"""
        adapter._http_client = None
        await adapter.close()

    @pytest.mark.asyncio
    async def test_get_user_info_success(self, adapter):
        """获取用户信息"""
        mock_client = MockAsyncClient(
            MockResponse(
                200,
                {
                    "errcode": 0,
                    "result": {
                        "userid": "user_001",
                        "name": "张三",
                        "dept_id_list": [1, 2],
                        "title": "工程师",
                        "mobile": "13800138000",
                        "email": "zhangsan@example.com",
                        "avatar": "https://example.com/avatar.jpg",
                    },
                },
            )
        )
        adapter._http_client = mock_client
        adapter._access_token = "token"
        adapter._token_expires = time.time() + 3600

        info = await adapter.get_user_info("user_001")
        assert info["user_id"] == "user_001"
        assert info["name"] == "张三"
        assert info["department"] == [1, 2]
        assert info["position"] == "工程师"
        assert info["mobile"] == "13800138000"
        assert info["email"] == "zhangsan@example.com"

    @pytest.mark.asyncio
    async def test_get_user_info_failure(self, adapter):
        """获取用户信息失败返回空 dict"""
        mock_client = MockAsyncClient(MockResponse(200, {"errcode": 40001, "errmsg": "error"}))
        adapter._http_client = mock_client
        adapter._access_token = "token"
        adapter._token_expires = time.time() + 3600

        info = await adapter.get_user_info("user_001")
        assert info == {}

    @pytest.mark.asyncio
    async def test_send_waiting_indicator(self, adapter):
        """发送等待提示"""
        mock_client = MockAsyncClient()
        adapter._http_client = mock_client
        adapter._access_token = "token"
        adapter._token_expires = time.time() + 3600

        result = await adapter.send_waiting_indicator("user_001", "思考中...")
        assert result is True
        # 等待提示应发送到单聊
        call = mock_client.post_calls[0]
        assert "oToMessages/batchSend" in call["url"]
