#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""餐饮费用计算"""

from typing import List

from db import get_db, shared_tenant_ids


def calculate_meal_cost(items: list, tenant_id: str, region_names: List[str],
                        total_people: int, trip_days: int,
                        meal_tier: str, season_type: str,
                        teacher_count: int = 0, subagent_id: str = '') -> list:
    """计算餐饮费用"""
    tenant_ids = shared_tenant_ids(tenant_id, subagent_id)
    if len(tenant_ids) > 1:
        tenant_sql = "tenant_id = ANY(%s)"
        tenant_param = tenant_ids
    else:
        tenant_sql = "tenant_id = %s"
        tenant_param = tenant_id

    with get_db() as conn:
        params = [tenant_param, meal_tier]
        season_filter = " AND (season_type='default' OR season_type=%s)"
        params.append(season_type)
        region_filter = " AND (region_name IS NULL OR region_name = ''"
        if region_names:
            placeholders = ','.join(['%s'] * len(region_names))
            region_filter += f" OR region_name IN ({placeholders})"
            params.extend(region_names)
        region_filter += ")"

        conn.execute(
            f"SELECT * FROM bs_travel_quote_meals WHERE {tenant_sql} AND is_active=true "
            f"AND meal_tier=%s {season_filter} {region_filter} "
            f"ORDER BY (region_name IS NULL OR region_name = '')",
            tuple(params)
        )
        meals = conn.fetchall()

    if not meals:
        return items

    meal_by_type = {}
    for m in meals:
        t = m['meal_type']
        if t not in meal_by_type:
            meal_by_type[t] = m

    meal_counts = {
        'breakfast': trip_days,
        'lunch': trip_days,
        'dinner': trip_days - 1 if trip_days > 1 else 1,
    }

    for meal_type, count in meal_counts.items():
        if meal_type in meal_by_type:
            m = meal_by_type[meal_type]
            price = float(m['price_per_person'])
            per_person = round(price * count, 2)
            teacher_cost = round(price * teacher_count * count, 2) if teacher_count > 0 else 0

            items.append({
                "category": "用餐",
                "name": m.get('meal_type_label', meal_type),
                "unit_price": price,
                "quantity": 1,
                "unit": "人",
                "frequency": count,
                "freq_unit": "餐",
                "subtotal": per_person,
                "teacher_subtotal": teacher_cost,
                "remark": f"{m.get('meal_tier_label', meal_tier)}，{m.get('dishes_standard', '')}",
            })

    return items
