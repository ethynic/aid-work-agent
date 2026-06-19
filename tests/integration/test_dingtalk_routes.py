"""
钉钉回调路由集成测试

覆盖 implementation_plan.md 阶段三任务 3.2 的核心场景：
1. 正常消息接收 → 签名验证 → 去重 → 异步处理
2. 签名验证失败 → 返回 403
3. 重复事件 → 返回 200 但不处理（msgId 去重）
4. 单聊消息 → 后台处理函数被调用
5. 群聊消息 → 后台处理函数被调用
6. 缺失配置 → 返回 404
7. body JSON 解析失败 → 返回 400
8. 空消息（msgtype=empty）→ 返回 200 但不处理
9. GET 回调返回 200（兼容路径）
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------- Fixtures ----------


@pytest.fixture
def dingtalk_adapter():
    """构造可用于路由测试的 DingTalkAdapter mock

    使用真实的 DingTalkCrypto 以便验证签名校验逻辑。
    """
    from src.channels.dingtalk.crypto import DingTalkCrypto

    real_crypto = DingTalkCrypto(app_secret="dt_test_secret")

    adapter = MagicMock()
    adapter.crypto = real_crypto
    adapter.app_key = "dingdtest"
    adapter.app_secret = "dt_test_secret"
    adapter.robot_code = "dingdtest"

    async def _verify(signature, timestamp, nonce, body):
        if not real_crypto.verify_signature(timestamp, signature):
            return False
        if not real_crypto.check_timestamp(timestamp):
            return False
        return True

    adapter.verify_signature = AsyncMock(side_effect=_verify)
    return adapter


@pytest.fixture
def client():
    """构建最小 FastAPI app，避免触发 src.main 中 apscheduler 的副作用"""
    from fastapi import FastAPI
    from src.saas.api import channel_routes

    test_app = FastAPI()
    test_app.include_router(channel_routes.router)
    return TestClient(test_app)


@pytest.fixture(autouse=True)
def reset_dingtalk_dedup_cache():
    """每个测试前用内存版去重器替换默认实现，避免跨测试 DB 状态污染"""
    from src.saas.api import channel_routes

    seen: set[str] = set()

    class InMemoryDedup:
        async def is_duplicate(self, message_id: str) -> bool:
            if message_id in seen:
                return True
            seen.add(message_id)
            return False

    fake_factory = lambda tenant_id: InMemoryDedup()  # noqa: E731

    with patch.object(channel_routes, "_get_dingtalk_event_dedup", side_effect=fake_factory):
        yield


# ---------- Helpers ----------


def _make_event(
    msg_type: str = "text",
    text: str = "hello",
    msg_id: str = "",
    conversation_type: str = "1",
    conversation_id: str = "cidXXX",
    sender_id: str = "ding_user_001",
) -> dict:
    """构造钉钉消息回调 JSON

    Args:
        msg_type: text / picture / file / empty
        text: 文本内容（msg_type=text 时使用）
        msg_id: 消息 ID（默认随机生成）
        conversation_type: "1" 单聊 / "2" 群聊
        conversation_id: 会话 ID（群聊时为 openConversationId）
        sender_id: 发送者 ID
    """
    if not msg_id:
        msg_id = f"msg_{uuid.uuid4().hex}"

    event: dict = {
        "msgtype": msg_type,
        "msgId": msg_id,
        "createAt": int(time.time() * 1000),
        "conversationType": conversation_type,
        "conversationId": conversation_id,
        "senderId": sender_id,
        "senderNick": "测试用户",
        "senderCorpId": "ding_corp_test",
        "robotCode": "dingdtest",
    }

    if msg_type == "text":
        event["text"] = {"content": text}
    elif msg_type == "picture":
        event["picture"] = {"downloadCode": "pic_dl_code_test"}
    elif msg_type == "file":
        event["file"] = {"downloadCode": "file_dl_code_test", "fileName": "test.pdf"}

    return event


def _sign(timestamp: str, app_secret: str) -> str:
    """计算钉钉 HmacSHA256 签名"""
    string_to_sign = f"{timestamp}\n{app_secret}"
    hmac_code = hmac.new(
        app_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def _patch_factory(adapter):
    """Patch ChannelFactory.create_from_tenant_config 返回指定 adapter"""
    return patch(
        "src.saas.api.channel_routes.ChannelFactory.create_from_tenant_config",
        return_value=(adapter, "chan_dingtalk_test", "test-subagent"),
    )


def _patch_background():
    """Patch 后台处理函数，避免真实业务处理"""
    return patch(
        "src.saas.api.channel_routes._process_tenant_dingtalk_background",
        new_callable=AsyncMock,
    )


# ---------- 1. 签名验证 ----------


class TestSignatureVerification:
    def test_valid_signature_passes(self, client, dingtalk_adapter):
        """带正确签名的请求 → 200"""
        timestamp = str(int(time.time() * 1000))
        sign = _sign(timestamp, "dt_test_secret")
        event = _make_event()

        with _patch_factory(dingtalk_adapter), _patch_background() as bg:
            resp = client.post(
                "/t/tenant_test/dingtalk/callback",
                json=event,
                headers={"timestamp": timestamp, "sign": sign},
            )

        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        # 后台被调度
        bg.assert_called_once()

    def test_invalid_signature_returns_403(self, client, dingtalk_adapter):
        """签名错误 → 403"""
        timestamp = str(int(time.time() * 1000))
        event = _make_event()

        with _patch_factory(dingtalk_adapter), _patch_background() as bg:
            resp = client.post(
                "/t/tenant_test/dingtalk/callback",
                json=event,
                headers={"timestamp": timestamp, "sign": "WRONG_SIGNATURE"},
            )

        assert resp.status_code == 403
        bg.assert_not_called()

    def test_no_signature_header_passes(self, client, dingtalk_adapter):
        """未提供 sign 头 → 跳过签名校验，直接放行（兼容内网/调试场景）"""
        event = _make_event()

        with _patch_factory(dingtalk_adapter), _patch_background() as bg:
            resp = client.post(
                "/t/tenant_test/dingtalk/callback",
                json=event,
            )

        assert resp.status_code == 200
        bg.assert_called_once()


# ---------- 2. 消息去重 ----------


class TestMessageDeduplication:
    def test_duplicate_msg_id_not_processed(self, client, dingtalk_adapter):
        """5 分钟内重复 msgId 直接返回 200，不触发后台处理"""
        timestamp = str(int(time.time() * 1000))
        sign = _sign(timestamp, "dt_test_secret")
        event = _make_event(msg_id="msg_dedup_001")

        with _patch_factory(dingtalk_adapter), _patch_background() as bg:
            resp1 = client.post(
                "/t/tenant_test/dingtalk/callback",
                json=event,
                headers={"timestamp": timestamp, "sign": sign},
            )
            resp2 = client.post(
                "/t/tenant_test/dingtalk/callback",
                json=event,
                headers={"timestamp": timestamp, "sign": sign},
            )

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # 第一次触发，第二次被去重拦截
        assert bg.call_count == 1

    def test_different_msg_ids_both_processed(self, client, dingtalk_adapter):
        """不同 msgId 各自触发后台处理"""
        timestamp = str(int(time.time() * 1000))
        sign = _sign(timestamp, "dt_test_secret")
        event1 = _make_event(msg_id="msg_unique_001")
        event2 = _make_event(msg_id="msg_unique_002")

        with _patch_factory(dingtalk_adapter), _patch_background() as bg:
            client.post(
                "/t/tenant_test/dingtalk/callback",
                json=event1,
                headers={"timestamp": timestamp, "sign": sign},
            )
            client.post(
                "/t/tenant_test/dingtalk/callback",
                json=event2,
                headers={"timestamp": timestamp, "sign": sign},
            )

        assert bg.call_count == 2


# ---------- 3. 消息类型 ----------


class TestMessageTypes:
    def test_single_chat_message_dispatched(self, client, dingtalk_adapter):
        """单聊消息（conversationType=1）→ 后台处理"""
        timestamp = str(int(time.time() * 1000))
        sign = _sign(timestamp, "dt_test_secret")
        event = _make_event(conversation_type="1")

        with _patch_factory(dingtalk_adapter), _patch_background() as bg:
            resp = client.post(
                "/t/tenant_test/dingtalk/callback",
                json=event,
                headers={"timestamp": timestamp, "sign": sign},
            )

        assert resp.status_code == 200
        bg.assert_called_once()
        # 验证传入的 event_data 中保留了 conversationType
        call_args = bg.call_args
        assert call_args[0][1].get("conversationType") == "1"

    def test_group_chat_message_dispatched(self, client, dingtalk_adapter):
        """群聊消息（conversationType=2）→ 后台处理"""
        timestamp = str(int(time.time() * 1000))
        sign = _sign(timestamp, "dt_test_secret")
        event = _make_event(
            conversation_type="2",
            conversation_id="cidGroup123",
        )

        with _patch_factory(dingtalk_adapter), _patch_background() as bg:
            resp = client.post(
                "/t/tenant_test/dingtalk/callback",
                json=event,
                headers={"timestamp": timestamp, "sign": sign},
            )

        assert resp.status_code == 200
        bg.assert_called_once()
        call_args = bg.call_args
        assert call_args[0][1].get("conversationType") == "2"
        assert call_args[0][1].get("conversationId") == "cidGroup123"

    def test_empty_message_skipped(self, client, dingtalk_adapter):
        """msgtype=empty 直接返回 200，不触发后台"""
        timestamp = str(int(time.time() * 1000))
        sign = _sign(timestamp, "dt_test_secret")
        event = _make_event(msg_type="empty")

        with _patch_factory(dingtalk_adapter), _patch_background() as bg:
            resp = client.post(
                "/t/tenant_test/dingtalk/callback",
                json=event,
                headers={"timestamp": timestamp, "sign": sign},
            )

        assert resp.status_code == 200
        bg.assert_not_called()


# ---------- 4. 错误处理 ----------


class TestErrorHandling:
    def test_missing_config_returns_404(self, client):
        """租户没有钉钉配置时返回 404"""
        with patch(
            "src.saas.api.channel_routes.ChannelFactory.create_from_tenant_config",
            return_value=(None, None, None),
        ):
            resp = client.post(
                "/t/tenant_no_config/dingtalk/callback",
                json=_make_event(),
            )

        assert resp.status_code == 404

    def test_invalid_json_body_returns_400(self, client, dingtalk_adapter):
        """body 非合法 JSON → 400"""
        timestamp = str(int(time.time() * 1000))
        sign = _sign(timestamp, "dt_test_secret")

        with _patch_factory(dingtalk_adapter):
            resp = client.post(
                "/t/tenant_test/dingtalk/callback",
                content="this-is-not-json",
                headers={
                    "Content-Type": "application/json",
                    "timestamp": timestamp,
                    "sign": sign,
                },
            )

        assert resp.status_code == 400


# ---------- 5. GET 回调（兼容） ----------


class TestGetCallback:
    def test_get_callback_returns_ok(self, client):
        """GET /dingtalk/callback 返回 200（兼容路径）"""
        resp = client.get("/t/tenant_test/dingtalk/callback")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------- 6. 后台调度立即返回 ----------


class TestImmediateResponse:
    @pytest.mark.asyncio
    async def test_route_responds_immediately(self, client, dingtalk_adapter):
        """路由立即返回 2xx，后台处理通过 asyncio.create_task 异步执行"""
        timestamp = str(int(time.time() * 1000))
        sign = _sign(timestamp, "dt_test_secret")
        event = _make_event()

        with _patch_factory(dingtalk_adapter), _patch_background() as bg:
            resp = client.post(
                "/t/tenant_test/dingtalk/callback",
                json=event,
                headers={"timestamp": timestamp, "sign": sign},
            )

        assert resp.status_code == 200
        assert resp.json() == {"success": True}

        # 给事件循环一点时间执行 create_task
        import asyncio
        await asyncio.sleep(0.1)

        bg.assert_called_once()
        call_args = bg.call_args
        assert call_args[0][0] == "tenant_test"  # tenant_id
        assert call_args[1].get("subagent_type") == "test-subagent"
