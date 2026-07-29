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
        "严格返回JSON对象，字段为 matched(bool), person_name(str), mobile(str), "
        "evidence_quote(str), confidence(number), reason(str)。未命中时字符串字段可为空。"
        f"\n目标协会：{association}\n目标姓名：{person}\n证据：\n{text}"
    )
    response = await gateway.chat(
        messages=[
            {"role": "system", "content": "你是联系人证据核验器，只输出严格JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=800,
    )
    return _parse_json_content(str(response.get("content", "")))


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
