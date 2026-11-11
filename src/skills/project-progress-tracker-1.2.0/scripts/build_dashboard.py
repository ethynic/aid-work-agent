#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data/projects.json + assets/dashboard.template.html -> dashboard/index.html

单文件离线看板：数据内嵌，双击即可打开，无需本地服务。
校验必填字段与三态取值，重算 totals / days_remaining，缺字段直接报错。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REQUIRED = ["id", "name", "status", "status_reason", "confidence", "progress", "planned_progress", "risk_level", "summary"]
STATUSES = {"on_track", "delayed", "overdue"}
PLACEHOLDER = "__DASHBOARD_DATA__"


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat((value or "")[:10])
    except (ValueError, TypeError):
        return None


def validate_and_normalize(data: dict, as_of: str) -> tuple[dict, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if "projects" not in data or not isinstance(data["projects"], list):
        raise SystemExit("[build] projects.json 缺少 projects 数组")
    meta = data.get("meta", {})
    meta["as_of"] = as_of
    meta.setdefault("title", "多项目进度看板")
    meta.setdefault("subtitle", "客户项目组合 · 进度健康度巡检")
    meta.setdefault("source_mode", "upload")

    today = parse_date(as_of) or dt.date.today()
    seen: set[str] = set()
    projects = []
    for i, p in enumerate(data["projects"]):
        label = p.get("name") or f"第 {i + 1} 项"
        for k in REQUIRED:
            if k not in p or p[k] is None or p[k] == "":
                errors.append(f"{label}：缺必填字段 `{k}`")
        if p.get("status") not in STATUSES:
            errors.append(f"{label}：status 非法值 {p.get('status')!r}（只允许 on_track / delayed / overdue）")
        if p.get("id") in seen:
            errors.append(f"{label}：id 重复 {p.get('id')!r}")
        seen.add(p.get("id"))
        for k, hi in (("progress", 100), ("planned_progress", 100), ("confidence", 1)):
            v = p.get(k, 0)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 or v > hi:
                errors.append(f"{label}：{k} 应为 0~{hi} 的数值，实际 {v!r}")
        if p.get("risk_level") not in {"high", "medium", "low"}:
            warnings.append(f"{label}：risk_level 未识别，回退为 medium")
            p["risk_level"] = "medium"

        for k in ("highlights", "risks", "next_actions", "evidence_gaps", "milestones", "sources", "communications", "status_history"):
            p.setdefault(k, [])
        for k in ("client", "owner", "start_date", "due_date", "updated_at"):
            p.setdefault(k, "")
        p.setdefault("closed", False)
        p.setdefault("demo", bool(meta.get("demo")))

        due = parse_date(p.get("due_date", ""))
        p["days_remaining"] = (due - today).days if due else None
        if p["days_remaining"] is not None and p["days_remaining"] < 0 and not p["closed"]:
            p["overdue_days"] = -p["days_remaining"]
        else:
            p["overdue_days"] = 0
        projects.append(p)

    counts = {s: sum(1 for p in projects if p["status"] == s) for s in STATUSES}
    total = len(projects)
    meta["totals"] = {
        "total": total,
        "on_track": counts["on_track"],
        "delayed": counts["delayed"],
        "overdue": counts["overdue"],
        "avg_progress": round(sum(p["progress"] for p in projects) / total, 1) if total else 0,
        "high_risk": sum(1 for p in projects if p["risk_level"] == "high"),
    }
    meta["generated_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    meta.setdefault("overall_status", "—")
    meta.setdefault("executive_summary", "暂无总体总结，请先运行 AI 研判。")
    meta.setdefault("top_risks", [])
    meta.setdefault("recommendations", [])
    return {"meta": meta, "projects": projects}, errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="构建单文件离线项目进度看板")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                    help="数据目录（可写）：读 data/projects.json，输出 dashboard/ 与 logs/")
    ap.add_argument("--skill-root", default=str(Path(__file__).resolve().parent.parent),
                    help="技能只读资源目录（assets/ 模板），一般保持默认")
    ap.add_argument("--as-of", default="", help="覆盖基准日；默认取 projects.json 的 meta.as_of 或今天")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    skill_root = Path(args.skill_root).resolve()
    src = root / "data" / "projects.json"
    tpl = skill_root / "assets" / "dashboard.template.html"
    if not src.exists():
        raise SystemExit("[build] 缺少 data/projects.json；可先运行 make_demo.py 生成演示数据")
    if not tpl.exists():
        raise SystemExit("[build] 缺少 assets/dashboard.template.html")

    raw = json.loads(src.read_text(encoding="utf-8"))
    as_of = args.as_of or raw.get("meta", {}).get("as_of") or dt.date.today().isoformat()
    data, errors, warnings = validate_and_normalize(raw, as_of)
    if errors:
        for e in errors:
            print(f"[build] 校验失败：{e}")
        raise SystemExit(f"[build] 共 {len(errors)} 处错误，未生成看板。字段规范见 references/data-schema.md")

    # 把规范化结果（含重算的 totals / days_remaining / 数组默认值）回写真源，
    # 保证 /api/data、--mode api 等消费方拿到的都是完整数据，而不是缺派生字段的草稿。
    src.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\!--")
    html = tpl.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise SystemExit(f"[build] 模板缺少占位符 {PLACEHOLDER}")
    html = html.replace(PLACEHOLDER, payload)

    outdir = root / "dashboard"
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "index.html"
    out.write_text(html, encoding="utf-8")

    (root / "logs").mkdir(parents=True, exist_ok=True)
    t = data["meta"]["totals"]
    report = "\n".join([
        "# 看板构建报告", "",
        f"- 生成时间：{data['meta']['generated_at']}",
        f"- 基准日：{as_of}",
        f"- 项目数：{t['total']}（正常 {t['on_track']} / 延误 {t['delayed']} / 超时 {t['overdue']}）",
        f"- 平均完成率：{t['avg_progress']}%",
        f"- 输出：{out}（{out.stat().st_size:,} 字节）",
        "", "## 提示", "",
    ] + [f"- {w}" for w in warnings] or ["- 无"])
    (root / "logs" / "build_report.md").write_text(report + "\n", encoding="utf-8")
    for w in warnings:
        print(f"[build] 提示：{w}")
    print(f"[build] 看板已生成 -> {out}")
    print(f"[build] {t['total']} 个项目：正常 {t['on_track']} / 延误 {t['delayed']} / 超时 {t['overdue']}，平均完成率 {t['avg_progress']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
