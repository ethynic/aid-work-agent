"""
渠道适配器工厂

从租户渠道配置（tenant_channel_configs 表）动态创建 ChannelAdapter 实例。

适配器实例按 (tenant_id, channel_type, config_id) 缓存，避免每次回调都重新构造
导致 token 缓存 / 速率限制 / HTTP 连接池失效。
配置变更或删除时，调用 invalidate_adapter / invalidate_tenant 主动失效。
"""

import asyncio
import json
import time
from typing import Optional, Dict, Any, Tuple

from loguru import logger

from src.saas.db.channel_config_db import ChannelConfigDB


# 缓存项：(adapter, config_id, subagent_type, created_at, lock)
# 用 asyncio.Lock 串行化同一 key 的并发创建，避免重复构造
_ADAPTER_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}
_CACHE_LOCKS: Dict[Tuple[str, str], asyncio.Lock] = {}

# 缓存 TTL：超过后下次获取时重建（兜底，防止配置变更未触发 invalidate）
_CACHE_TTL_SECONDS = 3600


def _get_cache_lock(tenant_id: str, channel_type: str) -> asyncio.Lock:
    """获取某 (tenant_id, channel_type) 的创建锁"""
    key = (tenant_id, channel_type)
    if key not in _CACHE_LOCKS:
        _CACHE_LOCKS[key] = asyncio.Lock()
    return _CACHE_LOCKS[key]


def _is_expired(entry: Dict[str, Any]) -> bool:
    """缓存项是否过期"""
    return time.time() - entry.get("created_at", 0) > _CACHE_TTL_SECONDS


async def _build_adapter(tenant_id: str, channel_type: str, config: dict) -> Any:
    """构造 adapter 实例（同步构造 + 异步后置初始化）"""
    adapter = ChannelFactory.create_adapter(channel_type, config)
    # 若 adapter 支持接收 tenant_id（用于租户隔离存储路径等），注入
    if hasattr(adapter, "set_tenant_id"):
        try:
            await adapter.set_tenant_id(tenant_id)  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning(
                f"adapter.set_tenant_id 失败: tenant={tenant_id}, type={channel_type}, error={e}"
            )
    return adapter


class ChannelFactory:
    """
    渠道适配器工厂

    根据渠道类型和配置字典创建对应的 ChannelAdapter 实例。
    适配器实例在进程内按 (tenant_id, channel_type) 缓存，复用 token / 连接池 / 速率限制器。
    """

    # 渠道类型 → 适配器类（延迟导入）
    _ADAPTER_CLASSES = {
        "wecom": "src.channels.wecom.adapter.WeComAdapter",
        "wecom_kf": "src.channels.wecom_kf.adapter.WeComKfAdapter",
        "dingtalk": "src.channels.dingtalk.adapter.DingTalkAdapter",
        "feishu": "src.channels.feishu.adapter.FeishuAdapter",
    }

    @classmethod
    def create_adapter(cls, channel_type: str, config: dict):
        """
        根据渠道类型和配置创建适配器（每次都新建实例，不走缓存）

        Args:
            channel_type: wecom / dingtalk / feishu
            config: 渠道凭证配置字典

        Returns:
            ChannelAdapter 实例
        """
        class_path = cls._ADAPTER_CLASSES.get(channel_type)
        if not class_path:
            raise ValueError(f"Unknown channel type: {channel_type}")

        # 延迟导入
        module_path, class_name = class_path.rsplit(".", 1)
        import importlib
        module = importlib.import_module(module_path)
        adapter_class = getattr(module, class_name)

        # 传入配置参数创建实例
        return adapter_class(**config)

    @classmethod
    async def create_from_tenant_config(
        cls,
        tenant_id: str,
        channel_type: str,
        config_id: Optional[str] = None,
    ):
        """
        从租户 DB 配置创建适配器（带进程内缓存）

        Args:
            tenant_id: 租户 ID
            channel_type: 渠道类型
            config_id: 渠道配置 ID（可选，传入时按 ID 精确查找）

        Returns:
            (adapter, config_id, subagent_type) 或 (None, None, None)
        """
        cache_key = (tenant_id, channel_type)

        # 快速路径：命中未过期的缓存
        entry = _ADAPTER_CACHE.get(cache_key)
        if entry and not _is_expired(entry):
            # 若调用方指定了 config_id 且与缓存不一致，则强制重建
            if config_id and entry.get("config_id") != config_id:
                # 缓存与请求的 config_id 不一致，走重建
                pass
            else:
                return entry["adapter"], entry["config_id"], entry.get("subagent_type")

        # 串行化同一 key 的并发创建
        async with _get_cache_lock(tenant_id, channel_type):
            # 双重检查
            entry = _ADAPTER_CACHE.get(cache_key)
            if entry and not _is_expired(entry):
                if not (config_id and entry.get("config_id") != config_id):
                    return entry["adapter"], entry["config_id"], entry.get("subagent_type")

            # 读取 DB 配置
            cfg, used_config_id = cls._load_config(tenant_id, channel_type, config_id)
            if not cfg:
                # 配置不存在时清掉旧缓存，避免下次还返回旧实例
                _ADAPTER_CACHE.pop(cache_key, None)
                return None, None, None

            try:
                adapter = await _build_adapter(tenant_id, channel_type, cfg["config"])
            except Exception as e:
                logger.error(
                    f"Failed to create adapter for tenant {tenant_id}/{channel_type}: {e}"
                )
                return None, None, None

            entry = {
                "adapter": adapter,
                "config_id": used_config_id,
                "subagent_type": cfg.get("subagent_type"),
                "created_at": time.time(),
            }
            _ADAPTER_CACHE[cache_key] = entry
            return adapter, used_config_id, cfg.get("subagent_type")

    @classmethod
    def _load_config(
        cls,
        tenant_id: str,
        channel_type: str,
        config_id: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        从 DB 读取配置，返回 (cfg, used_config_id)。

        优先按 config_id 精确查找；其次取已验证的配置；最后取第一条。
        """
        if config_id:
            cfg = ChannelConfigDB.get_by_id(config_id)
            if cfg and cfg.get("tenant_id") == tenant_id and cfg.get("channel_type") == channel_type:
                return cfg, config_id
            return None, None

        configs = ChannelConfigDB.list_by_tenant(tenant_id, channel_type)
        if not configs:
            return None, None

        for cfg in configs:
            if cfg.get("verified"):
                return cfg, cfg["config_id"]

        cfg = configs[0]
        return cfg, cfg["config_id"]

    @classmethod
    async def invalidate_adapter(
        cls,
        tenant_id: str,
        channel_type: str,
        close: bool = True,
    ) -> None:
        """
        使缓存中的 adapter 失效（配置变更 / 删除时调用）

        Args:
            tenant_id: 租户 ID
            channel_type: 渠道类型
            close: 是否调用 adapter.close() 释放 httpx 连接池
        """
        cache_key = (tenant_id, channel_type)
        entry = _ADAPTER_CACHE.pop(cache_key, None)
        if entry and close:
            adapter = entry.get("adapter")
            if adapter and hasattr(adapter, "close"):
                try:
                    await adapter.close()
                except Exception as e:
                    logger.warning(
                        f"adapter.close 失败: tenant={tenant_id}, type={channel_type}, error={e}"
                    )

    @classmethod
    async def invalidate_tenant(cls, tenant_id: str) -> None:
        """使某租户所有渠道的 adapter 失效（租户删除时调用）"""
        keys_to_remove = [k for k in list(_ADAPTER_CACHE.keys()) if k[0] == tenant_id]
        for key in keys_to_remove:
            await cls.invalidate_adapter(key[0], key[1], close=True)

    @classmethod
    async def close_all(cls) -> None:
        """关闭所有缓存的 adapter（应用 shutdown 时调用）"""
        keys = list(_ADAPTER_CACHE.keys())
        for key in keys:
            entry = _ADAPTER_CACHE.pop(key, None)
            if not entry:
                continue
            adapter = entry.get("adapter")
            if adapter and hasattr(adapter, "close"):
                try:
                    await adapter.close()
                except Exception as e:
                    logger.warning(
                        f"adapter.close 失败: tenant={key[0]}, type={key[1]}, error={e}"
                    )
        logger.info(f"ChannelFactory 已关闭 {len(keys)} 个缓存 adapter")
