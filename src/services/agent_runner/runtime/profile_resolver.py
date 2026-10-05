from __future__ import annotations
import asyncio
from typing import List, Optional

class ProfileResolver:
    def __init__(self, registry, tenant_id):
        self.subagent_registry, self.tenant_id = registry, tenant_id
        self._available = None

    def available(self):
        if self._available is None:
            self._available = self._resolve_available_subagents(self.tenant_id)
        return list(self._available)

    async def prime(self):
        self._available = await asyncio.to_thread(self._resolve_available_subagents, self.tenant_id)
    def _resolve_available_subagents(self, tenant_id: Optional[str]) -> List[str]:
        """同步解析租户可见的子智能体；异步入口通过 ``to_thread`` 调用。"""
        if not self.subagent_registry:
            return []

        # 有租户 ID：从订阅表查询
        if tenant_id:
            from src.db.database import get_db_connection
            from src.saas.db.subscription_db import SubscriptionDB

            with get_db_connection() as conn:
                allowed_subagent_types = SubscriptionDB.get_allowed_subagent_types(conn, tenant_id)
            # 过滤注册的子智能体，只保留租户订阅的
            # _configs 的 key 与 subagent_type 均为 dir_name（agent_id）
            filtered = []
            for agent_id in self.subagent_registry._configs.keys():
                if agent_id in allowed_subagent_types and agent_id != "main":
                    filtered.append(agent_id)
        else:
            # 无租户上下文（platform_admin 全局视图/后台调用）：返回全部（排除主智能体）
            filtered = [
                agent_id
                for agent_id in self.subagent_registry._configs.keys()
                if agent_id != "main"
            ]

        return filtered

