"""协会采集证据日志——每个协会每天一个文件，落盘链路上所有原始文本。

文件：%LOCALAPPDATA%\\AidWorkAgent\\association-client\\logs\\evidence\\{协会名}_{YYYY-MM-DD}.log

记录内容（按采集链路）：
- 文心：发送的问句、文心输出的回答原文
- 微信搜一搜：每次搜索的查询词、列表页复制文本、详情页复制文本（含 OCR 文本）

与 app.log 的分工：app.log 记脱敏进度/审计事件（会滚动清理）；证据日志保存
**未脱敏原文**，仅供客户机本地排障（与 result.xlsx 同信任域），不做滚动、
不主动清理。所有操作吞异常——证据日志绝不能影响采集主流程。
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime
from pathlib import Path

_LOCK = threading.Lock()

# Windows 文件名非法字符 + 控制字符，统一替换成下划线
_ILLEGAL_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _evidence_dir() -> Path:
    base = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("APPDATA")
        or str(Path.home())
    )
    return Path(base) / "AidWorkAgent" / "association-client" / "logs" / "evidence"


def _evidence_file(association_name: str) -> Path:
    """文件名：{协会名}_{YYYY-MM-DD}.log（协会名去非法字符、压缩空白）。"""
    safe_name = _ILLEGAL_FILENAME_RE.sub("_", association_name.strip())
    safe_name = re.sub(r"\s+", "", safe_name) or "未知协会"
    date_part = datetime.now().strftime("%Y-%m-%d")
    return _evidence_dir() / f"{safe_name}_{date_part}.log"


def evidence_log(association_name: str, kind: str, text: object) -> None:
    """追加一条证据到该协会当天的日志文件。

    Args:
        association_name: 协会名（决定文件名）
        kind: 片段类型（如「文心·问句」「微信搜手机·详情文本·第2条」）
        text: 原文内容（str/None 均可；None 记为 <空>）
    """
    try:
        content = str(text) if text is not None else "<空>"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        block = f"\n===== [{ts}] {kind} =====\n{content}\n"
        with _LOCK:
            path = _evidence_file(association_name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as stream:
                stream.write(block)
    except Exception:
        # 证据日志失败不能影响采集主流程
        pass
