"""
短期记忆系统

管理当前会话的对话上下文
"""

from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional


class ShortTermMemory:
    """
    短期记忆管理器
    
    使用滑动窗口策略管理会话上下文
    """
    
    def __init__(
        self,
        max_messages: int = 100,
        ttl: int = 3600,
    ):
        """
        初始化短期记忆
        
        Args:
            max_messages: 最大消息数量
            ttl: 过期时间(秒)
        """
        self.max_messages = max_messages
        self.ttl = ttl
        self._cache: Dict[str, deque] = {}
        self._timestamps: Dict[str, datetime] = {}
    
    def add_message(
        self,
        session_id: str,
        message: Dict[str, Any],
    ) -> None:
        """
        添加消息到记忆
        
        Args:
            session_id: 会话ID
            message: 消息字典
        """
        if session_id not in self._cache:
            self._cache[session_id] = deque(maxlen=self.max_messages)
        
        # 添加时间戳
        if "timestamp" not in message:
            message["timestamp"] = datetime.now().isoformat()
        
        self._cache[session_id].append(message)
        self._timestamps[session_id] = datetime.now()
    
    def add(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        """
        添加消息的便捷方法
        
        Args:
            session_id: 会话ID
            role: 角色 (user/assistant/system)
            content: 消息内容
        """
        self.add_message(session_id, {
            "role": role,
            "content": content,
        })
    
    def get_context(
        self,
        session_id: str,
    ) -> List[Dict[str, Any]]:
        """
        获取会话上下文
        
        Args:
            session_id: 会话ID
        
        Returns:
            消息列表
        """
        if session_id not in self._cache:
            return []
        
        # 检查是否过期
        if self._is_expired(session_id):
            self.clear(session_id)
            return []
        
        return list(self._cache[session_id])
    
    def get_recent_messages(
        self,
        session_id: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        获取最近的消息
        
        Args:
            session_id: 会话ID
            limit: 最大数量
        
        Returns:
            消息列表
        """
        context = self.get_context(session_id)
        return context[-limit:] if context else []
    
    def get_last_user_message(
        self,
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        获取最后一条用户消息
        
        Args:
            session_id: 会话ID
        
        Returns:
            消息字典或None
        """
        context = self.get_context(session_id)
        for msg in reversed(context):
            if msg.get("role") == "user":
                return msg
        return None
    
    def get_last_assistant_message(
        self,
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        获取最后一条助手消息
        
        Args:
            session_id: 会话ID
        
        Returns:
            消息字典或None
        """
        context = self.get_context(session_id)
        for msg in reversed(context):
            if msg.get("role") == "assistant":
                return msg
        return None
    
    def clear(self, session_id: str) -> None:
        """
        清除会话记忆
        
        Args:
            session_id: 会话ID
        """
        if session_id in self._cache:
            del self._cache[session_id]
        if session_id in self._timestamps:
            del self._timestamps[session_id]
    
    def _is_expired(self, session_id: str) -> bool:
        """
        检查会话是否过期
        
        Args:
            session_id: 会话ID
        
        Returns:
            是否过期
        """
        if session_id not in self._timestamps:
            return True
        
        elapsed = (datetime.now() - self._timestamps[session_id]).total_seconds()
        return elapsed > self.ttl
    
    def get_message_count(self, session_id: str) -> int:
        """
        获取消息数量
        
        Args:
            session_id: 会话ID
        
        Returns:
            消息数量
        """
        if session_id not in self._cache:
            return 0
        return len(self._cache[session_id])
    
    def get_active_sessions(self) -> List[str]:
        """
        获取活跃会话列表
        
        Returns:
            会话ID列表
        """
        active = []
        expired = []
        
        for session_id in self._cache:
            if self._is_expired(session_id):
                expired.append(session_id)
            else:
                active.append(session_id)
        
        # 清理过期会话
        for session_id in expired:
            self.clear(session_id)
        
        return active
    
    def to_llm_messages(
        self,
        session_id: str,
        system_prompt: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """
        转换为LLM消息格式
        
        Args:
            session_id: 会话ID
            system_prompt: 系统提示词（可选）
        
        Returns:
            LLM格式的消息列表
        """
        messages = []
        
        # 添加系统提示词
        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt,
            })
        
        # 添加对话历史
        context = self.get_context(session_id)
        for msg in context:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ["user", "assistant"]:
                messages.append({
                    "role": role,
                    "content": content,
                })
        
        return messages
