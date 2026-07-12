"""archive.fetcher 单元测试

覆盖：
- 配置不存在 → 静默跳过
- channel_type 非 wecom_personal_rpa → 跳过
- 凭证不完整 → 写错误状态
- listen_mode 非 server → 跳过（防御性）
- 锁竞争 → 跳过（不报错）
- 正常拉取：拉到密文 → RSA 解 random_key → SDK DecryptData → 构造 envelope → 调 _process_inbound_message → 推进 seq
- 单条解密失败：跳过该条并推进 seq（不卡死整个租户死循环重拉）
- 45009 异常：写错误状态
- 空批次：清错误状态
- envelope 构造正确性（event_id / client_id 占位 / message_type 映射 / conversation 推断）

注意：fetcher 内部调 ``chat_crypto.decrypt_random_key``（RSA）+ ``wecom_finance_sdk.decrypt_data_raw``
（SDK 内部 base64+AES）。测试中 mock 这两个函数，避免依赖真实 RSA 密钥与真实 SDK。
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.wecom_personal_rpa.archive import fetcher as fetcher_module
from src.channels.wecom_personal_rpa.archive import chat_crypto, http_client
from src.channels.wecom_personal_rpa.archive.fetcher import ServerArchiveFetcher

_ORIGINAL_ENSURE_ACCOUNT_MAPPING = ServerArchiveFetcher._ensure_account_mapping


# ----------------- 测试用 RSA 密钥（仅用于构造合法私钥 PEM，加解密都 mock 掉） -----------------


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
    monkeypatch.setattr(
        ServerArchiveFetcher,
        "_ensure_account_mapping",
        staticmethod(lambda tenant_id, cfg, account_id: "client_001"),
    )
    inbox = []
    def _enqueue(tenant_id, config_id, event_id, envelope_json):
        inbox.append({"id": len(inbox) + 1, "tenant_id": tenant_id, "claim_token": "claim_test",
                      "event_id": event_id, "envelope": json.loads(envelope_json)})
        return True
    def _claim(tenant_id, limit=20):
        rows, inbox[:] = list(inbox), []
        return rows
    monkeypatch.setattr(fetcher_module.rpa_db, "enqueue_inbound_archive_message", _enqueue)
    monkeypatch.setattr(fetcher_module.rpa_db, "claim_archive_inbox", _claim)
    monkeypatch.setattr(fetcher_module.rpa_db, "mark_archive_inbox", lambda *a, **kw: None)
    monkeypatch.setattr(fetcher_module.rpa_db, "heartbeat_archive_inbox", lambda *a: True)


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
    """正常拉取一条 text 消息：RSA 解 random_key → SDK DecryptData → 构造 envelope → 调 _process_inbound_message → 推进 seq。"""
    pem, private_key = _gen_rsa_pem()
    cfg = _make_cfg_record(_make_config_data(private_key=pem))
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    # mock RSA 解密：返回固定 random_key bytes（不需要真的可解码，只是占位）
    fake_random_key = b"fake_random_key_bytes_pad_to_32!!"  # 32 字节占位
    monkeypatch.setattr(
        fetcher_module.chat_crypto,
        "decrypt_random_key",
        lambda priv_key, enc_key: fake_random_key,
    )
    # mock SDK DecryptData：直接返回构造好的明文 JSON（不真的走 SDK）
    plain_json = json.dumps({"text": {"content": "你好"}})
    decrypt_seen = {}

    def _fake_decrypt_data(encrypt_key, encrypt_msg, timeout_seconds=8):
        decrypt_seen["timeout_seconds"] = timeout_seconds
        return plain_json

    monkeypatch.setattr(
        fetcher_module.wecom_finance_sdk,
        "decrypt_data_raw",
        _fake_decrypt_data,
    )

    item = http_client.ChatDataItem(
        seq=1001,
        msg_id="msg_abc",
        action="upload",
        from_="user_a",
        tolist=["user_b"],
        roomid=None,
        msg_time=1700000000,
        msg_type="text",
        encrypt_random_key="encrypted_random_key_placeholder",  # 内容无所谓，被 mock
        encrypt_chat_msg="encrypted_chat_msg_placeholder",      # 内容无所谓，被 mock
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
    assert env.client_id == "client_001"
    assert env.account_id == ServerArchiveFetcher._infer_account_id("tenant_test", cfg)
    assert env.event_type == "message"

    payload = env.payload
    assert payload["conversation_id"] == "user_a_user_b"
    assert payload["conversation_type"] == "external_user"
    assert payload["sender_display_name"] == "user_a"
    assert payload["sender_stable_id"] == "user_a"
    assert payload["message_type"] == "text"
    assert payload["text"] == "你好"

    # 关键意图：fetcher 必须把 SDK DecryptData 的超时放到子进程代理层，
    # 不能只依赖外层 asyncio.wait_for。
    assert decrypt_seen["timeout_seconds"] == fetcher_module._SDK_DECRYPT_TIMEOUT_SECONDS

    # 应推进 seq
    assert ("last_seq", 1001) in seq_updates


@pytest.mark.asyncio
async def test_fetch_uses_decrypted_plain_fields_when_outer_item_lacks_metadata(
    patched_lock, patched_process_msg, monkeypatch
):
    """真实企微 GetChatData 外层可能没有 msgtype/from/tolist/msgtime，必须从 DecryptData 明文取。"""
    pem, private_key = _gen_rsa_pem()
    cfg = _make_cfg_record(_make_config_data(private_key=pem))
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )
    monkeypatch.setattr(
        fetcher_module.chat_crypto,
        "decrypt_random_key",
        lambda priv_key, enc_key: b"fake_random_key_bytes_pad_to_32!!",
    )
    plain_json = json.dumps(
        {
            "msgid": "plain_msg_001",
            "action": "send",
            "from": "wm_sender",
            "tolist": ["wm_peer"],
            "roomid": "",
            "msgtime": 1700000000123,
            "msgtype": "text",
            "text": {"content": "服务端明文消息"},
        }
    )
    monkeypatch.setattr(
        fetcher_module.wecom_finance_sdk,
        "decrypt_data_raw",
        lambda encrypt_key, encrypt_msg, timeout_seconds=8: plain_json,
    )

    item = http_client.ChatDataItem(
        seq=1002,
        msg_id="outer_msg_001",
        action="",
        from_="",
        tolist=[],
        roomid=None,
        msg_time=0,
        msg_type="",
        encrypt_random_key="placeholder",
        encrypt_chat_msg="placeholder",
    )
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "update_config_field", lambda *a, **kw: True
    )

    with patch.object(
        fetcher_module.http_client,
        "get_chat_data",
        new=AsyncMock(return_value=http_client.ChatDataBatch(items=[item])),
    ):
        fetcher_obj = ServerArchiveFetcher()
        await fetcher_obj.fetch_once("tenant_test", "chan_test_001")

    assert len(patched_process_msg.calls) == 1
    env = patched_process_msg.calls[0]["env"]
    payload = env.payload
    assert env.event_id == "msg_outer_msg_001"
    assert payload["conversation_id"] == "wm_sender_wm_peer"
    assert payload["conversation_type"] == "external_user"
    assert payload["sender_display_name"] == "wm_sender"
    assert payload["sender_stable_id"] == "wm_sender"
    assert payload["message_type"] == "text"
    assert payload["text"] == "服务端明文消息"
    assert env.occurred_at.timestamp() == pytest.approx(1700000000.123)


@pytest.mark.asyncio
async def test_fetch_room_message(patched_lock, patched_process_msg, monkeypatch):
    """群消息：conversation_id=roomid，conversation_type=external_group。"""
    pem, private_key = _gen_rsa_pem()
    cfg = _make_cfg_record(_make_config_data(private_key=pem))
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    monkeypatch.setattr(
        fetcher_module.chat_crypto,
        "decrypt_random_key",
        lambda priv_key, enc_key: b"fake_random_key_bytes_pad_to_32!!",
    )
    plain_json = json.dumps({"text": {"content": "群消息"}})
    monkeypatch.setattr(
        fetcher_module.wecom_finance_sdk,
        "decrypt_data_raw",
        lambda encrypt_key, encrypt_msg, timeout_seconds=8: plain_json,
    )

    item = http_client.ChatDataItem(
        seq=2001, msg_id="msg_room", action="upload",
        from_="user_a", tolist=[], roomid="room_xxx",
        msg_time=1700000100, msg_type="text",
        encrypt_random_key="placeholder",
        encrypt_chat_msg="placeholder",
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

    monkeypatch.setattr(
        fetcher_module.chat_crypto,
        "decrypt_random_key",
        lambda priv_key, enc_key: b"fake_random_key_bytes_pad_to_32!!",
    )
    plain_json = json.dumps({})
    monkeypatch.setattr(
        fetcher_module.wecom_finance_sdk,
        "decrypt_data_raw",
        lambda encrypt_key, encrypt_msg, timeout_seconds=8: plain_json,
    )

    item = http_client.ChatDataItem(
        seq=3001, msg_id="msg_x", action="upload",
        from_="user_a", tolist=["user_b"], roomid=None,
        msg_time=1700000200, msg_type="unknown_type",  # 未知类型
        encrypt_random_key="placeholder",
        encrypt_chat_msg="placeholder",
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
async def test_fetch_decrypt_failure_skips_and_advances(patched_lock, patched_process_msg, monkeypatch):
    """单条解密失败 → 跳过该条并推进 seq（不卡死整个租户死循环重拉），已成功条目也已推进 seq。"""
    pem, private_key = _gen_rsa_pem()
    cfg = _make_cfg_record(_make_config_data(private_key=pem))
    monkeypatch.setattr(
        fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    # mock RSA 解密：第 2 条抛错（模拟 SDK DecryptData 失败或 RSA 失败）
    plain_json_good = json.dumps({"text": {"content": "ok"}})
    call_count = {"n": 0}

    def _fake_decrypt_random_key(priv_key, enc_key):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # 第 2 条模拟密文损坏
            raise ValueError("RSA 解密失败: 模拟损坏密文")
        return b"fake_random_key_bytes_pad_to_32!!"

    def _fake_decrypt_data(encrypt_key, encrypt_msg, timeout_seconds=8):
        return plain_json_good

    monkeypatch.setattr(fetcher_module.chat_crypto, "decrypt_random_key", _fake_decrypt_random_key)
    monkeypatch.setattr(fetcher_module.wecom_finance_sdk, "decrypt_data_raw", _fake_decrypt_data)

    # 第 1 条正常
    good_item = http_client.ChatDataItem(
        seq=5001, msg_id="good", action="upload", from_="user_a", tolist=["user_b"],
        msg_time=1700000300, msg_type="text",
        encrypt_random_key="placeholder1",
        encrypt_chat_msg="placeholder1",
    )
    # 第 2 条会让 mock 的 decrypt_random_key 抛错
    bad_item = http_client.ChatDataItem(
        seq=5002, msg_id="bad", action="upload", from_="user_a", tolist=["user_b"],
        msg_time=1700000301, msg_type="text",
        encrypt_random_key="placeholder2",
        encrypt_chat_msg="placeholder2",
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

    # 失败条目已跳过：_process_inbound_message 只被调用 1 次（成功的第 1 条）
    assert len(patched_process_msg.calls) == 1
    # 失败条目的 seq 也推进了（避免坏消息卡死整个租户死循环重拉）
    assert ("last_seq", 5001) in seq_updates
    assert ("last_seq", 5002) in seq_updates  # 失败条目也推进 seq


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

    env, env_raw = fetcher_obj._build_envelope("acct_001", "client_001", item, plain)

    assert env.event_id == "msg_msg_test"
    assert env.client_id == "client_001"
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

    env, env_raw = fetcher_obj._build_envelope("acct_001", "client_001", item, plain)

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
    env, env_raw = fetcher_obj._build_envelope("acct", "client_001", item, "not valid json")
    assert env.payload["text"] == ""


def test_internal_account_id_is_stable_and_tenant_namespaced():
    cfg = _make_cfg_record(_make_config_data(account_id="travel-consultant"))

    first = ServerArchiveFetcher._infer_account_id("tenant_a", cfg)
    assert first == ServerArchiveFetcher._infer_account_id("tenant_a", cfg)
    assert first.startswith("rpa_acct_")
    assert first != ServerArchiveFetcher._infer_account_id("tenant_b", cfg)


def test_internal_account_id_falls_back_to_config_not_subagent():
    cfg = _make_cfg_record(_make_config_data(account_id=""))
    cfg["subagent_type"] = "travel-consultant"

    derived = ServerArchiveFetcher._infer_account_id("tenant_a", cfg)
    cfg["subagent_type"] = "another-agent"
    assert derived == ServerArchiveFetcher._infer_account_id("tenant_a", cfg)

    cfg["config_id"] = "another_config"
    assert derived != ServerArchiveFetcher._infer_account_id("tenant_a", cfg)


def test_account_mapping_uses_only_active_same_tenant_configured_client(monkeypatch):
    from src.channels.wecom_personal_rpa import db as rpa_db

    monkeypatch.setattr(rpa_db, "get_account", lambda account_id: None)
    monkeypatch.setattr(
        rpa_db, "get_client",
        lambda client_id: {"id": client_id, "tenant_id": "tenant_test", "status": "active"},
    )
    upsert = MagicMock(return_value={"id": "acct_001"})
    monkeypatch.setattr(rpa_db, "upsert_account", upsert)

    assert _ORIGINAL_ENSURE_ACCOUNT_MAPPING(
        "tenant_test", {"config": {"client_id": "client_configured"}}, "acct_001"
    ) == "client_configured"
    assert upsert.call_args.kwargs["client_id"] == "client_configured"


def test_account_mapping_rejects_cross_tenant_client(monkeypatch):
    from src.channels.wecom_personal_rpa import db as rpa_db

    monkeypatch.setattr(rpa_db, "get_account", lambda account_id: None)
    monkeypatch.setattr(
        rpa_db, "get_client",
        lambda client_id: {"id": client_id, "tenant_id": "other", "status": "active"},
    )
    upsert = MagicMock()
    monkeypatch.setattr(rpa_db, "upsert_account", upsert)

    assert _ORIGINAL_ENSURE_ACCOUNT_MAPPING(
        "tenant_test", {"config": {"client_id": "client_other"}}, "acct_001"
    ) is None
    upsert.assert_not_called()


# ----------------- inbox 解耦与恢复专项 -----------------


@pytest.mark.asyncio
async def test_drain_does_not_wait_for_slow_agent_over_30_seconds(monkeypatch):
    """领取后只调度 worker；Agent 即使远超 fetch 超时也不能阻塞 fetcher。"""
    gate = asyncio.Event()
    row = {"id": 1, "tenant_id": "tenant_test", "event_id": "msg_slow",
           "envelope": {}, "claim_token": "lease_1"}
    monkeypatch.setattr(fetcher_module.rpa_db, "claim_archive_inbox", lambda *_: [row])
    fetcher_obj = ServerArchiveFetcher()
    async def _slow_worker(_row):
        await gate.wait()
    worker = AsyncMock(side_effect=_slow_worker)
    monkeypatch.setattr(fetcher_obj, "_process_inbox_row", worker)

    await asyncio.wait_for(fetcher_obj._drain_inbox("tenant_test"), timeout=0.2)
    worker.assert_called_once_with(row)
    # 清理测试创建的后台任务，避免形成孤儿。
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task() and not task.done():
            task.cancel()


@pytest.mark.asyncio
async def test_cancelled_inbox_worker_is_marked_retryable(monkeypatch):
    """进程关闭取消 worker 时持久化 retryable，重启后可恢复。"""
    gate = asyncio.Event()
    async def _slow(*args, **kwargs):
        await gate.wait()
    monkeypatch.setattr(
        "src.saas.api.wecom_personal_rpa_routes._process_inbound_message", _slow,
    )
    marks = []
    monkeypatch.setattr(fetcher_module.rpa_db, "mark_archive_inbox",
                        lambda *args: marks.append(args) or True)
    row = {"id": 7, "tenant_id": "tenant_test", "event_id": "msg_cancel",
           "envelope": {"event_id": "msg_cancel", "event_type": "message",
                        "client_id": "client_001", "account_id": "acct_001",
                        "occurred_at": "2026-07-12T00:00:00Z", "payload": {}},
           "claim_token": "lease_cancel"}
    task = asyncio.create_task(ServerArchiveFetcher()._process_inbox_row(row))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert marks == [(7, "lease_cancel", "retryable", "worker_cancelled")]


def test_duplicate_event_id_enqueue_uses_database_unique_constraint():
    """重复 msgid 的写入必须依赖租户+event 唯一键幂等。"""
    sql = (fetcher_module.rpa_db.enqueue_inbound_archive_message.__doc__ or "")
    assert "重复 event_id" in sql
    migration = open("deploy/db_update.sql", encoding="utf-8").read()
    assert "UNIQUE (tenant_id, event_id)" in migration
    assert "ON CONFLICT (tenant_id, event_id) DO NOTHING" in __import__(
        "inspect"
    ).getsource(fetcher_module.rpa_db.enqueue_inbound_archive_message)


@pytest.mark.asyncio
async def test_enqueue_failure_does_not_advance_seq(patched_lock, monkeypatch):
    """PG inbox 不可写时必须保留原游标，下一轮重拉同一消息。"""
    cfg = _make_cfg_record(_make_config_data())
    monkeypatch.setattr(fetcher_module.ChannelConfigDB, "get_by_tenant_and_id", lambda *_: cfg)
    monkeypatch.setattr(fetcher_module.chat_crypto, "decrypt_random_key", lambda *_: b"key")
    monkeypatch.setattr(fetcher_module.wecom_finance_sdk, "decrypt_data_raw",
                        lambda *a, **kw: json.dumps({"text": {"content": "retry"}}))
    monkeypatch.setattr(fetcher_module.rpa_db, "enqueue_inbound_archive_message",
                        MagicMock(side_effect=RuntimeError("postgres unavailable")))
    updates = []
    monkeypatch.setattr(fetcher_module.ChannelConfigDB, "update_config_field",
                        lambda *args: updates.append(args) or True)
    item = http_client.ChatDataItem(
        seq=88, msg_id="enqueue_fail", action="send", from_="a", tolist=["b"],
        msg_time=1700000000, msg_type="text", encrypt_random_key="x",
        encrypt_chat_msg="y",
    )
    monkeypatch.setattr(fetcher_module.http_client, "get_chat_data", AsyncMock(
        return_value=http_client.ChatDataBatch(items=[item])))

    await ServerArchiveFetcher().fetch_once("tenant_test", "chan_test_001")

    assert not any(field == "last_seq" for _, field, _ in updates)


@pytest.mark.asyncio
async def test_slow_worker_renews_lease_until_completion(monkeypatch):
    """慢 Agent 运行期间周期续租，避免五分钟回收产生并行重复处理。"""
    monkeypatch.setattr(fetcher_module, "_INBOX_HEARTBEAT_SECONDS", 0.001)
    renewals = []
    monkeypatch.setattr(fetcher_module.rpa_db, "heartbeat_archive_inbox",
                        lambda *args: renewals.append(args) or True)
    heartbeat = asyncio.create_task(ServerArchiveFetcher()._heartbeat_inbox_row(
        {"id": 9, "claim_token": "lease_slow", "event_id": "msg_slow"}))
    for _ in range(100):
        if len(renewals) >= 2:
            break
        await asyncio.sleep(0.005)
    heartbeat.cancel()
    with pytest.raises(asyncio.CancelledError):
        await heartbeat
    assert len(renewals) >= 2
    assert set(renewals) == {(9, "lease_slow")}


@pytest.mark.asyncio
async def test_shutdown_cancels_and_awaits_registered_workers(monkeypatch):
    """shutdown 必须等待 worker 的 CancelledError 清理完成，而非遗留孤儿任务。"""
    cancelled = asyncio.Event()
    async def _worker(_row):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    fetcher_obj = ServerArchiveFetcher()
    monkeypatch.setattr(fetcher_obj, "_process_inbox_row", _worker)
    monkeypatch.setattr(fetcher_module.rpa_db, "claim_archive_inbox", lambda *_: [
        {"id": 10, "tenant_id": "tenant_test", "claim_token": "lease_shutdown"}
    ])
    await fetcher_obj._drain_inbox("tenant_test")

    await fetcher_obj.shutdown()

    assert cancelled.is_set()
    assert not fetcher_obj._worker_tasks
