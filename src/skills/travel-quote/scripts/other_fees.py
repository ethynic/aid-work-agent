#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""其他固定费用计算"""

from db import get_db


def calculate_other_fees(items: list, tenant_id: str, total_people: int,
                         trip_days: int, vehicle_count: int,
                         include_insurance: bool, teacher_count: int = 0) -> list:
    """计算其他固定费用"""
    with get_db() as conn:
        conn.execute(
            "SELECT * FROM bs_travel_quote_fees WHERE tenant_id=%s AND is_active=true "
            "ORDER BY COALESCE(sort_order, id)",
            (tenant_id,)
        )
        fees = conn.fetchall()

    for fee in fees:
        if not include_insurance and fee.get('fee_category') == 'insurance':
            continue

        method = fee.get('billing_method', 'per_person')
        price = float(fee['unit_price'])

        if method == 'per_person':
            subtotal = price
            teacher_subtotal = round(price * teacher_count, 2) if teacher_count > 0 else 0
        elif method == 'per_person_per_day':
            subtotal = price * trip_days
            teacher_subtotal = round(price * teacher_count * trip_days, 2) if teacher_count > 0 else 0
        elif method == 'per_trip':
            subtotal = round(price / total_people, 2)
            teacher_subtotal = 0
        elif method == 'per_vehicle_per_day':
            subtotal = round(price * vehicle_count * trip_days / total_people, 2)
            teacher_subtotal = 0
        else:
            subtotal = price
            teacher_subtotal = 0

        items.append({
            "category": "其他",
            "name": fee['fee_name'],
            "unit_price": price,
            "quantity": total_people if method == 'per_person' else 1,
            "unit": "人" if 'per_person' in method else "团",
            "frequency": trip_days if 'per_day' in method else 1,
            "freq_unit": "天" if 'per_day' in method else "次",
            "subtotal": round(subtotal, 2),
            "teacher_subtotal": teacher_subtotal,
            "remark": fee.get('fee_category', ''),
        })

    return items
