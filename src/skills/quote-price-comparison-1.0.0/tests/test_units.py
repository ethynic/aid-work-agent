#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单位换算回归测试：验证「单价×数量」金额守恒在多样化单位下都成立。

用法：
    python tests/test_units.py

覆盖场景：
  1. 千件 vs 个（同量纲进位）—— 历史 1000 倍 bug 的原始场景
  2. 价格单位≠数量单位（同量纲）：元/吨 + 千克
  3. 跨量纲 + 人工换算关系：元/箱 + 包（1箱=20包）
  4. 跨量纲未给关系 → 必须标不可比，不得臆测
  5. kg vs 吨 混用
  6. 幂等性：同一输入两次运行结果完全一致
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
sys.path.insert(0, SCRIPTS)

PASS, FAIL = "PASS", "FAIL"
results = []


def make_item(supplier, name, spec, uom, qty, price, **kw):
    d = {
        "item_id": "", "supplier": supplier, "source_type": "upload",
        "source_file": f"{supplier}.xlsx", "source_ref": "", "source_date": "2026-11-12",
        "code": "C1", "name": name, "spec": spec, "brand": "", "qty": qty,
        "uom": uom, "qty_uom": uom, "standard_uom": uom, "uom_known": True,
        "unit_price": price, "price_raw": str(price), "currency": "CNY",
        "tax_included": True, "tax_rate": 0.13, "unit_multiplier": 1.0,
        "amount_reported": None, "quote_status": "quoted", "quote_note": "",
        "note": "", "match_key": "", "match_confidence": 1.0,
        "locator": {"kind": "none"},
    }
    d.update(kw)
    return d


def run_compare(items, workdir, tag):
    qf = os.path.join(workdir, f"q_{tag}.json")
    od = os.path.join(workdir, f"out_{tag}")
    with open(qf, "w", encoding="utf-8") as f:
        json.dump({"items": items, "warnings": [], "defaults": {}}, f, ensure_ascii=False)
    shutil.rmtree(od, ignore_errors=True)
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "compare_prices.py"),
         "--quotes", qf, "--out-dir", od],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"compare_prices 失败：\n{proc.stdout}\n{proc.stderr}")
    with open(os.path.join(od, "comparison.json"), encoding="utf-8") as f:
        return json.load(f)


def check(name, cond, detail=""):
    results.append((name, PASS if cond else FAIL, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail and not cond else ""))


def amounts(res, idx=0):
    g = res["groups"][idx]
    return [r.get("amount_std") for r in g["rows"]]


def main():
    tmp = tempfile.mkdtemp(prefix="qpc_test_")
    try:
        # --- 场景1：千件 vs 个（历史 1000 倍 bug）---
        r = run_compare([
            make_item("A", "螺栓", "M10", "千件", 50, 1850),
            make_item("B", "螺栓", "M10", "个", 50000, 1.75),
        ], tmp, "s1")
        a = amounts(r)
        check("千件/个 换算后金额 = 92500 / 87500",
              abs(a[0] - 92500) < 0.01 and abs(a[1] - 87500) < 0.01, f"实际 {a}")
        check("千件/个 归入同一组", len(r["groups"]) == 1, f"实际 {len(r['groups'])} 组")

        # --- 场景2：价格单位≠数量单位（同量纲）---
        r = run_compare([
            make_item("A", "钢板", "SPCC", "吨", 12, 5280, qty_uom="千克"),
            make_item("B", "钢板", "SPCC", "吨", 12, 5000, qty_uom="千克"),
        ], tmp, "s2")
        a = amounts(r)
        check("元/吨 + 千克：两组金额一致 63.36 / 60.00",
              abs(a[0] - 63.36) < 0.01 and abs(a[1] - 60.00) < 0.01, f"实际 {a}")
        check("元/吨 + 千克：采购数量换算为 12 千克",
              abs(r["groups"][0]["qty"] - 12) < 0.001, f"实际 {r['groups'][0]['qty']}")

        # --- 场景3：跨量纲 + 人工换算关系（1箱=20包）---
        # 语义：qty 恒为 qty_uom 计量单位下的数量；uom 只是单价计价单位。
        #   A：单价 2.5 元/包，数量 300 包
        #   B：单价 50 元/箱，数量 300 包（= 15 箱），1 箱 = 20 包 → qty_to_price_uom = 0.05
        r = run_compare([
            make_item("A", "螺钉", "M8", "包", 300, 2.5),
            make_item("B", "螺钉", "M8", "箱", 300, 50, qty_uom="包", qty_to_price_uom=0.05),
        ], tmp, "s3")
        a = amounts(r)
        check("元/箱 + 包(1箱=20包)：金额守恒 750 / 750",
              abs(a[0] - 750) < 0.01 and abs(a[1] - 750) < 0.01, f"实际 {a}")
        check("元/箱 + 包：归入同一组", len(r["groups"]) == 1, f"实际 {len(r['groups'])} 组")

        # --- 场景4：跨量纲未给关系 → 必须不可比 ---
        r = run_compare([
            make_item("A", "螺钉", "M8", "包", 300, 2.5),
            make_item("B", "螺钉", "M8", "箱", 15, 50, qty_uom="包"),
        ], tmp, "s4")
        flags = [x["flag"] for x in r["groups"][0]["rows"]]
        check("跨量纲未给换算关系 → 标不可比（不臆测）",
              "uncomparable" in flags, f"实际 {flags}")
        reasons = [x.get("flag_reason", "") for x in r["groups"][0]["rows"]]
        check("不可比原因写明需补 qty_to_price_uom",
              any("qty_to_price_uom" in s for s in reasons), f"实际 {reasons}")

        # --- 场景5：kg vs 吨 ---
        r = run_compare([
            make_item("A", "钢", "SPCC", "kg", 12000, 5.28),
            make_item("B", "钢", "SPCC", "吨", 12, 5280),
        ], tmp, "s5")
        a = amounts(r)
        check("kg/吨 混用：金额一致 63360",
              abs(a[0] - 63360) < 1 and abs(a[1] - 63360) < 1, f"实际 {a}")

        # --- 场景6：幂等性 ---
        items = [
            make_item("A", "螺栓", "M10", "千件", 50, 1850),
            make_item("B", "螺栓", "M10", "个", 50000, 1.75),
            make_item("C", "钢板", "SPCC", "吨", 12, 5280),
        ]
        r1 = run_compare(items, tmp, "s6a")
        r2 = run_compare(items, tmp, "s6b")
        check("同一输入两次运行结果完全一致",
              json.dumps(r1, sort_keys=True, ensure_ascii=False) ==
              json.dumps(r2, sort_keys=True, ensure_ascii=False))

        # --- 场景7：语义不同单位不合并（支 vs 件） ---
        r = run_compare([
            make_item("A", "焊条", "Φ3.2", "支", 100, 5.5),
            make_item("B", "焊条", "Φ3.2", "件", 100, 5.5),
        ], tmp, "s7")
        check("支/件 语义不同 → 不自动合并",
              len(r["groups"]) == 2, f"实际 {len(r['groups'])} 组")

        # --- 场景8：数量单位与数量值不同步 → 必须被检出 ---
        # 典型错误：源单写 80,000 个，执行者把数量单位改成「千件」却忘了把数量改成 80
        r = run_compare([
            make_item("A", "密封圈", "φ25", "千件", 80, 320),
            make_item("B", "密封圈", "φ25", "千件", 50, 290),   # 数量值未同步改
        ], tmp, "s8")
        anomalies = " ".join(r.get("anomalies", []))
        check("数量单位与数量值不同步 → 记异常并提示",
              "数量单位" in anomalies and "同步修改" in anomalies, f"异常: {anomalies[:120]}")

        # --- 场景9：跨执行口径无关性（不同 qty_uom 表述应得同一金额） ---
        r1 = run_compare([
            make_item("A", "螺栓", "M10", "千件", 50, 1850),
            make_item("B", "螺栓", "M10", "个", 50000, 1.75),
        ], tmp, "s9a")
        r2 = run_compare([
            make_item("A", "螺栓", "M10", "千件", 50, 1850),
            make_item("B", "螺栓", "M10", "千件", 50, 1750),   # 同一口径重述
        ], tmp, "s9b")
        a1, a2 = amounts(r1), amounts(r2)
        check("同一采购量不同单位表述 → 金额一致",
              abs(a1[1] - 87500) < 0.01 and abs(a2[1] - 87500) < 0.01, f"实际 {a1} / {a2}")

        n_fail = sum(1 for _, s, _ in results if s == FAIL)
        print(f"\n{'=' * 60}")
        print(f"通过 {len(results) - n_fail}/{len(results)}"
              + (f"，失败 {n_fail} 项" if n_fail else "，全部通过 ✅"))
        return 1 if n_fail else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
