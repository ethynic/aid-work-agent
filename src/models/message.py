"""
消息模型

定义统一的消息格式，用于多渠道消息标准化
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChannelType(str, Enum):
    """渠道类型"""
    WECOM = "wecom"
    DINGTALK = "dingtalk"
    FEISHU = "feishu"
    WEB = "web"


class MessageType(str, Enum):
    """消息类型"""
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    EVENT = "event"


class Attachment(BaseModel):
    """附件模型"""
    type: str = Field(..., description="附件类型: image, file, etc.")
    url: str = Field(..., description="附件URL")
    name: Optional[str] = Field(None, description="附件名称")
    size: Optional[int] = Field(None, description="附件大小(字节)")
    mime_type: Optional[str] = Field(None, description="MIME类型")


class UnifiedMessage(BaseModel):
    """
    统一消息格式
    
    所有渠道的消息都会转换为这个统一格式
    """
    message_id: str = Field(..., description="消息唯一ID")
    channel_type: ChannelType = Field(..., description="渠道类型")
    user_id: str = Field(..., description="用户ID")
    user_name: Optional[str] = Field(None, description="用户名称")
    department_id: Optional[str] = Field(None, description="部门ID")
    message_type: MessageType = Field(default=MessageType.TEXT, description="消息类型")
    content: Dict[str, Any] = Field(default_factory=dict, description="消息内容")
    attachments: List[Attachment] = Field(default_factory=list, description="附件列表")
    timestamp: datetime = Field(default_factory=datetime.now, description="消息时间戳")
    raw_message: Dict[str, Any] = Field(default_factory=dict, description="原始消息")
    
    class Config:
        use_enum_values = True
    
    @property
    def text(self) -> str:
        """获取文本内容"""
        return self.content.get("text", "")
    
    @classmethod
    def from_text(
        cls,
        text: str,
        user_id: str,
        message_id: str = "",
        channel_type: ChannelType = ChannelType.WEB,
        user_name: Optional[str] = None,
    ) -> "UnifiedMessage":
        """
        从文本创建消息
        
        Args:
            text: 文本内容
            user_id: 用户ID
            message_id: 消息ID
            channel_type: 渠道类型
            user_name: 用户名称
        
        Returns:
            UnifiedMessage实例
        """
        return cls(
            message_id=message_id or f"msg_{datetime.now().timestamp()}",
            channel_type=channel_type,
            user_id=user_id,
            user_name=user_name,
            message_type=MessageType.TEXT,
            content={"text": text},
        )
    
    def to_llm_message(self) -> Dict[str, str]:
        """
        转换为LLM消息格式
        
        Returns:
            OpenAI格式的消息字典
        """
        return {
            "role": "user",
            "content": self.text,
        }


class UnifiedResponse(BaseModel):
    """
    统一响应格式
    
    Agent响应转换为这个格式，再由渠道适配器转换为各平台格式
    """
    message_id: str = Field(..., description="消息唯一ID")
    reply_to: str = Field(..., description="回复的消息ID")
    content: Dict[str, Any] = Field(default_factory=dict, description="响应内容")
    attachments: List[Attachment] = Field(default_factory=list, description="附件列表")
    timestamp: datetime = Field(default_factory=datetime.now, description="响应时间戳")
    
    @property
    def text(self) -> str:
        """获取文本内容"""
        return self.content.get("text", "")
    
    @classmethod
    def from_text(
        cls,
        text: str,
        reply_to: str,
        message_id: str = "",
    ) -> "UnifiedResponse":
        """
        从文本创建响应
        
        Args:
            text: 文本内容
            reply_to: 回复的消息ID
            message_id: 消息ID
        
        Returns:
            UnifiedResponse实例
        """
        return cls(
            message_id=message_id or f"resp_{datetime.now().timestamp()}",
            reply_to=reply_to,
            content={"text": text},
        )
