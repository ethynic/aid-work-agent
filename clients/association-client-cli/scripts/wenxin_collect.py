"""文心联网采集 stdin/stdout IO 入口（开发期 probe 用）。

采集逻辑单点在 runtime/wenxin_collector.py（被 CLI 主进程进程内 await）。
本文件保留 stdin JSON → stdout 单行 JSON 的 IO 协议，供 scripts/probe-
wenxin-batch.py 等开发期工具独立运行。CLI 主流程不再 spawn 本脚本。

IO 协议：
- stdin : 单个 JSON {"association_name": "..."}
- stdout: 单行压缩 JSON {"ok": true, "answer": "...", "note": "..."}
- 失败 : stderr 只写 wenxin_collect_failed:{ExceptionType}（不写原文/证据，防泄漏）
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
from pathlib import Path

# 独立运行 / importlib 加载时，把 clients/association-client-cli/ 加到 path，
# 使 runtime.wenxin_collector 可 import（CLI 主进程已自带此路径，无需处理）。
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from runtime.wenxin_collector import (  # noqa: E402
    CDP_URL,
    MAX_TOTAL_SECONDS,
    QUERY_TMPL,
    START_WAIT_SECONDS,
    STABLE_TICKS,
    collect_one,
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        payload = json.loads(sys.stdin.read())
        name = str(payload.get("association_name", "")).strip()
        if not name:
            sys.stderr.write("wenxin_collect_failed:ValueError\n")
            return 2
        # 隔离 playwright 内部日志，保证 CLI 协议 stdout 只有一行 JSON
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = asyncio.run(collect_one(name))
        sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
        return 0
    except Exception as exc:
        sys.stderr.write(f"wenxin_collect_failed:{type(exc).__name__}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
