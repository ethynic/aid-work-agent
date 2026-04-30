"""
数字员工授权数据库访问层
- UserAgentPermissionDB: 用户级数字员工授权

注：租户级授权已合并到 subscriptions 表，由 SubscriptionDB 管理
"""

from typing import List, Any
from loguru import logger


class UserAgentPermissionDB:
    """用户-数字员工授权数据库操作"""

    @staticmethod
    def set_permissions(conn: Any, user_id: str, tenant_id: str, agent_ids: List[str]) -> None:
        """设置用户的数字员工授权（覆盖原有）"""
        cursor = conn.cursor()

        # 删除原有所有授权
        cursor.execute("DELETE FROM user_agent_permissions WHERE user_id = %s", (user_id,))

        # 插入新授权
        for agent_id in agent_ids:
            try:
                cursor.execute(
                    "INSERT INTO user_agent_permissions (user_id, agent_id, tenant_id) VALUES (%s, %s, %s)",
                    (user_id, agent_id, tenant_id)
                )
            except Exception:
                # 唯一约束冲突，忽略
                pass

        conn.commit()
        logger.info(f"User {user_id} agent permissions updated: {len(agent_ids)} agents")

    @staticmethod
    def add_permission(conn: Any, user_id: str, tenant_id: str, agent_id: str) -> None:
        """添加单个用户授权（用于自动授权）"""
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO user_agent_permissions (user_id, agent_id, tenant_id) VALUES (%s, %s, %s)",
                (user_id, agent_id, tenant_id)
            )
            conn.commit()
            logger.info(f"Added permission for user {user_id} to agent {agent_id}")
        except Exception:
            # 已存在，无需重复添加
            pass

    @staticmethod
    def get_allowed_agents(conn: Any, user_id: str) -> List[str]:
        """获取用户允许使用的数字员工ID列表"""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT agent_id FROM user_agent_permissions WHERE user_id = %s ORDER BY agent_id",
            (user_id,)
        )
        return [row["agent_id"] for row in cursor.fetchall()]

    @staticmethod
    def has_permission(conn: Any, user_id: str, agent_id: str) -> bool:
        """检查用户是否有权限使用该数字员工"""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM user_agent_permissions WHERE user_id = %s AND agent_id = %s",
            (user_id, agent_id)
        )
        return cursor.fetchone() is not None

    @staticmethod
    def count_allowed(conn: Any, user_id: str) -> int:
        """统计用户授权的数字员工数量"""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM user_agent_permissions WHERE user_id = %s",
            (user_id,)
        )
        result = cursor.fetchone()
        return result["cnt"] if result else 0

    @staticmethod
    def clear_user_permissions(conn: Any, user_id: str) -> None:
        """清除用户所有授权（当删除用户时调用）"""
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_agent_permissions WHERE user_id = %s", (user_id,))
        conn.commit()
        logger.info(f"Cleared all permissions for user {user_id}")

    @staticmethod
    def remove_agent_from_all_users_in_tenant(conn: Any, tenant_id: str, agent_id: str) -> None:
        """从租户所有用户移除该数字员工授权（级联删除使用）"""
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM user_agent_permissions WHERE tenant_id = %s AND agent_id = %s",
            (tenant_id, agent_id)
        )
        conn.commit()
        logger.info(f"Removed agent {agent_id} from all users in tenant {tenant_id} (deleted {cursor.rowcount} records)")
