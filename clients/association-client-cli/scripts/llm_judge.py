"""LLM 证据判断 stdin/stdout IO 入口（开发期 probe 用）。

判断逻辑单点在 runtime/llm_judge.py（被 CLI llm-judge 子命令进程内 await）。
本文件保留 stdin JSON → stdout 单行 JSON 的 IO 协议，供开发期独立运行
（python scripts/llm_judge.py）。CLI 主流程（打包 exe）不再 spawn 本脚本——
改走 main.py llm-judge 子命令 + ProxyLLMGateway，避免依赖客户机 python / 本地 key。

IO 协议：
- stdin : 单个 JSON {"association_name":"...","person_name":"...","text":"..."}
- stdout: 单行压缩 JSON {"matched":bool,...,"token_usage":{...}}
- 失败 : stderr 只写 judge_failed:{ExceptionType}（不写原文/证据，防泄漏）
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
from pathlib import Path

# 独立运行 / importlib 加载时，把 clients/association-client-cli/ 加到 path，
# 使 runtime.llm_judge 可 import（CLI 主进程已自带此路径，无需处理）。
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from runtime.llm_judge import JUDGE_MAX_TOKENS, JudgeUsageError, run_judge  # noqa: E402,F401


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        payload = json.loads(sys.stdin.read())
        # 项目 Gateway/Provider 可能通过 loguru 或 print 输出内部配置、Key 后缀，
        # 隔离这些输出，保证 CLI 协议只有一行 JSON，stderr 也不携带证据。
        # gateway 省略 → run_judge 回退到 src.llm.gateway.llm_gateway（开发调试用）。
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
