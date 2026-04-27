"""
短信发送器抽象基类

定义所有短信通道必须实现的接口
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional


class SmsSender(ABC):
    """
    短信发送器抽象基类

    所有短信通道适配器（助通、阿里云、腾讯云等）都需要继承此类
    """

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """
        获取通道名称

        Returns:
            通道类型标识
        """
        pass

    @abstractmethod
    def send(
        self,
        mobile: str,
        template_id: Optional[str] = None,
        template_params: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """
        发送短信

        Args:
            mobile: 手机号
            template_id: 模板ID，如果为None则使用默认验证码模板
            template_params: 模板参数，键值对，例如 {"code": "123456}

        Returns:
            响应字典，包含发送结果，code=200 表示成功
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        检查短信通道是否可用（已配置）

        Returns:
            是否可用
        """
        pass

    def validate_mobile(self, mobile: str) -> bool:
        """
        验证手机号格式

        Args:
            mobile: 手机号

        Returns:
            是否有效
        """
        return len(mobile) == 11 and mobile.isdigit()
