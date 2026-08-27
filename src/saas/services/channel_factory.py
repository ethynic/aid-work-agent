"""
渠道适配器工厂

从租户渠道配置（tenant_channel_configs 表）动态创建 ChannelAdapter 实例。

适配器实例按 (tenant_id, channel_type, config_id) 三元组缓存，避免每次回调都重新构造
导致 token 缓存 / 速率限制 / HTTP 连接池失效。同一租户同一渠道下不同 config_id 各自
独立缓存，互不驱逐。配置变更或删除时，调用 invalidate_adapter / invalidate_tenant
主动失效。
"""

import asyncio
import json
import time
from typing import Optional, Dict, Any, Tuple

from loguru import logger

from src.saas.db.channel_config_db import ChannelConfigDB


# 缓存项：(adapter, config_id, subagent_type, created_at, lock)
# 用 asyncio.Lock 串行化同一 key 的并发创建，避免重复构造
# 键三元组 (tenant_id, channel_type, config_id) 中 config_id 可能为 None（表示未指定，
# 命中后用解析出的实际 config_id 作为缓存键），调用方显式传入 config_id 时走精确匹配
_ADAPTER_CACHE: Dict[Tuple[str, str, Optional[str]], Dict[str, Any]] = {}
_CACHE_LOCKS: Dict[Tuple[str, str, Optional[str]], asyncio.Lock] = {}

# 缓存 TTL：超过后下次获取时重建（兜底，防止配置变更未触发 invalidate）
_CACHE_TTL_SECONDS = 3600


def _get_cache_lock(
    tenant_id: str, channel_type: str, config_id: Optional[str]
) -> asyncio.Lock:
    """获取某 (tenant_id, channel_type, config_id) 的创建锁"""
    key = (tenant_id, channel_type, config_id)
    if key not in _CACHE_LOCKS:
        _CACHE_LOCKS[key] = asyncio.Lock()
    return _CACHE_LOCKS[key]


def _is_expired(entry: Dict[str, Any]) -> bool:
    """缓存项是否过期"""
    return time.time() - entry.get("created_at", 0) > _CACHE_TTL_SECONDS


async def _is_cache_stale(entry: Dict[str, Any], config_id: str) -> bool:
    """缓存配置是否已过期：比对 DB 当前 updated_at 与缓存时记录版本。

    Gunicorn 多 worker 兜底：invalidate_adapter 只清处理修改请求所在 worker 的
    进程内缓存，其他 worker 下次回调靠本方法发现配置变更并强制重建。
    DB 读取失败时保守返回 False（沿用缓存，不阻断业务）。
    该方法位于缓存命中快速路径上（每个回调都会走到），同步 DB 查询用
    asyncio.to_thread 包裹，避免阻塞事件循环。
    """
    try:
        current_updated_at = await asyncio.to_thread(
            ChannelConfigDB.get_config_version, config_id
        )
    except Exception as e:
        logger.warning(
            f"[ChannelFactory] 配置版本校验失败，沿用缓存: config={config_id}, error={e}"
        )
        return False
    if current_updated_at is None:
        # 配置已被删除：视为过期，触发重建（重建时 _load_config 返回 None 会清缓存）
        return True
    return current_updated_at != entry.get("cfg_updated_at")


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
    适配器实例在进程内按 (tenant_id, channel_type, config_id) 三元组缓存，
    复用 token / 连接池 / 速率限制器。同租户同渠道不同 config_id 各自独立缓存。
    """

    # 渠道类型 → 适配器类（延迟导入）
    _ADAPTER_CLASSES = {
        "wecom": "src.channels.wecom.adapter.WeComAdapter",
        "wecom_kf": "src.channels.wecom_kf.adapter.WeComKfAdapter",
        "wecom_personal_rpa": "src.channels.wecom_personal_rpa.adapter.WeComPersonalRpaAdapter",
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

        缓存键为 (tenant_id, channel_type, effective_config_id) 三元组，
        同一租户同一渠道下不同 config_id 各自独立缓存，互不驱逐。

        Args:
            tenant_id: 租户 ID
            channel_type: 渠道类型
            config_id: 渠道配置 ID（可选）
                - 显式传入：按 (t, c, config_id) 精确命中缓存
                - 传 None：先从 DB 解析实际使用的 config_id（取 verified 优先），
                  再以该实际 config_id 作为缓存键。这样 None 调用与显式调用命中同一缓存槽，
                  不会产生孤儿缓存

        Returns:
            (adapter, config_id, subagent_type) 或 (None, None, None)
        """
        # Step 1: 解析 effective_config_id
        # config_id 为 None 时锁外先 _load_config 解析实际 config_id，
        # 用解析结果作为缓存键（避免 None 调用产生孤儿缓存，与显式调用共享缓存槽）
        cfg = None
        used_config_id: Optional[str] = None
        if config_id is None:
            cfg, used_config_id = cls._load_config(tenant_id, channel_type, None)
            if not cfg:
                return None, None, None
            effective_config_id = used_config_id
        else:
            effective_config_id = config_id

        cache_key = (tenant_id, channel_type, effective_config_id)

        # Step 2: 快速路径（命中未过期且配置版本未变化的缓存）
        entry = _ADAPTER_CACHE.get(cache_key)
        if entry and not _is_expired(entry) and not await _is_cache_stale(entry, effective_config_id):
            return entry["adapter"], entry["config_id"], entry.get("subagent_type")

        # Step 3: 锁内双重检查 + build（锁粒度收窄到 (t, c, config_id)）
        async with _get_cache_lock(tenant_id, channel_type, effective_config_id):
            entry = _ADAPTER_CACHE.get(cache_key)
            if entry and not _is_expired(entry) and not await _is_cache_stale(entry, effective_config_id):
                return entry["adapter"], entry["config_id"], entry.get("subagent_type")
            if entry:
                # 缓存已过期（TTL 或配置版本变化）：清掉旧实例，走重建
                _ADAPTER_CACHE.pop(cache_key, None)

            # 显式调用此时才 _load_config（None 调用上面已加载过）
            if cfg is None:
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
                "cfg_updated_at": cfg.get("updated_at"),
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
        config_id: Optional[str] = None,
        close: bool = True,
    ) -> None:
        """
        使缓存中的 adapter 失效（配置变更 / 删除时调用）

        Args:
            tenant_id: 租户 ID
            channel_type: 渠道类型
            config_id: 渠道配置 ID
                - 非空：精确失效 (t, c, config_id) 单条缓存
                - 为空：通配失效该 (tenant_id, channel_type) 下所有 config_id 的缓存
                        （配置变更场景下 config_id 通常已知，通配分支用于租户级清理）
            close: 是否调用 adapter.close() 释放 httpx 连接池
        """
        if config_id is not None:
            # 精确失效单条
            cache_key = (tenant_id, channel_type, config_id)
            entry = _ADAPTER_CACHE.pop(cache_key, None)
            if entry and close:
                await cls._close_adapter_safe(
                    entry.get("adapter"), tenant_id, channel_type, config_id
                )
            return

        # 通配失效：遍历 pop 所有 (t, c, *) 的键
        keys_to_remove = [
            k
            for k in list(_ADAPTER_CACHE.keys())
            if k[0] == tenant_id and k[1] == channel_type
        ]
        for key in keys_to_remove:
            entry = _ADAPTER_CACHE.pop(key, None)
            if entry and close:
                await cls._close_adapter_safe(
                    entry.get("adapter"), tenant_id, channel_type, key[2]
                )

    @staticmethod
    async def _close_adapter_safe(
        adapter: Any,
        tenant_id: str,
        channel_type: str,
        config_id: Optional[str],
    ) -> None:
        """安全关闭 adapter，吞异常仅记日志"""
        if not adapter or not hasattr(adapter, "close"):
            return
        try:
            await adapter.close()
        except Exception as e:
            logger.warning(
                f"adapter.close 失败: tenant={tenant_id}, type={channel_type}, "
                f"config={config_id}, error={e}"
            )

    @classmethod
    async def invalidate_tenant(cls, tenant_id: str) -> None:
        """使某租户所有渠道所有 config 的 adapter 失效（租户删除时调用）"""
        # 委托 invalidate_adapter 的通配分支，传入 close=True
        # 收集该租户下所有 (channel_type, config_id) 唯一组合后逐个通配清理
        channels_to_clear = {
            (k[1], k[2]) for k in list(_ADAPTER_CACHE.keys()) if k[0] == tenant_id
        }
        for channel_type, _config_id in channels_to_clear:
            # invalidate_adapter(config_id=None) 已能覆盖该 channel 下所有 config，
            # 多次调用同一 channel 是幂等的（pop 已不存在的 key 返回 None）
            await cls.invalidate_adapter(tenant_id, channel_type, config_id=None, close=True)

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
                        f"adapter.close 失败: tenant={key[0]}, type={key[1]}, "
                        f"config={key[2]}, error={e}"
                    )
        logger.info(f"ChannelFactory 已关闭 {len(keys)} 个缓存 adapter")
