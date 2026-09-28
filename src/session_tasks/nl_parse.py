"""会话任务自然语言建任务解析（NL → 部分草稿 spec + 缺失要素清单）。

定位：LLM 只做「抽取」，组装、必要要素缺失判定、绑定过滤一律以后端为权威——
不信任 LLM 自报的完成度，幻觉的 binding_id 一律丢弃（计划文档 §数据与安全）。

端点为纯读（绑定查询 + 一次 chat_no_thinking），不落库、不占幂等键。
必要要素（缺一即入 missing、前端禁存）：目标用户（绑定）、任务目标、完成
方式及参数、截止时间；其余字段给项目默认值，不阻塞保存。
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from .constants import (
    DEFAULT_SCENARIO_KEY,
    ERR_PARSE_UNAVAILABLE,
    ERR_VALIDATION_FAILED,
    SessionTaskError,
)

_MAX_TEXT_CHARS = 4000
_LLM_TIMEOUT_SECONDS = 45

# 必要要素 → 缺失提示（前端按 field 渲染清单；文案面向最终用户）
_MISSING_MESSAGES = {
    "binding": "未识别到要发消息的对象，请从已有会话绑定中选择",
    "goal": "描述中未说明任务要实现的目标",
    "completion_rule": "描述中未说明完成方式（聊几轮 / 完成标准 / 需对方确认什么）",
    "expires_at": "描述中未说明任务截止时间",
}

_DEFAULT_STYLE = "专业、简洁，不作未经授权的承诺"


def _extract_json(content: str) -> Optional[Dict[str, Any]]:
    """分层提取 LLM 输出中的 JSON：直接解析 → code block → 正则兜底。"""
    text = (content or "").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass
    code_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    if code_match:
        try:
            value = json.loads(code_match.group(1).strip())
            return value if isinstance(value, dict) else None
        except (json.JSONDecodeError, ValueError):
            pass
    json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text)
    if json_match:
        try:
            value = json.loads(json_match.group())
            return value if isinstance(value, dict) else None
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _clamp_int(value: Any, low: int, high: int) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(low, min(high, number))


def _clean_string(value: Any, max_chars: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:max_chars]


def _clean_string_list(value: Any, max_items: int, max_chars: int) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        text = _clean_string(item, max_chars)
        if text and text not in result:
            result.append(text)
        if len(result) >= max_items:
            break
    return result


def _clean_completion(value: Any) -> Optional[Dict[str, Any]]:
    """清洗 completion_rule：仅接受三种合法形态，参数越界整体丢弃（入 missing）。"""
    if not isinstance(value, dict):
        return None
    mode = value.get("mode")
    if mode == "rounds":
        rounds = _clamp_int(value.get("rounds_target"), 1, 1000)
        if rounds is None:
            return None
        return {"mode": "rounds", "rounds_target": rounds}
    if mode == "judged":
        criteria = _clean_string_list(value.get("criteria"), 10, 500)
        if not criteria:
            return None
        return {"mode": "judged", "criteria": criteria}
    if mode == "peer_confirmed":
        raw_fields = value.get("fields") if isinstance(value.get("fields"), list) else []
        fields = []
        for raw in raw_fields[:10]:
            if not isinstance(raw, dict):
                continue
            key = raw.get("key")
            key = key.strip().lower().replace("-", "_") if isinstance(key, str) else ""
            if not re.match(r"^[a-z][a-z0-9_]{0,63}$", key or ""):
                # 常见中文/非法标识：生成可用 key（field_1 序号），语义由 question 承载
                key = f"field_{len(fields) + 1}"
            question = _clean_string(raw.get("question"), 500)
            allowed = _clean_string_list(raw.get("allowed_values"), 20, 100)
            accepted = _clean_string_list(raw.get("accepted_values"), 20, 100)
            if not question or not allowed or not accepted:
                continue
            # 接受值须落在允许值集合内；无交集即整体丢弃该字段（不伪造接受语义）
            accepted = [v for v in accepted if v in allowed]
            if not accepted:
                continue
            if any(f["key"] == key for f in fields):
                continue
            fields.append({"key": key, "question": question, "allowed_values": allowed, "accepted_values": accepted})
        if not fields:
            return None
        return {"mode": "peer_confirmed", "fields": fields, "require_all": True}
    return None


def _clean_expires(value: Any) -> Optional[str]:
    """截止时间：接受 ISO8601（带时区）； naive 视为北京时间；过期/非法返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    if parsed <= datetime.now(timezone.utc):
        return None
    return parsed.isoformat()


def _list_bindings(tenant_id: str, user_id: str, scenario_key: str) -> List[Dict[str, Any]]:
    from .scenario_descriptor import ScenarioDescriptorError, require_descriptor

    try:
        resolver = require_descriptor(scenario_key).binding_resolver
        rows = resolver.list_bindings(tenant_id, user_id, "", 200)
    except ScenarioDescriptorError as exc:
        raise SessionTaskError(f"场景 {scenario_key} 不支持绑定管理或未注册", ERR_VALIDATION_FAILED, 400) from exc
    # 只保留匹配所需最小字段，验证证据等敏感列不出域（计划 §数据与安全）
    keep = ("id", "device_id", "account_binding_id", "conversation_type", "conversation_label", "verification_status")
    return [{k: (str(row[k]) if k in ("id", "device_id", "account_binding_id") else row.get(k)) for k in keep}
            for row in (rows or [])]


def _build_prompt(text: str, bindings: List[Dict[str, Any]]) -> Tuple[str, str]:
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    if bindings:
        binding_lines = "\n".join(
            f"- id: {b['id']} | 名称: {b.get('conversation_label') or '（未命名）'} | 类型: {b.get('conversation_type') or 'unknown'}"
            for b in bindings
        )
    else:
        binding_lines = "（当前没有任何已绑定的会话对象，binding_ids 必须返回空数组）"
    system = f"""你是微信自动聊天会话任务的配置解析器，唯一任务：把用户的自然语言任务描述抽取为结构化配置，输出严格 JSON。

## 今天日期
{today}（北京时间）。描述中的日期一律按北京时间理解；expires_at 输出 ISO8601 带时区（例 "2026-09-30T18:00:00+08:00"）。

## 可用会话对象
{binding_lines}

## 字段规则
- goal：任务要实现的目标（≤2000 字）；描述没有则 null
- opening_text：开场白原文（≤500 字）；没有则 null
- completion_rule：完成方式，三选一；描述没有则 null：
  - {{"mode":"rounds","rounds_target":正整数}} —— 用户说了聊几轮/发几条
  - {{"mode":"judged","criteria":["…", …]}} —— 用户给了"做到什么算完成"的标准（1–10 条）
  - {{"mode":"peer_confirmed","fields":[{{"key":"英文标识","question":"确认问题","allowed_values":["可选值"],"accepted_values":["接受值"]}}]}} —— 用户要求对方明确确认某些信息
- reply_policy.style：用户提到的回复语气/风格；没有则 null
- reply_policy.allowed_facts / forbidden_commitments：用户提到可用事实/禁止承诺（字符串数组）；没有则空数组
- limits.max_replies / max_decisions / max_cost_units / peer_wait_timeout_seconds：仅用户明确给出数值上限才填，否则 null
- limits.expires_at：任务截止时间；用户没说则 null
- binding_ids：把描述里提到的对象（人名/群名/备注）与上面会话对象列表匹配，按相关性从高到低返回 id 数组；匹配不到返回 []

## 输出
只输出一个 JSON 对象，不要解释、不要 markdown：
{{"goal":null,"opening_text":null,"completion_rule":null,"reply_policy":{{"style":null,"allowed_facts":[],"forbidden_commitments":[]}},"limits":{{"max_replies":null,"max_decisions":null,"max_cost_units":null,"peer_wait_timeout_seconds":null,"expires_at":null}},"binding_ids":[]}}"""
    return system, f"## 任务描述\n{text}"


async def _call_llm(text: str, bindings: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """返回 (解析结果, 失败原因)。网关异常 → 503（前端提示后回退手动填表）；
    输出非 JSON → (None, parse_error)，由调用方降级为全缺失。"""
    from src.llm.gateway import llm_gateway

    system, user = _build_prompt(text, bindings)
    try:
        result = await asyncio.wait_for(
            llm_gateway.chat_no_thinking(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
                max_tokens=2048,
            ),
            timeout=_LLM_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 网关/超时/限流 → 503，前端回退手动填表
        logger.warning("session_task NL 解析 LLM 调用失败: {err}", err=str(exc)[:200])
        raise SessionTaskError("解析服务暂不可用，请稍后重试或手动填写", ERR_PARSE_UNAVAILABLE, 503) from exc
    parsed = _extract_json(result.get("content", ""))
    if parsed is None:
        logger.warning("session_task NL 解析输出非 JSON: {preview}", preview=str(result.get("content", ""))[:200])
        return None, "解析结果格式异常，已保留空白表单，请手动填写或调整描述后重试"
    return parsed, None


def _assemble_spec(parsed: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """LLM 抽取 → 表单可用的部分 spec：必要要素缺失保持空，其余补项目默认值。"""
    parsed = parsed or {}
    completion = _clean_completion(parsed.get("completion_rule"))
    raw_limits = parsed.get("limits") if isinstance(parsed.get("limits"), dict) else {}
    raw_policy = parsed.get("reply_policy") if isinstance(parsed.get("reply_policy"), dict) else {}

    rounds_target = completion.get("rounds_target") if completion and completion["mode"] == "rounds" else None
    max_replies = _clamp_int(raw_limits.get("max_replies"), 1, 1000) or (rounds_target + 2 if rounds_target else 5)
    max_decisions = _clamp_int(raw_limits.get("max_decisions"), 1, 1000) or max(max_replies * 2, 10)
    max_cost = _clamp_int(raw_limits.get("max_cost_units"), 1, 10_000_000) or 100
    wait_seconds = _clamp_int(raw_limits.get("peer_wait_timeout_seconds"), 1, 30 * 86400) or 86400
    return {
        "goal": _clean_string(parsed.get("goal"), 2000) or "",
        "opening_text": _clean_string(parsed.get("opening_text"), 500),
        "completion_rule": completion,
        "reply_policy": {
            "style": _clean_string(raw_policy.get("style"), 200) or _DEFAULT_STYLE,
            "allowed_facts": _clean_string_list(raw_policy.get("allowed_facts"), 50, 500),
            "forbidden_commitments": _clean_string_list(raw_policy.get("forbidden_commitments"), 50, 500),
        },
        "limits": {
            "max_replies": max_replies,
            "max_decisions": max_decisions,
            "max_cost_units": max_cost,
            "peer_wait_timeout_seconds": wait_seconds,
            "expires_at": _clean_expires(raw_limits.get("expires_at")),
        },
        "work_window": None,
    }


def _match_bindings(parsed: Optional[Dict[str, Any]], bindings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """LLM 匹配的 binding_ids 过滤为真实属主绑定，按 LLM 相关性顺序返回整行。"""
    if not parsed or not bindings:
        return []
    allowed = {str(b["id"]): b for b in bindings}
    seen = set()
    candidates = []
    for raw in parsed.get("binding_ids") or []:
        binding_id = str(raw) if isinstance(raw, (str, int)) else ""
        if binding_id in allowed and binding_id not in seen:
            seen.add(binding_id)
            candidates.append(allowed[binding_id])
    return candidates


def _missing_fields(spec: Dict[str, Any], candidates: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    missing = []
    if not candidates:
        missing.append({"field": "binding", "message": _MISSING_MESSAGES["binding"]})
    if not spec.get("goal"):
        missing.append({"field": "goal", "message": _MISSING_MESSAGES["goal"]})
    if not spec.get("completion_rule"):
        missing.append({"field": "completion_rule", "message": _MISSING_MESSAGES["completion_rule"]})
    if not spec.get("limits", {}).get("expires_at"):
        missing.append({"field": "expires_at", "message": _MISSING_MESSAGES["expires_at"]})
    return missing


async def parse_natural_language(tenant_id: str, user_id: str, text: str,
                                 scenario_key: str = DEFAULT_SCENARIO_KEY) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text or len(text) > _MAX_TEXT_CHARS:
        raise SessionTaskError("任务描述须为 1–4000 字", ERR_VALIDATION_FAILED, 400)
    bindings = await asyncio.to_thread(_list_bindings, tenant_id, user_id, scenario_key)
    parsed, parse_error = await _call_llm(text, bindings)
    spec = _assemble_spec(parsed)
    candidates = _match_bindings(parsed, bindings)
    return {
        "spec": spec,
        "binding_candidates": candidates,
        "bindings": bindings,
        "missing": _missing_fields(spec, candidates),
        "parse_error": parse_error,
    }
