"""端到端集成测试：企微回调 → callback_handler → fetcher → _process_inbound_message

Phase 11 补强：验证完整 server 模式链路在 mock 企微 API + Redis + DB 的场景下能跑通，
且各 audit 事件被正确触发。

不依赖真实企微服务，所有外部调用 mock。
"""
import asyncio
import base64
import json
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.wecom.crypto import WeComCrypto
from src.channels.wecom_personal_rpa.archive import (
    audit as archive_audit,
    callback_handler,
    fetcher as fetcher_module,
    http_client,
)
from src.channels.wecom_personal_rpa.archive.fetcher import ServerArchiveFetcher


TEST_TOKEN = "QDG6eK"
TEST_AES_KEY = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"
TEST_CORP_ID = "wx5823bf96d3bd56c7"


def _make_crypto() -> WeComCrypto:
    return WeComCrypto(TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID)


def _gen_rsa_pem():
    """生成 RSA 密钥对，返回 (PEM 字符串, 私钥对象)。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = pk.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return pem, pk


def _encrypt_chat_msg(random_key: bytes, plain: bytes) -> str:
    """模拟企微 AES-CBC 加密 chat_msg。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = secrets.token_bytes(16)
    pad_len = 32 - (len(plain) % 32)
    padded = plain + bytes([pad_len] * pad_len)
    cipher = Cipher(algorithms.AES(random_key[:32]), modes.CBC(iv))
    enc = cipher.encryptor()
    return base64.b64encode(iv + enc.update(padded) + enc.finalize()).decode("ascii")


def _rsa_encrypt_oaep_sha1(public_key, plain: bytes) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding

    return base64.b64encode(
        public_key.encrypt(
            plain,
            rsa_padding.OAEP(
                mgf=rsa_padding.MGF1(algorithm=hashes.SHA1()),
                algorithm=hashes.SHA1(),
                label=None,
            ),
        )
    ).decode("ascii")


@pytest.fixture
def patched_lock(monkeypatch):
    """让 Redis 分布式锁总是获锁成功。"""
    monkeypatch.setattr(
        fetcher_module.redis_client, "acquire_lock", lambda *a, **kw: True
    )
    monkeypatch.setattr(
        fetcher_module.redis_client, "release_lock", lambda *a, **kw: True
    )


@pytest.fixture
def patched_process_msg(monkeypatch):
    """mock _process_inbound_message 避免触发整个 agent 主循环。"""
    calls = []

    async def _fake(tenant_id, env, env_raw, source="client_callback"):
        calls.append({"tenant_id": tenant_id, "env": env, "source": source})

    monkeypatch.setattr(
        "src.saas.api.wecom_personal_rpa_routes._process_inbound_message", _fake
    )
    return calls


# ----------------- 完整链路 e2e -----------------


@pytest.mark.asyncio
async def test_full_chain_callback_to_message_processing(
    patched_lock, patched_process_msg, monkeypatch
):
    """完整链路：构造企微回调 → callback_handler.handle_archive_event →
    fetcher.fetch_once → 解密 → _process_inbound_message。

    覆盖 audit 事件触发：callback_received + fetch_success。
    """
    # 准备：RSA 私钥 + 加密一条 text 消息
    pem, private_key_obj = _gen_rsa_pem()
    random_key = secrets.token_bytes(32)
    plain_msg = json.dumps({"text": {"content": "hello e2e"}})
    encrypted_random_key = _rsa_encrypt_oaep_sha1(private_key_obj.public_key(), random_key)
    encrypted_chat_msg = _encrypt_chat_msg(random_key, plain_msg.encode("utf-8"))

    item = http_client.ChatDataItem(
        seq=9001, msg_id="msg_e2e_001", action="upload",
        from_="user_e2e_a", tolist=["user_e2e_b"], roomid=None,
        msg_time=1700000000, msg_type="text",
        encrypt_random_key=encrypted_random_key,
        encrypt_chat_msg=encrypted_chat_msg,
    )
    batch = http_client.ChatDataBatch(items=[item])

    # mock ChannelConfigDB 返回含明文凭证的配置
    cfg = {
        "config_id": "chan_e2e",
        "tenant_id": "t_e2e",
        "channel_type": "wecom_personal_rpa",
        "config": {
            "listen_mode": "server",
            "corp_id": TEST_CORP_ID,
            "archive_secret": "secret_e2e",
            "private_key": pem,
            "token": TEST_TOKEN,
            "encoding_aes_key": TEST_AES_KEY,
            "last_seq": 9000,
            "batch_limit": 100,
            "account_id": "acct_e2e",
        },
        "verified": 1,
    }
    monkeypatch.setattr(
        callback_handler.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "update_config_field", lambda *a, **kw: True
    )

    # mock 企微 API（get_chat_data 走 C SDK，此处 mock http_client 层）
    with patch.object(http_client, "get_chat_data", new=AsyncMock(return_value=batch)):
        # mock audit 写入（仅断言调用，不触达真实 DB）
        audit_calls = []
        orig_log_callback_received = archive_audit.log_callback_received
        orig_log_fetch_success = archive_audit.log_fetch_success

        def _spy_callback_received(tenant_id, config_id, verify_ok, detail=""):
            audit_calls.append(("callback_received", tenant_id, config_id, verify_ok))
            orig_log_callback_received(tenant_id, config_id, verify_ok, detail)

        def _spy_fetch_success(tenant_id, config_id, source, batch_size, processed, last_seq, account_id=None):
            audit_calls.append(("fetch_success", tenant_id, config_id, batch_size, processed, last_seq))
            orig_log_fetch_success(tenant_id, config_id, source, batch_size, processed, last_seq, account_id)

        monkeypatch.setattr(archive_audit, "log_callback_received", _spy_callback_received)
        monkeypatch.setattr(archive_audit, "log_fetch_success", _spy_fetch_success)
        # fetcher 内部 import audit，需要 patch fetcher 模块里的 audit 引用
        monkeypatch.setattr(fetcher_module.archive_audit, "log_callback_received", _spy_callback_received)
        monkeypatch.setattr(fetcher_module.archive_audit, "log_fetch_success", _spy_fetch_success)
        monkeypatch.setattr(callback_handler.archive_audit, "log_callback_received", _spy_callback_received)

        # Step 1: 构造企微回调 POST event
        crypto = _make_crypto()
        event_xml = "<xml><Event>chat_update</Event></xml>"
        encrypted_event = crypto.encrypt(event_xml)
        sig = crypto.generate_signature("e2e_ts", "e2e_nonce", encrypted_event)

        body = f"<xml><Encrypt><![CDATA[{encrypted_event}]]></Encrypt></xml>".encode("utf-8")
        req = MagicMock()
        req.headers = {"content-type": "application/xml"}
        req.query_params = {
            "msg_signature": sig, "timestamp": "e2e_ts", "nonce": "e2e_nonce"
        }

        # Step 2: 触发 callback_handler
        resp = await callback_handler.handle_archive_event("t_e2e", "chan_e2e", req, body)
        assert resp.status_code == 200

        # Step 3: 等异步 fetcher 完成（create_task 在 callback_handler 内部）
        # 由于我们直接调用 handler 而非路由，asyncio.create_task 会在当前 event loop 跑
        await asyncio.sleep(0.1)

    # 断言：_process_inbound_message 被调用，source=server_fetcher
    assert len(patched_process_msg) == 1
    call = patched_process_msg[0]
    assert call["source"] == "server_fetcher"
    env = call["env"]
    assert env.event_id == "msg_msg_e2e_001"
    assert env.payload["text"] == "hello e2e"
    assert env.payload["message_type"] == "text"

    # 断言：audit 事件触发
    callback_audit = [c for c in audit_calls if c[0] == "callback_received"]
    fetch_audit = [c for c in audit_calls if c[0] == "fetch_success"]
    assert len(callback_audit) == 1
    assert callback_audit[0][3] is True  # verify_ok
    assert len(fetch_audit) == 1
    assert fetch_audit[0][4] == 1  # processed=1
    assert fetch_audit[0][5] == 9001  # last_seq


@pytest.mark.asyncio
async def test_callback_verify_failed_writes_failure_audit(
    patched_lock, patched_process_msg, monkeypatch
):
    """验签失败时写 archive_callback_verify_failed audit，不触发 fetcher。"""
    cfg = {
        "config_id": "chan_e2e_2",
        "tenant_id": "t_e2e",
        "channel_type": "wecom_personal_rpa",
        "config": {
            "listen_mode": "server",
            "corp_id": TEST_CORP_ID,
            "archive_secret": "secret",
            "private_key": "fake",
            "token": TEST_TOKEN,
            "encoding_aes_key": TEST_AES_KEY,
        },
        "verified": 1,
    }
    monkeypatch.setattr(
        callback_handler.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    audit_calls = []
    monkeypatch.setattr(
        callback_handler.archive_audit, "log_callback_received",
        lambda tid, cid, verify_ok, detail="": audit_calls.append((tid, cid, verify_ok))
    )

    # 构造验签失败的 POST
    body = b"<xml><Encrypt><![CDATA[fake_encrypt_data]]></Encrypt></xml>"
    req = MagicMock()
    req.headers = {"content-type": "application/xml"}
    req.query_params = {
        "msg_signature": "0" * 40,  # 篡改签名
        "timestamp": "x", "nonce": "y"
    }

    resp = await callback_handler.handle_archive_event("t_e2e", "chan_e2e_2", req, body)
    assert resp.status_code == 401
    assert len(patched_process_msg) == 0  # 未触发 fetcher
    assert len(audit_calls) == 1
    assert audit_calls[0][2] is False  # verify_ok=False
