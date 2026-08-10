"""本地完整运行日志 —— 把每条 NDJSON 事件追加到滚动日志文件。

文件：%LOCALAPPDATA%\\AidWorkAgent\\association-client\\logs\\app.log
每行：`时间 session=<sid> <事件JSON>`。超 MAX_BYTES 滚动保留 1 个备份（app.log.1）。

设计：所有操作吞异常——日志绝不能影响采集主流程。供 GUI「导出诊断包」与人工排障读取。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()
_MAX_BYTES = 5 * 1024 * 1024  # 5MB 触发滚动
_session_id = "unknown"


def _log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "AidWorkAgent" / "association-client" / "logs"


def _log_file() -> Path:
    return _log_dir() / "app.log"


def _backup_file() -> Path:
    return _log_dir() / "app.log.1"


def set_session_id(sid: str) -> None:
    """collect 开始时注入本次 run 的 session_id（写进每行便于过滤）。"""
    global _session_id
    _session_id = sid or "unknown"


def append_event(event: dict) -> None:
    """把一条（已脱敏的）事件追加到本地日志。吞一切异常。"""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} session={_session_id} {json.dumps(event, ensure_ascii=False, default=str)}\n"
        with _LOCK:
            path = _log_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if path.exists() and path.stat().st_size > _MAX_BYTES:
                    bak = _backup_file()
                    if bak.exists():
                        bak.unlink()
                    path.rename(bak)
            except Exception:
                pass
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
