#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""季节判断"""

from datetime import datetime
from decimal import Decimal
from typing import Tuple

from loguru import logger

from db import get_db, shared_tenant_ids


def determine_season(tenant_id: str, start_date_str: str,
                     subagent_id: str = '') -> Tuple[str, Decimal]:
    """确定出行日期的季节类型和价格倍率"""
    try:
        start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 'default', Decimal('1.00')

    tenant_ids = shared_tenant_ids(tenant_id, subagent_id)
    if len(tenant_ids) > 1:
        tenant_sql = "tenant_id = ANY(%s)"
        tenant_param = tenant_ids
    else:
        tenant_sql = "tenant_id = %s"
        tenant_param = tenant_id

    with get_db() as conn:
        conn.execute(
            f"SELECT season_type, price_multiplier FROM bs_travel_quote_seasons "
            f"WHERE {tenant_sql} AND is_active=true AND start_date <= %s AND end_date >= %s "
            "ORDER BY price_multiplier DESC LIMIT 1",
            (tenant_param, start, start)
        )
        row = conn.fetchone()
        if row:
            return row['season_type'], Decimal(str(row['price_multiplier']))
    return 'default', Decimal('1.00')
