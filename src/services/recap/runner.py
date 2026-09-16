#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Recap 运行时 runner

职责：读取 agent.subagent_config.recap 配置 -> 校验系统级开关 -> Redis SET NX 幂等 ->
分发到 tasks 注册表中的适配器 -> 单任务故障隔离（异常吞掉不上抛，不影响对话主流程）。
过程留痕走 tlog("recap", ...)，主日志每轮至多一条 INFO 汇总。
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client
from src.core.temp_logger import tlog

# 幂等键 TTL：recap_task:{tenant_id}:{task_name}:{round_message_id}
RECAP_IDEMPOTENT_TTL = 86400  # 24h

# 队列消息最大滞留时长：超过后消费侧丢弃。滞留超过幂等键 TTL（24h）的陈旧任务
# 会绕过 SET NX 防重，且用过期对话内容推送跟进对客户无意义
RECAP_MAX_QUEUE_AGE_SECONDS = 3600  # 1h

# 触发时机白名单：当前仅支持 every_round
VALID_WHEN = ("every_round",)


@dataclass
class RecapTaskConfig:
    """SUBAGENT.md recap.tasks 单任务配置"""
    name: str
    when: str = "every_round"
    enabled: bool = True


@dataclass
class RecapPayload:
    """传给适配器的本轮上下文（runner 构造，不查 DB——DB 采集是适配器自己的事）

    user_id/trace_id 由 trigger_recap 从 record_service 提取，供 background 进程
    执行时使用（background 进程拿不到 record_service 对象）。
    """
    tenant_id: str
    session_id: str
    subagent_name: str
    round_message_id: Any
    user_content: str
    assistant_reply: str
    task_config: Optional[List[Dict[str, Any]]] = None
    record_service: Any = None
    user_id: Optional[str] = None
    trace_id: Optional[str] = None
    enqueued_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可 JSON 化的 dict（不含 record_service 对象）"""
        return {
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "subagent_name": self.subagent_name,
            "round_message_id": self.round_message_id,
            "user_content": self.user_content,
            "assistant_reply": self.assistant_reply,
            "task_config": self.task_config,
            "user_id": self.user_id,
            "trace_id": self.trace_id,
            "enqueued_at": self.enqueued_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecapPayload":
        """从 JSON dict 重建（background 进程消费队列时使用）"""
        return cls(
            tenant_id=data.get("tenant_id") or "",
            session_id=data.get("session_id") or "",
            subagent_name=data.get("subagent_name") or "",
            round_message_id=data.get("round_message_id"),
            user_content=data.get("user_content") or "",
            assistant_reply=data.get("assistant_reply") or "",
            task_config=data.get("task_config"),
            record_service=None,
            user_id=data.get("user_id"),
            trace_id=data.get("trace_id"),
            enqueued_at=data.get("enqueued_at"),
        )


def parse_recap_tasks(recap_config: Optional[Dict[str, Any]]) -> List[RecapTaskConfig]:
    """解析 SUBAGENT.md recap 块为任务配置列表

    规则（设计文档 §3）：
    - 无 recap 块 / tasks 为空 -> 返回空列表（行为与现状一致）
    - 未知 name 不在此校验（分发时由注册表跳过 + warning）
    - when 不在白名单 / enabled=false 的任务过滤掉
    """
    if not recap_config or not isinstance(recap_config, dict):
        return []
    raw_tasks = recap_config.get("tasks")
    if not raw_tasks or not isinstance(raw_tasks, list):
        return []

    tasks: List[RecapTaskConfig] = []
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            logger.warning("[recap] recap.tasks 存在缺少 name 的任务项，已跳过")
            continue
        when = str(raw.get("when") or "every_round").strip()
        if when not in VALID_WHEN:
            logger.warning(f"[recap] 任务 {name} 的 when={when} 不支持（仅 {VALID_WHEN}），已跳过")
            continue
        enabled = raw.get("enabled", True)
        if not enabled:
            continue
        tasks.append(RecapTaskConfig(name=name, when=when, enabled=True))
    return tasks


def _system_switch_enabled(task_name: str) -> bool:
    """系统级开关（config.yaml），任一任务的总闸映射在此维护

    external_push -> settings.external_push.pre_sales.enabled（kill switch，回滚手段）
    """
    if task_name == "external_push":
        try:
            from src.config.settings import settings
            return bool(settings.external_push.pre_sales.enabled)
        except Exception as e:
            logger.warning(f"[recap] 读取 external_push 系统开关失败，默认放行: {e}")
            return True
    return True


def _subagent_name_from_session(session_id: str) -> str:
    """session_id 末段即 subagent_id（tenant_{tid}_{channel}_{user}_{subagent} 格式）"""
    if not session_id:
        return ""
    return session_id.rsplit("_", 1)[-1]


def trigger_recap(
    agent: Any,
    session_id: str,
    tenant_id: str,
    user_content: str,
    assistant_reply: str,
    round_message_id: Any,
    record_service: Any = None,
) -> None:
    """轮后 recap 触发入口（同步函数，内部 create_task 异步执行，不阻塞回复链路）

    由 ChannelSessionManager.process_and_persist 在 send_ok=True 时调用（设计文档 §4.3）。
    任何异常都不允许上抛——recap 是沉淀任务，失败不能影响对话。
    """
    try:
        subagent_config = getattr(agent, "subagent_config", None)
        recap_config = getattr(subagent_config, "recap", None) if subagent_config else None
        tasks = parse_recap_tasks(recap_config)
        if not tasks:
            return
        # 幂等键依赖本轮落库的 user message_id；缺失（极端写库失败场景）时无法防重，放弃执行
        if not round_message_id:
            logger.warning(
                f"[recap] round_message_id 缺失，放弃本轮 recap 任务 session={session_id}"
            )
            return

        subagent_name = ""
        if subagent_config is not None:
            subagent_name = getattr(subagent_config, "name", "") or getattr(subagent_config, "dir_name", "")
        if not subagent_name:
            subagent_name = _subagent_name_from_session(session_id)

        # 从 record_service 提取归属用户与当轮 trace_id（background 进程执行时
        # 拿不到 record_service，必须在触发时捕获；trace_id 供 recap 追加 span）
        record_user_id = getattr(record_service, "user_id", None) if record_service else None
        trace_collector = getattr(record_service, "trace_collector", None) if record_service else None
        trace_id = getattr(trace_collector, "trace_id", None) if trace_collector else None

        payload = RecapPayload(
            tenant_id=tenant_id,
            session_id=session_id,
            subagent_name=subagent_name,
            round_message_id=round_message_id,
            user_content=user_content or "",
            assistant_reply=assistant_reply or "",
            record_service=record_service,
            user_id=record_user_id,
            trace_id=trace_id,
        )

        # 任务列表序列化进 payload，background 进程消费时无需再解析 agent 配置
        payload.task_config = [
            {"name": t.name, "when": t.when, "enabled": t.enabled} for t in tasks
        ]
        payload.enqueued_at = time.time()

        # 优先入队到 background runner 进程执行（与 HTTP worker 重启解耦，
        # 2026-09-08 事故：worker max_requests 自重启杀掉进行中的推送）；
        # Redis 不可用时降级为进程内 asyncio 任务（保持功能可用）。
        # 直接传 dict（rpush 内部统一 json.dumps，避免双重编码）
        enqueued = False
        try:
            enqueued = redis_client.rpush(
                redis_client.make_key(CacheKeys.RECAP_QUEUE),
                payload.to_dict(),
            )
        except Exception as e:
            logger.warning(f"[recap] 入队异常（将降级进程内执行）: {e}")

        if enqueued:
            return

        # 自持引用 + done_callback 丢弃，防止任务被 GC（仿 channel_routes._dingtalk_background_tasks）
        bg_task = asyncio.create_task(_run_tasks(tasks, payload))
        _background_tasks.add(bg_task)
        bg_task.add_done_callback(_background_tasks.discard)
    except Exception as e:
        logger.opt(exception=True).error(f"[recap] 触发入口异常（不影响对话）: {e}")


# 后台任务自持引用集（仅防 GC，无其他语义）
_background_tasks: set = set()


def enqueue_lead_refresh(tenant_id: str, session_id: str, round_message_id: Any) -> None:
    """人工期消息落库后的线索刷新入队（轻量，不依赖 agent 实例）

    人工期（转人工后）无智能体轮次，trigger_recap 不会被调用——入口 B 在
    channel_routes._persist_kf_context_customer_message 落库成功后调用本函数，
    构造最小 RecapPayload rpush 到 RECAP_QUEUE，由 background_runner 消费。
    lead_refresh 是拉模式（执行时自采 DB），user_content / assistant_reply 留空。

    任何异常吞掉不阻断消息链路；Redis 不可用时直接放弃（不降级进程内执行——
    消息处理协程在 API worker 中，进程内执行会把 LLM 分析拖回 worker 生命周期，
    且下一条客户消息会再次触发，无需补偿）。
    """
    try:
        from src.core.temp_logger import tlog as _tlog

        # round_message_id（企微回调 msgid）仅作幂等键成分；缺失时幂等键退化为
        # 固定串，首条 SET NX 占坑 24h 会把该租户所有线索的入口 B 消息全部
        # dedup 掉（功能静默停摆），必须与 trigger_recap 同款守卫放弃执行
        if not round_message_id:
            logger.warning(
                f"[recap] enqueue_lead_refresh round_message_id 缺失，放弃入队 session={session_id}"
            )
            return

        payload = RecapPayload(
            tenant_id=tenant_id,
            session_id=session_id,
            # session_id 末段即 subagent_id（与 trigger_recap 的回退逻辑一致）
            subagent_name=_subagent_name_from_session(session_id),
            round_message_id=round_message_id,
            user_content="",
            assistant_reply="",
            # 人工期消息无系统用户上下文，user_id 不设置——计费归属仅到租户
            # （chat_records.user_id 为空，与 external_push 请求期触发不同）
            task_config=[{"name": "lead_refresh", "when": "every_round", "enabled": True}],
            enqueued_at=time.time(),
        )
        enqueued = redis_client.rpush(
            redis_client.make_key(CacheKeys.RECAP_QUEUE),
            payload.to_dict(),
        )
        _tlog(
            "lead_refresh",
            "入口B入队{res}: tenant={tid}, session={sid}, round={rid}",
            res="ok" if enqueued else "failed(redis不可用)",
            tid=tenant_id,
            sid=session_id,
            rid=round_message_id,
        )
    except Exception as e:
        logger.warning(f"[recap] lead_refresh 入队异常（不影响消息链路）: {e}")


def rebuild_tasks(task_config: Optional[List[Dict[str, Any]]]) -> List[RecapTaskConfig]:
    """从序列化的 task_config 重建任务配置列表（background 进程消费队列时使用）"""
    if not task_config or not isinstance(task_config, list):
        return []
    tasks: List[RecapTaskConfig] = []
    for raw in task_config:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        when = str(raw.get("when") or "every_round").strip()
        if when not in VALID_WHEN:
            logger.warning(f"[recap] 重建任务 {name} 的 when={when} 不支持（仅 {VALID_WHEN}），已跳过")
            continue
        tasks.append(RecapTaskConfig(
            name=name,
            when=when,
            enabled=bool(raw.get("enabled", True)),
        ))
    return tasks


async def _run_tasks(tasks: List[RecapTaskConfig], payload: RecapPayload) -> None:
    """串行分发执行，任务间故障隔离"""
    from src.services.recap.tasks import RECAP_TASK_ADAPTERS

    # 清空任务内拷贝的 SessionRecord ContextVar：trigger_recap 由请求协程
    # create_task 派生，ContextVar 指向的主对话 record 此时已 save 落库，
    # 后续 record_background_llm_usage 走 add_llm_usage 累加只会丢失（计费缺口）。
    # 清空后走独立落库分支（source_type=background_llm）。仅影响本任务拷贝的
    # 上下文，不影响调用方请求协程。
    try:
        from src.services.session_record import SessionRecordManager

        SessionRecordManager.set_current_record(None)
    except Exception:
        logger.debug("recap 清空 SessionRecord ContextVar 失败（不影响任务执行）")

    results: List[str] = []
    for task in tasks:
        # 1) 系统级开关
        if not _system_switch_enabled(task.name):
            results.append(f"{task.name}=skipped(disabled)")
            continue

        # 2) 幂等：Redis SET NX，同轮重入/回调重放直接跳过
        dedup_key = redis_client.make_key(
            CacheKeys.RECAP_TASK_DEDUP,
            f"{payload.tenant_id}:{task.name}:{payload.round_message_id}",
        )
        if not redis_client.acquire_lock(dedup_key, "1", ex=RECAP_IDEMPOTENT_TTL):
            results.append(f"{task.name}=skipped(dup)")
            continue

        # 3) 分发
        adapter = RECAP_TASK_ADAPTERS.get(task.name)
        if adapter is None:
            logger.warning(f"[recap] 未知 recap 任务名 {task.name}（未注册适配器），已跳过")
            results.append(f"{task.name}=skipped(unknown)")
            continue

        try:
            tlog("recap", f"task={task.name} start, tenant={payload.tenant_id}, round={payload.round_message_id}")
            await adapter.execute(payload)
            tlog("recap", f"task={task.name} done, round={payload.round_message_id}")
            results.append(f"{task.name}=ok")
        except Exception as e:
            # 不重试、不上抛——下一轮问答自然产生新的 recap
            logger.opt(exception=True).error(f"[recap] task={task.name} 失败（不影响对话）: {e}")
            results.append(f"{task.name}=failed")

    logger.info(f"[recap] 会话 {payload.session_id} 轮后任务完成: {', '.join(results)}")
