#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官网报告 -> 分块紧凑事实表（纯脚本，不调用任何大模型）。

为什么需要
----------
200 家企业时，`probe/*.json` 里每份 `site_text` 最多 6000 字（外层巡检
执行器的截断口径），200 份约 120 万字符 —— 直接读进上下文必定爆窗；而 `email-copybook.md` 又硬性要求
每封邮件至少 2 处「只对该公司成立的细节」。本脚本用纯规则把原文压成
**每家 ≤ 300 字**的事实表，并按 `--chunk` 切成小块，让每个模型 turn 只读
一块（25 家 × 300 字 ≈ 7500 字），上下文与输出量都可控。

用法
----
python scripts/facts_builder.py \
    --probe <campaign>/probe --out <campaign>/facts.jsonl \
    --keywords "valve,brass,fittings,DIN" --chunk 25

关键词来自 brief.md 的产品词 / 品类词（由模型提取一次即可，1 个 turn），
也可写成文件用 --keywords-file 传入。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

STOPWORDS = set("""
the and for with from that this have has are was were will your you our their
they them into about more other than when which such also been being over under
产品 公司 我们 您 他们 可以 提供 服务 主要 以及 并且 通过 进行 一个 目前 欢迎
联系 地址 电话 邮箱 首页 更多 关于 新闻 有限公司 有限 公司 集团 网站 所有
""".split())


def split_sentences(text: str):
    parts = re.split(r"(?<=[。！？!?.;；])\s*|\n+", text or "")
    return [p.strip() for p in parts if p and p.strip()]


def extract_terms(text: str, keywords, topn: int = 6):
    low = (text or "").lower()
    hits = [k for k in keywords if k and k.lower() in low]
    words = re.findall(r"\b[a-z][a-z-]{3,}\b", low)
    freq = Counter(w for w in words if w not in STOPWORDS and len(w) > 3)
    rest = [w for w, _ in freq.most_common(topn * 2) if w not in hits]
    return (hits + rest)[:topn]


def pick_evidence(sentences, keywords, maxn: int, per_sent: int):
    scored = []
    for s in sentences:
        score = sum(1 for k in keywords if k and k.lower() in s.lower())
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    picked = [s[:per_sent] for _, s in scored[:maxn]]
    if not picked and sentences:
        picked = [sentences[0][:per_sent]]
    # 去重
    seen, out = set(), []
    for s in picked:
        key = s[:40]
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def load_reports(probe_dir: Path):
    index_path = probe_dir / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        entries = index.get("results") or []
    else:
        entries = [{"file": p.name, "task": {}} for p in sorted(probe_dir.glob("*.json"))]
    for entry in entries:
        f = probe_dir / entry["file"]
        if not f.exists():
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        payload = rec.get("result")
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if not isinstance(payload, dict):
            payload = {}
        # 兼容无 result 包裹的报告（如外层管线直接产出 site_text/emails 顶层字段）
        if not payload and (rec.get("site_text") or rec.get("emails")):
            payload = rec
        task = rec.get("task") or entry.get("task") or {}
        yield task, payload


def build_fact(task, payload, keywords, max_chars, max_evidence, per_sent):
    site_text = payload.get("site_text") or ""
    sentences = split_sentences(site_text)
    emails = payload.get("emails") or []
    email_list = []
    for e in emails:
        addr = e.get("email") if isinstance(e, dict) else str(e)
        if addr and addr not in email_list:
            email_list.append(addr)
        if len(email_list) >= 2:
            break

    fact = {
        "company": task.get("company") or payload.get("company") or "",
        "country": task.get("country") or "",
        "website": task.get("url") or payload.get("base_url") or "",
        "terms": extract_terms(site_text, keywords),
        "evidence": pick_evidence(sentences, keywords, max_evidence, per_sent),
        "emails": email_list,
        "pages": len(payload.get("pages_fetched") or []),
    }
    # 硬性压缩到每行 <= max_chars 字符，宁可少给证据也不撑爆上下文
    while len(json.dumps(fact, ensure_ascii=False)) > max_chars and fact["evidence"]:
        fact["evidence"].pop()
    while len(json.dumps(fact, ensure_ascii=False)) > max_chars and fact["terms"]:
        fact["terms"].pop()
    return fact


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把批量落盘的官网报告压成分块紧凑事实表（纯规则，不调模型）")
    ap.add_argument("--probe", required=True, help="batch_delegate.py scrape 的 --out-dir（含 index.json）")
    ap.add_argument("--out", required=True, help="输出 facts.jsonl 路径")
    ap.add_argument("--keywords", default="", help="产品词/品类词，逗号分隔")
    ap.add_argument("--keywords-file", default="", help="每行一个关键词的文件（优先于 --keywords）")
    ap.add_argument("--chunk", type=int, default=25, help="每块家数；0=不切块（默认 25）")
    ap.add_argument("--max-chars", type=int, default=320, help="每家事实表最大字符数（默认 320）")
    ap.add_argument("--max-evidence", type=int, default=2, help="每家最多保留几条官网证据句")
    ap.add_argument("--per-sentence", type=int, default=110, help="单条证据句最大字符数")
    args = ap.parse_args()

    if args.keywords_file:
        keywords = [l.strip() for l in Path(args.keywords_file).read_text(
            encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    else:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    keywords = [k.lower() for k in keywords]

    probe_dir = Path(args.probe).expanduser()
    out_path = Path(args.out).expanduser()
    if not probe_dir.exists():
        print(f"[错误] probe 目录不存在: {probe_dir}", file=sys.stderr)
        return 3
    out_path.parent.mkdir(parents=True, exist_ok=True)

    facts = []
    for task, payload in load_reports(probe_dir):
        facts.append(build_fact(task, payload, keywords, args.max_chars,
                                args.max_evidence, args.per_sentence))
    if not facts:
        print("[错误] 没有可用的报告，检查 --probe 路径", file=sys.stderr)
        return 3

    lines = [json.dumps(f, ensure_ascii=False) for f in facts]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    chunks = []
    if args.chunk > 0:
        for i in range(0, len(lines), args.chunk):
            part = lines[i:i + args.chunk]
            cp = out_path.with_name(f"{out_path.stem}-chunk-{i // args.chunk + 1:02d}.jsonl")
            cp.write_text("\n".join(part) + "\n", encoding="utf-8")
            chunks.append(str(cp.resolve()))
        manifest = out_path.with_name("chunks.json")
        manifest.write_text(json.dumps(
            {"total": len(facts), "chunk_size": args.chunk, "chunks": chunks},
            ensure_ascii=False, indent=2), encoding="utf-8")

    empty = sum(1 for f in facts if not f["evidence"])
    avg = round(sum(len(l) for l in lines) / len(lines))
    print(f"[facts] total={len(facts)} with_evidence={len(facts) - empty} "
          f"empty={empty} avg_chars={avg} max_chars={max(len(l) for l in lines)} "
          f"chunks={len(chunks)} out={out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
