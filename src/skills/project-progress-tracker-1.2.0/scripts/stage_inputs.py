#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把会话上传文件 / 知识库检索正文归位到 <root>/data/sources/<项目名>/

对话式交付没有「手工把文件放进目录」这一步：上传文件散落在会话上传路径、
知识库检索结果只在工具返回文本里。本脚本代劳归组落盘，之后走与本地文件
完全相同的抽取流水线。原始文件只读复制，绝不改写。

用法：
  # 1) 会话上传文件归组（--file 可重复）
  python scripts/stage_inputs.py --root <数据目录> --project <项目名> \
      --file <上传路径1> --file <上传路径2>

  # 2) 知识库检索正文落盘为 kb_*.md（符合 references/knowledge-sources.md 落盘规范）
  python scripts/stage_inputs.py --root <数据目录> --project <项目名> --kb \
      --title <文档标题> --text-file <检索正文路径> \
      [--date <YYYY-MM-DD>] [--origin <原始文档链接>]
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import sys
from pathlib import Path

ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe(s: str, limit: int = 60) -> str:
    s = ILLEGAL.sub("_", str(s or "")).strip(" ._")
    return (s[:limit] or "未命名")


def project_dir(root: Path, name: str) -> Path:
    """优先对齐已有项目目录名，避免同一项目被拆成两个目录。"""
    base = root / "data" / "sources"
    base.mkdir(parents=True, exist_ok=True)
    if (base / name).is_dir():
        return base / name
    for d in sorted(base.iterdir()):
        if d.is_dir() and (d.name in name or name in d.name):
            return d
    d = base / safe(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def unique_target(d: Path, name: str) -> Path:
    target = d / Path(name).name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 100):
        cand = d / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
    raise SystemExit(f"[stage] 目标目录里同名文件过多：{d / name}")


def stage_file(d: Path, src: str) -> str:
    p = Path(src)
    if not p.is_file():
        raise SystemExit(f"[stage] 文件不存在或不可读：{src}")
    target = unique_target(d, p.name)
    shutil.copy2(p, target)
    return str(target)


def stage_kb(d: Path, title: str, text_path: str, date: str, origin: str) -> str:
    text = Path(text_path).read_text(encoding="utf-8", errors="replace") if text_path else ""
    if not text.strip():
        raise SystemExit("[stage] --text-file 为空或不存在，不落空文档")
    today = dt.date.today().isoformat()
    fname = f"kb_{safe(date or today, 10)}_{safe(title, 36)}.md"
    target = unique_target(d, fname)
    front = [
        "---",
        "source: knowledge_base",
        f"origin: {origin or '知识库检索'}",
        f"title: {title}",
        f"doc_date: {date or ''}",
        f"retrieved_at: {dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
        "---",
        "",
        f"# {title}",
        "",
        text.strip(),
        "",
    ]
    target.write_text("\n".join(front), encoding="utf-8")
    return str(target)


def main() -> int:
    ap = argparse.ArgumentParser(description="归组上传文件 / 知识库检索正文到 data/sources/<项目名>/")
    ap.add_argument("--root", required=True, help="数据目录（可写）")
    ap.add_argument("--project", required=True, help="项目名（即 data/sources/ 子目录名）")
    ap.add_argument("--file", action="append", default=[], help="会话上传文件路径，可重复")
    ap.add_argument("--kb", action="store_true", help="落盘为知识库检索文档 kb_*.md")
    ap.add_argument("--title", default="", help="kb 文档标题")
    ap.add_argument("--text-file", default="", help="kb 正文文本文件路径（检索结果正文）")
    ap.add_argument("--date", default="", help="kb 文档日期 YYYY-MM-DD，未知留空")
    ap.add_argument("--origin", default="", help="kb 原始文档链接或空间名")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    d = project_dir(root, args.project)
    written: list[str] = []

    for f in args.file:
        written.append(stage_file(d, f))

    if args.kb:
        if not args.title:
            raise SystemExit("[stage] --kb 需要同时提供 --title")
        written.append(stage_kb(d, args.title, args.text_file, args.date, args.origin))

    if not written:
        print("[stage] 未提供 --file / --kb，未做任何事。")
        return 0
    print(f"[stage] 项目「{args.project}」新增 {len(written)} 份资料 -> {d}")
    for w in written:
        print(f"[stage]   {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
