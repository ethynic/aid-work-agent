"""用户邮箱配置数据库访问模型

管理用户绑定的邮箱 SMTP/IMAP 配置，密码加密存储
"""

from typing import Optional, Dict, Any

from loguru import logger

from src.db.database import get_db_connection
from src.db.remote_credential import encryption_manager


class EmailCredentialDB:
    """用户邮箱配置数据库访问类"""

    @staticmethod
    def upsert(user_id: str, config: Dict[str, Any]) -> bool:
        """
        创建或更新用户邮箱配置（upsert by user_id）

        Args:
            user_id: 用户ID
            config: 邮箱配置字典，包含 email_address, smtp_server, smtp_port, smtp_user,
                    smtp_password, smtp_encryption, imap_server, imap_port, imap_encryption

        Returns:
            是否成功
        """
        # 加密密码
        encrypted_password = encryption_manager.encrypt(config["smtp_password"])

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 先尝试更新
            cursor.execute("""
                UPDATE user_email_settings
                SET email_address = ?, smtp_server = ?, smtp_port = ?, smtp_user = ?,
                    smtp_password = ?, smtp_encryption = ?, imap_server = ?,
                    imap_port = ?, imap_encryption = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND status = 1
            """, (
                config["email_address"],
                config["smtp_server"],
                config["smtp_port"],
                config["smtp_user"],
                encrypted_password,
                config.get("smtp_encryption", "ssl"),
                config["imap_server"],
                config["imap_port"],
                config.get("imap_encryption", "ssl"),
                user_id,
            ))

            if cursor.rowcount > 0:
                conn.commit()
                logger.info(f"更新用户邮箱配置: user_id={user_id}")
                return True

            # 不存在则插入
            cursor.execute("""
                INSERT INTO user_email_settings (
                    user_id, email_address, smtp_server, smtp_port, smtp_user,
                    smtp_password, smtp_encryption, imap_server, imap_port,
                    imap_encryption, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                user_id,
                config["email_address"],
                config["smtp_server"],
                config["smtp_port"],
                config["smtp_user"],
                encrypted_password,
                config.get("smtp_encryption", "ssl"),
                config["imap_server"],
                config["imap_port"],
                config.get("imap_encryption", "ssl"),
            ))
            conn.commit()
            logger.info(f"创建用户邮箱配置: user_id={user_id}")
            return True

    @staticmethod
    def get_by_user(user_id: str) -> Optional[Dict[str, Any]]:
        """
        获取用户邮箱配置（解密密码）

        Args:
            user_id: 用户ID

        Returns:
            配置字典或 None
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM user_email_settings
                WHERE user_id = ? AND status = 1
            """, (user_id,))
            row = cursor.fetchone()

            if not row:
                return None

            credential = dict(row)
            # 解密密码
            try:
                credential["smtp_password"] = encryption_manager.decrypt(credential["smtp_password"])
            except Exception as e:
                logger.error(f"解密邮箱密码失败: {e}")
                credential["smtp_password"] = ""

            return credential

    @staticmethod
    def get_masked_by_user(user_id: str) -> Optional[Dict[str, Any]]:
        """
        获取用户邮箱配置（密码掩码，用于 API 返回）

        Args:
            user_id: 用户ID

        Returns:
            配置字典（密码掩码）或 None
        """
        config = EmailCredentialDB.get_by_user(user_id)
        if config:
            config["smtp_password"] = "****"
        return config

    @staticmethod
    def delete(user_id: str) -> bool:
        """
        删除用户邮箱配置（软删除）

        Args:
            user_id: 用户ID

        Returns:
            是否成功
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE user_email_settings
                SET status = 0, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND status = 1
            """, (user_id,))
            conn.commit()

            success = cursor.rowcount > 0
            if success:
                logger.info(f"删除用户邮箱配置: user_id={user_id}")
            return success

    @staticmethod
    def get_user_email_model(user_id: str):
        """
        获取用户的 UserEmail Pydantic 模型实例（供工具直接使用）

        Args:
            user_id: 用户ID

        Returns:
            UserEmail 实例或 None
        """
        config = EmailCredentialDB.get_by_user(user_id)
        if not config:
            return None

        try:
            from src.models.user import UserEmail, EncryptionType

            return UserEmail(
                email_address=config["email_address"],
                smtp_server=config["smtp_server"],
                smtp_port=config["smtp_port"],
                smtp_user=config["smtp_user"],
                smtp_password=config["smtp_password"],
                smtp_encryption=EncryptionType(config.get("smtp_encryption", "ssl")),
                imap_server=config["imap_server"],
                imap_port=config["imap_port"],
                imap_encryption=EncryptionType(config.get("imap_encryption", "ssl")),
            )
        except Exception as e:
            logger.error(f"构造 UserEmail 模型失败: {e}")
            return None
