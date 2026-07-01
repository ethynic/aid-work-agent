#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""车型推荐 + 用车费用计算"""

import json
import subprocess as _sp
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from loguru import logger

from db import get_db, query_by_region

# 项目根目录
_script_path = Path(__file__).resolve()
if 'src' in _script_path.parts:
    _src_index = _script_path.parts.index('src')
    _project_root = Path(*_script_path.parts[:_src_index])
else:
    _project_root = _script_path.parent


def recommend_vehicle(people_count: int, vehicles: List[dict],
                      rate_field: str = "daily_rate") -> List[dict]:
    """按最少车辆数、最低总单价、最少空座依次推荐车型组合。"""
    if not vehicles or people_count <= 0:
        return []

    valid_vehicles = []
    for v in vehicles:
        rate = v.get(rate_field)
        seats = v.get('seats_max')
        if rate is not None and seats is not None and float(rate) > 0 and int(seats) > 0:
            valid_vehicles.append((v, int(seats), float(rate)))
    if not valid_vehicles:
        logger.warning(f"[travel-quote] 所有车型 {rate_field} 均为空，无法推荐")
        return []

    max_seats = max(seats for _, seats, _ in valid_vehicles)
    capacity_limit = people_count + max_seats - 1
    # dp[capacity] = (vehicle_count, total_rate, tuple(vehicle_counts))
    dp = [None] * (capacity_limit + 1)
    dp[0] = (0, 0.0, (0,) * len(valid_vehicles))
    for capacity in range(capacity_limit + 1):
        state = dp[capacity]
        if state is None:
            continue
        count, total_rate, counts = state
        for index, (_, seats, rate) in enumerate(valid_vehicles):
            next_capacity = capacity + seats
            if next_capacity > capacity_limit:
                continue
            next_counts = list(counts)
            next_counts[index] += 1
            candidate = (count + 1, total_rate + rate, tuple(next_counts))
            current = dp[next_capacity]
            if current is None or candidate[:2] < current[:2]:
                dp[next_capacity] = candidate

    candidates = [
        (state[0], state[1], capacity - people_count, state[2])
        for capacity, state in enumerate(dp)
        if capacity >= people_count and state is not None
    ]
    if not candidates:
        return []

    _, _, _, selected_counts = min(candidates)
    return [
        {"vehicle": valid_vehicles[index][0], "count": count}
        for index, count in enumerate(selected_counts)
        if count > 0
    ]


def calculate_vehicle_cost(items: list, tenant_id: str, region_names: List[str],
                           total_people: int, trip_days: int, season_type: str,
                           vehicle_count: Optional[int],
                           route_distance_km: Optional[float] = None,
                           leg_details: list = None) -> Tuple[list, int]:
    """计算交通费用"""
    vehicles = query_by_region("bs_travel_quote_vehicles", tenant_id, region_names)
    if not vehicles:
        return items, 0

    per_km_vehicles = [v for v in vehicles if v.get('pricing_mode') == 'per_km']
    daily_vehicles = [v for v in vehicles if v.get('pricing_mode') != 'per_km']

    if per_km_vehicles and route_distance_km is not None:
        return _calculate_per_km_cost(items, per_km_vehicles, total_people,
                                      route_distance_km, leg_details)

    if per_km_vehicles and route_distance_km is None and daily_vehicles:
        vehicles = daily_vehicles
    elif per_km_vehicles and route_distance_km is None:
        vehicles = per_km_vehicles

    combo = recommend_vehicle(total_people, vehicles, "daily_rate")
    total_vehicle_count = sum(c["count"] for c in combo)

    for c in combo:
        v = c["vehicle"]
        count = c["count"]
        daily = float(v.get('daily_rate') or 0)
        if daily == 0:
            logger.warning(f"[travel-quote] 车型 {v.get('vehicle_type')} daily_rate 为空，跳过")
            continue
        total_rental = daily * trip_days * count
        per_person = round(total_rental / total_people, 2)

        remark_parts = []
        if v.get('vehicle_type_label'):
            remark_parts.append(v['vehicle_type_label'])
        if count > 1:
            remark_parts.append(f"{count}辆")
        remark_parts.append(f"{v['seats_max']}座")
        if leg_details:
            total_route_km = sum(l["distance_km"] for l in leg_details)
            remark_parts.append(f"参考里程约{total_route_km:.0f}km")

        items.append({
            "category": "用车",
            "name": v.get('vehicle_type_label') or v.get('vehicle_type', '旅游车辆'),
            "unit_price": daily,
            "quantity": count,
            "unit": "辆",
            "frequency": trip_days,
            "freq_unit": "天",
            "subtotal": per_person,
            "teacher_subtotal": 0,
            "remark": "、".join(remark_parts),
        })

    return items, total_vehicle_count


def calculate_multi_leg_distance(daily_routes: list, region: str = "") -> Tuple[float, list]:
    """根据每日行程路线，逐段调用高德 API 计算总距离"""
    route_script = _project_root / "src" / "skills" / "route-distance-1.0.0" / "scripts" / "route_distance.py"
    total_km = 0.0
    leg_details = []

    for day_route in daily_routes:
        day_num = day_route.get("day", 0)
        legs = day_route.get("legs", [])

        for leg in legs:
            origin = leg.get("from", "").strip()
            destination = leg.get("to", "").strip()

            if not origin or not destination:
                continue
            if origin == destination:
                continue

            try:
                payload = {"origin": origin, "destination": destination}
                if region:
                    payload["region"] = region

                _result = _sp.run(
                    [sys.executable, str(route_script)],
                    input=json.dumps(payload),
                    capture_output=True, text=True, timeout=30,
                )
                if _result.returncode == 0 and _result.stdout.strip():
                    _parsed = json.loads(_result.stdout.strip())
                    if _parsed.get('success'):
                        leg_km = _parsed['distance_km']
                        leg_details.append({
                            "day": day_num,
                            "from": origin,
                            "to": destination,
                            "distance_km": leg_km,
                        })
                        total_km += leg_km
                        logger.info(f"[travel-quote] D{day_num}: {origin} → {destination}, {leg_km}km")
                    else:
                        logger.warning(f"[travel-quote] D{day_num} {origin}→{destination} 计算失败: {_parsed.get('error')}")
                else:
                    logger.warning(f"[travel-quote] D{day_num} {origin}→{destination} 子进程失败")
            except Exception as e:
                logger.warning(f"[travel-quote] D{day_num} {origin}→{destination} 异常: {e}")

    return round(total_km, 1), leg_details


def calculate_single_leg_distance(origin: str, destination: str) -> Optional[float]:
    """单段距离计算"""
    try:
        route_script = _project_root / "src" / "skills" / "route-distance-1.0.0" / "scripts" / "route_distance.py"
        _result = _sp.run(
            [sys.executable, str(route_script)],
            input=json.dumps({"origin": origin, "destination": destination}),
            capture_output=True, text=True, timeout=30,
        )
        if _result.returncode == 0 and _result.stdout.strip():
            _parsed = json.loads(_result.stdout.strip())
            if _parsed.get('success'):
                return _parsed['distance_km']
    except Exception as e:
        logger.warning(f"[travel-quote] 单段距离计算失败: {e}")
    return None


def _format_leg_details_remark(leg_details: list) -> str:
    """将多段路线明细格式化为 remark 文本"""
    if not leg_details:
        return ""

    day_groups = {}
    for leg in leg_details:
        d = leg["day"]
        if d not in day_groups:
            day_groups[d] = []
        day_groups[d].append(leg)

    parts = []
    for day_num in sorted(day_groups.keys()):
        legs = day_groups[day_num]
        leg_strs = [f"{l['from']}→{l['to']}({l['distance_km']:.0f}km)" for l in legs]
        parts.append(f"D{day_num}{'、'.join(leg_strs)}")

    return ", ".join(parts)


def _calculate_per_km_cost(items: list, vehicles: list, total_people: int,
                           distance_km: float,
                           leg_details: list = None) -> Tuple[list, int]:
    """按公里计费"""
    combo = recommend_vehicle(total_people, vehicles, "per_km_rate")
    total_vehicle_count = sum(c["count"] for c in combo)

    for c in combo:
        v = c["vehicle"]
        count = c["count"]
        per_km_rate = float(v.get('per_km_rate') or 0)

        single_vehicle_cost = distance_km * per_km_rate
        total_cost = single_vehicle_cost * count
        per_person = round(total_cost / total_people, 2)

        remark_parts = []
        if v.get('vehicle_type_label'):
            remark_parts.append(v['vehicle_type_label'])
        if count > 1:
            remark_parts.append(f"{count}辆")
        remark_parts.append(f"{v['seats_max']}座")
        if leg_details:
            route_detail = _format_leg_details_remark(leg_details)
            remark_parts.append(f"按公里计费({distance_km:.0f}km: {route_detail})")
        else:
            remark_parts.append(f"按公里计费({distance_km:.0f}km)")

        items.append({
            "category": "用车",
            "name": v.get('vehicle_type_label') or v.get('vehicle_type', '旅游车辆'),
            "unit_price": round(single_vehicle_cost, 2),
            "quantity": count,
            "unit": "辆",
            "frequency": 1,
            "freq_unit": "趟",
            "subtotal": per_person,
            "teacher_subtotal": 0,
            "remark": "、".join(remark_parts),
        })

    return items, total_vehicle_count
