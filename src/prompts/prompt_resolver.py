"""
Prompt 运行时解析器

提供 PromptCache（Redis 缓存）和 PromptResolver（降级链解析），
供 Agent._build_system_prompt() 在运行时解析当前应使用的 Prompt 版本。
保存即生效：运行时直接读最新版本，无第二指针。
"""

from typing import Optional, Dict, Any

from src.core.cache_utils import (
    CacheKeys,
    get_cached,
    set_cached,
    delete_cached,
    delete_cached_pattern,
)
from src.db.prompt_db import PromptRegistryDB, PromptVersionDB


class PromptCache:
    """Redis 缓存层"""

    CONTENT_TTL = 600     # 版本内容缓存 10 分钟
    REGISTRY_TTL = 300    # 注册记录缓存 5 分钟

    # --- registry ---

    def get_registry(
        self, tenant_id: Optional[str], scope: str, scope_id: str
    ) -> Optional[Dict[str, Any]]:
        tenant_key = tenant_id or "_sys"
        return get_cached(CacheKeys.PROMPT_REGISTRY, tenant_key, scope, scope_id)

    def set_registry(
        self, tenant_id: Optional[str], scope: str, scope_id: str, data: Dict
    ):
        tenant_key = tenant_id or "_sys"
        set_cached(CacheKeys.PROMPT_REGISTRY, tenant_key, scope, scope_id,
                    value=data, ttl=self.REGISTRY_TTL)

    # --- content ---

    def get_content(self, prompt_id: str, version: int) -> Optional[str]:
        return get_cached(CacheKeys.PROMPT_CONTENT, prompt_id, str(version))

    def set_content(self, prompt_id: str, version: int, content: str):
        set_cached(CacheKeys.PROMPT_CONTENT, prompt_id, str(version),
                    value=content, ttl=self.CONTENT_TTL)

    # --- invalidation ---

    def invalidate_registry(self, tenant_id: Optional[str], scope: str, scope_id: str):
        """失效 registry 缓存（键按 scope_id 组织，删除 prompt 时必须单独失效，
        否则同 scope_id 重建后 resolve 在 TTL 内命中旧 prompt_id 导致拿不到内容）"""
        tenant_key = tenant_id or "_sys"
        delete_cached(CacheKeys.PROMPT_REGISTRY, tenant_key, scope, scope_id)

    def invalidate_prompt(self, prompt_id: str):
        delete_cached_pattern(CacheKeys.PROMPT_CONTENT, prompt_id, "")


class PromptResolver:
    """
    运行时 Prompt 解析器

    降级链：registry → 最新版 → None（保存即生效，无标签第二指针）
    """

    def __init__(self):
        self.cache = PromptCache()

    def resolve(
        self, scope: str, scope_id: str, tenant_id: Optional[str] = None
    ) -> Optional[str]:
        """
        解析当前应使用的 Prompt content。

        返回 None 表示数据库中无记录，调用方应降级到文件系统加载。
        """
        # 1. 查 registry（缓存 → DB）
        registry = self._resolve_registry(scope, scope_id, tenant_id)
        if not registry:
            return None

        prompt_id = str(registry["id"])

        # 2. 最新版本即生效版本
        latest = PromptVersionDB.get_latest(prompt_id)
        if not latest:
            return None

        # 3. 获取内容（缓存 → DB）
        return self._resolve_content(prompt_id, latest["version"])

    def _resolve_registry(
        self, scope: str, scope_id: str, tenant_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        cached = self.cache.get_registry(tenant_id, scope, scope_id)
        if cached:
            return cached

        record = PromptRegistryDB.get_by_scope(tenant_id, scope, scope_id)
        if record:
            # 转换 UUID 为字符串以便 JSON 序列化
            serializable = {k: str(v) if isinstance(v, type(record.get("id"))) and v is not None else v
                           for k, v in record.items()}
            self.cache.set_registry(tenant_id, scope, scope_id, serializable)
        return record

    def _resolve_content(self, prompt_id: str, version: int) -> Optional[str]:
        cached = self.cache.get_content(prompt_id, version)
        if cached:
            return cached

        version_record = PromptVersionDB.get_by_prompt_and_version(prompt_id, version)
        if not version_record:
            return None

        content = version_record["content"]
        self.cache.set_content(prompt_id, version, content)
        return content


# 全局单例
prompt_resolver = PromptResolver()
