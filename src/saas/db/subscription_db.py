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
    def get_allowed_subagent_types(conn: Any, tenant_id: str) -> List[str]:
        """获取租户当前有效订阅的数字员工类型列表

        替代原 TenantAgentPermissionDB.get_allowed_agents
        """
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT subagent_type
            FROM subscriptions
            WHERE tenant_id = %s
              AND status = 'active'
              AND starts_at <= CURRENT_TIMESTAMP
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            ORDER BY subagent_type
        """, (tenant_id,))
        return [row["subagent_type"] for row in cursor.fetchall()]

    @staticmethod
    def count_active_subscriptions(conn: Any, tenant_id: str) -> int:
        """统计租户有效订阅的数字员工数量

        替代原 TenantAgentPermissionDB.count_allowed
        """
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(DISTINCT subagent_type) as cnt
            FROM subscriptions
            WHERE tenant_id = %s
              AND status = 'active'
              AND starts_at <= CURRENT_TIMESTAMP
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
        """, (tenant_id,))
        result = cursor.fetchone()
        return result["cnt"] if result else 0

    @staticmethod
    def set_tenant_subscriptions(conn: Any, tenant_id: str, agent_ids: List[str], agent_quotas: Optional[Dict[str, int]] = None) -> None:
        """设置租户授权的数字员工列表（覆盖原有有效订阅）

        替代原 TenantAgentPermissionDB.set_permissions。
        对于新增的 agent_id 创建订阅，对于移除的 agent_id 将订阅状态设为 cancelled。
        agent_quotas: 每个数字员工的实例配额字典，未指定的默认为1。
        """
        from src.saas.db.permission_db import UserAgentPermissionDB

        cursor = conn.cursor()
        if agent_quotas is None:
            agent_quotas = {}

        # 获取当前有效订阅的 agent 列表
        current_agents = set(SubscriptionDB.get_allowed_subagent_types(conn, tenant_id))
        new_agents = set(agent_ids)
        removed_agents = current_agents - new_agents

        # 将移除的订阅设为 cancelled
        if removed_agents:
            placeholders = ",".join(["%s"] * len(removed_agents))
            cursor.execute(f"""
                UPDATE subscriptions
                SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = %s
                  AND subagent_type IN ({placeholders})
                  AND status = 'active'
            """, (tenant_id, *removed_agents))

        # 获取现有有效订阅的配额映射
        cursor.execute("""
            SELECT subagent_type, instance_quota FROM subscriptions
            WHERE tenant_id = %s
              AND status = 'active'
              AND starts_at <= CURRENT_TIMESTAMP
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
        """, (tenant_id,))
        existing_quotas = {row["subagent_type"]: row["instance_quota"] for row in cursor.fetchall()}

        # 处理新增和已有的 agent 订阅（更新配额）
        for agent_id in new_agents:
            # 确定配额：优先使用 agent_quotas 中的值，其次现有配额，最后默认值1
            if agent_id in agent_quotas:
                quota = agent_quotas[agent_id]
            elif agent_id in existing_quotas:
                quota = existing_quotas[agent_id]
            else:
                quota = 1
            # 检查是否已存在有效订阅
            cursor.execute("""
                SELECT subscription_id, instance_quota FROM subscriptions
                WHERE tenant_id = %s
                  AND subagent_type = %s
                  AND status = 'active'
                  AND starts_at <= CURRENT_TIMESTAMP
                  AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                LIMIT 1
            """, (tenant_id, agent_id))
            existing = cursor.fetchone()
            if existing:
                # 如果配额发生变化，则更新
                if existing["instance_quota"] != quota:
                    cursor.execute("""
                        UPDATE subscriptions
                        SET instance_quota = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE subscription_id = %s
                    """, (quota, existing["subscription_id"]))
            else:
                # 创建新订阅
                try:
                    subscription_id = f"sub_{uuid.uuid4().hex[:12]}"
                    cursor.execute("""
                        INSERT INTO subscriptions
                            (subscription_id, tenant_id, subagent_type, instance_quota,
                             billing_cycle, unit_price, token_quota, status, starts_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    """, (subscription_id, tenant_id, agent_id, quota, 'monthly', 0, -1, 'active'))
                except Exception:
                    pass

        # 级联删除：从该租户所有用户的授权中移除被删除的 agent_id
        if removed_agents:
            for agent_id in removed_agents:
                UserAgentPermissionDB.remove_agent_from_all_users_in_tenant(conn, tenant_id, agent_id)

        conn.commit()
        logger.info(f"Tenant {tenant_id} subscriptions updated: {len(agent_ids)} agents, removed {len(removed_agents)}, quotas updated")

    @staticmethod
    def delete_all_for_tenant(conn: Any, tenant_id: str) -> None:
        """删除租户所有订阅和用户授权（当删除租户时调用）

        替代原 TenantAgentPermissionDB.delete_all_for_tenant
        """
        cursor = conn.cursor()
        cursor.execute("DELETE FROM subscriptions WHERE tenant_id = %s", (tenant_id,))
        cursor.execute("DELETE FROM user_agent_permissions WHERE tenant_id = %s", (tenant_id,))
        conn.commit()
        logger.info(f"Deleted all subscriptions and permissions for tenant {tenant_id}")

    @staticmethod
    def remove_agent_from_all_tenants(conn: Any, agent_id: str) -> None:
        """从所有租户移除该数字员工的订阅和用户授权（当删除数字员工时调用）

        替代原 TenantAgentPermissionDB.remove_agent_from_all_tenants
        """
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE subscriptions
            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE subagent_type = %s AND status = 'active'
        """, (agent_id,))
        cursor.execute("DELETE FROM user_agent_permissions WHERE agent_id = %s", (agent_id,))
        conn.commit()
        logger.info(f"Removed agent {agent_id} from all tenant subscriptions and user permissions")

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
