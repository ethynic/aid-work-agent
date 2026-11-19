#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 3 步编排：按 analysis.json + 已收集的答案，算出「这一轮还需要问什么」。

用途
- 一次问答只能问「当前已经解锁的问题」；被 depends_on 挡住的问题先不问。
- 用户回答后，第二轮再跑一次，只输出新解锁的问题（复杂场景可能 N 轮）。
- 全部必填项都有答案时 ready_to_fill=true，直接进第 4 步回填。

本脚本不感知任何业务场景：问题全部来自 analysis.json 的 fields / list_tables。

用法
    python3 probe.py <analysis.json> [answers.json|-] [--round 2]
                     [--merge 新答案.json] [--merge-out 合并后.json]
                     [--json-only]

--merge 把本轮新答案并入已有答案（后面的覆盖前面的），并把轮次记录写进 _meta。
"""

import os
import sys
import json
import argparse


# --------------------------------------------------------------------------
# 依赖判定：只有一个入口，语义在 references/analysis_schema.md 里定义
# --------------------------------------------------------------------------
def _norm(v):
    return "" if v is None else str(v).strip()


def dep_eval(dep, values):
    """返回 (是否满足, 缺失/未满足的依赖键列表)。

    dep 为空/None 视为始终满足。
    支持形态：
        {"key": "x"}                       # x 有值即满足
        {"key": "x", "equals": "是"}        # 等于
        {"key": "x", "not_equals": "否"}
        {"key": "x", "in": ["a","b"]}       # 取值在集合内
        {"key": "x", "not_empty": true}     # 显式写法，等价 {"key":"x"}
        {"all": [dep, dep]}                 # 全部满足
        {"any": [dep, dep]}                 # 任一满足
    """
    if not dep:
        return True, []
    if "all" in dep:
        miss = []
        for d in dep["all"] or []:
            ok, m = dep_eval(d, values)
            if not ok:
                miss.extend(m or [d.get("key")])
        return (not miss), miss
    if "any" in dep:
        miss = []
        for d in dep["any"] or []:
            ok, m = dep_eval(d, values)
            if ok:
                return True, []
            miss.extend(m or [d.get("key")])
        return False, miss

    key = dep.get("key")
    if not key:
        return True, []
    val = _norm(values.get(key))
    if not val:
        return False, [key]
    if "equals" in dep and val != _norm(dep["equals"]):
        return False, [key]
    if "not_equals" in dep and val == _norm(dep["not_equals"]):
        return False, [key]
    if "in" in dep:
        opts = [_norm(x) for x in (dep.get("in") or [])]
        if val not in opts:
            return False, [key]
    return True, []


def has_value(v):
    if isinstance(v, list):
        return len(v) > 0
    return bool(_norm(v))


# --------------------------------------------------------------------------
# 计划计算
# --------------------------------------------------------------------------
def build_plan(analysis, answers, round_no):
    values = {k: v for k, v in (answers or {}).items() if not str(k).startswith("_")}

    fields = analysis.get("fields", []) or []
    open_fields, blocked, answered, auto = [], [], [], []

    for fld in fields:
        key = fld.get("key")
        if not key:
            continue
        phase = fld.get("phase") or 1
        dep = fld.get("depends_on")
        ok_dep, blockers = dep_eval(dep, values)

        if has_value(values.get(key)):
            answered.append({"key": key, "label": fld.get("label"), "phase": phase})
            continue

        # default / formula 字段由编排层自动解决（fill_doc 会填默认 / 算公式），
        # 不向用户提问。否则 ready_to_fill 会被这些字段永久卡住。
        if fld.get("formula") or has_value(fld.get("default")):
            auto.append({"key": key, "label": fld.get("label") or key,
                         "phase": phase,
                         "via": "formula" if fld.get("formula") else "default"})
            continue

        item = {
            "key": key,
            "label": fld.get("label") or key,
            "question": fld.get("question") or fld.get("label") or key,
            "required": bool(fld.get("required")),
            "phase": phase,
            "default": fld.get("default"),
            "kind": fld.get("kind") or ("cell" if _first_kind(fld) == "cell" else "text"),
            "hint": fld.get("hint"),
        }
        if not ok_dep:
            item["depends_on"] = dep
            item["blocked_by"] = blockers
            # 被依赖挡住：默认不问也不计入缺失；skip_when_blocked=false 时仍要问
            if fld.get("skip_when_blocked", True) is False:
                item["required"] = bool(fld.get("required"))
                open_fields.append(item)
            else:
                blocked.append(item)
            continue
        open_fields.append(item)

    # 清单表：rows_source 没有条目时，这一轮要收集
    open_lists = []
    for st in (analysis.get("list_tables") or analysis.get("service_tables") or []):
        src = st.get("rows_source", "items")
        items = answers.get(src) or []
        cols = [{"key": c.get("key"), "label": c.get("label") or c.get("key")}
                for c in (st.get("columns") or [])]
        # min_items=0 表示允许空表：空清单不算待收集，不阻塞 ready_to_fill。
        min_items = st.get("min_items", 1)
        if isinstance(min_items, bool):
            min_items = 1 if min_items else 0
        try:
            min_items = int(min_items)
        except (TypeError, ValueError):
            min_items = 1
        if not items and min_items > 0:
            open_lists.append({
                "rows_source": src,
                "table_index": st.get("table_index"),
                "min_items": min_items,
                "columns": cols,
                "item_hint": st.get("item_hint"),
            })

    required_open = [f for f in open_fields if f["required"]]
    ready = not required_open and not open_lists

    # 按阶段分组：先问 phase 小的
    phases = {}
    for f in open_fields:
        phases.setdefault(str(f["phase"]), []).append(f)

    return {
        "round": round_no,
        "ready_to_fill": ready,
        "counts": {
            "fields_total": len(fields),
            "answered": len(answered),
            "auto_resolved": len(auto),
            "open": len(open_fields),
            "open_required": len(required_open),
            "blocked": len(blocked),
            "open_lists": len(open_lists),
        },
        "auto_resolved": auto,
        "open_fields": open_fields,
        "blocked_fields": blocked,
        "open_lists": open_lists,
        "by_phase": phases,
        "next_phase": (min((f["phase"] for f in open_fields), default=None)),
    }


def _first_kind(fld):
    locs = fld.get("locators") or []
    return locs[0].get("kind", "literal") if locs else "literal"


def merge_answers(base, new, round_no, note=None):
    out = dict(base or {})
    meta = dict(out.get("_meta") or {})
    rounds = list(meta.get("rounds") or [])
    rounds.append({"round": round_no,
                   "answered_keys": [k for k in new if not str(k).startswith("_")],
                   "note": note})
    meta["rounds"] = rounds
    meta["last_round"] = round_no
    out["_meta"] = meta
    for k, v in (new or {}).items():
        if str(k).startswith("_") and k != "_meta":
            continue
        if k == "_meta":
            continue
        out[k] = v
    return out


# --------------------------------------------------------------------------
def load_json(path):
    if path in (None, "-"):
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="计算本轮还需要问哪些问题")
    ap.add_argument("analysis")
    ap.add_argument("answers", nargs="?", default=None,
                    help="已收集答案；省略表示尚无答案，传 '-' 从 stdin 读")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--merge", help="本轮新答案（JSON 文件或 '-'）")
    ap.add_argument("--merge-out", help="把合并后的答案写到该文件")
    ap.add_argument("--note", help="本轮备注，写进 _meta.rounds")
    ap.add_argument("--json-only", action="store_true", help="只输出 JSON，不附人读摘要")
    args = ap.parse_args()

    analysis = load_json(args.analysis)
    answers = load_json(args.answers) if args.answers else {}

    if args.merge:
        new = load_json(args.merge)
        answers = merge_answers(answers, new, args.round, args.note)
        target = args.merge_out or args.answers
        if target and target != "-":
            os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(answers, f, ensure_ascii=False, indent=1)

    plan = build_plan(analysis, answers, args.round)

    if not args.json_only:
        c = plan["counts"]
        print("第 %d 轮 | 已答 %d / 共 %d 字段 | 本轮待问 %d（必填 %d）| 依赖未满足暂不问 %d | 待收集清单 %d | 可回填 %s"
              % (plan["round"], c["answered"], c["fields_total"], c["open"],
                 c["open_required"], c["blocked"], c["open_lists"],
                 plan["ready_to_fill"]))

    print(json.dumps(plan, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
