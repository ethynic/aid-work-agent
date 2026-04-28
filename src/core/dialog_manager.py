"""
对话管理器

⚠️ 未投入使用：当前生产流程中，Agent 直接使用 ShortTermMemory 管理对话上下文，
DialogManager 未被任何业务代码调用。保留此模块供未来扩展使用。
"""

import uuid
from typing import Any, Dict, List, Optional

from loguru import logger

from src.config.settings import settings
from src.models.session import Session, SessionContext, SessionState
from src.models.message import UnifiedMessage, UnifiedResponse
from src.memory.short_term import ShortTermMemory


class DialogManager:
    """
    对话管理器

    ⚠️ 未投入使用。当前生产流程中 Agent 直接管理对话上下文。

    负责：
    - 管理多轮对话上下文
    - 维护会话状态
    - 协调各引擎工作
    """
    
    def __init__(
        self,
        max_history: int = 10,
        session_ttl: int = 3600,
    ):
        """
        初始化对话管理器
        
        Args:
            max_history: 最大历史消息数
            session_ttl: 会话过期时间(秒)
        """
        self.max_history = max_history
        self.session_ttl = session_ttl
        self._sessions: Dict[str, Session] = {}
        self._memory = ShortTermMemory(max_messages=max_history)
    
    def create_session(
        self,
        user_id: str,
        channel_type: str = "web",
    ) -> Session:
        """
        创建新会话
        
        Args:
            user_id: 用户ID
            channel_type: 渠道类型
        
        Returns:
            新创建的会话
        """
        session_id = str(uuid.uuid4())
        session = Session(
            session_id=session_id,
            user_id=user_id,
            channel_type=channel_type,
        )
        self._sessions[session_id] = session
        logger.info(f"创建新会话: {session_id}, 用户: {user_id}")
        return session
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """
        获取会话
        
        Args:
            session_id: 会话ID
        
        Returns:
            会话实例或None
        """
        session = self._sessions.get(session_id)
        if session and not session.is_expired(self.session_ttl):
            session.touch()
            return session
        return None
    
    def get_or_create_session(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        channel_type: str = "web",
    ) -> Session:
        """
        获取或创建会话
        
        Args:
            user_id: 用户ID
            session_id: 会话ID（可选）
            channel_type: 渠道类型
        
        Returns:
            会话实例
        """
        if session_id:
            session = self.get_session(session_id)
            if session:
                return session
        
        return self.create_session(user_id, channel_type)
    
    def close_session(self, session_id: str) -> None:
        """
        关闭会话
        
        Args:
            session_id: 会话ID
        """
        session = self._sessions.get(session_id)
        if session:
            session.context.state = SessionState.CLOSED
            self._memory.clear(session_id)
            logger.info(f"关闭会话: {session_id}")
    
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        """
        添加消息到会话历史
        
        Args:
            session_id: 会话ID
            role: 角色 (user/assistant)
            content: 消息内容
        """
        session = self.get_session(session_id)
        if session:
            session.context.add_message(role, content)
            self._memory.add_message(session_id, {
                "role": role,
                "content": content,
            })
    
    def get_context(
        self,
        session_id: str,
    ) -> List[Dict[str, str]]:
        """
        获取会话上下文
        
        Args:
            session_id: 会话ID
        
        Returns:
            消息历史列表
        """
        return self._memory.get_context(session_id)
    
    def get_recent_messages(
        self,
        session_id: str,
        limit: int = 10,
    ) -> List[Dict[str, str]]:
        """
        获取最近的消息
        
        Args:
            session_id: 会话ID
            limit: 最大数量
        
        Returns:
            消息列表
        """
        session = self.get_session(session_id)
        if session:
            return session.context.get_recent_messages(limit)
        return []
    
    def set_intent(
        self,
        session_id: str,
        intent: str,
    ) -> None:
        """
        设置当前意图
        
        Args:
            session_id: 会话ID
            intent: 意图名称
        """
        session = self.get_session(session_id)
        if session:
            session.context.current_intent = intent
    
    def get_intent(self, session_id: str) -> Optional[str]:
        """
        获取当前意图
        
        Args:
            session_id: 会话ID
        
        Returns:
            意图名称或None
        """
        session = self.get_session(session_id)
        if session:
            return session.context.current_intent
        return None
    
    def set_slot(
        self,
        session_id: str,
        key: str,
        value: Any,
    ) -> None:
        """
        设置槽位值
        
        Args:
            session_id: 会话ID
            key: 槽位名称
            value: 槽位值
        """
        session = self.get_session(session_id)
        if session:
            session.context.set_slot(key, value)
    
    def get_slot(
        self,
        session_id: str,
        key: str,
        default: Any = None,
    ) -> Any:
        """
        获取槽位值
        
        Args:
            session_id: 会话ID
            key: 槽位名称
            default: 默认值
        
        Returns:
            槽位值
        """
        session = self.get_session(session_id)
        if session:
            return session.context.get_slot(key, default)
        return default
    
    def get_all_slots(self, session_id: str) -> Dict[str, Any]:
        """
        获取所有槽位
        
        Args:
            session_id: 会话ID
        
        Returns:
            槽位字典
        """
        session = self.get_session(session_id)
        if session:
            return session.context.slots.copy()
        return {}
    
    def clear_slots(self, session_id: str) -> None:
        """
        清空槽位
        
        Args:
            session_id: 会话ID
        """
        session = self.get_session(session_id)
        if session:
            session.context.clear_slots()
    
    def set_state(
        self,
        session_id: str,
        state: SessionState,
    ) -> None:
        """
        设置会话状态
        
        Args:
            session_id: 会话ID
            state: 新状态
        """
        session = self.get_session(session_id)
        if session:
            session.context.state = state
    
    def get_state(self, session_id: str) -> Optional[SessionState]:
        """
        获取会话状态
        
        Args:
            session_id: 会话ID
        
        Returns:
            会话状态或None
        """
        session = self.get_session(session_id)
        if session:
            return session.context.state
        return None
    
    def cleanup_expired_sessions(self) -> int:
        """
        清理过期会话
        
        Returns:
            清理的会话数量
        """
        expired = []
        for session_id, session in self._sessions.items():
            if session.is_expired(self.session_ttl):
                expired.append(session_id)
        
        for session_id in expired:
            del self._sessions[session_id]
            self._memory.clear(session_id)
        
        if expired:
            logger.info(f"清理过期会话: {len(expired)}个")
        
        return len(expired)
    
    def get_session_count(self) -> int:
        """
        获取活跃会话数量
        
        Returns:
            会话数量
        """
        return len(self._sessions)


# 全局对话管理器实例
dialog_manager = DialogManager(
    max_history=settings.memory.short_term.max_messages,
    session_ttl=settings.memory.short_term.ttl,
)
