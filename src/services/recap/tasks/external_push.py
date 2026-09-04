#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
external_push recap 适配器：每轮问答后把对话数据推送到租户外部客户管理系统

由 recap runner 调度（任务级幂等已由 runner 完成）。适配器本身不硬编码任何
第三方系统的 BASE_URL / 接口路径 / 模块字段元数据——这些全部由租户接口文档
storage/tenants/{tenant_id}/templates/pre-sales-api.md 指定，执行流程：

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
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from pydantic import BaseModel, Field

from src.core.temp_logger import tlog
from src.services.recap.runner import RecapPayload
from src.tools.base import BaseTool

# ============== 租户接口文档与 api-meta 约定 ==============

_DOC_FILENAME = "pre-sales-api.md"

# 用户身份 token 默认变量名 / external_userid 承载字段默认名（10605 惯例，
# 租户可在 api-meta 块中覆盖）
_DEFAULT_USER_TOKEN_NAME = "client_token"
_DEFAULT_EXTERNAL_USERID_FIELD = "unionid"

_HTTP_TIMEOUT_SECONDS = 15
_TEXT_TRUNCATE_CHARS = 200
_DIALOGUE_TRUNCATE_CHARS = 1000

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


def _tenant_doc_path(tenant_id: str) -> Path:
    """租户接口文档路径：storage/tenants/{去前缀租户ID}/templates/pre-sales-api.md"""
    from src.core.storage import normalize_tenant_id

    return (
        Path(__file__).resolve().parents[4]
        / "storage" / "tenants" / normalize_tenant_id(tenant_id) / "templates" / _DOC_FILENAME
    )


def _load_tenant_doc(tenant_id: str) -> Optional[str]:
    """读取租户接口文档全文；缺失返回 None（放弃本轮）

    文档是 LLM 调用外部系统接口的唯一依据，不做任何截断（对齐
    load_api_config.py 的 _no_truncate 契约）。
    """
    path = _tenant_doc_path(tenant_id)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"[external_push] 租户文档读取失败 tenant={tenant_id}: {e}")
        return None


def parse_api_meta(doc_text: str) -> Optional[Dict[str, str]]:
    """从租户文档中解析机器可读 api-meta 约定块

    格式（文档顶部，```api-meta 围栏，YAML 风格 key: value 逐行）：
        ```api-meta
        login_url: https://...
        auth_mode: delegate_login
        user_token_name: client_token
        external_userid_field: unionid
        ```

    返回 meta dict；user_token_name / external_userid_field 缺省回退 10605 惯例值。
    无块 / login_url 缺失 / login_url 非 https 均返回 None（放弃原因写入 tlog）。
    宁可解析失败放弃本轮，绝不猜测 URL。
    """
    match = re.search(r"```\s*api-meta[^\n]*\n(.*?)```", doc_text or "", re.DOTALL)
    if not match:
        tlog("售前推送", "api-meta 解析失败：文档缺少 api-meta 约定块")
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
        tlog("售前推送", "api-meta 解析失败：缺少 login_url")
        return None
    if not login_url.startswith("https://"):
        tlog("售前推送", f"api-meta 解析失败：login_url 非 https（{login_url.split('?')[0]}）")
        return None

    meta.setdefault("user_token_name", _DEFAULT_USER_TOKEN_NAME)
    meta.setdefault("external_userid_field", _DEFAULT_EXTERNAL_USERID_FIELD)
    return meta


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


def _resolve_assignee_phone(tenant_id: str, open_kfid: str) -> Optional[str]:
    """归属员工手机号：open_kfid -> kf_account.tenant_user_id -> users.phone

    与 record_lead_capture._resolve_employee_phone 同款查询，但 kf_config 不经
    ContextVar（后台任务无上下文），改为从租户渠道配置反查。
    """
    try:
        from src.saas.db.channel_config_db import ChannelConfigDB

        for cfg in ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf"):
            for kf in cfg.get("config", {}).get("kf_account", []):
                if kf.get("open_kfid") == open_kfid:
                    tenant_user_id = kf.get("tenant_user_id")
                    if not tenant_user_id:
                        return None
                    from src.db.models import UserDB

                    user = UserDB.get_by_id(tenant_user_id)
                    return (user or {}).get("phone") or None
    except Exception as e:
        logger.warning(f"[external_push] 解析归属员工手机号失败 tenant={tenant_id}, open_kfid={open_kfid}: {e}")
    return None


def _collect_context(payload: RecapPayload) -> Optional[Dict[str, Any]]:
    """采集推送上下文（全部同步 DB 读，无 LLM）。缺失关键字段返回 None（放弃本轮）"""
    parsed = parse_wecom_kf_session(payload.session_id)
    if not parsed:
        logger.warning(
            f"[external_push] 非 wecom_kf 会话，放弃推送 session={payload.session_id}"
        )
        return None
    external_userid = parsed["external_userid"]
    open_kfid = parsed["open_kfid"]
    if not external_userid:
        logger.warning(f"[external_push] external_userid 为空，放弃推送 session={payload.session_id}")
        return None

    from src.channels.session import channel_session_manager

    session = channel_session_manager.get_session_by_id(payload.session_id) or {}
    metadata = session.get("metadata") or {}
    nickname = (session.get("username") or "").strip()

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

    assignee_phone = _resolve_assignee_phone(payload.tenant_id, open_kfid)

    return {
        "open_kfid": open_kfid,
        "external_userid": external_userid,
        "subagent": parsed["subagent"],
        "nickname": nickname,
        "lead_phone": lead_phone,
        "assignee_phone": assignee_phone,
    }


# ============== LLM 摘要（轻量模型，独立计费） ==============


def _truncate(text: str, limit: int = _TEXT_TRUNCATE_CHARS) -> str:
    text = (text or "").strip()
    return text[:limit]


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


async def _summarize(payload: RecapPayload, ctx: Dict[str, Any]) -> Dict[str, str]:
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

        response = await llm_gateway.chat_lite(
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"客户微信昵称：{ctx.get('nickname') or '未知'}\n"
                        f"客户消息：{_truncate(payload.user_content, _DIALOGUE_TRUNCATE_CHARS)}\n"
                        f"AI回复：{_truncate(payload.assistant_reply, _DIALOGUE_TRUNCATE_CHARS)}"
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=settings.external_push.pre_sales.summary_max_tokens,
        )
        # 计费：billing_audit.md §3.5 条件 A（独立任务无 record 上下文，走独立落库路径）
        # model 必须显式传入：chat_lite 返回的 model 即 lite_model，独立落库兜底分支
        # 缺 model 会误用 mid_term 摘要模型单价（session_record.py P2-1 修复先例）
        record_background_llm_usage(
            response.get("usage") if isinstance(response, dict) else None,
            tenant_id=payload.tenant_id,
            source="pre_sales_push",
            user_message="[recap external_push] 摘要生成",
            model=response.get("model") if isinstance(response, dict) else None,
        )
        data = _extract_json_object(response.get("content", ""))
        if not data:
            tlog("售前推送", "摘要输出解析失败，降级截断原文")
            return fallback
        return {
            "customer_need": _truncate(str(data.get("customer_need") or "")) or fallback["customer_need"],
            "reply_summary": _truncate(str(data.get("reply_summary") or "")) or fallback["reply_summary"],
            "customer_name_hint": str(data.get("customer_name_hint") or "").strip(),
        }
    except Exception as e:
        logger.warning(f"[external_push] 摘要 LLM 失败，降级截断原文: {e}")
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
) -> Optional[Dict[str, Any]]:
    """委托登录获取用户身份 token（默认变量名 client_token，可由文档声明）

    缓存与对话内技能脚本 delegate_login.py 共享同一 Redis 键
    （CacheKeys.PRE_SALES_CLIENT_TOKEN:{tenant_id}:{mobile}，TTL 23h），
    命中 0 次 HTTP；Code=-99 场景由调用方带 force_refresh=True 强刷。
    """
    from src.core.cache_utils import CacheKeys, get_cached, set_cached

    if not force_refresh:
        cached = get_cached(CacheKeys.PRE_SALES_CLIENT_TOKEN, tenant_id, mobile)
        if isinstance(cached, dict) and cached.get("client_token"):
            return {**cached, "cached": True}
    try:
        body = _post_json(login_url, {"mobile": mobile}, agent_token)
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
    }
    if token_payload["client_token"]:
        try:
            set_cached(
                CacheKeys.PRE_SALES_CLIENT_TOKEN, tenant_id, mobile,
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
        "1. 认证：请求头中的 ${AGENT_TOKEN} 占位符由系统自动替换为实际值，"
        "请保持原样书写，不要改写成其他形式。"
        f"用户身份 token（变量名：{user_token}）已由系统完成委托登录获取，见用户消息；"
        "不要调用文档中的登录接口。"
        "若收到「用户身份 token 已强制刷新」的系统消息，用新 token 重试刚才失败的调用。\n"
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
        "7. 终止：完成全部外部调用、或按规则放弃时，必须调用 report_push_result 工具"
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
        f"留资手机号：{lead_phone}\n"
        f"归属员工手机号：{ctx.get('assignee_phone') or ''}（已用于委托登录，无需再登录）\n"
        f"当前日期：{datetime.now().strftime('%Y-%m-%d')}\n"
        "\n"
        "【委托登录信息】\n"
        f"{user_token}：{login.get('client_token') or ''}\n"
        f"委托人：{login.get('display_name') or ''} / {login.get('agent_name') or ''}"
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
) -> None:
    """推送主体：主模型 + http_api 工具多轮循环，按租户文档自主完成推送

    终止：report_push_result（确定性）/ 无 tool_calls / 轮次上限 / 连续失败熔断。
    未收到成功报告即 raise，由 runner 吞掉记 failed（下一轮 recap 自然重试）。
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
        {"role": "user", "content": _build_user_message(payload, ctx, summary, login, meta)}
    ]
    system_prompt = _build_system_prompt(doc, meta)

    auth_retried = False
    consecutive_failures = 0
    last_content = ""
    aborted_reason = ""

    for round_no in range(1, max_rounds + 1):
        response = await llm_gateway.chat_with_tools(
            messages,
            tools=tools,
            tool_choice="auto",
            system_prompt=system_prompt,
            temperature=0.1,
        )
        # 计费：每轮主模型调用独立落库（billing_audit.md §3.5 条件 A），model 显式透传
        record_background_llm_usage(
            response.get("usage") if isinstance(response, dict) else None,
            tenant_id=payload.tenant_id,
            source="pre_sales_push",
            user_message=f"[recap external_push] 推送循环 第{round_no}轮",
            model=response.get("model") if isinstance(response, dict) else None,
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
            if not name or args is None:
                result: Dict[str, Any] = {
                    "success": False,
                    "error": "工具调用参数非法（工具名为空或 arguments 不是合法 JSON 对象），请修正后重新调用",
                }
            else:
                try:
                    result = await executor.execute(name, args, context=context)
                except Exception as e:
                    result = {"success": False, "error": f"工具执行异常: {e}"}

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
                    tlog("售前推送", f"业务接口 Code=-99，强刷委托登录 round={round_no}")
                    refreshed = await asyncio.to_thread(
                        _delegate_login,
                        payload.tenant_id,
                        ctx["assignee_phone"],
                        agent_token,
                        meta["login_url"],
                        True,
                    )
                    if refreshed and refreshed.get("client_token"):
                        messages.append({
                            "role": "user",
                            "content": (
                                f"用户身份 token（{user_token}）已强制刷新，"
                                f"请用新 token 重试刚才失败的调用：{refreshed['client_token']}"
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
    tlog("售前推送", f"推送完成 round={payload.round_message_id}, detail={report.get('detail')}")


# ============== 适配器入口 ==============


class ExternalPushAdapter:
    """recap 任务适配器：external_push（每轮问答后推送租户外部客户管理系统）"""

    name = "external_push"

    @staticmethod
    async def execute(payload: RecapPayload) -> None:
        ctx = _collect_context(payload)
        if ctx is None:
            return

        doc = _load_tenant_doc(payload.tenant_id)
        if doc is None:
            tlog("售前推送", f"放弃：租户未配置 {_DOC_FILENAME}, tenant={payload.tenant_id}")
            return

        meta = parse_api_meta(doc)
        if meta is None:
            logger.warning(
                f"[external_push] 租户文档 api-meta 解析失败，放弃推送 tenant={payload.tenant_id}"
            )
            return

        if not ctx.get("assignee_phone"):
            logger.warning(
                f"[external_push] 归属员工手机号缺失，放弃推送 session={payload.session_id}"
            )
            tlog("售前推送", f"放弃：归属员工手机号缺失, tenant={payload.tenant_id}, open_kfid={ctx['open_kfid']}")
            return

        agent_token = _get_agent_token(payload.tenant_id, ctx.get("subagent"))
        if not agent_token:
            logger.warning(
                f"[external_push] 租户未配置 AGENT_TOKEN，放弃推送 session={payload.session_id}"
            )
            tlog("售前推送", f"放弃：租户未配置 AGENT_TOKEN, tenant={payload.tenant_id}")
            return

        summary = await _summarize(payload, ctx)

        login = await asyncio.to_thread(
            _delegate_login, payload.tenant_id, ctx["assignee_phone"], agent_token, meta["login_url"]
        )
        if not login or not login.get("client_token"):
            raise RuntimeError("委托登录失败（无 client_token）")

        await _run_push_loop(payload, ctx, summary, doc, meta, agent_token, login)
