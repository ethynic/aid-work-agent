# -*- coding: utf-8 -*-
"""
合并分批编写的 items 分片为完整 items.json（配合 generate_quotation_lists.py 使用）。

大项目按业态/户型分批编条目时，每个分片是同 schema 的小 JSON（顶层 files 只含
一个文件对象 + 该文件用到的 parameters / precise_images / page_renderings / project）。

用法：
    python merge_items.py --parts-dir <分片目录> --out <items.json>

分片按「第一个文件的 filename」排序后合并：
  * files        -> 顺序拼接
  * parameters   -> 同名 key 后写覆盖前写，remark 追加来源分片标记
  * precise_images / page_renderings -> 字典浅合并
  * project      -> 逐字段取第一个非空值

硬校验：同名 code 跨分片出现 -> 报错退出（非 0），提示修改分片。
"""
import argparse
import glob
import json
import os
import sys


def load_parts(parts_dir):
    paths = sorted(glob.glob(os.path.join(parts_dir, "*.json")))
    if not paths:
        raise SystemExit("分片目录无 JSON 文件: %s" % parts_dir)
    parts = []
    for p in paths:
        try:
            data = json.load(open(p, encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit("分片 JSON 解析失败 %s: %s" % (p, e))
        if not data.get("files"):
            raise SystemExit("分片缺少 files: %s" % p)
        parts.append((os.path.basename(p), data))
    parts.sort(key=lambda kv: kv[1]["files"][0].get("filename", ""))
    return parts


def merge(parts):
    merged = {"files": [], "precise_images": {}, "page_renderings": {},
              "parameters": {}, "project": {}}
    seen_codes = {}  # code -> 分片名
    for part_name, data in parts:
        for f in data["files"]:
            merged["files"].append(f)
            for grp in f.get("groups", []):
                for it in grp.get("items", []):
                    code = it.get("code", "")
                    if not code:
                        continue
                    if code in seen_codes:
                        raise SystemExit(
                            "条目编号冲突: %s 同时出现在 %s 和 %s，请修改分片后重试"
                            % (code, seen_codes[code], part_name))
                    seen_codes[code] = part_name
        for k in ("precise_images", "page_renderings"):
            merged[k].update(data.get(k) or {})
        for k, meta in (data.get("parameters") or {}).items():
            if k in merged["parameters"]:
                remark = ""
                if isinstance(meta, dict) and meta.get("remark"):
                    remark = "%s（合并自 %s）" % (meta["remark"], part_name)
                elif isinstance(meta, dict):
                    remark = "合并自 %s" % part_name
                if isinstance(meta, dict):
                    meta = dict(meta, remark=remark)
            merged["parameters"][k] = meta
        for k, v in (data.get("project") or {}).items():
            if v and not merged["project"].get(k):
                merged["project"][k] = v
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts-dir", required=True, help="分片目录（*.json）")
    ap.add_argument("--out", required=True, help="合并后的 items.json 输出路径")
    args = ap.parse_args()

    parts = load_parts(args.parts_dir)
    merged = merge(parts)
    n_files = len(merged["files"])
    n_items = sum(len(g.get("items") or []) for f in merged["files"] for g in (f.get("groups") or []))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)
    print("merged %d parts -> %s  (文件 %d，条目 %d，参数 %d)"
          % (len(parts), args.out, n_files, n_items, len(merged["parameters"])))


if __name__ == "__main__":
    main()
