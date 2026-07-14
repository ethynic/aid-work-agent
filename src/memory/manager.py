"""
记忆管理器

统一管理短期、中期、长期记忆
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from .short_term import ShortTermMemory
from .models import MemoryItem, MemoryType, ConversationMemory, UserPreference


class MemoryManager:
    """
    记忆管理器
    
    统一管理所有类型的记忆：
    - 短期记忆：当前会话上下文
    - 中期记忆：会话摘要（V1.5）
    - 长期记忆：用户画像（V2.0）
    """
    
    def __init__(
        self,
        max_short_term_messages: int = 100,
        short_term_ttl: int = 3600,
    ):
        """
        初始化记忆管理器

        Args:
            max_short_term_messages: 短期记忆最大消息数
            short_term_ttl: 短期记忆过期时间
        """
        self.short_term = ShortTermMemory(
            max_messages=max_short_term_messages,
            ttl=short_term_ttl,
        )
        # 中期记忆和长期记忆在V1.5/V2.0实现
        self._conversation_memories: Dict[str, ConversationMemory] = {}
        self._user_preferences: Dict[str, UserPreference] = {}

    # ==================== 内部属性访问（过渡方案） ====================

    @property
    def _cache(self):
        """临时兼容：早期直接操作缓存的过渡方案。后续 Phase 应重构为公开方法。"""
        return self.short_term._cache

    # ==================== 短期记忆操作 ====================

    def add(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        """
        添加简单消息到短期记忆

        Args:
            session_id: 会话ID
            role: 角色
            content: 内容
        """
        self.short_term.add(session_id, role, content)

    def add_message(
        self,
        session_id: str,
        message: Dict[str, Any],
    ) -> None:
        """
        添加完整消息到短期记忆

        Args:
            session_id: 会话ID
            message: 消息字典，需包含 role 和 content
        """
        self.short_term.add_message(session_id, message)
    
    def get_context(
        self,
        session_id: str,
    ) -> List[Dict[str, Any]]:
        """
        获取短期记忆上下文
        
        Args:
            session_id: 会话ID
        
        Returns:
            消息列表
        """
        return self.short_term.get_context(session_id)
    
    def clear_session(self, session_id: str) -> None:
        """
        清除会话记忆

        Args:
            session_id: 会话ID
        """
        self.short_term.clear(session_id)

    def clear(self, session_id: str) -> None:
        """清除会话记忆（别名，与 ShortTermMemory 接口兼容）"""
        self.short_term.clear(session_id)

    def get_message_count(self, session_id: str) -> int:
        """获取会话消息数量"""
        return self.short_term.get_message_count(session_id)

    def get_active_sessions(self) -> List[str]:
        """获取所有活跃会话ID列表"""
        return self.short_term.get_active_sessions()

    def load_history(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
    ) -> None:
        """批量加载历史消息到短期记忆（仅在 session 为空时生效）"""
        self.short_term.load_history(session_id, messages)
    
    # ==================== 对话记忆操作 ====================
    
    def create_conversation_memory(
        self,
        session_id: str,
        user_id: str,
    ) -> ConversationMemory:
        """
        创建对话记忆
        
        Args:
            session_id: 会话ID
            user_id: 用户ID
        
        Returns:
            对话记忆实例
        """
        memory = ConversationMemory(
            session_id=session_id,
            user_id=user_id,
        )
        self._conversation_memories[session_id] = memory
        return memory
    
    def get_conversation_memory(
        self,
        session_id: str,
    ) -> Optional[ConversationMemory]:
        """
        获取对话记忆
        
        Args:
            session_id: 会话ID
        
        Returns:
            对话记忆实例或None
        """
        return self._conversation_memories.get(session_id)
    
    def save_conversation_summary(
        self,
        session_id: str,
        summary: str,
        key_entities: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        保存对话摘要
        
        Args:
            session_id: 会话ID
            summary: 摘要内容
            key_entities: 关键实体
        """
        memory = self.get_conversation_memory(session_id)
        if memory:
            memory.summary = summary
            if key_entities:
                for entity_type, entity_value in key_entities.items():
                    memory.add_entity(entity_type, entity_value)
    
    # ==================== 用户偏好操作 ====================
    
    def get_user_preference(
        self,
        user_id: str,
    ) -> UserPreference:
        """
        获取用户偏好
        
        Args:
            user_id: 用户ID
        
        Returns:
            用户偏好实例
        """
        if user_id not in self._user_preferences:
            self._user_preferences[user_id] = UserPreference(user_id=user_id)
        return self._user_preferences[user_id]
    
    def update_user_preference(
        self,
        user_id: str,
        preference_type: str,
        value: Any,
    ) -> None:
        """
        更新用户偏好
        
        Args:
            user_id: 用户ID
            preference_type: 偏好类型
            value: 偏好值
        """
        preference = self.get_user_preference(user_id)
        
        if preference_type == "communication_style":
            preference.communication_style = value
        elif preference_type == "detail_level":
            preference.detail_level = value
        elif preference_type == "language":
            preference.language = value
        elif preference_type == "tool_usage":
            preference.update_tool_usage(value)
        elif preference_type == "contact":
            preference.update_contact(value)
        
        preference.updated_at = datetime.now()
    
    # ==================== 记忆检索 ====================
    
    def get_relevant_context(
        self,
        session_id: str,
        user_id: str,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取相关上下文
        
        Args:
            session_id: 会话ID
            user_id: 用户ID
            query: 查询内容（可选，用于语义检索）
        
        Returns:
            相关上下文字典
        """
        context = {
            "short_term": self.short_term.get_context(session_id),
            "user_preference": self.get_user_preference(user_id).model_dump(),
        }
        
        # 添加对话记忆（如果有）
        conv_memory = self.get_conversation_memory(session_id)
        if conv_memory:
            context["conversation"] = {
                "summary": conv_memory.summary,
                "key_entities": conv_memory.key_entities,
                "topics": conv_memory.topics,
            }
        
        return context
    
    def to_llm_messages(
        self,
        session_id: str,
        system_prompt: Optional[str] = None,
        include_preference: bool = False,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """
        转换为LLM消息格式
        
        Args:
            session_id: 会话ID
            system_prompt: 系统提示词
            include_preference: 是否包含用户偏好
            user_id: 用户ID
        
        Returns:
            LLM格式的消息列表
        """
        messages = []
        
        # 构建系统提示词
        if system_prompt:
            enhanced_prompt = system_prompt
            
            # 添加用户偏好到系统提示词
            if include_preference and user_id:
                preference = self.get_user_preference(user_id)
                enhanced_prompt += f"\n\n用户偏好：\n- 沟通风格：{preference.communication_style}\n- 详细程度：{preference.detail_level}\n- 首选语言：{preference.language}"
            
            messages.append({
                "role": "system",
                "content": enhanced_prompt,
            })
        
        # 添加对话历史
        context = self.short_term.get_context(session_id)
        for msg in context:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ["user", "assistant"]:
                messages.append({
                    "role": role,
                    "content": content,
                })
        
        return messages
    
    # ==================== 清理操作 ====================

    def cleanup_expired(self) -> int:
        """
        清理过期记忆，返回清理的会话数量
        """
        before = len(self.short_term._cache)
        self.short_term.get_active_sessions()
        after = len(self.short_term._cache)
        cleaned = before - after
        if cleaned > 0:
            logger.debug(f"记忆清理完成，清理 {cleaned} 个过期会话，剩余 {after} 个活跃会话")
        return cleaned
    
    def get_stats(self) -> Dict[str, int]:
        """
        获取记忆统计
        
        Returns:
            统计信息
        """
        return {
            "active_sessions": len(self.short_term.get_active_sessions()),
            "conversation_memories": len(self._conversation_memories),
            "user_preferences": len(self._user_preferences),
        }
