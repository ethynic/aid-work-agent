"""
渠道适配器基类

定义所有渠道适配器必须实现的接口
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from loguru import logger

from src.config.settings import settings
from src.models.message import UnifiedMessage, UnifiedResponse


def build_public_url(download_url: str) -> str:
    """将相对路径转为完整的公开 URL，供渠道端文件下载使用"""
    if download_url.startswith("http://") or download_url.startswith("https://"):
        return download_url
    base = settings.app.public_base_url or ""
    if not base:
        logger.warning("public_base_url 未配置，渠道端文件链接可能不可访问")
    return f"{base.rstrip('/')}{download_url}"


def format_file_size(size: int) -> str:
    """格式化文件大小为可读字符串"""
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    else:
        return f"{size / (1024 * 1024):.1f}MB"


class ChannelAdapter(ABC):
    """
    渠道适配器抽象基类
    
    所有渠道适配器（企业微信、钉钉、飞书等）都需要继承此类
    """
    
    @property
    @abstractmethod
    def channel_type(self) -> str:
        """
        获取渠道类型
        
        Returns:
            渠道类型标识
        """
        pass
    
    @abstractmethod
    async def parse_message(self, raw_message: Dict[str, Any]) -> UnifiedMessage:
        """
        解析原始消息为统一格式
        
        Args:
            raw_message: 原始消息数据
        
        Returns:
            统一消息格式
        """
        pass
    
    @abstractmethod
    async def send_message(self, message: UnifiedResponse) -> bool:
        """
        发送统一格式消息
        
        Args:
            message: 统一响应格式
        
        Returns:
            是否发送成功
        """
        pass
    
    @abstractmethod
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """
        获取用户信息
        
        Args:
            user_id: 用户ID
        
        Returns:
            用户信息字典
        """
        pass
    
    async def verify_signature(
        self,
        signature: str,
        timestamp: str,
        nonce: str,
        body: str,
    ) -> bool:
        """
        验证消息签名
        
        Args:
            signature: 签名
            timestamp: 时间戳
            nonce: 随机数
            body: 消息体
        
        Returns:
            签名是否有效
        """
        # 默认实现，子类可覆盖
        return True
    
    def format_response(self, text: str, **kwargs) -> Dict[str, Any]:
        """
        格式化响应消息
        
        Args:
            text: 响应文本
            **kwargs: 额外参数
        
        Returns:
            格式化后的响应
        """
        return {
            "text": text,
            **kwargs,
        }
