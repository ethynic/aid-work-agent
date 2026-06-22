#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""住宿费用计算"""

import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from loguru import logger

from db import get_db


def calculate_hotel_cost(items: list, tenant_id: str, hotel_id: Optional[int],
                         total_people: int, couples: int, trip_days: int,
                         season_type: str, hotel_doc_id: Optional[int] = None,
                         teacher_count: int = 0,
                         start_date: Optional[str] = None) -> Tuple[list, float]:
    """计算住宿费用（单酒店模式），返回 (items, 单房差)"""
    if hotel_doc_id:
        return _calculate_hotel_cost_from_kb(items, tenant_id, hotel_doc_id,
                                              total_people, couples, trip_days,
                                              teacher_count=teacher_count,
                                              start_date=start_date)

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
                          name_overrides: Optional[dict] = None,
                          start_date: Optional[str] = None) -> Tuple[list, float]:
    """多城市分住不同酒店，每个城市一行 item

    Args:
        name_overrides: {city: hotel_name} 可选，客户在换酒店场景下指定酒店名时使用，
                        优先级高于从 retriever 解析出的酒店名
        start_date: 行程出发日期（ISO 字符串）。按入住日匹配酒店价格表的季节区间；
                    每个城市的入住日 = start_date + 前面城市累计 nights。
    """
    from hotel_retriever import HotelRetriever
    retriever = HotelRetriever()
    single_supplement = 0
    name_overrides = name_overrides or {}

    # 入住日游标：第 i 个城市的入住日 = start_date + 前 i 个城市的 nights 之和
    try:
        running_dt = datetime.strptime(start_date, '%Y-%m-%d') if start_date else None
    except (ValueError, TypeError):
        running_dt = None

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
        check_in_iso = running_dt.strftime('%Y-%m-%d') if running_dt else None
        price = _parse_team_price(price_table, check_in_iso)

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

        # 推进到下一个城市的入住日（当前城市住 nights 晚）
        if running_dt is not None:
            running_dt = running_dt + timedelta(days=nights)

    return items, single_supplement


def _calculate_hotel_cost_from_kb(items, tenant_id: str, doc_id: int,
                                   total_people: int, couples: int,
                                   trip_days: int, teacher_count: int = 0,
                                   start_date: Optional[str] = None) -> Tuple[list, float]:
    """从知识库获取酒店价格计算住宿费用"""
    from hotel_retriever import HotelRetriever
    retriever = HotelRetriever()

    price_table = retriever.get_price_table(doc_id)
    if not price_table:
        logger.warning(f"[travel-quote] 酒店 doc_id={doc_id} 无价格表")
        return items, 0

    nights = trip_days - 1
    default_price = _parse_team_price(price_table, start_date)

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


def _extract_first_team_price(price_table: str) -> float:
    """兜底解析：提取价格表里第一条可解析的团队价。

    不依赖任何日期/节日关键词，仅做纯文本拆列。用于无入住日期或 LLM 选价失败时回退。
    """
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
    # 无团队价行：回退任意可解析价格
    for line in lines:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 3:
            try:
                return float(parts[2].strip())
            except ValueError:
                continue
    return 0


def _select_team_price_by_llm(price_table: str, check_in_date: str) -> float:
    """把完整价格表 + 入住日期交给 LLM，由其按语义返回合适的团队价。

    用 LLM 而非硬编码匹配的原因：节日繁多且别名不一（五一/劳动节、国庆/十一、
    端午/中秋/春节……）、日期写法多变（"暑期"/"旺季"/"5月"/"7-8月"）、区间表达
    灵活，正则与关键词列表永远覆盖不全。LLM 理解语义，天然覆盖这些变体。
    """
    from llm_client import call_llm

    prompt = f"""你是酒店报价助手。下面是某酒店的价格明细表，每行格式为：
房型 | 客户类型 | 价格(元) | 含早 | 适用日期

价格明细表：
{price_table}

入住日期：{check_in_date}

请根据入住日期选出团队房价。判断规则：
1. 只考虑"客户类型"含"团队"的行（"团队/团散同价"也算团队）。
2. 取"适用日期"覆盖该入住日期的那一行。
3. 若入住日同时落在【节假日专用区间】（通常带括号备注，如"（五一）"、"（国庆）"，或含节假日字样）和【普通季节区间】内，优先取节假日专用价。
4. 若没有任何区间的"适用日期"包含入住日期（例如入住日是淡季但表里只有旺季行），则取所有团队价里按价格表出现顺序的第一条。
5. 只输出一个整数价格（单位：元），不要任何其他文字、单位、解释、标点。

答案："""

    raw = call_llm(prompt)
    m = re.search(r'\d+(?:\.\d+)?', raw or '')
    if not m:
        raise ValueError(f"LLM 输出无法解析为价格: {raw!r}")
    return float(m.group(0))


def _parse_team_price(price_table: str, check_in_date: Optional[str] = None) -> float:
    """从知识库价格表文本中提取团队房价。

    价格表每行格式：房型 | 客户类型 | 价格 | 含早 | 适用日期

    Args:
        check_in_date: 入住日期（ISO 字符串，如 '2026-07-01'）。传入时把完整价格表
            和入住日期交给 LLM 按语义匹配（覆盖节日别名、模糊日期、各种区间写法）。
            为 None（如 update_hotel 的"是否有价"校验）或 LLM 调用失败时，回退到
            首条团队价。
    """
    if not price_table:
        return 0
    fallback = _extract_first_team_price(price_table)
    if not check_in_date:
        return fallback
    try:
        price = _select_team_price_by_llm(price_table, check_in_date)
        if price > 0:
            return price
        logger.warning(f"[travel-quote] LLM 返回非正价格 {price}，回退首条团队价")
    except Exception as e:
        logger.warning(
            f"[travel-quote] 入住日期 {check_in_date} LLM 选价失败，回退首条团队价: {e}"
        )
    return fallback
