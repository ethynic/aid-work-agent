#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复核：确认报价范围内的每一项是否都已报价。

读取 items_extracted.json，仅统计 in_scope=true 的条目，输出：
  - 范围内条目总数 / 已报价 / 未报价 / 替换项 / 知识库来源 / AI建议来源
  - 未报价清单（若有，必须补齐或明确标记）
  - 替换项清单
  - 结论「全部完成？」

用法：
  python review.py --json items_extracted.json
"""
import argparse
import json


def main():
    ap = argparse.ArgumentParser(description="复核报价完整性")
    ap.add_argument("--json", required=True)
    args = ap.parse_args()

    data = json.load(open(args.json, encoding="utf-8"))
    items = data["items"]
    in_scope = [i for i in items if i.get("in_scope")]
    total = len(in_scope)
    quoted = [i for i in in_scope if i.get("unit_price") not in (None, "")]
    unquoted = [i for i in in_scope if i.get("unit_price") in (None, "")]
    mismatched = [i for i in in_scope
                  if i.get("spec_consistent") is False or i.get("replacement_desc")]
    ai = [i for i in in_scope
          if i.get("source") and ("AI" in str(i["source"]) or "建议" in str(i["source"]))]
    kb = [i for i in in_scope
          if i.get("source") and ("KB" in str(i["source"]) or "知识库" in str(i["source"]))]

    report = {
        "project": data.get("project", ""),
        "in_scope_total": total,
        "quoted": len(quoted),
        "unquoted": len(unquoted),
        "mismatched": len(mismatched),
        "ai_suggested": len(ai),
        "kb_sourced": len(kb),
        "unquoted_items": [
            {"code": i["code"], "file": i["file"], "title": i["title_cn_en"]}
            for i in unquoted
        ],
        "mismatched_items": [
            {"code": i["code"], "file": i["file"], "replacement": i.get("replacement_desc")}
            for i in mismatched
        ],
        "complete": len(unquoted) == 0,
    }
    with open("review_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("=== 复核报告 ===")
    print("报价范围条目总数：%d" % total)
    print("已报价：%d" % len(quoted))
    print("未报价：%d" % len(unquoted))
    print("规格不一致/已替换：%d" % len(mismatched))
    print("知识库来源：%d，AI建议来源：%d" % (len(kb), len(ai)))
    if unquoted:
        print("\n❌ 以下条目尚未报价：")
        for i in unquoted:
            print("   - [%s] %s | %s" % (i["code"], i["title_cn_en"], i["file"]))
    if mismatched:
        print("\n⚠ 规格不一致/替换项：")
        for i in mismatched:
            print("   - [%s] %s" % (i["code"], i.get("replacement_desc")))
    print("\n全部完成？", "是 ✅" if report["complete"] else "否 ❌")
    print("写出 -> review_report.json")


if __name__ == "__main__":
    main()
