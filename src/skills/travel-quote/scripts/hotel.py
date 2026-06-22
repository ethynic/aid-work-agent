#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""住宿费用计算"""

from typing import List, Optional, Tuple

from loguru import logger

from db import get_db


def calculate_hotel_cost(items: list, tenant_id: str, hotel_id: Optional[int],
                         total_people: int, couples: int, trip_days: int,
                         season_type: str, hotel_doc_id: Optional[int] = None,
                         teacher_count: int = 0) -> Tuple[list, float]:
    """计算住宿费用（单酒店模式），返回 (items, 单房差)"""
    if hotel_doc_id:
        return _calculate_hotel_cost_from_kb(items, tenant_id, hotel_doc_id,
                                              total_people, couples, trip_days,
                                              teacher_count=teacher_count)

    if not hotel_id:
        return items, 0

    nights = trip_days - 1

    with get_db() as conn:
        conn.execute(
            "SELECT * FROM bs_travel_quote_rooms WHERE hotel_id=%s AND is_active=true "
            "AND (season_type='default' OR season_type=%s)",
            (hotel_id, season_type)
        )
        rooms = conn.fetchall()

        if not rooms:
            return items, 0

        standard_rooms = [r for r in rooms if r['room_type'] == 'standard']
        default_room = standard_rooms[0] if standard_rooms else (rooms[0] if rooms else None)
        if not default_room:
            return items, 0

        room_price = float(default_room.get('retail_price') or 0)
        if room_price == 0:
            room_price = float(default_room.get('agency_price') or default_room['retail_price'])

        # 酒店按"间"计费：间数 = ceil(总人数/2)，行总价 = 房价 × 间数 × 夜数
        # subtotal（人均）= 行总价 ÷ 总人数
        import math
        rooms_per_n = math.ceil(total_people / 2) if total_people > 0 else 1
        room_nights = rooms_per_n * nights
        row_total = round(room_price * room_nights, 2)
        subtotal = round(row_total / total_people, 2) if total_people > 0 else 0
        teacher_cost = round(room_price * nights * teacher_count, 2) if teacher_count > 0 else 0

        couple_people = couples * 2
        remaining = total_people - couple_people
        single_supplement = 0
        if remaining > 0 and remaining % 2 == 1:
            single_supplement = round(room_price * nights / total_people, 2)

        items.append({
            "category": "住宿",
            "name": f"{default_room.get('room_type_label', '标准间')}",
            "unit_price": room_price,
            "quantity": rooms_per_n,
            "unit": "间",
            "frequency": nights,
            "freq_unit": "晚",
            "subtotal": subtotal,
            "teacher_subtotal": teacher_cost,
            "remark": f"两人一间，{rooms_per_n}间×{nights}晚" + (f"，含{couples}对夫妻大床房" if couples > 0 else ""),
        })

        return items, single_supplement


def calculate_hotel_stays(items: list, tenant_id: str, hotel_stays: list,
                          total_people: int, teacher_count: int,
                          couples: int,
                          name_overrides: Optional[dict] = None) -> Tuple[list, float]:
    """多城市分住不同酒店，每个城市一行 item

    Args:
        name_overrides: {city: hotel_name} 可选，客户在换酒店场景下指定酒店名时使用，
                        优先级高于从 retriever 解析出的酒店名
    """
    from hotel_retriever import HotelRetriever
    retriever = HotelRetriever()
    single_supplement = 0
    name_overrides = name_overrides or {}

    for stay in hotel_stays:
        city = stay.get("city", "")
        nights = stay.get("nights", 1)
        doc_id = stay.get("hotel_doc_id")

        if not doc_id:
            items.append({
                "category": "住宿",
                "name": f"{city}酒店（待确认）",
                "unit_price": 0,
                "quantity": 0,
                "unit": "间",
                "frequency": nights,
                "freq_unit": "夜",
                "subtotal": 0,
                "teacher_subtotal": 0,
                "remark": "酒店未匹配",
            })
            continue

        price_table = retriever.get_price_table(doc_id)
        price = _parse_team_price(price_table)

        if price == 0:
            items.append({
                "category": "住宿",
                "name": f"{city}酒店",
                "unit_price": 0,
                "quantity": 0,
                "unit": "间",
                "frequency": nights,
                "freq_unit": "夜",
                "subtotal": 0,
                "teacher_subtotal": 0,
                "remark": "价格表无有效价格",
            })
            continue

        # 酒店名优先级：客户指定 (name_overrides) > retriever 解析 > 默认 "{city}酒店"
        hotel_name = name_overrides.get(city)
        if not hotel_name:
            attraction_info = retriever.get_hotel_info(doc_id)
            hotel_name = f"{city}酒店"
            if attraction_info:
                for line in attraction_info.split('\n'):
                    if '酒店名称' in line or '名称' in line:
                        parts = line.split('：', 1)
                        if len(parts) > 1:
                            hotel_name = parts[-1].strip()
                        break

        # 酒店按"间"计费：间数 = ceil(总人数/2)，行总价 = 房价 × 间数 × 夜数
        # subtotal（人均）= 行总价 ÷ 总人数
        import math
        rooms_per_n = math.ceil(total_people / 2) if total_people > 0 else 1
        room_nights = rooms_per_n * nights
        row_total = round(price * room_nights, 2)
        subtotal = round(row_total / total_people, 2) if total_people > 0 else 0
        teacher_cost = round(price * nights * teacher_count, 2) if teacher_count > 0 else 0

        students = total_people - teacher_count
        couple_people = couples * 2
        remaining_students = students - couple_people
        if remaining_students > 0 and remaining_students % 2 == 1:
            single_supplement += round(price * nights / total_people, 2)

        items.append({
            "category": "住宿",
            "name": hotel_name,
            "unit_price": price,
            "quantity": rooms_per_n,
            "unit": "间",
            "frequency": nights,
            "freq_unit": "夜",
            "subtotal": subtotal,
            "teacher_subtotal": teacher_cost,
            "remark": f"{city}{nights}晚，{rooms_per_n}间×{nights}晚" + (f"，含{couples}对夫妻大床房" if couples > 0 else ""),
        })

    return items, single_supplement


def _calculate_hotel_cost_from_kb(items, tenant_id: str, doc_id: int,
                                   total_people: int, couples: int,
                                   trip_days: int, teacher_count: int = 0) -> Tuple[list, float]:
    """从知识库获取酒店价格计算住宿费用"""
    from hotel_retriever import HotelRetriever
    retriever = HotelRetriever()

    price_table = retriever.get_price_table(doc_id)
    if not price_table:
        logger.warning(f"[travel-quote] 酒店 doc_id={doc_id} 无价格表")
        return items, 0

    nights = trip_days - 1
    default_price = _parse_team_price(price_table)

    if default_price == 0:
        logger.warning(f"[travel-quote] 酒店 doc_id={doc_id} 价格表无有效价格")
        return items, 0

    # 酒店按"间"计费：间数 = ceil(总人数/2)，行总价 = 房价 × 间数 × 夜数
    # subtotal（人均）= 行总价 ÷ 总人数
    import math
    rooms_per_n = math.ceil(total_people / 2) if total_people > 0 else 1
    room_nights = rooms_per_n * nights
    row_total = round(default_price * room_nights, 2)
    subtotal = round(row_total / total_people, 2) if total_people > 0 else 0
    teacher_cost = round(default_price * nights * teacher_count, 2) if teacher_count > 0 else 0

    single_supplement = 0
    students = total_people - teacher_count
    couple_people = couples * 2
    remaining = students - couple_people
    if remaining > 0 and remaining % 2 == 1:
        single_supplement = round(default_price * nights / total_people, 2)

    items.append({
        "category": "住宿",
        "name": "酒店住宿",
        "unit_price": default_price,
        "quantity": rooms_per_n,
        "unit": "间",
        "frequency": nights,
        "freq_unit": "晚",
        "subtotal": subtotal,
        "teacher_subtotal": teacher_cost,
        "remark": f"两人一间，{rooms_per_n}间×{nights}晚" + (f"，含{couples}对夫妻大床房" if couples > 0 else ""),
    })

    return items, single_supplement


def _parse_team_price(price_table: str) -> float:
    """从知识库价格表文本中提取团队房价"""
    if not price_table:
        return 0
    lines = [l.strip() for l in price_table.split('\n') if l.strip() and '|' in l]
    for line in lines:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 3 and '团队' in parts[1]:
            try:
                return float(parts[2].strip())
            except ValueError:
                continue
    for line in lines:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 3:
            try:
                return float(parts[2].strip())
            except ValueError:
                continue
    return 0
