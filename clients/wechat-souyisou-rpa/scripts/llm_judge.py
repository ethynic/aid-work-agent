"""LLMGateway evidence judge adapter. stdin/stdout are single JSON documents."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import math
import pathlib
import re
import sys
from typing import Any


JUDGE_MAX_TOKENS = 2500


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
    try:
        return _parse_json_content(str(response.get("content", "")))
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
        retry_response = await gateway.chat(
            messages=retry_messages,
            temperature=0,
            max_tokens=JUDGE_MAX_TOKENS,
        )
        return _parse_json_content(str(retry_response.get("content", "")))


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
        # 不写输入原文；仅输出异常类型，供调用方判定 inconclusive。
        sys.stderr.write(f"judge_failed:{type(exc).__name__}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
