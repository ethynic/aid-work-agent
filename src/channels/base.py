"""
渠道适配器基类

定义所有渠道适配器必须实现的接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from loguru import logger

from src.config.settings import settings
from src.models.message import UnifiedMessage, UnifiedResponse


# 低优先级 status 发送结果状态（Phase 3 verbose，设计 §9.2/§9.3）
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_SUPPRESSED_RATE_LIMIT = "suppressed_rate_limit"
STATUS_SUPPRESSED_UNSUPPORTED = "suppressed_unsupported"
STATUS_SUPPRESSED_REPLY_BUDGET = "suppressed_reply_budget"


@dataclass(frozen=True)
class StatusDeliveryResult:
    """低优先级 status/verbose 消息的一次投递结果。

    判别「真发送成功」的唯一标准是 ``status == "sent"``（``is_sent``）。
    suppressed_* 表示未发送且不占额度/预算，不是失败重试对象（dispatcher 不重试）。
    """

    status: str
    reason: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_sent(self) -> bool:
        return self.status == STATUS_SENT

    @property
    def suppressed(self) -> bool:
        return self.status.startswith("suppressed_")

    @classmethod
    def sent(cls, reason: str = "") -> "StatusDeliveryResult":
        return cls(status=STATUS_SENT, reason=reason)

    @classmethod
    def failed(cls, reason: str = "") -> "StatusDeliveryResult":
        return cls(status=STATUS_FAILED, reason=reason)

    @classmethod
    def timeout(cls, reason: str = "") -> "StatusDeliveryResult":
        return cls(status=STATUS_TIMEOUT, reason=reason)

    @classmethod
    def suppressed_rate_limit(cls, reason: str = "") -> "StatusDeliveryResult":
        return cls(status=STATUS_SUPPRESSED_RATE_LIMIT, reason=reason)

    @classmethod
    def suppressed_unsupported(cls, reason: str = "") -> "StatusDeliveryResult":
        return cls(status=STATUS_SUPPRESSED_UNSUPPORTED, reason=reason)

    @classmethod
    def suppressed_reply_budget(cls, reason: str = "") -> "StatusDeliveryResult":
        return cls(status=STATUS_SUPPRESSED_REPLY_BUDGET, reason=reason)


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

    async def send_status_message(
        self,
        message: UnifiedResponse,
        *,
        reserve_for_final: int = 1,
    ) -> StatusDeliveryResult:
        """低优先级 status/verbose 消息发送入口（Phase 3，设计 §9.3）。

        能力契约：
        - adapter 必须在同一次限流判定中确认「本次发送后至少还剩
          ``reserve_for_final`` 个额度」（原子预留，禁止先查余额再普通发送）；
        - 额度不足返回 ``suppressed_rate_limit``，不发送、不占额度；
        - 不支持安全预留的渠道**默认抑制**（本基类实现），绝不降级调
          ``send_message``——否则 verbose 可能占掉 final 的最后一个额度。

        仅 wecom / dingtalk / feishu / wecom_kf / wecom_personal_rpa 覆盖本方法。
        """
        return StatusDeliveryResult.suppressed_unsupported(
            "adapter does not support atomic-reserved status send"
        )
    
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
