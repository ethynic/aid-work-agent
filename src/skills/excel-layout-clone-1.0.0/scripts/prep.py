#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 1+2 步一次编排：扫描样版 -> 选定 -> 结构转储 -> 提取锚点/候选表 -> 缓存检查。

用法:
  python3 prep.py --work <工作目录> --templates <目录或文件> [--template <文件名或绝对路径>]
                  [--cache-dir <目录>] [--no-compact] [--max-lines 500]
"""
import os
import sys
import json
import shutil
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_templates import scan_dir, scan_one  # noqa: E402
from dump_template import dump  # noqa: E402


def compact_dump(rec, max_lines=500):
    lines = []
    for s in rec["sheets"]:
        lines.append("SHEET|%s|%s|%s|%d cells" %
                     (s["name"], s["state"], s["dimension"], len(s["cells"])))
        for c in s["cells"]:
            lines.append("S[%s] %s|%s" %
                         (s["name"], c["ref"], c["formula"] or c["value"]))
            if len(lines) >= max_lines:
                return lines
        for m in s["merged"]:
            lines.append("S[%s] MERGE|%s" % (s["name"], m))
        for t in s["tables"]:
            lines.append("S[%s] TABLE|%s|%s" % (s["name"], t["name"], t["ref"]))
        for dv in s["data_validations"]:
            lines.append("S[%s] DV|%s|%s" % (s["name"], dv["sqref"], dv["type"]))
        for cf in s["conditional_formattings"]:
            lines.append("S[%s] CF|%s|%s" % (s["name"], cf["sqref"], cf["type"]))
        for t in s["candidate_tables"]:
            lines.append("S[%s] CANDIDATE|header=%d|rows=%s|total=%s|%s" %
                         (s["name"], t["header_row"], t["sample_rows"],
                          t["total_row"], t["cols"]))
        if len(lines) >= max_lines:
            break
    return lines[:max_lines]


def sheet_brief(rec):
    out = []
    for s in rec["sheets"]:
        out.append({
            "name": s["name"], "state": s["state"], "dimension": s["dimension"],
            "max_row": s["max_row"], "max_col": s["max_col"],
            "cells": len(s["cells"]),
            "formulas": sum(1 for c in s["cells"] if c["formula"]),
            "merged": s["merged"][:20], "tables": s["tables"],
            "charts": len(s["charts"]), "images": s["images"],
            "dv": len(s["data_validations"]), "cf": len(s["conditional_formattings"]),
            "freeze": s["freeze"], "autofilter": s["autofilter"],
            "print_area": s["print_area"],
            "candidate_tables": s["candidate_tables"][:5],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    # 我方补丁：--templates 改必传（运行时 cwd 为临时目录，模板来自会话上传路径，
    # 不再默认读技能包内 assets/templates）
    ap.add_argument("--templates", required=True)
    ap.add_argument("--template", default=None)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--no-compact", action="store_true")
    ap.add_argument("--max-lines", type=int, default=500)
    args = ap.parse_args()

    work = os.path.abspath(args.work)
    os.makedirs(work, exist_ok=True)
    cache_dir = os.path.abspath(args.cache_dir or
                                os.path.join(os.path.dirname(work), "_analysis_cache"))
    os.makedirs(cache_dir, exist_ok=True)
    catalog = [r for r in scan_dir(args.templates) if not r.get("error")]
    out = {"work": work, "templates_dir": os.path.abspath(args.templates),
           "templates_count": len(catalog), "catalog": catalog,
           "cache_dir": cache_dir, "warnings": []}

    selected = None
    if args.template:
        cand = args.template
        if not os.path.isabs(cand):
            cand = os.path.join(os.path.abspath(args.templates), cand)
        if not os.path.exists(cand):
            hit = [c["path"] for c in catalog if os.path.basename(c["path"]) == args.template]
            if hit:
                cand = hit[0]
        selected = os.path.abspath(cand) if os.path.exists(cand) else None
        if not selected:
            out["error"] = "找不到样版: %s" % args.template
    elif len(catalog) == 1:
        selected = catalog[0]["path"]
    elif len(catalog) == 0:
        out["error"] = "未找到 .xlsx/.xlsm/.xltx/.xltm 样版文件"

    if out.get("error"):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if not selected:
        out["selected"] = None
        out["needs_template_choice"] = True
        out["next_action"] = ("目录中有 %d 份样版，请按需求语义选定一份，再用 --template 重调。" %
                              len(catalog))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    info = scan_one(selected)
    thash = "xlsx-" + info["hash"]
    out["selected"] = {"file": info["file"], "path": info["path"],
                       "format": info["format"], "hash": thash}
    out["working_template"] = info["path"]
    out["layout_fidelity"] = "exact-xlsx-package（未触及部件按字节保留）"

    text, rec = dump(selected)
    dump_path = os.path.join(work, "template_dump.txt")
    with open(dump_path, "w", encoding="utf-8") as f:
        f.write(text)
    out["dump_path"] = dump_path.replace("\\", "/")
    if not args.no_compact:
        out["compact_dump"] = compact_dump(rec, args.max_lines)
    out["sheets"] = sheet_brief(rec)
    out["anchors"] = rec["anchors"]
    out["layout_signature"] = rec["layout_signature"]
    out["warnings"].extend(rec.get("warnings", []))
    sig = rec["layout_signature"]
    if sig.get("vba"):
        out["warnings"].append("含 VBA 宏：引擎按原字节保留 vbaProject.bin，不会重编译宏")
    # 我方补丁（上游 bug）：signature 的键带 xl/ 前缀（xl/pivotTables），上游检查
    # 无前缀键导致透视/切片器警告从未触发；两种键名都兼容
    if (sig.get("pivotTables") or sig.get("slicerCaches")
            or sig.get("xl/pivotTables") or sig.get("xl/slicerCaches")):
        out["warnings"].append("含数据透视/切片器：源区域会随明细行扩展并设置打开时刷新，缓存记录在 Excel 打开时重建")

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
                                 "copied_to": dst.replace("\\", "/"), "meta": meta}
        out["next_action"] = ("分析缓存命中：analysis.json 已就位，跳过第 2 步，"
                              "直接进入第 3 步一次性收集答案。")
    else:
        out["analysis_cache"] = {"hit": False, "path": cached}
        out["next_action"] = ("分析缓存未命中：依据 compact_dump / anchors / sheets[].candidate_tables "
                              "写 <work>/analysis.json（locators 的坐标必须来自转储）。"
                              "生成成功后会自动按样版 hash 入缓存。")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
