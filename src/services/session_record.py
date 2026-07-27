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
from src.services.billing import calculate_credit_cost

_RESULT_MAX_LENGTH = 2000


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
                    "result": str(te.result)[:_RESULT_MAX_LENGTH] if te.result else None,
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
    会话记录服务 - 收集并保存对话记录到 chat_records 表

    主要功能：
    - 收集用户消息和AI回复
    - 记录token消耗（用于计费和用量统计）
    - 保存执行详情（工具调用、思考过程等）
    - 记录执行性能（耗时、状态、错误信息）

    与 chat_messages 表的区别：
    - chat_records：完整对话记录，用于用量统计、计费、审计、性能监控
    - chat_messages：单条消息存储，用于前端展示和上下文构建

    两个表存在内容冗余但设计合理，服务于不同的业务目的。
    """

    def __init__(self, session_id: str, user_id: str, user_message: str,
                 tenant_id: str = None, source_type: str = "chat"):
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.user_message = user_message
        self.source_type = source_type

        # 执行详情
        self.execution_details = ExecutionDetails()

        # Token消耗
        self.total_token_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_input_tokens = 0

        # 模型信息
        self.model = None
        self.provider = None

        # Agent loop 迭代次数
        self.agent_iterations = 0

        # 子智能体调用
        self.subagent_calls = []

        # 状态
        self.start_time = time.time()
        self.end_time = None
        self.status = "completed"
        self.error_message = None

        # AI回复
        self.assistant_message = ""

        # 当前正在执行的工具
        self._current_tool: Optional[ToolExecution] = None

        # LLM调用计数
        self._llm_call_count = 0

        # 关联的 TraceCollector 引用（由 agent.process_message 在创建 TraceCollector 后注入）。
        # process_and_persist 写入 channel_messages 后通过它回填 user_message_id，
        # 用于 monitor.py 精确匹配撤回状态。类型为 Any 避免循环依赖。
        self.trace_collector: Optional[AnyType] = None

        # 跳过落库标记：渠道侧余额阻断等"无需持久化"场景置 True，
        # save() 直接返回 None，避免写入空 chat_record 噪声
        self.skip_save: bool = False

    def create_progress_callback(self):
        """创建用于传递给Agent的progress_callback"""
        async def progress_callback(event: Dict[str, Any]):
            await self._handle_progress_event(event)

        return progress_callback

    def handle_progress_event(self, event: Dict[str, Any]):
        """处理进度事件（同步版本）"""
        event_type = event.get("type", "")

        if event_type == "tool_start":
            self._current_tool = ToolExecution(
                tool_name=event.get("toolName", ""),
                tool_args=event.get("toolArgs", {}),
                start_time=time.time()
            )
            self.execution_details.tool_executions.append(self._current_tool)

        elif event_type == "tool_result":
            tool_name = event.get("toolName", "")
            result = event.get("result", {})
            success = event.get("success", True)
            error = result.get("error") if isinstance(result, dict) else None

            if self._current_tool and self._current_tool.tool_name == tool_name:
                self._current_tool.complete(result, success, error)
                self._current_tool = None

        elif event_type == "thinking":
            pass

        elif event_type == "progress":
            pass

        elif event_type == "response":
            data = event.get("data", "")
            if data:
                self.assistant_message += data

    async def _handle_progress_event(self, event: Dict[str, Any]):
        """处理进度事件（异步版本，兼容async回调）"""
        self.handle_progress_event(event)

    def add_llm_usage(self, usage: Dict[str, int]):
        """添加LLM token使用量（纯内存操作，~0.001ms）"""
        if usage:
            self.prompt_tokens += usage.get("prompt_tokens", 0)
            self.completion_tokens += usage.get("completion_tokens", 0)
            self.total_token_count += usage.get("total_tokens", 0)
            self.cached_input_tokens += usage.get("cached_tokens", 0)
            self._llm_call_count += 1

    def set_model(self, model: str):
        """设置使用的模型"""
        self.model = model

    def set_provider(self, provider: str):
        """设置LLM提供商"""
        self.provider = provider

    def increment_iterations(self):
        """增加迭代计数（纯内存操作）"""
        self.execution_details.total_iterations += 1
        self.agent_iterations += 1

    def add_subagent_call(self, subagent_name: str, task_description: str,
                         result: Dict[str, Any], success: bool,
                         token_usage: Dict[str, int] = None):
        """记录子智能体调用"""
        call_record = {
            "subagent_name": subagent_name,
            "task_description": task_description[:200] if task_description else "",
            "success": success,
            "result_summary": str(result)[:200] if result else ""
        }
        if token_usage:
            call_record["token_usage"] = token_usage
        self.execution_details.subagent_calls.append(call_record)
        self.subagent_calls.append(call_record)

    def set_plan_info(self, plan_id: str, steps: List[Dict[str, Any]]):
        """设置计划信息"""
        self.execution_details.plan_id = plan_id
        self.execution_details.plan_steps = steps

    def mark_error(self, error_message: str):
        """标记错误"""
        self.status = "failed"
        self.error_message = error_message

    def set_trace_merge_semantics(self, *, termination_reason=None, merge_role=None):
        """同步更新内存和已落库 Trace metadata，不伪造 message_id。"""
        collector = self.trace_collector
        if collector is None:
            return
        collector.set_merge_semantics(
            termination_reason=termination_reason, merge_role=merge_role
        )
        try:
            from src.core.trace_persist import update_trace_metadata
            metadata = {}
            if termination_reason:
                metadata["termination_reason"] = termination_reason
            if merge_role:
                metadata["merge_role"] = merge_role
            update_trace_metadata(collector.trace_id, metadata)
        except Exception as e:
            logger.debug(f"set_trace_merge_semantics persist failed: {e}")

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
        保存会话记录到数据库（带异常保护）

        在 agent 执行完毕后调用，失败只记日志不影响已返回的响应。
        同事务内：
        1. INSERT chat_records（含 credit_cost）
        2. UPDATE tenants.credit_balance 原子扣减（tenant_id 为空或 credit_cost=0 时跳过）
        """
        try:
            # 渠道侧余额阻断等场景跳过落库，避免写入空 chat_record 噪声
            if self.skip_save:
                logger.info(
                    f"Session record skipped (skip_save=True): "
                    f"session_id={self.session_id}, tenant_id={self.tenant_id}, "
                    f"source_type={self.source_type}"
                )
                return None

            if self.end_time is None:
                self.end_time = time.time()

            # 计算积分用量：单价缺失时 credit_cost = 0，不阻断对话
            try:
                credit_cost = calculate_credit_cost(
                    prompt_tokens=self.prompt_tokens,
                    completion_tokens=self.completion_tokens,
                    model=self.model,
                    cached_input_tokens=self.cached_input_tokens,
                )
            except Exception as billing_err:
                # 计费异常不应影响对话记录落库
                logger.error(f"计费计算失败，credit_cost 降级为 0: {billing_err}")
                credit_cost = 0.0

            record = ChatRecordDB.create(
                session_id=self.session_id,
                tenant_id=self.tenant_id,
                user_id=self.user_id,
                user_message=self.user_message,
                assistant_message=self.assistant_message,
                total_token_count=self.total_token_count,
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                cached_input_tokens=self.cached_input_tokens,
                model=self.model,
                provider=self.provider,
                execution_details=self.execution_details.to_dict(),
                agent_iterations=self.agent_iterations,
                subagent_calls=self.subagent_calls if self.subagent_calls else None,
                status=self.status,
                error_message=self.error_message,
                duration_ms=self.get_duration_ms(),
                source_type=self.source_type,
                credit_cost=credit_cost
            )

            if record:
                logger.info(
                    f"Session record saved: record_id={record['record_id']}, "
                    f"session_id={self.session_id}, tenant_id={self.tenant_id}, "
                    f"tokens(total={self.total_token_count}, "
                    f"input={self.prompt_tokens}, output={self.completion_tokens}, "
                    f"cached={self.cached_input_tokens}), "
                    f"credit_cost={credit_cost}, "
                    f"iterations={self.agent_iterations}, "
                    f"duration={self.get_duration_ms()}ms"
                )

            return record

        except Exception as e:
            logger.error(f"Failed to save session record: {e}")
            return None


class SessionRecordManager:
    """
    会话记录管理器

    管理当前请求的生命周期内的记录服务实例。
    使用 threading.local() 实现线程隔离。
    """

    _local = threading.local()

    @classmethod
    def start_record(
        cls,
        session_id: str,
        user_id: str,
        user_message: str,
        tenant_id: str = None,
        source_type: str = "chat"
    ) -> SessionRecordService:
        """开始一条新的记录"""
        cls._local.record_service = SessionRecordService(
            session_id=session_id,
            user_id=user_id,
            user_message=user_message,
            tenant_id=tenant_id,
            source_type=source_type
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
