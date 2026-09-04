#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
external_push recap 适配器：每轮问答后推送 10605 售前系统

由 recap runner 调度（任务级幂等已由 runner 完成），执行流程：
上下文采集 -> LLM 摘要（计费）-> 委托登录（复用技能脚本缓存）->
客户查重/建改 -> 创建跟进记录。任一步失败按降级表处理，异常上抛由 runner 吞掉。

业务规则出处：docs/subagent/pre-sales/external-push-code-hook-design.md §2/§3.3、
ext/10605-售前咨询接口文档.md §12（已代码化）。
"""

import asyncio
import importlib.util
import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from src.core.temp_logger import tlog
from src.services.recap.runner import RecapPayload

# ============== 10605 端点（环境变量可覆盖） ==============

_BASE_URL = os.environ.get("PRESALES_10605_BASE_URL", "https://erp10605.aidingyi.cn")
LOGIN_URL = f"{_BASE_URL}/api/v1/erp.delegate/login"
LISTING_URL = f"{_BASE_URL}/api/v1/erp.module/module_listing_view"
DATA_UPDATE_URL = f"{_BASE_URL}/api/v1/erp.module/module_data_update"

_HTTP_TIMEOUT_SECONDS = 15
_TEXT_TRUNCATE_CHARS = 200

# 跟进动作固定值（接口文档 §12.3）
_FOLLOW_UP_ACTION = "微信咨询（AI）"

# wecom_kf 会话 ID 格式：tenant_{tid}_wecom_kf_{open_kfid}_{external_userid}_{subagent}
_WECOM_KF_MARKER = "_wecom_kf_"

# LLM 摘要 system prompt（输出固定 JSON，§3.4）
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
    return {"open_kfid": open_kfid, "external_userid": external_userid}


def _post_10605(url: str, client_token: Optional[str], body: Dict[str, Any]) -> Dict[str, Any]:
    """同步 POST 10605 业务接口（双 Token 鉴权），返回顶层响应 dict（含 Code/Error/Response）"""
    agent_token = os.environ.get("AGENT_TOKEN", "")
    headers = {
        "Content-Type": "application/json",
        "Api-Authorize-Token": agent_token,
    }
    if client_token:
        headers["Client-Authorize-Token"] = client_token
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def _post_10605_async(url: str, client_token: Optional[str], body: Dict[str, Any]) -> Dict[str, Any]:
    """_post_10605 的异步包装（urllib 同步 IO 必须 to_thread，避免阻塞事件循环）"""
    return await asyncio.to_thread(_post_10605, url, client_token, body)


def _load_delegate_login_module():
    """加载技能脚本 delegate_login.py（目录名含连字符，无法常规 import，走 importlib）"""
    script_path = (
        Path(__file__).resolve().parents[3]
        / "skills" / "pre-sales-api-1.0.0" / "scripts" / "delegate_login.py"
    )
    spec = importlib.util.spec_from_file_location("presales_delegate_login", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _delegate_login(tenant_id: str, mobile: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """委托登录（复用技能脚本的缓存与请求函数），成功返回含 client_token 的 dict

    缓存命中 0 次 HTTP；Code=-99 场景由调用方带 force_refresh=True 强刷。
    """
    module = _load_delegate_login_module()
    if not force_refresh:
        cached = module._read_cache(tenant_id, mobile)
        if isinstance(cached, dict) and cached.get("client_token"):
            return {**cached, "cached": True}
    ok, result = module._call_login_api(LOGIN_URL, mobile)
    if not ok:
        logger.warning(f"[external_push] 委托登录失败 tenant={tenant_id}: {result}")
        return None
    token_payload = {
        "client_token": result.get("client_token", ""),
        "record_id": result.get("record_id"),
        "display_name": result.get("display_name", ""),
        "agent_name": result.get("agent_name", ""),
    }
    if token_payload["client_token"]:
        module._write_cache(tenant_id, mobile, token_payload)
    return {**token_payload, "cached": False}


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
        "nickname": nickname,
        "lead_phone": lead_phone,
        "assignee_phone": assignee_phone,
    }


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
    """LLM 摘要本轮问答；失败降级为截断原文（推送流程继续，§3.4）

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
                        f"客户消息：{_truncate(payload.user_content, 1000)}\n"
                        f"AI回复：{_truncate(payload.assistant_reply, 1000)}"
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


def _customer_name(summary: Dict[str, str], ctx: Dict[str, Any]) -> str:
    """客户名称兜底优先级：对话表明身份 > 微信昵称 > external_userid 前 8 位"""
    return (
        summary.get("customer_name_hint")
        or ctx.get("nickname")
        or ctx["external_userid"][:8]
    )


async def _execute_with_retry(payload: RecapPayload, ctx: Dict[str, Any], summary: Dict[str, str]) -> None:
    """单次完整推送（登录 -> 查重 -> 建改 -> 跟进记录），Code=-99 强刷登录重试一次"""
    mobile = ctx.get("assignee_phone")
    if not mobile:
        logger.warning(
            f"[external_push] 归属员工手机号缺失，放弃推送 session={payload.session_id}"
        )
        tlog("售前推送", f"放弃：归属员工手机号缺失, tenant={payload.tenant_id}, open_kfid={ctx['open_kfid']}")
        return

    login = await asyncio.to_thread(_delegate_login, payload.tenant_id, mobile)
    if not login or not login.get("client_token"):
        raise RuntimeError("委托登录失败（无 client_token）")

    code, _ = await _push_once(payload, ctx, login, summary)
    if code == 0:
        return
    # 业务错误重试 1 次；-99 先强刷 token
    tlog("售前推送", f"首次推送 Code={code}，重试一次")
    force_refresh = code == -99
    login = await asyncio.to_thread(
        _delegate_login, payload.tenant_id, mobile, force_refresh=force_refresh
    )
    if not login or not login.get("client_token"):
        raise RuntimeError(f"重试前委托登录失败（Code={code}）")
    code, _ = await _push_once(payload, ctx, login, summary)
    if code != 0:
        raise RuntimeError(f"重试后仍失败（Code={code}）")


async def _push_once(
    payload: RecapPayload,
    ctx: Dict[str, Any],
    login: Dict[str, Any],
    summary: Dict[str, str],
) -> Tuple[int, Any]:
    """单轮推送主体：查重 -> 建改客户 -> 创建跟进记录。返回 (最终Code, Response)"""
    client_token = login["client_token"]

    # 1. 客户查重（unionid = external_userid，必须 exact=true，§12.1）
    list_resp = await _post_10605_async(LISTING_URL, client_token, {
        "module": "kehuxinxi",
        "filters": [
            {"attr": "unionid", "value": [ctx["external_userid"]], "component": "input", "exact": True},
        ],
        "page": 1,
        "limit": 1,
    })
    code = list_resp.get("Code", -1)
    if code != 0:
        return code, list_resp.get("Response")
    rows = (list_resp.get("Response") or {}).get("data") or []
    existing = rows[0] if rows else None

    # 2. 建/改分流（查重命中后严禁再创建——历史重复客户根因）
    today = datetime.now().strftime("%Y-%m-%d")
    if existing is None:
        create_resp = await _post_10605_async(DATA_UPDATE_URL, client_token, {
            "module": "kehuxinxi",
            "did": None,
            "tables": [{
                "method": "insert",
                "table": "t_kehuxinxi",
                "data": [{
                    "xingming": _customer_name(summary, ctx),
                    "lianxidianhua": ctx.get("lead_phone") or "",
                    "weixinhao": "",
                    "unionid": ctx["external_userid"],
                    "tuiguangriqi": today,
                    "tuiguangqudao": "微信私聊",
                    "beizhu": "企业微信客服 AI 咨询",
                    "update_time": "",
                    "id": "",
                }],
            }],
            "temp": False,
        })
        code = create_resp.get("Code", -1)
        if code != 0:
            return code, create_resp.get("Response")
        # 创建响应只返回新 id，跟进记录字段回填用查重时同款最小信息
        customer = {
            "id": create_resp.get("Response"),
            "xingming": _customer_name(summary, ctx),
            "lianxidianhua": ctx.get("lead_phone") or "",
            "weixinhao": "",
            "suoshuhangye": "",
            "lianxiren": "",
        }
        tlog("售前推送", f"创建客户成功 id={customer['id']}, unionid={ctx['external_userid'][:12]}...")
    else:
        customer = existing
        new_phone = ctx.get("lead_phone") or ""
        old_phone = customer.get("lianxidianhua") or ""
        # 命中且有新留资信息（手机号非空且与已存不同）-> 修改（带 update_time 防 Code:2）
        if new_phone and new_phone != old_phone:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            update_resp = await _post_10605_async(DATA_UPDATE_URL, client_token, {
                "module": "kehuxinxi",
                "did": customer.get("id"),
                "tables": [{
                    "method": "update",
                    "table": "t_kehuxinxi",
                    "data": [{
                        "lianxidianhua": new_phone,
                        "update_time": now_str,
                        "id": customer.get("id"),
                    }],
                }],
                "temp": False,
            })
            code = update_resp.get("Code", -1)
            if code != 0:
                return code, update_resp.get("Response")
            customer["lianxidianhua"] = new_phone
            tlog("售前推送", f"更新客户手机号成功 id={customer.get('id')}")
        else:
            tlog("售前推送", f"客户已存在且无新信息，跳过客户写入 id={customer.get('id')}")

    # 3. 创建跟进记录（每轮必建，§12.3；字段从客户记录回填）
    follow_resp = await _post_10605_async(DATA_UPDATE_URL, client_token, {
        "module": "lianxijilu",
        "did": None,
        "tables": [{
            "method": "insert",
            "table": "t_lianxijilu",
            "data": [{
                "kehuxingming": customer.get("xingming") or "",
                "lianxiren": customer.get("lianxiren") or "",
                "lianxifangshi": customer.get("lianxidianhua") or "",
                "weixinhao": customer.get("weixinhao") or "",
                "suoshuhangye": customer.get("suoshuhangye") or "",
                "shijian": today,
                "genjinren": login.get("display_name") or "",
                "neirong": summary.get("reply_summary") or "",
                "kehuhuifuneirong": summary.get("customer_need") or "",
                "genjindongzuo": _FOLLOW_UP_ACTION,
                "update_time": "",
                "id": "",
            }],
        }],
        "temp": False,
    })
    code = follow_resp.get("Code", -1)
    if code != 0:
        return code, follow_resp.get("Response")
    tlog("售前推送", f"创建跟进记录成功 id={follow_resp.get('Response')}, round={payload.round_message_id}")
    return 0, follow_resp.get("Response")


class ExternalPush10605Adapter:
    """recap 任务适配器：external_push（10605 售前系统每轮推送）"""

    name = "external_push"

    @staticmethod
    async def execute(payload: RecapPayload) -> None:
        ctx = _collect_context(payload)
        if ctx is None:
            return

        summary = await _summarize(payload, ctx)
        await _execute_with_retry(payload, ctx, summary)
