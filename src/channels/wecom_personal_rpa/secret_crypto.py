"""企业微信个人账号 RPA 渠道客户端密钥加解密

本模块负责 wecom_rpa_clients.encrypted_secret 字段的对称加解密。

主密钥来源（优先级递减）：
1. ``settings.app.secret_key``（当前 AppConfig 未定义该字段，保留以备未来扩展）
2. 环境变量 ``RPA_SECRET_KEY``
3. 两者皆缺失 → 抛出明确启动错误（``RuntimeError``），禁止静默使用弱默认值。

安全约束：
- 绝不把明文 secret 写入日志（loguru.error 仅记录异常类型与脱敏信息）。
- 加密算法采用 Fernet（AES-128-CBC + HMAC-SHA256），密文为 urlsafe base64 字符串，
  可直接存入 ``encrypted_secret`` TEXT 字段。
- Fernet 密钥要求 32 字节 urlsafe base64；本模块用 SHA-256 派生固定 32 字节密钥，
  因此主密钥可为任意长度的随机串。

相关契约：docs/system/wecom-personal-rpa-protocol.md §A.1（client_secret 仅在服务端解密后参与签名）。
"""

import base64
import hashlib
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

# 主密钥环境变量名
_RPA_SECRET_KEY_ENV = "RPA_SECRET_KEY"

# 模块级 Fernet 实例占位（惰性初始化：仅首次 encrypt/decrypt 时在 _get_fernet 内初始化；
# 缺失主密钥时在该调用点抛 RuntimeError，import 期不初始化、不抛——保证 app 无密钥也能启动）
_fernet: Optional[Fernet] = None


def _load_master_key() -> str:
    """加载主密钥。优先 settings.app.secret_key，其次 RPA_SECRET_KEY 环境变量。

    缺失时抛 RuntimeError（明确启动错误，不使用弱默认值）。
    """
    # 1. 尝试从 settings.app.secret_key 读取（若未来 AppConfig 扩展该字段）
    try:
        from src.config.settings import settings  # 绝对路径 import
        app_cfg = getattr(settings, "app", None)
        if app_cfg is not None:
            secret_key = getattr(app_cfg, "secret_key", None)
            if secret_key:
                return secret_key
    except Exception as e:
        # 配置加载失败不一定是致命错误（可能仅缺少该字段），降级到环境变量
        logger.debug(f"settings.app.secret_key 不可用，降级到环境变量: {e}")

    # 2. 环境变量 RPA_SECRET_KEY
    env_key = os.environ.get(_RPA_SECRET_KEY_ENV)
    if env_key:
        return env_key

    # 3. 缺失：明确启动错误
    raise RuntimeError(
        "RPA 客户端密钥加解密无法初始化：未找到主密钥。"
        f"请在配置 settings.app.secret_key 或环境变量 {_RPA_SECRET_KEY_ENV} 中设置一个强随机串。"
    )


def _get_fernet() -> Fernet:
    """获取（惰性初始化）Fernet 实例。"""
    global _fernet
    if _fernet is None:
        master_key = _load_master_key()
        # Fernet 要求 32 字节 urlsafe base64 密钥；用 SHA-256 派生固定长度密钥
        derived = hashlib.sha256(master_key.encode("utf-8")).digest()
        fernet_key = base64.urlsafe_b64encode(derived)
        _fernet = Fernet(fernet_key)
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    """加密客户端 secret，返回可入库存入 encrypted_secret 字段的密文字符串。

    Args:
        plaintext: 客户端 secret 明文。

    Returns:
        urlsafe base64 密文字符串。

    Raises:
        RuntimeError: 主密钥缺失。
        ValueError: plaintext 为空。
    """
    if not plaintext:
        raise ValueError("encrypt_secret: plaintext 不能为空")
    try:
        fernet = _get_fernet()
        token = fernet.encrypt(plaintext.encode("utf-8"))
        # Fernet.encrypt 已返回 urlsafe base64 bytes，解码为 str 直接入库
        return token.decode("ascii")
    except RuntimeError:
        # 主密钥缺失，向上抛出明确启动错误
        raise
    except Exception as e:
        # 绝不记录明文；仅记录异常类型与脱敏信息
        logger.error(f"encrypt_secret 加密失败: {type(e).__name__}")
        raise


def decrypt_secret(ciphertext: str) -> bytes:
    """解密客户端 secret，返回原始 secret 字节供 HMAC 使用。

    Args:
        ciphertext: encrypted_secret 字段中存储的密文字符串。

    Returns:
        原始 secret 字节（供 auth.compute_signature 作为 HMAC key）。

    Raises:
        RuntimeError: 主密钥缺失。
        ValueError: 解密失败（密钥不匹配 / 密文损坏）。
    """
    if not ciphertext:
        raise ValueError("decrypt_secret: ciphertext 不能为空")
    try:
        fernet = _get_fernet()
        plaintext_bytes = fernet.decrypt(ciphertext.encode("ascii"))
        return plaintext_bytes
    except RuntimeError:
        raise
    except InvalidToken as e:
        logger.error(f"decrypt_secret 解密失败: 密文无效或主密钥不匹配")
        raise ValueError("解密失败：密文无效或主密钥不匹配") from e
    except Exception as e:
        logger.error(f"decrypt_secret 解密失败: {type(e).__name__}")
        raise ValueError(f"解密失败: {type(e).__name__}") from e
