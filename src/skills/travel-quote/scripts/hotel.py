#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""住宿费用计算"""

import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

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
        room_type = stay.get("room_type") or ""

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
        selected = _parse_team_price(
            price_table,
            check_in_iso,
            total_people=total_people,
            couples=couples,
            teacher_count=teacher_count,
            room_type=room_type or None,
        )
        price = selected["price"]
        breakfast = selected.get("breakfast", "")
        selected_room_type = selected.get("room_type", "")

        if price == 0:
            # 指定了房型但价格表没匹配到 → 报错，避免静默回退到标准间
            if room_type:
                available = _list_available_room_types(price_table)
                raise ValueError(
                    f"酒店 doc_id={doc_id} 的价格表中未找到房型 '{room_type}'"
                    f"的团队价。该酒店可用房型：{available or '无'}"
                )
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

        # 名称与备注带上房型：价格基于哪个房型，备注就必须明确写出哪个房型
        # （价格-房型配对原则，让客户能核对报价单）
        # 房型来源优先级：客户/overrides 显式指定 > LLM 选中行的房型（价格表里的房型名）
        # 两者都为空（价格表行数据不规范）时兜底"标准间"，避免报价单出现"裸价格"
        display_room_type = room_type or selected_room_type or "标准间"
        display_name = f"{hotel_name}（{display_room_type}）"
        remark_parts = [f"{city}{nights}晚", f"{rooms_per_n}间×{nights}晚", f"房型={display_room_type}"]
        if breakfast:
            remark_parts.append(breakfast)
        if couples > 0:
            remark_parts.append(f"含{couples}对夫妻大床房")

        items.append({
            "category": "住宿",
            "name": display_name,
            "unit_price": price,
            "quantity": rooms_per_n,
            "unit": "间",
            "frequency": nights,
            "freq_unit": "夜",
            "subtotal": subtotal,
            "teacher_subtotal": teacher_cost,
            "remark": "，".join(remark_parts),
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
    selected = _parse_team_price(
        price_table,
        start_date,
        total_people=total_people,
        couples=couples,
        teacher_count=teacher_count,
    )
    default_price = selected["price"]
    breakfast = selected.get("breakfast", "")
    selected_room_type = selected.get("room_type", "")

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

    # 名称与备注：透传 LLM 选中行的房型 + 含早（本路径无 room_type 入参，被动透传）
    # 价格基于哪个房型，备注就必须明确写出哪个房型（价格-房型配对原则）
    # selected_room_type 为空（价格表数据不规范）时兜底"标准间"
    display_room_type = selected_room_type or "标准间"
    display_name = f"酒店住宿（{display_room_type}）"
    remark_parts = [f"两人一间，{rooms_per_n}间×{nights}晚", f"房型={display_room_type}"]
    if breakfast:
        remark_parts.append(breakfast)
    if couples > 0:
        remark_parts.append(f"含{couples}对夫妻大床房")

    items.append({
        "category": "住宿",
        "name": display_name,
        "unit_price": default_price,
        "quantity": rooms_per_n,
        "unit": "间",
        "frequency": nights,
        "freq_unit": "晚",
        "subtotal": subtotal,
        "teacher_subtotal": teacher_cost,
        "remark": "，".join(remark_parts),
    })

    return items, single_supplement


def _extract_first_team_price(price_table: str, room_type: Optional[str] = None) -> Dict:
    """兜底解析：返回价格表里第一条可解析的团队价行信息。

    返回 dict：{"price": float, "breakfast": str, "room_type": str}
    无任何行时返回 {"price": 0, "breakfast": "", "room_type": ""}。

    新版六列格式下，_parse_hotel_price_rows 已保证每行 team_price 在回退后 > 0，
    故此处直接取第一行的 price（即 team_price）。用于无入住日期或 LLM 选价失败时回退。
    传入 room_type 时，只在该房型对应的行里挑；房型一行都没有时返回 price=0 的零值 dict。
    """
    zero = {"price": 0, "breakfast": "", "room_type": ""}
    if not price_table:
        return zero
    rows = _parse_hotel_price_rows(price_table)
    if room_type:
        rows = _filter_rows_by_room_type(rows, room_type)
    if not rows:
        return zero
    team_rows = [r for r in rows if r.get('is_team')] or rows
    row = team_rows[0]
    return {
        "price": row['price'],
        "breakfast": row.get('breakfast', ''),
        "room_type": row.get('room_type', ''),
    }


def _filter_rows_by_room_type(rows: List[Dict], room_type: str) -> List[Dict]:
    """按房型模糊包含匹配过滤价格行。

    客户传入的 room_type 是"大床"/"亲子"/"套房"等关键词，价格表房型字段
    可能是"大床房"/"豪华大床房"/"亲子房"等。用双向包含匹配提高容错。
    """
    if not room_type:
        return rows
    keyword = room_type.strip()
    if not keyword:
        return rows
    matched = []
    for r in rows:
        rt = (r.get('room_type') or '').strip()
        if not rt:
            continue
        if keyword in rt or rt in keyword:
            matched.append(r)
    return matched


def _list_available_room_types(price_table: str) -> List[str]:
    """提取价格表中所有出现过的房型名（去重保序），用于报错提示。"""
    rows = _parse_hotel_price_rows(price_table)
    seen = []
    for r in rows:
        rt = (r.get('room_type') or '').strip()
        if rt and rt not in seen:
            seen.append(rt)
    return seen


def _parse_hotel_price_rows(price_table: str) -> List[Dict]:
    """解析酒店价格表中的有效价格行。

    新版六列格式（第一行为表头，会被跳过）：
        房型 | 散客价 | 团客价 | 含早 | 适用日期 | 备注

    每行提取 team_price（团客价）和 retail_price（散客价）两个数字。
    团队价缺失时 team_price 回退到散客价（兼容"单价格同时填两列"的写入约定，
    以及下游"is_team"判定的鲁棒性）。
    """
    rows = []
    if not price_table:
        return rows

    HEADER_KEYWORDS = ("房型", "散客价", "团客价")

    for idx, line in enumerate(price_table.splitlines()):
        line = line.strip()
        if not line or '|' not in line:
            continue
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 3:
            continue

        # 跳过表头行：同时含"房型"+"散客价/团客价"关键词即判定为表头
        joined = "|".join(parts)
        if "房型" in parts[0] and any(k in joined for k in ("散客价", "团客价")):
            continue

        # 新格式：parts[1]=散客价，parts[2]=团客价
        retail_match = re.search(r'\d+(?:\.\d+)?', parts[1].replace(',', '')) if len(parts) >= 2 else None
        team_match = re.search(r'\d+(?:\.\d+)?', parts[2].replace(',', '')) if len(parts) >= 3 else None

        # 至少要有一个对外价格；都没有则跳过（含"特殊政策"行等无价行）
        if not retail_match and not team_match:
            continue

        retail_price = float(retail_match.group(0)) if retail_match else 0.0
        team_price = float(team_match.group(0)) if team_match else 0.0
        # 团队价缺失时回退到散客价（解析约定：单价格会同时填两列，但兜底容错）
        if team_price == 0 and retail_price > 0:
            team_price = retail_price

        rows.append({
            "room_type": parts[0],
            "retail_price": retail_price,
            "team_price": team_price,
            # 兼容字段：下游选价仍读 row['price']，统一指向团队价（业务主路径）
            "price": team_price,
            "breakfast": parts[3] if len(parts) >= 4 else "",
            "date_text": parts[4].strip() if len(parts) >= 5 else "",
            "remark": parts[5].strip() if len(parts) >= 6 else "",
            "row_index": idx,
            "raw": line,
            # 新格式下只要团客价列有数字就是团队价行（已通过 team_price > 0 体现）
            "is_team": team_price > 0,
        })

    return rows


def _format_team_context(total_people: Optional[int],
                         couples: Optional[int],
                         teacher_count: Optional[int]) -> str:
    parts = []
    if total_people is not None:
        parts.append(f"总人数{total_people}人")
    if couples:
        parts.append(f"夫妻{couples}对")
    if teacher_count:
        parts.append(f"随队老师{teacher_count}人")
    return "，".join(parts) if parts else "未提供"


def _format_check_in_context(check_in_date: str) -> str:
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    try:
        weekday = weekdays[datetime.fromisoformat(check_in_date).weekday()]
        return f"{check_in_date}（{weekday}）"
    except Exception:
        return check_in_date


def _select_team_price_by_llm(price_table: str, check_in_date: str,
                              total_people: Optional[int] = None,
                              couples: Optional[int] = None,
                              teacher_count: Optional[int] = None,
                              room_type: Optional[str] = None) -> Dict:
    """用短 prompt + 关闭推理从价格表候选行中选择团队价行。

    返回 dict：{"price": float, "breakfast": str, "room_type": str}
    房型无任何匹配行时返回 {"price": 0, "breakfast": "", "room_type": ""}，
    让上游报错（避免静默回退到非客户指定房型）。

    新版六列格式：房型 | 散客价 | 团客价 | 含早 | 适用日期 | 备注
    候选行展示"团客价"列（业务对外报价走团队价口径）；团客价列缺失时由
    _parse_hotel_price_rows 已回退到散客价。

    LLM 输出"选中行的序号"（候选已带 1..N 编号），函数据此直接拿到完整 row，
    避免让 LLM 抄写价格数字，同时顺带取到 breakfast / room_type 写入 remark。
    兼容 LLM 偶尔仍输出价格的情况：序号越界时按价格反查首条。
    """
    from llm_client import call_llm

    zero = {"price": 0, "breakfast": "", "room_type": ""}
    rows = _parse_hotel_price_rows(price_table)
    if room_type:
        rows = _filter_rows_by_room_type(rows, room_type)
        if not rows:
            logger.warning(
                f"[travel-quote] 房型 '{room_type}' 在价格表中未匹配到任何行"
            )
            return zero
    team_rows = [row for row in rows if row.get("is_team")] or rows
    candidates = "\n".join(
        f"{idx}. 房型={row['room_type']} | 团客价={row['team_price']:.0f} | 含早={row.get('breakfast', '')} | 日期={row.get('date_text', '')} | 备注={row.get('remark', '')}"
        for idx, row in enumerate(team_rows, 1)
    )
    if not candidates:
        candidates = price_table

    prompt = f"""任务：为酒店住宿从候选价格行中选出适用于入住日期的团队价行，只输出该行的"序号"（数字）。
入住日期：{_format_check_in_context(check_in_date)}
团队构成：{_format_team_context(total_people, couples, teacher_count)}
**重要**：你选中的这一行的"房型"会原样展示在给客户的报价单备注里，客户据此核对"价格↔房型"是否匹配。所以选哪一行，就等于告诉客户订的是哪种房。

选择规则：
1. 候选已按房型过滤，每行已带 1..N 序号，团客价列即对外团队价。
2. 根据入住日期匹配"适用日期"列，节假日专用价格优先于普通日期区间。
3. **房型必须与团队构成匹配**（价格-房型配对原则）：
   - 团队构成是"学生/老师"为主时，默认选"标准间/双床房/双人间"等标准房型
   - 团队构成含夫妻（couples>0）且客户未指定房型时，仍选标准间（夫妻房由备注另行说明，不在这里改房型）
   - 不要因为某房型更贵就选它，也不要因为更便宜就回避；按"标准入住"语义选最常见的房型
4. 团队构成只用于判断客户类型，不要按人数重新计算房费。
5. 如果没有日期覆盖且房型无明确倾向，选序号 1。
6. 只输出一个数字（行序号），不要单位、解释或标点。

候选：
{candidates}

答案："""

    raw = call_llm(
        prompt,
        timeout=8.0,
        max_tokens=64,
        task="hotel_price_select",
        extra_body={"thinking": {"type": "disabled"}},
    )
    m = re.search(r'\d+', raw or '')
    if not m:
        raise ValueError(f"LLM 输出无法解析为行序号: {raw!r}")
    num = int(m.group(0))
    if 1 <= num <= len(team_rows):
        row = team_rows[num - 1]
    else:
        # 兼容 LLM 仍输出价格的情况：按价格反查首条
        match = next((r for r in team_rows if int(r['price']) == num), None)
        if not match:
            raise ValueError(
                f"LLM 输出序号/价格越界: {num}, 候选数={len(team_rows)}"
            )
        row = match
    return {
        "price": row['price'],
        "breakfast": row.get('breakfast', ''),
        "room_type": row.get('room_type', ''),
    }


def _parse_team_price(price_table: str, check_in_date: Optional[str] = None,
                      total_people: Optional[int] = None,
                      couples: Optional[int] = None,
                      teacher_count: Optional[int] = None,
                      room_type: Optional[str] = None) -> Dict:
    """从知识库价格表文本中提取团队房价行信息。

    返回 dict：{"price": float, "breakfast": str, "room_type": str}
    price=0 表示价格表无该房型团队价（breakfast/room_type 为空）。

    新版六列格式（第一行为表头，会被解析器跳过）：
        房型 | 散客价 | 团客价 | 含早 | 适用日期 | 备注

    Args:
        check_in_date: 入住日期（ISO 字符串，如 '2026-07-01'）。传入时用短 prompt
            LLM 根据入住日期和团队构成选择团队价。为 None（如 update_hotel 的
            "是否有价"校验）或 LLM 调用失败时，回退到首条团队价。
        room_type: 客户指定的房型（如"大床房"、"亲子房"）。传入时候选行先按房型
            模糊包含过滤，过滤后无任何行时返回 price=0（让上游报错，避免静默回退到
            非客户指定的房型）。不传时行为完全不变。
    """
    zero = {"price": 0, "breakfast": "", "room_type": ""}
    if not price_table:
        return zero
    fallback = _extract_first_team_price(price_table, room_type=room_type)
    if not check_in_date:
        return fallback

    try:
        result = _select_team_price_by_llm(
            price_table,
            check_in_date,
            total_people=total_people,
            couples=couples,
            teacher_count=teacher_count,
            room_type=room_type,
        )
        if result["price"] > 0:
            return result
        logger.warning(f"[travel-quote] LLM 返回非正价格 {result['price']}，回退首条团队价")
    except Exception as e:
        logger.warning(
            f"[travel-quote] 入住日期 {check_in_date} LLM 选价失败，回退首条团队价: {e}"
        )
    return fallback


def _parse_hotel_name(info_text: str) -> str:
    """从酒店信息文本中解析'酒店名称：'字段"""
    if not info_text:
        return ''
    for line in info_text.split('\n'):
        if '酒店名称' in line or '名称' in line:
            parts = line.split('：', 1)
            if len(parts) > 1:
                return parts[-1].strip()
    return ''


def resolve_hotel_overrides(tenant_id: str, hotel_stays: list, overrides: list) -> dict:
    """按酒店名反查 doc_id 并覆写到 hotel_stays 对应城市；同时透传客户指定的房型。

    用于客户明确指定酒店的场景（generate.py 的 hotel_overrides 参数、update_hotel.py 的换酒店流程）。
    会原地修改 hotel_stays 元素的 hotel_doc_id / room_type 字段。

    Args:
        tenant_id: 租户ID
        hotel_stays: LLM 解析出的住宿清单，元素需有 city 字段
        overrides: [{city, hotel_name, room_type?}]，客户指定的酒店；
                   room_type 可选，传入时要求价格表必须有该房型的团队价，否则报错

    Returns:
        name_overrides: {city: hotel_name}，传给 calculate_hotel_stays 的 name_overrides 参数

    Raises:
        ValueError: city 未在 hotel_stays 中 / hotel_name 缺失 / 酒店名歧义 /
                    查不到 / 价格表无有效团队价 / 指定房型在该酒店价格表中不存在
    """
    from hotel_retriever import HotelRetriever

    stay_by_city = {s.get("city", ""): s for s in hotel_stays}

    for ov in overrides:
        city = ov.get('city', '')
        if city not in stay_by_city:
            raise ValueError(
                f"未在城市住宿清单中找到 '{city}'，无法替换酒店。"
                f"本次报价包含的城市：{list(stay_by_city.keys())}"
            )
        if not ov.get('hotel_name'):
            raise ValueError(f"城市 '{city}' 的 hotel_overrides 缺少 hotel_name")

    retriever = HotelRetriever()
    name_overrides = {}

    for ov in overrides:
        city = ov['city']
        hotel_name = ov['hotel_name']
        room_type = (ov.get('room_type') or '').strip() or None
        matches = retriever.search_by_name(tenant_id, hotel_name, top_k=10)

        exact_matches = []
        for m in matches:
            parsed_name = _parse_hotel_name(m.get('info', ''))
            if parsed_name and parsed_name == hotel_name:
                exact_matches.append(m)

        if not exact_matches:
            if len(matches) == 1:
                exact_matches = matches
            else:
                raise ValueError(
                    f"按酒店名 '{hotel_name}' 未找到唯一匹配的酒店"
                    f"（共匹配 {len(matches)} 条，请使用更精确的酒店全名）"
                )
        if len(exact_matches) > 1:
            titles = [m.get('title', '') for m in exact_matches[:5]]
            raise ValueError(
                f"酒店名 '{hotel_name}' 匹配到多个酒店，存在歧义：{titles}。"
                f"请使用更精确的酒店全名"
            )

        doc_id = exact_matches[0]['doc_id']
        price_table = retriever.get_price_table(doc_id)
        if not price_table:
            raise ValueError(f"酒店 '{hotel_name}' (doc_id={doc_id}) 在知识库中查不到价格表")
        if _parse_team_price(price_table)["price"] == 0:
            raise ValueError(f"酒店 '{hotel_name}' (doc_id={doc_id}) 价格表无有效团队价格")

        # 房型校验：客户指定了房型但价格表里没有该房型的任何行 → 立即报错
        # （不要等下游 _parse_team_price 静默回退，否则客户以为订到了 X 房型，实际按标准间算）
        if room_type:
            rows = _parse_hotel_price_rows(price_table)
            if not _filter_rows_by_room_type(rows, room_type):
                available = _list_available_room_types(price_table)
                raise ValueError(
                    f"酒店 '{hotel_name}' (doc_id={doc_id}) 价格表中未找到房型 '{room_type}'。"
                    f"该酒店可用房型：{available or '无'}。请改用以上房型之一"
                )

        stay_by_city[city]['hotel_doc_id'] = doc_id
        if room_type:
            stay_by_city[city]['room_type'] = room_type
        name_overrides[city] = hotel_name
        logger.info(
            f"[travel-quote] 酒店名 '{hotel_name}' → doc_id={doc_id}"
            + (f"，房型 '{room_type}'" if room_type else "")
        )

    return name_overrides
