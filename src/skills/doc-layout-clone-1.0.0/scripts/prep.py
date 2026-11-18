#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 1+2 步一次编排：扫描样版 -> 选定 -> PDF 转换/DOCX 直用 -> 坐标转储 ->
提取锚点与表格结构 -> 检查 analysis 缓存。stdout 只输出精简 JSON 摘要。

- 单份样版自动选定；多份先给精简目录，选定后再带 --template 调一次。
- --templates 必传：样版来自会话上传文件的实际路径（单个文件或目录）。
- --template 可传绝对路径 / 文件名（来自扫描目录）。
- analysis.json 按「源文件字节 hash + 格式」缓存到 --cache-dir，
  我方约定传 $SKILL_TMP_DIR 下的任务缓存目录（不写进技能包，也不写用户上传目录）。

用法:
    python3 prep.py --work <工作目录> --templates <目录或文件> \
        [--template <文件名或绝对路径>] [--pages 1-3] [--cache-dir <目录>] [--no-compact]
"""
import os
import re
import sys
import json
import glob
import shutil
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_templates import scan_dir, scan_one          # noqa: E402
from dump_template import dump, HINT_PATTERNS          # noqa: E402
from pdf_to_docx import convert as convert_pdf, file_hash  # noqa: E402


def compact_dump(rec):
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
    blocks = {}
    for p in rec["paragraphs"]:
        blocks["P%03d" % p["index"]] = p["text"]
    for t in rec["tables"]:
        for r in t["rows"]:
            for c in r["cells"]:
                if c["text"] and not c["merged"]:
                    blocks["T%d R%02d C%02d" % (t["index"], r["row"], c["col"])] = c["text"]
    out, seen = [], set()
    for coord, txt in blocks.items():
        tags = set()
        for pat, tag in HINT_PATTERNS:
            if re.search(pat, txt):
                tags.add(tag)
        if not tags or (coord, txt) in seen:
            continue
        seen.add((coord, txt))
        out.append({"coord": coord, "tags": sorted(tags), "repr": repr(txt)})
    return out


def tables_schema(rec):
    out = []
    for t in rec["tables"]:
        rows = [[c["text"] for c in r["cells"]] for r in t["rows"][:4]]
        out.append({"table": t["index"], "rows": len(t["rows"]),
                    "cols": max((len(r["cells"]) for r in t["rows"]), default=0),
                    "preview_rows": rows})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    # 我方补丁：运行时 cwd 是 skill_execute 临时目录，样版一律来自会话上传文件
    # 的实际路径，不能依赖相对默认值 assets/templates，故改为必传
    ap.add_argument("--templates", required=True)
    ap.add_argument("--template", default=None)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--pages", default=None, help="PDF 仅转换指定页，如 1-3,5")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--force-convert", action="store_true")
    ap.add_argument("--no-compact", action="store_true")
    ap.add_argument("--excerpt", type=int, default=160)
    args = ap.parse_args()

    work = os.path.abspath(args.work)
    os.makedirs(work, exist_ok=True)
    cache_dir = os.path.abspath(args.cache_dir or
                                os.path.join(os.path.dirname(work), "_analysis_cache"))
    os.makedirs(cache_dir, exist_ok=True)

    catalog = [r for r in scan_dir(args.templates, args.excerpt) if not r.get("error")]
    out = {"work": work, "templates_dir": os.path.abspath(args.templates),
           "templates_count": len(catalog), "catalog": catalog,
           "cache_dir": cache_dir, "warnings": []}

    selected = None
    if args.template:
        cand = args.template
        if not os.path.isabs(cand):
            cand = os.path.join(os.path.abspath(args.templates), args.template)
        if not os.path.exists(cand):
            hit = [c["path"] for c in catalog
                   if os.path.basename(c["path"]) == args.template]
            if hit:
                cand = hit[0]
        if os.path.exists(cand):
            selected = os.path.abspath(cand)
        else:
            out["error"] = "找不到样版: %s" % args.template
    elif len(catalog) == 1:
        selected = catalog[0]["path"]

    if not selected:
        out["selected"] = None
        out["needs_template_choice"] = True
        out["next_action"] = ("目录中有 %d 份样版，请按需求语义选定一份，"
                              "然后再次调用 prep.py 并传 --template <文件名或绝对路径>。" %
                              len(catalog))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    ext = os.path.splitext(selected)[1].lower()
    thash = "%s-%s" % (ext.lstrip("."), file_hash(selected))
    info = scan_one(selected, args.excerpt)
    out["selected"] = {"file": info["file"], "path": info["path"],
                       "format": info["format"], "hash": thash}

    if ext == ".pdf":
        conv_dir = os.path.join(work, "_converted")
        target = os.path.join(conv_dir, os.path.splitext(info["file"])[0] + ".docx")
        rep = convert_pdf(selected, target, args.pages, args.dpi, args.force_convert)
        out["conversion"] = rep
        out["warnings"].extend(rep.get("warnings", []))
        working = target
        out["layout_fidelity"] = "converted-from-pdf"
    elif ext == ".docx":
        working = selected
        out["layout_fidelity"] = "exact-docx"
    else:
        out["error"] = "不支持的样版格式: %s（仅 .docx / .pdf）" % ext
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    out["working_template"] = working.replace("\\", "/")
    dump_path = os.path.join(work, "template_dump.txt")
    text, rec = dump(working, with_hints=True)
    with open(dump_path, "w", encoding="utf-8") as f:
        f.write(text)
    out["dump_path"] = dump_path
    if not args.no_compact:
        out["compact_dump"] = compact_dump(rec)
    out["anchors"] = build_anchors(rec)
    out["tables_schema"] = tables_schema(rec)
    out["layout_signature"] = rec["layout_signature"]

    cached = os.path.join(cache_dir, "%s.analysis.json" % thash)
    if os.path.exists(cached):
        dst = os.path.join(work, "analysis.json")
        shutil.copyfile(cached, dst)
        meta = {}
        meta_path = os.path.join(cache_dir, "%s.meta.json" % thash)
        if os.path.exists(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                pass
        out["analysis_cache"] = {"hit": True, "path": cached,
                                 "copied_to": dst, "meta": meta}
        out["next_action"] = ("分析缓存命中：analysis.json 已就位，跳过第 2 步，"
                              "直接进入第 3 步一次性收集答案。")
    else:
        out["analysis_cache"] = {"hit": False, "path": cached}
        out["next_action"] = ("分析缓存未命中：依据 compact_dump / anchors / tables_schema 写 "
                              "<work>/analysis.json（locators 的 text 必须逐字来自 anchors 或 dump）。"
                              "生成成功后会自动按样版 hash 入缓存。")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
