"""
租户用户映射 CRUD 操作
"""

import uuid
from typing import Optional, List, Dict, Any

from loguru import logger

from src.db.database import get_db_connection, get_db_placeholder


class TenantUserDB:
    """租户用户映射数据库访问类"""

    @staticmethod
    def create(
        tenant_id: str,
        user_id: str,
        department: Optional[str] = None,
        role: str = "user",
        source: str = "admin_manual",
    ) -> Optional[Dict[str, Any]]:
        """创建租户用户映射"""
        mapping_id = f"tu_{uuid.uuid4().hex[:12]}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO tenant_users (mapping_id, tenant_id, user_id, department, role, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (mapping_id, tenant_id, user_id, department, role, source))
                conn.commit()
                logger.info(f"Tenant user mapping created: {user_id} -> {tenant_id}")
                return TenantUserDB.get_by_mapping_id(mapping_id)
            except Exception as e:
                logger.error(f"Failed to create tenant user mapping: {e}")
                return None

    @staticmethod
    def get_by_mapping_id(mapping_id: str) -> Optional[Dict[str, Any]]:
        """根据 mapping_id 获取映射"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tenant_users WHERE mapping_id = ?", (mapping_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_user_and_tenant(user_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """根据用户和租户获取映射"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tenant_users WHERE user_id = ? AND tenant_id = ? AND status = 1",
                (user_id, tenant_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_tenants_by_user(user_id: str) -> List[Dict[str, Any]]:
        """获取用户所属的所有租户"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT tu.*, t.company_name
                FROM tenant_users tu
                JOIN tenants t ON t.tenant_id = tu.tenant_id
                WHERE tu.user_id = ? AND tu.status = 1
            """, (user_id,))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def list_by_tenant(tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """列出租户下所有用户"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            placeholder = get_db_placeholder()
            # PostgreSQL 不支持 LIMIT ?，需要直接拼接
            cursor.execute(f"""
                SELECT tu.*, u.username, u.phone
                FROM tenant_users tu
                LEFT JOIN users u ON u.user_id = tu.user_id
                WHERE tu.tenant_id = {placeholder} AND tu.status = 1
                ORDER BY tu.created_at DESC
                LIMIT {limit}
            """, (tenant_id,))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def update(mapping_id: str, **kwargs) -> bool:
        """更新映射信息"""
        allowed_fields = {"department", "role"}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields and v is not None}

        if not updates:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values())

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE tenant_users SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE mapping_id = ?",
                (*values, mapping_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete(mapping_id: str) -> bool:
        """软删除映射（设 status=0）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tenant_users SET status = 0, updated_at = CURRENT_TIMESTAMP
                WHERE mapping_id = ?
            """, (mapping_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def batch_create(tenant_id: str, users: List[Dict[str, Any]]) -> int:
        """批量创建用户映射"""
        count = 0
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for user_data in users:
                mapping_id = f"tu_{uuid.uuid4().hex[:12]}"
                try:
                    cursor.execute("""
                        INSERT INTO tenant_users (mapping_id, tenant_id, user_id, department, role, source)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        mapping_id, tenant_id,
                        user_data["user_id"],
                        user_data.get("department"),
                        user_data.get("role", "user"),
                        user_data.get("source", "batch_import"),
                    ))
                    count += 1
                except Exception as e:
                    logger.warning(f"Skip user mapping {user_data.get('user_id')}: {e}")
            conn.commit()
        return count
