"""
租户 CRUD 操作
"""

import json
import uuid
from typing import Optional, List, Dict, Any

from loguru import logger

from src.db.database import get_db_connection
from src.saas.db.subscription_db import SubscriptionDB
from src.saas.models.enums import TenantStatus
from src.core.cache_utils import CacheKeys, get_cached, set_cached, delete_cached, invalidate_tenant_cache


class TenantDB:
    """租户数据库访问类"""

    @staticmethod
    def create(
        company_name: str,
        tenant_code: str,
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
        tenant_code_upper = tenant_code.upper()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # 检查租户代码唯一性（大小写不敏感）
                cursor.execute("SELECT COUNT(*) as cnt FROM tenants WHERE UPPER(tenant_code) = %s", (tenant_code_upper,))
                if cursor.fetchone()["cnt"] > 0:
                    logger.error(f"Tenant code already exists: {tenant_code}")
                    return None

                cursor.execute("""
                    INSERT INTO tenants (tenant_id, company_name, tenant_code, contact_name, contact_phone,
                                        initial_admin_name, initial_admin_phone,
                                        plan, max_instances, max_users, settings, expire_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    tenant_id, company_name, tenant_code_upper, contact_name, contact_phone,
                    initial_admin_name, initial_admin_phone,
                    plan, max_instances, max_users,
                    json.dumps(settings or {}, ensure_ascii=False),
                    expire_at,
                ))
                conn.commit()
                logger.info(f"Tenant created: {tenant_id} ({company_name}) code: {tenant_code_upper}")
                return TenantDB.get_by_id(tenant_id)
            except Exception as e:
                logger.error(f"Failed to create tenant: {e}")
                return None

    @staticmethod
    def get_by_id(tenant_id: str) -> Optional[Dict[str, Any]]:
        """根据 tenant_id 获取租户（优先从 Redis 缓存读取，TTL 30分钟）"""
        cached = get_cached(CacheKeys.TENANT, tenant_id)
        if cached is not None:
            return cached

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tenants WHERE tenant_id = %s", (tenant_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["settings"] = json.loads(d["settings"]) if d.get("settings") else {}
                # 添加已授权数字员工数量
                d["agent_count"] = SubscriptionDB.count_active_subscriptions(conn, tenant_id)
                set_cached(CacheKeys.TENANT, tenant_id, value=d, ttl=1800)
                return d
            return None

    @staticmethod
    def get_by_code(tenant_code: str) -> Optional[Dict[str, Any]]:
        """根据 tenant_code 获取租户（大小写不敏感，优先从 Redis 缓存读取，TTL 30分钟）"""
        code_upper = tenant_code.upper()
        cached = get_cached(CacheKeys.TENANT_CODE, code_upper)
        if cached is not None:
            return cached

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tenants WHERE UPPER(tenant_code) = UPPER(%s)", (tenant_code,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["settings"] = json.loads(d["settings"]) if d.get("settings") else {}
                # 添加已授权数字员工数量
                d["agent_count"] = SubscriptionDB.count_active_subscriptions(conn, d["tenant_id"])
                set_cached(CacheKeys.TENANT_CODE, code_upper, value=d, ttl=1800)
                set_cached(CacheKeys.TENANT, d["tenant_id"], value=d, ttl=1800)
                return d
            return None

    @staticmethod
    def update(tenant_id: str, **kwargs) -> bool:
        """更新租户信息"""
        allowed_fields = {
            "company_name", "contact_name", "contact_phone",
            "initial_admin_name", "initial_admin_phone",
            "plan", "status", "max_instances", "max_users", "settings",
            "expire_at", "tenant_code",
        }
        updates = {}
        for k, v in kwargs.items():
            if k in allowed_fields and v is not None:
                if k == "settings" and isinstance(v, dict):
                    v = json.dumps(v, ensure_ascii=False)
                elif k == "tenant_code":
                    v = v.upper()
                updates[k] = v

        if not updates:
            return False

        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 检查租户代码唯一性（如果正在更新）
            if "tenant_code" in updates:
                new_code = updates["tenant_code"]
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM tenants WHERE UPPER(tenant_code) = %s AND tenant_id != %s",
                    (new_code, tenant_id)
                )
                if cursor.fetchone()["cnt"] > 0:
                    logger.error(f"Tenant code already exists: {new_code}")
                    return False

            updates["updated_at"] = "CURRENT_TIMESTAMP"
            set_clause = ", ".join(f"{k} = %s" if k != "updated_at" else f"{k} = CURRENT_TIMESTAMP" for k in updates)
            values = [v for k, v in updates.items() if k != "updated_at"]

            cursor.execute(f"UPDATE tenants SET {set_clause} WHERE tenant_id = %s", (*values, tenant_id))
            conn.commit()
            result = cursor.rowcount > 0
            if result:
                # 清除租户所有缓存
                invalidate_tenant_cache(tenant_id)
                # 如果更改了 tenant_code，还需要清除旧的 tenant_code 缓存和新 code 的缓存
                if "tenant_code" in updates:
                    delete_cached(CacheKeys.TENANT_CODE, updates["tenant_code"])
            return result

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
                f"UPDATE tenants SET status = '{TenantStatus.DEACTIVATED.value}', updated_at = CURRENT_TIMESTAMP WHERE tenant_id = %s",
                (tenant_id,),
            )
            conn.commit()
            if cursor.rowcount > 0:
                logger.info(f"Tenant deleted: {tenant_id}")
                invalidate_tenant_cache(tenant_id)
                return True
            return False

    @staticmethod
    def get_stats(tenant_id: str) -> Dict[str, Any]:
        """获取租户统计信息（优先从 Redis 缓存读取，TTL 60秒）"""
        cached = get_cached(CacheKeys.TENANT_STATS, tenant_id)
        if cached is not None:
            return cached

        with get_db_connection() as conn:
            cursor = conn.cursor()

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

            result = {
                "user_count": user_count,
                "admin_count": admin_count,
                "active_subscriptions": active_subscriptions,
            }
            set_cached(CacheKeys.TENANT_STATS, tenant_id, value=result, ttl=60)
            return result
