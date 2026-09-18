# -*- coding: utf-8 -*-
"""
spec-to-quotation-list 端到端实测脚本（在 aid-agent-api 容器内运行）

目的：实测 building-supply-chain 子智能体处理样例 spec book 的真实
LLM 调用次数（= 迭代轮数）、工具调用数、tokens 与耗时，回答
「context.max_iterations: 60 是否够用」。命中轮数上限时自动发送
「继续」续跑（模拟用户手动续作），统计完成整个任务的真实总需求。

用法：
    docker exec aid-agent-api python /app/scripts/e2e_spec2quote_test.py \
        [--max-continuations 3] [--max-seconds 1800]

输出：
  - 控制台逐轮进度（轮号 / 模型 / tokens / 工具调用）
  - 事件级明细 JSONL：log/spec2quote_e2e_<ts>.jsonl
  - 结束汇总：总轮数 / 总工具调用 / tokens / 耗时 / 续跑次数，
    并对生成目录自动跑 validate.py
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = "/app"
PDF_PATH = "/app/ext/建筑行业方案清单生成器/Design Specs Book_16JUN2026.pdf"
SUBAGENT = "building-supply-chain"
TENANT_ID = "tenant_e2e_spec2quote"
TIMEOUT_MSG = "I apologize, but the task is taking too long"

USER_PROMPT = (
    "根据文档生成Excel报价清单\n\n"
    "【已上传文件路径】\n"
    f"  - Design Specs Book_16JUN2026.pdf: {PDF_PATH}\n"
    "请使用上述路径读取文件内容。"
)


async def run_turn(agent, session_id, text, jsonl, stats, turn_label):
    """跑一轮对话流，返回 (最终回复文本, 是否命中轮数上限)"""
    final_text = ""
    n_tool_results = 0
    async for ev in agent.process_message(text, session_id):
        etype = ev.get("type")
        if etype == "llm_call":
            usage = ev.get("usage") or {}
            pt = usage.get("prompt_tokens", 0)
            ct = usage.get("completion_tokens", 0)
            stats["llm_calls"] += 1
            stats["prompt_tokens"] += pt
            stats["completion_tokens"] += ct
            stats["llm_ms"] += ev.get("duration_ms", 0)
            print(f"  [round {stats['llm_calls']:>3}] {ev.get('model', '?'):<16} "
                  f"in={pt:>6} out={ct:>5} 累计in={stats['prompt_tokens']:>8} "
                  f"累计out={stats['completion_tokens']:>6} "
                  f"耗时{ev.get('duration_ms', 0) / 1000:.1f}s", flush=True)
            jsonl.write(json.dumps({"t": time.time(), "event": "llm_call", "turn": turn_label,
                                    "round": stats["llm_calls"], "usage": usage,
                                    "duration_ms": ev.get("duration_ms", 0)},
                                   ensure_ascii=False) + "\n")
        elif etype == "tool_result":
            n_tool_results += 1
            stats["tool_calls"] += 1
            name = ev.get("displayName") or ev.get("toolName") or "?"
            ok = "ok" if ev.get("success", True) else "FAIL"
            head = ""
            result = ev.get("result")
            if isinstance(result, dict):
                head = str(result.get("stdout") or result.get("content") or "")[:80]
            print(f"      tool[{n_tool_results}] {name} ({ok}) {head}".rstrip(), flush=True)
            jsonl.write(json.dumps({"t": time.time(), "event": "tool_result", "turn": turn_label,
                                    "tool": name, "success": ev.get("success", True),
                                    "result_head": head}, ensure_ascii=False) + "\n")
        elif etype == "response":
            data = ev.get("data", "")
            if data:
                final_text = data if not final_text else final_text
                print(f"      response: {data[:120]}", flush=True)
            jsonl.write(json.dumps({"t": time.time(), "event": "response", "turn": turn_label,
                                    "data": str(data)[:500]}, ensure_ascii=False) + "\n")
    return final_text


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-continuations", type=int, default=3,
                    help="命中轮数上限后自动「继续」的最大次数")
    ap.add_argument("--max-seconds", type=int, default=1800, help="总时长硬上限")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = Path(PROJECT_ROOT) / "log" / f"spec2quote_e2e_{ts}.jsonl"
    log_path.parent.mkdir(exist_ok=True)
    jsonl = open(log_path, "w", encoding="utf-8")

    from src.subagents.factory import AgentFactory

    session_id = f"session_spec2quote_e2e_{ts}"
    agent = AgentFactory.create_standalone_subagent(SUBAGENT, session_id, tenant_id=TENANT_ID)
    if agent is None:
        print(f"子智能体 {SUBAGENT} 不存在", file=sys.stderr)
        sys.exit(2)
    max_iter = agent.subagent_config.get_max_iterations()
    print(f"subagent={SUBAGENT} session={session_id} "
          f"max_iterations={max_iter} log={log_path}", flush=True)

    stats = {"llm_calls": 0, "tool_calls": 0, "prompt_tokens": 0,
             "completion_tokens": 0, "llm_ms": 0}
    t0 = time.time()
    turn_label = 0
    final = await run_turn(agent, session_id, USER_PROMPT, jsonl, stats, turn_label)

    continuations = 0
    # 命中轮数上限或最终回复为空（输出截断等静默失败）都续跑
    while (TIMEOUT_MSG in final or not final.strip()) and continuations < args.max_continuations:
        if time.time() - t0 > args.max_seconds:
            print("超过 --max-seconds，停止续跑", flush=True)
            break
        continuations += 1
        turn_label += 1
        print(f"\n=== 命中轮数上限，自动续跑第 {continuations} 次（请继续完成任务）===", flush=True)
        final = await run_turn(agent, session_id, "继续完成任务", jsonl, stats, turn_label)

    wall = time.time() - t0
    jsonl.close()

    print("\n" + "=" * 72)
    print(f"总 LLM 调用（=迭代轮数）: {stats['llm_calls']}  上限 {max_iter} "
          f"(自动续跑 {continuations} 次)")
    print(f"总工具调用: {stats['tool_calls']}")
    print(f"tokens: input={stats['prompt_tokens']:,} output={stats['completion_tokens']:,} "
          f"合计={stats['prompt_tokens'] + stats['completion_tokens']:,}")
    print(f"LLM 累计耗时: {stats['llm_ms'] / 1000:.0f}s | 任务总耗时: {wall:.0f}s")
    print(f"最终回复: {final[:300]}")
    print(f"明细日志: {log_path}")

    # 对最新生成的 spec2quote 目录跑 validate.py
    tmp_root = Path(PROJECT_ROOT) / "storage" / "tmp"
    out_dirs = sorted(tmp_root.glob("spec2quote_*"), key=lambda p: p.stat().st_mtime)
    xlsx_dirs = [d for d in out_dirs if list(d.glob("*.xlsx"))]
    if not xlsx_dirs:
        print("\n未发现生成的 xlsx（任务可能未完成）")
        sys.exit(1)
    out_dir = xlsx_dirs[-1]
    print(f"\n生成目录: {out_dir}")
    v = subprocess_run_validate(out_dir)
    sys.exit(0 if v else 1)


def subprocess_run_validate(out_dir):
    import subprocess
    r = subprocess.run(
        [sys.executable, str(Path(PROJECT_ROOT) / "src/skills/spec-to-quotation-list-1.0.0/scripts/validate.py"),
         str(out_dir), "--max-mb", "20"],
        capture_output=True, text=True)
    print(r.stdout[-2500:])
    if r.returncode != 0:
        print(r.stderr[-500:])
    return r.returncode == 0


if __name__ == "__main__":
    asyncio.run(main())
