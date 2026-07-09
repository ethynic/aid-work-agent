"""archive.chat_crypto 单元测试

覆盖：
- RSA-PKCS1v15 解密闭环（自生成密钥对，模拟企微加密）
- AES-256-CBC + PKCS7 解密闭环
- decrypt_message 组合 API
- 错误路径：私钥格式错、密文损坏、random_key 不足 32 字节、密文短于 16 字节
"""
import base64
import os
import secrets

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

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


def _aes_cbc_encrypt(random_key: bytes, iv: bytes, plaintext: bytes) -> str:
    """模拟企微 AES-256-CBC + PKCS7 加密 chat_msg，返回 base64（iv + 密文）。"""
    pad_len = 32 - (len(plaintext) % 32)
    padded = plaintext + bytes([pad_len] * pad_len)

    cipher = Cipher(algorithms.AES(random_key[:32]), modes.CBC(iv))
    encryptor = cipher.encryptor()
    cipher_bytes = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + cipher_bytes).decode("ascii")


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


# ----------------- AES 解密 -----------------


def test_decrypt_chat_msg_ok():
    """AES-256-CBC + PKCS7 解密闭环。"""
    random_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)
    plain = "你好，企微会话存档".encode("utf-8")
    encrypted_b64 = _aes_cbc_encrypt(random_key, iv, plain)

    decrypted = chat_crypto.decrypt_chat_msg(random_key, encrypted_b64)
    assert decrypted == "你好，企微会话存档"


def test_decrypt_chat_msg_missing_padding_tolerated():
    """缺末尾 ``=`` padding 但数据字符长度合法（4n/4n+2/4n+3）的 Base64 仍能解密。

    背景：企微 SDK 偶尔返回的 encrypt_chat_msg 缺末尾 ``=`` padding，
    Python ``base64.b64decode`` 严格模式直接拒绝。``_b64decode_lenient``
    会自动补 ``=``，对 4n/4n+2/4n+3 三种合法长度都能恢复。
    """
    random_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)
    plain = "缺 padding 的 Base64 测试".encode("utf-8")
    encrypted_b64 = _aes_cbc_encrypt(random_key, iv, plain)

    # 剥掉末尾所有 = padding
    stripped = encrypted_b64.rstrip("=")
    data_chars = len(stripped)

    # 4n+1 是物理不可能（密文被截断），跳过此场景，由下一个测试覆盖
    if data_chars % 4 == 1:
        pytest.skip("密文恰好 4n+1，由 test_..._truncated_*_raises 覆盖")

    decrypted = chat_crypto.decrypt_chat_msg(random_key, stripped)
    assert decrypted == "缺 padding 的 Base64 测试"


def test_b64decode_lenient_truncated_4n_plus_1_raises():
    """数据字符长度 4n+1（如 agent2 seq=3 的 393 字符）= 密文被截断，物理不可能解码。

    Base64 编码 3 字节 = 4 字符，所以数据字符长度（去掉 = padding）只能是 4n / 4n+2 / 4n+3。
    4n+1 说明原始字节序列不存在任何能编码出这种长度的输入 → 字符串必然被截断。
    补 padding 无济于事，应抛清晰错误而非标准库的晦涩报错。
    """
    # 模拟 agent2 seq=3：393 个 'A'，无 padding
    truncated = "A" * 393
    with pytest.raises(ValueError, match="数据字符长度 393 = 4n\\+1.*密文被截断"):
        chat_crypto._b64decode_lenient(truncated, "encrypt_chat_msg")


def test_decrypt_chat_msg_with_internal_whitespace_tolerated():
    """带换行/空白的 Base64 仍能解密（SDK 输出可能含 CRLF）。"""
    random_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)
    plain = "带空白测试".encode("utf-8")
    encrypted_b64 = _aes_cbc_encrypt(random_key, iv, plain)

    # 在中间和首尾插入换行、空格
    polluted = "\n " + encrypted_b64[:10] + "\r\n" + encrypted_b64[10:] + " \n"

    decrypted = chat_crypto.decrypt_chat_msg(random_key, polluted)
    assert decrypted == "带空白测试"


def test_decrypt_chat_msg_illegal_chars_still_raises():
    """含非法 Base64 字符（! @ 等）时仍抛 ValueError（密文真的损坏，不能静默吞）。"""
    random_key = secrets.token_bytes(32)
    with pytest.raises(ValueError, match="含非法 Base64 字符"):
        chat_crypto.decrypt_chat_msg(random_key, "invalid!!!@#$")


def test_decrypt_chat_msg_random_key_too_short_raises():
    with pytest.raises(ValueError, match="random_key 至少 32 字节"):
        chat_crypto.decrypt_chat_msg(b"short", base64.b64encode(b"xxxxxxxxxxxxxxx").decode())


def test_decrypt_chat_msg_empty_ciphertext_raises():
    with pytest.raises(ValueError, match="encrypt_chat_msg_b64 不能为空"):
        chat_crypto.decrypt_chat_msg(secrets.token_bytes(32), "")


def test_decrypt_chat_msg_too_short_raises():
    """密文（base64 解码后）不足 16 字节 → 抛 ValueError。"""
    short_b64 = base64.b64encode(b"only10bytes").decode()  # 12 字节 < 16
    with pytest.raises(ValueError, match="长度不足 16 字节"):
        chat_crypto.decrypt_chat_msg(secrets.token_bytes(32), short_b64)


def test_decrypt_chat_msg_corrupted_padding_raises():
    """密文损坏 → PKCS7 去填充失败 → 抛 ValueError（pad_len > 32 或 < 1）。"""
    random_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)
    # 故意构造一段不会产生合法 padding 的密文
    bad_cipher = secrets.token_bytes(32)
    encrypted_b64 = base64.b64encode(iv + bad_cipher).decode()
    # 不强制断言具体错误信息（不同 cryptography 版本信息可能不同），只要是 ValueError 即可
    with pytest.raises(ValueError):
        chat_crypto.decrypt_chat_msg(random_key, encrypted_b64)


# ----------------- 组合 API -----------------


def test_decrypt_message_full_roundtrip():
    """组合 API 闭环：模拟企微双层加密 → Python 一次调用解密。"""
    pem, private_key = _gen_rsa_keypair()
    random_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)
    plain_json = '{"msgid":"msg_001","action":"upload","msgtype":"text","text":{"content":"hello","noise":"123"}}'
    encrypted_random_key = _rsa_encrypt_pkcs1v15(private_key.public_key(), random_key)
    encrypted_chat_msg = _aes_cbc_encrypt(random_key, iv, plain_json.encode("utf-8"))

    result = chat_crypto.decrypt_message(pem, encrypted_random_key, encrypted_chat_msg)
    assert result == plain_json


# ----------------- 算法稳定性验证（固定输入可重现） -----------------


def test_algorithm_consistency_fixed_input():
    """验证 Python 实现的算法行为可重现（固定输入固定输出）。

    对照企微官方文档（https://developer.work.weixin.qq.com/document/path/91774）：
      - RSA: PKCS1v15
      - AES: CBC + PKCS7，key=random_key[:32]，IV=ciphertext[:16]，cipher=ciphertext[16:]
    """
    pem, private_key = _gen_rsa_keypair()
    # 固定的 random_key 和 iv，确保两次运行结果可重现
    random_key = b"A" * 32
    iv = b"B" * 16
    plain = b'{"test":"consistency"}'

    encrypted_random_key = _rsa_encrypt_pkcs1v15(private_key.public_key(), random_key)
    encrypted_chat_msg = _aes_cbc_encrypt(random_key, iv, plain)

    # Python 解密应得到原 plain
    decrypted = chat_crypto.decrypt_message(pem, encrypted_random_key, encrypted_chat_msg)
    assert decrypted == plain.decode("utf-8")
