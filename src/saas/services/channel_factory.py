"""
渠道适配器工厂

从租户渠道配置（tenant_channel_configs 表）动态创建 ChannelAdapter 实例。
"""

import json
from typing import Optional, Dict, Any

from loguru import logger

from src.saas.db.channel_config_db import ChannelConfigDB


class ChannelFactory:
    """
    渠道适配器工厂

    根据渠道类型和配置字典创建对应的 ChannelAdapter 实例。
    """

    # 渠道类型 → 适配器类（延迟导入）
    _ADAPTER_CLASSES = {
        "wecom": "src.channels.wecom.adapter.WeComAdapter",
        "dingtalk": "src.channels.dingtalk.adapter.DingtalkAdapter",
        "feishu": "src.channels.feishu.adapter.FeishuAdapter",
    }

    @classmethod
    def create_adapter(cls, channel_type: str, config: dict):
        """
        根据渠道类型和配置创建适配器

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
    def create_from_tenant_config(cls, tenant_id: str, channel_type: str, config_id: Optional[str] = None):
        """
        从租户 DB 配置创建适配器

        Args:
            tenant_id: 租户 ID
            channel_type: 渠道类型
            config_id: 渠道配置 ID（可选，传入时按 ID 精确查找）

        Returns:
            (adapter, config_id, subagent_type) 或 (None, None, None)
        """
        # 按 config_id 精确查找
        if config_id:
            cfg = ChannelConfigDB.get_by_id(config_id)
            if cfg and cfg.get("tenant_id") == tenant_id and cfg.get("channel_type") == channel_type:
                try:
                    adapter = cls.create_adapter(channel_type, cfg["config"])
                    return adapter, cfg["config_id"], cfg.get("subagent_type")
                except Exception as e:
                    logger.error(f"Failed to create adapter for config {config_id}: {e}")
                    return None, None, None
            return None, None, None

        # 兼容：未传 config_id 时取第一个已验证的配置
        configs = ChannelConfigDB.list_by_tenant(tenant_id, channel_type)
        if not configs:
            return None, None, None

        for cfg in configs:
            if cfg.get("verified"):
                try:
                    adapter = cls.create_adapter(channel_type, cfg["config"])
                    return adapter, cfg["config_id"], cfg.get("subagent_type")
                except Exception as e:
                    logger.error(f"Failed to create adapter for tenant {tenant_id}/{channel_type}: {e}")
                    return None, None, None

        # 如果没有已验证的，取第一个
        cfg = configs[0]
        try:
            adapter = cls.create_adapter(channel_type, cfg["config"])
            return adapter, cfg["config_id"], cfg.get("subagent_type")
        except Exception as e:
            logger.error(f"Failed to create adapter for tenant {tenant_id}/{channel_type}: {e}")
            return None, None, None
