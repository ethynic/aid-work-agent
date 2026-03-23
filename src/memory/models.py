"""
记忆数据模型

定义记忆项的数据结构
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    """记忆类型"""
    CONVERSATION = "conversation"
    PREFERENCE = "preference"
    FACT = "fact"
    EVENT = "event"


class MemoryItem(BaseModel):
    """
    记忆项模型
    
    表示单个记忆单元
    """
    memory_id: str = Field(..., description="记忆唯一ID")
    session_id: Optional[str] = Field(None, description="来源会话ID")
    memory_type: MemoryType = Field(default=MemoryType.CONVERSATION, description="记忆类型")
    content: str = Field(..., description="记忆内容")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="重要性分数")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    last_accessed: datetime = Field(default_factory=datetime.now, description="最后访问时间")
    access_count: int = Field(default=0, description="访问次数")
    
    class Config:
        use_enum_values = True
    
    def access(self) -> None:
        """记录访问"""
        self.last_accessed = datetime.now()
        self.access_count += 1
    
    def decay_importance(self, decay_factor: float = 0.95) -> None:
        """
        衰减重要性
        
        Args:
            decay_factor: 衰减因子
        """
        days_since_creation = (datetime.now() - self.created_at).days
        self.importance *= (decay_factor ** days_since_creation)
    
    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典
        
        Returns:
            字典表示
        """
        return {
            "memory_id": self.memory_id,
            "session_id": self.session_id,
            "memory_type": self.memory_type,
            "content": self.content,
            "metadata": self.metadata,
            "importance": self.importance,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "access_count": self.access_count,
        }


class ConversationMemory(BaseModel):
    """
    对话记忆模型
    
    存储对话相关的记忆
    """
    session_id: str = Field(..., description="会话ID")
    user_id: str = Field(..., description="用户ID")
    summary: str = Field(default="", description="对话摘要")
    key_entities: Dict[str, Any] = Field(default_factory=dict, description="关键实体")
    topics: List[str] = Field(default_factory=list, description="讨论主题")
    sentiment: Optional[str] = Field(None, description="情感倾向")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    
    def add_entity(self, entity_type: str, entity_value: Any) -> None:
        """
        添加实体
        
        Args:
            entity_type: 实体类型
            entity_value: 实体值
        """
        if entity_type not in self.key_entities:
            self.key_entities[entity_type] = []
        if entity_value not in self.key_entities[entity_type]:
            self.key_entities[entity_type].append(entity_value)
    
    def add_topic(self, topic: str) -> None:
        """
        添加主题
        
        Args:
            topic: 主题
        """
        if topic not in self.topics:
            self.topics.append(topic)


class UserPreference(BaseModel):
    """
    用户偏好模型
    
    存储用户的长期偏好
    """
    user_id: str = Field(..., description="用户ID")
    communication_style: str = Field(default="neutral", description="沟通风格")
    detail_level: str = Field(default="medium", description="详细程度")
    language: str = Field(default="zh-CN", description="首选语言")
    frequent_tools: List[str] = Field(default_factory=list, description="常用工具")
    frequent_contacts: List[str] = Field(default_factory=list, description="常用联系人")
    work_hours: Dict[str, str] = Field(default_factory=lambda: {"start": "09:00", "end": "18:00"}, description="工作时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")
    
    def update_tool_usage(self, tool_name: str) -> None:
        """
        更新工具使用记录
        
        Args:
            tool_name: 工具名称
        """
        if tool_name not in self.frequent_tools:
            self.frequent_tools.append(tool_name)
        self.updated_at = datetime.now()
    
    def update_contact(self, contact_name: str) -> None:
        """
        更新联系人记录
        
        Args:
            contact_name: 联系人名称
        """
        if contact_name not in self.frequent_contacts:
            self.frequent_contacts.append(contact_name)
        self.updated_at = datetime.now()
