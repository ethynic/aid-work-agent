#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""房型过滤 + 含早透传 3 轮稳定性评估。

针对每个酒店 × 每个房型关键词 × N 轮，调用 _parse_team_price，统计：
- 价格稳定性（N 轮价格是否完全一致）
- 含早一致性（N 轮 breakfast 是否完全一致）
- 选中房型一致性（N 轮 selected_room_type 是否完全一致）

示例：
    venv/Scripts/python.exe src/skills/travel-quote/scripts/evaluate_hotel_breakfast_roomtype_stability.py \
        --tenant-id tenant_xxx --date 2026-07-15 --room-types 大床,亲子,套房,标准,双床 --limit 30 --rounds 3
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")

from hotel import (  # noqa: E402
    _filter_rows_by_room_type,
    _parse_hotel_price_rows,
    _parse_team_price,
)
from hotel_retriever import HotelRetriever  # noqa: E402


def _run_one_round(price_table: str, check_in_date: str,
                   room_type: Optional[str], total_people: int = 30) -> Dict:
    """单次选价，返回 {price, breakfast, selected_room_type, error}。"""
    try:
        result = _parse_team_price(
            price_table,
            check_in_date,
            total_people=total_people,
            room_type=room_type,
        )
        return {
            "price": result.get("price", 0),
            "breakfast": result.get("breakfast", ""),
            "selected_room_type": result.get("room_type", ""),
            "error": "",
        }
    except Exception as exc:
        return {
            "price": None,
            "breakfast": None,
            "selected_room_type": None,
            "error": str(exc),
        }


def _all_equal(values: List) -> bool:
    """列表中所有非 None 元素是否完全相等；全 None 或空返回 True。"""
    non_none = [v for v in values if v is not None]
    if not non_none:
        return True
    return all(v == non_none[0] for v in non_none)


def evaluate(tenant_id: str, check_in_date: str,
             room_type_keywords: List[str], limit: int, rounds: int) -> Dict:
    from src.db.database import init_postgres_pool

    init_postgres_pool()
    retriever = HotelRetriever()
    hotels = retriever.list_all(tenant_id, limit=limit)["items"]

    cases = []  # 每个 case = 一个 (hotel, keyword) 组合的 N 轮结果
    skipped_no_hit = []

    for hotel in hotels:
        doc_id = hotel["doc_id"]
        title = hotel.get("title") or ""
        price_table = retriever.get_price_table(doc_id) or ""
        all_rows = _parse_hotel_price_rows(price_table)
        if not all_rows:
            continue

        for kw in room_type_keywords:
            expected_hits = len(_filter_rows_by_room_type(all_rows, kw))
            if expected_hits == 0:
                skipped_no_hit.append({
                    "doc_id": doc_id,
                    "title": title,
                    "keyword": kw,
                    "reason": "keyword_no_hit",
                })
                continue

            round_results = [
                _run_one_round(price_table, check_in_date, room_type=kw)
                for _ in range(rounds)
            ]
            # 对照基线：无房型过滤的 N 轮
            baseline_results = [
                _run_one_round(price_table, check_in_date, room_type=None)
                for _ in range(rounds)
            ]

            errors = [r for r in round_results if r["error"]]
            ok_rounds = [r for r in round_results if not r["error"]]

            cases.append({
                "doc_id": doc_id,
                "title": title,
                "keyword": kw,
                "expected_hits": expected_hits,
                "rounds": round_results,
                "price_stable": _all_equal([r["price"] for r in ok_rounds]),
                "breakfast_consistent": _all_equal([r["breakfast"] for r in ok_rounds]),
                "roomtype_consistent": _all_equal([r["selected_room_type"] for r in ok_rounds]),
                "has_error": len(errors) > 0,
                "baseline_price_stable": _all_equal([
                    r["price"] for r in baseline_results if not r["error"]
                ]),
            })

    # 聚合
    valid_cases = [c for c in cases if not c["has_error"]]
    total = len(valid_cases)
    price_stable_count = sum(1 for c in valid_cases if c["price_stable"])
    breakfast_consistent_count = sum(1 for c in valid_cases if c["breakfast_consistent"])
    roomtype_consistent_count = sum(1 for c in valid_cases if c["roomtype_consistent"])

    by_keyword: Dict[str, Dict] = {}
    for c in valid_cases:
        kw = c["keyword"]
        bucket = by_keyword.setdefault(kw, {
            "total": 0, "price_stable": 0,
            "breakfast_consistent": 0, "roomtype_consistent": 0,
        })
        bucket["total"] += 1
        if c["price_stable"]: bucket["price_stable"] += 1
        if c["breakfast_consistent"]: bucket["breakfast_consistent"] += 1
        if c["roomtype_consistent"]: bucket["roomtype_consistent"] += 1

    for kw, bucket in by_keyword.items():
        t = bucket["total"]
        bucket["price_stable_rate"] = round(bucket["price_stable"] / t, 4) if t else None
        bucket["breakfast_consistent_rate"] = round(bucket["breakfast_consistent"] / t, 4) if t else None
        bucket["roomtype_consistent_rate"] = round(bucket["roomtype_consistent"] / t, 4) if t else None

    unstable_cases = [
        {
            "doc_id": c["doc_id"],
            "title": c["title"],
            "keyword": c["keyword"],
            "expected_hits": c["expected_hits"],
            "rounds_summary": [
                {
                    "price": r["price"],
                    "breakfast": r["breakfast"],
                    "selected_room_type": r["selected_room_type"],
                    "error": r["error"],
                }
                for r in c["rounds"]
            ],
        }
        for c in valid_cases
        if not (c["price_stable"] and c["breakfast_consistent"] and c["roomtype_consistent"])
    ]

    return {
        "tenant_id": tenant_id,
        "date": check_in_date,
        "rounds": rounds,
        "hotel_count": len(hotels),
        "total_cases": len(cases),
        "valid_cases": total,
        "skipped_no_hit": len(skipped_no_hit),
        "error_cases": sum(1 for c in cases if c["has_error"]),
        "summary": {
            "price_stable_rate": round(price_stable_count / total, 4) if total else None,
            "breakfast_consistent_rate": round(breakfast_consistent_count / total, 4) if total else None,
            "roomtype_consistent_rate": round(roomtype_consistent_count / total, 4) if total else None,
        },
        "by_keyword": by_keyword,
        "unstable_cases": unstable_cases,
        "skipped_no_hit_samples": skipped_no_hit[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="房型过滤 + 含早透传 3 轮稳定性评估"
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--date", required=True, help="入住日期 YYYY-MM-DD")
    parser.add_argument("--room-types", default="大床,亲子,套房,标准,双床",
                        help="房型关键词逗号分隔")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()

    keywords = [k.strip() for k in args.room_types.split(",") if k.strip()]
    result = evaluate(args.tenant_id, args.date, keywords, args.limit, args.rounds)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
