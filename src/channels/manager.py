"""
渠道管理器

管理所有渠道适配器的注册和路由
"""

from typing import Any, Dict, Optional, Type

from loguru import logger

from .base import ChannelAdapter


class ChannelManager:
    """
    渠道管理器
    
    管理：
    - 渠道适配器注册
    - 消息路由
    - 用户信息获取
    """
    
    def __init__(self):
        """初始化渠道管理器"""
        self._adapters: Dict[str, ChannelAdapter] = {}
    
    def register(self, adapter: ChannelAdapter) -> None:
        """
        注册渠道适配器
        
        Args:
            adapter: 适配器实例
        """
        channel_type = adapter.channel_type
        self._adapters[channel_type] = adapter
        logger.info(f"注册渠道适配器: {channel_type}")
    
    def unregister(self, channel_type: str) -> None:
        """
        注销渠道适配器
        
        Args:
            channel_type: 渠道类型
        """
        if channel_type in self._adapters:
            del self._adapters[channel_type]
            logger.info(f"注销渠道适配器: {channel_type}")
    
    def get_adapter(self, channel_type: str) -> Optional[ChannelAdapter]:
        """
        获取渠道适配器
        
        Args:
            channel_type: 渠道类型
        
        Returns:
            适配器实例或None
        """
        return self._adapters.get(channel_type)
    
    def has_adapter(self, channel_type: str) -> bool:
        """
        检查适配器是否存在
        
        Args:
            channel_type: 渠道类型
        
        Returns:
            是否存在
        """
        return channel_type in self._adapters
    
    def list_channels(self) -> list:
        """
        列出所有渠道
        
        Returns:
            渠道类型列表
        """
        return list(self._adapters.keys())
    
    async def route_message(
        self,
        channel_type: str,
        raw_message: Dict[str, Any],
    ):
        """
        路由消息到对应适配器
        
        Args:
            channel_type: 渠道类型
            raw_message: 原始消息
        
        Returns:
            统一消息格式
        """
        adapter = self.get_adapter(channel_type)
        if not adapter:
            raise ValueError(f"不支持的渠道类型: {channel_type}")
        
        return await adapter.parse_message(raw_message)


# 全局渠道管理器
channel_manager = ChannelManager()
