#!/usr/bin/env python3
"""由对齐结果 JSON 生成「要求—响应—偏离」对照矩阵（Markdown + CSV）。

输入 matrix.json 结构见 references/compliance-matrix.md。
用法:
    python build_matrix.py <matrix.json> [--out-dir <目录>]

产出:
    <out-dir>/偏离表.md   可读的偏离表（含风险汇总）
    <out-dir>/偏离表.csv  机器可读版，供归档/二次处理
"""
import sys
import os
import json
import csv
import argparse

SEV = {"不满足": "高风险", "负偏离": "高风险",
       "部分": "中风险", "无偏离": "无", "正偏离": "加分", "满足": "无"}


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def collect(rows, keys, kind):
    """统一抽取 (id, requirement, our, deviation, evidence, note)。"""
    out = []
    id_key, req_key, our_key, dev_key = keys
    for r in rows:
        out.append({
            "kind": kind,
            "id": r.get(id_key, ""),
            "requirement": r.get(req_key, ""),
            "our": r.get(our_key, ""),
            "deviation": r.get(dev_key, ""),
            "evidence": r.get("evidence", ""),
            "note": r.get("note", ""),
        })
    return out


def risk_of(deviation):
    return SEV.get(deviation, "未知")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("matrix")
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args(argv[1:])

    data = load(args.matrix)
    items = []
    items += collect(data.get("qualification", []),
                     ("req_id", "requirement", "our_evidence", "status"), "资格")
    items += collect(data.get("technical", []),
                     ("spec_id", "requirement", "our_parameter", "deviation"), "技术")
    items += collect(data.get("commercial", []),
                     ("term_id", "requirement", "our_commitment", "deviation"), "商务")

    os.makedirs(args.out_dir, exist_ok=True)
    md_path = os.path.join(args.out_dir, "偏离表.md")
    csv_path = os.path.join(args.out_dir, "偏离表.csv")

    risks = [i for i in items if risk_of(i["deviation"]) in ("高风险", "中风险")]

    # Markdown
    lines = ["# 投标要求—响应—偏离对照表", "",
             f"- 总条目数：{len(items)}",
             f"- 高风险（否决级）条目：{sum(1 for i in items if risk_of(i['deviation'])=='高风险')}",
             f"- 中风险条目：{sum(1 for i in items if risk_of(i['deviation'])=='中风险')}",
             f"- 加分（正偏离）条目：{sum(1 for i in items if risk_of(i['deviation'])=='加分')}",
             ""]
    if risks:
        lines.append("## ⚠️ 风险汇总（须显式处理）")
        for i in risks:
            lines.append(f"- **[{risk_of(i['deviation'])}] {i['id']} {i['kind']}**：{i['requirement']} → 我方：{i['our']}（{i['deviation']}）{('— ' + i['note']) if i['note'] else ''}")
        lines.append("")

    lines.append("## 资格符合性")
    lines.append("| 编号 | 招标要求 | 我方佐证 | 状态 | 备注 |")
    lines.append("|---|---|---|---|---|")
    for i in items:
        if i["kind"] != "资格":
            continue
        lines.append(f"| {i['id']} | {i['requirement']} | {i['our']} | {i['deviation']} | {i['note']} |")

    lines.append("\n## 技术规格响应/偏离")
    lines.append("| 编号 | 招标要求 | 我方参数 | 偏离 | 佐证 | 备注 |")
    lines.append("|---|---|---|---|---|---|")
    for i in items:
        if i["kind"] != "技术":
            continue
        lines.append(f"| {i['id']} | {i['requirement']} | {i['our']} | {i['deviation']} | {i['evidence']} | {i['note']} |")

    lines.append("\n## 商务条款响应/偏离")
    lines.append("| 编号 | 招标条款 | 我方承诺 | 偏离 | 备注 |")
    lines.append("|---|---|---|---|---|")
    for i in items:
        if i["kind"] != "商务":
            continue
        lines.append(f"| {i['id']} | {i['requirement']} | {i['our']} | {i['deviation']} | {i['note']} |")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # CSV
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["类别", "编号", "招标要求/条款", "我方响应", "偏离", "风险", "佐证", "备注"])
        for i in items:
            w.writerow([i["kind"], i["id"], i["requirement"], i["our"],
                        i["deviation"], risk_of(i["deviation"]), i["evidence"], i["note"]])

    print(f"已生成：{md_path}")
    print(f"已生成：{csv_path}")
    print(f"条目 {len(items)}，高风险 {sum(1 for i in items if risk_of(i['deviation'])=='高风险')}，中风险 {sum(1 for i in items if risk_of(i['deviation'])=='中风险')}。")
    if risks:
        print("⚠️ 存在需显式处理的风险条目，详见偏离表.md 风险汇总。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
