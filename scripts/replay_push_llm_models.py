"""重放 2026-09-17 erp11095 推送循环失败轮次的 LLM 请求，对比三个 lite 候选模型

事故背景：qwen3.8-flash 在首轮就把 ${AGENT_TOKEN} 写进错误鉴权头
（Authorization: Bearer / X-API-Key），QBA 按 Api-Authorize-Token 识别智能体
身份失败，Code=-99 连续 3 次熔断。

重放方式：取 trace tr_37a5659e3d904f05 llm_round_1 的原始 messages（system
租户文档 + user 上下文），用真实工具 schema（http_api + report_push_result）
向各模型发起请求（temperature=0.1 与生产一致），每模型跑 N 次，检查模型写出
的鉴权 Header 名与值形态。只做首轮调用，不执行工具、不访问外部系统。
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")

MESSAGES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "messages.json")
ATTEMPTS = int(os.environ.get("REPLAY_ATTEMPTS", "3"))
MODELS = [
    ("qwen", "qwen3.8-flash"),      # 现役 lite 模型
    ("deepseek", "deepseek-flash"),
    ("zhipu", "GLM-5.3-Flash"),
]

AGENT_HEADER = "Api-Authorize-Token"


def judge(tool_calls):
    """判定一轮 tool_calls 的鉴权头写法，返回 (结论, 明细)"""
    http_calls = [tc for tc in tool_calls if (tc.get("function") or {}).get("name") == "http_api"]
    if not http_calls:
        return "NO_TOOL_CALL", ""
    details, ok_all = [], True
    for tc in http_calls:
        args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
        headers = args.get("headers") or {}
        if isinstance(headers, str):
            try:
                headers = json.loads(headers)
            except json.JSONDecodeError:
                headers = {"<unparsable>": headers}
        keys = {k: str(v) for k, v in headers.items()}
        agent_keys = {k: v for k, v in keys.items() if k.lower() == AGENT_HEADER.lower()}
        wrong = {k: v for k, v in keys.items() if "TOKEN" in str(v).upper() and k.lower() != AGENT_HEADER.lower()}
        detail = "; ".join(f"{k}={v[:40]}" for k, v in keys.items())
        if not agent_keys or wrong:
            ok_all = False
            detail = f"[X] {detail}"
        else:
            detail = f"[OK] {detail}"
        details.append(detail)
    verdict = "PASS" if ok_all else "FAIL"
    return verdict, " | ".join(details)


async def main():
    from src.llm.gateway import llm_gateway
    from src.services.recap.tasks.external_push import _create_tool_runtime
    from src.tools.context import ToolExecutionContext

    with open(MESSAGES_PATH, encoding="utf-8") as f:
        messages = json.load(f)

    context = ToolExecutionContext(
        tenant_id="replay_test",
        session_id="replay_test",
        channel="wecom_kf",
        env_vars={"AGENT_TOKEN": "${AGENT_TOKEN}"},
    )
    registry, _, _ = _create_tool_runtime(context)
    tools = registry.get_tool_definitions()

    summary = {}
    for provider, model in MODELS:
        print(f"\n===== {provider} / {model} =====")
        results = []
        for i in range(1, ATTEMPTS + 1):
            try:
                resp = await llm_gateway.chat_direct(
                    provider, model, messages,
                    tools=tools, tool_choice="auto", temperature=0.1,
                )
            except Exception as e:
                verdict, detail = "ERROR", str(e)[:200]
            else:
                verdict, detail = judge(resp.get("tool_calls") or [])
                if verdict == "NO_TOOL_CALL":
                    detail = f"content={(resp.get('content') or '')[:120]}"
            results.append(verdict)
            print(f"  第{i}次: {verdict}  {detail}")
        summary[f"{provider}/{model}"] = results

    print("\n===== 汇总 =====")
    for k, v in summary.items():
        print(f"{k}: {v.count('PASS')}/{len(v)} PASS  {v}")


if __name__ == "__main__":
    asyncio.run(main())
