"""
租户管理员 CRUD 操作
"""

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from loguru import logger

from src.db.database import get_db_connection


class TenantAdminDB:
    """租户管理员数据库访问类"""

    @staticmethod
    def create(
        tenant_id: str,
        phone: str,
        name: Optional[str] = None,
        password: Optional[str] = None,
        role: str = "admin",
        sso_provider: Optional[str] = None,
        sso_uid: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """创建管理员"""
        admin_id = f"admin_{uuid.uuid4().hex[:12]}"
        password_hash = hashlib.sha256(password.encode()).hexdigest() if password else None

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO tenant_admins
                        (admin_id, tenant_id, phone, name, password_hash, role, sso_provider, sso_uid)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (admin_id, tenant_id, phone, name, password_hash, role, sso_provider, sso_uid))
                conn.commit()
                logger.info(f"Tenant admin created: {admin_id} for tenant {tenant_id}")
                return TenantAdminDB.get_by_id(admin_id)
            except Exception as e:
                logger.error(f"Failed to create tenant admin: {e}")
                return None

    @staticmethod
    def get_by_id(admin_id: str) -> Optional[Dict[str, Any]]:
        """根据 admin_id 获取管理员"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tenant_admins WHERE admin_id = ?", (admin_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_phone(phone: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """根据手机号获取管理员"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id:
                cursor.execute(
                    "SELECT * FROM tenant_admins WHERE phone = ? AND tenant_id = ? AND status = 1",
                    (phone, tenant_id),
                )
            else:
                cursor.execute(
                    "SELECT * FROM tenant_admins WHERE phone = ? AND status = 1",
                    (phone,),
                )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_sso(sso_provider: str, sso_uid: str) -> Optional[Dict[str, Any]]:
        """根据 SSO 信息获取管理员"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tenant_admins WHERE sso_provider = ? AND sso_uid = ? AND status = 1",
                (sso_provider, sso_uid),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_by_tenant(tenant_id: str) -> list:
        """列出租户下所有管理员"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tenant_admins WHERE tenant_id = ? AND status = 1 ORDER BY created_at",
                (tenant_id,),
            )
            return [dict(row) for row in cursor.fetchall()]


class TenantAdminTokenDB:
    """管理员 Token 数据库访问类"""

    @staticmethod
    def create(admin_id: str, tenant_id: str, expires_days: int = 7) -> Optional[str]:
        """生成管理员 token"""
        import secrets
        token = f"saas_{secrets.token_urlsafe(32)}"
        expires_at = (datetime.now() + timedelta(days=expires_days)).strftime("%Y-%m-%d %H:%M:%S")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO tenant_admin_tokens (token, admin_id, tenant_id, expires_at)
                    VALUES (?, ?, ?, ?)
                """, (token, admin_id, tenant_id, expires_at))
                conn.commit()
                return token
            except Exception as e:
                logger.error(f"Failed to create admin token: {e}")
                return None

    @staticmethod
    def verify(token: str) -> Optional[Dict[str, Any]]:
        """验证 token，返回 {admin_id, tenant_id} 或 None"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.admin_id, t.tenant_id, t.expires_at
                FROM tenant_admin_tokens t
                WHERE t.token = ?
            """, (token,))
            row = cursor.fetchone()

            if not row:
                return None

            # 检查过期
            if datetime.now() > datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S"):
                cursor.execute("DELETE FROM tenant_admin_tokens WHERE token = ?", (token,))
                conn.commit()
                return None

            return {"admin_id": row["admin_id"], "tenant_id": row["tenant_id"]}

    @staticmethod
    def delete(token: str) -> bool:
        """删除 token"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tenant_admin_tokens WHERE token = ?", (token,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def cleanup_expired() -> int:
        """清理过期 token"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM tenant_admin_tokens WHERE expires_at < ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
            )
            conn.commit()
            return cursor.rowcount
