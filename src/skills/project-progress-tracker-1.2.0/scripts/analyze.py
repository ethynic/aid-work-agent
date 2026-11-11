#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""证据包 -> AI 待分析包（默认）或 LLM 自动研判结果（可选）

mode=packet : 生成 data/analysis_packet.md，交给 Skill 执行体（当前智能体）研判
mode=api    : 调用 OpenAI 兼容接口，产出 data/analysis.json 并自动合并进 projects.json
             密钥只从环境变量 PPT_LLM_API_KEY 读取，脚本不做可用性探测、不设为阻断项
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

PER_FILE_LIMIT = 6000
PER_PROJECT_LIMIT = 30000

OUTPUT_INSTRUCTION = """
## 输出要求

只输出一个 JSON 对象，不要 Markdown 代码块，不要解释文字。结构：

{
  "meta": {
    "as_of": "<基准日>",
    "overall_status": "健康 | 可控 | 需重点关注 | 高风险",
    "executive_summary": "3~5 句：整体健康度、最该先处理的 2~3 个项目及原因、跨项目共性风险",
    "top_risks": ["按影响面×紧急度排序，最多 5 条"],
    "recommendations": ["3~4 条可执行的组合管理建议"]
  },
  "projects": [
    {
      "id": "p001（已有项目沿用原 id，新项目按顺序新编）",
      "name": "项目名",
      "client": "客户/部门",
      "owner": "负责人",
      "status": "on_track | delayed | overdue",
      "status_reason": "一句话判定理由，必须含具体日期或数字",
      "confidence": 0.0,
      "progress": 0,
      "planned_progress": 0,
      "start_date": "YYYY-MM-DD",
      "due_date": "YYYY-MM-DD",
      "risk_level": "high | medium | low",
      "closed": false,
      "summary": "一句话进度结论",
      "highlights": ["近期关键进展 2~4 条"],
      "risks": ["具体风险 2~4 条"],
      "next_actions": ["可执行的下一步 2~4 条"],
      "evidence_gaps": ["材料缺口 / 数据冲突；无则空数组"],
      "milestones": [{"name":"", "planned":"YYYY-MM-DD", "actual":"YYYY-MM-DD 或 null", "state":"done|done_late|pending|overdue"}],
      "sources": [{"file":"出处文件名", "type":"report|communication|milestone|acceptance|other", "date":"YYYY-MM-DD", "excerpt":"支撑结论的原文片段"}],
      "communications": [{"date":"YYYY-MM-DD", "channel":"", "from":"", "excerpt":"", "signal":"blocked|progress|resolved|neutral"}],
      "status_history": [{"as_of":"YYYY-MM-DD", "status":"", "progress":0}]
    }
  ]
}

硬性要求：
1. 不编造。任何数字、日期、风险都必须能在上面的证据里找到出处；找不到就写进 evidence_gaps 并下调 confidence。
2. status 只能是三态之一，严格按判定优先级执行；超时与延误不许混用。
3. sources[].excerpt 必须是证据原文片段（可截短），不允许改写成自己的话。
4. 沟通记录里出现「已解决/已闭环」等解除信号时，不得据此判延误。
5. 全局总结禁止写成各项目摘要的流水账。
""".strip()


def load_packet_input(root: Path, skill_root: Path) -> tuple[dict, str]:
    path = root / "data" / "extracted.json"
    if not path.exists():
        raise SystemExit("[analyze] 缺少 data/extracted.json，请先运行 extract_sources.py")
    payload = json.loads(path.read_text(encoding="utf-8"))
    # 判定规则是技能只读资源，从技能目录读；--root 只承载可写数据
    rules = (skill_root / "references" / "status-rules.md").read_text(encoding="utf-8")
    return payload, rules


def build_packet(root: Path, as_of: str, skill_root: Path) -> str:
    payload, rules = load_packet_input(root, skill_root)
    by_project: dict[str, list[dict]] = {}
    for r in payload["records"]:
        by_project.setdefault(r["project"], []).append(r)

    lines = [
        "# 项目进度 AI 待分析包", "",
        f"- 基准日 **as_of = {as_of}**（所有「已过期」判断以此为准）",
        f"- 项目数：{len(by_project)}；证据文件数：{payload['meta']['source_count']}",
        "- 本包由 extract_sources.py 自动抽取，原始资料只读。",
        "", "---", "", "## 一、判定规则（权威口径）", "", rules, "", "---", "",
        "## 二、按项目分组的证据", "",
    ]

    for project, recs in sorted(by_project.items()):
        lines += [f"### 项目：{project}", "", f"共 {len(recs)} 份材料：", ""]
        lines += ["| 文件 | 类型 | 日期 | 字数 | 解析 |", "|---|---|---|---:|---|"]
        for r in recs:
            lines.append(f"| `{r['file']}` | {r['type']} | {r['date'] or '—'} | {r['chars']} | {r['method']} |")
        lines.append("")
        budget = PER_PROJECT_LIMIT
        for r in sorted(recs, key=lambda x: x["date"] or "", reverse=True):
            if budget <= 0:
                lines.append(f"> 其余材料因长度上限省略，完整内容见 data/extracted.json（{r['file']} 等）。")
                break
            body = r["text"][: min(PER_FILE_LIMIT, budget)]
            budget -= len(body)
            lines += [
                f"#### 证据 · `{r['file']}`（{r['type']}，{r['date'] or '日期未知'}）", "",
                "```text", body if body.strip() else "[无可提取文本]", "```", "",
            ]
            if r["warning"]:
                lines.append(f"> 注：{r['warning']}")
                lines.append("")
        lines += ["---", ""]

    lines += ["## 三、已有看板数据（用于对齐 id 与历史，非新证据）", ""]
    pj = root / "data" / "projects.json"
    if pj.exists():
        try:
            cur = json.loads(pj.read_text(encoding="utf-8"))
            brief = [
                {"id": p.get("id"), "name": p.get("name"), "status": p.get("status"),
                 "progress": p.get("progress"), "status_history": p.get("status_history", [])}
                for p in cur.get("projects", [])
            ]
            lines += ["```json", json.dumps(brief, ensure_ascii=False, indent=2), "```", ""]
        except Exception:
            lines.append("（读取失败，忽略）")
    else:
        lines.append("（尚无，需新建）")
    lines += ["", "---", "", "## 四、任务", "", OUTPUT_INSTRUCTION, ""]
    return "\n".join(lines)


def call_llm(packet: str) -> str:
    key = os.environ.get("PPT_LLM_API_KEY", "")
    base = os.environ.get("PPT_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("PPT_LLM_MODEL", "gpt-4o-mini")
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "你是资深 PMO 项目组合分析师。严格按用户给出的规则与 JSON 结构输出，只输出 JSON，不编造证据。"},
            {"role": "user", "content": packet},
        ],
        "temperature": 0.2,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


def parse_llm_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.M)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型返回内容里找不到 JSON 对象")
    return json.loads(raw[start:end + 1])


def merge_analysis(root: Path, analysis: dict, as_of: str) -> None:
    pj = root / "data" / "projects.json"
    if pj.exists():
        current = json.loads(pj.read_text(encoding="utf-8"))
    else:
        current = {"meta": {}, "projects": []}
    old = {p.get("id"): p for p in current.get("projects", [])}
    merged = []
    for p in analysis.get("projects", []):
        base = dict(old.get(p.get("id"), {}))
        base.update(p)
        hist = list(base.get("status_history", []))
        if not hist or hist[-1].get("as_of") != as_of:
            hist.append({"as_of": as_of, "status": base.get("status"), "progress": base.get("progress")})
        base["status_history"] = hist[-12:]
        base["updated_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        merged.append(base)
    meta = dict(current.get("meta", {}))
    meta.update(analysis.get("meta", {}))
    meta["as_of"] = as_of
    meta["generated_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    meta["source_mode"] = meta.get("source_mode", "upload")
    meta["demo"] = False
    pj.write_text(json.dumps({"meta": meta, "projects": merged}, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 AI 待分析包 / 可选 LLM 自动研判")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                    help="数据目录（可写）：data/ 与产物都写在这里；技能目录只读时必须指向独立工作目录")
    ap.add_argument("--skill-root", default=str(Path(__file__).resolve().parent.parent),
                    help="技能只读资源目录（references/ 等），一般保持默认")
    ap.add_argument("--as-of", default=dt.date.today().isoformat())
    ap.add_argument("--mode", choices=["packet", "api"], default="packet")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    skill_root = Path(args.skill_root).resolve()
    (root / "data").mkdir(parents=True, exist_ok=True)

    packet = build_packet(root, args.as_of, skill_root)
    (root / "data" / "analysis_packet.md").write_text(packet, encoding="utf-8")
    print(f"[analyze] 待分析包 -> data/analysis_packet.md（{len(packet)} 字符）")

    if args.mode == "api":
        if not os.environ.get("PPT_LLM_API_KEY"):
            print("[analyze] 未设置 PPT_LLM_API_KEY，已降级为 packet 模式；可直接由智能体研判，不影响后续流程。")
            return 0
        try:
            raw = call_llm(packet)
            analysis = parse_llm_json(raw)
        except Exception as exc:
            print(f"[analyze] LLM 调用失败：{exc}；待分析包仍可用，流程不中断。")
            return 0
        (root / "data" / "analysis.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        merge_analysis(root, analysis, args.as_of)
        print(f"[analyze] 研判结果 -> data/analysis.json，并已合并进 data/projects.json（{len(analysis.get('projects', []))} 个项目）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
