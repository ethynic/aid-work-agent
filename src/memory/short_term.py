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
        max_messages: int = 200,
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

        cache miss（新 worker 进程）时从 chat_messages 表恢复，含 tool 消息（assistant with tool_calls + role:tool 配对）。
        恢复后填进 deque，后续同 worker 命中走快路径。

        Args:
            session_id: 会话ID

        Returns:
            消息列表
        """
        if session_id not in self._cache:
            db_messages = self._load_from_db(session_id)
            if db_messages:
                self._cache[session_id] = deque(maxlen=self.max_messages)
                for msg in db_messages:
                    self._cache[session_id].append(msg)
                self._timestamps[session_id] = datetime.now()
            else:
                return []

        # 检查是否过期
        if self._is_expired(session_id):
            self.clear(session_id)
            return []

        return list(self._cache[session_id])

    @staticmethod
    def _load_from_db(session_id: str) -> List[Dict[str, Any]]:
        """从 chat_messages 表恢复会话消息（含 tool 消息），转换为 LLM messages 格式。

        DB 存储约定：
        - role=user/assistant/tool
        - assistant 有两种子情况：最终回复（无 tool_calls）/ 决定调工具（metadata.tool_calls 存在）
        - assistant(tool_calls) 的 content 存空字符串，reasoning_content 在 metadata
        """
        import json
        from src.db.models import MessageDB

        # 防御：渠道会话的上下文由 agent._rebuild_memory_from_db 从 channel_messages 重建，
        # 此 cache-miss 自动恢复路径只服务于 web 会话。渠道会话若误入此路径，不能读 chat_messages
        # （可能含迁移期残留的陈旧行，会劫持真实对话，见 wecom-kf-context-loss-research.md §9），
        # 返回空更安全（渠道会话的权威历史已由 agent 重建流程加载）。
        try:
            from src.channels.session import channel_session_manager
            if channel_session_manager.is_channel_session(session_id):
                return []
        except Exception:
            pass

        try:
            rows = MessageDB.list_by_session(session_id)
        except Exception:
            return []

        messages: List[Dict[str, Any]] = []
        for row in rows:
            role = row.get("role")
            content = row.get("content") or ""
            meta_raw = row.get("metadata")
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) and meta_raw else (meta_raw or {})

            if role == "user":
                messages.append({"role": "user", "content": content})
            elif role == "assistant":
                msg: Dict[str, Any] = {"role": "assistant", "content": content}
                if meta.get("tool_calls"):
                    msg["tool_calls"] = meta["tool_calls"]
                if meta.get("reasoning_content"):
                    msg["reasoning_content"] = meta["reasoning_content"]
                messages.append(msg)
            elif role == "tool":
                messages.append({
                    "role": "tool",
                    "tool_call_id": meta.get("tool_call_id", ""),
                    "content": content,
                })
        return messages
    
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
    
    def load_history(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
    ) -> None:
        """
        批量加载历史消息到指定 session（仅在 session 为空时生效）

        用于会话恢复场景：Agent 首次处理某 session 的消息前，
        从 DB 加载历史消息到 ShortTermMemory。

        Args:
            session_id: 会话 ID
            messages: 历史消息列表，每条消息需包含 role 和 content 字段
                      顺序应为时间正序（ASC）
        """
        # 如果 session 已存在且有数据，跳过加载
        if session_id in self._cache and len(self._cache[session_id]) > 0:
            return

        dq = deque(maxlen=self.max_messages)
        for msg in messages:
            if "role" in msg and "content" in msg:
                if "timestamp" not in msg:
                    msg["timestamp"] = datetime.now().isoformat()
                dq.append(msg)

        self._cache[session_id] = dq
        self._timestamps[session_id] = datetime.now()

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
            if role == "system" and msg.get("_skill_summary"):
                # Skill 执行摘要：作为轻量用户消息保留，让后续对话能感知 Skill 执行结果
                messages.append({
                    "role": "user",
                    "content": f"[系统提醒] {content}"
                })
            elif role in ["user", "assistant"]:
                messages.append({
                    "role": role,
                    "content": content,
                })
        
        return messages
