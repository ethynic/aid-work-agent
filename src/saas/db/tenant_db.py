"""
租户 CRUD 操作
"""

import json
import uuid
from typing import Optional, List, Dict, Any

from loguru import logger

from src.db.database import get_db_connection
from src.saas.db.subscription_db import SubscriptionDB


class TenantDB:
    """租户数据库访问类"""

    @staticmethod
    def create(
        company_name: str,
        contact_name: Optional[str] = None,
        contact_phone: Optional[str] = None,
        initial_admin_name: Optional[str] = None,
        initial_admin_phone: Optional[str] = None,
        plan: str = "basic",
        max_instances: int = 5,
        max_users: int = 50,
        settings: Optional[dict] = None,
        expire_at=None,
    ) -> Optional[Dict[str, Any]]:
        """创建租户"""
        tenant_id = f"tenant_{uuid.uuid4().hex[:12]}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO tenants (tenant_id, company_name, contact_name, contact_phone,
                                        initial_admin_name, initial_admin_phone,
                                        plan, max_instances, max_users, settings, expire_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    tenant_id, company_name, contact_name, contact_phone,
                    initial_admin_name, initial_admin_phone,
                    plan, max_instances, max_users,
                    json.dumps(settings or {}, ensure_ascii=False),
                    expire_at,
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
            cursor.execute("SELECT * FROM tenants WHERE tenant_id = %s", (tenant_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["settings"] = json.loads(d["settings"]) if d.get("settings") else {}
                # 添加已授权数字员工数量
                d["agent_count"] = SubscriptionDB.count_active_subscriptions(conn, tenant_id)
                return d
            return None

    @staticmethod
    def update(tenant_id: str, **kwargs) -> bool:
        """更新租户信息"""
        allowed_fields = {
            "company_name", "contact_name", "contact_phone",
            "initial_admin_name", "initial_admin_phone",
            "plan", "status", "max_instances", "max_users", "settings",
            "expire_at",
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
        set_clause = ", ".join(f"{k} = %s" if k != "updated_at" else f"{k} = CURRENT_TIMESTAMP" for k in updates)
        values = [v for k, v in updates.items() if k != "updated_at"]

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE tenants SET {set_clause} WHERE tenant_id = %s", (*values, tenant_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def list_tenants(status: Optional[str] = None, page: int = 1, page_size: int = 20) -> dict:
        """列出租户（分页）

        Returns:
            {"tenants": [...], "total": int, "page": int, "page_size": int}
        """
        offset = (page - 1) * page_size
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM tenants WHERE status = %s",
                    (status,),
                )
                total = cursor.fetchone()["cnt"]
                cursor.execute(
                    "SELECT * FROM tenants WHERE status = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    (status, page_size, offset),
                )
            else:
                cursor.execute("SELECT COUNT(*) as cnt FROM tenants")
                total = cursor.fetchone()["cnt"]
                cursor.execute(
                    "SELECT * FROM tenants ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    (page_size, offset),
                )
            rows = cursor.fetchall()
            results = []
            for row in rows:
                d = dict(row)
                d["settings"] = json.loads(d["settings"]) if d.get("settings") else {}
                # 添加已授权数字员工数量
                tenant_id = d["tenant_id"]
                count = SubscriptionDB.count_active_subscriptions(conn, tenant_id)
                d["agent_count"] = count
                results.append(d)
            return {"tenants": results, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def delete(tenant_id: str) -> bool:
        """删除租户（软删除，设置 status = deactivated）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tenants SET status = 'deactivated', updated_at = CURRENT_TIMESTAMP WHERE tenant_id = %s",
                (tenant_id,),
            )
            conn.commit()
            if cursor.rowcount > 0:
                logger.info(f"Tenant deleted: {tenant_id}")
                return True
            return False

    @staticmethod
    def get_stats(tenant_id: str) -> Dict[str, Any]:
        """获取租户统计信息"""
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 实例数
            cursor.execute(
                "SELECT COUNT(*) as count FROM agent_instances WHERE tenant_id = %s",
                (tenant_id,),
            )
            instance_count = cursor.fetchone()["count"]

            # 用户数（使用 users 表）
            cursor.execute(
                "SELECT COUNT(*) as count FROM users WHERE tenant_id = %s AND status = 'active' AND role != 'platform_admin'",
                (tenant_id,),
            )
            user_count = cursor.fetchone()["count"]

            # 管理员数（使用 users 表）
            cursor.execute(
                "SELECT COUNT(*) as count FROM users WHERE tenant_id = %s AND role = 'tenant_admin' AND status = 'active'",
                (tenant_id,),
            )
            admin_count = cursor.fetchone()["count"]

            # 活跃订阅数
            cursor.execute(
                "SELECT COUNT(*) as count FROM subscriptions WHERE tenant_id = %s AND status = 'active'",
                (tenant_id,),
            )
            active_subscriptions = cursor.fetchone()["count"]

            return {
                "instance_count": instance_count,
                "user_count": user_count,
                "admin_count": admin_count,
                "active_subscriptions": active_subscriptions,
            }
