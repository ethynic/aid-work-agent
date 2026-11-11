#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看板「导出待分析包」-> 增量资料 -> 一键重跑流水线

把 data/inbox.json（看板导出的新录入记录）按项目写回 data/sources/<项目名>/，
然后可选串联 extract_sources.py → analyze.py → build_dashboard.py，完成增量闭环。

用法：
  python scripts/ingest_inbox.py --root <技能目录> --as-of 2026-11-10 --chain
  python scripts/ingest_inbox.py --root <技能目录> --chain --mode api   # 需 PPT_LLM_API_KEY
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

TYPE_LABEL = {"report": "汇报", "communication": "沟通", "milestone": "里程碑", "acceptance": "验收", "other": "资料"}
ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe(s: str, limit: int = 60) -> str:
    s = ILLEGAL.sub("_", str(s or "")).strip(" ._")
    return (s[:limit] or "未命名")


def project_dir(root: Path, name: str) -> Path:
    """优先对齐已有项目目录名，避免同一个项目被拆成两个目录。"""
    base = root / "data" / "sources"
    base.mkdir(parents=True, exist_ok=True)
    if (base / name).is_dir():
        return base / name
    for d in base.iterdir():
        if d.is_dir() and (d.name in name or name in d.name):
            return d
    d = base / safe(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_inbox(path: Path) -> list[dict]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(obj, list):
        return obj
    items = obj.get("items", [])
    if not isinstance(items, list):
        raise SystemExit("[ingest] inbox.json 格式不正确：应为数组或含 items 数组的对象")
    return items


def build_text(item: dict) -> str:
    lines = [
        f"# {item.get('title') or TYPE_LABEL.get(item.get('type'), '资料')}",
        "",
        f"- 项目：{item.get('project', '')}",
        f"- 资料类型：{TYPE_LABEL.get(item.get('type'), '资料')}",
        f"- 记录日期：{item.get('date', '')}",
        f"- 来源/渠道：{item.get('source', '')}",
        f"- 录入时间：{item.get('created_at', '')}",
        f"- 来源方式：看板增量录入",
        "",
        "## 正文",
        "",
        (item.get("content") or "").strip() or "[未填写正文，请查看随附文件]",
        "",
    ]
    files = item.get("files") or []
    binary = item.get("binary_files") or []
    if files:
        lines += ["## 随附文件", ""] + [f"- {f.get('name') if isinstance(f, dict) else f}" for f in files] + [""]
    if binary:
        lines += ["## 需人工补入的二进制文件", ""]
        lines += ["以下文件仅登记了文件名，浏览器无法传出内容，请把原文件放到本项目目录后重跑抽取：", ""]
        lines += [f"- {n}" for n in binary] + [""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="导入看板待分析包并可选串联重跑流水线")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                    help="数据目录（可写）：data/ 与产物都写在这里；技能目录只读时必须指向独立工作目录")
    ap.add_argument("--skill-root", default=str(Path(__file__).resolve().parent.parent),
                    help="技能只读资源目录（scripts/ 等），一般保持默认")
    ap.add_argument("--as-of", default=dt.date.today().isoformat())
    ap.add_argument("--inbox", default="", help="inbox.json 路径，默认 <root>/data/inbox.json")
    ap.add_argument("--chain", action="store_true", help="导入后自动串联抽取/分析/构建")
    ap.add_argument("--mode", choices=["packet", "api"], default="packet")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    skill_root = Path(args.skill_root).resolve()
    inbox = Path(args.inbox).resolve() if args.inbox else root / "data" / "inbox.json"
    scripts = skill_root / "scripts"
    (root / "logs").mkdir(parents=True, exist_ok=True)

    if not inbox.exists():
        print(f"[ingest] 未找到 {inbox}。请先在看板点击「导出待分析包」，把下载的 inbox.json 放到 data/ 下。")
        return 0
    items = load_inbox(inbox)
    if not items:
        print("[ingest] inbox.json 里没有记录，无需处理。")
        return 0

    written: list[str] = []
    for i, item in enumerate(items, 1):
        name = str(item.get("project") or "_未归类")
        d = project_dir(root, name)
        stem = f"inbox_{safe(item.get('date') or 'nodate', 10)}_{safe(item.get('type') or 'other', 12)}_{i:02d}_{safe(item.get('title') or '', 36)}"
        f = d / f"{stem}.md"
        f.write_text(build_text(item), encoding="utf-8")
        written.append(str(f.relative_to(root)).replace("\\", "/"))
        for n in (item.get("binary_files") or []):
            print(f"[ingest] 待补二进制文件 -> data/sources/{d.name}/ <- {n}")

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    processed = root / "data" / f"inbox_processed_{stamp}.json"
    inbox.replace(processed)

    log = ["# 增量录入导入报告", "", f"- 时间：{dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
           f"- 基准日：{args.as_of}", f"- 导入记录数：{len(items)}", f"- 原待分析包归档：{processed.name}", "",
           "## 生成的文件", ""] + [f"- `{w}`" for w in written] + [""]
    (root / "logs" / "inbox_ingest.md").write_text("\n".join(log) + "\n", encoding="utf-8")
    print(f"[ingest] {len(items)} 条记录已写入 {len(set(Path(w).parent for w in written))} 个项目目录，原文件归档为 {processed.name}")

    if args.chain:
        py = sys.executable
        steps = [
            [py, str(scripts / "extract_sources.py"), "--root", str(root), "--as-of", args.as_of],
            [py, str(scripts / "analyze.py"), "--root", str(root), "--as-of", args.as_of, "--mode", args.mode],
        ]
        if args.mode == "api":
            steps.append([py, str(scripts / "build_dashboard.py"), "--root", str(root), "--as-of", args.as_of])
        for cmd in steps:
            print("[ingest] > " + " ".join(f'"{c}"' if " " in c else c for c in cmd[1:]))
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.stdout:
                print(r.stdout.rstrip())
            if r.returncode != 0:
                print(f"[ingest] 步骤失败（退出码 {r.returncode}）：\n{r.stderr}")
                return r.returncode
        if args.mode == "packet":
            print("[ingest] 已生成 data/analysis_packet.md。由智能体研判并写入 data/projects.json 后，")
            print("[ingest] 再运行：python scripts/build_dashboard.py --root <技能目录>  即可刷新看板。")
        else:
            print("[ingest] 看板已刷新：dashboard/index.html")
    else:
        print("[ingest] 加 --chain 可自动串联抽取/分析/构建。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
