# -*- coding: utf-8 -*-
"""
合并分批编写的 items 分片为完整 items.json（配合 generate_quotation_lists.py 使用）。

大项目按空间/大类分批编条目时，每个分片是同 schema 的小 JSON（顶层 items 只含
本批条目 + 该批用到的 parameters / precise_images / page_renderings / project）。

用法：
    python merge_items.py --parts-dir <分片目录> --out <items.json>

分片按文件名排序后合并：
  * items       -> 顺序拼接
  * parameters  -> 同名 key 后写覆盖前写，remark 追加来源分片标记
  * precise_images / page_renderings -> 字典浅合并
  * project / disable_auto_image -> 逐字段取第一个非空值

硬校验（任一命中即报错退出，非 0）：
  * 同名 code 跨分片出现，或条目缺 code（新格式 code 必填）
  * 条目缺 space / theme / category（生成器会静默丢弃这类条目，提前报错）
  * qty_rule 引用的参数名不在 parameters 中，定位到条目 code
"""
import argparse
import glob
import json
import os
import re
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
        if not isinstance(data.get("items"), list) or not data["items"]:
            raise SystemExit("分片缺少非空 items 列表: %s" % p)
        parts.append((os.path.basename(p), data))
    return parts


def merge(parts):
    merged = {"items": [], "precise_images": {}, "page_renderings": {},
              "parameters": {}, "project": {}}
    seen_codes = {}  # code -> 分片名
    for part_name, data in parts:
        for it in data["items"]:
            code = (it.get("code") or "").strip()
            if not code:
                raise SystemExit(
                    "分片 %s 存在缺 code 的条目（%s），新格式 code 必填，请补编号后重试"
                    % (part_name, it.get("cn") or it.get("en") or "?"))
            if code in seen_codes:
                raise SystemExit(
                    "条目编号冲突: %s 同时出现在 %s 和 %s，请修改分片后重试"
                    % (code, seen_codes[code], part_name))
            seen_codes[code] = part_name
            missing = [k for k in ("space", "theme", "category") if not (it.get(k) or "").strip()]
            if missing:
                raise SystemExit(
                    "条目 %s（分片 %s）缺少必填维度: %s；生成器会静默丢弃该条目，请补齐后重试"
                    % (code, part_name, "/".join(missing)))
            merged["items"].append(it)
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
        for k in ("project", "disable_auto_image"):
            v = data.get(k)
            if v and not merged.get(k):
                merged[k] = v
    return merged


def check_param_refs(merged):
    """qty_rule 引用的参数名必须存在于 parameters，否则数量公式生成后是死引用"""
    bad = []
    for it in merged["items"]:
        rule = it.get("qty_rule")
        if not rule:
            continue
        names = []
        if rule.get("mode") == "area":
            if rule.get("param"):
                names.append(rule["param"])
        elif rule.get("mode") == "expr":
            names = re.findall(r"\{([^}]+)\}", rule.get("expr") or "")
        for nm in names:
            if nm not in merged["parameters"]:
                bad.append((it.get("code") or it.get("cn") or "?", nm))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts-dir", required=True, help="分片目录（*.json）")
    ap.add_argument("--out", required=True, help="合并后的 items.json 输出路径")
    args = ap.parse_args()

    parts = load_parts(args.parts_dir)
    merged = merge(parts)
    bad_refs = check_param_refs(merged)
    if bad_refs:
        for code, nm in bad_refs[:20]:
            print("  参数缺失引用: %s -> {%s}" % (code, nm), file=sys.stderr)
        raise SystemExit(
            "qty_rule 引用了 parameters 中不存在的参数（%d 处），"
            "请在分片中补定义参数或修正引用后重试" % len(bad_refs))
    n_items = len(merged["items"])
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)
    print("merged %d parts -> %s  (条目 %d，参数 %d)"
          % (len(parts), args.out, n_items, len(merged["parameters"])))


if __name__ == "__main__":
    main()
