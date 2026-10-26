"""租户邮箱配置数据库访问模型

租户级邮箱（SMTP/IMAP）配置，密码加密存储。作为邮箱三层解析的第 2 层兜底：
用户个人邮箱（user_email_settings）-> 租户邮箱（tenant_email_settings）-> 报错提示。
系统 .env 邮箱仅用于平台级通知，不进入本表。
"""

from typing import Optional, Dict, Any

from loguru import logger

from src.db.database import get_db_connection
from src.db.encryption import encryption_manager


class TenantEmailCredentialDB:
    """租户邮箱配置数据库访问类"""

    @staticmethod
    def upsert(tenant_id: str, config: Dict[str, Any]) -> bool:
        """创建或更新租户邮箱配置（upsert by tenant_id，密码加密）"""
        encrypted_password = encryption_manager.encrypt(config["smtp_password"])

        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE tenant_email_settings
                SET email_address = %s, smtp_server = %s, smtp_port = %s, smtp_user = %s,
                    smtp_password = %s, smtp_encryption = %s, imap_server = %s,
                    imap_port = %s, imap_encryption = %s, updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = %s AND status = 'active'
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
                tenant_id,
            ))

            if cursor.rowcount > 0:
                conn.commit()
                logger.info(f"更新租户邮箱配置: tenant_id={tenant_id}")
                return True

            # UPDATE 未命中（首次配置，或软删除后重配）：同 user_email_settings 的
            # 处理——同事务先清旧记录（含软删除，避免唯一冲突）再插入，一次 commit
            cursor.execute("DELETE FROM tenant_email_settings WHERE tenant_id = %s",
                           (tenant_id,))
            if cursor.rowcount > 0:
                logger.info(f"清除旧租户邮箱配置（含软删除记录）: tenant_id={tenant_id}")

            cursor.execute("""
                INSERT INTO tenant_email_settings (
                    tenant_id, email_address, smtp_server, smtp_port, smtp_user,
                    smtp_password, smtp_encryption, imap_server, imap_port,
                    imap_encryption, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
            """, (
                tenant_id,
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
            logger.info(f"创建租户邮箱配置: tenant_id={tenant_id}")
            return True

    @staticmethod
    def get_by_tenant(tenant_id: str) -> Optional[Dict[str, Any]]:
        """获取租户邮箱配置（密码解密，仅内存使用，调用方不得打印/落盘/返回明文）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM tenant_email_settings
                WHERE tenant_id = %s AND status = 'active'
            """, (tenant_id,))
            row = cursor.fetchone()

            if not row:
                return None

            credential = dict(row)
            try:
                credential["smtp_password"] = encryption_manager.decrypt(credential["smtp_password"])
            except Exception as e:
                logger.opt(exception=True).error(f"解密租户邮箱密码失败: {e}")
                credential["smtp_password"] = ""

            return credential

    @staticmethod
    def get_masked_by_tenant(tenant_id: str) -> Optional[Dict[str, Any]]:
        """获取租户邮箱配置（密码掩码，用于 API 返回）"""
        config = TenantEmailCredentialDB.get_by_tenant(tenant_id)
        if config:
            config["smtp_password"] = "****"
        return config

    @staticmethod
    def delete(tenant_id: str) -> bool:
        """删除租户邮箱配置（软删除）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tenant_email_settings
                SET status = 'inactive', updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = %s AND status = 'active'
            """, (tenant_id,))
            conn.commit()

            success = cursor.rowcount > 0
            if success:
                logger.info(f"删除租户邮箱配置: tenant_id={tenant_id}")
            return success
