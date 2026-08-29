#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导游费用计算"""

from typing import List

from db import get_db, shared_tenant_ids


def calculate_guide_cost(items: list, tenant_id: str, region_names: List[str],
                         guide_type: str, trip_days: int,
                         season_type: str, total_people: int = 1,
                         subagent_id: str = '') -> list:
    """计算导游费用"""
    tenant_ids = shared_tenant_ids(tenant_id, subagent_id)
    if len(tenant_ids) > 1:
        tenant_sql = "tenant_id = ANY(%s)"
        tenant_param = tenant_ids
    else:
        tenant_sql = "tenant_id = %s"
        tenant_param = tenant_id

    with get_db() as conn:
        params = [tenant_param, guide_type]
        season_filter = " AND (season_type='default' OR season_type=%s)"
        params.append(season_type)
        region_filter = " AND (region_name IS NULL OR region_name = ''"
        if region_names:
            placeholders = ','.join(['%s'] * len(region_names))
            region_filter += f" OR region_name IN ({placeholders})"
            params.extend(region_names)
        region_filter += ")"

        conn.execute(
            f"SELECT * FROM bs_travel_quote_guides WHERE {tenant_sql} AND is_active=true "
            f"AND guide_type=%s {season_filter} {region_filter} "
            f"ORDER BY (region_name IS NULL OR region_name = '')",
            tuple(params)
        )
        guide = conn.fetchone()

    if not guide:
        return items

    if guide.get('billing_method') == 'per_trip' and guide.get('trip_rate'):
        cost = float(guide['trip_rate'])
        # subtotal = 按团总价 ÷ 总人数
        subtotal = round(cost / total_people, 2) if total_people > 0 else round(cost, 2)
        items.append({
            "category": "导游",
            "name": guide.get('guide_type_label', guide_type),
            "unit_price": cost,
            "quantity": 1,
            "unit": "团",
            "frequency": 1,
            "freq_unit": "次",
            "subtotal": subtotal,
            "teacher_subtotal": 0,
            "remark": "按团计费",
        })
    elif guide.get('daily_rate'):
        rate = float(guide['daily_rate'])
        if guide.get('language_premium'):
            rate += float(guide['language_premium'])
        multiplier = float(guide.get('peak_season_multiplier', 1.0)) if season_type == 'peak' else 1.0
        total = rate * trip_days * multiplier
        # subtotal = 按日总价 ÷ 总人数
        subtotal = round(total / total_people, 2) if total_people > 0 else round(total, 2)
        items.append({
            "category": "导游",
            "name": guide.get('guide_type_label', guide_type),
            "unit_price": rate,
            "quantity": 1,
            "unit": "名",
            "frequency": trip_days,
            "freq_unit": "天",
            "subtotal": subtotal,
            "teacher_subtotal": 0,
            "remark": guide.get('guide_level_label', '') + (" 旺季加价" if multiplier > 1 else ""),
        })

    return items
