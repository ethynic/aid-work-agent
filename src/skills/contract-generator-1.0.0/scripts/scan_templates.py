#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 1 步：动态扫描模板目录，生成「模板目录」供技能判断客户需求最匹配哪套模板。

本脚本与具体模板无关：新增模板只需把 .docx 放进 assets/templates/，
无需改技能、无需登记关键词。脚本读取每份模板的标题、正文摘要、表格表头，
输出结构化目录（默认 stdout 文本，--json 输出 JSON，--out 写文件）。

用法:
    python3 scan_templates.py [templates_dir] [--json] [--out catalog.json] [--excerpt 600]
"""
import os
import re
import sys
import json
import glob
import argparse
from docx import Document


def _clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def table_header(table, max_rows=2, max_cols=12):
    out = []
    for r in table.rows[:max_rows]:
        cells = []
        seen = set()
        for c in r.cells:
            if id(c._tc) in seen:
                continue
            seen.add(id(c._tc))
            cells.append(_clean(c.text))
            if len(cells) >= max_cols:
                break
        out.append(cells)
    return out


def scan_one(path, excerpt=600):
    doc = Document(path)
    paras = [_clean(p.text) for p in doc.paragraphs]
    paras = [p for p in paras if p]
    title = paras[0] if paras else os.path.splitext(os.path.basename(path))[0]
    body = " ".join(paras)
    tables = []
    for i, t in enumerate(doc.tables):
        tables.append({
            "index": i,
            "rows": len(t.rows),
            "cols": len(t.columns),
            "header": table_header(t),
        })
    return {
        "file": os.path.basename(path),
        "path": path.replace("\\", "/"),
        "title": title,
        "paragraph_count": len(doc.paragraphs),
        "table_count": len(doc.tables),
        "tables": tables,
        "excerpt": body[:excerpt],
        "full_text_length": len(body),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("templates_dir", nargs="?", default="assets/templates")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--excerpt", type=int, default=600)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.templates_dir, "*.docx")))
    files = [f for f in files if not os.path.basename(f).startswith("~$")]
    catalog = [scan_one(f, args.excerpt) for f in files]

    if args.json:
        text = json.dumps(catalog, ensure_ascii=False, indent=2)
    else:
        lines = ["# 合同模板目录（自动生成，勿手工维护）", ""]
        for i, c in enumerate(catalog):
            lines.append("## [%d] %s" % (i, c["file"]))
            lines.append("标题: %s" % c["title"])
            lines.append("规模: %d 段 / %d 表" % (c["paragraph_count"], c["table_count"]))
            for t in c["tables"]:
                hdr = " | ".join(" / ".join(r) for r in t["header"] if r)
                lines.append("  表%d(%d行x%d列) 表头: %s" % (t["index"], t["rows"], t["cols"], hdr[:300]))
            lines.append("摘要: %s" % c["excerpt"])
            lines.append("")
        text = "\n".join(lines)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("WROTE " + args.out + " (%d templates)" % len(catalog))
    else:
        print(text)


if __name__ == "__main__":
    main()
