#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
external_push recap 适配器：每轮问答后把对话数据推送到租户外部客户管理系统

由 recap runner 调度（任务级幂等已由 runner 完成）。适配器本身不硬编码任何
第三方系统的 BASE_URL / 接口路径 / 模块字段元数据——这些全部由租户接口文档
storage/tenants/{tenant_id}/templates/{subagent_name}-api.md 指定（按子智能体
严格隔离，不回退其他智能体的文档），执行流程：

上下文采集 -> 租户文档加载（缺失放弃）-> api-meta 解析 -> 前置校验 ->
LLM 摘要（计费）-> 委托登录（Redis 缓存，login_url 来自文档 api-meta 块）->
LLM 工具循环（主模型 + http_api 工具，按文档自主完成查重/建改/跟进记录）。

系统对租户接口文档的硬性要求（在 ext/10605-售前咨询接口文档.md 模板中示范）：
a) 双 Token：agent_token（智能体身份，环境变量名固定 AGENT_TOKEN）+
   用户身份 token（变量名可由文档 api-meta 的 user_token_name 声明，默认 client_token）
b) 客户表必须有一个字段承载我方 external_userid（字段名由 api-meta 的
   external_userid_field 声明，默认 unionid）

任一步失败按降级处理；LLM 循环失败上抛由 runner 吞掉，下一轮 recap 自然重试。
"""

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from pydantic import BaseModel, Field

from src.core.temp_logger import tlog
from src.services.recap.runner import RecapPayload
from src.tools.base import BaseTool

# ============== 租户接口文档与 api-meta 约定 ==============

# 用户身份 token 默认变量名 / 鉴权 Header 名 / external_userid 承载字段默认名
# （10605 惯例，租户可在 api-meta 块中覆盖）
_DEFAULT_USER_TOKEN_NAME = "client_token"
_DEFAULT_USER_TOKEN_HEADER = "Client-Authorize-Token"
_DEFAULT_EXTERNAL_USERID_FIELD = "unionid"
# 智能体身份 token（AGENT_TOKEN）的鉴权 Header 默认名，api-meta 可覆盖
_DEFAULT_AGENT_TOKEN_HEADER = "Api-Authorize-Token"

_HTTP_TIMEOUT_SECONDS = 15
_TEXT_TRUNCATE_CHARS = 200
_DIALOGUE_TRUNCATE_CHARS = 1000
_TRACE_TRUNCATE_CHARS = 2000


# ============== trace 埋点（观测旁路，best-effort，无 trace_id 时静默跳过） ==============


def _trace_llm_span(
    payload: RecapPayload,
    name: str,
    response: Any,
    model: Optional[str],
    start_ts: float,
    success: bool = True,
    error: str = "",
    messages: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """向当轮对话 trace 追加 recap LLM 调用 span（generation）"""
    if not payload.trace_id:
        return
    try:
        from src.core.trace_persist import append_recap_span

        usage = response.get("usage") or {} if isinstance(response, dict) else {}
        output = (response.get("content") or "") if isinstance(response, dict) else ""
        if not success:
            usage = {}
            output = error
        else:
            _accumulate_obs_cost(payload, usage, model)
        # 记录 LLM 输入（messages），前端「最后一次 LLM 调用上下文」取最后一个
        # generation span 的 input 展示；与主循环 llm_call span 的 input 结构对齐
        span_input = ""
        if messages:
            span_input = json.dumps({"messages": messages}, ensure_ascii=False, default=str)
        append_recap_span(
            trace_id=payload.trace_id,
            name=name,
            span_type="generation",
            input=span_input,
            output=_truncate(output, _TRACE_TRUNCATE_CHARS),
            model=model or "",
            usage=usage if isinstance(usage, dict) else {},
            start_time=start_ts,
            end_time=time.time(),
            success=success,
        )
    except Exception as e:
        logger.debug(f"[external_push] trace span 写入失败: {e}")


def _trace_tool_span(
    payload: RecapPayload,
    tool_name: str,
    args: Dict[str, Any],
    result: Any,
    start_ts: float,
    trace_prefix: str = "recap:external_push",
) -> None:
    """向当轮对话 trace 追加 recap 工具调用 span（http_api / report_push_result）"""
    if not payload.trace_id:
        return
    try:
        from src.core.trace_persist import append_recap_span

        append_recap_span(
            trace_id=payload.trace_id,
            name=f"{trace_prefix}:tool_{tool_name}",
            span_type="span",
            input=_truncate(json.dumps(args, ensure_ascii=False, default=str), _TRACE_TRUNCATE_CHARS),
            output=_truncate(json.dumps(result, ensure_ascii=False, default=str), _TRACE_TRUNCATE_CHARS),
            start_time=start_ts,
            end_time=time.time(),
            success=bool(result.get("success")) if isinstance(result, dict) else False,
        )
    except Exception as e:
        logger.debug(f"[external_push] trace span 写入失败: {e}")


def _accumulate_obs_cost(payload: RecapPayload, usage: Any, model: Optional[str]) -> None:
    """累计本次 LLM 调用的观测成本（仅 obs_traces 展示口径，非计费权威）。

    计费权威仍是 record_background_llm_usage 落 chat_records；此处折算仅用于
    任务结束时回填 obs_traces.total_cost（GREATEST 语义，不回退已有值）。
    """
    if not isinstance(usage, dict):
        return
    try:
        from src.services.billing import calculate_credit_cost

        cost = calculate_credit_cost(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            model=model,
        )
        payload._obs_cost = getattr(payload, "_obs_cost", 0.0) + cost
    except Exception as e:
        logger.debug(f"[external_push] 观测成本折算失败: {e}")


def _trace_summary(payload: RecapPayload, status: str, detail: str) -> None:
    """向当轮对话 trace 的 obs_traces.metadata.recap 合并任务结果摘要"""
    if not payload.trace_id:
        return
    try:
        from src.core.trace_persist import append_recap_summary

        append_recap_summary(payload.trace_id, {
            "recap": {
                "task": "external_push",
                "status": status,
                "detail": _truncate(str(detail), _TRACE_TRUNCATE_CHARS),
                "round": str(payload.round_message_id),
            },
        }, total_cost=round(getattr(payload, "_obs_cost", 0.0), 2))
    except Exception as e:
        logger.debug(f"[external_push] trace summary 写入失败: {e}")

# wecom_kf 会话 ID 格式：tenant_{tid}_wecom_kf_{open_kfid}_{external_userid}_{subagent}
_WECOM_KF_MARKER = "_wecom_kf_"

# 连续工具失败熔断阈值（防 LLM 死循环烧钱）
_MAX_CONSECUTIVE_FAILURES = 3

# LLM 摘要 system prompt（输出固定 JSON）
_SUMMARY_SYSTEM_PROMPT = (
    "你是售前咨询系统的对话摘要器。根据给出的本轮客户消息与AI回复，输出 JSON（不要输出其他内容）：\n"
    '{"customer_need": "客户诉求摘要（客户问了什么、表达了什么意向，1~3句）",\n'
    ' "reply_summary": "AI回复要点（讲解了什么产品/价格/方案，1~3句）",\n'
    ' "customer_name_hint": "客户在对话中明确自报的姓名/称呼，未自报则为空字符串"}\n'
    "只依据本轮对话内容，不编造信息。"
)


def parse_wecom_kf_session(session_id: str) -> Optional[Dict[str, str]]:
    """解析 wecom_kf 会话 ID，返回 open_kfid / external_userid

    格式：tenant_{tid}_wecom_kf_{open_kfid}_{external_userid}_{subagent}
    tenant_id 与 channel_type（wecom_kf）自身含下划线，external_userid 也可能含下划线，
    因此用渠道标记定位 + 从右往左解析（末段 subagent、次末段 external_userid）。
    非 wecom_kf 会话返回 None。
    """
    if not session_id:
        return None
    idx = session_id.find(_WECOM_KF_MARKER)
    if idx < 0:
        return None
    rest = session_id[idx + len(_WECOM_KF_MARKER):]
    head, _, _subagent = rest.rpartition("_")
    if not head or "_" not in head:
        return None
    open_kfid, _, external_userid = head.partition("_")
    if not open_kfid or not external_userid:
        return None
    return {"open_kfid": open_kfid, "external_userid": external_userid, "subagent": _subagent}


# ============== 租户文档加载与 api-meta 解析 ==============


def _load_tenant_doc(tenant_id: str, subagent_name: str) -> Optional[str]:
    """读取子智能体生效的接口文档全文；缺失返回 None（放弃本轮）

    租户文档优先，未上传时按技能白名单兜底通用模板
    （configs/api_doc_templates/{skill}.md，如 pre-sales-api.md），并把
    ${APP_ID} 等非凭证占位符渲染为租户环境变量实际值（凭证类保留，由
    http_api 运行时替换）。文档是 LLM 调用外部系统接口的唯一依据，不做
    任何截断（对齐 load_api_config.py 的 _no_truncate 契约）。
    """
    # subagent_name 是文档路径/模板判定的必选参数（c43c65e5 曾漏传导致 TypeError，
    # 所有租户推送全挂；此处透传，缺参在函数签名处即报错而非静默错路径）
    try:
        from src.services.tenant_api_doc import load_rendered_doc

        return load_rendered_doc(tenant_id, subagent_name)
    except Exception as e:
        logger.warning(f"[external_push] 租户文档加载失败 tenant={tenant_id}: {e}")
        return None


def parse_api_meta(doc_text: str, topic: str = "外部推送") -> Optional[Dict[str, str]]:
    """从租户文档中解析机器可读 api-meta 约定块

    格式（文档顶部，```api-meta 围栏，YAML 风格 key: value 逐行）：
        ```api-meta
        login_url: https://...
        auth_mode: delegate_login
        user_token_name: client_token
        external_userid_field: unionid
        ```

    返回 meta dict；user_token_name / external_userid_field 缺省回退 10605 惯例值。
    http_method（可选）：业务接口统一请求方式声明。声明后推送循环强制把 http_api
    调用的 method 纠正为该值（防御 lite 模型把查询类请求自作主张改成 GET 导致
    鉴权 Header 缺失，2026-09-08 Code=-99 事故）；未声明时不干预。
    user_token_header（可选）：用户身份 token 的鉴权 Header 名，缺省 Client-Authorize-Token。
    声明后推送循环把该 Header 连同 token 值强制注入每个 http_api 调用
    （防御 lite 模型漏带鉴权头，2026-09-16 erp11096 Code=-99 事故）。
    agent_token_header（可选）：智能体身份 token（AGENT_TOKEN）的鉴权 Header 名，
    缺省 Api-Authorize-Token。声明后推送循环把该 Header 连同 ${AGENT_TOKEN} 占位符
    强制注入每个 http_api 调用并清除模型误写的其他鉴权头
    （防御 lite 模型写错 Header 名导致对方系统识别不了智能体身份，
    2026-09-17 erp11095 Code=-99「请求缺少身份令牌」事故）。
    push_exclude_sections（可选）：逗号分隔章节标题，注入 LLM 前裁剪对应章节，
    见 _strip_excluded_sections。
    无块 / login_url 缺失 / login_url 非 https 均返回 None（放弃原因写入 tlog）。
    宁可解析失败放弃本轮，绝不猜测 URL。
    """
    match = re.search(r"```\s*api-meta[^\n]*\n(.*?)```", doc_text or "", re.DOTALL)
    if not match:
        tlog(topic, "api-meta 解析失败：文档缺少 api-meta 约定块")
        return None

    meta: Dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        meta[key.strip()] = value.strip().rstrip(",")

    login_url = meta.get("login_url", "")
    if not login_url:
        tlog(topic, "api-meta 解析失败：缺少 login_url")
        return None
    if not login_url.startswith("https://"):
        tlog(topic, f"api-meta 解析失败：login_url 非 https（{login_url.split('?')[0]}）")
        return None
    if "${" in login_url:
        # 通用模板渲染后仍残留占位符 = 租户环境变量未配置（如 APP_ID），放弃推送
        tlog(topic, f"api-meta 解析失败：login_url 占位符未解析（租户环境变量未配置）: {login_url}")
        return None

    meta.setdefault("user_token_name", _DEFAULT_USER_TOKEN_NAME)
    meta.setdefault("user_token_header", _DEFAULT_USER_TOKEN_HEADER)
    meta.setdefault("agent_token_header", _DEFAULT_AGENT_TOKEN_HEADER)
    meta.setdefault("external_userid_field", _DEFAULT_EXTERNAL_USERID_FIELD)
    declared_method = (meta.get("http_method") or "").strip().upper()
    if declared_method:
        meta["http_method"] = declared_method
    else:
        meta.pop("http_method", None)
    return meta


def _strip_excluded_sections(doc_text: str, meta: Dict[str, str], topic: str = "外部推送") -> str:
    """按 api-meta 的 push_exclude_sections 裁剪注入 LLM 的文档章节

    值为逗号分隔的章节标题，与 `## ` 标题行做子串匹配（兼容「2. 委托登录接口」
    这类编号前缀）。推送流程不使用登录/详情等章节（登录由代码完成、查重走列表
    接口），裁剪可显著降低每轮 LLM 输入 token 与延迟。键未配置时原样返回
    （向后兼容，不影响未声明该键的租户文档）。
    """
    raw = (meta.get("push_exclude_sections") or "").strip()
    if not raw:
        return doc_text
    excludes = [item.strip() for item in re.split(r"[,，]", raw) if item.strip()]
    if not excludes:
        return doc_text

    parts = re.split(r"\n(?=## )", doc_text)
    kept = [parts[0]]
    removed = []
    for part in parts[1:]:
        title_line = part.strip().splitlines()[0] if part.strip() else ""
        if any(item in title_line for item in excludes):
            removed.append(title_line.lstrip("#").strip())
            continue
        kept.append(part)
    if removed:
        tlog(topic, f"文档裁剪章节: {', '.join(removed)}")
    return "\n".join(kept)


# ============== 上下文采集（渠道侧，平移自原 10605 适配器） ==============


def _get_agent_token(tenant_id: str, subagent_name: Optional[str]) -> Optional[str]:
    """AGENT_TOKEN 存于租户级子智能体环境变量（subagent_env_vars 表）

    recap 是后台任务，无请求上下文（env_vars 不注入 os.environ），必须按
    tenant_id 显式查库。先按当前子智能体精确查，未配置则兜底遍历租户全部。
    """
    try:
        from src.db.subagent_env_var import SubagentEnvVarDB

        if subagent_name:
            for var in SubagentEnvVarDB.get_vars(tenant_id, subagent_name):
                if var.get("var_name") == "AGENT_TOKEN" and var.get("var_value"):
                    return var["var_value"]
        for var in SubagentEnvVarDB.get_all_vars_for_tenant(tenant_id):
            if var.get("var_name") == "AGENT_TOKEN" and var.get("var_value"):
                return var["var_value"]
    except Exception as e:
        logger.warning(f"[external_push] 读取租户 AGENT_TOKEN 失败 tenant={tenant_id}: {e}")
    return None


def _resolve_assignee(tenant_id: str, open_kfid: str) -> tuple:
    """归属员工 (手机号, 姓名)：open_kfid -> kf_account.tenant_user_id -> users 表

    与 record_lead_capture._resolve_employee_phone/_resolve_employee_name 同款查询
    （姓名取 users.nickname or username），但 kf_config 不经 ContextVar（后台任务
    无上下文），改为从租户渠道配置反查。姓名用于委托登录自动建号。
    """
    try:
        from src.saas.db.channel_config_db import ChannelConfigDB

        for cfg in ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf"):
            for kf in cfg.get("config", {}).get("kf_account", []):
                if kf.get("open_kfid") == open_kfid:
                    tenant_user_id = kf.get("tenant_user_id")
                    if not tenant_user_id:
                        return None, None
                    from src.db.models import UserDB

                    user = UserDB.get_by_id(tenant_user_id) or {}
                    return (
                        (user or {}).get("phone") or None,
                        (user or {}).get("nickname") or (user or {}).get("username") or None,
                    )
    except Exception as e:
        logger.warning(f"[external_push] 解析归属员工信息失败 tenant={tenant_id}, open_kfid={open_kfid}: {e}")
    return None, None


def _collect_context(payload: RecapPayload) -> Optional[Dict[str, Any]]:
    """采集推送上下文（全部同步 DB 读，无 LLM）。缺失关键字段返回 None（放弃本轮）

    open_kfid / external_userid / subagent 以 channel_sessions 会话行为准：
    2026-08-14（channel_chat_id 纳入 session_id 改造）之前创建的存量会话，
    session_id 是老格式 `tenant_{tid}_wecom_kf_{external_userid}_{subagent}`
    （无 open_kfid 段），但 DB 行上 channel_chat_id 有值——从 session_id 字符串
    反解析会把 external_userid 误判为 open_kfid（生产事故 2026-09-15）。
    会话行缺失时回退到 session_id 解析（仅新格式）。
    """
    from src.channels.session import channel_session_manager

    session = channel_session_manager.get_session_by_id(payload.session_id) or {}
    channel_type = (session.get("channel_type") or "").strip()
    external_userid = (session.get("channel_user_id") or "").strip()
    open_kfid = (session.get("channel_chat_id") or "").strip()
    subagent_name = (session.get("subagent_id") or "").strip()

    if not channel_type:
        parsed = parse_wecom_kf_session(payload.session_id)
        if not parsed:
            logger.warning(
                f"[external_push] 非 wecom_kf 会话，放弃推送 session={payload.session_id}"
            )
            return None
        open_kfid = parsed["open_kfid"]
        external_userid = parsed["external_userid"]
        subagent_name = parsed["subagent"]
    elif channel_type != "wecom_kf":
        logger.warning(
            f"[external_push] 非 wecom_kf 会话（channel_type={channel_type}），"
            f"放弃推送 session={payload.session_id}"
        )
        return None

    if not external_userid:
        logger.warning(f"[external_push] external_userid 为空，放弃推送 session={payload.session_id}")
        return None

    metadata = session.get("metadata") or {}
    nickname = (session.get("username") or "").strip()

    # 头像/性别：渠道客户的 users 记录（wecom_kf 注册时从企微 batchget 落库）
    avatar, gender = None, 0
    if session.get("user_id"):
        try:
            from src.db.models import UserDB

            user = UserDB.get_by_id(session["user_id"]) or {}
            avatar = (user.get("avatar_url") or "").strip() or None
            gender = user.get("gender") or 0
        except Exception as e:
            logger.warning(f"[external_push] 读取客户头像/性别失败 user_id={session.get('user_id')}: {e}")

    # 留资手机号：metadata.lead_capture.lead_id -> 线索记录（phone 已解密返回）
    lead_phone = None
    lead_id = (metadata.get("lead_capture") or {}).get("lead_id")
    if lead_id:
        try:
            from src.saas.db.lead_capture_db import LeadCaptureDB

            lead = LeadCaptureDB.get_by_id(lead_id, payload.tenant_id) or {}
            lead_phone = lead.get("phone") or None
        except Exception as e:
            logger.warning(f"[external_push] 读取留资手机号失败 lead_id={lead_id}: {e}")

    assignee_phone, assignee_name = _resolve_assignee(payload.tenant_id, open_kfid)

    return {
        "open_kfid": open_kfid,
        "external_userid": external_userid,
        "subagent": subagent_name,
        "nickname": nickname,
        "avatar": avatar,
        "gender": gender,
        "lead_phone": lead_phone,
        "assignee_phone": assignee_phone,
        "assignee_name": assignee_name,
    }


# ============== LLM 摘要（轻量模型，独立计费） ==============


def _truncate(text: str, limit: int = _TEXT_TRUNCATE_CHARS) -> str:
    text = (text or "").strip()
    return text[:limit]


def _current_time_line() -> str:
    """注入真实时刻（含时段标签），模型不得自行虚构时间段（2026-09-17 上午被写成"深夜客户"事故）"""
    dt = datetime.now()
    h = dt.hour
    if h < 6:
        period = "凌晨"
    elif h < 12:
        period = "上午"
    elif h < 13:
        period = "中午"
    elif h < 18:
        period = "下午"
    else:
        period = "晚上"
    return f"当前时间：{dt.strftime('%Y-%m-%d %H:%M')}（{period}）\n"


def _extract_json_object(content: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中提取 JSON object（容忍 ```json 包裹与前后杂文本）"""
    if not content:
        return None
    text = content.strip()
    if "```" in text:
        # 取第一个 ``` 之后、最后一个 ``` 之前的片段
        parts = text.split("```")
        for part in parts:
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
    """解析 lite 模型名用于计费单价归属

    provider 的 _parse_response 不返回 model 键，response.get("model") 恒为 None；
    lite 路径计费需显式解析 settings.llm.get_lite_target() 的模型名，否则独立落库
    兜底分支会误用主模型（deepseek-flash）单价。
    """
    try:
        from src.config.settings import settings

        return settings.llm.get_lite_target()[1] or None
    except Exception:
        return None


async def _summarize(payload: RecapPayload, ctx: Dict[str, Any], topic: str = "外部推送") -> Dict[str, str]:
    """LLM 摘要本轮问答；失败降级为截断原文（推送流程继续）

    用 chat_lite 走 llm.lite_model 轻量小模型——摘要任务简单，无需主模型。
    """
    fallback = {
        "customer_need": _truncate(payload.user_content),
        "reply_summary": _truncate(payload.assistant_reply),
        "customer_name_hint": "",
    }
    try:
        from src.config.settings import settings
        from src.llm.gateway import llm_gateway
        from src.services.session_record import record_background_llm_usage

        summarize_start = time.time()
        summarize_messages = [
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"客户微信昵称：{ctx.get('nickname') or '未知'}\n"
                    f"客户消息：{_truncate(payload.user_content, _DIALOGUE_TRUNCATE_CHARS)}\n"
                    f"AI回复：{_truncate(payload.assistant_reply, _DIALOGUE_TRUNCATE_CHARS)}"
                ),
            },
        ]
        response = await llm_gateway.chat_lite(
            messages=summarize_messages,
            temperature=0.2,
            max_tokens=settings.external_push.pre_sales.summary_max_tokens,
        )
        # 计费：billing_audit.md §3.5 条件 A（独立任务无 record 上下文，走独立落库路径）
        # model 必须显式解析 lite 模型名：provider 响应不含 model 键，缺 model 会
        # 误用 mid_term 摘要模型单价（session_record.py P2-1 修复先例）
        record_background_llm_usage(
            response.get("usage") if isinstance(response, dict) else None,
            tenant_id=payload.tenant_id,
            user_id=payload.user_id or getattr(payload.record_service, "user_id", None),
            source=f"external_push_{ctx.get('subagent') or 'unknown'}",
            user_message="[recap external_push] 摘要生成",
            model=_resolve_lite_model_name(),
        )
        _trace_llm_span(
            payload, "recap:external_push:summarize", response,
            _resolve_lite_model_name(), summarize_start,
            messages=summarize_messages,
        )
        data = _extract_json_object(response.get("content", ""))
        if not data:
            tlog(topic, "摘要输出解析失败，降级截断原文")
            return fallback
        return {
            "customer_need": _truncate(str(data.get("customer_need") or "")) or fallback["customer_need"],
            "reply_summary": _truncate(str(data.get("reply_summary") or "")) or fallback["reply_summary"],
            "customer_name_hint": str(data.get("customer_name_hint") or "").strip(),
        }
    except Exception as e:
        logger.warning(f"[external_push] 摘要 LLM 失败，降级截断原文: {e}")
        _trace_llm_span(payload, "recap:external_push:summarize", None, None, time.time(), success=False, error=str(e))
        return fallback


# ============== 委托登录（代码侧缓存，login_url 来自文档 api-meta） ==============


def _post_json(
    url: str,
    body: Dict[str, Any],
    agent_token: str,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """同步 POST JSON（当前仅委托登录使用），返回顶层响应 dict"""
    headers = {"Content-Type": "application/json", "Api-Authorize-Token": agent_token}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _delegate_login(
    tenant_id: str,
    mobile: str,
    agent_token: str,
    login_url: str,
    force_refresh: bool = False,
    name: Optional[str] = None,
    subagent_name: str = "",
) -> Optional[Dict[str, Any]]:
    """委托登录获取用户身份 token（默认变量名 client_token，可由文档声明）

    缓存与对话内技能脚本 delegate_login.py 共享同一 Redis 键
    （CacheKeys.EXTERNAL_LOGIN_TOKEN:{tenant_id}:{subagent}:{mobile}，TTL 23h），
    命中 0 次 HTTP；缓存值带 login_url，读取时校验不一致按未命中处理
    （防同租户多智能体对接不同外部系统时串号）。Code=-99 场景由调用方带
    force_refresh=True 强刷。subagent_name 参与缓存键隔离不同智能体对接的系统。
    name 为归属员工姓名，仅手机号不存在触发自动建号时使用，空值不传。
    """
    from src.core.cache_utils import CacheKeys, get_cached, set_cached

    cache_key_args = (tenant_id, subagent_name or "-", mobile)
    if not force_refresh:
        cached = get_cached(CacheKeys.EXTERNAL_LOGIN_TOKEN, *cache_key_args)
        if (
            isinstance(cached, dict)
            and cached.get("client_token")
            and cached.get("login_url") == login_url
        ):
            return {**cached, "cached": True}
    login_payload = {"mobile": mobile}
    if name:
        login_payload["name"] = name
    try:
        body = _post_json(login_url, login_payload, agent_token)
    except Exception as e:
        logger.warning(f"[external_push] 委托登录请求异常 tenant={tenant_id}: {e}")
        return None
    if not isinstance(body, dict) or body.get("Code") != 0:
        err = (body or {}).get("Error") or "未知业务错误"
        logger.warning(f"[external_push] 委托登录失败 tenant={tenant_id}: {err}")
        return None
    result = body.get("Response") or {}
    token_payload = {
        "client_token": result.get("client_token", ""),
        "record_id": result.get("record_id"),
        "display_name": result.get("display_name", ""),
        "agent_name": result.get("agent_name", ""),
        "created": bool(result.get("created")),
        "login_url": login_url,
    }
    if token_payload["client_token"]:
        try:
            set_cached(
                CacheKeys.EXTERNAL_LOGIN_TOKEN, *cache_key_args,
                value=token_payload, ttl=23 * 3600,
            )
        except Exception as e:
            logger.warning(f"[external_push] 登录缓存写入失败（不阻断）: {e}")
    return {**token_payload, "cached": False}


# ============== 推送循环工具集 ==============


class PushReportInput(BaseModel):
    """report_push_result 参数模型"""
    success: bool = Field(..., description="本轮推送是否成功完成（全部外部调用成功）")
    detail: str = Field(default="", description="简述执行了哪些调用、或放弃原因（不含客户手机号明文）")


class PushReportTool(BaseTool):
    """推送完成报告工具（终止信号）

    必须继承 BaseTool：ToolRegistry.get_tool_definitions() 与 ToolExecutor.execute
    都按 BaseTool 协议调用（to_tool_definition / validate_parameters）。

    有构造依赖（共享 report_holder 列表），catalog=False 不进自动发现目录。
    作用：给推送循环一个确定性的终止/成败信号，不依赖 finish_reason 与自由文本解析。
    """

    name = "report_push_result"
    description = (
        "报告本轮外部系统推送的最终结果。必须在完成或按规则放弃全部外部调用后"
        "作为最后一个工具调用：success=true 表示全部调用业务成功；"
        "success=false 表示按规则放弃（detail 写明原因）。"
    )
    display_name = "推送结果报告"
    category = "general"
    InputModel = PushReportInput
    catalog = False

    def __init__(self, report_holder: List[Dict[str, Any]]):
        self._report_holder = report_holder

    async def execute(self, **kwargs) -> Dict[str, Any]:
        record = {"success": bool(kwargs.get("success")), "detail": str(kwargs.get("detail") or "")}
        self._report_holder.append(record)
        return {"success": True, "recorded": True}


def _create_tool_runtime(
    context: "ToolExecutionContext",
) -> Tuple[Any, Any, List[Dict[str, Any]]]:
    """构建推送循环专用工具运行时：仅 http_api + report_push_result

    返回 (registry, executor, report_holder)。独立成函数便于测试替换。
    """
    from src.tools.executor import ToolExecutor
    from src.tools.network.http_api import HttpApiTool
    from src.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(HttpApiTool())
    report_holder: List[Dict[str, Any]] = []
    registry.register(PushReportTool(report_holder))
    return registry, ToolExecutor(registry), report_holder


def _extract_business_code(result: Dict[str, Any]) -> Optional[int]:
    """从 http_api 工具结果中提取第三方业务 Code

    优先取 data 为 dict 时的 data["Code"]；业务错误被 http_api 重分类后
    Code 进入 error 文案（"业务错误 Code=-99: ..."），用正则兜底提取。
    """
    data = result.get("data")
    if isinstance(data, dict) and "Code" in data:
        try:
            return int(data["Code"])
        except (TypeError, ValueError):
            return None
    match = re.search(r"Code\s*=\s*(-?\d+)", str(result.get("error") or ""))
    return int(match.group(1)) if match else None


def _normalize_http_method(args: Dict[str, Any], meta: Dict[str, str], topic: str = "外部推送") -> Dict[str, Any]:
    """按 api-meta 的 http_method 声明强制纠正 http_api 调用的 method

    lite 模型偶发把文档声明为 POST 的查询类接口自作主张写成 GET（鉴权 Header
    随之缺失，ERP 返回 -99）。声明存在时确定性纠正，杜绝该类偏差；未声明不干预。
    """
    declared = (meta.get("http_method") or "").strip().upper()
    if not declared:
        return args
    original = str(args.get("method") or "GET").strip().upper()
    if original == declared:
        return args
    corrected = dict(args)
    corrected["method"] = declared
    tlog(
        topic,
        "纠正请求方式: model={orig} -> declared={declared}, url={url}",
        orig=original,
        declared=declared,
        url=str(corrected.get("url") or "")[:120],
    )
    return corrected


def _normalized_headers(args: Dict[str, Any], topic: str = "外部推送") -> Dict[str, Any]:
    """从 http_api args 归一化出可写的 headers 副本（JSON 字符串解析 / 非法重建）"""
    headers = args.get("headers")
    if isinstance(headers, str):
        headers = _safe_json_loads(headers)
    if not isinstance(headers, dict):
        if headers:
            tlog(topic, f"http_api headers 非法（{type(headers).__name__}），重建 headers")
        return {}
    return dict(headers)


def _ensure_agent_token_header(
    args: Dict[str, Any],
    meta: Dict[str, str],
    topic: str = "外部推送",
) -> Dict[str, Any]:
    """把智能体身份鉴权 Header 强制注入 http_api 调用（覆盖式，确定性纠偏）

    2026-09-17 erp11095 Code=-99「请求缺少身份令牌」事故：lite 模型把 AGENT_TOKEN
    写进错误 Header 名（Authorization: Bearer / X-API-Key），对方系统按
    Api-Authorize-Token 识别智能体身份失败，同一把有效 client_token 也全部被拒。
    与 _ensure_user_token_header 同属确定性纠偏：Header 名由 api-meta 的
    agent_token_header 声明（缺省 Api-Authorize-Token），值写 ${AGENT_TOKEN} 占位符
    由 http_api 运行时替换（同凭证不渲染进 LLM 上下文的原则）；清除模型误写到
    其他 Header 的 ${AGENT_TOKEN} 值，避免真实凭证经未知 Header 发出。
    """
    header = (meta.get("agent_token_header") or _DEFAULT_AGENT_TOKEN_HEADER).strip()
    headers = _normalized_headers(args, topic)
    for key in list(headers.keys()):
        if isinstance(key, str) and key.lower() == header.lower() and key != header:
            headers.pop(key)
    for key, value in list(headers.items()):
        if isinstance(value, str) and "${AGENT_TOKEN}" in value and key != header:
            headers.pop(key)
    headers[header] = "${AGENT_TOKEN}"
    corrected = dict(args)
    corrected["headers"] = headers
    return corrected


def _ensure_user_token_header(
    args: Dict[str, Any],
    client_token: str,
    meta: Dict[str, str],
    topic: str = "外部推送",
) -> Dict[str, Any]:
    """把用户身份 token 强制注入 http_api 调用的鉴权 Header（覆盖式，确定性纠偏）

    2026-09-16 erp11096 Code=-99 事故：lite 模型偶发漏带 Client-Authorize-Token
    头（-99 强刷自愈实际是多余的，token 并未失效），每轮多 1~2 次重试与延迟。
    系统本就持有 client_token，直接写入而非依赖模型抄写，与 _normalize_http_method
    同属确定性纠偏。覆盖式写入保证 -99 强刷后下一轮自动携带新 token；
    大小写不同的同名 Header 一并清除，避免 httpx 发出重复鉴权头。
    """
    if not client_token:
        return args
    header = (meta.get("user_token_header") or _DEFAULT_USER_TOKEN_HEADER).strip()
    headers = _normalized_headers(args, topic)
    for key in list(headers.keys()):
        if isinstance(key, str) and key.lower() == header.lower() and key != header:
            headers.pop(key)
    headers[header] = client_token
    corrected = dict(args)
    corrected["headers"] = headers
    return corrected


def _safe_json_loads(raw: Any) -> Optional[Dict[str, Any]]:
    """解析工具调用 arguments（JSON 字符串）；失败返回 None 不抛"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


# ============== 提示词组装（跨租户通用，10605 语义只存在于租户文档） ==============


def _build_system_prompt(doc: str, meta: Dict[str, str]) -> str:
    user_token = meta.get("user_token_name", _DEFAULT_USER_TOKEN_NAME)
    ext_field = meta.get("external_userid_field", _DEFAULT_EXTERNAL_USERID_FIELD)
    return (
        "你是外部客户管理系统的数据同步执行器。你的唯一任务是：按下方租户接口文档，"
        "通过多轮 http_api 工具调用，把本轮对话结果同步到外部系统。"
        "这不是与用户的对话，不要输出面向客户的文案。\n"
        "\n"
        "==================== 租户接口文档（唯一依据） ====================\n"
        f"{doc}\n"
        "==================================================================\n"
        "\n"
        "通用约定（与文档冲突时以文档为准，但本条不可违反）：\n"
        "1. 认证：两把鉴权 Header——智能体身份 Header（"
        f"{meta.get('agent_token_header', '')}，值 ${'{AGENT_TOKEN}'}）与"
        f"用户身份 token（变量名：{user_token}）的鉴权 Header（{meta.get('user_token_header', '')}）"
        "均由系统自动附加到每次 http_api 调用，"
        "不要自行构造、复制或修改任何鉴权 Header（文档中的 token 示例值不要照抄）。"
        "不要调用文档中的登录接口。"
        "若收到「用户身份 token 已强制刷新」的系统消息，直接重试刚才失败的调用。"
        "每个业务请求必须按文档声明的请求方式调用（不要因为「查询」就自行改用 GET）。"
        "2. 成功判定：HTTP 200 不代表业务成功，以文档定义的业务成功码为准；"
        "只有业务成功才算该步完成。\n"
        "3. 执行纪律：严格按文档「同步业务规则」（或同等章节）的顺序与分流执行；"
        "同一客户查重命中后严禁再创建新记录；文档要求每轮必做的记录不可省略。"
        f"文档中声明的外部用户唯一标识承载字段（{ext_field}）固定存储系统提供的 external_userid，"
        "查重与创建均使用该字段。\n"
        "4. 重试与放弃：单步业务失败时核对文档修正参数后重试一次；仍失败则放弃本轮推送，"
        "调用 report_push_result(success=false, detail=原因)。不要无限重试。\n"
        "5. 查询最小化：查询类请求用最小结果数（如 limit=1）；"
        "若工具结果含 truncated=true（响应超长被落盘），基于预览内容判断或缩小查询条件，"
        "不要尝试读取文件。\n"
        "6. 隐私：推送是后台动作，任何信息不得向客户暴露；detail 中不要写客户手机号明文。\n"
        "7. 执行效率：查重确认后，互不依赖的写操作（如创建跟进记录与修改客户）"
        "应尽量在同一轮并行发起多个工具调用，减少轮次。\n"
        "8. 累计摘要格式：归纳客户的累计摘要（跟进汇总摘要类字段，以租户文档定义为准）时按天分条，"
        "每条以跟进日期开头（如 2026-9-15, 当日要点），每条不超过 100 字，当天多轮对话合并为一条，"
        "只保留关键诉求、结论与待办，不逐轮罗列过程；条与条之间用 CRLF（\\r\\n）分隔，"
        "禁止合并成一段或用分号分隔；须保留客户当前摘要中的历史日期条目（保持原样，不扩写），仅新增或更新当天条目。"
        "当天条目内区分多轮时以上方系统注入的真实时刻为准，"
        "禁止虚构「上午/下午/傍晚/晚间/深夜」等与真实时间不符的时段标签。\n"
        "9. 终止：完成全部外部调用、或按规则放弃时，必须调用 report_push_result 工具"
        "（success=true/false + detail 简述执行结果），且它是你最后调用的工具。"
    )


def _build_user_message(
    payload: RecapPayload,
    ctx: Dict[str, Any],
    summary: Dict[str, str],
    login: Dict[str, Any],
    meta: Dict[str, str],
) -> str:
    user_token = meta.get("user_token_name", _DEFAULT_USER_TOKEN_NAME)
    lead_phone = ctx.get("lead_phone") or "（客户未留资，留空，不得用其他号码冒充）"
    from src.tools.channel.channel_user_info import GENDER_LABELS

    gender = int(ctx.get("gender") or 0)
    gender_label = GENDER_LABELS.get(gender, "未知")
    return (
        "本轮对话数据如下，请按系统提示词与租户接口文档完成推送。\n"
        "\n"
        "【本轮对话】\n"
        f"客户消息：{_truncate(payload.user_content, _DIALOGUE_TRUNCATE_CHARS)}\n"
        f"AI 回复：{_truncate(payload.assistant_reply, _DIALOGUE_TRUNCATE_CHARS)}\n"
        "\n"
        "【预生成摘要（已由系统生成，可直接使用）】\n"
        f"客户诉求：{summary.get('customer_need') or ''}\n"
        f"回复要点：{summary.get('reply_summary') or ''}\n"
        f"客户自称：{summary.get('customer_name_hint') or '（未自报）'}\n"
        "\n"
        "【客户上下文】\n"
        f"external_userid（客户唯一标识，同一客户跨轮稳定）：{ctx['external_userid']}\n"
        f"微信昵称：{ctx.get('nickname') or '未知'}\n"
        f"微信头像：{ctx.get('avatar') or '（无，留空）'}\n"
        f"性别：{gender_label}（0未知/1男/2女，仅当租户文档声明性别字段时推送）\n"
        f"留资手机号：{lead_phone}\n"
        f"归属员工手机号：{ctx.get('assignee_phone') or ''}（已用于委托登录，无需再登录）\n"
        f"{_current_time_line()}"
        "\n"
        "【委托登录信息】\n"
        f"委托人：{login.get('display_name') or ''} / {login.get('agent_name') or ''}"
        f"（用户身份 token {user_token} 已由系统托管并自动附加到鉴权 Header，无需关注）"
    )


# ============== LLM 工具循环 ==============


async def _run_push_loop(
    payload: RecapPayload,
    ctx: Dict[str, Any],
    summary: Dict[str, str],
    doc: str,
    meta: Dict[str, str],
    agent_token: str,
    login: Dict[str, Any],
    topic: str = "外部推送",
    system_prompt: Optional[str] = None,
    user_message: Optional[str] = None,
    billing_source: Optional[str] = None,
    trace_prefix: str = "recap:external_push",
) -> str:
    """推送主体：主模型 + http_api 工具多轮循环，按租户文档自主完成推送

    终止：report_push_result（确定性）/ 无 tool_calls / 轮次上限 / 连续失败熔断。
    未收到成功报告即 raise，由 runner 吞掉记 failed（下一轮 recap 自然重试）。
    成功返回模型报告的 detail。
    system_prompt / user_message 可选覆盖：由变体适配器（如 external_push_human）
    传入定制提示词；不传时走 external_push 默认组装（行为不变）。
    """
    from src.config.settings import settings
    from src.llm.gateway import llm_gateway
    from src.services.session_record import record_background_llm_usage
    from src.tools.context import ToolExecutionContext

    max_rounds = settings.external_push.pre_sales.max_tool_rounds
    user_token = meta.get("user_token_name", _DEFAULT_USER_TOKEN_NAME)

    context = ToolExecutionContext(
        tenant_id=payload.tenant_id,
        session_id=payload.session_id,
        channel="wecom_kf",
        subagent_id=ctx.get("subagent"),
        env_vars={"AGENT_TOKEN": agent_token},
    )
    registry, executor, report_holder = _create_tool_runtime(context)
    tools = registry.get_tool_definitions()

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt or _build_system_prompt(doc, meta)},
        {"role": "user", "content": user_message or _build_user_message(payload, ctx, summary, login, meta)},
    ]

    auth_retried = False
    consecutive_failures = 0
    last_content = ""
    aborted_reason = ""
    # 系统持有的当前有效用户身份 token：注入 http_api 鉴权头的唯一来源，
    # -99 强刷成功后同步更新，保证重试自动携带新 token
    active_client_token = login.get("client_token") or ""

    for round_no in range(1, max_rounds + 1):
        round_start = time.time()
        used_lite = True
        try:
            # 推送循环用 lite 轻量模型降延迟（主模型每轮携带完整租户文档，实测 3~11s/轮）。
            # chat_lite 无 system_prompt 形参（kwargs 会被 provider **kwargs 静默吞掉），
            # system 消息由调用方并入 messages 首位
            response = await llm_gateway.chat_lite(
                messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.1,
            )
        except ValueError:
            # lite 链路不可用（lite_model 未配置或缺少 lite provider key）时回退主模型
            used_lite = False
            logger.warning("[external_push] lite 链路不可用，推送循环回退主模型链路")
            response = await llm_gateway.chat_with_tools(
                messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.1,
            )
        # 计费：每轮 LLM 调用独立落库（billing_audit.md §3.5 条件 A）。
        # provider 响应不含 model 键，按实际链路显式解析模型名，保证单价归属正确
        billed_model = _resolve_lite_model_name() if used_lite else (
            getattr(settings.llm, "model", None) or None
        )
        eff_source = billing_source or f"external_push_{ctx.get('subagent') or 'unknown'}"
        record_background_llm_usage(
            response.get("usage") if isinstance(response, dict) else None,
            tenant_id=payload.tenant_id,
            user_id=payload.user_id or getattr(payload.record_service, "user_id", None),
            source=eff_source,
            # 审计文本：默认路径保持 "[recap external_push]" 原样（不传 billing_source 时行为不变）
            user_message=f"[recap {billing_source or 'external_push'}] 推送循环 第{round_no}轮",
            model=billed_model,
        )
        _trace_llm_span(
            payload, f"{trace_prefix}:llm_round_{round_no}", response,
            billed_model, round_start, messages=messages,
        )

        tool_calls = response.get("tool_calls") or []
        if not tool_calls:
            last_content = response.get("content") or ""
            break

        messages.append({
            "role": "assistant",
            "content": response.get("content") or "",
            "tool_calls": tool_calls,
        })

        reported = False
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args = _safe_json_loads(fn.get("arguments"))
            tool_start = time.time()
            if not name or args is None:
                result: Dict[str, Any] = {
                    "success": False,
                    "error": "工具调用参数非法（工具名为空或 arguments 不是合法 JSON 对象），请修正后重新调用",
                }
            else:
                if name == "http_api":
                    args = _normalize_http_method(args, meta, topic)
                    args = _ensure_agent_token_header(args, meta, topic)
                    args = _ensure_user_token_header(args, active_client_token, meta, topic)
                try:
                    result = await executor.execute(name, args, context=context)
                except Exception as e:
                    result = {"success": False, "error": f"工具执行异常: {e}"}
            _trace_tool_span(payload, name or "unknown", args if isinstance(args, dict) else {}, result, tool_start, trace_prefix)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or "",
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

            consecutive_failures = 0 if result.get("success") else consecutive_failures + 1

            if name == "report_push_result":
                reported = True
            else:
                # -99 鉴权失效：仅首次触发强刷登录（防循环刷）
                code = _extract_business_code(result)
                if code == -99 and not auth_retried:
                    auth_retried = True
                    tlog(topic, f"业务接口 Code=-99，强刷委托登录 round={round_no}")
                    refreshed = await asyncio.to_thread(
                        _delegate_login,
                        payload.tenant_id,
                        ctx["assignee_phone"],
                        agent_token,
                        meta["login_url"],
                        True,
                        ctx.get("assignee_name"),
                        ctx.get("subagent") or payload.subagent_name or "",
                    )
                    if refreshed and refreshed.get("client_token"):
                        active_client_token = refreshed["client_token"]
                        messages.append({
                            "role": "user",
                            "content": (
                                f"用户身份 token（{user_token}）已强制刷新，"
                                f"鉴权 Header 将由系统自动携带新值，请直接重试刚才失败的调用。"
                            ),
                        })

        if reported:
            break
        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            aborted_reason = f"连续 {_MAX_CONSECUTIVE_FAILURES} 次工具调用失败，熔断终止"
            break

    # 终态判定：未收到成功报告即失败
    if not report_holder:
        detail = f"（{aborted_reason}）" if aborted_reason else ""
        raise RuntimeError(
            f"推送循环未收到完成报告{detail}，最后模型输出: {_truncate(last_content)}"
        )
    report = report_holder[-1]
    if not report.get("success"):
        raise RuntimeError(f"推送失败（模型报告放弃）: {report.get('detail') or '未说明原因'}")
    tlog(topic, f"推送完成 round={payload.round_message_id}, detail={report.get('detail')}")
    return report.get("detail") or ""


# ============== 适配器入口 ==============


class ExternalPushAdapter:
    """recap 任务适配器：external_push（每轮问答后推送租户外部客户管理系统）"""

    name = "external_push"

    @staticmethod
    async def execute(payload: RecapPayload) -> None:
        ctx = _collect_context(payload)
        if ctx is None:
            _trace_summary(payload, "skipped", "上下文采集失败")
            return

        subagent_name = (ctx.get("subagent") or payload.subagent_name or "").strip()
        if not subagent_name:
            logger.warning(
                f"[external_push] 子智能体名缺失，放弃推送 session={payload.session_id}"
            )
            _trace_summary(payload, "skipped", "子智能体名缺失")
            return
        topic = f"外部推送-{subagent_name}"
        doc_filename = f"{subagent_name}-api.md"

        doc = _load_tenant_doc(payload.tenant_id, subagent_name)
        if doc is None:
            tlog(topic, f"放弃：租户未配置 {doc_filename}, tenant={payload.tenant_id}")
            _trace_summary(payload, "skipped", f"租户未配置 {doc_filename}")
            return

        meta = parse_api_meta(doc, topic)
        if meta is None:
            logger.warning(
                f"[external_push] 租户文档 api-meta 解析失败，放弃推送 tenant={payload.tenant_id}"
            )
            _trace_summary(payload, "skipped", "租户文档 api-meta 解析失败")
            return

        doc = _strip_excluded_sections(doc, meta, topic)

        if not ctx.get("assignee_phone"):
            logger.warning(
                f"[external_push] 归属员工手机号缺失，放弃推送 session={payload.session_id}"
            )
            tlog(topic, f"放弃：归属员工手机号缺失, tenant={payload.tenant_id}, open_kfid={ctx['open_kfid']}")
            _trace_summary(payload, "skipped", "归属员工手机号缺失")
            return

        agent_token = _get_agent_token(payload.tenant_id, subagent_name)
        if not agent_token:
            logger.warning(
                f"[external_push] 租户未配置 AGENT_TOKEN，放弃推送 session={payload.session_id}"
            )
            tlog(topic, f"放弃：租户未配置 AGENT_TOKEN, tenant={payload.tenant_id}")
            _trace_summary(payload, "skipped", "租户未配置 AGENT_TOKEN")
            return

        try:
            summary = await _summarize(payload, ctx, topic)

            login = await asyncio.to_thread(
                _delegate_login, payload.tenant_id, ctx["assignee_phone"], agent_token, meta["login_url"],
                False, ctx.get("assignee_name"), subagent_name,
            )
            if not login or not login.get("client_token"):
                raise RuntimeError("委托登录失败（无 client_token）")

            detail = await _run_push_loop(payload, ctx, summary, doc, meta, agent_token, login, topic)
            _trace_summary(payload, "ok", detail)
        except Exception as e:
            _trace_summary(payload, "failed", str(e))
            raise
