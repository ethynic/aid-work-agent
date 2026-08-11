"""本地工具安全原语：配对码 / 设备 token / claim token 生成与哈希

只保存哈希，明文只在其生成的当次响应中返回。
"""

import hashlib
import secrets

# 8 位配对码字符集：大写字母+数字，去掉易混淆的 0/O/1/I
PAIR_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIR_CODE_LENGTH = 8


def generate_pair_code() -> str:
    """生成 8 位配对码（大写字母+数字，无易混淆字符）"""
    return "".join(secrets.choice(PAIR_CODE_ALPHABET) for _ in range(PAIR_CODE_LENGTH))


def generate_device_token() -> str:
    """生成 256-bit 设备 token（hex 64 字符）"""
    return secrets.token_hex(32)


def generate_claim_token() -> str:
    """生成一次性 claim token（hex 64 字符），仅在 claim 响应中返回明文"""
    return secrets.token_hex(32)


def sha256_hex(text: str) -> str:
    """sha256 哈希（hex），用于 token/code/fingerprint 落库"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
