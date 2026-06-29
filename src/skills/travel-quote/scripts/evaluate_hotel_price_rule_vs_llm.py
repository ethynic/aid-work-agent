#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比酒店优化 LLM 选价与原完整 prompt LLM 选价。

示例：
    venv/Scripts/python.exe src/skills/travel-quote/scripts/evaluate_hotel_price_rule_vs_llm.py \
        --tenant-id tenant_9eb3e45cab83 --dates 2026-05-03,2026-07-15,2026-10-01 --limit 50
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from loguru import logger


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")

from hotel import _parse_hotel_price_rows, _select_team_price_by_llm
from hotel_retriever import HotelRetriever


def evaluate(tenant_id: str, dates: List[str], limit: int) -> Dict:
    from src.db.database import init_postgres_pool

    init_postgres_pool()
    retriever = HotelRetriever()
    hotels = retriever.list_all(tenant_id, limit=limit)["items"]
    cases = []

    for hotel in hotels:
        doc_id = hotel["doc_id"]
        title = hotel.get("title") or ""
        price_table = retriever.get_price_table(doc_id) or ""
        rows = _parse_hotel_price_rows(price_table)
        if not rows:
            continue

        for check_in_date in dates:
            optimized_price = None
            optimized_error = ""
            legacy_price = None
            legacy_error = ""
            try:
                optimized_price = _select_team_price_by_llm(price_table, check_in_date)["price"]
            except Exception as exc:
                optimized_error = str(exc)
            try:
                legacy_price = _select_team_price_by_legacy_llm(price_table, check_in_date)
            except Exception as exc:
                legacy_error = str(exc)

            matched = (
                optimized_price is not None
                and legacy_price is not None
                and abs(float(optimized_price) - float(legacy_price)) < 0.01
            )
            cases.append({
                "doc_id": doc_id,
                "title": title,
                "date": check_in_date,
                "optimized_price": optimized_price,
                "optimized_error": optimized_error,
                "legacy_price": legacy_price,
                "legacy_error": legacy_error,
                "matched": matched,
            })

    comparable = [
        case for case in cases
        if case["optimized_price"] is not None and case["legacy_price"] is not None
    ]
    matched_count = sum(1 for case in comparable if case["matched"])
    return {
        "tenant_id": tenant_id,
        "dates": dates,
        "hotel_count": len(hotels),
        "case_count": len(cases),
        "comparable_count": len(comparable),
        "matched_count": matched_count,
        "match_rate": round(matched_count / len(comparable), 4) if comparable else None,
        "diffs": [case for case in comparable if not case["matched"]],
        "errors": [
            case for case in cases
            if case["optimized_error"] or case["legacy_error"]
        ],
    }


def _select_team_price_by_legacy_llm(price_table: str, check_in_date: str) -> float:
    """使用 Phase 1 改造前的完整 LLM prompt，作为精确度对照基线。

    ⚠️ 注意：此基线 prompt 基于"五列旧格式"（房型 | 客户类型 | 价格 | 含早 | 适用日期）。
    自酒店价格表升级为"六列新格式"（房型 | 散客价 | 团客价 | 含早 | 适用日期 | 备注）后，
    本基线已与现网数据不兼容，仅作历史对照保留。如需重新建立基线，请基于新格式重写。
    """
    import re

    from llm_client import call_llm

    logger.warning(
        "[evaluate_hotel_legacy] legacy prompt 基于已废弃的五列格式，"
        "在六列新格式数据上结果不可靠，仅作历史对照"
    )

    prompt = f"""你是酒店报价助手。下面是某酒店的价格明细表，每行格式为：
房型 | 客户类型 | 价格(元) | 含早 | 适用日期

价格明细表：
{price_table}

入住日期：{check_in_date}

请根据入住日期选出团队房价。判断规则：
1. 只考虑"客户类型"含"团队"的行（"团队/团散同价"也算团队）。
2. 取"适用日期"覆盖该入住日期的那一行。
3. 若入住日同时落在【节假日专用区间】（通常带括号备注，如"（五一）"、"（国庆）"，或含节假日字样）和【普通季节区间】内，优先取节假日专用价。
4. 若没有任何区间的"适用日期"包含入住日期（例如入住日是淡季但表里只有旺季行），则取所有团队价里按价格表出现顺序的第一条。
5. 只输出一个整数价格（单位：元），不要任何其他文字、单位、解释、标点。

答案："""

    raw = call_llm(prompt, timeout=30.0, max_tokens=32, task="hotel_price_select_legacy")
    m = re.search(r'\d+(?:\.\d+)?', raw or '')
    if not m:
        raise ValueError(f"LLM 输出无法解析为价格: {raw!r}")
    return float(m.group(0))


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare optimized hotel LLM price selection with legacy prompt.")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--dates", default="2026-05-03,2026-07-15,2026-08-15,2026-10-01")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    dates = [item.strip() for item in args.dates.split(",") if item.strip()]
    result = evaluate(args.tenant_id, dates, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
