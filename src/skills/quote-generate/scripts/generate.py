#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
研学旅游报价生成脚本

查库 → 计算 → 导出 Excel，一次调用完成全部报价流程。
接收 JSON 参数（stdin），输出 JSON 结果（stdout）。
"""

import json
import math
import os
import re
import sys
import tempfile
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 添加项目根目录到路径
script_path = Path(__file__).resolve()
if 'src' in script_path.parts:
    src_index = script_path.parts.index('src')
    project_root = Path(*script_path.parts[:src_index])
else:
    project_root = script_path.parent
sys.path.insert(0, str(project_root))

from loguru import logger

# 技能目录（用于定位模板等资源）
SKILL_DIR = Path(__file__).resolve().parent.parent


# ============================================================
# 数据库连接
# ============================================================

def get_db():
    """获取数据库连接（子进程安全）"""
    from src.db.database import (
        get_db_connection as _get_db,
        get_postgres_pool,
        init_postgres_pool,
    )
    if get_postgres_pool() is None:
        logger.info("[quote-generate] 子进程中 PostgreSQL 连接池未初始化，正在自动初始化")
        init_postgres_pool()
    return _get_db()


# ============================================================
# 表初始化
# ============================================================

TABLE_DEFINITIONS = {
    "bs_travel_quote_vehicles": """
        CREATE TABLE IF NOT EXISTS bs_travel_quote_vehicles (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT,
            region_name TEXT,
            vehicle_type TEXT NOT NULL,
            vehicle_type_label TEXT,
            seats_min INT NOT NULL,
            seats_max INT NOT NULL,
            daily_rate DECIMAL(10,2) NOT NULL,
            overtime_rate DECIMAL(10,2),
            overkm_rate DECIMAL(10,2),
            driver_meal_allowance DECIMAL(10,2),
            driver_accommodation DECIMAL(10,2),
            pricing_mode TEXT DEFAULT 'daily',
            per_km_rate DECIMAL(10,2),
            base_km DECIMAL(10,2),
            base_fee DECIMAL(10,2),
            season_type TEXT DEFAULT 'default',
            effective_from DATE,
            effective_to DATE,
            is_active BOOLEAN DEFAULT TRUE,
            sort_order INT DEFAULT 0,
            remark TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    "bs_travel_quote_meals": """
        CREATE TABLE IF NOT EXISTS bs_travel_quote_meals (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT,
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    "bs_travel_quote_guides": """
        CREATE TABLE IF NOT EXISTS bs_travel_quote_guides (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT,
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    "bs_travel_quote_fees": """
        CREATE TABLE IF NOT EXISTS bs_travel_quote_fees (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT,
            fee_name TEXT NOT NULL,
            fee_category TEXT NOT NULL,
            billing_method TEXT NOT NULL,
            unit_price DECIMAL(10,2) NOT NULL,
            is_mandatory BOOLEAN DEFAULT FALSE,
            is_active BOOLEAN DEFAULT TRUE,
            sort_order INT DEFAULT 0,
            remark TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    "bs_travel_quote_seasons": """
        CREATE TABLE IF NOT EXISTS bs_travel_quote_seasons (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT,
            season_type TEXT NOT NULL,
            season_type_label TEXT NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            price_multiplier DECIMAL(3,2) DEFAULT 1.00,
            is_active BOOLEAN DEFAULT TRUE,
            remark TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
}


def init_tables():
    """初始化所有旅游报价表"""
    with get_db() as conn:
        for table_name, ddl in TABLE_DEFINITIONS.items():
            conn.execute(ddl)
        conn.commit()
    logger.info("[quote-generate] 数据库表初始化完成")


# ============================================================
# 季节判断
# ============================================================

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


# ============================================================
# 查询定价数据
# ============================================================

def query_by_region(table: str, tenant_id: str, region_names: List[str],
                    season_type: str = None, extra_where: str = "") -> List[Dict]:
    """区域感知查询：优先匹配区域，无匹配取全国通用"""
    with get_db() as conn:
        # 先查有区域匹配的
        if region_names:
            placeholders = ','.join(['%s'] * len(region_names))
            params = [tenant_id]
            season_filter = ""
            if season_type:
                season_filter = " AND (season_type=%s OR season_type='default')"
                params.append(season_type)
            params.extend(region_names)

            conn.execute(
                f"SELECT * FROM {table} WHERE tenant_id=%s AND is_active=true "
                f"{season_filter} AND (region_name IN ({placeholders}) OR region_name IS NULL) "
                f"{extra_where} ORDER BY region_name IS NULL, sort_order",
                tuple(params)
            )
            rows = conn.fetchall()
            if rows:
                # 如果有区域匹配的行，优先使用
                region_rows = [r for r in rows if r.get('region_name')]
                return region_rows if region_rows else rows

        # 查全国通用
        params = [tenant_id]
        season_filter = ""
        if season_type:
            season_filter = " AND (season_type=%s OR season_type='default')"
            params.append(season_type)

        conn.execute(
            f"SELECT * FROM {table} WHERE tenant_id=%s AND is_active=true "
            f"{season_filter} AND region_name IS NULL {extra_where} ORDER BY sort_order",
            tuple(params)
        )
        return conn.fetchall()


# ============================================================
# 车型推荐
# ============================================================

def recommend_vehicle(people_count: int, vehicles: List[Dict]) -> List[Dict]:
    """根据人数推荐最优车型组合"""
    if not vehicles:
        return []

    # 单辆能装下
    single_options = [v for v in vehicles if v['seats_max'] >= people_count]
    if single_options:
        best = min(single_options, key=lambda v: v['seats_max'])
        return [{"vehicle": best, "count": 1}]

    # 多辆组合
    largest = max(vehicles, key=lambda v: v['seats_max'])
    best_combo, best_cost = None, float('inf')

    for v in sorted(vehicles, key=lambda x: x['seats_max'], reverse=True):
        full_count = people_count // v['seats_max']
        remainder = people_count % v['seats_max']

        if remainder == 0:
            cost = full_count * float(v['daily_rate'])
            if cost < best_cost:
                best_cost, best_combo = cost, [{"vehicle": v, "count": full_count}]
        else:
            for v2 in vehicles:
                if v2['seats_max'] >= remainder:
                    cost = full_count * float(v['daily_rate']) + float(v2['daily_rate'])
                    if cost < best_cost:
                        best_cost, best_combo = cost, [
                            {"vehicle": v, "count": full_count},
                            {"vehicle": v2, "count": 1},
                        ]
                    break
            cost = (full_count + 1) * float(v['daily_rate'])
            if cost < best_cost:
                best_cost, best_combo = cost, [{"vehicle": v, "count": full_count + 1}]

    return best_combo or [{"vehicle": largest, "count": math.ceil(people_count / largest['seats_max'])}]


def calculate_vehicle_cost(items: list, tenant_id: str, region_names: List[str],
                           total_people: int, trip_days: int, season_type: str,
                           vehicle_count: Optional[int],
                           route_distance_km: Optional[float] = None) -> Tuple[list, int]:
    """计算交通费用"""
    vehicles = query_by_region("bs_travel_quote_vehicles", tenant_id, region_names, season_type)
    if not vehicles:
        return items, 0

    # 按 pricing_mode 分组
    per_km_vehicles = [v for v in vehicles if v.get('pricing_mode') == 'per_km']
    daily_vehicles = [v for v in vehicles if v.get('pricing_mode') != 'per_km']

    # 按公里计费：有 per_km 车辆且有距离数据
    if per_km_vehicles and route_distance_km is not None:
        return _calculate_per_km_cost(items, per_km_vehicles, total_people, route_distance_km)

    # 按公里计费车辆无距离数据时，fallback 到按天计费（如果有 daily 车辆）
    if per_km_vehicles and route_distance_km is None and daily_vehicles:
        vehicles = daily_vehicles
    elif per_km_vehicles and route_distance_km is None:
        vehicles = per_km_vehicles  # 无 daily 车辆可用，仍走 daily 逻辑兜底

    # 按天计费（原有逻辑）
    combo = recommend_vehicle(total_people, vehicles)
    total_vehicle_count = sum(c["count"] for c in combo)

    for c in combo:
        v = c["vehicle"]
        count = c["count"]
        daily = float(v['daily_rate'])
        total_rental = daily * trip_days * count
        per_person = round(total_rental / total_people, 2)

        remark_parts = []
        if v.get('vehicle_type_label'):
            remark_parts.append(v['vehicle_type_label'])
        if count > 1:
            remark_parts.append(f"{count}辆")
        remark_parts.append(f"{v['seats_min']}-{v['seats_max']}座")

        items.append({
            "category": "用车",
            "name": v.get('vehicle_type_label') or v.get('vehicle_type', '旅游车辆'),
            "unit_price": daily,
            "quantity": count,
            "unit": "辆",
            "frequency": trip_days,
            "freq_unit": "天",
            "subtotal": per_person,
            "remark": "、".join(remark_parts),
        })

    return items, total_vehicle_count


def _calculate_per_km_cost(items: list, vehicles: list, total_people: int,
                           distance_km: float) -> Tuple[list, int]:
    """按公里计费：base_fee + max(0, distance_km - base_km) * per_km_rate"""
    combo = recommend_vehicle(total_people, vehicles)
    total_vehicle_count = sum(c["count"] for c in combo)

    for c in combo:
        v = c["vehicle"]
        count = c["count"]
        per_km_rate = float(v.get('per_km_rate') or 0)
        base_km = float(v.get('base_km') or 0)
        base_fee = float(v.get('base_fee') or 0)

        # 单辆费用 = base_fee + max(0, distance_km - base_km) * per_km_rate
        extra_km = max(0, distance_km - base_km)
        single_vehicle_cost = base_fee + extra_km * per_km_rate
        total_cost = single_vehicle_cost * count
        per_person = round(total_cost / total_people, 2)

        remark_parts = []
        if v.get('vehicle_type_label'):
            remark_parts.append(v['vehicle_type_label'])
        if count > 1:
            remark_parts.append(f"{count}辆")
        remark_parts.append(f"{v['seats_min']}-{v['seats_max']}座")
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
            "remark": "、".join(remark_parts),
        })

    return items, total_vehicle_count


# ============================================================
# 门票计算
# ============================================================

def calculate_ticket_cost(items: list, tenant_id: str, attraction_ids: List[int],
                          adults: int, children_half: int, students: int, elders: int,
                          total_people: int,
                          attraction_doc_ids: Optional[List[int]] = None) -> list:
    """计算门票费用"""
    # 知识库模式
    if attraction_doc_ids:
        return _calculate_ticket_cost_from_kb(items, tenant_id, attraction_doc_ids,
                                               adults, children_half, students,
                                               elders, total_people)

    if not attraction_ids:
        return items

    with get_db() as conn:
        for attr_id in attraction_ids:
            # 查景点
            conn.execute(
                "SELECT * FROM bs_travel_quote_attractions WHERE id=%s AND is_active=true",
                (attr_id,)
            )
            attr = conn.fetchone()
            if not attr:
                continue

            # 景区内交通
            if attr.get('internal_transport_price') and attr['internal_transport_price'] > 0:
                transport_total = float(attr['internal_transport_price']) * total_people
                items.append({
                    "category": "门票",
                    "name": f"{attr['name']}({attr.get('internal_transport_name', '景区交通')})",
                    "unit_price": float(attr['internal_transport_price']),
                    "quantity": total_people,
                    "unit": "人",
                    "frequency": 1,
                    "freq_unit": "次",
                    "subtotal": round(transport_total / total_people, 2),
                    "remark": "",
                })

            # 查门票
            conn.execute(
                "SELECT * FROM bs_travel_quote_tickets WHERE attraction_id=%s AND is_active=true "
                "AND (season_type='default' OR season_type IS NULL)",
                (attr_id,)
            )
            tickets = conn.fetchall()

            if not tickets:
                items.append({
                    "category": "门票",
                    "name": f"{attr['name']}(门票待确认)",
                    "unit_price": 0,
                    "quantity": total_people,
                    "unit": "人",
                    "frequency": 1,
                    "freq_unit": "次",
                    "subtotal": 0,
                    "remark": "价格待确认",
                })
                continue

            ticket_map = {t['ticket_type']: t for t in tickets}

            # 成人票
            if adults > 0 and 'adult' in ticket_map:
                t = ticket_map['adult']
                price = float(t.get('agency_price') or t['retail_price'])
                items.append({
                    "category": "门票",
                    "name": f"{attr['name']}({t['ticket_type_label']})",
                    "unit_price": price,
                    "quantity": adults,
                    "unit": "人",
                    "frequency": 1,
                    "freq_unit": "次",
                    "subtotal": round(price * adults / total_people, 2),
                    "remark": "协议价" if t.get('agency_price') else "挂牌价",
                })

            # 儿童半票
            if children_half > 0 and 'child_half' in ticket_map:
                t = ticket_map['child_half']
                price = float(t.get('agency_price') or t['retail_price'])
                items.append({
                    "category": "门票",
                    "name": f"{attr['name']}({t['ticket_type_label']})",
                    "unit_price": price,
                    "quantity": children_half,
                    "unit": "人",
                    "frequency": 1,
                    "freq_unit": "次",
                    "subtotal": round(price * children_half / total_people, 2),
                    "remark": t['ticket_type_label'],
                })

            # 学生票
            if students > 0 and 'student' in ticket_map:
                t = ticket_map['student']
                price = float(t.get('agency_price') or t['retail_price'])
                items.append({
                    "category": "门票",
                    "name": f"{attr['name']}({t['ticket_type_label']})",
                    "unit_price": price,
                    "quantity": students,
                    "unit": "人",
                    "frequency": 1,
                    "freq_unit": "次",
                    "subtotal": round(price * students / total_people, 2),
                    "remark": t['ticket_type_label'],
                })

            # 老人免票不收费

    return items


# ============================================================
# 住宿计算（知识库模式）
# ============================================================

def _calculate_hotel_cost_from_kb(items, tenant_id: str, doc_id: int,
                                   total_people: int, couples: int,
                                   trip_days: int) -> Tuple[list, float]:
    """从知识库获取酒店价格计算住宿费用"""
    from hotel_retriever import HotelRetriever
    retriever = HotelRetriever()

    price_table = retriever.get_price_table(doc_id)
    if not price_table:
        logger.warning(f"[quote-generate] 酒店 doc_id={doc_id} 无价格表")
        return items, 0

    nights = trip_days - 1

    # 解析价格表提取基础房价
    # 价格表格式：每行 "房型 | 客户类型 | 价格 | 含早 | 适用日期：..."
    lines = [l.strip() for l in price_table.split('\n') if l.strip() and '|' in l]

    # 取第一个"团队"类型的价格作为默认房价
    default_price = 0
    for line in lines:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 3 and '团队' in parts[1]:
            price_str = parts[2].strip()
            try:
                default_price = float(price_str)
                break
            except ValueError:
                continue

    if default_price == 0:
        # 如果没找到团队价，取第一个有效价格
        for line in lines:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 3:
                try:
                    default_price = float(parts[2].strip())
                    break
                except ValueError:
                    continue

    if default_price == 0:
        logger.warning(f"[quote-generate] 酒店 doc_id={doc_id} 价格表无有效价格")
        return items, 0

    # 排房逻辑（与旧版相同）
    couple_people = couples * 2
    remaining = total_people - couple_people
    standard_count = math.ceil(remaining / 2) if remaining > 0 else 0

    total_room_cost = (standard_count + couples) * default_price * nights
    per_person = round(total_room_cost / total_people, 2)

    single_supplement = 0
    if remaining > 0 and remaining % 2 == 1:
        single_supplement = round(default_price * nights / total_people, 2)

    items.append({
        "category": "住宿",
        "name": "酒店住宿",
        "unit_price": default_price,
        "quantity": standard_count + couples,
        "unit": "间",
        "frequency": nights,
        "freq_unit": "晚",
        "subtotal": per_person,
        "remark": "两人一间" + (f"，含{couples}对夫妻大床房" if couples > 0 else ""),
    })

    return items, single_supplement


# ============================================================
# 门票计算（知识库模式）
# ============================================================

def _calculate_ticket_cost_from_kb(items, tenant_id: str, doc_ids: list,
                                    adults: int, children_half: int,
                                    students: int, elders: int,
                                    total_people: int) -> list:
    """从知识库获取景点门票价格计算门票费用"""
    from attraction_retriever import AttractionRetriever
    retriever = AttractionRetriever()

    for doc_id in doc_ids:
        ticket_table = retriever.get_ticket_table(doc_id)
        attraction_info = retriever.get_attraction_info(doc_id)

        if not ticket_table:
            continue

        # 从 info 中提取景点名称
        attraction_name = ""
        if attraction_info:
            for line in attraction_info.split('\n'):
                if '景点名称' in line or '名称' in line:
                    parts = line.split('：', 1)
                    if len(parts) > 1:
                        attraction_name = parts[-1].strip()
                    break

        # 解析门票价格表
        lines = [l.strip() for l in ticket_table.split('\n') if l.strip() and '|' in l]

        def find_price(ticket_keyword, customer_keyword='团队'):
            """从价格表中查找指定票型的团队价"""
            for line in lines:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 3:
                    if ticket_keyword in parts[0] and customer_keyword in parts[1]:
                        try:
                            return float(parts[2].strip())
                        except ValueError:
                            pass
            # 没找到团队价，取散客价
            for line in lines:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 3 and ticket_keyword in parts[0]:
                    try:
                        return float(parts[2].strip())
                    except ValueError:
                        pass
            return 0

        # 成人票
        if adults > 0:
            price = find_price('成人')
            if price > 0:
                items.append({
                    "category": "门票",
                    "name": f"{attraction_name}(成人票)",
                    "unit_price": price,
                    "quantity": adults,
                    "unit": "人",
                    "frequency": 1,
                    "freq_unit": "次",
                    "subtotal": round(price * adults / total_people, 2),
                    "remark": "团队价",
                })

        # 儿童票
        if children_half > 0:
            price = find_price('儿童')
            if price > 0:
                items.append({
                    "category": "门票",
                    "name": f"{attraction_name}(儿童票)",
                    "unit_price": price,
                    "quantity": children_half,
                    "unit": "人",
                    "frequency": 1,
                    "freq_unit": "次",
                    "subtotal": round(price * children_half / total_people, 2),
                    "remark": "儿童票",
                })

        # 学生票
        if students > 0:
            price = find_price('学生')
            if price > 0:
                items.append({
                    "category": "门票",
                    "name": f"{attraction_name}(学生票)",
                    "unit_price": price,
                    "quantity": students,
                    "unit": "人",
                    "frequency": 1,
                    "freq_unit": "次",
                    "subtotal": round(price * students / total_people, 2),
                    "remark": "学生票",
                })

        # 老人免票不收费

    return items


# ============================================================
# 住宿计算
# ============================================================

def calculate_hotel_cost(items: list, tenant_id: str, hotel_id: Optional[int],
                         total_people: int, couples: int, trip_days: int,
                         season_type: str, hotel_doc_id: Optional[int] = None) -> Tuple[list, float]:
    """计算住宿费用，返回 (items, 单房差)"""
    # 知识库模式
    if hotel_doc_id:
        return _calculate_hotel_cost_from_kb(items, tenant_id, hotel_doc_id,
                                              total_people, couples, trip_days)

    if not hotel_id:
        return items, 0

    nights = trip_days - 1  # 住N-1晚

    with get_db() as conn:
        conn.execute(
            "SELECT * FROM bs_travel_quote_rooms WHERE hotel_id=%s AND is_active=true "
            "AND (season_type='default' OR season_type=%s)",
            (hotel_id, season_type)
        )
        rooms = conn.fetchall()

        if not rooms:
            return items, 0

        # 按房型分组
        standard_rooms = [r for r in rooms if r['room_type'] == 'standard']
        double_rooms = [r for r in rooms if r['room_type'] == 'double']
        triple_rooms = [r for r in rooms if r['room_type'] == 'triple']

        # 默认用标间
        default_room = standard_rooms[0] if standard_rooms else (rooms[0] if rooms else None)
        if not default_room:
            return items, 0

        room_price = float(default_room.get('agency_price') or default_room['retail_price'])

        # 夫妻用大床房
        couple_room = double_rooms[0] if double_rooms else None
        couple_room_price = float(couple_room.get('agency_price') or couple_room['retail_price']) if couple_room else room_price

        # 计算排房
        couple_people = couples * 2
        remaining = total_people - couple_people

        # 标间数
        standard_count = math.ceil(remaining / 2) if remaining > 0 else 0

        # 总房费
        total_room_cost = (standard_count * room_price + couples * couple_room_price) * nights
        per_person = round(total_room_cost / total_people, 2)

        # 单房差：如果剩余人是奇数
        single_supplement = 0
        if remaining > 0 and remaining % 2 == 1:
            single_supplement = round(room_price * nights / total_people, 2)

        items.append({
            "category": "住宿",
            "name": f"{default_room.get('room_type_label', '标准间')}",
            "unit_price": room_price,
            "quantity": standard_count + couples,
            "unit": "间",
            "frequency": nights,
            "freq_unit": "晚",
            "subtotal": per_person,
            "remark": f"两人一间" + (f"，含{couples}对夫妻大床房" if couples > 0 else ""),
        })

        return items, single_supplement


# ============================================================
# 餐饮计算
# ============================================================

def calculate_meal_cost(items: list, tenant_id: str, region_names: List[str],
                        total_people: int, trip_days: int,
                        meal_tier: str, season_type: str) -> list:
    """计算餐饮费用"""
    with get_db() as conn:
        params = [tenant_id, meal_tier]
        season_filter = " AND (season_type='default' OR season_type=%s)"
        params.append(season_type)
        region_filter = " AND (region_name IS NULL"
        if region_names:
            placeholders = ','.join(['%s'] * len(region_names))
            region_filter += f" OR region_name IN ({placeholders})"
            params.extend(region_names)
        region_filter += ")"

        conn.execute(
            f"SELECT * FROM bs_travel_quote_meals WHERE tenant_id=%s AND is_active=true "
            f"AND meal_tier=%s {season_filter} {region_filter} ORDER BY region_name IS NULL",
            tuple(params)
        )
        meals = conn.fetchall()

    if not meals:
        return items

    # 按餐类分组
    meal_by_type = {}
    for m in meals:
        t = m['meal_type']
        if t not in meal_by_type:
            meal_by_type[t] = m

    # 计算餐数：trip_days天，早餐=trip_days（含最后一天），午餐=trip_days，晚餐=trip_days-1（不含最后一晚）
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

            items.append({
                "category": "用餐",
                "name": m.get('meal_type_label', meal_type),
                "unit_price": price,
                "quantity": 1,
                "unit": "人",
                "frequency": count,
                "freq_unit": "餐",
                "subtotal": per_person,
                "remark": f"{m.get('meal_tier_label', meal_tier)}，{m.get('dishes_standard', '')}",
            })

    return items


# ============================================================
# 导游计算
# ============================================================

def calculate_guide_cost(items: list, tenant_id: str, region_names: List[str],
                         guide_type: str, trip_days: int,
                         season_type: str) -> list:
    """计算导游费用"""
    with get_db() as conn:
        params = [tenant_id, guide_type]
        season_filter = " AND (season_type='default' OR season_type=%s)"
        params.append(season_type)
        region_filter = " AND (region_name IS NULL"
        if region_names:
            placeholders = ','.join(['%s'] * len(region_names))
            region_filter += f" OR region_name IN ({placeholders})"
            params.extend(region_names)
        region_filter += ")"

        conn.execute(
            f"SELECT * FROM bs_travel_quote_guides WHERE tenant_id=%s AND is_active=true "
            f"AND guide_type=%s {season_filter} {region_filter} ORDER BY region_name IS NULL",
            tuple(params)
        )
        guide = conn.fetchone()

    if not guide:
        return items

    if guide.get('billing_method') == 'per_trip' and guide.get('trip_rate'):
        cost = float(guide['trip_rate'])
        items.append({
            "category": "导游",
            "name": guide.get('guide_type_label', guide_type),
            "unit_price": cost,
            "quantity": 1,
            "unit": "团",
            "frequency": 1,
            "freq_unit": "次",
            "subtotal": round(cost, 2),
            "remark": "按团计费",
        })
    elif guide.get('daily_rate'):
        rate = float(guide['daily_rate'])
        if guide.get('language_premium'):
            rate += float(guide['language_premium'])
        multiplier = float(guide.get('peak_season_multiplier', 1.0)) if season_type == 'peak' else 1.0
        total = rate * trip_days * multiplier
        items.append({
            "category": "导游",
            "name": guide.get('guide_type_label', guide_type),
            "unit_price": rate,
            "quantity": 1,
            "unit": "名",
            "frequency": trip_days,
            "freq_unit": "天",
            "subtotal": round(total, 2),
            "remark": guide.get('guide_level_label', '') + (" 旺季加价" if multiplier > 1 else ""),
        })

    return items


# ============================================================
# 其他费用
# ============================================================

def calculate_other_fees(items: list, tenant_id: str, total_people: int,
                         trip_days: int, vehicle_count: int,
                         include_insurance: bool) -> list:
    """计算其他固定费用"""
    with get_db() as conn:
        conn.execute(
            "SELECT * FROM bs_travel_quote_fees WHERE tenant_id=%s AND is_active=true "
            "ORDER BY sort_order",
            (tenant_id,)
        )
        fees = conn.fetchall()

    for fee in fees:
        # 跳过保险（如果不需要）
        if not include_insurance and fee.get('fee_category') == 'insurance':
            continue

        method = fee.get('billing_method', 'per_person')
        price = float(fee['unit_price'])

        if method == 'per_person':
            subtotal = price
        elif method == 'per_person_per_day':
            subtotal = price * trip_days
        elif method == 'per_trip':
            subtotal = round(price / total_people, 2)
        elif method == 'per_vehicle_per_day':
            subtotal = round(price * vehicle_count * trip_days / total_people, 2)
        else:
            subtotal = price

        items.append({
            "category": "其他",
            "name": fee['fee_name'],
            "unit_price": price,
            "quantity": total_people if method == 'per_person' else 1,
            "unit": "人" if 'per_person' in method else "团",
            "frequency": trip_days if 'per_day' in method else 1,
            "freq_unit": "天" if 'per_day' in method else "次",
            "subtotal": round(subtotal, 2),
            "remark": fee.get('fee_category', ''),
        })

    return items


# ============================================================
# Excel 模板导出
# ============================================================

def export_with_template(quote_data: dict, template_path: str) -> str:
    """使用模板导出报价单 Excel"""
    try:
        import openpyxl
        from openpyxl.utils import get_column_letter
    except ImportError:
        return _export_simple(quote_data)

    if not template_path or not Path(template_path).exists():
        template_path = str(SKILL_DIR / "templates" / "default.xlsx")

    if not Path(template_path).exists():
        return _export_simple(quote_data)

    import shutil
    dst = tempfile.mktemp(suffix='.xlsx')
    shutil.copy2(template_path, dst)
    wb = openpyxl.load_workbook(dst)
    ws = wb.active

    # 查找 {{#items}} 和 {{/items}} 标记
    items_start_row, items_end_row = None, None
    for row in ws.iter_rows():
        for cell in row:
            if cell.value and '{{#items}}' in str(cell.value):
                items_start_row = cell.row
            if cell.value and '{{/items}}' in str(cell.value):
                items_end_row = cell.row

    if items_start_row and items_end_row:
        # 提取模板行
        template_row_range = items_start_row + 1
        template_cells = []
        for col in range(1, ws.max_column + 1):
            template_cells.append(ws.cell(row=template_row_range, column=col).value)

        # 删除标记行
        ws.delete_rows(items_end_row)
        ws.delete_rows(items_start_row)

        # 插入数据行
        insert_row = items_start_row
        for i, item in enumerate(quote_data.get('items', [])):
            # 插入新行（从最后一行复制格式）
            ws.insert_rows(insert_row + 1)
            for col_idx, template_val in enumerate(template_cells, 1):
                cell = ws.cell(row=insert_row, column=col_idx)
                val = template_val
                if val and isinstance(val, str) and '{{' in val:
                    val = _replace_placeholders(val, item)
                cell.value = val
            insert_row += 1
    else:
        # 无标记行，只替换汇总变量
        pass

    # 替换所有 {{变量名}}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value and isinstance(cell.value, str) and '{{' in str(cell.value):
                cell.value = _replace_placeholders(str(cell.value), quote_data)

    wb.save(dst)
    return dst


def _replace_placeholders(text: str, data: dict) -> str:
    """替换 {{变量名}} 占位符"""
    def replacer(match):
        key = match.group(1).strip()
        val = data.get(key, '')
        if isinstance(val, float):
            return f"{val:,.2f}"
        return str(val) if val is not None else ''

    return re.sub(r'\{\{(\w+)\}\}', replacer, text)


def _export_simple(quote_data: dict) -> str:
    """无模板时的简单导出（使用内置格式）"""
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        # openpyxl 不可用时返回 JSON 文本文件
        dst = tempfile.mktemp(suffix='.json')
        with open(dst, 'w', encoding='utf-8') as f:
            json.dump(quote_data, f, ensure_ascii=False, indent=2)
        return dst

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = quote_data.get('course_name', '报价')[:31]

    HEADER_FONT = Font(name='微软雅黑', bold=True, size=11)
    TITLE_FONT = Font(name='微软雅黑', bold=True, size=14)
    DATA_FONT = Font(name='微软雅黑', size=10)
    CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
    BORDER = Border(left=Side('thin'), right=Side('thin'), top=Side('thin'), bottom=Side('thin'))

    col_widths = [12, 22, 10, 8, 6, 8, 6, 12, 30]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    row = 1
    # 标题
    ws.merge_cells(f'A{row}:I{row}')
    cell = ws.cell(row=row, column=1, value=f"{quote_data.get('company_name', '')}报价表")
    cell.font = TITLE_FONT
    cell.alignment = CENTER
    row += 1

    # 信息行
    info = f"课程：{quote_data.get('course_name', '')}    日期：{quote_data.get('start_date', '')}    人数：{quote_data.get('total_people', '')}人    天数：{quote_data.get('trip_days', '')}天"
    ws.merge_cells(f'A{row}:I{row}')
    ws.cell(row=row, column=1, value=info).font = DATA_FONT
    row += 2

    # 表头
    headers = ['成本类别', '项目', '单价', '数量', '单位', '次数', '单位', '费用小计', '备注']
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=col, value=h)
        c.font = HEADER_FONT
        c.alignment = CENTER
        c.border = BORDER
    row += 1

    # 数据行
    for item in quote_data.get('items', []):
        vals = [
            item.get('category', ''),
            item.get('name', ''),
            item.get('unit_price', 0),
            item.get('quantity', 1),
            item.get('unit', ''),
            item.get('frequency', 1),
            item.get('freq_unit', ''),
            item.get('subtotal', 0),
            item.get('remark', ''),
        ]
        for col, v in enumerate(vals, 1):
            c = ws.cell(row=row, column=col, value=v)
            c.font = DATA_FONT
            c.border = BORDER
            if isinstance(v, float):
                c.number_format = '#,##0.00'
        row += 1

    # 合计行
    ws.merge_cells(f'A{row}:G{row}')
    c = ws.cell(row=row, column=1, value='合计')
    c.font = HEADER_FONT
    c.alignment = CENTER
    c = ws.cell(row=row, column=8, value=quote_data.get('cost_per_person', 0))
    c.font = HEADER_FONT
    c.number_format = '#,##0.00'

    dst = tempfile.mktemp(suffix='.xlsx')
    wb.save(dst)
    return dst


# ============================================================
# 主流程
# ============================================================

def generate_quote(params: dict) -> dict:
    """执行完整的报价生成流程"""
    init_tables()

    tenant_id = params.get('tenant_id', '')
    region_name = params.get('region_name', '')
    total_people = params.get('total_people', 30)
    adults = params.get('adults', total_people)
    children_half = params.get('children_half', 0)
    students = params.get('students', 0)
    elders = params.get('elders', 0)
    couples = params.get('couples', 0)
    trip_days = params.get('trip_days', 1)
    start_date = params.get('start_date', date.today().isoformat())
    attraction_ids = params.get('attraction_ids', [])
    hotel_id = params.get('hotel_id')
    hotel_doc_id = params.get('hotel_doc_id')
    attraction_doc_ids = params.get('attraction_doc_ids', [])
    meal_tier = params.get('meal_tier', 'standard')
    guide_type = params.get('guide_type', 'local')
    vehicle_count = params.get('vehicle_count')
    include_insurance = params.get('include_insurance', True)
    profit_rate = params.get('profit_rate', 0.15)
    course_name = params.get('course_name', '')
    company_name = params.get('company_name', '')
    template_path = params.get('template_path')
    departure_city = params.get('departure_city', '')
    destination = params.get('destination', '')

    # Step 1: 区域名称
    region_names = [region_name] if region_name else []

    # Step 2: 确定季节
    season_type, season_multiplier = determine_season(tenant_id, start_date)

    # Step 2.5: 计算导航距离（用于按公里计费）
    route_distance_km = None
    if departure_city and destination:
        try:
            import subprocess as _sp
            route_script = project_root / "src" / "skills" / "route-distance-1.0.0" / "scripts" / "route_distance.py"
            _result = _sp.run(
                [sys.executable, str(route_script)],
                input=json.dumps({"origin": departure_city, "destination": destination}),
                capture_output=True, text=True, timeout=30,
            )
            if _result.returncode == 0 and _result.stdout.strip():
                _parsed = json.loads(_result.stdout.strip())
                if _parsed.get('success'):
                    route_distance_km = _parsed['distance_km']
                    logger.info(f"[quote-generate] 导航距离: {departure_city} → {destination}, {route_distance_km}km")
        except Exception as e:
            logger.warning(f"[quote-generate] 导航距离计算失败: {e}")

    items = []

    # Step 3: 交通
    items, actual_vehicle_count = calculate_vehicle_cost(
        items, tenant_id, region_names, total_people, trip_days,
        season_type, vehicle_count, route_distance_km
    )
    if actual_vehicle_count == 0:
        actual_vehicle_count = vehicle_count or 1

    # Step 4: 门票
    items = calculate_ticket_cost(
        items, tenant_id, attraction_ids,
        adults, children_half, students, elders, total_people,
        attraction_doc_ids=attraction_doc_ids
    )

    # Step 5: 住宿
    items, single_supplement = calculate_hotel_cost(
        items, tenant_id, hotel_id, total_people, couples, trip_days, season_type,
        hotel_doc_id=hotel_doc_id
    )

    # Step 6: 餐饮
    items = calculate_meal_cost(
        items, tenant_id, region_names, total_people, trip_days,
        meal_tier, season_type
    )

    # Step 7: 导游
    items = calculate_guide_cost(
        items, tenant_id, region_names, guide_type, trip_days, season_type
    )

    # Step 8: 其他费用
    items = calculate_other_fees(
        items, tenant_id, total_people, trip_days,
        actual_vehicle_count, include_insurance
    )

    # 汇总
    cost_per_person = round(sum(item['subtotal'] for item in items), 2)
    total_cost = round(cost_per_person * total_people, 2)
    quote_per_person = round(cost_per_person * (1 + profit_rate), 2)
    quote_total = round(quote_per_person * total_people, 2)

    # 导出 Excel
    quote_data = {
        "course_name": course_name,
        "company_name": company_name,
        "region_name": region_name,
        "start_date": start_date,
        "trip_days": trip_days,
        "total_people": total_people,
        "items": items,
        "total_cost": total_cost,
        "cost_per_person": cost_per_person,
        "single_supplement": single_supplement,
        "profit_rate": profit_rate,
        "quote_per_person": quote_per_person,
        "quote_total": quote_total,
    }

    file_path = export_with_template(quote_data, template_path)

    quote_data["file_path"] = os.path.abspath(file_path)
    return quote_data


def main():
    """主入口：从 stdin 读取 JSON 参数"""
    try:
        if sys.stdin.isatty():
            # 无 stdin 时尝试从命令行参数读取
            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument('--params', help='JSON 参数字符串')
            parser.add_argument('--params-file', help='JSON 参数文件路径')
            args = parser.parse_args()

            if args.params:
                params = json.loads(args.params)
            elif args.params_file:
                with open(args.params_file, 'r', encoding='utf-8') as f:
                    params = json.load(f)
            else:
                print(json.dumps({"success": False, "error": "请通过 stdin 或 --params 提供参数"}, ensure_ascii=False))
                sys.exit(1)
        else:
            params = json.load(sys.stdin)

        result = generate_quote(params)

        print(json.dumps({
            "success": True,
            "data": result,
        }, ensure_ascii=False, indent=2, default=str))

    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "error": f"JSON 解析错误: {e}"}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        logger.error(f"[quote-generate] 报价生成失败: {e}", exc_info=True)
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
