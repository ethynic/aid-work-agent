"""
数字员工权限检查核心逻辑
- 两级权限控制：租户级订阅 -> 用户级授权
- 平台管理员代管理时遵守租户级权限限制
"""

from typing import List, Optional, Tuple
from src.db.database import get_db_connection
from src.saas.db.permission_db import UserAgentPermissionDB
from src.core.agent import master_agent


def is_platform_admin(user: dict) -> bool:
    """检查是否为平台管理员"""
    return user.get("role") == "platform_admin"


def is_tenant_admin(user: dict) -> bool:
    """检查是否为租户管理员"""
    return user.get("role") == "tenant_admin"


def get_tenant_id_from_user(user: dict) -> Optional[str]:
    """从用户信息中获取租户ID"""
    return user.get("tenant_id")


def get_agent_quota(tenant_id: str, agent_id: str) -> Tuple[bool, int]:
    """
    获取租户对某个数字员工的访问权限和当前实例配额

    Args:
        tenant_id: 租户ID
        agent_id: 数字员工ID (subagent_type)

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
        """, (tenant_id, agent_id))
        row = cursor.fetchone()
        return bool(row["has_access"]), int(row["instance_quota"])


def check_agent_access(agent_id: str, user: dict) -> bool:
    """检查当前用户是否有权限访问某个数字员工

    权限检查规则：
    1. 平台管理员且不在租户前台代管理 → 允许所有
    2. 平台管理员在租户前台代管理 → 遵守租户授权限制
    3. 租户无有效订阅 → 拒绝
    4. 租户管理员 → 允许租户订阅范围内的所有
    5. 普通用户 → 检查用户级授权

    Args:
        agent_id: 要访问的数字员工ID
        user: 当前用户信息，包含 user_id, role, tenant_id 字段

    Returns:
        bool: 是否允许访问
    """
    # 平台管理员且不在租户代管理 → 完全放开
    if is_platform_admin(user) and get_tenant_id_from_user(user) is None:
        return True

    tenant_id = get_tenant_id_from_user(user)
    if not tenant_id:
        return False

    # 第一层：检查租户级订阅
    has_access, _ = get_agent_quota(tenant_id, agent_id)
    if not has_access:
        return False

    # 第二层：租户管理员 → 全部允许
    if is_tenant_admin(user):
        return True

    # 第三层：普通用户 → 检查用户级授权
    user_id = user.get("user_id")
    if not user_id:
        return False

    with get_db_connection() as conn:
        return UserAgentPermissionDB.has_permission(conn, user_id, agent_id)


def get_allowed_agent_ids_for_user(user: dict) -> List[str]:
    """获取当前用户允许访问的所有数字员工ID

    Args:
        user: 当前用户信息，包含 user_id, role, tenant_id 字段

    Returns:
        List[str]: 允许访问的数字员工ID列表
    """
    # 平台管理员且不在租户代管理 → 返回所有数字员工
    if is_platform_admin(user) and get_tenant_id_from_user(user) is None:
        registry = master_agent.subagent_registry
        if registry:
            return list(registry.list_subagents())
        return []

    tenant_id = get_tenant_id_from_user(user)
    if not tenant_id:
        return []

    # Demo 租户：可以访问所有内置的 subagent
    if tenant_id == "demo":
        registry = master_agent.subagent_registry
        if registry:
            return list(registry.list_subagents())
        return []

    with get_db_connection() as conn:
        # 获取租户有有效订阅的列表
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT subagent_type
            FROM subscriptions
            WHERE tenant_id = %s
              AND status = 'active'
              AND starts_at <= CURRENT_TIMESTAMP
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
        """, (tenant_id,))
        tenant_allowed = [row["subagent_type"] for row in cursor.fetchall()]

        if not tenant_allowed:
            return []

        # 租户管理员 或 平台管理员代管理 → 返回租户允许的全部
        if is_tenant_admin(user) or is_platform_admin(user):
            return tenant_allowed

        # 普通用户 → 取交集（用户允许且租户有订阅）
        user_id = user.get("user_id")
        if not user_id:
            return []

        user_allowed = set(UserAgentPermissionDB.get_allowed_agents(conn, user_id))
        return [aid for aid in tenant_allowed if aid in user_allowed]


def count_tenant_subscribed_agents(tenant_id: str) -> int:
    """统计租户有有效订阅的数字员工数量"""
    # Demo 租户：返回所有内置 subagent 的数量
    if tenant_id == "demo":
        registry = master_agent.subagent_registry
        if registry:
            return len(registry.list_subagents())
        return 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(DISTINCT subagent_type) as count
            FROM subscriptions
            WHERE tenant_id = %s
              AND status = 'active'
              AND starts_at <= CURRENT_TIMESTAMP
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
        """, (tenant_id,))
        return cursor.fetchone()["count"]


def count_user_allowed_agents(user_id: str) -> int:
    """统计用户授权的数字员工数量"""
    with get_db_connection() as conn:
        return UserAgentPermissionDB.count_allowed(conn, user_id)


# 兼容旧函数名
count_tenant_allowed_agents = count_tenant_subscribed_agents
