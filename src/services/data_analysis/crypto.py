"""
数据分析 - 加密工具

复用项目已有的 encryption_manager 进行加解密。
"""

from src.db.encryption import encryption_manager


def encrypt_password(plain: str) -> str:
    """加密密码"""
    return encryption_manager.encrypt(plain)


def decrypt_password(encrypted: str) -> str:
    """解密密码"""
    return encryption_manager.decrypt(encrypted)
