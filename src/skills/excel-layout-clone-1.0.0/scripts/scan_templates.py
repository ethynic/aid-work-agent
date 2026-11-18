#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 XLSX 样版目录，输出精简目录（不加载工作簿内容）。"""
import os
import re
import sys
import json
import glob
import hashlib
import zipfile
import argparse

EXTS = (".xlsx", ".xlsm", ".xltx", ".xltm")


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sheet_names(path):
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("xl/workbook.xml").decode("utf-8", "ignore")
        return re.findall(r'<sheet[^>]*name="([^"]+)"', xml)[:20]
    except Exception:
        return []


def scan_one(path, excerpt=120):
    path = os.path.abspath(path)
    info = {"file": os.path.basename(path), "path": path.replace("\\", "/"),
            "format": os.path.splitext(path)[1].lower().lstrip("."),
            "size_kb": round(os.path.getsize(path) / 1024, 1),
            "hash": file_hash(path), "sheets": _sheet_names(path)}
    info["summary"] = "%d 个 sheet: %s" % (len(info["sheets"]), ", ".join(info["sheets"][:6]))
    return info


def scan_dir(target, excerpt=120):
    if os.path.isfile(target):
        paths = [target]
    else:
        paths = []
        for ext in EXTS:
            paths.extend(glob.glob(os.path.join(target, "*" + ext)))
            paths.extend(glob.glob(os.path.join(target, "*" + ext.upper())))
    out = []
    for p in sorted(set(paths)):
        if os.path.basename(p).startswith("~$"):
            continue
        try:
            out.append(scan_one(p, excerpt))
        except Exception as exc:
            out.append({"path": p, "error": str(exc)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", default="assets/templates")
    ap.add_argument("--excerpt", type=int, default=120)
    args = ap.parse_args()
    print(json.dumps(scan_dir(args.target, args.excerpt), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
