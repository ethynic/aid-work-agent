#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Recap 运行时 runner

职责：读取 agent.subagent_config.recap 配置 -> 校验系统级开关 -> Redis SET NX 幂等 ->
分发到 tasks 注册表中的适配器 -> 单任务故障隔离（异常吞掉不上抛，不影响对话主流程）。
过程留痕走 tlog("recap", ...)，主日志每轮至多一条 INFO 汇总。
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client
from src.core.temp_logger import tlog

# 幂等键 TTL：recap_task:{tenant_id}:{task_name}:{round_message_id}
RECAP_IDEMPOTENT_TTL = 86400  # 24h

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
    """传给适配器的本轮上下文（runner 构造，不查 DB——DB 采集是适配器自己的事）"""
    tenant_id: str
    session_id: str
    subagent_name: str
    round_message_id: Any
    user_content: str
    assistant_reply: str
    task_config: Optional[Dict[str, Any]] = None
    record_service: Any = None


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

        payload = RecapPayload(
            tenant_id=tenant_id,
            session_id=session_id,
            subagent_name=subagent_name,
            round_message_id=round_message_id,
            user_content=user_content or "",
            assistant_reply=assistant_reply or "",
            record_service=record_service,
        )

        # 自持引用 + done_callback 丢弃，防止任务被 GC（仿 channel_routes._dingtalk_background_tasks）
        bg_task = asyncio.create_task(_run_tasks(tasks, payload))
        _background_tasks.add(bg_task)
        bg_task.add_done_callback(_background_tasks.discard)
    except Exception as e:
        logger.opt(exception=True).error(f"[recap] 触发入口异常（不影响对话）: {e}")


# 后台任务自持引用集（仅防 GC，无其他语义）
_background_tasks: set = set()


async def _run_tasks(tasks: List[RecapTaskConfig], payload: RecapPayload) -> None:
    """串行分发执行，任务间故障隔离"""
    from src.services.recap.tasks import RECAP_TASK_ADAPTERS

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
