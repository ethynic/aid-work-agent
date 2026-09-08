"""
Agent Router - 智能体路由器

根据入口参数选择使用主智能体或独立模式的子智能体。

所有入口（Web URL / IM 机器人回调）最终都汇聚到 AgentRouter.get_agent()：
- subagent_name=None → master_agent（全局单例）
- subagent_name=xxx → StandaloneAgent（按 session:subagent 缓存）

注意：多 worker 环境下，standalone 子智能体在每个 worker 中独立缓存。
短期记忆已通过 DB 恢复历史消息，但 skill session 等中间状态无法跨 worker 共享。
建议部署层配置 sticky session（按 session_id 固定路由）。
"""

import time
from typing import Optional, Dict
from loguru import logger

from src.core.agent import Agent, AgentMode, master_agent
from src.core.redis_client import redis_client


class AgentRouter:
    """
    智能体路由器

    管理主智能体单例和独立模式子智能体的缓存。
    缓存元信息同步到 Redis，使各 worker 能感知其他 worker 中的实例。
    """

    # 自定义智能体（subagent_definitions DB 定义，from_db=True）的运行时字段，用于检测配置是否被修改
    _CONFIG_CHANGE_FIELDS = (
        "llm_provider", "llm_model_codes", "tools", "skills",
        "context", "reply_style", "chat_toolbar", "upload_accept",
    )

    def __init__(self):
        # 使用现有全局单例，避免重复初始化
        # master_agent 内部通过 ShortTermMemory 的 Dict[session_id, deque] 实现会话隔离
        self.master_agent = master_agent
        self._standalone_cache: Dict[str, Agent] = {}

    def _redis_key(self, cache_key: str) -> str:
        return redis_client.make_key("standalone_agent", cache_key)

    @staticmethod
    def _config_changed(old, new) -> bool:
        """比较两个 SubagentConfig 的运行时字段，判断配置是否被修改"""
        return any(
            getattr(old, f) != getattr(new, f)
            for f in AgentRouter._CONFIG_CHANGE_FIELDS
        )

    def _should_rebuild(self, cached: Agent, subagent_name: str) -> bool:
        """
        判断缓存的自定义智能体实例是否需要重建。

        自定义智能体配置存于数据库，可被管理后台修改，而进程内 Agent 实例的
        subagent_config 在构造时固定且无法跨 worker 感知变更。因此每次消息进入时
        实时读取最新 DB 配置比对：配置变了 → 重建实例立即生效（无需重启）；
        配置没变 → 复用实例以保留会话状态。内置（文件系统）智能体永远复用。
        """
        if not getattr(cached.subagent_config, "from_db", False):
            return False
        registry = self.master_agent.subagent_registry if self.master_agent.subagent_registry else None
        if not registry:
            return False
        from src.subagents.factory import AgentFactory
        latest = AgentFactory.get_runtime_config(registry, subagent_name)
        if latest is None:
            # DB 无定义（已删除/无 system_prompt）时复用旧实例，避免中断进行中的会话
            return False
        return AgentRouter._config_changed(cached.subagent_config, latest)

    def get_agent(
        self,
        subagent_name: Optional[str],
        session_id: str,
        tenant_id: Optional[str] = None,
    ) -> Agent:
        """
        根据子智能体名称获取对应的 Agent 实例

        Args:
            subagent_name: 子智能体名称，None 表示使用主智能体
            session_id: 会话ID
            tenant_id: 租户ID（用于加载租户定制 extra.md）

        Returns:
            Agent 实例
        """
        if not subagent_name:
            return self.master_agent

        cache_key = f"{session_id}:{subagent_name}"
        cached = self._standalone_cache.get(cache_key)
        if cached is not None:
            # 自定义智能体：配置被修改则重建实例（下一条消息立即生效），未修改则复用保持会话状态
            if self._should_rebuild(cached, subagent_name):
                logger.info(f"[AgentRouter] Rebuilding standalone agent (config changed): {cache_key}")
                self._standalone_cache.pop(cache_key, None)
                try:
                    redis_client.delete(self._redis_key(cache_key))
                except Exception:
                    pass
            else:
                return cached

        if cache_key not in self._standalone_cache:
            # 检查 Redis 中是否有其他 worker 已创建该 standalone agent
            try:
                remote = redis_client.get(self._redis_key(cache_key))
                if remote:
                    logger.info(
                        f"[AgentRouter] Standalone agent {cache_key} active in another worker, "
                        f"creating local mirror"
                    )
            except Exception:
                pass

            from src.subagents.factory import AgentFactory
            agent = AgentFactory.create_standalone_subagent(subagent_name, session_id, tenant_id=tenant_id)
            if not agent:
                logger.warning(
                    f"[AgentRouter] Subagent '{subagent_name}' not found, "
                    f"fallback to master"
                )
                return self.master_agent
            agent._created_at = time.time()
            self._standalone_cache[cache_key] = agent

            # 同步缓存元信息到 Redis，使其他 worker 可见
            try:
                redis_client.set(
                    self._redis_key(cache_key),
                    {"created_at": agent._created_at, "session_id": session_id, "subagent": subagent_name},
                    ex=3600,
                )
            except Exception:
                pass

            logger.info(f"[AgentRouter] Created standalone agent: {cache_key}")

        return self._standalone_cache[cache_key]

    def release_session(self, session_id: str):
        """
        释放会话相关的所有独立模式子智能体

        Args:
            session_id: 会话ID
        """
        keys_to_remove = [
            k for k in self._standalone_cache
            if k.startswith(f"{session_id}:")
        ]
        for k in keys_to_remove:
            del self._standalone_cache[k]
            try:
                redis_client.delete(self._redis_key(k))
            except Exception:
                pass
            logger.info(f"[AgentRouter] Released standalone agent: {k}")

    def cleanup_expired(self, max_age_seconds: int = 3600):
        """
        清理过期的独立模式子智能体实例

        建议由 APScheduler 定期调用，避免缓存无限增长。

        Args:
            max_age_seconds: 最大存活时间（秒），默认 1 小时
        """
        now = time.time()
        expired_keys = [
            k for k, agent in self._standalone_cache.items()
            if now - getattr(agent, '_created_at', now) > max_age_seconds
        ]
        for k in expired_keys:
            del self._standalone_cache[k]
            try:
                redis_client.delete(self._redis_key(k))
            except Exception:
                pass
            logger.info(f"[AgentRouter] Cleaned up expired standalone agent: {k}")

    @property
    def cached_count(self) -> int:
        """当前缓存的独立模式子智能体数量"""
        return len(self._standalone_cache)


# 全局路由实例
agent_router = AgentRouter()
