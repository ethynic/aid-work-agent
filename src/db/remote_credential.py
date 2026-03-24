"""远程连接凭据数据库访问模型

用于管理 SMB/FTP 服务器的连接凭据
"""

import json
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from loguru import logger
from cryptography.fernet import Fernet
import base64

from src.db.database import get_db_connection


# ============== 加密密钥管理 ==============

class EncryptionManager:
    """加密管理器"""

    def __init__(self):
        """初始化加密密钥"""
        # 从环境变量或配置中获取密钥
        key_env = None
        try:
            from src.config.settings import settings
            key_env = getattr(settings, 'encryption_key', None)
        except:
            pass

        if key_env:
            # 如果是字符串，需要是 base64 编码的 32 字节密钥
            if isinstance(key_env, str):
                self.fernet = Fernet(key_env)
            else:
                self.fernet = Fernet(key_env)
        else:
            # 开发环境使用固定密钥（生产环境必须配置）
            logger.warning("使用开发环境加密密钥，生产环境请配置 ENCRYPTION_KEY 环境变量")
            # 生成一个有效的 Fernet 密钥
            self.fernet = Fernet(Fernet.generate_key())

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


# 全局加密管理器实例
encryption_manager = EncryptionManager()


# ============== 远程连接凭据数据库访问 ==============

class RemoteCredentialDB:
    """远程连接凭据数据库访问类"""

    @staticmethod
    def generate_credential_id() -> str:
        """生成唯一凭据ID"""
        return f"cred_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def create(
        user_id: str,
        connection_type: str,  # 'smb' 或 'ftp'
        server_host: str,
        server_port: int,
        username: str,
        password: str,
        remote_path: str,
        name: Optional[str] = None,
        domain: Optional[str] = None,  # SMB 域
        description: Optional[str] = None
    ) -> str:
        """
        创建远程连接凭据

        Args:
            user_id: 用户ID
            connection_type: 连接类型 ('smb' 或 'ftp')
            server_host: 服务器地址
            server_port: 服务器端口
            username: 用户名
            password: 密码（将被加密存储）
            remote_path: 远程路径
            name: 凭据名称（可选）
            domain: SMB 域（可选）
            description: 描述（可选）

        Returns:
            凭据ID
        """
        credential_id = RemoteCredentialDB.generate_credential_id()

        # 加密密码
        encrypted_password = encryption_manager.encrypt(password)

        # 生成默认名称
        if not name:
            name = f"{connection_type.upper()}://{server_host}{remote_path}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO remote_credentials (
                    credential_id,
                    user_id,
                    connection_type,
                    server_host,
                    server_port,
                    username,
                    password,
                    remote_path,
                    domain,
                    name,
                    description,
                    status,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, (
                credential_id,
                user_id,
                connection_type,
                server_host,
                server_port,
                username,
                encrypted_password,
                remote_path,
                domain,
                name,
                description
            ))
            conn.commit()
            logger.info(f"创建远程连接凭据: {credential_id}, 类型: {connection_type}")

        return credential_id

    @staticmethod
    def get_by_id(credential_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        根据ID获取凭据（解密密码）

        Args:
            credential_id: 凭据ID
            user_id: 用户ID（权限校验）

        Returns:
            凭据字典或None
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM remote_credentials
                WHERE credential_id = ? AND user_id = ? AND status = 1
            """, (credential_id, user_id))
            row = cursor.fetchone()

            if not row:
                return None

            credential = dict(row)
            # 解密密码
            try:
                credential['password'] = encryption_manager.decrypt(credential['password'])
            except Exception as e:
                logger.error(f"解密密码失败: {e}")
                credential['password'] = ''

            return credential

    @staticmethod
    def list_by_user(user_id: str, connection_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        获取用户的所有凭据列表（不包含密码）

        Args:
            user_id: 用户ID
            connection_type: 连接类型筛选（可选）

        Returns:
            凭据列表
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if connection_type:
                cursor.execute("""
                    SELECT credential_id, connection_type, server_host, server_port,
                           username, remote_path, domain, name, description,
                           status, created_at, updated_at
                    FROM remote_credentials
                    WHERE user_id = ? AND connection_type = ? AND status = 1
                    ORDER BY created_at DESC
                """, (user_id, connection_type))
            else:
                cursor.execute("""
                    SELECT credential_id, connection_type, server_host, server_port,
                           username, remote_path, domain, name, description,
                           status, created_at, updated_at
                    FROM remote_credentials
                    WHERE user_id = ? AND status = 1
                    ORDER BY created_at DESC
                """, (user_id,))

            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def find_by_path(user_id: str, remote_path: str) -> Optional[Dict[str, Any]]:
        """
        根据远程路径查找凭据（用于智能体自动匹配）

        Args:
            user_id: 用户ID
            remote_path: 远程路径

        Returns:
            凭据字典或None（不包含密码）
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT credential_id, connection_type, server_host, server_port,
                       username, remote_path, domain, name, description,
                       status, created_at, updated_at
                FROM remote_credentials
                WHERE user_id = ? AND remote_path = ? AND status = 1
                LIMIT 1
            """, (user_id, remote_path))
            row = cursor.fetchone()

            return dict(row) if row else None

    @staticmethod
    def update(credential_id: str, user_id: str, **kwargs) -> bool:
        """
        更新凭据

        Args:
            credential_id: 凭据ID
            user_id: 用户ID
            **kwargs: 要更新的字段

        Returns:
            是否成功
        """
        # 如果更新密码，需要加密
        if 'password' in kwargs:
            kwargs['password'] = encryption_manager.encrypt(kwargs['password'])

        # 生成更新语句
        valid_fields = {
            'connection_type', 'server_host', 'server_port', 'username',
            'password', 'remote_path', 'domain', 'name', 'description', 'status'
        }
        updates = {k: v for k, v in kwargs.items() if k in valid_fields}

        if not updates:
            return False

        set_clause = ', '.join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values()) + [credential_id, user_id]

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE remote_credentials
                SET {set_clause}, updated_at = CURRENT_TIMESTAMP
                WHERE credential_id = ? AND user_id = ?
            """, values)
            conn.commit()

            success = cursor.rowcount > 0
            if success:
                logger.info(f"更新远程连接凭据: {credential_id}")

            return success

    @staticmethod
    def delete(credential_id: str, user_id: str) -> bool:
        """
        删除凭据（软删除）

        Args:
            credential_id: 凭据ID
            user_id: 用户ID

        Returns:
            是否成功
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE remote_credentials
                SET status = 0, updated_at = CURRENT_TIMESTAMP
                WHERE credential_id = ? AND user_id = ?
            """, (credential_id, user_id))
            conn.commit()

            success = cursor.rowcount > 0
            if success:
                logger.info(f"删除远程连接凭据: {credential_id}")

            return success
