"""企微会话存档拉取路径的解密工具

企微会话存档双层加密（官方文档 https://developer.work.weixin.qq.com/document/path/91774）：
  1. RSA-PKCS1v15：用企业管理后台生成的会话存档私钥解密 ``encrypt_random_key``，得到
     ``random_key``（典型 32 字节）。**注意不是 OAEP-SHA1**（早期文档/SDK 误传）。
  2. AES-256-CBC + PKCS7：以 ``random_key`` 前 32 字节为 key，base64 解码后的
     ``encrypt_chat_msg`` 前 16 字节为 IV，剩余字节为密文。

注意：**不是 AES-GCM**（早期设计文档误写为 GCM）。
"""

import base64
import re
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


# Base64 字符校验（合法字符 + 末尾允许的 =）
_B64_VALID_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")


def _b64decode_lenient(s: str, field_name: str) -> bytes:
    """容错的 Base64 解码。

    企微 SDK 偶尔返回的密文 Base64 字符串长度非 4 的倍数（缺末尾 padding `=`），
    Python ``base64.b64decode`` 严格模式会拒绝。C# 的 ``Convert.FromBase64String``
    对缺失 padding 的容忍度更高，所以历史上 C# 客户端能解而 Python 端报错。

    本函数：
    1. 去除首尾空白（避免 SDK 返回带换行）
    2. 去除内部换行（部分 SDK 输出带 CRLF）
    3. 长度非 4 倍数时自动补 ``=``（最多补 3 个，超过则视为损坏密文抛错）
    4. 含非法字符时仍抛 ValueError（密文真的损坏）

    Args:
        s: Base64 字符串。
        field_name: 字段名（用于错误信息，如 "encrypt_random_key" / "encrypt_chat_msg"）。

    Returns:
        解码后的字节串。

    Raises:
        ValueError: 字符串含非法 Base64 字符 / 补 padding 后仍非 4 倍数 / 字符串为空。
    """
    if not s:
        raise ValueError(f"{field_name} 不能为空")

    cleaned = "".join(s.split())  # 去所有空白（含换行、空格、制表符）
    if not cleaned:
        raise ValueError(f"{field_name} 去空白后为空")

    if not _B64_VALID_RE.match(cleaned):
        raise ValueError(
            f"{field_name} 含非法 Base64 字符，长度={len(cleaned)}，前 40 字符="
            f"{cleaned[:40]!r}"
        )

    # 4 字节对齐：缺几个 = 补几个（最多 3 个；缺 0 个则不动）
    missing = (-len(cleaned)) % 4
    if missing:
        cleaned = cleaned + ("=" * missing)

    return base64.b64decode(cleaned)


def decrypt_random_key(private_key_pem: str, encrypt_random_key_b64: str) -> bytes:
    """RSA-PKCS1v15 解密 encrypt_random_key，返回 random_key 字节。

    Args:
        private_key_pem: PEM 格式 PKCS#1 或 PKCS#8 RSA 私钥（含 BEGIN/END 头）。
        encrypt_random_key_b64: 企微下发的 encrypt_random_key（base64）。

    Returns:
        random_key 字节（典型 32 字节）。

    Raises:
        ValueError: 私钥格式错误 / 密文损坏 / 解密失败。
    """
    if not private_key_pem:
        raise ValueError("private_key_pem 不能为空")
    if not encrypt_random_key_b64:
        raise ValueError("encrypt_random_key_b64 不能为空")

    cipher_bytes = _b64decode_lenient(encrypt_random_key_b64, "encrypt_random_key")

    try:
        private_key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"), password=None
        )
    except Exception as e:
        raise ValueError(f"私钥 PEM 解析失败: {type(e).__name__}: {e}") from e

    try:
        # PKCS1v15：企微官方规范（https://developer.work.weixin.qq.com/document/path/91774）
        # 早期文档/SDK 误传为 OAEP-SHA1，但企微实际用 PKCS1（已用真机密文验证）
        plain_bytes = private_key.decrypt(cipher_bytes, rsa_padding.PKCS1v15())
    except Exception as e:
        raise ValueError(f"RSA 解密失败: {type(e).__name__}: {e}") from e

    return plain_bytes


def decrypt_chat_msg(random_key: bytes, encrypt_chat_msg_b64: str) -> str:
    """AES-256-CBC + PKCS7 解密 encrypt_chat_msg，返回明文 UTF-8 字符串。

    key = random_key 前 32 字节；IV = base64 解码后 encrypt_chat_msg 的前 16 字节；
    密文 = 剩余字节。

    Args:
        random_key: 由 decrypt_random_key 返回的字节串（≥32 字节）。
        encrypt_chat_msg_b64: 企微下发的 encrypt_chat_msg（base64）。

    Returns:
        解密后的明文 JSON 字符串（msgtype=text 时含 content，image/file 时含 sdkfileid）。

    Raises:
        ValueError: random_key 不足 32 字节 / 密文短于 16 字节 / 解密失败。
    """
    if not random_key or len(random_key) < 32:
        raise ValueError(f"random_key 至少 32 字节，实际 {len(random_key) if random_key else 0}")
    if not encrypt_chat_msg_b64:
        raise ValueError("encrypt_chat_msg_b64 不能为空")

    all_bytes = _b64decode_lenient(encrypt_chat_msg_b64, "encrypt_chat_msg")
    if len(all_bytes) < 16:
        raise ValueError(f"encrypt_chat_msg 长度不足 16 字节（缺少 IV），实际 {len(all_bytes)}")

    iv = all_bytes[:16]
    cipher_bytes = all_bytes[16:]

    # AES-256-CBC：key 必须 32 字节，从 random_key 取前 32 字节
    aes_key = random_key[:32]

    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded_plain = decryptor.update(cipher_bytes) + decryptor.finalize()

    # PKCS7 去填充
    pad_len = padded_plain[-1]
    if pad_len < 1 or pad_len > 32:
        raise ValueError(f"无效的 PKCS7 填充长度: {pad_len}")
    plain_bytes = padded_plain[:-pad_len]

    return plain_bytes.decode("utf-8")


def decrypt_message(
    private_key_pem: str, encrypt_random_key_b64: str, encrypt_chat_msg_b64: str
) -> str:
    """组合 API：先 RSA 解密 random_key，再 AES 解密 chat_msg。

    供 fetcher 一次性调用。失败抛 ValueError。
    """
    random_key = decrypt_random_key(private_key_pem, encrypt_random_key_b64)
    return decrypt_chat_msg(random_key, encrypt_chat_msg_b64)
