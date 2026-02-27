"""
会话模型

管理用户会话状态和上下文
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SessionState(str, Enum):
    """会话状态"""
    IDLE = "idle"
    PROCESSING = "processing"
    WAITING_INPUT = "waiting_input"
    CLOSED = "closed"


class SessionContext(BaseModel):
    """
    会话上下文
    
    存储当前会话的状态信息
    """
    current_intent: Optional[str] = Field(None, description="当前意图")
    slots: Dict[str, Any] = Field(default_factory=dict, description="槽位信息")
    history: List[Dict[str, str]] = Field(default_factory=list, description="对话历史")
    state: SessionState = Field(default=SessionState.IDLE, description="会话状态")
    current_task_id: Optional[str] = Field(None, description="当前任务ID")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    
    class Config:
        use_enum_values = True
    
    def add_message(self, role: str, content: str) -> None:
        """
        添加消息到历史
        
        Args:
            role: 角色 (user/assistant)
            content: 消息内容
        """
        self.history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
    
    def get_recent_messages(self, limit: int = 10) -> List[Dict[str, str]]:
        """
        获取最近的消息
        
        Args:
            limit: 最大数量
        
        Returns:
            消息列表
        """
        return self.history[-limit:] if self.history else []
    
    def set_slot(self, key: str, value: Any) -> None:
        """
        设置槽位值
        
        Args:
            key: 槽位名称
            value: 槽位值
        """
        self.slots[key] = value
    
    def get_slot(self, key: str, default: Any = None) -> Any:
        """
        获取槽位值
        
        Args:
            key: 槽位名称
            default: 默认值
        
        Returns:
            槽位值
        """
        return self.slots.get(key, default)
    
    def clear_slots(self) -> None:
        """清空槽位"""
        self.slots.clear()


class Session(BaseModel):
    """
    会话模型
    
    管理用户会话的完整信息
    """
    session_id: str = Field(..., description="会话唯一ID")
    user_id: str = Field(..., description="用户ID")
    channel_type: str = Field(default="web", description="渠道类型")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")
    context: SessionContext = Field(default_factory=SessionContext, description="会话上下文")
    
    def touch(self) -> None:
        """更新会话时间"""
        self.updated_at = datetime.now()
    
    def is_expired(self, ttl_seconds: int = 3600) -> bool:
        """
        检查会话是否过期
        
        Args:
            ttl_seconds: 过期时间(秒)
        
        Returns:
            是否过期
        """
        elapsed = (datetime.now() - self.updated_at).total_seconds()
        return elapsed > ttl_seconds
    
    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典
        
        Returns:
            字典表示
        """
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "channel_type": self.channel_type,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "context": self.context.model_dump(),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        """
        从字典创建会话
        
        Args:
            data: 字典数据
        
        Returns:
            Session实例
        """
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        if isinstance(data.get("context"), dict):
            data["context"] = SessionContext(**data["context"])
        return cls(**data)
