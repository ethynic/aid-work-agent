"""
企业微信加解密模块单元测试

测试 WeComCrypto 的签名验证、加解密功能。
"""

import base64
import hashlib
import struct

import pytest

from src.channels.wecom.crypto import WeComCrypto


# 测试用的固定参数
TEST_TOKEN = "test_token_123"
TEST_CORP_ID = "ww_test_corp_id"
# 生成 43 字符的 Base64 encoding_aes_key
TEST_AES_KEY_RAW = b"a" * 32  # 32 字节 AES 密钥
TEST_ENCODING_AES_KEY = base64.b64encode(TEST_AES_KEY_RAW).decode("utf-8").rstrip("=")


@pytest.fixture
def crypto():
    """创建 WeComCrypto 实例"""
    return WeComCrypto(TEST_TOKEN, TEST_ENCODING_AES_KEY, TEST_CORP_ID)


class TestWeComCryptoInit:
    """初始化测试"""

    def test_valid_encoding_aes_key(self):
        crypto = WeComCrypto(TEST_TOKEN, TEST_ENCODING_AES_KEY, TEST_CORP_ID)
        assert len(crypto.aes_key) == 32

    def test_invalid_encoding_aes_key(self):
        with pytest.raises(ValueError, match="32 字节"):
            WeComCrypto(TEST_TOKEN, "tooshort", TEST_CORP_ID)


class TestWeComCryptoSignature:
    """签名验证测试"""

    def test_verify_signature_correct(self, crypto):
        """正确的签名应该验证通过"""
        timestamp = "1234567890"
        nonce = "test_nonce"
        encrypt = "test_encrypt_content"

        # 手动计算预期签名
        items = [TEST_TOKEN, timestamp, nonce, encrypt]
        items.sort()
        combined = "".join(items)
        expected_sig = hashlib.sha1(combined.encode("utf-8")).hexdigest()

        assert crypto.verify_signature(expected_sig, timestamp, nonce, encrypt) is True

    def test_verify_signature_wrong(self, crypto):
        """错误的签名应该验证失败"""
        assert crypto.verify_signature(
            "wrong_signature", "123", "nonce", "encrypt"
        ) is False

    def test_verify_signature_order_independent(self, crypto):
        """签名的元素排序不影响结果"""
        timestamp = "1409659 infer0898"
        nonce = "13726231 infer4016"
        encrypt = "test_encrypt"

        items = [TEST_TOKEN, timestamp, nonce, encrypt]
        items.sort()
        sig = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()

        assert crypto.verify_signature(sig, timestamp, nonce, encrypt) is True


class TestWeComCryptoEncryptDecrypt:
    """加解密往返测试"""

    def test_encrypt_decrypt_roundtrip(self, crypto):
        """加密后解密应得到原始消息"""
        original = "Hello, 企业微信！这是一条测试消息。"
        encrypted = crypto.encrypt(original)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == original

    def test_encrypt_decrypt_empty_message(self, crypto):
        """空消息的加解密"""
        original = ""
        encrypted = crypto.encrypt(original)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == original

    def test_encrypt_decrypt_long_message(self, crypto):
        """长消息的加解密"""
        original = "测试" * 1000
        encrypted = crypto.encrypt(original)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == original

    def test_encrypt_decrypt_special_chars(self, crypto):
        """包含特殊字符的消息"""
        original = "<xml>测试 & \"引号\" '单引号' \n换行\t制表符</xml>"
        encrypted = crypto.encrypt(original)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == original

    def test_encrypt_produces_different_ciphertext(self, crypto):
        """每次加密应产生不同密文（因为有随机前缀）"""
        original = "同样的消息"
        enc1 = crypto.encrypt(original)
        enc2 = crypto.encrypt(original)
        # 由于随机前缀，密文应该不同
        assert enc1 != enc2
        # 但都能正确解密
        assert crypto.decrypt(enc1) == original
        assert crypto.decrypt(enc2) == original

    def test_decrypt_corp_id_mismatch(self):
        """解密时 corp_id 不匹配应抛出异常"""
        crypto1 = WeComCrypto(TEST_TOKEN, TEST_ENCODING_AES_KEY, TEST_CORP_ID)
        encrypted = crypto1.encrypt("test message")

        # 用不同的 corp_id 解密
        crypto2 = WeComCrypto(TEST_TOKEN, TEST_ENCODING_AES_KEY, "ww_different_corp")
        with pytest.raises(ValueError, match="corp_id 不匹配"):
            crypto2.decrypt(encrypted)

    def test_decrypt_invalid_base64(self, crypto):
        """无效的 Base64 应抛出异常"""
        with pytest.raises(Exception):
            crypto.decrypt("not_valid_base64!!!")


class TestWeComCryptoGenerateReply:
    """加密回复生成测试"""

    def test_generate_encrypted_reply_xml(self, crypto):
        """生成的回复应是有效的 XML"""
        reply = crypto.generate_encrypted_reply("测试回复", "1234567890", "nonce123")
        assert "<xml>" in reply
        assert "<Encrypt>" in reply
        assert "<MsgSignature>" in reply
        assert "<TimeStamp>" in reply
        assert "<Nonce>" in reply
        assert "</xml>" in reply

    def test_generate_signature_matches_verify(self, crypto):
        """生成的签名应能通过验证"""
        timestamp = "1234567890"
        nonce = "test_nonce"
        encrypt = crypto.encrypt("test")

        sig = crypto.generate_signature(timestamp, nonce, encrypt)
        assert crypto.verify_signature(sig, timestamp, nonce, encrypt) is True
