"""子智能体委派的执行期授权边界。"""

from typing import Callable, Optional, Set

from src.tools.context import ToolExecutionContext


class DelegationAuthorizer:
    """无状态授权器；授权结果不写入 Agent 或工具实例。"""

    def __init__(self, allowed_types_resolver: Optional[Callable[[str], Set[str]]] = None):
        self._resolver = allowed_types_resolver

    def authorize(
        self,
        context: Optional[ToolExecutionContext],
        subagent_name: str,
        subagent_config,
    ) -> bool:
        tenant_id = context.tenant_id if context else None
        if not tenant_id:
            return False
        agent_id = getattr(subagent_config, "dir_name", None) or subagent_name
        if agent_id == "main":
            return False
        if self._resolver is not None:
            allowed = set(self._resolver(tenant_id))
        else:
            from src.db.database import get_db_connection
            from src.saas.db.subscription_db import SubscriptionDB
            with get_db_connection() as conn:
                allowed = set(SubscriptionDB.get_allowed_subagent_types(conn, tenant_id))
        return agent_id in allowed
