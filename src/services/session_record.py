"""
会话记录服务

在Agent执行过程中收集并记录：
- 用户消息和AI回复
- Token消耗
- 执行详情（工具调用、思考过程等）
"""

import time
import json
import os
import contextvars
from typing import Optional, List, Dict, Any, Callable, Coroutine, Any as AnyType
from dataclasses import dataclass, field, asdict
from datetime import datetime
from loguru import logger

from src.db.models import ChatRecordDB
from src.services.billing import (
    calculate_credit_cost,
    calculate_embedding_credit_cost,
    calculate_asr_credit_cost,
    calculate_credit_cost_with_breakdown,
    calculate_llm_credit_cost_with_breakdown,
    calculate_embedding_credit_cost_with_breakdown,
    calculate_asr_credit_cost_with_breakdown,
)

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
        # 显式缓存创建 token（cache_control 首条消息触发，按输入价 125% 计费）
        self.cache_creation_input_tokens = 0

        # Embedding / ASR 用量（LLM 计费接入改造 2026-08-12）
        self.embedding_tokens = 0
        self.asr_calls = 0
        self.usage_breakdown: Dict[str, Any] = {}  # 详细分项明细 JSON

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

        # 每轮 LLM 调用 usage 快照（add_llm_usage 追加；分段计价模型 save() 时逐轮查档用）
        self._llm_usages: list = []

        # 关联的 TraceCollector 引用（由 agent.process_message 在创建 TraceCollector 后注入）。
        # process_and_persist 写入 channel_messages 后通过它回填 user_message_id，
        # 用于 monitor.py 精确匹配撤回状态。类型为 Any 避免循环依赖。
        self.trace_collector: Optional[AnyType] = None

        # 跳过落库标记：渠道侧余额阻断等"无需持久化"场景置 True，
        # save() 直接返回 None，避免写入空 chat_record 噪声
        self.skip_save: bool = False

        # 合并语义标记：merged_follower（被合并方）/ merged_owner（合并方）。
        # 由 set_trace_merge_semantics 设置，save() 写入 usage_breakdown["merge"]，
        # 供审计识别「token=0 仅含 ASR 计费」的合并跟随记录是正常现象而非计费丢失。
        self.merge_semantics: Optional[Dict[str, str]] = None

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
            self.cache_creation_input_tokens += usage.get("cache_creation_tokens", 0)
            # 每轮调用快照（分段计价模型 save() 时按单次请求输入逐轮查档；非分段模型仍走累计原逻辑）
            self._llm_usages.append({
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "cached_tokens": int(usage.get("cached_tokens", 0) or 0),
                "cache_creation_tokens": int(usage.get("cache_creation_tokens", 0) or 0),
            })
            self._llm_call_count += 1

    def add_embedding_usage(self, tokens: int, model: str = "text-embedding-v3"):
        """添加 embedding 调用的 token 用量（累加，纯内存操作）

        对话内检索（RAG query 向量化）等场景调用，累加到当前会话记录。
        离线向量化（文档上传）不调用此方法，应独立 ChatRecordDB.create。
        """
        if not tokens or tokens <= 0:
            return
        self.embedding_tokens += tokens
        breakdown = self.usage_breakdown.setdefault(
            "embedding", {"tokens": 0, "calls": 0, "model": model}
        )
        breakdown["tokens"] += tokens
        breakdown["calls"] += 1
        breakdown["model"] = model

    def add_asr_usage(self, calls: int = 1, model: str = "aliyun-nls-asr"):
        """添加 ASR 调用次数（累加，纯内存操作）

        微信语音消息转文字等场景调用，累加到当前会话记录。
        阿里云 NLS 按次计费（响应不返回音频时长）。
        """
        if not calls or calls <= 0:
            return
        self.asr_calls += calls
        breakdown = self.usage_breakdown.setdefault(
            "asr", {"calls": 0, "model": model}
        )
        breakdown["calls"] += calls
        breakdown["model"] = model

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
        """记录合并语义到内存（save() 写入 usage_breakdown）与 Trace metadata，不伪造 message_id。"""
        metadata = {}
        if termination_reason:
            metadata["termination_reason"] = termination_reason
        if merge_role:
            metadata["merge_role"] = merge_role
        # 内存标记不依赖 trace_collector：merged_follower 未走 agent 处理，
        # trace_collector 恒为 None，此前直接 return 导致合并语义从未落库
        self.merge_semantics = metadata if metadata else None

        collector = self.trace_collector
        if collector is None:
            return
        collector.set_merge_semantics(
            termination_reason=termination_reason, merge_role=merge_role
        )
        try:
            from src.core.trace_persist import update_trace_metadata
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
            chat_credit_cost = 0.0
            chat_bd: Dict[str, Any] = {}
            try:
                # 分段计价模型按每轮调用逐轮查档合并计费；非分段模型回退累计原逻辑。
                # 极端兜底：无逐轮快照（老路径）时按累计值单元素构造。
                usage_calls = self._llm_usages
                if not usage_calls and self.prompt_tokens > 0:
                    usage_calls = [{
                        "prompt_tokens": self.prompt_tokens,
                        "completion_tokens": self.completion_tokens,
                        "cached_tokens": self.cached_input_tokens,
                        "cache_creation_tokens": self.cache_creation_input_tokens,
                    }]
                chat_credit_cost, chat_bd = calculate_llm_credit_cost_with_breakdown(
                    usage_calls=usage_calls,
                    model=self.model,
                )
            except Exception as billing_err:
                # 计费异常不应影响对话记录落库
                logger.error(f"计费计算失败，credit_cost 降级为 0: {billing_err}")
                chat_credit_cost = 0.0
                chat_bd = {}

            # Embedding 积分（对话内检索 RAG 等场景）
            embedding_credit_cost = 0.0
            emb_bd: Dict[str, Any] = {}
            try:
                embedding_credit_cost, emb_bd = calculate_embedding_credit_cost_with_breakdown(
                    embedding_tokens=self.embedding_tokens,
                )
            except Exception as billing_err:
                logger.error(f"embedding 计费计算失败，降级为 0: {billing_err}")
                embedding_credit_cost = 0.0
                emb_bd = {}

            # ASR 积分（微信语音转文字等场景）
            asr_credit_cost = 0.0
            asr_bd: Dict[str, Any] = {}
            try:
                asr_credit_cost, asr_bd = calculate_asr_credit_cost_with_breakdown(asr_calls=self.asr_calls)
            except Exception as billing_err:
                logger.error(f"ASR 计费计算失败，降级为 0: {billing_err}")
                asr_credit_cost = 0.0
                asr_bd = {}

            # 合并总积分（chat + embedding + asr）
            credit_cost = round(chat_credit_cost + embedding_credit_cost + asr_credit_cost, 2)

            # 构造 usage_breakdown：补充 chat 分项单价/分项积分与各分项 credit
            usage_breakdown: Dict[str, Any] = dict(self.usage_breakdown)
            usage_breakdown.setdefault("chat", {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "cache_creation_input_tokens": self.cache_creation_input_tokens,
                "total_tokens": self.total_token_count,
                "model": self.model,
                "credit": round(chat_credit_cost, 2),
            })
            if chat_bd:
                usage_breakdown["chat"].update({
                    "non_cached_input_tokens": chat_bd.get("non_cached_input_tokens", 0),
                    "cache_creation_input_tokens": chat_bd.get("cache_creation_input_tokens", self.cache_creation_input_tokens),
                    "unit_prices": chat_bd.get("unit_prices", {}),
                    "usage_factor": chat_bd.get("usage_factor"),
                    "credits": chat_bd.get("credits", {}),
                })
                if chat_bd.get("tiered"):
                    # 分段计价模型标记，便于审计区分 tiered 记录（反向合并单价）
                    usage_breakdown["chat"]["tiered"] = True
            if self.embedding_tokens > 0 and "embedding" in usage_breakdown:
                usage_breakdown["embedding"]["credit"] = round(embedding_credit_cost, 2)
                if emb_bd:
                    usage_breakdown["embedding"].update({
                        "unit_price_per_m": emb_bd.get("unit_price_per_m"),
                        "usage_factor": emb_bd.get("usage_factor"),
                    })
            if self.asr_calls > 0 and "asr" in usage_breakdown:
                usage_breakdown["asr"]["credit"] = round(asr_credit_cost, 2)
                if asr_bd:
                    usage_breakdown["asr"].update({
                        "unit_price_per_call": asr_bd.get("unit_price_per_call"),
                        "usage_factor": asr_bd.get("usage_factor"),
                    })

            # 合并语义标记（语音合并等场景）：merged_follower 记录 token 天然为 0，
            # 仅含 ASR 等非 LLM 计费，标记便于审计/对账识别，避免误判为计费丢失
            if self.merge_semantics:
                usage_breakdown["merge"] = self.merge_semantics

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
                credit_cost=credit_cost,
                embedding_tokens=self.embedding_tokens,
                asr_calls=self.asr_calls,
                usage_breakdown=usage_breakdown if usage_breakdown else None,
            )

            if record:
                logger.info(
                    f"Session record saved: record_id={record['record_id']}, "
                    f"session_id={self.session_id}, tenant_id={self.tenant_id}, "
                    f"tokens(total={self.total_token_count}, "
                    f"input={self.prompt_tokens}, output={self.completion_tokens}, "
                    f"cached={self.cached_input_tokens}), "
                    f"embedding_tokens={self.embedding_tokens}, asr_calls={self.asr_calls}, "
                    f"credit_cost={credit_cost} (chat={chat_credit_cost}, "
                    f"embedding={embedding_credit_cost}, asr={asr_credit_cost}), "
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
    使用 contextvars.ContextVar 实现 asyncio task 级隔离。

    为什么用 ContextVar 而非 threading.local()：
    wecom_kf / web SSE 等多条消息通过 asyncio.create_task 并发处理，
    它们共享同一事件循环线程。threading.local() 是线程级单槽，并发 task
    的 start_record 会互相覆盖 record_service，导致 end_record 保存错误
    实例、真实发生的 LLM/ASR 计费丢失。ContextVar 按 asyncio task 隔离，
    同一 task 内 start→end 顺序配对；无 asyncio 的同步代码按线程存储
    （等价 threading.local），后台线程无上下文时 get() 返回 None，
    与原有语义一致（走独立落库兜底）。
    """

    _local: "contextvars.ContextVar[Optional[SessionRecordService]]" = contextvars.ContextVar(
        "session_record_manager", default=None
    )

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
        service = SessionRecordService(
            session_id=session_id,
            user_id=user_id,
            user_message=user_message,
            tenant_id=tenant_id,
            source_type=source_type
        )
        cls._local.set(service)
        return service

    @classmethod
    def get_current_record(cls) -> Optional[SessionRecordService]:
        """获取当前记录服务"""
        return cls._local.get()

    @classmethod
    def set_current_record(cls, record: Optional["SessionRecordService"]) -> "contextvars.Token":
        """把已有 record 设为当前上下文的记录服务，返回恢复用 token。

        供请求级隔离使用：如 Agent.process_message_sync 把渠道入口创建的
        record_service 挂到 ContextVar（替代共享 Agent 实例属性——并发请求
        会互相覆盖实例属性）。调用方必须在 finally 中 reset_current_record(token)
        恢复进入前值：本调用可能发生在已有 record 的请求协程内（嵌套执行），
        无条件清空会把外层请求的 record 一并清掉。
        """
        return cls._local.set(record)

    @classmethod
    def reset_current_record(cls, token: "contextvars.Token") -> None:
        """恢复 set_current_record 之前的上下文值（token 须配对使用）"""
        cls._local.reset(token)

    @classmethod
    def end_record(cls) -> Optional[Dict[str, Any]]:
        """结束当前记录并保存"""
        record = cls._local.get()
        if record is not None:
            cls._local.set(None)
            return record.save()
        return None


def record_background_llm_usage(
    usage: Optional[Dict[str, int]],
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    source: str = "background_llm",
    user_message: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """后台 LLM 调用（非主循环 chat_with_tools）的 usage 累加到当前 SessionRecordService

    用于对话内触发的后台 LLM 调用：
    - mid_term 上下文压缩（mid_term.py:913 gateway 路径）
    - 情感分析（sentiment_service.py:49）
    - 文本分类（classification_service.py:50）
    - 案件匹配（case_matching_service.py:126）
    - 内容生成工具（content_generate_tool.py:105）
    - 数据分析（analysis_agent.py:44/93）

    这些调用走 gateway.chat 但不在 agent 主循环中，原有 record_response_usage
    因无 recorder 安装是 no-op，usage 被丢弃。本函数显式把 usage 累加到当前
    SessionRecordService，确保 chat_records 的 token 统计完整。

    若当前线程无 SessionRecordService（background_runner 调度场景），降级为
    独立 ChatRecordDB.create(source_type=background_llm)，确保后台扫描类 LLM
    调用也能计入计费。background_runner 调度线程无 HTTP 上下文，
    SessionRecordManager.get_current_record() 恒为 None。

    新增 keyword-only 参数（v3.2.2 P1 修复）：
    - tenant_id/user_id：background_runner 调度场景由调用方（mid_term）显式
      传入，从 SessionMeta 解析；让计费能归属到具体租户，避免硬编码 None
    - source：计费来源标识，与 memory_summarizer 一致用于 session_id 拼接
    - user_message：用户可见消息文本（默认 "上下文压缩扫描摘要"）
    - model：实际调用模型名。非 mid_term 场景（工具路由/技能脚本用主
      gateway 默认模型）必须显式传入，否则兜底分支会误用 mid_term 摘要
      模型单价（P2-1 修复）

    对话内调用方（case_matching/classification/sentiment/analysis_agent/
    content_generate_tool）不传这些参数，默认 None 向后兼容。
    """
    if not usage:
        return
    try:
        record = SessionRecordManager.get_current_record()
        if record:
            record.add_llm_usage(usage)
            return
        # background_runner 调度场景：无 SessionRecordService，独立落库
        _persist_background_llm_record(
            usage,
            tenant_id=tenant_id,
            user_id=user_id,
            source=source,
            user_message=user_message,
            model=model,
        )
    except Exception:
        logger.opt(exception=True).debug("Failed to record background LLM usage")


def _persist_background_llm_record(
    usage: Dict[str, int],
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    source: str = "background_llm",
    user_message: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """background_runner 调度线程的后台 LLM 调用独立写入 chat_records

    source_type=background_llm，tenant_id/user_id 由调用方（mid_term）从
    SessionMeta 透传；早期版本硬编码 None 会导致计费无法归属租户。

    本函数仅在 mid_term background_scan 等无 SessionRecordService 的场景下
    作为兜底，避免后台 LLM 调用计费丢失。memory_summarizer.py /
    work_outcome_review.py 有各自独立的 `_record_background_llm_billing`，
    本函数不覆盖那些路径。

    Args:
        usage: LLM 调用返回的 token 用量 dict
        tenant_id: 租户 ID（从 SessionMeta.tenant_id 透传；对话内调用走
                   record.add_llm_usage 不会进本函数）
        user_id: 用户 ID（从 SessionMeta.user_id 透传）
        source: 计费来源标识，用于 session_id 拼接与追溯
        user_message: chat_records.user_message 字段值
        model: 实际调用模型名；未传时回退 mid_term 摘要模型（向后兼容）
    """
    try:
        from src.db.models import ChatRecordDB
        from src.services.billing import calculate_credit_cost

        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", 0) or 0)
        cached_input_tokens = int(usage.get("cached_tokens", 0) or 0)
        cache_creation_input_tokens = int(usage.get("cache_creation_tokens", 0) or 0)

        # 模型优先级：调用方显式传入 model > mid_term 摘要模型（历史兜底）
        # mid_term 走独立 provider 用 summary_llm.model；工具路由/技能脚本用
        # 主 gateway 默认模型，必须显式传 model 才能算准单价（P2-1 修复）
        llm_model = model
        if not llm_model:
            try:
                from src.config.settings import settings as _settings
                llm_model = getattr(_settings.memory.mid_term.summary_llm, "model", None) or "deepseek-chat"
            except Exception:
                llm_model = "deepseek-chat"

        credit_cost = 0.0
        chat_bd: Dict[str, Any] = {}
        try:
            credit_cost, chat_bd = calculate_credit_cost_with_breakdown(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=llm_model,
                cached_input_tokens=cached_input_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens,
            )
        except Exception as billing_err:
            logger.error(f"background_llm 计费计算失败，credit_cost 降级为 0: {billing_err}")
            credit_cost = 0.0
            chat_bd = {}

        usage_breakdown = {
            "chat": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "total_tokens": total_tokens,
                "model": llm_model,
                "credit": round(credit_cost, 2),
            }
        }
        if chat_bd:
            usage_breakdown["chat"].update({
                "non_cached_input_tokens": chat_bd.get("non_cached_input_tokens", 0),
                "unit_prices": chat_bd.get("unit_prices", {}),
                "usage_factor": chat_bd.get("usage_factor"),
                "credits": chat_bd.get("credits", {}),
            })

        # session_id 与 memory_summarizer 一致：background_llm_{source}_{user_id|unknown}
        # 便于按 source + user 维度追溯后台扫描计费记录
        session_id = f"background_llm_{source}_{user_id or 'unknown'}"
        ChatRecordDB.create(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            user_message=user_message or "上下文压缩扫描摘要",
            assistant_message=None,
            total_token_count=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_input_tokens=cached_input_tokens,
            model=llm_model,
            provider="mid_term_background_scan",
            source_type="background_llm",
            credit_cost=credit_cost,
            usage_breakdown=usage_breakdown,
            status="completed",
        )
        logger.info(
            f"background_llm (source={source}) 计费: tenant={tenant_id}, "
            f"user={user_id}, tokens={total_tokens}, credit={credit_cost}"
        )
    except Exception as e:
        logger.opt(exception=True).error(f"background_llm 计费落库失败: {e}")


def record_skill_llm_usage(
    usage: Optional[Dict[str, Any]],
    *,
    tenant_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    stage: Optional[str] = None,
    model: Optional[str] = None,
    source: str = "skill_llm",
) -> None:
    """skill 子进程内 LLM 调用的 usage 独立落库（Excel ETL M3 等计量，显式参数版）

    skill 以子进程运行（skill_executor._execute_command），无 SessionRecordService，
    技能脚本（如 excel-to-template pipeline.py）直接调用本函数把 LLM usage 写入
    chat_records 并按 calculate_credit_cost 扣积分，确保公用云按任务对账。

    与 record_background_llm_usage 的区别：后者优先累加到当前 SessionRecordService，
    本函数为子进程显式参数版——参数缺省时回退读环境变量 AID_TENANT_ID /
    AID_SESSION_ID / AID_USER_ID（由 skill_executor 从 ToolExecutionContext 注入）。

    Args:
        usage: LLM 调用返回的 token 用量 dict；空/None 直接返回
        tenant_id/session_id/user_id: 计费归属；缺省回退 AID_* 环境变量，
            user_id 仍为空时用 "unknown" 占位（chat_records.user_id 生产库 NOT NULL）
        stage: 计量阶段（excel_etl 的 extract/repair/schema），拼进 user_message
        model: 实际调用模型名（必须传准，否则单价算错）；未传时回退
            usage["model"]（excel_template_ai._default_llm return_usage 已附带），
            仍缺省兜底 deepseek-chat
        source: 计费来源标识（默认 "skill_llm"），用于 session_id 拼接与追溯

    异常只记 warning 不抛（对齐 _persist_background_llm_record 容错风格，
    计量失败不能影响技能主流程）。
    """
    if not usage:
        return
    if tenant_id is None:
        tenant_id = os.environ.get("AID_TENANT_ID") or None
    if session_id is None:
        session_id = os.environ.get("AID_SESSION_ID") or None
    if user_id is None:
        user_id = os.environ.get("AID_USER_ID") or None
    try:
        _persist_skill_llm_record(
            dict(usage),
            tenant_id=tenant_id,
            session_id=session_id,
            user_id=user_id,
            stage=stage,
            model=model,
            source=source,
        )
    except Exception as e:
        logger.opt(exception=True).warning(f"skill_llm 计量落库失败(忽略): {e}")


def _persist_skill_llm_record(
    usage: Dict[str, Any],
    *,
    tenant_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    stage: Optional[str] = None,
    model: Optional[str] = None,
    source: str = "skill_llm",
) -> None:
    """skill 子进程 LLM 计量写入 chat_records（与 _persist_background_llm_record 同款路径）

    source_type="skill_llm"（chat_records.source_type 为自由字符串无枚举约束）；
    session_id 缺省拼 "skill_llm_{source}_{user_id|unknown}" 便于按 source+user 追溯；
    stage 拼进 user_message（如 "[stage=extract] Excel ETL 抽取"）。
    """
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", 0) or 0) or (prompt_tokens + completion_tokens)
    cached_input_tokens = int(usage.get("cached_tokens", 0) or 0)
    cache_creation_input_tokens = int(usage.get("cache_creation_tokens", 0) or 0)

    # 模型优先级：调用方显式传入 model > usage 附带的 model（_default_llm return_usage
    # 产出，provider 可能是 qwen/zhipu，缺失时才兜底 deepseek-chat——单价按模型取，传错即错价）
    llm_model = model or str(usage.get("model") or "") or "deepseek-chat"

    credit_cost = 0.0
    chat_bd: Dict[str, Any] = {}
    try:
        credit_cost, chat_bd = calculate_credit_cost_with_breakdown(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=llm_model,
            cached_input_tokens=cached_input_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
        )
    except Exception as billing_err:
        logger.warning(f"skill_llm 计费计算失败，credit_cost 降级为 0: {billing_err}")
        credit_cost = 0.0
        chat_bd = {}

    usage_breakdown = {
        "chat": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "total_tokens": total_tokens,
            "model": llm_model,
            "credit": round(credit_cost, 2),
        }
    }
    if chat_bd:
        usage_breakdown["chat"].update({
            "non_cached_input_tokens": chat_bd.get("non_cached_input_tokens", 0),
            "unit_prices": chat_bd.get("unit_prices", {}),
            "usage_factor": chat_bd.get("usage_factor"),
            "credits": chat_bd.get("credits", {}),
        })

    effective_user_id = user_id or "unknown"
    effective_session_id = session_id or f"skill_llm_{source}_{effective_user_id}"
    user_message = f"[stage={stage}] Excel ETL LLM 调用" if stage else "Skill 子进程 LLM 调用"
    ChatRecordDB.create(
        session_id=effective_session_id,
        tenant_id=tenant_id,
        user_id=effective_user_id,
        user_message=user_message,
        assistant_message=None,
        total_token_count=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_input_tokens=cached_input_tokens,
        model=llm_model,
        provider="skill_subprocess",
        source_type="skill_llm",
        credit_cost=credit_cost,
        usage_breakdown=usage_breakdown,
        status="completed",
    )
    logger.info(
        f"skill_llm (source={source}, stage={stage}) 计费: tenant={tenant_id}, "
        f"user={effective_user_id}, tokens={total_tokens}, credit={credit_cost}"
    )


def _resolve_admin_user_id(user_id: Optional[str]) -> str:
    """管理后台计费落库的 user_id 兜底：为空时取 ContextVar，仍为空用 "unknown" 占位

    chat_records.user_id 在生产库为 NOT NULL，传 None 会违反约束导致计费落库失败
    （报错 "null value in column user_id ... violates not-null constraint"）。
    无认证上下文的调用方（离线脚本、无 token 后台任务）用 "unknown" 占位，
    与 session_id 中的 user_id or 'unknown' 保持一致，保证计费不丢失。
    """
    if user_id:
        return user_id
    try:
        from src.saas.context import get_current_user_id
        return get_current_user_id() or "unknown"
    except Exception:
        return "unknown"


def record_admin_llm_usage(
    response: Optional[Dict[str, Any]],
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    source_label: str = "admin_llm_ops",
    model: Optional[str] = None,
) -> None:
    """管理后台 API 路由直接调用 LLM 的计费独立落库

    管理后台 AI 增强（提示词优化、智能体描述增强、页面推荐等）无会话上下文，
    用独立 ChatRecordDB.create(source_type=admin_llm_ops) 落账，并从
    request.state 透传 tenant_id/user_id 归属租户。

    usage_breakdown.chat 写入分项单价/分项积分，与主路径格式对齐，
    便于统一 SQL 对账。失败只记日志，不影响已返回的响应。
    """
    user_id = _resolve_admin_user_id(user_id)
    if not isinstance(response, dict):
        return
    usage = response.get("usage")
    if not usage:
        return
    try:
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", 0) or 0)
        cached_input_tokens = int(usage.get("cached_tokens", 0) or 0)
        cache_creation_input_tokens = int(usage.get("cache_creation_tokens", 0) or 0)

        if not model:
            try:
                from src.llm.gateway import llm_gateway
                model = llm_gateway.get_model_name()
            except Exception:
                model = None

        credit_cost = 0.0
        chat_bd: Dict[str, Any] = {}
        try:
            credit_cost, chat_bd = calculate_credit_cost_with_breakdown(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=model,
                cached_input_tokens=cached_input_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens,
            )
        except Exception as billing_err:
            logger.error(f"管理后台 LLM 计费计算失败，credit_cost 降级为 0: {billing_err}")
            credit_cost = 0.0
            chat_bd = {}

        usage_breakdown = {
            "chat": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "total_tokens": total_tokens,
                "model": model,
                "credit": round(credit_cost, 2),
            }
        }
        if chat_bd:
            usage_breakdown["chat"].update({
                "non_cached_input_tokens": chat_bd.get("non_cached_input_tokens", 0),
                "unit_prices": chat_bd.get("unit_prices", {}),
                "usage_factor": chat_bd.get("usage_factor"),
                "credits": chat_bd.get("credits", {}),
            })

        ChatRecordDB.create(
            session_id=f"admin_llm_ops_{source_label}_{user_id or 'unknown'}_{int(time.time())}",
            tenant_id=tenant_id,
            user_id=user_id,
            user_message=f"[管理后台] {source_label}",
            assistant_message=None,
            total_token_count=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_input_tokens=cached_input_tokens,
            model=model,
            provider=None,
            source_type="admin_llm_ops",
            credit_cost=credit_cost,
            usage_breakdown=usage_breakdown,
            status="completed",
        )
        logger.info(
            f"管理后台 LLM 计费: source={source_label}, tenant={tenant_id}, "
            f"user={user_id}, tokens={total_tokens}, credit={credit_cost}"
        )
    except Exception as e:
        logger.opt(exception=True).error(f"管理后台 LLM 计费落库失败: {e}")


def record_admin_embedding_usage(
    embedding_client,
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    source_label: str = "admin_embedding_ops",
    source_type: str = "admin_embedding_ops",
) -> None:
    """管理后台/数据管理/运维脚本的 embedding 调用独立落库计费

    embedding_client 需带 last_usage_tokens（累加计数器）与 model 属性。
    无会话上下文，用独立 ChatRecordDB.create(source_type=...) 落账，
    从 request.state 或调用方透传 tenant_id/user_id 归属租户。

    usage_breakdown.embedding 写入分项单价/系数，与主路径格式对齐，
    便于统一 SQL 对账。失败只记日志，不影响已返回的响应。
    """
    user_id = _resolve_admin_user_id(user_id)
    tokens = int(getattr(embedding_client, "last_usage_tokens", 0) or 0)
    if tokens <= 0:
        return
    model = getattr(embedding_client, "model", "text-embedding-v3")
    try:
        credit_cost = 0.0
        emb_bd: Dict[str, Any] = {}
        try:
            credit_cost, emb_bd = calculate_embedding_credit_cost_with_breakdown(
                embedding_tokens=tokens,
                model=model,
            )
        except Exception as billing_err:
            logger.error(f"embedding 计费计算失败，credit_cost 降级为 0: {billing_err}")
            credit_cost = 0.0
            emb_bd = {}

        usage_breakdown = {
            "embedding": {
                "tokens": tokens,
                "model": model,
                "credit": round(credit_cost, 2),
            }
        }
        if emb_bd:
            usage_breakdown["embedding"].update(emb_bd)

        ChatRecordDB.create(
            session_id=f"{source_type}_{source_label}_{user_id or 'unknown'}_{int(time.time())}",
            tenant_id=tenant_id,
            user_id=user_id,
            user_message=f"[管理后台] {source_label}",
            assistant_message=None,
            total_token_count=0,
            prompt_tokens=0,
            completion_tokens=0,
            cached_input_tokens=0,
            model=model,
            provider=None,
            source_type=source_type,
            credit_cost=credit_cost,
            embedding_tokens=tokens,
            usage_breakdown=usage_breakdown,
            status="completed",
        )
        logger.info(
            f"管理后台 embedding 计费: source={source_label}, tenant={tenant_id}, "
            f"user={user_id}, tokens={tokens}, credit={credit_cost}"
        )
    except Exception as e:
        logger.opt(exception=True).error(f"管理后台 embedding 计费落库失败: {e}")
