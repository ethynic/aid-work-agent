#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
价格库查询（与具体合同模板无关）。

- 单条查询：按名称（+可选模式）从 CSV 查标准单价。
- 批量补价：给一份「产品/服务清单」，对未指定单价的条目按名称自动查价。
  第 3 步问答时可用；第 4 步 fill_contract.py 也会对缺价条目自动调用本模块。

本脚本只负责「按名称+模式查单价」，不知道任何价格具体是多少——价格库 CSV
由调用方提供（来自资料库报价清单的标准售价），技能本身不内置任何价格数据。

用法:
    # 单条
    python3 lookup_price.py <你的价格库.csv> "AI在线客服" --mode 私有云 [--json]
    # 批量：读取 items.json（数组），补齐 --price-key 字段，输出
    python3 lookup_price.py <你的价格库.csv> --items items.json --name-key name \
        --price-key unit_price --mode 私有云 [--out items_priced.json] [--json]
"""
import csv
import sys
import json
import argparse


def _read_rows(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def lookup(csv_path, query, mode=None, fuzzy=True):
    """返回命中的价格行列表（dict）。"""
    query = (query or "").strip()
    mode = (mode or "").strip()
    out = []
    for row in _read_rows(csv_path):
        name = (row.get("名称") or "").strip()
        if not query:
            continue
        hit = ((query in name) or (name in query)) if fuzzy else (query == name)
        if not hit:
            continue
        if mode:
            m = (row.get("模式") or "").strip()
            if mode not in m and m not in mode:
                continue
        out.append(row)
    return out


def best_price(csv_path, query, mode=None, price_key="单价"):
    """返回最匹配的一条单价字符串；无匹配返回 None。"""
    rows = lookup(csv_path, query, mode)
    if not rows:
        return None
    return (rows[0].get(price_key) or "").strip() or None


def price_items(csv_path, items, mode, name_key="name", price_key="unit_price",
                only_missing=True, source_key="price_source"):
    """对清单中缺价条目按名称查价，返回 (补齐后的清单, 查价记录)。"""
    priced, log = [], []
    for it in items:
        item = dict(it)
        name = str(item.get(name_key) or "").strip()
        has_price = str(item.get(price_key) or "").strip() != ""
        if name and (not has_price or not only_missing):
            rows = lookup(csv_path, name, mode)
            if rows:
                item[price_key] = (rows[0].get("单价") or "").strip()
                item[source_key] = "知识库:%s" % rows[0].get("名称")
                log.append({"name": name, "price": item[price_key],
                            "matched": rows[0].get("名称"), "hits": len(rows)})
            else:
                log.append({"name": name, "price": None, "matched": None, "hits": 0,
                            "warning": "未命中价格知识库"})
        else:
            if has_price:
                item.setdefault(source_key, "用户提供")
        priced.append(item)
    return priced, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("query", nargs="?")
    ap.add_argument("--mode")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--items")
    ap.add_argument("--name-key", default="name")
    ap.add_argument("--price-key", default="unit_price")
    ap.add_argument("--out")
    args = ap.parse_args()

    if args.items:
        with open(args.items, encoding="utf-8") as f:
            data = json.load(f)
        items = data if isinstance(data, list) else data.get("service_items", [])
        priced, log = price_items(args.csv_path, items, args.mode,
                                  args.name_key, args.price_key)
        out_obj = priced if isinstance(data, list) else {**data, "service_items": priced}
        payload = json.dumps(out_obj, ensure_ascii=False, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(payload)
            print("WROTE %s (%d items, %d priced)" %
                  (args.out, len(priced), sum(1 for x in log if x.get("price"))))
        else:
            print(payload)
        if not args.json:
            for x in log:
                print("  %s -> %s (命中 %s)" % (x["name"], x.get("price"), x.get("matched")),
                      file=sys.stderr)
        return

    if not args.query:
        ap.error("需要提供 query 或 --items")
    rows = lookup(args.csv_path, args.query, args.mode)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        if not rows:
            print("未找到匹配的价格条目：%s%s" % (args.query, (" / " + args.mode if args.mode else "")))
        for r in rows:
            print("%s | 模式:%s | 单价:%s %s | 单位:%s | 备注:%s" % (
                r.get("名称"), r.get("模式"), r.get("单价"), r.get("币种"),
                r.get("单位"), r.get("备注")))


if __name__ == "__main__":
    main()
