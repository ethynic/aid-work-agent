"""
飞书回调路由集成测试

锁定 implementation_plan.md §2.6.3 的 6 个测试点：
1. test_post_url_verification_returns_challenge         — POST url_verification 返回 challenge
2. test_post_url_verification_rejects_wrong_token        — token 不匹配返回 403
3. test_v2_event_signature_verified                      — X-Lark-Signature 签名校验
4. test_event_deduplication_by_event_id                  — 5 分钟内重复 event_id 直接返回 200
5. test_non_message_event_returns_200                    — 非 im.message.receive_v1 事件返回 200
6. test_route_responds_200_immediately                   — 路由立即返回 2xx，后台异步处理
"""

import hashlib
import json
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def feishu_adapter():
    """创建测试用 FeishuAdapter mock（无加密模式，简化大多数测试）"""
    adapter = MagicMock()
    adapter.crypto = None  # 无加密模式
    adapter.verification_token = "vt_test_token"
    adapter.encrypt_key = "ek_test_key"
    adapter.app_id = "cli_test"
    return adapter


@pytest.fixture
def feishu_adapter_encrypted():
    """创建测试用 FeishuAdapter mock（加密模式）"""
    from src.channels.feishu.crypto import FeishuCrypto

    real_crypto = FeishuCrypto(
        verification_token="vt_test_token",
        encrypt_key="ek_test_key",
    )
    adapter = MagicMock()
    adapter.crypto = real_crypto
    adapter.verification_token = "vt_test_token"
    adapter.encrypt_key = "ek_test_key"
    adapter.app_id = "cli_test"
    return adapter


@pytest.fixture
def client():
    """构建最小 FastAPI app，只挂 channel_routes.router，避免导入 src.main（会触发 apscheduler）"""
    from fastapi import FastAPI
    from src.saas.api import channel_routes

    test_app = FastAPI()
    test_app.include_router(channel_routes.router)
    return TestClient(test_app)


@pytest.fixture(autouse=True)
def reset_feishu_dedup_cache():
    """每个测试前用内存版去重器替换 PostgreSQL 版，避免跨测试 DB 状态污染

    路由代码调用 `_get_feishu_event_dedup(tenant_id)` 拿到的 `MessageDeduplicator`
    底层走 PostgreSQL `channel_message_dedup` 表，记录在 DB 中持久化。
    测试环境下替换为内存版，每个测试从空白状态开始，但同一测试内重复 event_id 仍能被检测到。
    """
    from src.saas.api import channel_routes

    seen: set[str] = set()

    class InMemoryDedup:
        async def is_duplicate(self, message_id: str) -> bool:
            if message_id in seen:
                return True
            seen.add(message_id)
            return False

    fake_factory = lambda tenant_id: InMemoryDedup()  # noqa: E731

    with patch.object(channel_routes, "_get_feishu_event_dedup", side_effect=fake_factory):
        yield


def _v2_event(
    text: str = "hello",
    event_id: str = "",
    event_type: str = "im.message.receive_v1",
) -> dict:
    """构造飞书 v2.0 im.message.receive_v1 事件

    event_id 默认自动生成 UUID，避免跨测试在 PostgreSQL 去重表中的冲突。
    需要固定 event_id 的场景（如去重测试）显式传入。
    """
    if not event_id:
        event_id = f"evt_{uuid.uuid4().hex}"
    return {
        "header": {
            "event_id": event_id,
            "event_type": event_type,
            "create_time": "1700000000000",
            "token": "test_token",
            "app_id": "cli_test",
            "tenant_key": "tenant_test",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_sender_001", "user_id": "", "union_id": ""},
                "sender_type": "user",
            },
            "message": {
                "message_id": "msg_v2_test",
                "root_id": "",
                "parent_id": "",
                "create_time": "1700000000000",
                "chat_id": "oc_test",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "mentions": [],
            },
        },
    }


def _patch_factory(adapter):
    """Patch ChannelFactory.create_from_tenant_config 返回指定 adapter"""
    return patch(
        "src.saas.api.channel_routes.ChannelFactory.create_from_tenant_config",
        return_value=(adapter, "chan_feishu_test", "test-subagent"),
    )


def _patch_background():
    """Patch 后台处理函数，避免真实业务处理"""
    return patch(
        "src.saas.api.channel_routes._process_tenant_feishu_background",
        new_callable=AsyncMock,
    )


# ---------- 1. url_verification 返回 challenge ----------


class TestUrlVerification:
    def test_post_url_verification_returns_challenge(self, client, feishu_adapter):
        """POST url_verification 正确 token → 返回 challenge"""
        body = {
            "type": "url_verification",
            "token": "vt_test_token",
            "challenge": "challenge_abc123",
        }

        with _patch_factory(feishu_adapter):
            resp = client.post(
                "/t/tenant_test/feishu/callback/chan_feishu_test",
                json=body,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["challenge"] == "challenge_abc123"

    def test_post_url_verification_rejects_wrong_token(self, client, feishu_adapter):
        """POST url_verification 错误 token → 403"""
        body = {
            "type": "url_verification",
            "token": "WRONG_TOKEN",
            "challenge": "challenge_abc123",
        }

        with _patch_factory(feishu_adapter):
            resp = client.post(
                "/t/tenant_test/feishu/callback/chan_feishu_test",
                json=body,
            )

        assert resp.status_code == 403

    def test_encrypted_url_verification_returns_challenge(self, client, feishu_adapter_encrypted):
        """加密模式下，url_verification 也能正确返回 challenge"""
        # 构造明文 url_verification
        plain = {
            "type": "url_verification",
            "token": "vt_test_token",
            "challenge": "challenge_encrypted_001",
        }
        # 加密
        encrypted = feishu_adapter_encrypted.crypto.encrypt(plain)
        body = {"encrypt": encrypted}

        with _patch_factory(feishu_adapter_encrypted):
            resp = client.post(
                "/t/tenant_test/feishu/callback/chan_feishu_test",
                json=body,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["challenge"] == "challenge_encrypted_001"

    def test_encrypted_url_verification_rejects_wrong_token(self, client, feishu_adapter_encrypted):
        """加密模式下，url_verification token 错误 → 403"""
        plain = {
            "type": "url_verification",
            "token": "WRONG_TOKEN",
            "challenge": "challenge_encrypted_002",
        }
        encrypted = feishu_adapter_encrypted.crypto.encrypt(plain)
        body = {"encrypt": encrypted}

        with _patch_factory(feishu_adapter_encrypted):
            resp = client.post(
                "/t/tenant_test/feishu/callback/chan_feishu_test",
                json=body,
            )

        assert resp.status_code == 403


# ---------- 2. v2.0 签名校验 ----------


class TestSignatureVerification:
    def test_v2_event_signature_verified(self, client, feishu_adapter_encrypted):
        """v2.0 事件签名正确 → 放行"""
        event = _v2_event()
        body_str = json.dumps(event, ensure_ascii=False)

        # 计算签名: SHA256(timestamp + nonce + encrypt_key + body)
        # 使用当前时间戳，避免触发 ±1 小时偏差校验
        timestamp = str(int(time.time()))
        nonce = "test_nonce"
        encrypt_key = feishu_adapter_encrypted.encrypt_key
        content = timestamp + nonce + encrypt_key + body_str
        signature = hashlib.sha256(content.encode("utf-8")).hexdigest()

        with _patch_factory(feishu_adapter_encrypted), _patch_background():
            resp = client.post(
                "/t/tenant_test/feishu/callback/chan_feishu_test",
                content=body_str,
                headers={
                    "Content-Type": "application/json",
                    "X-Lark-Signature": signature,
                    "X-Lark-Request-Timestamp": timestamp,
                    "X-Lark-Request-Nonce": nonce,
                },
            )

        assert resp.status_code == 200

    def test_v2_event_signature_wrong_returns_403(self, client, feishu_adapter_encrypted):
        """v2.0 事件签名错误 → 403"""
        event = _v2_event()
        body_str = json.dumps(event, ensure_ascii=False)

        with _patch_factory(feishu_adapter_encrypted):
            resp = client.post(
                "/t/tenant_test/feishu/callback/chan_feishu_test",
                content=body_str,
                headers={
                    "Content-Type": "application/json",
                    "X-Lark-Signature": "invalid_signature_value",
                    "X-Lark-Request-Timestamp": "1700000000",
                    "X-Lark-Request-Nonce": "test_nonce",
                },
            )

        assert resp.status_code == 403

    def test_no_signature_header_passes_when_crypto_enabled(self, client, feishu_adapter_encrypted):
        """未提供 X-Lark-Signature 头时（非加密事件或测试环境），路由直接放行"""
        event = _v2_event()

        with _patch_factory(feishu_adapter_encrypted), _patch_background():
            resp = client.post(
                "/t/tenant_test/feishu/callback/chan_feishu_test",
                json=event,
                # 不带 X-Lark-Signature
            )

        # 无签名头 → 跳过签名校验，路由放行
        assert resp.status_code == 200


# ---------- 3. 事件去重 ----------


class TestEventDeduplication:
    @pytest.mark.asyncio
    async def test_event_deduplication_by_event_id(self, client, feishu_adapter):
        """5 分钟内重复 event_id 直接返回 200，不触发后台处理"""
        event = _v2_event(event_id="evt_dedup_001")

        with _patch_factory(feishu_adapter), _patch_background() as bg_mock:
            # 第一次：正常处理
            resp1 = client.post(
                "/t/tenant_test/feishu/callback/chan_feishu_test",
                json=event,
            )
            assert resp1.status_code == 200

            # 第二次：重复 event_id，应直接返回 200，不调用后台处理
            resp2 = client.post(
                "/t/tenant_test/feishu/callback/chan_feishu_test",
                json=event,
            )
            assert resp2.status_code == 200

            # 后台处理只应被调用 1 次（第二次被去重拦截）
            assert bg_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_different_event_ids_not_deduped(self, client, feishu_adapter):
        """不同 event_id 不被去重，各自触发后台处理"""
        event1 = _v2_event(event_id="evt_unique_001")
        event2 = _v2_event(event_id="evt_unique_002")

        with _patch_factory(feishu_adapter), _patch_background() as bg_mock:
            client.post("/t/tenant_test/feishu/callback/chan_feishu_test", json=event1)
            client.post("/t/tenant_test/feishu/callback/chan_feishu_test", json=event2)

            # 两次都触发后台处理
            assert bg_mock.call_count == 2


# ---------- 4. 非消息事件 ----------


class TestNonMessageEvent:
    def test_non_message_event_returns_200(self, client, feishu_adapter):
        """im.message.recalled_v1 等非消息事件返回 200，不触发后台处理"""
        event = _v2_event(event_type="im.message.recalled_v1")

        with _patch_factory(feishu_adapter), _patch_background() as bg_mock:
            resp = client.post(
                "/t/tenant_test/feishu/callback/chan_feishu_test",
                json=event,
            )

        assert resp.status_code == 200
        # 不应触发后台处理
        bg_mock.assert_not_called()

    def test_chat_disbanded_event_returns_200(self, client, feishu_adapter):
        """im.chat.disbanded_v1 事件返回 200"""
        event = _v2_event(event_type="im.chat.disbanded_v1")

        with _patch_factory(feishu_adapter), _patch_background() as bg_mock:
            resp = client.post(
                "/t/tenant_test/feishu/callback/chan_feishu_test",
                json=event,
            )

        assert resp.status_code == 200
        bg_mock.assert_not_called()


# ---------- 5. 路由立即返回 ----------


class TestImmediateResponse:
    @pytest.mark.asyncio
    async def test_route_responds_200_immediately(self, client, feishu_adapter):
        """消息处理走 asyncio.create_task 异步，路由立即返回 2xx"""
        event = _v2_event()

        with _patch_factory(feishu_adapter), _patch_background() as bg_mock:
            resp = client.post(
                "/t/tenant_test/feishu/callback/chan_feishu_test",
                json=event,
            )

        # 路由立即返回 200
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0

        # 后台处理被调度（通过 create_task，不一定立即执行完）
        # TestClient 是同步的，create_task 会在事件循环中运行
        # 给事件循环一点时间执行
        import asyncio
        await asyncio.sleep(0.1)

        # 后台处理应被调用
        bg_mock.assert_called_once()
        # 调用参数正确
        call_args = bg_mock.call_args
        assert call_args[0][0] == "tenant_test"  # tenant_id
        assert call_args[1].get("config_id") == "chan_feishu_test"
        assert call_args[1].get("subagent_type") == "test-subagent"


# ---------- 6. 配置缺失 ----------


class TestConfigNotFound:
    def test_missing_config_returns_404(self, client):
        """租户没有飞书配置时返回 404"""
        with patch(
            "src.saas.api.channel_routes.ChannelFactory.create_from_tenant_config",
            return_value=(None, None, None),
        ):
            resp = client.post(
                "/t/tenant_no_config/feishu/callback/chan_no_config",
                json=_v2_event(),
            )

        assert resp.status_code == 404


# ---------- 7. GET 兼容 ----------


class TestGetCallback:
    def test_get_callback_returns_ok(self, client):
        """GET 回调返回 200 + {"status": "ok"}（健康检查兜底，不回显 challenge）"""
        resp = client.get("/t/tenant_test/feishu/callback/chan_feishu_test")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
