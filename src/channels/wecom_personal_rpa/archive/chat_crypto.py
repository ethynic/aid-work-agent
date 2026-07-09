"""企微会话存档拉取路径的解密工具（仅 RSA 解密 random_key）

企微会话存档双层加密（官方文档 https://developer.work.weixin.qq.com/document/path/91774）：
  1. RSA-PKCS1v15：用企业管理后台生成的会话存档私钥解密 ``encrypt_random_key``，得到
     ``random_key``（典型 32 字节，可 UTF-8 解码为字符串）。**注意不是 OAEP-SHA1**
     （早期文档/SDK 误传）。
  2. ``encrypt_chat_msg`` 的 AES 解密**交给 SDK 的 DecryptData 接口**完成（见
     ``wecom_finance_sdk.decrypt_data_raw``），不要在本模块用 Python 自己解。

为什么 AES 解密要走 SDK：
  - SDK 内部对 base64 解码 + AES-CBC + PKCS7 全套处理，且对 SDK 自身返回的非标准
    长度密文有容错（实测遇到 4n+1 长度的 encrypt_chat_msg，Python ``base64.b64decode``
    直接拒绝，SDK DecryptData 内部能正常解）。
  - 早期版本本模块曾经自己实现 AES-CBC 解密，遇到上述边界情况无法处理。
"""

import base64
import binascii
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding


# Base64 字符校验（合法字符 + 末尾允许的 =）
_B64_VALID_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")


def _b64decode_lenient(s: str, field_name: str) -> bytes:
    """容错的 Base64 解码。

    企微 SDK 偶尔返回的密文 Base64 字符串长度非 4 的倍数（缺末尾 padding `=`），
    Python ``base64.b64decode`` 严格模式会拒绝。

    本函数：
    1. 去除首尾空白（避免 SDK 返回带换行）
    2. 去除内部换行（部分 SDK 输出带 CRLF）
    3. 长度非 4 倍数时自动补 ``=``（最多补 3 个，超过则视为损坏密文抛错）
    4. 含非法字符时仍抛 ValueError（密文真的损坏）

    Args:
        s: Base64 字符串。
        field_name: 字段名（用于错误信息）。

    Returns:
        解码后的字节串。

    Raises:
        ValueError: 字符串含非法 Base64 字符 / 4n+1 物理不可能长度 / 字符串为空。
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

    # 校验数据字符长度（去掉 = padding 后）的物理可行性：
    # Base64 编码 3 字节 = 4 字符，因此数据字符长度只能是 4n / 4n+2 / 4n+3。
    # 4n+1 是物理不可能（没有任何字节序列能编出这种长度），说明密文被截断。
    data_chars = len(cleaned.rstrip("="))
    if data_chars % 4 == 1:
        raise ValueError(
            f"{field_name} 数据字符长度 {data_chars} = 4n+1，物理不可能（密文被截断），"
            f"原始长度={len(s)}，末尾 20 字符={s[-20:]!r}"
        )

    try:
        return base64.b64decode(cleaned)
    except binascii.Error as e:
        raise ValueError(f"{field_name} Base64 解码失败: {e}") from e


def decrypt_random_key(private_key_pem: str, encrypt_random_key_b64: str) -> bytes:
    """RSA-PKCS1v15 解密 encrypt_random_key，返回 random_key 字节。

    Args:
        private_key_pem: PEM 格式 PKCS#1 或 PKCS#8 RSA 私钥（含 BEGIN/END 头）。
        encrypt_random_key_b64: 企微下发的 encrypt_random_key（base64）。

    Returns:
        random_key 字节（典型 32 字节，可 UTF-8 解码为字符串后传给 SDK DecryptData）。

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
