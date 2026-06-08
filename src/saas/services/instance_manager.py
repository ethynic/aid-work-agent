"""
智能体实例管理器

⚠️ 智能体实例并发控制功能拟废弃 ⚠️

管理租户的 Agent 实例生命周期。每个实例对应一个独立的 AgentRouter，
共享底层 master_agent 单例（线程安全，session 隔离由 ShortTermMemory 保证）。

运行状态通过 Redis 同步，解决多 worker 环境下状态不一致问题。
"""

import os
from typing import Optional, Dict

from loguru import logger

from src.saas.db.agent_instance_db import AgentInstanceDB
from src.core.redis_client import redis_client


class AgentInstanceManager:
    """
    智能体实例管理器

    管理 Dict[instance_id, AgentRouter]，每个租户的每个 agent 实例一个独立的 Router。
    运行状态通过 Redis 同步到所有 worker，避免多 worker 下状态不一致。
    """

    def __init__(self):
        self._routers: Dict[str, AgentRouter] = {}
        self._instance_info: Dict[str, dict] = {}  # instance_id → DB 记录缓存

    def _redis_key(self, instance_id: str) -> str:
        return f"instance_status:{instance_id}"

    def start_instance(self, instance_id: str) -> bool:
        """
        启动实例：创建 AgentRouter 并注册

        Args:
            instance_id: 实例 ID

        Returns:
            是否启动成功
        """
        # 检查 Redis 中是否已有其他 worker 在运行该实例
        try:
            remote = redis_client.get(self._redis_key(instance_id))
            if remote and remote.get("running"):
                logger.warning(f"Instance {instance_id} already running in another worker")
                # 如果本地没有，也创建一个 Router 以便本地路由可用
                if instance_id not in self._routers:
                    from src.core.agent_router import AgentRouter
                    router = AgentRouter()
                    self._routers[instance_id] = router
                    instance = AgentInstanceDB.get_by_id(instance_id)
                    if instance:
                        self._instance_info[instance_id] = instance
                return True
        except Exception as e:
            logger.warning(f"Failed to check remote instance status: {e}")

        if instance_id in self._routers:
            logger.warning(f"Instance {instance_id} already running locally")
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

        # 同步运行状态到 Redis，使其他 worker 可见
        try:
            redis_client.set(
                self._redis_key(instance_id),
                {"running": True, "pid": os.getpid(), "started_at": __import__("time").time()},
                ex=3600,
            )
        except Exception as e:
            logger.warning(f"Failed to sync instance status to Redis: {e}")

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

        # 清除 Redis 中的运行状态
        try:
            redis_client.delete(self._redis_key(instance_id))
        except Exception as e:
            logger.warning(f"Failed to clear instance status from Redis: {e}")
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
        """检查实例是否在运行（本地或 Redis 中的任一状态）"""
        if instance_id in self._routers:
            return True
        try:
            remote = redis_client.get(self._redis_key(instance_id))
            return bool(remote and remote.get("running"))
        except Exception:
            return False

    def list_running(self) -> Dict[str, dict]:
        """列出所有运行中的实例（本地视图）"""
        return {
            iid: info
            for iid, info in self._instance_info.items()
            if iid in self._routers
        }

    def restore_running_instances(self) -> int:
        """
        恢复运行中的实例（简化版）

        在应用启动时调用（lifespan）。由于状态简化，不再从数据库恢复运行状态。
        应用重启后所有实例都停止，需要按需启动。

        Returns:
            恢复的实例数量（始终为0）
        """
        logger.info("Instance restore skipped: status simplified to idle/busy only, no running instances to restore")
        return 0

    def cleanup(self):
        """清理所有实例（shutdown 时调用）"""
        for iid in list(self._routers.keys()):
            self.stop_instance(iid)
        logger.info("All instances cleaned up")


# 全局单例
instance_manager = AgentInstanceManager()
