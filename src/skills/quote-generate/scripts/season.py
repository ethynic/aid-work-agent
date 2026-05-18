#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""季节判断"""

from datetime import datetime
from decimal import Decimal
from typing import Tuple

from loguru import logger

from db import get_db


def determine_season(tenant_id: str, start_date_str: str) -> Tuple[str, Decimal]:
    """确定出行日期的季节类型和价格倍率"""
    try:
        start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 'default', Decimal('1.00')

    with get_db() as conn:
        conn.execute(
            "SELECT season_type, price_multiplier FROM bs_travel_quote_seasons "
            "WHERE tenant_id=%s AND is_active=true AND start_date <= %s AND end_date >= %s "
            "ORDER BY price_multiplier DESC LIMIT 1",
            (tenant_id, start, start)
        )
        row = conn.fetchone()
        if row:
            return row['season_type'], Decimal(str(row['price_multiplier']))
    return 'default', Decimal('1.00')
