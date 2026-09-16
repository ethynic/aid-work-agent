#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lead_refresh recap 适配器：留资线索动态刷新（意向度 / 客户需求分条）

由 recap runner 调度（任务级幂等已由 runner 完成）。拉模式：触发入口
（智能体轮次 recap / 人工期客户消息入队）只传会话定位信息，执行时刻
自行采集 channel_messages 快照做 LLM 分析并回写 bs_lead_capture_leads，
防抖跳过的消息不丢失（下一条消息再触发时一并覆盖）。

确定性数据（人工服务归属）不在本适配器——由 transfer_to_human 工具
转接成功时事件驱动即时回写（update_transfer_info），与本章字段不重叠。

设计文档：docs/subagent/pre-sales/lead-capture-refresh-design.md
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

from loguru import logger

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client
from src.core.temp_logger import tlog
from src.services.recap.runner import RecapPayload
from src.saas.models.enums import LeadIntentLevel

# 冷却防抖：同一线索 5 分钟内至多分析一次（成本控制核心，跳过不丢数据）
LEAD_REFRESH_COOLDOWN_SECONDS = 300

# 采集消息条数窗口（近 50 条，按时间正序）
_COLLECT_MESSAGE_LIMIT = 50

# 单条消息内容截断（防超长消息撑爆输入）
_MESSAGE_TRUNCATE_CHARS = 300

_TOPIC = "lead_refresh"

# 意向度判定标准（一期固定通用标准，租户级自定义见设计文档开放问题 §9.4）
_ANALYSIS_SYSTEM_PROMPT = (
    "你是售前咨询系统的客户意向度分析师。根据客户线索信息与近期对话记录，"
    "输出 JSON（不要输出任何其他内容）：\n"
    '{"intent_level": "high|medium|low",\n'
    ' "intent_reason": "判定依据，一句话，供运营人员理解",\n'
    ' "demand_points": ["客户需求分条概括，每条一句话，客户无明确需求时为空数组"]}\n'
    "\n"
    "意向度判定标准：\n"
    "- high：出现明确购买信号（主动要报价、询问交付周期、约定下一步动作）\n"
    "- medium：持续追问产品细节、对比方案，但未出现明确购买信号\n"
    "- low：仅寒暄、信息收集，或已明确表示拒绝/暂无需求\n"
    "\n"
    "demand_points 要求：客户有多方面需求时分条概括（如产品咨询、价格、"
    "售后各自一条）；合并历史需求条目与最新对话中的新需求，去重后输出完整列表；"
    "最多 8 条，每条不超过 40 字。\n"
    "对话中带「[人工接待]」「[人工客服]」标记的是转人工阶段的客户与员工发言，"
    "同样计入分析，但注意区分客户本人的意向表达。\n"
    "只依据线索信息与对话内容判定，不编造信息。"
)

# 人工期客户消息来源 -> 阶段标记（人工期客户消息 metadata.source，F8）
_HUMAN_CUSTOMER_SOURCES = ("customer_human", "customer_ended")


def _extract_json_object(content: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中提取 JSON object（容忍 ```json 包裹与前后杂文本）"""
    if not content:
        return None
    text = content.strip()
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _resolve_lite_model_name() -> Optional[str]:
    """解析 lite 模型名用于计费单价归属（provider 响应不含 model 键，
    缺 model 会误用主模型单价，见 session_record.py P2-1 修复先例）"""
    try:
        from src.config.settings import settings

        return settings.llm.get_lite_target()[1] or None
    except Exception:
        return None


def _format_message(msg: Dict[str, Any]) -> Optional[str]:
    """单条消息转分析输入行；非 user/assistant 角色返回 None"""
    role = (msg.get("role") or "").strip()
    content = (msg.get("content") or "").strip()
    if not content:
        return None
    if role == "assistant":
        return f"AI：{content[:_MESSAGE_TRUNCATE_CHARS]}"
    if role != "user":
        return None
    metadata = msg.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    source = (metadata.get("source") or "").strip()
    if source == "servicer":
        # 员工消息落库时已带 [人工客服] 前缀，直接透传
        return f"{content[:_MESSAGE_TRUNCATE_CHARS]}"
    if source in _HUMAN_CUSTOMER_SOURCES:
        return f"[人工接待] 客户：{content[:_MESSAGE_TRUNCATE_CHARS]}"
    return f"客户：{content[:_MESSAGE_TRUNCATE_CHARS]}"


class LeadRefreshAdapter:
    """recap 任务适配器：lead_refresh（留资线索意向度/需求动态刷新）"""

    name = "lead_refresh"

    @staticmethod
    async def execute(payload: RecapPayload) -> None:
        from src.channels.session import channel_session_manager
        from src.saas.db.lead_capture_db import LeadCaptureDB

        # 1. 定位线索：channel_sessions.metadata.lead_capture 是「会话 -> 线索」关联键（F3）。
        #    未留资的会话静默 no-op（该任务对所有轮次安全）
        session = await asyncio.to_thread(
            channel_session_manager.get_session_by_id, payload.session_id
        ) or {}
        lead_capture = (session.get("metadata") or {}).get("lead_capture") or {}
        lead_id = (lead_capture.get("lead_id") or "").strip()
        if not lead_id:
            tlog(_TOPIC, "no-op: 会话未留资, session={sid}", sid=payload.session_id)
            return

        # 2. 线索不存在（已被「新会话」命令删除，#63）则 no-op
        lead = await asyncio.to_thread(LeadCaptureDB.get_by_id, lead_id, payload.tenant_id)
        if not lead:
            tlog(_TOPIC, "no-op: 线索不存在或已删除, lead_id={lid}", lid=lead_id)
            return

        # 3. 冷却占坑（防抖）：占坑失败说明 5 分钟内已分析过，本次跳过。
        #    跳过不丢数据——分析读执行时刻的消息快照，被跳过期间的消息由
        #    下一次触发（冷却到期后客户任一消息再触发）一并覆盖
        cooldown_key = redis_client.make_key(
            CacheKeys.LEAD_REFRESH_COOLDOWN, f"{payload.tenant_id}:{lead_id}"
        )
        if not redis_client.acquire_lock(cooldown_key, "1", ex=LEAD_REFRESH_COOLDOWN_SECONDS):
            tlog(
                _TOPIC,
                "cooldown skip: lead_id={lid}, round={rid}",
                lid=lead_id,
                rid=payload.round_message_id,
            )
            return

        try:
            # 4. 采集消息：执行时刻的 channel_messages 快照（近 50 条，时间正序）
            raw_messages = await asyncio.to_thread(
                channel_session_manager.get_messages,
                payload.session_id,
                _COLLECT_MESSAGE_LIMIT,
            )
            lines: List[str] = []
            last_message_id: Optional[str] = None
            for msg in raw_messages:
                line = _format_message(msg)
                if line:
                    lines.append(line)
                    last_message_id = msg.get("message_id") or last_message_id
            if not lines:
                tlog(_TOPIC, "no-op: 无可分析消息, lead_id={lid}", lid=lead_id)
                return
            transcript = "\n".join(lines)

            old_demand_points = lead.get("demand_points")
            if isinstance(old_demand_points, str):
                try:
                    old_demand_points = json.loads(old_demand_points)
                except (TypeError, json.JSONDecodeError):
                    old_demand_points = None

            # 5. 构造分析输入：线索既有信息 + 消息序列 + 上次需求分条（供合并去重）
            user_content = (
                "【线索既有信息】\n"
                f"客户姓名：{lead.get('contact_name') or '未知'}\n"
                f"留资时需求摘要：{lead.get('demand_summary') or '（无）'}\n"
                f"线索阶段：{lead.get('stage') or 'new'}\n"
                f"上次需求分条：{json.dumps(old_demand_points, ensure_ascii=False) if old_demand_points else '（无）'}\n"
                "\n"
                "【近期对话记录（时间正序，最多 50 条）】\n"
                f"{transcript}"
            )

            # 6. chat_lite 分析（轻量模型足够；主链路异常时 runner 吞掉，下一条消息重试）
            from src.llm.gateway import llm_gateway
            from src.services.session_record import record_background_llm_usage

            response = await llm_gateway.chat_lite(
                messages=[
                    {"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.2,
                max_tokens=1024,
            )
            # 计费：billing_audit.md §4.1 条件 A（background 进程无 record 上下文，
            # record_background_llm_usage 自动降级独立落 chat_records）
            record_background_llm_usage(
                response.get("usage") if isinstance(response, dict) else None,
                tenant_id=payload.tenant_id,
                user_id=payload.user_id,
                source=f"lead_refresh_{payload.subagent_name or 'unknown'}",
                user_message="[recap lead_refresh] 线索意向度分析",
                model=_resolve_lite_model_name(),
            )

            # 7. 解析与校验：失败/越界保留旧值，删占坑键待下一条消息重试
            data = _extract_json_object(response.get("content", ""))
            if not data:
                redis_client.delete(cooldown_key)
                tlog(_TOPIC, "分析输出解析失败，保留旧值: lead_id={lid}", lid=lead_id)
                return
            intent_level = str(data.get("intent_level") or "").strip().lower()
            if intent_level not in LeadIntentLevel.all_values():
                redis_client.delete(cooldown_key)
                tlog(
                    _TOPIC,
                    "intent_level 非法，保留旧值: lead_id={lid}, value={val}",
                    lid=lead_id,
                    val=intent_level,
                )
                return
            intent_reason = str(data.get("intent_reason") or "").strip() or None
            demand_points_raw = data.get("demand_points")
            if demand_points_raw is None and "demand_points" not in data:
                # 合法 JSON 但缺 demand_points 字段：沿用旧分条，避免覆盖为 NULL
                demand_points = old_demand_points if isinstance(old_demand_points, list) else None
            elif isinstance(demand_points_raw, list):
                demand_points = [
                    str(item).strip()[:80] for item in demand_points_raw if str(item).strip()
                ][:8]
            else:
                # 字段存在但类型非法：视为解析失败，保留旧值待重试
                redis_client.delete(cooldown_key)
                tlog(
                    _TOPIC,
                    "demand_points 类型非法，保留旧值: lead_id={lid}",
                    lid=lead_id,
                )
                return

            # 8. 回写线索行（同步 DB 调用经 to_thread 包裹，不阻塞事件循环）
            updated = await asyncio.to_thread(
                LeadCaptureDB.update_analysis,
                lead_id,
                payload.tenant_id,
                intent_level,
                intent_reason,
                demand_points,
                last_message_id,
            )

            # 9. 前后对比留痕（意向度变化、需求条数变化）
            old_points = old_demand_points if isinstance(old_demand_points, list) else []
            tlog(
                _TOPIC,
                "分析回写{ok}: lead_id={lid}, intent {old_i} -> {new_i}, "
                "需求条数 {old_n} -> {new_n}, last_message_id={mid}",
                ok="ok" if updated else "failed(线索已删除)",
                lid=lead_id,
                old_i=lead.get("intent_level") or "无",
                new_i=intent_level,
                old_n=len(old_points),
                new_n=len(demand_points or []),
                mid=last_message_id,
            )
        except Exception as e:
            # LLM 调用或回写失败：删占坑键，允许下一条消息重试
            try:
                redis_client.delete(cooldown_key)
            except Exception:
                logger.debug("[lead_refresh] 冷却键删除失败（不影响重试语义）")
            tlog(_TOPIC, "分析失败，冷却键已删除待重试: lead_id={lid}, err={err}", lid=lead_id, err=e)
            raise
