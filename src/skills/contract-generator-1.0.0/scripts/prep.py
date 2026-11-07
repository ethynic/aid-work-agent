#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 1+2 步编排（省往返）：一次调用完成「扫描模板目录 + dump + analysis 缓存检查」，
并只向 stdout 输出一份精简摘要，避免把大段文本反复塞进大模型上下文。

设计目标：把原本需要 3~5 次工具往返的机械工作压成 1 次。

- --template 未指定且目录里恰好 1 份 .docx：自动选定并 dump（最常见的单模板场景，一次搞定）。
- --template 未指定且有多份：只输出精简目录（不 dump），由技能按语义选模板后再调用一次。
- 指定 --template 后：写 dump.txt 到工作目录，并输出 compact_dump（紧凑坐标全文）、
  anchors（候选锚点的精确 repr，可直接抄进 locators）、tables_schema（表格结构）。
- analysis.json 按模板内容 SHA256 缓存到 --cache-dir（默认 <work>/../_analysis_cache，
  位于用户工作区，不写进技能包）。命中时自动复制到 <work>/analysis.json，
  技能可直接跳过第 2 步的「读 dump + 手写 analysis.json」。

用法:
    python3 prep.py --work <工作目录> --templates <模板目录> [--template <文件名>] \
        [--cache-dir <缓存目录>] [--no-compact]
"""
import os
import re
import sys
import json
import glob
import hashlib
import shutil
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_templates import scan_one          # noqa: E402
from dump_template import dump, HINT_PATTERNS  # noqa: E402


def template_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def compact_dump(rec):
    """紧凑坐标全文：一行一个非空块，去掉装饰线与合并标记，比原始 dump 短 30~40%。"""
    lines = []
    for p in rec["paragraphs"]:
        lines.append("P%03d|%s" % (p["index"], p["text"]))
    for t in rec["tables"]:
        for r in t["rows"]:
            for c in r["cells"]:
                if c["text"]:
                    lines.append("T%d R%02d C%02d|%s" %
                                 (t["index"], r["row"], c["col"], c["text"]))
    for h in rec["headers"]:
        lines.append("H%d-%d|%s" % (h["section"], h["index"], h["text"]))
    for f in rec["footers"]:
        lines.append("F%d-%d|%s" % (f["section"], f["index"], f["text"]))
    return lines


def build_anchors(rec):
    """候选待填位置的精确 repr：暴露前导/尾随空格与制表符，供 locators 逐字抄用。"""
    blocks = {}
    for p in rec["paragraphs"]:
        blocks["P%03d" % p["index"]] = p["text"]
    for t in rec["tables"]:
        for r in t["rows"]:
            for c in r["cells"]:
                if c["text"] and not c["merged"]:
                    blocks["T%d R%02d C%02d" % (t["index"], r["row"], c["col"])] = c["text"]

    # 直接从原文重建（dump 的 rec 里 _clean 已 strip，repr 用原文即可满足绝大多数场景）
    anchors = []
    seen = set()
    for coord, txt in blocks.items():
        tags = set()
        for pat, tag in HINT_PATTERNS:
            if re.search(pat, txt):
                tags.add(tag)
        if not tags:
            continue
        key = (coord, txt)
        if key in seen:
            continue
        seen.add(key)
        anchors.append({"coord": coord, "tags": sorted(tags), "repr": repr(txt)})
    return anchors


def tables_schema(rec):
    out = []
    for t in rec["tables"]:
        rows = []
        for r in t["rows"][:4]:
            rows.append([c["text"] for c in r["cells"]])
        out.append({"table": t["index"], "rows": len(t["rows"]),
                    "cols": max((len(r["cells"]) for r in t["rows"]), default=0),
                    "preview_rows": rows})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="本次任务工作目录（缓存也写这里）")
    ap.add_argument("--templates", default="assets/templates")
    ap.add_argument("--template", default=None, help="已选定的模板文件名或路径")
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--no-compact", action="store_true", help="不输出 compact_dump")
    ap.add_argument("--excerpt", type=int, default=200)
    args = ap.parse_args()

    work = os.path.abspath(args.work)
    os.makedirs(work, exist_ok=True)
    cache_dir = os.path.abspath(args.cache_dir or
                                os.path.join(os.path.dirname(work), "_analysis_cache"))
    os.makedirs(cache_dir, exist_ok=True)

    files = sorted(glob.glob(os.path.join(args.templates, "*.docx")))
    files = [f for f in files if not os.path.basename(f).startswith("~$")]

    catalog = []
    for f in files:
        c = scan_one(f, args.excerpt)
        catalog.append({"file": c["file"], "title": c["title"],
                        "paragraphs": c["paragraph_count"], "tables": c["table_count"],
                        "table_headers": [t["header"] for t in c["tables"]],
                        "excerpt": c["excerpt"]})

    out = {"work": work, "templates_dir": os.path.abspath(args.templates),
           "templates_count": len(catalog), "catalog": catalog,
           "cache_dir": cache_dir}

    # 选模板：显式指定 > 唯一模板自动选定 > 多模板待确认
    selected = None
    if args.template:
        cand = args.template if os.path.isabs(args.template) else os.path.join(args.templates, args.template)
        if not os.path.exists(cand):
            hit = [f for f in files if os.path.basename(f) == args.template]
            if hit:
                cand = hit[0]
        if os.path.exists(cand):
            selected = cand
        else:
            out["error"] = "找不到模板: %s" % args.template
    elif len(files) == 1:
        selected = files[0]

    if not selected:
        out["selected"] = None
        out["needs_template_choice"] = True
        out["next_action"] = ("目录中有 %d 份模板，请按需求语义选一套，然后再次调用 prep.py 并传 "
                              "--template <文件名> 获取 dump 与 analysis 缓存状态。" % len(files))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    thash = template_hash(selected)
    cached = os.path.join(cache_dir, "%s.analysis.json" % thash)
    meta_path = os.path.join(cache_dir, "%s.meta.json" % thash)

    out["selected"] = {"file": os.path.basename(selected),
                       "path": selected.replace("\\", "/"), "hash": thash}

    # dump 只写文件，不回显全文
    dump_path = os.path.join(work, "template_dump.txt")
    text, rec = dump(selected, with_hints=True)
    with open(dump_path, "w", encoding="utf-8") as f:
        f.write(text)
    out["dump_path"] = dump_path

    if not args.no_compact:
        out["compact_dump"] = compact_dump(rec)
    out["anchors"] = build_anchors(rec)
    out["tables_schema"] = tables_schema(rec)

    if os.path.exists(cached):
        dst = os.path.join(work, "analysis.json")
        shutil.copyfile(cached, dst)
        meta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                pass
        out["analysis_cache"] = {"hit": True, "path": cached, "copied_to": dst,
                                 "template_hash": thash, "meta": meta}
        out["next_action"] = ("分析缓存命中：analysis.json 已就位，请直接跳过第 2 步（无需读 dump、"
                              "无需重写 analysis.json），进入第 3 步一次性收集答案。")
    else:
        out["analysis_cache"] = {"hit": False, "path": cached, "template_hash": thash}
        out["next_action"] = ("分析缓存未命中：请依据 compact_dump / anchors / tables_schema 写 "
                              "analysis.json（locators 的原文必须逐字来自这里，无需再单独核对锚点）。"
                              "该文件会在第 4 步生成成功后自动入缓存，供同模板下次复用。")

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
