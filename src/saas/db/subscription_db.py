"""
订阅 CRUD 操作
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from loguru import logger

from src.db.database import get_db_connection


class SubscriptionDB:
    """订阅数据库访问类"""

    @staticmethod
    def create(
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        subagent_type: Optional[str] = None,
        instance_quota: int = 1,
        billing_cycle: str = "monthly",
        unit_price: float = 0,
        token_quota: int = -1,
        starts_at: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """创建订阅"""
        subscription_id = f"sub_{uuid.uuid4().hex[:12]}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO subscriptions
                        (subscription_id, tenant_id, user_id, subagent_type,
                         instance_quota, billing_cycle, unit_price, token_quota,
                         starts_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    subscription_id, tenant_id, user_id, subagent_type,
                    instance_quota, billing_cycle, unit_price, token_quota,
                    starts_at, expires_at,
                ))
                conn.commit()
                logger.info(f"Subscription created: {subscription_id}")
                return SubscriptionDB.get_by_id(subscription_id)
            except Exception as e:
                logger.error(f"Failed to create subscription: {e}")
                return None

    @staticmethod
    def get_by_id(subscription_id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 获取订阅"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM subscriptions WHERE subscription_id = %s", (subscription_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_by_tenant(tenant_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出租户的订阅"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute(
                    "SELECT * FROM subscriptions WHERE tenant_id = %s AND status = %s ORDER BY created_at DESC",
                    (tenant_id, status),
                )
            else:
                cursor.execute(
                    "SELECT * FROM subscriptions WHERE tenant_id = %s ORDER BY created_at DESC",
                    (tenant_id,),
                )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def update_tokens_used(subscription_id: str, tokens_delta: int) -> bool:
        """累加已用 token 数"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE subscriptions
                SET tokens_used = tokens_used + %s, updated_at = CURRENT_TIMESTAMP
                WHERE subscription_id = %s
            """, (tokens_delta, subscription_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_status(subscription_id: str, status: str, payment_status: Optional[str] = None) -> bool:
        """更新订阅状态"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if payment_status:
                cursor.execute("""
                    UPDATE subscriptions
                    SET status = %s, payment_status = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE subscription_id = %s
                """, (status, payment_status, subscription_id))
            else:
                cursor.execute("""
                    UPDATE subscriptions
                    SET status = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE subscription_id = %s
                """, (status, subscription_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def get_quota(tenant_id: str, subagent_type: str) -> Tuple[bool, int]:
        """
        获取租户对某个数字员工的访问权限和实例配额

        Returns:
            (has_access: bool, instance_quota: int)
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) > 0 AS has_access,
                    COALESCE(MAX(instance_quota), 0) AS instance_quota
                FROM subscriptions
                WHERE tenant_id = %s
                  AND subagent_type = %s
                  AND status = 'active'
                  AND starts_at <= CURRENT_TIMESTAMP
                  AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            """, (tenant_id, subagent_type))
            row = cursor.fetchone()
            return bool(row["has_access"]), int(row["instance_quota"])

    @staticmethod
    def get_active_by_instance(instance_id: str) -> Optional[Dict[str, Any]]:
        """根据 agent_instance 获取关联的有效订阅"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.* FROM subscriptions s
                JOIN agent_instances ai ON ai.subscription_id = s.subscription_id
                WHERE ai.instance_id = %s
                  AND s.status = 'active'
                  AND s.starts_at <= CURRENT_TIMESTAMP
                  AND (s.expires_at IS NULL OR s.expires_at > CURRENT_TIMESTAMP)
            """, (instance_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
