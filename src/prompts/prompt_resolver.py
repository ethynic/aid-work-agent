"""
Prompt 运行时解析器

提供 PromptCache（Redis 缓存）和 PromptResolver（降级链解析），
供 Agent._build_system_prompt() 在运行时解析当前应使用的 Prompt 版本。
"""

from typing import Optional, Dict, Any

from loguru import logger

from src.core.cache_utils import (
    CacheKeys,
    get_cached,
    set_cached,
    delete_cached,
    delete_cached_pattern,
)
from src.db.prompt_db import PromptRegistryDB, PromptVersionDB, PromptLabelDB


class PromptCache:
    """Redis 缓存层"""

    CONTENT_TTL = 600     # 版本内容缓存 10 分钟
    LABEL_TTL = 300       # 标签→版本映射缓存 5 分钟
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

    # --- label -> version ---

    def get_label_version(self, prompt_id: str, label: str) -> Optional[int]:
        result = get_cached(CacheKeys.PROMPT_LABEL, prompt_id, label)
        return result if isinstance(result, int) else None

    def set_label_version(self, prompt_id: str, label: str, version: int):
        set_cached(CacheKeys.PROMPT_LABEL, prompt_id, label,
                    value=version, ttl=self.LABEL_TTL)

    # --- content ---

    def get_content(self, prompt_id: str, version: int) -> Optional[str]:
        return get_cached(CacheKeys.PROMPT_CONTENT, prompt_id, str(version))

    def set_content(self, prompt_id: str, version: int, content: str):
        set_cached(CacheKeys.PROMPT_CONTENT, prompt_id, str(version),
                    value=content, ttl=self.CONTENT_TTL)

    # --- invalidation ---

    def invalidate_label(self, prompt_id: str, label: str):
        delete_cached(CacheKeys.PROMPT_LABEL, prompt_id, label)

    def invalidate_prompt(self, prompt_id: str):
        delete_cached_pattern(CacheKeys.PROMPT_CONTENT, prompt_id, "")
        delete_cached_pattern(CacheKeys.PROMPT_LABEL, prompt_id, "")


class PromptResolver:
    """
    运行时 Prompt 解析器

    降级链：production label → latest label → latest_version → None
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

        # 2. 尝试 production label
        version = self._resolve_label(prompt_id, "production")
        if not version:
            # 3. 尝试 latest label
            version = self._resolve_label(prompt_id, "latest")
        if not version:
            # 4. 尝试最新版本
            latest = PromptVersionDB.get_latest(prompt_id)
            version = latest["version"] if latest else None

        if version is None:
            return None

        # 5. 获取内容（缓存 → DB）
        return self._resolve_content(prompt_id, version)

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

    def _resolve_label(self, prompt_id: str, label: str) -> Optional[int]:
        cached_version = self.cache.get_label_version(prompt_id, label)
        if cached_version is not None:
            return cached_version

        label_record = PromptLabelDB.get_by_label(prompt_id, label)
        if not label_record:
            return None

        # 需要查出 version 号
        version_record = PromptVersionDB.get_by_id(str(label_record["version_id"]))
        if not version_record:
            return None

        version = version_record["version"]
        self.cache.set_label_version(prompt_id, label, version)
        return version

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
