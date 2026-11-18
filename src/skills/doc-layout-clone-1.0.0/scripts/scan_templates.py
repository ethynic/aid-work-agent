#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
样版目录扫描：支持 .docx / .pdf（大小写不敏感），只输出精简目录，不回显全文。

被 prep.py 调用；也可单独使用：
    python3 scan_templates.py <目录或单个文件> [--excerpt 160]
"""
import os
import re
import sys
import glob
import json
import argparse

EXTS = (".docx", ".pdf")


def _clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def scan_one(path, excerpt=160):
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    rec = {"file": os.path.basename(path), "path": path.replace("\\", "/"),
           "format": ext.lstrip("."), "title": "", "paragraph_count": 0,
           "table_count": 0, "page_count": 0, "excerpt": "", "error": None}
    try:
        if ext == ".docx":
            from docx import Document
            doc = Document(path)
            paras = [_clean(p.text) for p in doc.paragraphs]
            paras = [p for p in paras if p]
            rec["paragraph_count"] = len(paras)
            rec["table_count"] = len(doc.tables)
            rec["title"] = paras[0][:80] if paras else ""
            rec["excerpt"] = _clean(" / ".join(paras[:4]))[:excerpt]
        elif ext == ".pdf":
            import fitz  # PyMuPDF
            pdf = fitz.open(path)
            rec["page_count"] = len(pdf)
            text = "\n".join(pdf[i].get_text("text") for i in range(min(3, len(pdf))))
            pdf.close()
            lines = [_clean(x) for x in text.splitlines() if _clean(x)]
            rec["title"] = lines[0][:80] if lines else "(无可提取文字，疑似扫描件)"
            rec["excerpt"] = _clean(" / ".join(lines[:6]))[:excerpt]
        else:
            rec["error"] = "不支持的格式: %s" % ext
    except Exception as e:
        rec["error"] = str(e)
    return rec


def scan_dir(directory, excerpt=160):
    directory = os.path.abspath(directory)
    if os.path.isfile(directory):
        paths = [directory]
    else:
        paths = []
        for ext in EXTS:
            paths.extend(glob.glob(os.path.join(directory, "*" + ext)))
            paths.extend(glob.glob(os.path.join(directory, "*" + ext.upper())))
        paths = sorted(set(paths))
    paths = [p for p in paths if not os.path.basename(p).startswith("~$")]
    return [scan_one(p, excerpt) for p in paths]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="样版目录或单个 .docx/.pdf 文件")
    ap.add_argument("--excerpt", type=int, default=160)
    args = ap.parse_args()
    print(json.dumps(scan_dir(args.target, args.excerpt), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
