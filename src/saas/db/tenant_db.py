"""
租户 CRUD 操作
"""

import json
import uuid
from typing import Optional, List, Dict, Any

from loguru import logger

from src.db.database import get_db_connection, get_db_placeholder


class TenantDB:
    """租户数据库访问类"""

    @staticmethod
    def create(
        company_name: str,
        contact_name: Optional[str] = None,
        contact_phone: Optional[str] = None,
        plan: str = "basic",
        max_instances: int = 5,
        max_users: int = 50,
        settings: Optional[dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """创建租户"""
        tenant_id = f"tenant_{uuid.uuid4().hex[:12]}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO tenants (tenant_id, company_name, contact_name, contact_phone,
                                        plan, max_instances, max_users, settings)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    tenant_id, company_name, contact_name, contact_phone,
                    plan, max_instances, max_users,
                    json.dumps(settings or {}, ensure_ascii=False),
                ))
                conn.commit()
                logger.info(f"Tenant created: {tenant_id} ({company_name})")
                return TenantDB.get_by_id(tenant_id)
            except Exception as e:
                logger.error(f"Failed to create tenant: {e}")
                return None

    @staticmethod
    def get_by_id(tenant_id: str) -> Optional[Dict[str, Any]]:
        """根据 tenant_id 获取租户"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["settings"] = json.loads(d["settings"]) if d.get("settings") else {}
                return d
            return None

    @staticmethod
    def update(tenant_id: str, **kwargs) -> bool:
        """更新租户信息"""
        allowed_fields = {
            "company_name", "contact_name", "contact_phone",
            "plan", "status", "max_instances", "max_users", "settings",
        }
        updates = {}
        for k, v in kwargs.items():
            if k in allowed_fields and v is not None:
                if k == "settings" and isinstance(v, dict):
                    v = json.dumps(v, ensure_ascii=False)
                updates[k] = v

        if not updates:
            return False

        updates["updated_at"] = "CURRENT_TIMESTAMP"
        set_clause = ", ".join(f"{k} = ?" if k != "updated_at" else f"{k} = CURRENT_TIMESTAMP" for k in updates)
        values = [v for k, v in updates.items() if k != "updated_at"]

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE tenants SET {set_clause} WHERE tenant_id = ?", (*values, tenant_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def list_tenants(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """列出租户"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            placeholder = get_db_placeholder()
            # PostgreSQL 不支持 LIMIT ?，需要直接拼接
            if status:
                cursor.execute(
                    f"SELECT * FROM tenants WHERE status = {placeholder} ORDER BY created_at DESC LIMIT {limit}",
                    (status,),
                )
            else:
                cursor.execute(
                    f"SELECT * FROM tenants ORDER BY created_at DESC LIMIT {limit}",
                )
            rows = cursor.fetchall()
            results = []
            for row in rows:
                d = dict(row)
                d["settings"] = json.loads(d["settings"]) if d.get("settings") else {}
                results.append(d)
            return results

    @staticmethod
    def get_stats(tenant_id: str) -> Dict[str, Any]:
        """获取租户统计信息"""
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 实例数
            cursor.execute(
                "SELECT COUNT(*) as count FROM agent_instances WHERE tenant_id = ?",
                (tenant_id,),
            )
            instance_count = cursor.fetchone()["count"]

            # 用户数（使用 users 表）
            cursor.execute(
                "SELECT COUNT(*) as count FROM users WHERE tenant_id = ? AND status = 1 AND role != 'platform_admin'",
                (tenant_id,),
            )
            user_count = cursor.fetchone()["count"]

            # 管理员数（使用 users 表）
            cursor.execute(
                "SELECT COUNT(*) as count FROM users WHERE tenant_id = ? AND role = 'tenant_admin' AND status = 1",
                (tenant_id,),
            )
            admin_count = cursor.fetchone()["count"]

            # 活跃订阅数
            cursor.execute(
                "SELECT COUNT(*) as count FROM subscriptions WHERE tenant_id = ? AND status = 'active'",
                (tenant_id,),
            )
            active_subscriptions = cursor.fetchone()["count"]

            return {
                "instance_count": instance_count,
                "user_count": user_count,
                "admin_count": admin_count,
                "active_subscriptions": active_subscriptions,
            }
