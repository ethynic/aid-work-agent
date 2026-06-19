"""
飞书加解密模块单元测试

测试 FeishuCrypto 的密钥派生、加解密、签名校验、URL 验证。
覆盖 implementation_plan.md §2.6.1 全部 7 个用例，
锁定 P3（IV 来源）、P4（解密结构）、P5（POST url_verification）、P6（签名拼接）。
"""

import base64
import hashlib
import json
import struct

import pytest

from src.channels.feishu.crypto import FeishuCrypto


# 测试用固定参数
TEST_VERIFICATION_TOKEN = "test_verification_token_abc"
TEST_ENCRYPT_KEY = "test_encrypt_key_32_chars_long_xx"  # 任意字符串，长度不限


@pytest.fixture
def crypto():
    return FeishuCrypto(TEST_VERIFICATION_TOKEN, TEST_ENCRYPT_KEY)


class TestFeishuCryptoInit:
    """§2.6.1 用例 1：aes_key_is_sha256_of_encrypt_key"""

    def test_aes_key_is_sha256_of_encrypt_key(self, crypto):
        expected = hashlib.sha256(TEST_ENCRYPT_KEY.encode("utf-8")).digest()
        assert crypto.aes_key == expected
        assert len(crypto.aes_key) == 32

    def test_different_encrypt_key_produces_different_aes_key(self):
        c1 = FeishuCrypto("t", "key_one")
        c2 = FeishuCrypto("t", "key_two")
        assert c1.aes_key != c2.aes_key


class TestFeishuCryptoIVSource:
    """§2.6.1 用例 2：iv_comes_from_ciphertext_prefix_not_key (P3)"""

    def test_iv_comes_from_ciphertext_prefix_not_key(self, crypto):
        """IV 必须从 Base64 解码密文的前 16 字节取，不能从 aes_key 取。
        验证：每次加密产生不同密文（因为 IV 是随机生成的）。
        """
        data = {"text": "hello"}
        enc1 = crypto.encrypt(data)
        enc2 = crypto.encrypt(data)
        # 不同 IV 导致不同密文
        assert enc1 != enc2
        # 都能正确解密
        assert crypto.decrypt(enc1) == data
        assert crypto.decrypt(enc2) == data

    def test_iv_is_placed_at_ciphertext_prefix(self, crypto):
        """手动构造：把已知 IV 拼在密文前缀，验证解密能从该前缀正确提取 IV。
        如果实现错误地从 aes_key[:16] 取 IV，解密将失败。
        """
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        # 已知明文（符合飞书解密后结构）
        json_bytes = json.dumps({"type": "url_verification"}).encode("utf-8")
        random_prefix = b"\x00" * 16
        msg_len = struct.pack("!I", len(json_bytes))
        app_id = b""
        plain_body = random_prefix + msg_len + json_bytes + app_id
        # PKCS7 填充
        pad_len = 32 - (len(plain_body) % 32)
        plain_body += bytes([pad_len] * pad_len)

        # 用一个与 aes_key[:16] 完全不同的 IV
        distinct_iv = bytes([0xFF - b for b in crypto.aes_key[:16]])
        cipher = Cipher(algorithms.AES(crypto.aes_key), modes.CBC(distinct_iv))
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(plain_body) + encryptor.finalize()

        # 把 distinct_iv 拼在密文前缀 → Base64
        fake_encrypted = base64.b64encode(distinct_iv + ciphertext).decode("utf-8")

        # 解密应成功（说明 IV 是从密文前缀取的）
        result = crypto.decrypt(fake_encrypted)
        assert result == {"type": "url_verification"}


class TestFeishuCryptoDecrypt:
    """§2.6.1 用例 3 & 4：解密结构与 PKCS7 填充 (P4)"""

    def test_decrypt_strips_random_prefix_and_length_header(self, crypto):
        """解密后必须剥掉 16 字节随机串 + 4 字节大端序长度头。
        往返测试即能验证：如果结构解析有错，json.loads 会失败。
        """
        data = {"header": {"event_type": "im.message.receive_v1"}, "event": {"text": "测试"}}
        encrypted = crypto.encrypt(data)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == data

    def test_decrypt_with_app_id_suffix(self, crypto):
        """app_id 附加在 JSON 之后的尾部，解析时通过 msg_len 截断 JSON，不读取 app_id 部分。"""
        data = {"key": "value"}
        encrypted = crypto.encrypt(data, app_id="cli_test_app_id")
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == data

    def test_decrypt_pkcs7_padding_block_size_32(self, crypto):
        """PKCS7 填充块大小必须为 32（与企微相同）。"""
        assert crypto.BLOCK_SIZE == 32

        # 验证填充行为：构造刚好填满一个块的数据，应再加一个完整块
        # 32 字节数据 → 填充 32 字节 → 密文长度至少 64 + IV 16
        data = {"x": "a" * 20}  # JSON 序列化后长度不固定，但块大小固定
        encrypted = crypto.encrypt(data)
        enc_bytes = base64.b64decode(encrypted)
        # IV(16) + 密文（至少 32 字节，即 1 个块）
        assert len(enc_bytes) >= 16 + 32
        # 密文长度必须是 32 的倍数（减去 IV 后）
        ciphertext_len = len(enc_bytes) - 16
        assert ciphertext_len % 32 == 0

    def test_decrypt_invalid_padding_raises(self, crypto):
        """PKCS7 填充字节不一致时应抛出 ValueError。"""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        # 构造一段填充错误的密文：总长度 48 字节（3 × AES 16 字节块）
        # 末字节 0x05 声明有 5 字节填充，但前 4 字节不是 0x05 → 校验失败
        iv = b"\x00" * 16
        bad_plain = b"\x00" * 43 + b"\x01\x02\x03\x04" + b"\x05"
        assert len(bad_plain) == 48
        cipher = Cipher(algorithms.AES(crypto.aes_key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(bad_plain) + encryptor.finalize()
        bad_encrypted = base64.b64encode(iv + ciphertext).decode("utf-8")

        with pytest.raises(ValueError, match="PKCS7"):
            crypto.decrypt(bad_encrypted)

    def test_decrypt_too_short_for_iv(self, crypto):
        """密文 Base64 解码后不足 16 字节，无法提取 IV。"""
        short_data = base64.b64encode(b"\x00" * 10).decode("utf-8")
        with pytest.raises(ValueError, match="太短"):
            crypto.decrypt(short_data)


class TestFeishuCryptoSignature:
    """§2.6.1 用例 5：verify_signature_uses_encrypt_key_not_aes_key (P6)"""

    def test_verify_signature_uses_encrypt_key_not_aes_key(self, crypto):
        """v2.0 签名拼接的是 encrypt_key 原文，不是 SHA256 后的 aes_key。"""
        timestamp = "1718000000"
        nonce = "test_nonce_xyz"
        body = '{"type":"url_verification"}'

        # 正确：用 encrypt_key 原文拼接
        correct_content = timestamp + nonce + TEST_ENCRYPT_KEY + body
        correct_sig = hashlib.sha256(correct_content.encode("utf-8")).hexdigest()
        assert crypto.verify_signature(timestamp, nonce, body, correct_sig) is True

    def test_verify_signature_rejects_aes_key_based_signature(self, crypto):
        """若错误地使用 aes_key 计算签名，校验必须失败。"""
        timestamp = "1718000000"
        nonce = "test_nonce_xyz"
        body = '{"type":"url_verification"}'

        # 错误：用 aes_key（SHA256 后的）拼接
        wrong_content = timestamp + nonce + crypto.aes_key.hex() + body
        wrong_sig = hashlib.sha256(wrong_content.encode("utf-8")).hexdigest()
        assert crypto.verify_signature(timestamp, nonce, body, wrong_sig) is False

    def test_verify_signature_wrong_value(self, crypto):
        assert crypto.verify_signature("1", "n", "b", "wrong_sig") is False


class TestFeishuCryptoUrlVerification:
    """§2.6.1 用例 6 & 7：url_verification POST body (P5)"""

    def test_verify_url_verification_post_body(self, crypto):
        """POST JSON body {type:"url_verification", token, challenge}，
        校验 token 后返回 challenge。
        """
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
        """加密模式下 url_verification body 的 encrypt 字段需先解密再校验 token。"""
        # 构造明文 url_verification 数据
        plain = {
            "type": "url_verification",
            "token": TEST_VERIFICATION_TOKEN,
            "challenge": "challenge_encrypted_123",
        }
        encrypted = crypto.encrypt(plain)
        # 外层 body 只含 encrypt 字段
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
        encrypted = crypto.encrypt(plain)
        outer_body = {"encrypt": encrypted}
        result = crypto.verify_url_verification(outer_body)
        assert result is None

    def test_verify_url_verification_with_invalid_encrypt(self, crypto):
        """encrypt 字段解密失败时返回 None，不抛异常。"""
        outer_body = {"encrypt": "not_valid_base64!@#$"}
        result = crypto.verify_url_verification(outer_body)
        assert result is None


class TestFeishuCryptoEncrypt:
    """encrypt 方法辅助测试（主要用于生成测试密文）"""

    def test_encrypt_decrypt_roundtrip_complex_data(self, crypto):
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
