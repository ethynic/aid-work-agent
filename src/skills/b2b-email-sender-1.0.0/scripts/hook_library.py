# -*- coding: utf-8 -*-
"""b2b-email-sender 技能的 hook_library.py 薄壳转发。

权威实现位于兄弟技能目录 ../b2b-lead-research-1.0.0/scripts/hook_library.py
（同仓库同部署，目录互为兄弟）。发送后核验阶段需要在本技能内执行
`hook_library.py record` 回写钩子战绩，本文件保证命令行直跑语义。
禁止在本文件中实现任何业务逻辑；钩子库变更只改权威副本。
"""
import runpy
import sys
from pathlib import Path

_CANONICAL = (
    Path(__file__).resolve().parents[2]
    / "b2b-lead-research-1.0.0"
    / "scripts"
    / "hook_library.py"
)

if not _CANONICAL.exists():
    raise SystemExit(
        f"[错误] 找不到权威 hook_library.py：{_CANONICAL}\n"
        "b2b-email-sender 依赖 b2b-lead-research 技能的 hook_library.py（同仓库部署）。"
    )

_canon_globals = runpy.run_path(str(_CANONICAL), run_name="b2b_hook_library_canonical")
for _name, _value in _canon_globals.items():
    if not _name.startswith("__"):
        globals()[_name] = _value


if __name__ == "__main__":
    sys.exit(_canon_globals["main"]())
