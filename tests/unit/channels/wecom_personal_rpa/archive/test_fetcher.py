"""archive.fetcher 单元测试

覆盖：
- 配置不存在 → 静默跳过
- channel_type 非 wecom_personal_rpa → 跳过
- 凭证不完整 → 写错误状态
- listen_mode 非 server → 跳过（防御性）
- 锁竞争 → 跳过（不报错）
- 正常拉取：拉到密文 → 解密 → 构造 envelope → 调 _process_inbound_message → 推进 seq
- 单条解密失败：break，不推进当前 seq
- 45009 异常：写错误状态
- 空批次：清错误状态
- envelope 构造正确性（event_id / client_id 占位 / message_type 映射 / conversation 推断）
"""
import asyncio
import base64
import json
import secrets
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.wecom_personal_rpa.archive import fetcher as fetcher_module
from src.channels.wecom_personal_rpa.archive import chat_crypto, http_client
from src.channels.wecom_personal_rpa.archive.fetcher import ServerArchiveFetcher


# ----------------- 测试用 RSA 密钥 + AES 加密工具 -----------------


def _gen_rsa_pem() -> tuple[str, object]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return pem, private_key


def _aes_cbc_encrypt(random_key: bytes, iv: bytes, plaintext: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    pad_len = 32 - (len(plaintext) % 32)
    padded = plaintext + bytes([pad_len] * pad_len)
    cipher = Cipher(algorithms.AES(random_key[:32]), modes.CBC(iv))
    encryptor = cipher.encryptor()
    cipher_bytes = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + cipher_bytes).decode("ascii")


def _rsa_encrypt_pkcs1v15(public_key, plaintext: bytes) -> str:
    """模拟企微用公钥加密 random_key（RSA-PKCS1v15），返回 base64。

    企微官方明确要求 PKCS1（https://developer.work.weixin.qq.com/document/path/91774）。
    """
    from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding

    cipher = public_key.encrypt(plaintext, rsa_padding.PKCS1v15())
    return base64.b64encode(cipher).decode("ascii")


# ----------------- mock 工厂 -----------------


def _make_config_data(
    listen_mode: str = "server",
    corp_id: str = "ww_test_corp",
    archive_secret: str = "secret_xxx",
    private_key: str = "fake_pem",
    token: str = "token_xxx",
    encoding_aes_key: str = "aes_key_xxx",
    last_seq: int = 0,
    account_id: str = "acct_001",
    batch_limit: int = 1000,
) -> dict:
    return {
        "listen_mode": listen_mode,
        "corp_id": corp_id,
        "archive_secret": archive_secret,
        "private_key": private_key,
        "token": token,
        "encoding_aes_key": encoding_aes_key,
        "last_seq": last_seq,
        "batch_limit": batch_limit,
        "account_id": account_id,
    }


def _make_cfg_record(config_data: dict, channel_type: str = "wecom_personal_rpa") -> dict:
    return {
        "config_id": "chan_test_001",
        "tenant_id": "tenant_test",
        "channel_type": channel_type,
        "config": config_data,
        "subagent_type": "travel-consultant",
        "verified": 1,
    }


@pytest.fixture
def patched_lock(monkeypatch):
    """让 redis_client.acquire_lock 总是返回 True（获锁成功），release_lock 静默成功。"""
    monkeypatch.setattr(fetcher_module.redis_client, "acquire_lock", lambda *a, **kw: True)
    monkeypatch.setattr(fetcher_module.redis_client, "release_lock", lambda *a, **kw: True)


@pytest.fixture
def patched_process_msg(monkeypatch):
    """mock _process_inbound_message 避免触发整个 agent 主循环。"""
    async def _fake_process_msg(tenant_id, env, env_raw, source="client_callback"):
        # 记录调用以便断言
        patched_process_msg.calls.append({
            "tenant_id": tenant_id,
            "env": env,
            "env_raw": env_raw,
            "source": source,
        })
    patched_process_msg.calls = []
    # patch 入口（fetcher 在 _fetch_once_internal 内部延迟 import）
    monkeypatch.setattr(
        "src.saas.api.wecom_personal_rpa_routes._process_inbound_message",
        _fake_process_msg,
    )
    return patched_process_msg


# ----------------- 路径 1：前置校验 -----------------


@pytest.mark.asyncio
async def test_fetch_config_not_exist(patched_lock, monkeypatch):
    """配置不存在 → 静默跳过，不抛异常。"""
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: None
    )
    fetcher = ServerArchiveFetcher()
    # 不抛异常
    await fetcher.fetch_once("tenant_test", "chan_test_001")


@pytest.mark.asyncio
async def test_fetch_wrong_channel_type(patched_lock, monkeypatch):
    """channel_type 非 wecom_personal_rpa → 跳过。"""
    cfg = _make_cfg_record(_make_config_data(), channel_type="wecom_kf")
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )
    fetcher = ServerArchiveFetcher()
    await fetcher.fetch_once("tenant_test", "chan_test_001")


@pytest.mark.asyncio
async def test_fetch_credentials_incomplete(patched_lock, monkeypatch):
    """凭证不完整 → 写错误状态，不调 SDK。"""
    cfg = _make_cfg_record(_make_config_data(private_key=""))  # 缺私钥
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )
    mark_error_called = AsyncMock()
    fetcher_obj = ServerArchiveFetcher()
    monkeypatch.setattr(fetcher_obj, "_mark_error", mark_error_called)

    with patch.object(fetcher_module.http_client, "get_chat_data") as mock_chat_data:
        await fetcher_obj.fetch_once("tenant_test", "chan_test_001")
        mock_chat_data.assert_not_called()

    mark_error_called.assert_awaited()


@pytest.mark.asyncio
async def test_fetch_lock_not_acquired(monkeypatch):
    """锁竞争 → 跳过，不调 DB。"""
    monkeypatch.setattr(fetcher_module.redis_client, "acquire_lock", lambda *a, **kw: False)
    db_get = MagicMock()
    monkeypatch.setattr(fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", db_get)

    fetcher = ServerArchiveFetcher()
    await fetcher.fetch_once("tenant_test", "chan_test_001")
    db_get.assert_not_called()


# ----------------- 路径 2：正常拉取 -----------------


@pytest.mark.asyncio
async def test_fetch_empty_batch_clears_error(patched_lock, patched_process_msg, monkeypatch):
    """空批次 → 清错误状态，不调 _process_inbound_message。"""
    cfg = _make_cfg_record(_make_config_data())
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    with patch.object(fetcher_module.http_client, "get_chat_data", new=AsyncMock(return_value=http_client.ChatDataBatch(items=[]))) as mock_fetch:
        clear_error_called = AsyncMock()
        fetcher_obj = ServerArchiveFetcher()
        monkeypatch.setattr(fetcher_obj, "_clear_error", clear_error_called)
        await fetcher_obj.fetch_once("tenant_test", "chan_test_001")

    mock_fetch.assert_awaited_once()
    assert len(patched_process_msg.calls) == 0
    clear_error_called.assert_awaited()


@pytest.mark.asyncio
async def test_fetch_success_text_message(patched_lock, patched_process_msg, monkeypatch):
    """正常拉取一条 text 消息：解密 → 构造 envelope → 调 _process_inbound_message → 推进 seq。"""
    pem, private_key = _gen_rsa_pem()
    cfg = _make_cfg_record(_make_config_data(private_key=pem))
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    # 构造密文：random_key + plain_json
    random_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)
    plain_json = json.dumps({"text": {"content": "你好"}})
    encrypted_random_key = _rsa_encrypt_pkcs1v15(private_key.public_key(), random_key)
    encrypted_chat_msg = _aes_cbc_encrypt(random_key, iv, plain_json.encode("utf-8"))

    item = http_client.ChatDataItem(
        seq=1001,
        msg_id="msg_abc",
        action="upload",
        from_="user_a",
        tolist=["user_b"],
        roomid=None,
        msg_time=1700000000,
        msg_type="text",
        encrypt_random_key=encrypted_random_key,
        encrypt_chat_msg=encrypted_chat_msg,
    )
    batch = http_client.ChatDataBatch(items=[item])

    seq_updates = []
    # 真实签名：update_config_field(config_id, field_name, field_value)
    # mock 严格匹配签名，避免代码误传 tenant_id 被吞掉
    def _fake_update(cid, field, val):
        assert cid == "chan_test_001", f"config_id 应为 chan_test_001，实际 {cid}"
        seq_updates.append((field, val))
        return True
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB,
        "update_config_field",
        _fake_update,
    )

    with patch.object(fetcher_module.http_client, "get_chat_data", new=AsyncMock(return_value=batch)):
        fetcher_obj = ServerArchiveFetcher()
        await fetcher_obj.fetch_once("tenant_test", "chan_test_001")

    # 应调一次 _process_inbound_message，source=server_fetcher
    assert len(patched_process_msg.calls) == 1
    call = patched_process_msg.calls[0]
    assert call["source"] == "server_fetcher"
    assert call["tenant_id"] == "tenant_test"

    env = call["env"]
    assert env.event_id == "msg_msg_abc"  # 前缀 msg_ + archive msgid
    assert env.client_id == "_server_"     # 占位
    assert env.account_id == "acct_001"
    assert env.event_type == "message"

    payload = env.payload
    assert payload["conversation_id"] == "user_a_user_b"
    assert payload["conversation_type"] == "external_user"
    assert payload["sender_display_name"] == "user_a"
    assert payload["sender_stable_id"] == "user_a"
    assert payload["message_type"] == "text"
    assert payload["text"] == "你好"

    # 应推进 seq
    assert ("last_seq", 1001) in seq_updates


@pytest.mark.asyncio
async def test_fetch_room_message(patched_lock, patched_process_msg, monkeypatch):
    """群消息：conversation_id=roomid，conversation_type=external_group。"""
    pem, private_key = _gen_rsa_pem()
    cfg = _make_cfg_record(_make_config_data(private_key=pem))
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    random_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)
    plain_json = json.dumps({"text": {"content": "群消息"}})
    item = http_client.ChatDataItem(
        seq=2001, msg_id="msg_room", action="upload",
        from_="user_a", tolist=[], roomid="room_xxx",
        msg_time=1700000100, msg_type="text",
        encrypt_random_key=_rsa_encrypt_pkcs1v15(private_key.public_key(), random_key),
        encrypt_chat_msg=_aes_cbc_encrypt(random_key, iv, plain_json.encode("utf-8")),
    )

    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "update_config_field", lambda *a, **kw: True
    )

    with patch.object(fetcher_module.http_client, "get_chat_data", new=AsyncMock(return_value=http_client.ChatDataBatch(items=[item]))):
        fetcher_obj = ServerArchiveFetcher()
        await fetcher_obj.fetch_once("tenant_test", "chan_test_001")

    call = patched_process_msg.calls[0]
    payload = call["env"].payload
    assert payload["conversation_id"] == "room_xxx"
    assert payload["conversation_type"] == "external_group"


@pytest.mark.asyncio
async def test_fetch_message_type_mapping(patched_lock, patched_process_msg, monkeypatch):
    """未知 msgtype 归一为 link（与 C# MapMessageType 一致）。"""
    pem, private_key = _gen_rsa_pem()
    cfg = _make_cfg_record(_make_config_data(private_key=pem))
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    random_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)
    plain_json = json.dumps({})
    item = http_client.ChatDataItem(
        seq=3001, msg_id="msg_x", action="upload",
        from_="user_a", tolist=["user_b"], roomid=None,
        msg_time=1700000200, msg_type="unknown_type",  # 未知类型
        encrypt_random_key=_rsa_encrypt_pkcs1v15(private_key.public_key(), random_key),
        encrypt_chat_msg=_aes_cbc_encrypt(random_key, iv, plain_json.encode("utf-8")),
    )
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "update_config_field", lambda *a, **kw: True
    )

    with patch.object(fetcher_module.http_client, "get_chat_data", new=AsyncMock(return_value=http_client.ChatDataBatch(items=[item]))):
        fetcher_obj = ServerArchiveFetcher()
        await fetcher_obj.fetch_once("tenant_test", "chan_test_001")

    assert patched_process_msg.calls[0]["env"].payload["message_type"] == "link"


# ----------------- 路径 3：错误处理 -----------------


@pytest.mark.asyncio
async def test_fetch_decrypt_failure_break(patched_lock, patched_process_msg, monkeypatch):
    """单条解密失败 → break，已成功条目 seq 已推进，失败条目 seq 未推进。"""
    pem, private_key = _gen_rsa_pem()
    cfg = _make_cfg_record(_make_config_data(private_key=pem))
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    random_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)
    plain_json = json.dumps({"text": {"content": "ok"}})

    # 第 1 条正常
    good_item = http_client.ChatDataItem(
        seq=5001, msg_id="good", action="upload", from_="user_a", tolist=["user_b"],
        msg_time=1700000300, msg_type="text",
        encrypt_random_key=_rsa_encrypt_pkcs1v15(private_key.public_key(), random_key),
        encrypt_chat_msg=_aes_cbc_encrypt(random_key, iv, plain_json.encode("utf-8")),
    )
    # 第 2 条密文损坏
    bad_item = http_client.ChatDataItem(
        seq=5002, msg_id="bad", action="upload", from_="user_a", tolist=["user_b"],
        msg_time=1700000301, msg_type="text",
        encrypt_random_key="invalid!!!",
        encrypt_chat_msg="also_invalid!!!",
    )

    batch = http_client.ChatDataBatch(items=[good_item, bad_item])

    seq_updates = []
    # 真实签名：update_config_field(config_id, field_name, field_value)
    def _fake_update(cid, field, val):
        assert cid == "chan_test_001", f"config_id 应为 chan_test_001，实际 {cid}"
        seq_updates.append((field, val))
        return True
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB,
        "update_config_field",
        _fake_update,
    )

    with patch.object(fetcher_module.http_client, "get_chat_data", new=AsyncMock(return_value=batch)):
        fetcher_obj = ServerArchiveFetcher()
        await fetcher_obj.fetch_once("tenant_test", "chan_test_001")

    # 只处理了第 1 条
    assert len(patched_process_msg.calls) == 1
    assert ("last_seq", 5001) in seq_updates
    assert ("last_seq", 5002) not in seq_updates  # 失败条目 seq 未推进


@pytest.mark.asyncio
async def test_fetch_45029_marks_error(patched_lock, patched_process_msg, monkeypatch):
    """45009 频率限制 → 标记错误，不调 _process_inbound_message。"""
    pem, private_key = _gen_rsa_pem()
    cfg = _make_cfg_record(_make_config_data(private_key=pem))
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    mark_error_called = AsyncMock()
    fetcher_obj = ServerArchiveFetcher()
    monkeypatch.setattr(fetcher_obj, "_mark_error", mark_error_called)

    with patch.object(fetcher_module.http_client, "get_chat_data", new=AsyncMock(side_effect=http_client.WeComRateLimitException(60))):
        await fetcher_obj.fetch_once("tenant_test", "chan_test_001")

    assert len(patched_process_msg.calls) == 0
    mark_error_called.assert_awaited()


# ----------------- _build_envelope 单独测试（不依赖拉取） -----------------


def test_build_envelope_text_message():
    """envelope 构造正确性（不依赖 RSA/AES）。"""
    fetcher_obj = ServerArchiveFetcher()
    item = http_client.ChatDataItem(
        seq=100, msg_id="msg_test", action="upload",
        from_="external_userid_123", tolist=["user_internal"],
        roomid=None, msg_time=1700000000, msg_type="text",
        encrypt_random_key="", encrypt_chat_msg="",
    )
    plain = json.dumps({"text": {"content": "hello world"}})

    env, env_raw = fetcher_obj._build_envelope("acct_001", item, plain)

    assert env.event_id == "msg_msg_test"
    assert env.client_id == "_server_"
    assert env.account_id == "acct_001"
    assert env.event_type == "message"
    assert env.payload["text"] == "hello world"
    assert env.payload["message_type"] == "text"
    assert env.payload["conversation_id"] == "external_userid_123_user_internal"
    assert env.payload["conversation_type"] == "external_user"


def test_build_envelope_media_message():
    """媒体消息：sdkfileid 透传到 attachments[0].sdk_file_id。"""
    fetcher_obj = ServerArchiveFetcher()
    item = http_client.ChatDataItem(
        seq=200, msg_id="img_001", action="upload",
        from_="user_a", tolist=["user_b"], roomid=None,
        msg_time=1700000000, msg_type="image",
        encrypt_random_key="", encrypt_chat_msg="",
    )
    plain = json.dumps({"image": {"sdkfileid": "sdk_file_id_xxx", "filename": "test.jpg"}})

    env, env_raw = fetcher_obj._build_envelope("acct_001", item, plain)

    assert env.payload["message_type"] == "image"
    assert env.payload["text"] == ""  # 非文本消息 text 为空
    assert len(env.payload["attachments"]) == 1
    att = env.payload["attachments"][0]
    assert att["type"] == "image"
    assert att["sdk_file_id"] == "sdk_file_id_xxx"
    assert att["name"] == "test.jpg"
    assert att["url"] == ""  # URL 留空，客户端下载


def test_build_envelope_invalid_json():
    """明文非合法 JSON 时降级为空 dict，text 为空，不抛异常。"""
    fetcher_obj = ServerArchiveFetcher()
    item = http_client.ChatDataItem(
        seq=300, msg_id="x", action="upload",
        from_="u", tolist=["v"], msg_time=1700000000, msg_type="text",
        encrypt_random_key="", encrypt_chat_msg="",
    )
    env, env_raw = fetcher_obj._build_envelope("acct", item, "not valid json")
    assert env.payload["text"] == ""
