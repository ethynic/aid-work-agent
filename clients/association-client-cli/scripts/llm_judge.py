"""LLMGateway evidence judge adapter. stdin/stdout are single JSON documents."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import math
import os
import pathlib
import re
import sys
from typing import Any


JUDGE_MAX_TOKENS = 2500


class JudgeUsageError(ValueError):
    def __init__(self, token_usage: dict[str, int]):
        super().__init__("judge response invalid")
        self.token_usage = token_usage


def _usage(response: object) -> dict[str, int]:
    usage = response.get("usage", {}) if isinstance(response, dict) else {}
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}

    def read(primary: str, alias: str | None = None, *, default: int | None = None):
        name = primary if primary in usage else alias if alias and alias in usage else None
        if name is None:
            return default
        item = usage[name]
        return item if isinstance(item, int) and not isinstance(item, bool) and item >= 0 else None

    prompt = read("prompt_tokens", "input_tokens")
    cached = read("cached_tokens", "cached_input_tokens", default=0)
    completion = read("completion_tokens", "output_tokens")
    reported_total = read("total_tokens", default=0)
    if (
        None in (prompt, cached, completion, reported_total)
        or cached > prompt
        or (prompt == 0 and cached == 0 and completion == 0 and reported_total == 0)
    ):
        return {"prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}
    result = {
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    result["call_count"] = 1
    return result


def _add_usage(target: dict[str, int], source: dict[str, int]) -> None:
    for key in target:
        target[key] += source.get(key, 0)


def _parse_json_content(content: str) -> dict[str, Any]:
    value = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, re.DOTALL | re.IGNORECASE)
    if fenced:
        value = fenced.group(1)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("judge response must be an object")
    required = {"matched", "person_name", "mobile", "evidence_quote", "confidence", "reason"}
    if set(parsed) != required:
        raise ValueError("judge response schema must contain exactly the required fields")
    if not isinstance(parsed["matched"], bool):
        raise ValueError("matched must be boolean")
    if (
        isinstance(parsed["confidence"], bool)
        or not isinstance(parsed["confidence"], (int, float))
        or not math.isfinite(parsed["confidence"])
        or not 0 <= parsed["confidence"] <= 1
    ):
        raise ValueError("confidence must be numeric")
    for key in ("person_name", "mobile", "evidence_quote", "reason"):
        if not isinstance(parsed[key], str):
            raise ValueError(f"{key} must be string")
    return parsed


async def run_judge(payload: dict[str, Any], gateway: Any = None) -> dict[str, Any]:
    association = str(payload.get("association_name", "")).strip()
    person = str(payload.get("person_name", "")).strip()
    text = str(payload.get("text", ""))
    if not association or not person or not text:
        raise ValueError("association_name, person_name and text are required")
    if gateway is None:
        # 客户端 CLI 模式：从环境变量构造 ProxyLLMGateway（走服务端代理计费）
        server_url = os.environ.get("ASSOCIATION_CLIENT_SERVER_URL")
        access_token = os.environ.get("ASSOCIATION_CLIENT_ACCESS_TOKEN")
        if server_url and access_token:
            # 找到 runtime 目录（PyInstaller 打包后从 _MEIPASS）
            scripts_parent = pathlib.Path(__file__).resolve().parent.parent
            if str(scripts_parent) not in sys.path:
                sys.path.insert(0, str(scripts_parent))
            from runtime.proxy_gateway import ProxyLLMGateway
            gateway = ProxyLLMGateway(server_url, access_token)
        else:
            # 降级：直连 llm_gateway（服务端开发调试用）
            project_root = pathlib.Path(__file__).resolve().parents[3]
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))
            from src.llm.gateway import llm_gateway
            gateway = llm_gateway
    prompt = (
        "仅依据下面证据判断目标联系人手机号。不得补全或猜测号码。"
        "证据可能来自微信搜索结果，旧信息同样可以采用，不要因为发布时间较早而拒绝。"
        "必须按自然语言语义判断号码是否可作为目标人的联系方式，不能仅因目标姓名和号码"
        "偶然出现在同一行就命中。若目标人明确列在“联系人/联络人”等联系人组中，随后"
        "给出一个联系电话或手机号，则该号码可视为组内每个人（包括目标人）的可用联系方式，"
        "即使该号码为多人共用或同时服务其他人。只有文本明确把号码排他绑定给另一个人，"
        "或目标姓名只出现在与该联系方式无关的上下文时，才必须不命中。"
        "如果同一个完整手机号在多条相互独立的搜索结果中，反复与目标姓名及联系电话关系"
        "紧邻出现，这种重复交叉证据可以支持命中。存在多个候选时，优先选择在更多条独立"
        "结果中重复与目标联系人组关联的完整手机号，而不是只出现一次的候选。"
        "严格返回JSON对象，字段为 matched(bool), person_name(str), mobile(str), "
        "evidence_quote(str), confidence(number), reason(str)。"
        "命中时person_name必须等于目标姓名，mobile必须逐字来自证据，evidence_quote必须是"
        "证据中逐字连续的一段，且同时包含目标姓名和该完整手机号；优先选择最短、归属关系"
        "最清晰的原文片段。未命中时字符串字段可为空。"
        f"\n目标协会：{association}\n目标姓名：{person}\n证据：\n{text}"
    )
    messages = [
        {"role": "system", "content": "你是联系人证据核验器，只输出严格JSON。"},
        {"role": "user", "content": prompt},
    ]
    response = await gateway.chat(
        messages=messages,
        temperature=0,
        max_tokens=JUDGE_MAX_TOKENS,
    )
    total_usage = {"prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}
    _add_usage(total_usage, _usage(response))
    try:
        result = _parse_json_content(str(response.get("content", "")))
        if any(total_usage.values()):
            result["token_usage"] = total_usage
        return result
    except (json.JSONDecodeError, ValueError):
        # Do not include the invalid raw response: it may contain evidence,
        # secrets or prompt-amplifying text. Retry only format/schema errors;
        # provider/network/auth exceptions continue to fail immediately.
        retry_messages = messages + [
            {
                "role": "user",
                "content": (
                    "上一次输出不符合协议。请重新判断同一份证据，并且只输出一个严格JSON对象，"
                    "不要Markdown、代码围栏或解释。顶层字段必须且只能是："
                    "matched(bool), person_name(str), mobile(str), "
                    "evidence_quote(str), confidence(number 0..1), reason(str)。"
                ),
            }
        ]
        try:
            retry_response = await gateway.chat(
                messages=retry_messages,
                temperature=0,
                max_tokens=JUDGE_MAX_TOKENS,
            )
        except Exception as exc:
            if any(total_usage.values()):
                raise JudgeUsageError(total_usage) from exc
            raise
        _add_usage(total_usage, _usage(retry_response))
        try:
            result = _parse_json_content(str(retry_response.get("content", "")))
        except (json.JSONDecodeError, ValueError) as exc:
            raise JudgeUsageError(total_usage) from exc
        if any(total_usage.values()):
            result["token_usage"] = total_usage
        return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        payload = json.loads(sys.stdin.read())
        # 项目 Gateway/Provider 可能通过 loguru 或 print 输出内部配置、Key 后缀，
        # 隔离这些输出，保证 CLI 协议只有一行 JSON，stderr 也不携带证据。
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = asyncio.run(run_judge(payload))
        sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
        return 0
    except Exception as exc:
        token_usage = getattr(exc, "token_usage", None)
        if isinstance(token_usage, dict) and any(token_usage.values()):
            sys.stdout.write(json.dumps(
                {"inconclusive": True, "token_usage": token_usage},
                separators=(",", ":"),
            ) + "\n")
            return 0
        # 不写输入原文；仅输出异常类型，供调用方判定 inconclusive。
        sys.stderr.write(f"judge_failed:{type(exc).__name__}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
