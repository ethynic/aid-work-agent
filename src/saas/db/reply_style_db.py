"""
回复风格 CRUD 操作
"""

from typing import Optional, List, Dict, Any

from loguru import logger

from src.db.database import get_db_connection


SYSTEM_TENANT = "system"


class ReplyStyleDB:
    """回复风格数据库访问类"""

    @staticmethod
    def create(
        style_id: str,
        tenant_id: str,
        name: str,
        content: str,
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """创建风格（version=1, is_active=true）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO reply_styles (style_id, tenant_id, name, description, content, version, is_active)
                    VALUES (%s, %s, %s, %s, %s, 1, TRUE)
                """, (style_id, tenant_id, name, description, content))
                conn.commit()
                logger.info(f"Reply style created: {style_id} tenant={tenant_id}")
                return ReplyStyleDB.get_active(style_id, tenant_id)
            except Exception as e:
                logger.error(f"Failed to create reply style: {e}")
                return None

    @staticmethod
    def get_active(style_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """获取指定风格的当前激活版本"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM reply_styles WHERE style_id = %s AND tenant_id = %s AND is_active = TRUE",
                (style_id, tenant_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_active_by_tenant(tenant_id: str) -> List[Dict[str, Any]]:
        """列出租户的激活风格 + 系统内置风格（去重：租户自定义优先）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT ON (style_id) *
                FROM reply_styles
                WHERE (tenant_id = %s OR tenant_id = %s) AND is_active = TRUE
                ORDER BY style_id, tenant_id = %s DESC, version DESC
            """, (tenant_id, SYSTEM_TENANT, tenant_id))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def list_system_styles() -> List[Dict[str, Any]]:
        """列出所有系统内置激活风格"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM reply_styles WHERE tenant_id = %s AND is_active = TRUE",
                (SYSTEM_TENANT,),
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def list_all_active() -> List[Dict[str, Any]]:
        """列出所有激活风格（用于 StyleManager 缓存加载）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM reply_styles WHERE is_active = TRUE")
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def create_new_version(
        style_id: str,
        tenant_id: str,
        name: str,
        content: str,
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """创建新版本：将旧版本 is_active 设为 false，插入新版本"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # 获取当前最大版本号
                cursor.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM reply_styles WHERE style_id = %s AND tenant_id = %s",
                    (style_id, tenant_id),
                )
                max_version = cursor.fetchone()[0]
                new_version = max_version + 1

                # 将旧版本设为 inactive
                cursor.execute(
                    "UPDATE reply_styles SET is_active = FALSE WHERE style_id = %s AND tenant_id = %s AND is_active = TRUE",
                    (style_id, tenant_id),
                )

                # 插入新版本
                cursor.execute("""
                    INSERT INTO reply_styles (style_id, tenant_id, name, description, content, version, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                """, (style_id, tenant_id, name, description, content, new_version))
                conn.commit()
                logger.info(f"Reply style updated: {style_id} tenant={tenant_id} version={new_version}")
                return ReplyStyleDB.get_active(style_id, tenant_id)
            except Exception as e:
                logger.error(f"Failed to create new version: {e}")
                return None

    @staticmethod
    def delete_all_versions(style_id: str, tenant_id: str) -> bool:
        """删除风格的所有版本"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM reply_styles WHERE style_id = %s AND tenant_id = %s",
                (style_id, tenant_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def list_versions(style_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        """列出风格的所有版本（按版本号倒序）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM reply_styles WHERE style_id = %s AND tenant_id = %s ORDER BY version DESC",
                (style_id, tenant_id),
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def activate_version(style_id: str, tenant_id: str, version: int) -> bool:
        """激活指定版本（回滚）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # 将当前激活版本设为 inactive
                cursor.execute(
                    "UPDATE reply_styles SET is_active = FALSE WHERE style_id = %s AND tenant_id = %s AND is_active = TRUE",
                    (style_id, tenant_id),
                )
                # 激活目标版本
                cursor.execute(
                    "UPDATE reply_styles SET is_active = TRUE WHERE style_id = %s AND tenant_id = %s AND version = %s",
                    (style_id, tenant_id, version),
                )
                conn.commit()
                if cursor.rowcount > 0:
                    logger.info(f"Reply style activated: {style_id} tenant={tenant_id} version={version}")
                    return True
                return False
            except Exception as e:
                logger.error(f"Failed to activate version: {e}")
                return False

    @staticmethod
    def get_version(style_id: str, tenant_id: str, version: int) -> Optional[Dict[str, Any]]:
        """获取指定版本的风格"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM reply_styles WHERE style_id = %s AND tenant_id = %s AND version = %s",
                (style_id, tenant_id, version),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def exists(style_id: str, tenant_id: str) -> bool:
        """检查风格是否已存在"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM reply_styles WHERE style_id = %s AND tenant_id = %s LIMIT 1",
                (style_id, tenant_id),
            )
            return cursor.fetchone() is not None

    @staticmethod
    def is_system_style(style_id: str) -> bool:
        """检查是否为系统内置风格"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM reply_styles WHERE style_id = %s AND tenant_id = %s LIMIT 1",
                (style_id, SYSTEM_TENANT),
            )
            return cursor.fetchone() is not None
