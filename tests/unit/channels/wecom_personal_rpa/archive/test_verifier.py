"""archive.verifier 单元测试

覆盖 5 类错误诊断：
- access_token 失败（40013 / 40125 / 其他 errcode）
- chat_data 失败（45009 / 60011 SDK 权限）
- private_key 失败（RSA 解密错）
- callback 失败（Token 或 EncodingAESKey 错）
- 全成功路径（含私钥测试 + 拉空批次跳过私钥）
"""
import base64
import json
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.wecom.crypto import WeComCrypto
from src.channels.wecom_personal_rpa.archive import chat_crypto, http_client, verifier


TEST_TOKEN = "QDG6eK"
TEST_AES_KEY = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"
TEST_CORP_ID = "wx5823bf96d3bd56c7"


def _gen_rsa_pem():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return pk.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8"), pk


def _make_config(private_key: str = "fake", token: str = TEST_TOKEN,
                  aes_key: str = TEST_AES_KEY, corp_id: str = TEST_CORP_ID) -> dict:
    return {
        "corp_id": corp_id,
        "archive_secret": "secret_xxx",
        "private_key": private_key,
        "token": token,
        "encoding_aes_key": aes_key,
    }


# ----------------- Step 0: 字段缺失 -----------------


@pytest.mark.asyncio
async def test_verify_missing_fields():
    """5 个字段任一缺失 → stage=fields。"""
    for missing_field in ["corp_id", "archive_secret", "private_key", "token", "encoding_aes_key"]:
        cfg = _make_config()
        cfg[missing_field] = ""
        result = await verifier.verify_archive_server_mode(cfg, "t_test")
        assert result["success"] is False
        assert result["stage"] == "fields"
        assert missing_field in result["message"]


# ----------------- Step 1: access_token 失败 -----------------


@pytest.mark.asyncio
async def test_verify_access_token_invalid_corpid():
    """errcode=40013 → invalid corpid 诊断。"""
    cfg = _make_config()
    with patch.object(verifier.http_client, "get_access_token",
                      new=AsyncMock(side_effect=http_client.WeComApiException("gettoken", 40013, "invalid corpid"))):
        result = await verifier.verify_archive_server_mode(cfg, "t_test")
    assert result["success"] is False
    assert result["stage"] == "access_token"
    assert "Corp ID" in result["message"] or "Secret" in result["message"]


@pytest.mark.asyncio
async def test_verify_access_token_invalid_secret():
    """errcode=40125 → invalid secret 诊断。"""
    cfg = _make_config()
    with patch.object(verifier.http_client, "get_access_token",
                      new=AsyncMock(side_effect=http_client.WeComApiException("gettoken", 40125, "invalid secret"))):
        result = await verifier.verify_archive_server_mode(cfg, "t_test")
    assert result["success"] is False
    assert result["stage"] == "access_token"


@pytest.mark.asyncio
async def test_verify_access_token_network_error():
    """网络异常 → stage=access_token，message 含 "网络异常"。"""
    cfg = _make_config()
    with patch.object(verifier.http_client, "get_access_token",
                      new=AsyncMock(side_effect=ConnectionError("dns fail"))):
        result = await verifier.verify_archive_server_mode(cfg, "t_test")
    assert result["success"] is False
    assert result["stage"] == "access_token"
    assert "网络异常" in result["message"]


# ----------------- Step 2: chat_data 失败 -----------------


@pytest.mark.asyncio
async def test_verify_chat_data_rate_limited():
    """45009 → 频率限制诊断。"""
    cfg = _make_config()
    with patch.object(verifier.http_client, "get_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(verifier.http_client, "get_chat_data",
                      new=AsyncMock(side_effect=http_client.WeComRateLimitException(60))):
        result = await verifier.verify_archive_server_mode(cfg, "t_test")
    assert result["success"] is False
    assert result["stage"] == "chat_data"
    assert "45009" in result["message"]


@pytest.mark.asyncio
async def test_verify_chat_data_no_sdk_permission():
    """errcode=60011 → SDK 权限缺失诊断。"""
    cfg = _make_config()
    with patch.object(verifier.http_client, "get_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(verifier.http_client, "get_chat_data",
                      new=AsyncMock(side_effect=http_client.WeComApiException("get_chat_data", 60011, "no privilege"))):
        result = await verifier.verify_archive_server_mode(cfg, "t_test")
    assert result["success"] is False
    assert result["stage"] == "chat_data"
    assert "SDK 权限" in result["message"] or "未获得" in result["message"]


@pytest.mark.asyncio
async def test_verify_chat_data_sdk_load_error():
    """SDK 加载失败（SDKLoadError）→ chat_data 阶段失败诊断。"""
    from src.channels.wecom_personal_rpa.archive.wecom_finance_sdk import SDKLoadError
    cfg = _make_config()
    with patch.object(verifier.http_client, "get_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(verifier.http_client, "get_chat_data",
                      new=AsyncMock(side_effect=SDKLoadError(".so not found"))):
        result = await verifier.verify_archive_server_mode(cfg, "t_test")
    assert result["success"] is False
    assert result["stage"] == "chat_data"
    assert "异常" in result["message"]


# ----------------- Step 3+4: private_key 失败 -----------------


@pytest.mark.asyncio
async def test_verify_private_key_invalid():
    """私钥无法解密 encrypt_random_key → 私钥错误诊断。"""
    pem, _ = _gen_rsa_pem()  # 用正确私钥构造密文
    random_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding

    other_pem, other_pk = _gen_rsa_pem()  # 用另一对密钥的公钥加密
    encrypted_random_key = base64.b64encode(
        other_pk.public_key().encrypt(
            random_key,
            rsa_padding.OAEP(
                mgf=rsa_padding.MGF1(algorithm=hashes.SHA1()),
                algorithm=hashes.SHA1(),
                label=None,
            ),
        )
    ).decode("ascii")
    encrypted_chat_msg = base64.b64encode(iv + b"fake_cipher").decode("ascii")

    item = http_client.ChatDataItem(
        seq=1, msg_id="m1", action="upload", from_="u", tolist=[],
        msg_time=1700000000, msg_type="text",
        encrypt_random_key=encrypted_random_key,
        encrypt_chat_msg=encrypted_chat_msg,
    )
    batch = http_client.ChatDataBatch(items=[item])

    # 用正确私钥 pem（与 other_pk 公钥不匹配）做验证
    cfg = _make_config(private_key=pem)
    with patch.object(verifier.http_client, "get_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(verifier.http_client, "get_chat_data", new=AsyncMock(return_value=batch)):
        result = await verifier.verify_archive_server_mode(cfg, "t_test")
    assert result["success"] is False
    assert result["stage"] == "private_key"


# ----------------- Step 5: callback 失败 -----------------


@pytest.mark.asyncio
async def test_verify_callback_invalid_aes_key_format():
    """EncodingAESKey 长度异常 → WeComCrypto 构造失败 → stage=callback 诊断。

    自签自验无法发现 Token/EncodingAESKey 内容错误（因为加密解密用同一密钥），
    但长度/格式错误会被 WeComCrypto 构造期捕获。
    """
    pem, _ = _gen_rsa_pem()
    # 长度不对（不是 43 字符）
    cfg = _make_config(private_key=pem, aes_key="too_short_to_be_valid")
    with patch.object(verifier.http_client, "get_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(verifier.http_client, "get_chat_data",
                      new=AsyncMock(return_value=http_client.ChatDataBatch(items=[]))):
        result = await verifier.verify_archive_server_mode(cfg, "t_test")
    assert result["success"] is False
    assert result["stage"] == "callback"
    assert "EncodingAESKey" in result["message"] or "格式错误" in result["message"]


# ----------------- 成功路径 -----------------


@pytest.mark.asyncio
async def test_verify_success_empty_batch():
    """空批次 + callback 自测通过 → 全成功，但提示私钥未测试。"""
    pem, _ = _gen_rsa_pem()
    cfg = _make_config(private_key=pem)
    with patch.object(verifier.http_client, "get_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(verifier.http_client, "get_chat_data",
                      new=AsyncMock(return_value=http_client.ChatDataBatch(items=[]))):
        result = await verifier.verify_archive_server_mode(cfg, "t_test")
    assert result["success"] is True
    assert result["verified"] is True
    assert "私钥暂未测试" in result["message"]
    assert "private_key" not in result["stages_passed"]


@pytest.mark.asyncio
async def test_verify_success_full_chain():
    """完整链路：有密文 + 私钥解密成功 + callback 自测通过。"""
    pem, private_key_obj = _gen_rsa_pem()
    cfg = _make_config(private_key=pem)

    # 构造密文：用同一私钥加密
    random_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)
    plain = b'{"msgid":"msg1"}'
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encrypted_random_key = base64.b64encode(
        private_key_obj.public_key().encrypt(
            random_key,
            rsa_padding.OAEP(
                mgf=rsa_padding.MGF1(algorithm=hashes.SHA1()),
                algorithm=hashes.SHA1(),
                label=None,
            ),
        )
    ).decode("ascii")

    pad_len = 32 - (len(plain) % 32)
    padded = plain + bytes([pad_len] * pad_len)
    cipher = Cipher(algorithms.AES(random_key[:32]), modes.CBC(iv))
    enc = cipher.encryptor()
    encrypted_chat_msg = base64.b64encode(iv + enc.update(padded) + enc.finalize()).decode("ascii")

    item = http_client.ChatDataItem(
        seq=1, msg_id="msg1", action="upload", from_="u", tolist=[],
        msg_time=1700000000, msg_type="text",
        encrypt_random_key=encrypted_random_key,
        encrypt_chat_msg=encrypted_chat_msg,
    )
    batch = http_client.ChatDataBatch(items=[item])

    with patch.object(verifier.http_client, "get_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(verifier.http_client, "get_chat_data", new=AsyncMock(return_value=batch)):
        result = await verifier.verify_archive_server_mode(cfg, "t_test")

    assert result["success"] is True
    assert result["verified"] is True
    assert "private_key" in result["stages_passed"]
    assert "callback" in result["stages_passed"]
