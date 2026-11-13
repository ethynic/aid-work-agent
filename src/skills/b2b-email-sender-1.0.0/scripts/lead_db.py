# -*- coding: utf-8 -*-
"""b2b-email-sender 技能的 lead_db.py 薄壳转发。

权威实现位于兄弟技能目录 ../b2b-lead-research-1.0.0/scripts/lead_db.py
（同仓库同部署，目录互为兄弟）。本文件只做加载转发，保证：
1. 模块导入语义：`from lead_db import get_identity, ...`（reply_monitor.py /
   fetch_opens.py / send_emails.py 使用）
2. 命令行语义：`python lead_db.py <subcommand> ...` 直跑
禁止在本文件中实现任何业务逻辑；schema/子命令变更只改权威副本。
"""
import runpy
import sys
from pathlib import Path

_CANONICAL = (
    Path(__file__).resolve().parents[2]
    / "b2b-lead-research-1.0.0"
    / "scripts"
    / "lead_db.py"
)

if not _CANONICAL.exists():
    raise SystemExit(
        f"[错误] 找不到权威 lead_db.py：{_CANONICAL}\n"
        "b2b-email-sender 依赖 b2b-lead-research 技能的 lead_db.py（同仓库部署）。"
    )

_canon_globals = runpy.run_path(str(_CANONICAL), run_name="b2b_lead_db_canonical")
for _name, _value in _canon_globals.items():
    if not _name.startswith("__"):
        globals()[_name] = _value


if __name__ == "__main__":
    sys.exit(_canon_globals["main"]())
