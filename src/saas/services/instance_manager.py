"""
智能体实例管理器

管理租户的 Agent 实例生命周期。每个实例对应一个独立的 AgentRouter，
共享底层 master_agent 单例（线程安全，session 隔离由 ShortTermMemory 保证）。
"""

import time
from typing import Optional, Dict

from loguru import logger

from src.saas.db.agent_instance_db import AgentInstanceDB


class AgentInstanceManager:
    """
    智能体实例管理器

    管理 Dict[instance_id, AgentRouter]，每个租户的每个 agent 实例一个独立的 Router。
    """

    def __init__(self):
        self._routers: Dict[str, AgentRouter] = {}
        self._instance_info: Dict[str, dict] = {}  # instance_id → DB 记录缓存

    def start_instance(self, instance_id: str) -> bool:
        """
        启动实例：创建 AgentRouter 并注册

        Args:
            instance_id: 实例 ID

        Returns:
            是否启动成功
        """
        if instance_id in self._routers:
            logger.warning(f"Instance {instance_id} already running")
            return True

        # 从 DB 获取实例信息
        instance = AgentInstanceDB.get_by_id(instance_id)
        if not instance:
            logger.error(f"Instance {instance_id} not found in DB")
            return False

        # 创建独立的 AgentRouter（延迟导入避免触发 master_agent 单例）
        from src.core.agent_router import AgentRouter
        router = AgentRouter()
        self._routers[instance_id] = router
        self._instance_info[instance_id] = instance

        # 更新 DB 状态
        AgentInstanceDB.update(instance_id, status="running")

        logger.info(
            f"Instance started: {instance_id} "
            f"(tenant={instance['tenant_id']}, type={instance['subagent_type']})"
        )
        return True

    def stop_instance(self, instance_id: str) -> bool:
        """
        停止实例：清理 Router 并更新状态

        Args:
            instance_id: 实例 ID

        Returns:
            是否停止成功
        """
        router = self._routers.pop(instance_id, None)
        self._instance_info.pop(instance_id, None)

        if router:
            # 清理 Router 缓存
            router.cleanup_expired(max_age_seconds=0)
            logger.info(f"Instance stopped: {instance_id}")

        # 更新 DB 状态
        AgentInstanceDB.update(instance_id, status="stopped")
        return True

    def get_agent(self, instance_id: str, subagent_name: Optional[str], session_id: str):
        """
        从实例的 Router 获取 Agent

        Args:
            instance_id: 实例 ID
            subagent_name: 子智能体名称（可选）
            session_id: 会话 ID

        Returns:
            Agent 实例，如果实例不存在返回 None
        """
        router = self._routers.get(instance_id)
        if not router:
            return None
        return router.get_agent(subagent_name, session_id)

    def get_instance_info(self, instance_id: str) -> Optional[dict]:
        """获取实例缓存信息"""
        return self._instance_info.get(instance_id)

    def is_running(self, instance_id: str) -> bool:
        """检查实例是否在运行"""
        return instance_id in self._routers

    def list_running(self) -> Dict[str, dict]:
        """列出所有运行中的实例"""
        return {
            iid: info
            for iid, info in self._instance_info.items()
            if iid in self._routers
        }

    def restore_running_instances(self) -> int:
        """
        恢复所有 DB 中 status='running' 的实例

        在应用启动时调用（lifespan）。

        Returns:
            恢复的实例数量
        """
        instances = AgentInstanceDB.list_all_running()
        count = 0
        for inst in instances:
            try:
                from src.core.agent_router import AgentRouter
                router = AgentRouter()
                self._routers[inst["instance_id"]] = router
                self._instance_info[inst["instance_id"]] = inst
                count += 1
                logger.info(
                    f"Restored instance: {inst['instance_id']} "
                    f"(tenant={inst['tenant_id']}, type={inst['subagent_type']})"
                )
            except Exception as e:
                logger.error(f"Failed to restore instance {inst['instance_id']}: {e}")

        if count > 0:
            logger.info(f"Restored {count} running instances")
        return count

    def cleanup(self):
        """清理所有实例（shutdown 时调用）"""
        for iid in list(self._routers.keys()):
            self.stop_instance(iid)
        logger.info("All instances cleaned up")


# 全局单例
instance_manager = AgentInstanceManager()
