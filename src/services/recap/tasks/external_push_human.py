#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
external_push_human recap 适配器：人工接待期对话推送第三方系统（#64 Phase 2 §9.5）

转人工后员工接待期间的客户与员工对话只落 channel_messages，智能体轮次的
external_push 不覆盖该阶段——本适配器由入口 B（人工期客户消息落库后入队）触发，
把人工期对话以「微信咨询（人工）」跟进记录推送到租户外部客户管理系统，
并同步客户表转人工字段（zhuanrengongshijian / rengongkefuxingming，多次转人工
记录最新）与跟进汇总摘要（genjinhuizongzhaiyao / zhuangtai，承接 §9.1 AI 职责）。

复用 external_push 的推送架构（租户文档驱动 -> api-meta 解析 -> 委托登录 ->
LLM 工具循环），差异点：对话输入为人工期消息窗口（lead_refresh 同款格式化）、
system prompt 追加人工期特例段、冷却按 session 维度（不要求留资）。

no-op 条件：非 wecom_kf 会话 / 租户文档缺失（未对接外部系统）/ api-meta 解析
失败 / AGENT_TOKEN 或归属员工手机号缺失 / 窗口内无人工期消息。

设计文档：docs/subagent/pre-sales/lead-capture-refresh-design.md §9.5
"""

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client
from src.core.temp_logger import tlog
from src.services.recap.runner import RecapPayload
from src.services.recap.tasks.external_push import (
    _build_system_prompt,
    _collect_context,
    _delegate_login,
    _extract_json_object,
    _get_agent_token,
    _load_tenant_doc,
    parse_api_meta,
    _resolve_lite_model_name,
    _run_push_loop,
    _strip_excluded_sections,
    _trace_llm_span,
    _truncate,
)
from src.services.recap.tasks.lead_refresh import _COLLECT_MESSAGE_LIMIT, _format_message

_TOPIC = "人工期推送"

# 冷却防抖：同一会话 5 分钟内至多推送一次（成本控制核心，跳过不丢数据）
_HUMAN_COOLDOWN_SECONDS = 300

_TRACE_TRUNCATE_CHARS = 2000

# 人工期摘要 system prompt（输出固定 JSON；客户诉求=客户消息摘要、回复要点=员工回复要点）
_HUMAN_SUMMARY_SYSTEM_PROMPT = (
    "你是售前咨询系统的对话摘要器。当前是人工客服接待阶段，根据给出的近期对话记录，"
    "输出 JSON（不要输出其他内容）：\n"
    '{"customer_need": "客户诉求摘要（人工接待期客户问了什么、表达了什么意向，1~3句）",\n'
    ' "reply_summary": "人工客服回复要点（讲解/答复了什么，1~3句）",\n'
    ' "customer_name_hint": "客户在对话中明确自报的姓名/称呼，未自报则为空字符串"}\n'
    "对话中 [人工客服] 开头为员工发言，[人工接待] 客户： 开头为客户发言。"
    "只依据对话内容，不编造信息。"
)

# 追加到 external_push._build_system_prompt 之后的人工期特例段（优先于文档冲突条款；
# 字段名与「人工期推送规则」章节由租户文档声明，此处只做通用语义引导）
_HUMAN_SPECIAL_SECTION = (
    "\n"
    "==================== 人工接待期推送特例（本段优先于文档中与之冲突的条款） ====================\n"
    "本轮推送的是人工客服接待期间的客户与员工对话：\n"
    "1. 跟进动作字段（genjindongzuo 或文档中等价字段）按文档「人工期推送规则」章节执行"
    "（如「微信咨询（人工）」），文档中「微信咨询（AI）」的固定值不适用本轮。\n"
    "2. 跟进内容 neirong = 员工回复要点摘要；客户回复内容 kehuhuifuneirong = 客户消息摘要"
    "（直接使用用户消息中的预生成摘要）。\n"
    "3. 客户记录的转人工字段（转人工时间 / 转人工客服姓名，字段名见文档「人工期推送规则」）"
    "随本轮推送更新为最新值（多次转人工记录最新），数据见用户消息「转人工信息」。\n"
    "4. 跟进汇总摘要（genjinhuizongzhaiyao / zhuangtai）同步规则照常执行。\n"
    "5. 对话标记：[人工客服] 开头为员工发言，[人工接待] 客户： 开头为客户发言。\n"
    "================================================================================================\n"
)

# 人工期消息来源标记（与 lead_refresh._HUMAN_CUSTOMER_SOURCES 口径一致，另含员工消息）
_HUMAN_SIGNAL_SOURCES = ("customer_human", "customer_ended", "servicer")


def _has_human_period_message(raw_messages: List[Dict[str, Any]]) -> bool:
    """窗口内是否含人工期信号消息（客户人工期消息 / 员工消息），无则 no-op"""
    for msg in raw_messages or []:
        metadata = msg.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        if (metadata.get("source") or "").strip() in _HUMAN_SIGNAL_SOURCES:
            return True
    return False


def _format_transferred_at(value: Any) -> str:
    """转人工时间格式化为 YYYY-MM-DD HH:MM:SS（DB timestamp 可能带微秒）"""
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)[:19]


def _resolve_transfer_info(tenant_id: str, metadata: Dict[str, Any]) -> Dict[str, str]:
    """转人工信息（servicer_userid / 姓名 / 时间）：会话 metadata 优先，线索字段兜底

    会话 metadata.transferred_to / last_transferred_at 由 transfer_to_human 转接
    成功时写入；已留资会话线索表也有一份事件驱动回写（最后写赢），二者任一可用。
    员工姓名尽力而为：Redis 缓存反查优先，映射不到回退线索 servicer_name。
    """
    servicer_userid = (metadata.get("transferred_to") or "").strip()
    transferred_at = _format_transferred_at(metadata.get("last_transferred_at"))
    servicer_name = ""

    lead = None
    lead_id = ((metadata.get("lead_capture") or {}).get("lead_id") or "").strip()
    if lead_id:
        try:
            from src.saas.db.lead_capture_db import LeadCaptureDB

            lead = LeadCaptureDB.get_by_id(lead_id, tenant_id) or {}
        except Exception as e:
            logger.warning(f"[external_push_human] 读取线索转人工字段失败 lead_id={lead_id}: {e}")

    if lead:
        if not servicer_userid:
            servicer_userid = (lead.get("transferred_to") or "").strip()
        if not transferred_at:
            transferred_at = _format_transferred_at(lead.get("last_human_transfer_at"))

    if servicer_userid:
        try:
            cached = redis_client.get(
                f"{CacheKeys.WECOM_KF_SERVICER_NAME}:{tenant_id}:{servicer_userid}"
            )
            servicer_name = str(cached) if cached else ""
        except Exception:
            pass
        if not servicer_name and lead:
            servicer_name = (lead.get("servicer_name") or "").strip()

    return {
        "servicer_userid": servicer_userid,
        "servicer_name": servicer_name,
        "transferred_at": transferred_at,
    }


def _trace_summary(payload: RecapPayload, status: str, detail: str) -> None:
    """向当轮对话 trace 的 obs_traces.metadata.recap 合并任务结果摘要（人工期任务名）"""
    if not payload.trace_id:
        return
    try:
        from src.core.trace_persist import append_recap_summary

        append_recap_summary(payload.trace_id, {
            "recap": {
                "task": "external_push_human",
                "status": status,
                "detail": _truncate(str(detail), _TRACE_TRUNCATE_CHARS),
                "round": str(payload.round_message_id),
            },
        }, total_cost=round(getattr(payload, "_obs_cost", 0.0), 2))
    except Exception as e:
        logger.debug(f"[external_push_human] trace summary 写入失败: {e}")


async def _summarize_human(
    payload: RecapPayload,
    ctx: Dict[str, Any],
    transcript: str,
    topic: str = _TOPIC,
) -> Dict[str, str]:
    """LLM 摘要人工期对话窗口；失败降级为提示语（推送流程继续，由推送 LLM 自行归纳）"""
    fallback = {
        "customer_need": "（摘要生成失败，请依据对话窗口自行归纳客户诉求）",
        "reply_summary": "（摘要生成失败，请依据对话窗口自行归纳员工回复要点）",
        "customer_name_hint": "",
    }
    try:
        from src.config.settings import settings
        from src.llm.gateway import llm_gateway
        from src.services.session_record import record_background_llm_usage

        summarize_start = time.time()
        response = await llm_gateway.chat_lite(
            messages=[
                {"role": "system", "content": _HUMAN_SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": f"近期对话记录（时间正序）：\n{transcript}"},
            ],
            temperature=0.2,
            max_tokens=settings.external_push.pre_sales.summary_max_tokens,
        )
        # 计费：billing_audit.md §3.5 条件 A（独立落库）；model 显式解析 lite 模型名
        record_background_llm_usage(
            response.get("usage") if isinstance(response, dict) else None,
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            source=f"external_push_human_{ctx.get('subagent') or 'unknown'}",
            user_message="[recap external_push_human] 人工期对话摘要",
            model=_resolve_lite_model_name(),
        )
        _trace_llm_span(
            payload, "recap:external_push_human:summarize", response,
            _resolve_lite_model_name(), summarize_start,
        )
        data = _extract_json_object(response.get("content", ""))
        if not data:
            tlog(topic, "人工期摘要输出解析失败，降级为提示语")
            return fallback
        return {
            "customer_need": _truncate(str(data.get("customer_need") or "")) or fallback["customer_need"],
            "reply_summary": _truncate(str(data.get("reply_summary") or "")) or fallback["reply_summary"],
            "customer_name_hint": str(data.get("customer_name_hint") or "").strip(),
        }
    except Exception as e:
        logger.warning(f"[external_push_human] 摘要 LLM 失败，降级: {e}")
        _trace_llm_span(
            payload, "recap:external_push_human:summarize", None, None, time.time(),
            success=False, error=str(e),
        )
        return fallback


def _build_human_user_message(
    ctx: Dict[str, Any],
    summary: Dict[str, str],
    transfer: Dict[str, str],
    login: Dict[str, Any],
    meta: Dict[str, str],
    transcript: str,
) -> str:
    user_token = meta.get("user_token_name", "client_token")
    lead_phone = ctx.get("lead_phone") or "（客户未留资，留空，不得用其他号码冒充）"
    from src.tools.channel.channel_user_info import GENDER_LABELS

    gender = int(ctx.get("gender") or 0)
    gender_label = GENDER_LABELS.get(gender, "未知")
    return (
        "人工接待期对话推送数据如下，请按系统提示词与租户接口文档完成推送。\n"
        "\n"
        "【人工接待期对话窗口（时间正序，最多 50 条）】\n"
        f"{transcript}\n"
        "\n"
        "【预生成摘要（已由系统生成，可直接使用）】\n"
        f"客户诉求：{summary.get('customer_need') or ''}\n"
        f"人工客服回复要点：{summary.get('reply_summary') or ''}\n"
        f"客户自称：{summary.get('customer_name_hint') or '（未自报）'}\n"
        "\n"
        "【客户上下文】\n"
        f"external_userid（客户唯一标识，同一客户跨轮稳定）：{ctx['external_userid']}\n"
        f"微信昵称：{ctx.get('nickname') or '未知'}\n"
        f"微信头像：{ctx.get('avatar') or '（无，留空）'}\n"
        f"性别：{gender_label}（0未知/1男/2女，仅当租户文档声明性别字段时推送）\n"
        f"留资手机号：{lead_phone}\n"
        f"归属员工手机号：{ctx.get('assignee_phone') or ''}（已用于委托登录，无需再登录）\n"
        f"当前日期：{datetime.now().strftime('%Y-%m-%d')}\n"
        "\n"
        "【转人工信息】\n"
        f"转人工客服工号：{transfer.get('servicer_userid') or '（未知）'}\n"
        f"转人工客服姓名：{transfer.get('servicer_name') or '（未知）'}\n"
        f"转人工时间：{transfer.get('transferred_at') or '（未知）'}\n"
        "\n"
        "【委托登录信息】\n"
        f"{user_token}：{login.get('client_token') or ''}\n"
        f"委托人：{login.get('display_name') or ''} / {login.get('agent_name') or ''}"
    )


class ExternalPushHumanAdapter:
    """recap 任务适配器：external_push_human（人工接待期对话推送第三方系统）"""

    name = "external_push_human"

    @staticmethod
    async def execute(payload: RecapPayload) -> None:
        from src.channels.session import channel_session_manager

        # 内部为同步 DB 读（会话行/用户/线索/客服配置），to_thread 包裹不阻塞事件循环
        ctx = await asyncio.to_thread(_collect_context, payload)
        if ctx is None:
            _trace_summary(payload, "skipped", "上下文采集失败")
            return

        subagent_name = (ctx.get("subagent") or payload.subagent_name or "").strip()
        if not subagent_name:
            logger.warning(
                f"[external_push_human] 子智能体名缺失，放弃推送 session={payload.session_id}"
            )
            _trace_summary(payload, "skipped", "子智能体名缺失")
            return
        topic = f"人工期推送-{subagent_name}"
        doc_filename = f"{subagent_name}-api.md"

        doc = _load_tenant_doc(payload.tenant_id, subagent_name)
        if doc is None:
            # 非 10605 对接租户：正常 no-op（入口 B 对全部 wecom_kf 会话触发）
            tlog(topic, f"no-op: 租户未配置 {doc_filename}, tenant={payload.tenant_id}")
            _trace_summary(payload, "skipped", f"租户未配置 {doc_filename}")
            return

        meta = parse_api_meta(doc, topic)
        if meta is None:
            logger.warning(
                f"[external_push_human] 租户文档 api-meta 解析失败，放弃推送 tenant={payload.tenant_id}"
            )
            _trace_summary(payload, "skipped", "租户文档 api-meta 解析失败")
            return

        doc = _strip_excluded_sections(doc, meta, topic)

        if not ctx.get("assignee_phone"):
            logger.warning(
                f"[external_push_human] 归属员工手机号缺失，放弃推送 session={payload.session_id}"
            )
            tlog(topic, f"no-op: 归属员工手机号缺失, tenant={payload.tenant_id}, open_kfid={ctx['open_kfid']}")
            _trace_summary(payload, "skipped", "归属员工手机号缺失")
            return

        agent_token = _get_agent_token(payload.tenant_id, subagent_name)
        if not agent_token:
            logger.warning(
                f"[external_push_human] 租户未配置 AGENT_TOKEN，放弃推送 session={payload.session_id}"
            )
            tlog(topic, f"no-op: 租户未配置 AGENT_TOKEN, tenant={payload.tenant_id}")
            _trace_summary(payload, "skipped", "租户未配置 AGENT_TOKEN")
            return

        # 人工期窗口：执行时刻的 channel_messages 快照（近 50 条，时间正序），
        # 格式化与 lead_refresh 完全一致（同一对话两种沉淀的输入口径统一）
        raw_messages = await asyncio.to_thread(
            channel_session_manager.get_messages,
            payload.session_id,
            _COLLECT_MESSAGE_LIMIT,
        )
        if not _has_human_period_message(raw_messages):
            tlog(topic, "no-op: 窗口内无人工期消息, session={sid}", sid=payload.session_id)
            _trace_summary(payload, "skipped", "窗口内无人工期消息")
            return

        # 冷却占坑（防抖）：占坑失败说明 5 分钟内已推送过，本次跳过。
        # 不要求留资，按 session 维度；跳过不丢数据——下次触发一并覆盖
        cooldown_key = redis_client.make_key(
            CacheKeys.EXTERNAL_PUSH_HUMAN_COOLDOWN, f"{payload.tenant_id}:{payload.session_id}"
        )
        if not redis_client.acquire_lock(cooldown_key, "1", ex=_HUMAN_COOLDOWN_SECONDS):
            tlog(
                topic,
                "cooldown skip: session={sid}, round={rid}",
                sid=payload.session_id,
                rid=payload.round_message_id,
            )
            return

        try:
            # 对话窗口文本（与摘要、推送共用一份）
            lines = [line for line in (_format_message(msg) for msg in raw_messages) if line]
            transcript = "\n".join(lines)

            session_row = await asyncio.to_thread(
                channel_session_manager.get_session_by_id, payload.session_id
            ) or {}
            # 内部为同步 DB 读（线索兜底）+ Redis 反查，to_thread 包裹
            transfer = await asyncio.to_thread(
                _resolve_transfer_info, payload.tenant_id, session_row.get("metadata") or {}
            )

            summary = await _summarize_human(payload, ctx, transcript, topic)

            login = await asyncio.to_thread(
                _delegate_login, payload.tenant_id, ctx["assignee_phone"], agent_token, meta["login_url"],
                False, ctx.get("assignee_name"), subagent_name,
            )
            if not login or not login.get("client_token"):
                raise RuntimeError("委托登录失败（无 client_token）")

            system_prompt = _build_system_prompt(doc, meta) + _HUMAN_SPECIAL_SECTION
            user_message = _build_human_user_message(ctx, summary, transfer, login, meta, transcript)

            detail = await _run_push_loop(
                payload, ctx, summary, doc, meta, agent_token, login, topic,
                system_prompt=system_prompt,
                user_message=user_message,
                billing_source=f"external_push_human_{subagent_name or 'unknown'}",
                trace_prefix="recap:external_push_human",
            )
            _trace_summary(payload, "ok", detail)
        except Exception as e:
            # 推送失败：删冷却键，允许下一条人工期消息重试
            try:
                redis_client.delete(cooldown_key)
            except Exception:
                logger.debug("[external_push_human] 冷却键删除失败（不影响重试语义）")
            _trace_summary(payload, "failed", str(e))
            raise
