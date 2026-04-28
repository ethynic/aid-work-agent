"""
会话记录服务

在Agent执行过程中收集并记录：
- 用户消息和AI回复
- Token消耗
- 执行详情（工具调用、思考过程等）
"""

import time
import json
import threading
from typing import Optional, List, Dict, Any, Callable, Coroutine, Any as AnyType
from dataclasses import dataclass, field, asdict
from datetime import datetime
from loguru import logger

from src.db.models import ChatRecordDB


@dataclass
class ToolExecution:
    """工具执行记录"""
    tool_name: str
    tool_args: Dict[str, Any]
    result: Any = None
    success: bool = True
    error: str = None
    start_time: float = field(default_factory=time.time)
    end_time: float = None
    duration_ms: int = 0

    def complete(self, result: Any, success: bool = True, error: str = None):
        self.end_time = time.time()
        self.duration_ms = int((self.end_time - self.start_time) * 1000)
        self.result = result
        self.success = success
        self.error = error


@dataclass
class ExecutionDetails:
    """执行详情"""
    tool_executions: List[ToolExecution] = field(default_factory=list)
    total_iterations: int = 0
    subagent_calls: List[Dict[str, Any]] = field(default_factory=list)
    plan_id: str = None
    plan_steps: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_executions": [
                {
                    "tool_name": te.tool_name,
                    "tool_args": te.tool_args,
                    "result": str(te.result)[:500] if te.result else None,
                    "success": te.success,
                    "error": te.error,
                    "duration_ms": te.duration_ms
                }
                for te in self.tool_executions
            ],
            "total_iterations": self.total_iterations,
            "subagent_calls": self.subagent_calls,
            "plan_id": self.plan_id,
            "plan_steps": self.plan_steps
        }


class SessionRecordService:
    """
    会话记录服务

    使用方式：
    1. 在开始处理消息前，创建服务实例
    2. 将 progress_callback 传递给 agent
    3. 处理完成后，调用 save() 保存记录
    """

    def __init__(self, session_id: str, user_id: str, user_message: str):
        self.session_id = session_id
        self.user_id = user_id
        self.user_message = user_message
        
        # 执行详情
        self.execution_details = ExecutionDetails()
        
        # Token消耗
        self.total_token_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.model = None
        
        # 状态
        self.start_time = time.time()
        self.end_time = None
        self.status = "completed"
        self.error_message = None
        
        # AI回复
        self.assistant_message = ""
        
        # 当前正在执行的工具
        self._current_tool: Optional[ToolExecution] = None
        
        # LLM调用计数（用于统计token）
        self._llm_call_count = 0

    def create_progress_callback(self):
        """
        创建用于传递给Agent的progress_callback
        
        返回一个async回调函数，会收集所有执行详情
        """
        async def progress_callback(event: Dict[str, Any]):
            await self._handle_progress_event(event)
        
        return progress_callback

    def handle_progress_event(self, event: Dict[str, Any]):
        """处理进度事件（同步版本）"""
        event_type = event.get("type", "")
        
        if event_type == "tool_start":
            # 工具开始执行
            self._current_tool = ToolExecution(
                tool_name=event.get("toolName", ""),
                tool_args=event.get("toolArgs", {}),
                start_time=time.time()
            )
            self.execution_details.tool_executions.append(self._current_tool)
            
        elif event_type == "tool_result":
            # 工具执行完成
            tool_name = event.get("toolName", "")
            result = event.get("result", {})
            success = event.get("success", True)
            error = result.get("error") if isinstance(result, dict) else None
            
            # 找到对应的tool execution并更新
            if self._current_tool and self._current_tool.tool_name == tool_name:
                self._current_tool.complete(result, success, error)
                self._current_tool = None
                
        elif event_type == "thinking":
            # AI思考中（可以记录）
            pass
            
        elif event_type == "progress":
            # 进度消息
            pass
            
        elif event_type == "response":
            # AI回复片段
            data = event.get("data", "")
            if data:
                self.assistant_message += data

    async def _handle_progress_event(self, event: Dict[str, Any]):
        """处理进度事件（异步版本，兼容async回调）"""
        self.handle_progress_event(event)

    def add_llm_usage(self, usage: Dict[str, int]):
        """添加LLM token使用量"""
        if usage:
            self.prompt_tokens += usage.get("prompt_tokens", 0)
            self.completion_tokens += usage.get("completion_tokens", 0)
            self.total_token_count += usage.get("total_tokens", 0)

    def set_model(self, model: str):
        """设置使用的模型"""
        self.model = model

    def increment_iterations(self):
        """增加迭代计数"""
        self.execution_details.total_iterations += 1

    def add_subagent_call(self, subagent_name: str, task_description: str, 
                         result: Dict[str, Any], success: bool):
        """记录子智能体调用"""
        self.execution_details.subagent_calls.append({
            "subagent_name": subagent_name,
            "task_description": task_description[:200] if task_description else "",
            "success": success,
            "result_summary": str(result)[:200] if result else ""
        })

    def set_plan_info(self, plan_id: str, steps: List[Dict[str, Any]]):
        """设置计划信息"""
        self.execution_details.plan_id = plan_id
        self.execution_details.plan_steps = steps

    def mark_error(self, error_message: str):
        """标记错误"""
        self.status = "failed"
        self.error_message = error_message

    def complete(self, assistant_message: str = None):
        """完成记录"""
        self.end_time = time.time()
        if assistant_message:
            self.assistant_message = assistant_message

    def get_duration_ms(self) -> int:
        """获取执行时长（毫秒）"""
        if self.end_time:
            return int((self.end_time - self.start_time) * 1000)
        return int((time.time() - self.start_time) * 1000)

    def save(self) -> Optional[Dict[str, Any]]:
        """
        保存会话记录到数据库
        
        Returns:
            保存的记录，如果失败返回None
        """
        try:
            # 确保结束时间
            if self.end_time is None:
                self.end_time = time.time()
            
            record = ChatRecordDB.create(
                session_id=self.session_id,
                user_id=self.user_id,
                user_message=self.user_message,
                assistant_message=self.assistant_message,
                total_token_count=self.total_token_count,
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                model=self.model,
                execution_details=self.execution_details.to_dict(),
                status=self.status,
                error_message=self.error_message,
                duration_ms=self.get_duration_ms()
            )
            
            if record:
                logger.info(
                    f"Session record saved: record_id={record['record_id']}, "
                    f"session_id={self.session_id}, tokens={self.total_token_count}, "
                    f"duration={self.get_duration_ms()}ms"
                )
            
            return record
            
        except Exception as e:
            logger.error(f"Failed to save session record: {e}")
            return None


class SessionRecordManager:
    """
    会话记录管理器

    管理当前请求的生命周期内的记录服务实例
    """

    _local = threading.local()

    @classmethod
    def start_record(
        cls,
        session_id: str,
        user_id: str,
        user_message: str
    ) -> SessionRecordService:
        """开始一条新的记录"""
        cls._local.record_service = SessionRecordService(
            session_id=session_id,
            user_id=user_id,
            user_message=user_message
        )
        return cls._local.record_service

    @classmethod
    def get_current_record(cls) -> Optional[SessionRecordService]:
        """获取当前记录服务"""
        return getattr(cls._local, 'record_service', None)

    @classmethod
    def end_record(cls) -> Optional[Dict[str, Any]]:
        """结束当前记录并保存"""
        if hasattr(cls._local, 'record_service') and cls._local.record_service:
            record = cls._local.record_service.save()
            cls._local.record_service = None
            return record
        return None