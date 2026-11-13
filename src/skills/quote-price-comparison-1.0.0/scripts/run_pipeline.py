#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键流水线：抽取 → 比价 → 标色 → 自检，产出结构化 run_report.json。

把原来需要 3 次脚本调用 + 人工读中间文件的过程，压成 **1 次命令 + 1 份报告**，
从而减少大模型的往返调用次数。

用法：
  # 最简：给报价单，跑完整流程
  python run_pipeline.py 报价单1.xlsx 报价单2.pdf --work-dir out/

  # 带供应商映射
  python run_pipeline.py src/*.xlsx --supplier-map '{"A.xlsx":"甲公司"}' --work-dir out/

  # 已有 quotes.json（已补资料库/网络价），只跑比价+标色
  python run_pipeline.py --quotes quotes.json --work-dir out/

  # 只做抽取
  python run_pipeline.py 报价单.xlsx --work-dir out/ --stop-after extract

产出（均在 --work-dir 下）：
  quotes.json          统一结构报价条目
  comparison.xlsx      横向对比 / 明细数据 / 异常与不可比 / 口径与图例
  comparison.json      结构化结果
  marks.json           标色指令
  summary.docx         摘要（Word 格式）
  *_marked.*           带红绿标记的源报价单副本
  run_report.json      ★ 机器可读的执行报告（大模型只读这一个文件即可）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import time
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from qc_common import dump_json, today  # noqa: E402

PY = sys.executable


def _capture(fn, *args, **kwargs):
    """在进程内执行并捕获 stdout/stderr，返回 (returncode, stdout, stderr)。

    相比 subprocess 起子进程，省掉每次约 0.8s 的解释器启动开销。
    """
    buf_out, buf_err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            fn(*args, **kwargs)
        code = 0
    except SystemExit as e:
        code = int(e.code or 0)
    except Exception as e:  # noqa: BLE001
        import traceback
        buf_err.write(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        code = 1
    return code, buf_out.getvalue(), buf_err.getvalue()


def run_step(title, fn, report, t0, desc=None):
    """执行一个子步骤（进程内调用），记录耗时与结果。"""
    print(f"\n=== {title} ===")
    if desc:
        print("  $ " + desc)
    t = time.time()
    code, out, err = _capture(fn)
    dt = time.time() - t
    tail = [ln for ln in (out or "").splitlines() if ln.strip()]
    for ln in tail[-8:]:
        print("  " + ln)
    if code != 0:
        for ln in (err or "").splitlines()[-10:]:
            print("  ! " + ln)
    report["steps"].append({
        "step": title, "returncode": code, "seconds": round(dt, 2),
        "stdout_tail": tail[-12:], "stderr_tail": (err or "").splitlines()[-6:],
    })
    return code == 0


def summarize_quotes(qpath):
    """读取 quotes.json，产出结构化摘要（大模型无需再逐文件看）。"""
    if not os.path.isfile(qpath):
        return {}
    with open(qpath, encoding="utf-8") as f:
        d = json.load(f)
    items = d.get("items", [])
    by_sup = {}
    for it in items:
        s = by_sup.setdefault(it.get("supplier", "?"), {
            "file": it.get("source_file", ""), "type": it.get("source_type", ""),
            "quoted": 0, "no_quote": 0, "missing_spec": 0, "missing_uom": 0,
            "no_quote_items": [], "missing_spec_items": [],
        })
        if it.get("quote_status") == "no_quote":
            s["no_quote"] += 1
            s["no_quote_items"].append({
                "name": it.get("name"), "code": it.get("code"), "reason": it.get("quote_note", "")})
        else:
            s["quoted"] += 1
        if not (it.get("spec") or "").strip():
            s["missing_spec"] += 1
            if len(s["missing_spec_items"]) < 6:
                s["missing_spec_items"].append({"name": it.get("name"), "code": it.get("code")})
        if not (it.get("uom") or "").strip():
            s["missing_uom"] += 1
    return {
        "item_count": len(items),
        "supplier_count": len(by_sup),
        "warnings": d.get("warnings", []),
        "by_supplier": by_sup,
    }


def analyze_result(cpath):
    """读取 comparison.json，产出结论 + 自检结果。"""
    if not os.path.isfile(cpath):
        return {}
    with open(cpath, encoding="utf-8") as f:
        d = json.load(f)
    groups = d.get("groups", [])
    anomalies = d.get("anomalies", [])
    suspicious = d.get("suspicious", [])

    red = green = 0
    amt_bad = []
    totals = {}
    for g in groups:
        for r in g.get("rows", []):
            if r.get("source_type") == "upload":
                if r.get("flag") == "expensive":
                    red += 1
                elif r.get("flag") == "cheap":
                    green += 1
            if r.get("amount_check") == "不符":
                amt_bad.append({
                    "supplier": r.get("supplier"), "name": r.get("name"),
                    "amount_reported": r.get("amount_reported"),
                    "note": "; ".join(r.get("norm_reasons") or [])[:160],
                })
            if r.get("source_type") == "upload" and r.get("amount_std"):
                totals[r.get("supplier")] = totals.get(r.get("supplier"), 0) + r["amount_std"]

    # 金额守恒校验：各来源归一化金额合计 vs 源报价单金额列合计
    # 口径对齐：源单金额列可能是未税或含税，逐行按其自身税基折算到"含税"再累计，
    # 与归一化金额（含税口径）比对。
    per_sup_amount = {}
    for g in groups:
        for r in g.get("rows", []):
            if r.get("source_type") != "upload" or not r.get("amount_reported"):
                continue
            k = r.get("supplier")
            try:
                val = float(r["amount_reported"])
            except (TypeError, ValueError):
                continue
            rate = float(r.get("tax_rate") or 0)
            basis_incl = r.get("amount_tax_included")
            if basis_incl is None:
                basis_incl = bool(r.get("tax_included", True))
            if not basis_incl:                       # 未税金额 → 折成含税
                val *= (1 + rate)
            per_sup_amount[k] = per_sup_amount.get(k, 0) + val

    conservation = []
    for sup, tot in totals.items():
        src = per_sup_amount.get(sup)
        if src is None:
            continue
        diff = tot - src
        conservation.append({
            "supplier": sup, "normalized_total": round(tot, 2),
            "source_total": round(src, 2), "compare_basis": "源单金额列折算至含税",
            "diff": round(diff, 2),
            "ok": abs(diff) < max(1.0, abs(src) * 0.001),
        })

    # 判定是否需要人工介入。
    # 分两级：
    #   blocking —— 必须人工处理（数据自相矛盾，不处理就会得出错误结论）
    #   notice   —— 需要知悉但不阻塞（如源单如实写明"面议/暂不报价"，属正常业务信息）
    blocking = []
    notice = []
    if amt_bad:
        blocking.append(f"{len(amt_bad)} 条「单价×数量」与源单金额列不符，必须回源单核对")
    for c in conservation:
        if not c["ok"]:
            blocking.append(f"{c['supplier']} 归一化金额合计与源单总额差 {c['diff']:,.2f}，需复核单位口径")
    if suspicious:
        notice.append(f"{len(suspicious)} 组疑似同款未合并，确认是否同一品项（不影响当前结论，仅影响覆盖范围）")
    noq = sum(1 for g in groups for r in g.get("rows", []) if r.get("flag") == "no_quote")
    if noq:
        notice.append(f"{noq} 条源单未报出价格（面议/暂不报价），已排除出比较，详见异常表")
    n_uom_conflict = sum(1 for g in groups for r in g.get("rows", [])
                         if r.get("flag") == "uncomparable" and "量纲不同" in (r.get("flag_reason") or ""))
    if n_uom_conflict:
        blocking.append(f"{n_uom_conflict} 条价格单位与数量单位量纲不同，需补 qty_to_price_uom 才能比较")

    return {
        "group_count": len(groups),
        "red_count": red, "green_count": green,
        "anomalies": anomalies, "suspicious": suspicious,
        "amount_check_failed": amt_bad,
        "amount_conservation": conservation,
        "per_supplier_total": {k: round(v, 2) for k, v in totals.items()},
        "best_source": (min(totals, key=totals.get) if totals else None),
        "needs_human": blocking + notice,
        "blocking": blocking,
        "notice": notice,
        "auto_ok": not blocking,
    }


def main():
    ap = argparse.ArgumentParser(description="比价一键流水线：抽取 → 比价 → 标色 → 自检")
    ap.add_argument("inputs", nargs="*", help="报价单文件或目录")
    ap.add_argument("--work-dir", default="quote-compare-output")
    ap.add_argument("--quotes", default="", help="已有 quotes.json（跳过抽取步骤）")
    ap.add_argument("--supplier-map", default="", help="JSON：文件名 → 供应商名")
    ap.add_argument("--supplier", default="", help="统一供应商名")
    ap.add_argument("--source-type", default="upload", choices=["upload", "library", "web"])
    ap.add_argument("--suffix", default="_标色版", help="标色副本后缀")
    ap.add_argument("--fx", default="", help="汇率 JSON")
    ap.add_argument("--tolerance", type=float, default=0.05)
    ap.add_argument("--baseline", default="median")
    ap.add_argument("--include-web-in-baseline", action="store_true")
    ap.add_argument("--stop-after", default="", choices=["", "extract", "compare"])
    ap.add_argument("--no-mark", action="store_true", help="跳过标色")
    args = ap.parse_args()

    t0 = time.time()
    work = os.path.abspath(args.work_dir)
    os.makedirs(work, exist_ok=True)
    qpath = os.path.join(work, "quotes.json")
    report = {
        "generated_at": today(), "work_dir": work,
        "command": " ".join(sys.argv), "steps": [], "errors": [],
    }

    # ---------- 1. 抽取 ----------
    if args.quotes:
        print(f"[1/3] 使用已有 quotes.json：{args.quotes}")
        shutil.copy2(args.quotes, qpath)
        report["steps"].append({"step": "extract", "returncode": 0, "seconds": 0,
                                "stdout_tail": [f"复用 {args.quotes}"]})
    else:
        if not args.inputs:
            print("[ERROR] 未提供报价单，也没有 --quotes")
            sys.exit(2)
        import extract_quotes

        def _do_extract():
            argv = ["extract_quotes.py", *args.inputs,
                    "--out", qpath, "--source-type", args.source_type]
            if args.supplier_map:
                argv += ["--supplier-map", args.supplier_map]
            if args.supplier:
                argv += ["--supplier", args.supplier]
            sys.argv = argv
            extract_quotes.main()

        ok = run_step("[1/3] 抽取报价单 → quotes.json", _do_extract, report, t0,
                      desc="extract_quotes.py " + " ".join(args.inputs))
        if not ok:
            report["errors"].append("抽取失败")
            dump_json(os.path.join(work, "run_report.json"), report)
            sys.exit(2)

    report["quotes"] = summarize_quotes(qpath)
    if args.stop_after == "extract":
        report["stopped_after"] = "extract"
        report["elapsed_seconds"] = round(time.time() - t0, 2)
        dump_json(os.path.join(work, "run_report.json"), report)
        print(f"\n[OK] 已停止于抽取阶段，报告：{os.path.join(work, 'run_report.json')}")
        return

    # ---------- 2. 比价 ----------
    import compare_prices

    def _do_compare():
        argv = ["compare_prices.py", "--quotes", qpath, "--out-dir", work,
                "--tolerance", str(args.tolerance), "--baseline", args.baseline]
        if args.fx:
            argv += ["--fx", args.fx]
        if args.include_web_in_baseline:
            argv.append("--include-web-in-baseline")
        sys.argv = argv
        compare_prices.main()

    ok = run_step("[2/3] 归一化与比价 → comparison.xlsx / summary.docx",
                  _do_compare, report, t0, desc="compare_prices.py --quotes quotes.json")
    if not ok:
        report["errors"].append("比价失败")
        dump_json(os.path.join(work, "run_report.json"), report)
        sys.exit(2)

    report["result"] = analyze_result(os.path.join(work, "comparison.json"))
    if args.stop_after == "compare":
        report["stopped_after"] = "compare"
        report["elapsed_seconds"] = round(time.time() - t0, 2)
        dump_json(os.path.join(work, "run_report.json"), report)
        print(f"\n[OK] 已停止于比价阶段，报告：{os.path.join(work, 'run_report.json')}")
        return

    # ---------- 3. 标色 ----------
    if args.no_mark:
        print("[3/3] 按要求跳过标色")
        report["steps"].append({"step": "mark", "returncode": 0, "seconds": 0,
                                "stdout_tail": ["--no-mark 已跳过"]})
    else:
        import mark_sources

        def _do_mark():
            sys.argv = ["mark_sources.py",
                        "--marks", os.path.join(work, "marks.json"),
                        "--out-dir", work, "--suffix", args.suffix]
            mark_sources.main()

        ok = run_step("[3/3] 源报价单副本标红/标绿", _do_mark, report, t0,
                      desc=f"mark_sources.py --suffix {args.suffix}")
        if not ok:
            report["errors"].append("标色失败（部分条目见 unmarked.md）")

    # ---------- 报告 ----------
    rp = os.path.join(work, "run_report.json")
    mr_path = os.path.join(work, "mark_report.json")
    if os.path.isfile(mr_path):
        with open(mr_path, encoding="utf-8") as f:
            mr = json.load(f)
        report["mark"] = {
            "total_marks": mr.get("total_marks"), "marked": mr.get("marked"),
            "outputs": [os.path.basename(p) for p in mr.get("outputs", [])],
            "stale_outputs": [os.path.basename(p) for p in mr.get("stale_outputs", [])],
        }
        um = os.path.join(work, "unmarked.md")
        if os.path.isfile(um):
            with open(um, encoding="utf-8") as f:
                txt = f.read().strip()
            report["mark"]["unmarked"] = [ln for ln in txt.splitlines() if ln.strip().startswith("-")]
    elif not args.no_mark:
        report["mark"] = {"error": "未生成 mark_report.json，标色可能失败"}

    report["elapsed_seconds"] = round(time.time() - t0, 2)
    report["deliverables"] = sorted(
        os.path.basename(p) for p in [
            os.path.join(work, "comparison.xlsx"), os.path.join(work, "summary.docx"),
            os.path.join(work, "comparison.json"), os.path.join(work, "marks.json"),
        ] if os.path.isfile(p))
    report["deliverables"] += sorted(
        os.path.basename(p) for p in os.listdir(work)
        if args.suffix and args.suffix in p and os.path.isfile(os.path.join(work, p)))

    dump_json(rp, report)

    # ---------- 控制台结论 ----------
    res = report.get("result", {})
    print("\n" + "=" * 66)
    print(f"[OK] 全部完成，用时 {report['elapsed_seconds']}s")
    print(f"     条目 {report['quotes'].get('item_count', 0)} 条 → {res.get('group_count', 0)} 个品项组")
    print(f"     标红 {res.get('red_count', 0)} 处 / 标绿 {res.get('green_count', 0)} 处")
    if res.get("best_source"):
        print(f"     综合最优：{res['best_source']}")
    for c in res.get("amount_conservation", []):
        flag = "OK" if c["ok"] else "不符"
        print(f"     金额守恒 {c['supplier']}: 归一化 {c['normalized_total']:,.2f} vs 源单 {c['source_total']:,.2f} [{flag}]")
    if res.get("blocking"):
        print("\n     ⚠ 必须人工处理（否则结论不可靠）：")
        for n in res["blocking"]:
            print("       - " + n)
    elif res.get("auto_ok"):
        print("     ✓ 无阻断性问题，可直接交付")
    if res.get("notice"):
        print("\n     ℹ 需知悉（不阻塞交付）：")
        for n in res["notice"]:
            print("       - " + n)
    print(f"\n     报告（模型读这一个文件即可）：{rp}")
    print("=" * 66)


if __name__ == "__main__":
    main()
