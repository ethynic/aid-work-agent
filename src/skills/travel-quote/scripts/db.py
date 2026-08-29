#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库连接、表初始化、区域查询"""

from typing import Dict, List

from loguru import logger


def get_db():
    """获取数据库连接（子进程安全）"""
    from src.db.database import (
        get_db_connection as _get_db,
        get_postgres_pool,
        init_postgres_pool,
    )
    if get_postgres_pool() is None:
        logger.info("[travel-quote] 子进程中 PostgreSQL 连接池未初始化，正在自动初始化")
        init_postgres_pool()
    return _get_db()


TABLE_DEFINITIONS = {
    "bs_travel_quote_vehicles": """
        CREATE TABLE IF NOT EXISTS bs_travel_quote_vehicles (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT,
            user_id TEXT,
            region_name TEXT,
            vehicle_type TEXT NOT NULL,
            vehicle_type_label TEXT,
            seats_max INT NOT NULL,
            daily_rate DECIMAL(10,2),
            pricing_mode TEXT DEFAULT 'per_km',
            per_km_rate DECIMAL(10,2),
            driver_meal_allowance DECIMAL(10,2),
            driver_accommodation DECIMAL(10,2),
            effective_from DATE,
            effective_to DATE,
            is_active BOOLEAN DEFAULT TRUE,
            remark TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            uuid TEXT UNIQUE
        )""",
    "bs_travel_quote_meals": """
        CREATE TABLE IF NOT EXISTS bs_travel_quote_meals (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT,
            user_id TEXT,
            region_name TEXT,
            meal_tier TEXT NOT NULL,
            meal_tier_label TEXT NOT NULL,
            meal_type TEXT NOT NULL,
            meal_type_label TEXT NOT NULL,
            price_per_person DECIMAL(10,2) NOT NULL,
            pax_per_table INT DEFAULT 10,
            dishes_standard TEXT,
            season_type TEXT DEFAULT 'default',
            effective_from DATE,
            effective_to DATE,
            is_active BOOLEAN DEFAULT TRUE,
            remark TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            uuid TEXT UNIQUE
        )""",
    "bs_travel_quote_guides": """
        CREATE TABLE IF NOT EXISTS bs_travel_quote_guides (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT,
            user_id TEXT,
            region_name TEXT,
            guide_type TEXT NOT NULL,
            guide_type_label TEXT NOT NULL,
            guide_level TEXT DEFAULT 'standard',
            guide_level_label TEXT,
            billing_method TEXT DEFAULT 'daily',
            daily_rate DECIMAL(10,2),
            trip_rate DECIMAL(10,2),
            language_premium DECIMAL(10,2) DEFAULT 0,
            peak_season_multiplier DECIMAL(3,2) DEFAULT 1.00,
            season_type TEXT DEFAULT 'default',
            is_active BOOLEAN DEFAULT TRUE,
            remark TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            uuid TEXT UNIQUE
        )""",
    "bs_travel_quote_fees": """
        CREATE TABLE IF NOT EXISTS bs_travel_quote_fees (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT,
            user_id TEXT,
            fee_name TEXT NOT NULL,
            fee_category TEXT NOT NULL,
            billing_method TEXT NOT NULL,
            unit_price DECIMAL(10,2) NOT NULL,
            is_mandatory BOOLEAN DEFAULT FALSE,
            is_active BOOLEAN DEFAULT TRUE,
            sort_order INT DEFAULT 0,
            remark TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            uuid TEXT UNIQUE
        )""",
    "bs_travel_quote_seasons": """
        CREATE TABLE IF NOT EXISTS bs_travel_quote_seasons (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT,
            user_id TEXT,
            season_type TEXT NOT NULL,
            season_type_label TEXT NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            price_multiplier DECIMAL(3,2) DEFAULT 1.00,
            is_active BOOLEAN DEFAULT TRUE,
            remark TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            uuid TEXT UNIQUE
        )""",
}


def init_tables():
    """初始化所有旅游报价表"""
    with get_db() as conn:
        for table_name, ddl in TABLE_DEFINITIONS.items():
            conn.execute(ddl)
        conn.commit()
    logger.info("[travel-quote] 数据库表初始化完成")


def shared_tenant_ids(tenant_id: str, subagent_id: str = '') -> List[str]:
    """返回 [本租户] + 该 subagent 全部共享来源租户（去重）。

    仅子智能体 + 租户模式（subagent_id 非空）生效，与通用知识库检索的
    load_shared_ranges 语义一致：不传 source_type 时聚合该 subagent 启用的
    所有 (from_tenant_id, source_type) 对。subagent_id 为空或读取异常时
    退化为仅本租户（异常用 logger.warning 记录，不影响主流程）。
    """
    tenant_ids = [tenant_id]
    if not subagent_id or not tenant_id:
        return tenant_ids
    try:
        from src.knowledge.retriever.tenant_range import load_shared_ranges
        for from_tenant_id, _st in load_shared_ranges(tenant_id, subagent_id):
            if from_tenant_id and from_tenant_id not in tenant_ids:
                tenant_ids.append(from_tenant_id)
    except Exception as e:
        logger.warning(f"[travel-quote] 加载共享定价来源租户失败: {e}")
    return tenant_ids


def query_by_region(table: str, tenant_id: str, region_names: List[str],
                    season_type: str = None, extra_where: str = "",
                    subagent_id: str = '') -> List[Dict]:
    """区域感知查询：优先匹配区域，无匹配取全国通用。

    subagent_id 非空时聚合该 subagent 全部共享来源租户，用 tenant_id = ANY(%s)
    一次查出本租户 + 共享源租户的定价表数据。
    """
    tenant_ids = shared_tenant_ids(tenant_id, subagent_id)
    if len(tenant_ids) > 1:
        tenant_sql = "tenant_id = ANY(%s)"
        tenant_param = tenant_ids
    else:
        tenant_sql = "tenant_id = %s"
        tenant_param = tenant_id

    with get_db() as conn:
        if region_names:
            placeholders = ','.join(['%s'] * len(region_names))
            params = [tenant_param]
            season_filter = ""
            if season_type:
                season_filter = " AND (season_type=%s OR season_type='default')"
                params.append(season_type)
            params.extend(region_names)

            conn.execute(
                f"SELECT * FROM {table} WHERE {tenant_sql} AND is_active=true "
                f"{season_filter} AND (region_name IN ({placeholders}) "
                f"OR region_name IS NULL OR region_name = '') "
                f"{extra_where} ORDER BY created_at DESC",
                tuple(params)
            )
            rows = conn.fetchall()
            if rows:
                region_rows = [r for r in rows if r.get('region_name')]
                return region_rows if region_rows else rows

        params = [tenant_param]
        season_filter = ""
        if season_type:
            season_filter = " AND (season_type=%s OR season_type='default')"
            params.append(season_type)

        conn.execute(
            f"SELECT * FROM {table} WHERE {tenant_sql} AND is_active=true "
            f"{season_filter} AND (region_name IS NULL OR region_name = '') "
            f"{extra_where} ORDER BY created_at DESC",
            tuple(params)
        )
        return conn.fetchall()
