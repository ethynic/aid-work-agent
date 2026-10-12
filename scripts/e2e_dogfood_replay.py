# -*- coding: utf-8 -*-
"""
data-analysis 对话重放实测脚本（在 aid-agent-api1 容器内运行）

目的：直驱重放一局数据分析对话（六国狗粮出口销售 Q2 分析），逐事件记录
LLM 调用、工具调用、tokens 与耗时到 JSONL，用于 dogfood 观察子智能体
端到端真实行为与成本画像。结构复用 e2e_spec2quote_test.py 的 run_turn
事件循环，不含产物 validate 部分。

用法：
    容器重放（环境完全同构，验收信号以此为准）：
        docker exec aid-agent-api1 python /app/scripts/e2e_dogfood_replay.py \
            [--max-continuations 3] [--max-seconds 3600]
    本地重放（快速迭代：本地代码 + agent1(243) 仿真库，SSH 隧道，模型配置强制对齐）：
        bash scripts/e2e_dogfood_replay_local.sh [--max-continuations 3] [--max-seconds 3600]

输出：
  - 控制台逐轮进度（轮号 / 模型 / tokens / 工具调用）
  - 事件级明细 JSONL：log/dogfood_replay_<ts>.jsonl（逐事件即写即刷，
    进程被超时硬杀也不丢已发生的事件）
  - 结束无条件汇总单行：REPLAY_RESULT {...}（stdout 与 JSONL 各一份）；
    异常路径打印 REPLAY_FAILED 并 exit 1，正常 exit 0
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 由脚本位置推导项目根：容器内 /app/scripts/.. -> /app；本地 <repo>/scripts/.. -> <repo>
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 容器靠 PYTHONPATH=/app 找到 src 包，本地裸跑（python scripts/xxx.py）没有——统一补上
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SUBAGENT = "data-analysis"
TENANT_ID = "tenant_c148f4efb4dc"
TIMEOUT_MSG = "I apologize, but the task is taking too long"

USER_PROMPT = (
    "请帮我整体汇总和分析六个国家4-6月份的狗粮出口销售和订单数据，重点分析第二季度整体销售业绩情况"
    "以及每个月的销售业绩对比，找到每个国家的优势渠道和劣势渠道，进行销售人员排名，找到销冠标杆和"
    "待重点培训进步的员工，并为我下个月的销售提供建议。请输出分析总表、透视图和直接可以用于给领导进行汇报的文件。"
)

# 模块级状态：异常路径也要能打印部分统计
STATS = {"llm_calls": 0, "tool_calls": 0, "prompt_tokens": 0,
         "completion_tokens": 0, "llm_ms": 0, "tool_counts": {}}
SESSION_ID = ""
START_TS = time.time()
FINAL_TEXT = ""


def _jsonl_write(jsonl, payload):
    """事件即写即刷：硬杀（SIGTERM/SIGKILL、启动器超时）也不丢已发生的事件证据。

    此前 write 走缓冲、仅正常 close 才落盘：重放被启动器超时硬杀时
    整局事件全部丢失，信号扫描读到 0 字节 JSONL，误判为"重放未执行任何
    analyze_data 调用、内层工具打点全零"（2026-10-12 17:06 场次实测：
    /app/log/dogfood_replay_20261012_170619.jsonl 0 字节，而应用日志里
    有 75 次内层工具调用）。
    """
    jsonl.write(json.dumps(payload, ensure_ascii=False) + "\n")
    jsonl.flush()


async def run_turn(agent, session_id, text, jsonl, stats, turn_label):
    """跑一轮对话流，返回最终回复文本"""
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
            _jsonl_write(jsonl, {"t": time.time(), "event": "llm_call", "turn": turn_label,
                                 "round": stats["llm_calls"], "usage": usage,
                                 "duration_ms": ev.get("duration_ms", 0)})
        elif etype == "tool_result":
            n_tool_results += 1
            stats["tool_calls"] += 1
            # displayName 是给人看的（如「智能数据分析「…」」），机器判读要原始工具名
            # （如 analyze_data）——此前只记 displayName，JSONL 里按工具名计数全为零
            name = ev.get("displayName") or ev.get("toolName") or "?"
            tool_name = ev.get("toolName") or ev.get("name") or ""
            if tool_name:
                stats["tool_counts"][tool_name] = stats["tool_counts"].get(tool_name, 0) + 1
            ok = "ok" if ev.get("success", True) else "FAIL"
            head = ""
            result = ev.get("result")
            if isinstance(result, dict):
                head = str(result.get("stdout") or result.get("content") or "")[:80]
            print(f"      tool[{n_tool_results}] {name} ({ok}) {head}".rstrip(), flush=True)
            _jsonl_write(jsonl, {"t": time.time(), "event": "tool_result", "turn": turn_label,
                                 "tool": name, "tool_name": tool_name,
                                 "success": ev.get("success", True),
                                 "result_head": head})
        elif etype == "response":
            data = ev.get("data", "")
            if data:
                final_text = data if not final_text else final_text
                print(f"      response: {data[:120]}", flush=True)
            _jsonl_write(jsonl, {"t": time.time(), "event": "response", "turn": turn_label,
                                 "data": str(data)[:500]})
    # 轮级检查点：即便运行中途被硬杀、到不了最终 REPLAY_RESULT，
    # JSONL 里也留有每轮结束时的累计进度，stdout .log 里也有一行带真实
    # 计数的部分汇总（partial=true），扫描侧可直接判读
    _jsonl_write(jsonl, {"t": time.time(), "event": "turn_stats", "turn": turn_label,
                         "llm_calls": stats["llm_calls"], "tool_calls": stats["tool_calls"],
                         "prompt_tokens": stats["prompt_tokens"],
                         "completion_tokens": stats["completion_tokens"]})
    print_replay_result("", partial=True, turn=turn_label)
    return final_text


def _install_inner_tool_sink(jsonl):
    """把内层 AnalysisAgent 的逐工具打点（[AnalysisAgent][TOOL] <method>）捕进 JSONL。

    内层 agent 跑在 analyze_data 工具执行内部，外层事件循环只能看到外层工具；
    此前 JSONL 里没有任何内层调用记录，扫描按工具名统计时
    describe/append/aggregate 等内层计数全为零（2026-10-12 a2 场次根因之一）。
    返回 sink id，便于结束时移除。
    """
    from loguru import logger

    marker = "[AnalysisAgent][TOOL] "

    def _sink(message):
        try:
            tool = str(message.record.get("message", "")).split(marker, 1)[1].strip()
        except Exception:
            return
        if not tool:
            return
        STATS["tool_counts"][tool] = STATS["tool_counts"].get(tool, 0) + 1
        _jsonl_write(jsonl, {"t": time.time(), "event": "inner_tool", "tool": tool})

    return logger.add(_sink, filter=lambda r: marker in r["message"], level="INFO")


def print_replay_result(final_head, jsonl=None, partial=False, turn=None):
    """无条件打印单行汇总（成功与失败都要）；同时写入 JSONL 明细文件。

    stdout 管道可能随进程被硬杀一起丢失，JSONL 是扫描侧更可靠的证据源。
    partial=True 时为轮级部分汇总（final_head 置空、带 turn 号）：
    启动器超时硬杀后 .log 里至少留有带真实计数的可解析汇总行，
    避免扫描侧误判"重放未执行任何 analyze_data 调用、打点全零"。
    """
    summary = {
        "session_id": SESSION_ID,
        "llm_calls": STATS["llm_calls"],
        "tool_calls": STATS["tool_calls"],
        "prompt_tokens": STATS["prompt_tokens"],
        "completion_tokens": STATS["completion_tokens"],
        "duration_s": round(time.time() - START_TS, 1),
        "final_head": str(final_head or "")[:300],
        # 机器判读用：外层按原始工具名 + 内层逐工具计数（含 analyze_data 本身）
        "analyze_data_calls": STATS["tool_counts"].get("analyze_data", 0),
        "tool_counts": dict(STATS["tool_counts"]),
    }
    if partial:
        summary["partial"] = True
        summary["turn"] = turn
        summary["final_head"] = ""
    line = "REPLAY_RESULT " + json.dumps(summary, ensure_ascii=False)
    print(line, flush=True)
    if jsonl is not None and not jsonl.closed:
        try:
            jsonl.write(line + "\n")
            jsonl.flush()
        except Exception:
            pass


def _map_app_path(p):
    """容器内绝对路径 /app/storage/... -> 本地镜像 <repo>/storage/...（仅本地重放生效）"""
    s = str(p)
    if s.startswith("/app/storage/"):
        return str(PROJECT_ROOT / "storage" / s[len("/app/storage/"):])
    return p


def _install_local_path_shim():
    """本地重放（非容器）时把 /app/storage 路径映射到仓库镜像目录。

    agent1 库中表元数据的 source.file_path 是容器内绝对路径（/app/storage/...），
    本地进程直接读会 fail-loud 报"源数据不存在"（load_table 显式失败设计）。
    数据源文件已一次性从 243 拉到 <repo>/storage/tenants/ 镜像；在读取边界
    （os.path.exists / pd.read_excel / pd.read_csv）做前缀映射，不改产品代码。
    容器内跑（存在 /app/storage）时不映射，行为与原容器重放完全一致。
    """
    if os.path.isdir("/app/storage"):
        return
    import pandas as pd

    orig_exists = os.path.exists

    def _exists(path, *_a, **_k):
        return orig_exists(_map_app_path(path))

    os.path.exists = _exists

    orig_read_excel = pd.read_excel

    def _read_excel(path_or_buf, *a, **k):
        return orig_read_excel(_map_app_path(path_or_buf), *a, **k)

    pd.read_excel = _read_excel

    orig_read_csv = pd.read_csv

    def _read_csv(path_or_buf, *a, **k):
        return orig_read_csv(_map_app_path(path_or_buf), *a, **k)

    pd.read_csv = _read_csv
    print(f"本地重放：/app/storage 路径已映射到 {PROJECT_ROOT / 'storage'}", flush=True)


async def main():
    global SESSION_ID, FINAL_TEXT

    ap = argparse.ArgumentParser()
    ap.add_argument("--max-continuations", type=int, default=3,
                    help="命中轮数上限后自动「继续」的最大次数")
    ap.add_argument("--max-seconds", type=int, default=3600, help="总时长硬上限")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = PROJECT_ROOT / "log" / f"dogfood_replay_{ts}.jsonl"
    log_path.parent.mkdir(exist_ok=True)
    jsonl = open(log_path, "w", encoding="utf-8")
    sink_id = _install_inner_tool_sink(jsonl)

    # 先触发 .env 装载（settings 顶层 load_dotenv），防呆与配置打印才拿得到生效值
    import src.config.settings  # noqa: F401

    db_url = os.environ.get("DATABASE_URL", "")
    if "aid_work_agent1" not in db_url:
        print("REPLAY_FAILED DATABASE_URL 未指向 aid_work_agent1 仿真库；重放会写 "
              "chat_records/billing，禁止误写其他库。本地经隧道跑法见 "
              "scripts/e2e_dogfood_replay_local.sh", file=sys.stderr)
        print_replay_result("", jsonl=jsonl)
        jsonl.close()
        return 1

    print("生效 LLM 配置：provider=%s deepseek=%s qwen=%s zhipu=%s" % (
        os.environ.get("LLM_PROVIDER", ""), os.environ.get("DEEPSEEK_MODEL_CODE", ""),
        os.environ.get("QWEN_MODEL_CODE", ""), os.environ.get("ZHIPU_MODEL_CODE", "")),
        flush=True)

    _install_local_path_shim()

    from src.subagents.factory import AgentFactory

    SESSION_ID = f"session_replay_{ts}"
    agent = AgentFactory.create_standalone_subagent(SUBAGENT, SESSION_ID, tenant_id=TENANT_ID)
    if agent is None:
        print(f"子智能体 {SUBAGENT} 不存在", file=sys.stderr)
        print_replay_result("", jsonl=jsonl)
        jsonl.close()
        print("REPLAY_FAILED 子智能体 data-analysis 不存在（检查 tenant_c148f4efb4dc 注册表）",
              flush=True)
        return 1
    max_iter = agent.subagent_config.get_max_iterations()
    print(f"subagent={SUBAGENT} tenant={TENANT_ID} session={SESSION_ID} "
          f"max_iterations={max_iter} log={log_path}", flush=True)

    t0 = time.time()
    turn_label = 0
    final = await run_turn(agent, SESSION_ID, USER_PROMPT, jsonl, STATS, turn_label)
    FINAL_TEXT = final

    continuations = 0
    # 命中轮数上限或最终回复为空（输出截断等静默失败）都续跑
    while (TIMEOUT_MSG in final or not final.strip()) and continuations < args.max_continuations:
        if time.time() - t0 > args.max_seconds:
            print("超过 --max-seconds，停止续跑", flush=True)
            break
        continuations += 1
        turn_label += 1
        print(f"\n=== 命中轮数上限，自动续跑第 {continuations} 次（请继续完成任务）===",
              flush=True)
        final = await run_turn(agent, SESSION_ID, "继续完成任务", jsonl, STATS, turn_label)
        FINAL_TEXT = final

    print("\n" + "=" * 72)
    print(f"总 LLM 调用（=迭代轮数）: {STATS['llm_calls']}  上限 {max_iter} "
          f"(自动续跑 {continuations} 次)")
    print(f"总工具调用: {STATS['tool_calls']}")
    print(f"tokens: input={STATS['prompt_tokens']:,} output={STATS['completion_tokens']:,} "
          f"合计={STATS['prompt_tokens'] + STATS['completion_tokens']:,}")
    print(f"LLM 累计耗时: {STATS['llm_ms'] / 1000:.0f}s | 任务总耗时: {time.time() - t0:.0f}s")
    print(f"最终回复: {FINAL_TEXT[:300]}")
    print(f"明细日志: {log_path}")

    # 汇总也写入 JSONL（stdout 管道可能随进程被硬杀一起丢，JSONL 是更可靠证据源），再关闭
    print_replay_result(FINAL_TEXT, jsonl=jsonl)
    jsonl.close()
    from loguru import logger
    logger.remove(sink_id)
    return 0


if __name__ == "__main__":
    START_TS = time.time()

    def _on_terminate(signum, _frame):
        """SIGTERM（docker stop / 启动器超时宽限）也要落汇总再退。

        SIGKILL 无法捕获——由 _jsonl_write 即写即刷保底，事件不丢。
        """
        print_replay_result(FINAL_TEXT)
        print(f"REPLAY_FAILED signal={signum}", flush=True)
        sys.exit(1)

    try:
        import signal
        signal.signal(signal.SIGTERM, _on_terminate)
    except (ValueError, OSError, ImportError):
        pass  # 非 main 线程或平台不支持时跳过，不影响正常路径

    try:
        _rc = asyncio.run(main())
    except KeyboardInterrupt:
        print_replay_result(FINAL_TEXT)
        print("REPLAY_FAILED KeyboardInterrupt（手动中断）", flush=True)
        _rc = 1
    except Exception as e:  # noqa: BLE001 异常路径也要有可解析的汇总行
        print_replay_result(FINAL_TEXT)
        print(f"REPLAY_FAILED {type(e).__name__}: {str(e)[:500]}", flush=True)
        _rc = 1
    sys.exit(_rc)
