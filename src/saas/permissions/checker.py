"""
数字员工权限检查核心逻辑
- 两级权限控制：租户级 -> 用户级
- 平台管理员代管理时遵守租户级权限限制
"""

from typing import List, Optional
from src.db.database import get_db_connection
from src.saas.db.permission_db import TenantAgentPermissionDB, UserAgentPermissionDB
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


def check_agent_access(agent_id: str, user: dict) -> bool:
    """检查当前用户是否有权限访问某个数字员工

    权限检查规则：
    1. 平台管理员且不在租户前台代管理 → 允许所有
    2. 平台管理员在租户前台代管理 → 遵守租户授权限制
    3. 租户未授权任何数字员工 → 拒绝
    4. 租户管理员 → 允许租户授权范围内的所有
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

    with get_db_connection() as conn:
        # 第一层：检查租户级授权
        if not TenantAgentPermissionDB.has_permission(conn, tenant_id, agent_id):
            return False

        # 第二层：租户管理员 → 全部允许
        if is_tenant_admin(user):
            return True

        # 第三层：普通用户 → 检查用户级授权
        user_id = user.get("user_id")
        if not user_id:
            return False

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

    with get_db_connection() as conn:
        # 获取租户允许的列表
        tenant_allowed = TenantAgentPermissionDB.get_allowed_agents(conn, tenant_id)

        if not tenant_allowed:
            return []

        # 租户管理员 → 返回租户允许的全部
        if is_tenant_admin(user):
            return tenant_allowed

        # 普通用户 → 取交集（用户允许且租户允许）
        user_id = user.get("user_id")
        if not user_id:
            return []

        user_allowed = set(UserAgentPermissionDB.get_allowed_agents(conn, user_id))
        return [aid for aid in tenant_allowed if aid in user_allowed]


def count_tenant_allowed_agents(tenant_id: str) -> int:
    """统计租户授权的数字员工数量"""
    with get_db_connection() as conn:
        return TenantAgentPermissionDB.count_allowed(conn, tenant_id)


def count_user_allowed_agents(user_id: str) -> int:
    """统计用户授权的数字员工数量"""
    with get_db_connection() as conn:
        return UserAgentPermissionDB.count_allowed(conn, user_id)
