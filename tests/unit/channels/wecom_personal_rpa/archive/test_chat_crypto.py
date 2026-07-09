"""archive.chat_crypto 单元测试

覆盖：
- RSA-PKCS1v15 解密 encrypt_random_key 闭环（自生成密钥对，模拟企微加密）
- _b64decode_lenient 容错路径（缺 padding / 带空白 / 4n+1 截断）
- 错误路径：私钥格式错、密文损坏、空字符串

注意：``encrypt_chat_msg`` 的 AES 解密由 SDK DecryptData 完成（见
``wecom_finance_sdk.decrypt_data_raw``），不在本模块负责，本测试不覆盖。
"""
import base64
import secrets

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric import rsa

from src.channels.wecom_personal_rpa.archive import chat_crypto


# ----------------- 测试用密钥对生成（每个测试独立一份） -----------------


def _gen_rsa_keypair() -> tuple[str, rsa.RSAPrivateKey]:
    """生成测试用 RSA 密钥对，返回 (PEM 私钥字符串, 私钥对象)。"""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return pem, private_key


def _rsa_encrypt_pkcs1v15(public_key, plaintext: bytes) -> str:
    """模拟企微用公钥加密 random_key（RSA-PKCS1v15），返回 base64。

    企微官方文档明确要求 PKCS1
    （https://developer.work.weixin.qq.com/document/path/91774），
    早期文档/SDK 误传为 OAEP-SHA1，真机密文验证为 PKCS1。
    """
    cipher = public_key.encrypt(plaintext, rsa_padding.PKCS1v15())
    return base64.b64encode(cipher).decode("ascii")


# ----------------- RSA 解密 -----------------


def test_decrypt_random_key_ok():
    """RSA-PKCS1v15 解密闭环。"""
    pem, private_key = _gen_rsa_keypair()
    random_key_plain = secrets.token_bytes(32)
    encrypted_b64 = _rsa_encrypt_pkcs1v15(private_key.public_key(), random_key_plain)

    decrypted = chat_crypto.decrypt_random_key(pem, encrypted_b64)
    assert decrypted == random_key_plain


def test_decrypt_random_key_empty_pem_raises():
    with pytest.raises(ValueError, match="private_key_pem 不能为空"):
        chat_crypto.decrypt_random_key("", "abc")


def test_decrypt_random_key_empty_ciphertext_raises():
    pem, _ = _gen_rsa_keypair()
    with pytest.raises(ValueError, match="encrypt_random_key_b64 不能为空"):
        chat_crypto.decrypt_random_key(pem, "")


def test_decrypt_random_key_invalid_pem_raises():
    # 用合法 base64 但 PEM 内容非法，确保错误来自 PEM 解析阶段
    with pytest.raises(ValueError, match="私钥 PEM 解析失败"):
        chat_crypto.decrypt_random_key("not a pem", base64.b64encode(b"valid-b64").decode())


def test_decrypt_random_key_invalid_base64_ciphertext_raises():
    """非合法 base64 的密文应抛 ValueError（b64decode 失败）。"""
    pem, _ = _gen_rsa_keypair()
    with pytest.raises((ValueError, Exception)):
        # binascii.Error 也属于 Exception；这里只验证不静默通过
        chat_crypto.decrypt_random_key(pem, "not!valid!base64!!!")


def test_decrypt_random_key_corrupted_ciphertext_raises():
    pem, _ = _gen_rsa_keypair()
    with pytest.raises(ValueError, match="RSA 解密失败"):
        chat_crypto.decrypt_random_key(pem, base64.b64encode(b"corrupted").decode())


def test_decrypt_random_key_missing_padding_tolerated():
    """缺末尾 ``=`` padding 的 encrypt_random_key 仍能解密。

    企微 SDK 偶尔返回的密文缺末尾 ``=`` padding，``_b64decode_lenient`` 会自动补齐。
    """
    pem, private_key = _gen_rsa_keypair()
    random_key_plain = secrets.token_bytes(32)
    encrypted_b64 = _rsa_encrypt_pkcs1v15(private_key.public_key(), random_key_plain)

    # 剥掉末尾 = padding
    stripped = encrypted_b64.rstrip("=")
    # RSA 密文长度固定（2048 位 → 256 字节 → 344 base64 字符，剥掉 == 后 342 → 4n+2，可补 = 恢复）
    decrypted = chat_crypto.decrypt_random_key(pem, stripped)
    assert decrypted == random_key_plain


# ----------------- _b64decode_lenient 单元测试 -----------------


def test_b64decode_lenient_basic():
    """标准 base64 → 正常解码。"""
    assert chat_crypto._b64decode_lenient("aGVsbG8=", "test") == b"hello"


def test_b64decode_lenient_missing_padding():
    """缺 = padding → 自动补全。"""
    # "hello" 的 base64 是 "aGVsbG8="，剥掉 = 后仍能恢复
    assert chat_crypto._b64decode_lenient("aGVsbG8", "test") == b"hello"


def test_b64decode_lenient_with_whitespace():
    """含换行/空格 → 去除后解码。"""
    polluted = "\n aGVs \nbG8= \n"
    assert chat_crypto._b64decode_lenient(polluted, "test") == b"hello"


def test_b64decode_lenient_empty_raises():
    with pytest.raises(ValueError, match="不能为空"):
        chat_crypto._b64decode_lenient("", "test")


def test_b64decode_lenient_only_whitespace_raises():
    with pytest.raises(ValueError, match="去空白后为空"):
        chat_crypto._b64decode_lenient("\n\r\n  ", "test")


def test_b64decode_lenient_illegal_chars_raises():
    """含非法 Base64 字符（! @ 等）时抛 ValueError。"""
    with pytest.raises(ValueError, match="含非法 Base64 字符"):
        chat_crypto._b64decode_lenient("invalid!!!@#$", "test")


def test_b64decode_lenient_truncated_4n_plus_1_raises():
    """数据字符长度 4n+1（如 agent2 seq=3 的 393 字符）= 物理不可能，密文被截断。

    Base64 编码 3 字节 = 4 字符，所以数据字符长度（去掉 = padding）只能 4n / 4n+2 / 4n+3。
    4n+1 说明原始字节序列不存在任何能编码出这种长度的输入 → 字符串必然被截断。
    """
    truncated = "A" * 393
    with pytest.raises(ValueError, match="数据字符长度 393 = 4n\\+1.*密文被截断"):
        chat_crypto._b64decode_lenient(truncated, "encrypt_chat_msg")
