# -*- coding: utf-8 -*-
"""掩码合并算法单元验证（用三个互补的示例掩码）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline

pages = [
    {"url": "aiqicha:detail",
     "masked_phones": [{"raw": "1380013****", "pattern": "1380013****"}]},
    {"url": "aiqicha:compperson",
     "masked_phones": [{"raw": "138****8000", "pattern": "138****8000"}]},
    {"url": "shuidi",
     "masked_phones": [{"raw": "138******00", "pattern": "138******00"}]},
]
r = pipeline.merge_masked_phones(pages)
print("合并结果：")
for x in r:
    print("  ", x["number"], "| confidence=", x["confidence"], "| 来自", x["merged_from"])

print()
pages2 = [
    {"url": "a", "masked_phones": [{"raw": "1380013****", "pattern": "1380013****"}]},
    {"url": "b", "masked_phones": [{"raw": "137****8000", "pattern": "137****8000"}]},
]
r2 = pipeline.merge_masked_phones(pages2)
print("两个不同号码（不应误合并）：",
      [x["number"] for x in r2] or "无（正确：信息不足不下结论）")

print()
pages3 = [
    {"url": "a", "masked_phones": [{"raw": "132******53", "pattern": "132******53"}]},
]
r3 = pipeline.merge_masked_phones(pages3)
print("仅一个掩码、信息不足（不应臆断完整号）：",
      [x["number"] for x in r3] or "无（正确）")
