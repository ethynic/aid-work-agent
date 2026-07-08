"""
飞书加解密模块单元测试

测试 FeishuCrypto 的密钥派生、加解密、签名校验、URL 验证。

关键格式（与企微不同，对照飞书官方 SDK lark-oapi core/utils/decryptor.py）:
- AES key = SHA256(encrypt_key)，32 字节
- IV = Base64 解码密文的前 16 字节（不是从 key 取）
- PKCS7 填充块大小 = 16（AES.block_size，非企微的 32）
- 解密后明文 = 裸 JSON（无随机前缀、无 msg_len 头、无 app_id 尾部）
- 签名: SHA256(timestamp + nonce + encrypt_key + body)，拼接 encrypt_key 原文
"""

import base64
import hashlib
import json
import os
import time

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from src.channels.feishu.crypto import FeishuCrypto


# 测试用固定参数
TEST_VERIFICATION_TOKEN = "test_verification_token_abc"
TEST_ENCRYPT_KEY = "test_encrypt_key_32_chars_long_xx"  # 任意字符串，长度不限


@pytest.fixture
def crypto():
    return FeishuCrypto(TEST_VERIFICATION_TOKEN, TEST_ENCRYPT_KEY)


def _aes_key(encrypt_key: str) -> bytes:
    """飞书官方 AES key 派生：SHA256(encrypt_key)"""
    return hashlib.sha256(encrypt_key.encode("utf-8")).digest()


def _encrypt_with_official_format(encrypt_key: str, data: dict) -> str:
    """用飞书官方 SDK 格式（lark-oapi AESCipher）加密数据，返回 Base64 字符串。

    用于测试中模拟飞书真实推送的密文。明文是裸 JSON，无随机前缀/无 msg_len。
    """
    aes_key = _aes_key(encrypt_key)
    json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    pad_len = 16 - (len(json_bytes) % 16)
    padded = json_bytes + bytes([pad_len] * pad_len)
    iv = os.urandom(16)
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode("utf-8")


class TestFeishuCryptoInit:
    """密钥派生：aes_key = SHA256(encrypt_key)"""

    def test_aes_key_is_sha256_of_encrypt_key(self, crypto):
        expected = hashlib.sha256(TEST_ENCRYPT_KEY.encode("utf-8")).digest()
        assert crypto.aes_key == expected
        assert len(crypto.aes_key) == 32

    def test_different_encrypt_key_produces_different_aes_key(self):
        c1 = FeishuCrypto("t", "key_one")
        c2 = FeishuCrypto("t", "key_two")
        assert c1.aes_key != c2.aes_key


class TestFeishuCryptoIVSource:
    """IV 来源：从 Base64 解码密文的前 16 字节取（不从 aes_key 取）"""

    def test_iv_comes_from_ciphertext_prefix_not_key(self, crypto):
        """每次加密产生不同密文（IV 随机），且都能正确解密。"""
        data = {"text": "hello"}
        enc1 = crypto.encrypt(data)
        enc2 = crypto.encrypt(data)
        assert enc1 != enc2
        assert crypto.decrypt(enc1) == data
        assert crypto.decrypt(enc2) == data

    def test_iv_is_placed_at_ciphertext_prefix(self, crypto):
        """手动构造：把已知 IV 拼在密文前缀，验证解密从该前缀提取 IV。
        若实现错误地从 aes_key[:16] 取 IV，解密将失败。
        """
        json_bytes = json.dumps({"type": "url_verification"}).encode("utf-8")
        pad_len = 16 - (len(json_bytes) % 16)
        plain_body = json_bytes + bytes([pad_len] * pad_len)

        # 用一个与 aes_key[:16] 完全不同的 IV
        distinct_iv = bytes([0xFF - b for b in crypto.aes_key[:16]])
        cipher = Cipher(algorithms.AES(crypto.aes_key), modes.CBC(distinct_iv))
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(plain_body) + encryptor.finalize()

        fake_encrypted = base64.b64encode(distinct_iv + ciphertext).decode("utf-8")
        result = crypto.decrypt(fake_encrypted)
        assert result == {"type": "url_verification"}


class TestFeishuCryptoDecrypt:
    """解密格式：解密后是裸 JSON（无随机前缀、无 msg_len 头）"""

    def test_decrypt_returns_bare_json_no_prefix_no_length_header(self, crypto):
        """解密飞书官方格式密文，应直接返回 JSON 解析后的 dict。
        验证：用官方格式加密（裸 JSON），项目 decrypt 解出。
        """
        data = {"type": "url_verification", "challenge": "abc"}
        encrypted = _encrypt_with_official_format(TEST_ENCRYPT_KEY, data)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == data

    def test_decrypt_official_format_complex_event(self, crypto):
        """解密复杂 v2.0 事件（带 header/event 嵌套）。"""
        data = {
            "header": {
                "event_id": "evt_abc",
                "event_type": "im.message.receive_v1",
                "create_time": "1718000000",
                "token": "token",
                "app_id": "cli_test",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_xxx"}, "sender_type": "user"},
                "message": {
                    "message_id": "msg_xxx",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": '{"text":"hello 飞书"}',
                },
            },
        }
        encrypted = _encrypt_with_official_format(TEST_ENCRYPT_KEY, data)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == data

    def test_decrypt_pkcs7_padding_block_size_16(self, crypto):
        """PKCS7 填充块大小必须为 16（AES.block_size，非企微的 32）。"""
        assert crypto.BLOCK_SIZE == 16

        data = {"x": "a" * 20}
        encrypted = crypto.encrypt(data)
        enc_bytes = base64.b64decode(encrypted)
        # 密文长度（去掉 IV 16 字节）必须是 16 的倍数
        ciphertext_len = len(enc_bytes) - 16
        assert ciphertext_len % 16 == 0
        assert ciphertext_len > 0

    def test_decrypt_invalid_padding_raises(self, crypto):
        """PKCS7 填充字节不一致时应抛出 ValueError。"""
        iv = b"\x00" * 16
        # 构造末字节 0x05 声明 5 字节填充，但前 4 字节不是 0x05 -> 校验失败
        bad_plain = b"\x00" * 43 + b"\x01\x02\x03\x04" + b"\x05"
        assert len(bad_plain) == 48
        cipher = Cipher(algorithms.AES(crypto.aes_key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(bad_plain) + encryptor.finalize()
        bad_encrypted = base64.b64encode(iv + ciphertext).decode("utf-8")

        with pytest.raises(ValueError, match="PKCS7"):
            crypto.decrypt(bad_encrypted)

    def test_decrypt_too_short_for_iv_raises(self, crypto):
        """密文 Base64 解码后不足 32 字节（IV + 至少 1 块密文），应报错。"""
        short_data = base64.b64encode(b"\x00" * 10).decode("utf-8")
        with pytest.raises(ValueError, match="太短"):
            crypto.decrypt(short_data)

    def test_decrypt_invalid_json_raises(self, crypto):
        """解密后明文不是合法 JSON 时应抛出 ValueError。"""
        # 手动构造一段合法 PKCS7 但内容不是 JSON 的明文
        plain = b"not a json string!!"  # 20 字节
        pad_len = 16 - (len(plain) % 16)
        padded = plain + bytes([pad_len] * pad_len)
        iv = os.urandom(16)
        cipher = Cipher(algorithms.AES(crypto.aes_key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        bad_encrypted = base64.b64encode(iv + ciphertext).decode("utf-8")

        with pytest.raises(ValueError, match="不是合法 JSON"):
            crypto.decrypt(bad_encrypted)


class TestFeishuCryptoSignature:
    """签名：SHA256(timestamp + nonce + encrypt_key + body)，拼接 encrypt_key 原文"""

    def test_verify_signature_uses_encrypt_key_not_aes_key(self, crypto):
        """v2.0 签名拼接的是 encrypt_key 原文，不是 SHA256 后的 aes_key。"""
        timestamp = str(int(time.time()))
        nonce = "test_nonce_xyz"
        body = '{"type":"url_verification"}'

        correct_content = timestamp + nonce + TEST_ENCRYPT_KEY + body
        correct_sig = hashlib.sha256(correct_content.encode("utf-8")).hexdigest()
        assert crypto.verify_signature(timestamp, nonce, body, correct_sig) is True

    def test_verify_signature_rejects_aes_key_based_signature(self, crypto):
        """若错误地使用 aes_key 计算签名，校验必须失败。"""
        timestamp = str(int(time.time()))
        nonce = "test_nonce_xyz"
        body = '{"type":"url_verification"}'

        wrong_content = timestamp + nonce + crypto.aes_key.hex() + body
        wrong_sig = hashlib.sha256(wrong_content.encode("utf-8")).hexdigest()
        assert crypto.verify_signature(timestamp, nonce, body, wrong_sig) is False

    def test_verify_signature_wrong_value(self, crypto):
        # 时间戳合法但签名错误
        timestamp = str(int(time.time()))
        assert crypto.verify_signature(timestamp, "n", "b", "wrong_sig") is False

    def test_verify_signature_rejects_expired_timestamp(self, crypto):
        """时间戳偏差超过 1 小时，应拒绝（防重放）。"""
        # 2 小时前的时间戳
        expired_ts = str(int(time.time()) - 7200)
        body = '{"type":"url_verification"}'
        content = expired_ts + "n" + TEST_ENCRYPT_KEY + body
        sig = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert crypto.verify_signature(expired_ts, "n", body, sig) is False


class TestFeishuCryptoUrlVerification:
    """url_verification 处理：加密模式先解密再校验 token"""

    def test_verify_url_verification_post_body(self, crypto):
        """非加密模式 POST JSON body，校验 token 后返回 challenge。"""
        body = {
            "type": "url_verification",
            "token": TEST_VERIFICATION_TOKEN,
            "challenge": "challenge_abc_123",
        }
        result = crypto.verify_url_verification(body)
        assert result == "challenge_abc_123"

    def test_verify_url_verification_wrong_token(self, crypto):
        body = {
            "type": "url_verification",
            "token": "wrong_token",
            "challenge": "challenge_abc_123",
        }
        result = crypto.verify_url_verification(body)
        assert result is None

    def test_verify_url_verification_wrong_type(self, crypto):
        body = {
            "type": "event_callback",
            "token": TEST_VERIFICATION_TOKEN,
            "challenge": "challenge_abc_123",
        }
        result = crypto.verify_url_verification(body)
        assert result is None

    def test_verify_url_verification_with_encrypt_field(self, crypto):
        """加密模式下 url_verification body 的 encrypt 字段需先解密再校验 token。
        用飞书官方格式加密，模拟真实飞书推送。
        """
        plain = {
            "type": "url_verification",
            "token": TEST_VERIFICATION_TOKEN,
            "challenge": "challenge_encrypted_123",
        }
        encrypted = _encrypt_with_official_format(TEST_ENCRYPT_KEY, plain)
        outer_body = {"encrypt": encrypted}
        result = crypto.verify_url_verification(outer_body)
        assert result == "challenge_encrypted_123"

    def test_verify_url_verification_with_encrypt_field_wrong_token(self, crypto):
        """加密 url_verification 但明文 token 不匹配，应返回 None。"""
        plain = {
            "type": "url_verification",
            "token": "wrong_token_inside",
            "challenge": "challenge_123",
        }
        encrypted = _encrypt_with_official_format(TEST_ENCRYPT_KEY, plain)
        outer_body = {"encrypt": encrypted}
        result = crypto.verify_url_verification(outer_body)
        assert result is None

    def test_verify_url_verification_with_invalid_encrypt(self, crypto):
        """encrypt 字段解密失败时返回 None，不抛异常。"""
        outer_body = {"encrypt": "not_valid_base64!@#$"}
        result = crypto.verify_url_verification(outer_body)
        assert result is None


class TestFeishuCryptoEncrypt:
    """encrypt 方法：与飞书官方格式互通"""

    def test_encrypt_decrypt_roundtrip_complex_data(self, crypto):
        """encrypt -> decrypt 往返一致。"""
        data = {
            "header": {
                "event_id": "evt_abc123",
                "event_type": "im.message.receive_v1",
                "create_time": "1718000000",
                "token": "token",
                "app_id": "cli_test",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_xxx"}, "sender_type": "user"},
                "message": {
                    "message_id": "msg_xxx",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": '{"text":"hello 飞书"}',
                },
            },
        }
        encrypted = crypto.encrypt(data)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == data

    def test_encrypt_produces_official_compatible_format(self, crypto):
        """项目 encrypt 出的密文，用飞书官方 SDK 逻辑（AESCipher.decrypt）能解出。
        这是端到端互通的关键：飞书后台推送的密文项目能解，项目加密的回执飞书也能解。
        """
        data = {"type": "url_verification", "challenge": "abc"}
        encrypted = crypto.encrypt(data)

        # 用飞书官方 SDK 逻辑解密
        enc_bytes = base64.b64decode(encrypted)
        iv = enc_bytes[:16]
        ciphertext = enc_bytes[16:]
        cipher = Cipher(algorithms.AES(crypto.aes_key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        plain = decryptor.update(ciphertext) + decryptor.finalize()
        pad = plain[-1]
        assert 1 <= pad <= 16
        plain = plain[:-pad]
        assert json.loads(plain.decode("utf-8")) == data

    def test_encrypt_app_id_param_ignored_for_backward_compat(self, crypto):
        """旧调用签名传 app_id 参数不应报错（飞书 v2.0 不附加 app_id 到明文）。"""
        encrypted = crypto.encrypt({"type": "url_verification"}, app_id="cli_test")
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == {"type": "url_verification"}
