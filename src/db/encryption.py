"""加密管理器

提供基于 Fernet 的对称加密/解密功能
"""

import base64
import hashlib

from loguru import logger
from cryptography.fernet import Fernet


class EncryptionManager:
    """加密管理器"""

    def __init__(self):
        """初始化加密密钥"""
        key_env = None
        try:
            from src.config.settings import settings
            key_env = getattr(settings, 'encryption_key', None)
        except:
            pass

        if key_env:
            if isinstance(key_env, str):
                self.fernet = Fernet(key_env)
            else:
                self.fernet = Fernet(key_env)
        else:
            project_identifier = "aid-work-agent-encryption-key-v1"
            derived_key = base64.urlsafe_b64encode(
                hashlib.sha256(project_identifier.encode()).digest()
            )
            self.fernet = Fernet(derived_key)

    def encrypt(self, data: str) -> str:
        """加密字符串"""
        if not data:
            return data
        encrypted = self.fernet.encrypt(data.encode())
        return base64.urlsafe_b64encode(encrypted).decode()

    def decrypt(self, encrypted_data: str) -> str:
        """解密字符串"""
        if not encrypted_data:
            return encrypted_data
        try:
            decoded = base64.urlsafe_b64decode(encrypted_data.encode())
            decrypted = self.fernet.decrypt(decoded)
            return decrypted.decode()
        except Exception as e:
            logger.error(f"解密失败: {e}")
            raise ValueError("解密失败")


encryption_manager = EncryptionManager()
