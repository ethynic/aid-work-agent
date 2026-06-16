#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
研学旅游报价生成脚本 — 主流程编排

查库 → 计算 → 导出 Excel，一次调用完成全部报价流程。
接收 JSON 参数（stdin），输出 JSON 结果（stdout）。
"""

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from loguru import logger

# 添加项目根目录和 scripts 目录到路径
script_path = Path(__file__).resolve()
scripts_dir = script_path.parent
if 'src' in script_path.parts:
    src_index = script_path.parts.index('src')
    project_root = Path(*script_path.parts[:src_index])
else:
    project_root = script_path.parent
for p in [str(project_root), str(scripts_dir)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# 技能目录（用于定位模板等资源）
SKILL_DIR = Path(__file__).resolve().parent.parent

from db import init_tables
from season import determine_season
from vehicle import (calculate_vehicle_cost, calculate_multi_leg_distance,
                     calculate_single_leg_distance)
from attraction import calculate_attraction_cost
from hotel import calculate_hotel_cost, calculate_hotel_stays
from meal import calculate_meal_cost
from guide import calculate_guide_cost
from other_fees import calculate_other_fees
from excel_export import export_with_template
from itinerary_parser import parse_itinerary
from resource_resolver import resolve_resources


def generate_quote(params: dict) -> dict:
    """执行完整的报价生成流程"""
    init_tables()

    tenant_id = params.get('tenant_id', '')
    itinerary_text = params.get('itinerary_text', '')
    start_date = params.get('start_date', date.today().isoformat())
    course_name = params.get('course_name', '')
    company_name = params.get('company_name', '')
    template_path = params.get('template_path')

    if itinerary_text:
        # 新模式：行程文本驱动，LLM 解析 + 向量检索
        parsed = parse_itinerary(itinerary_text)
        logger.info(f"[travel-quote] 行程解析结果: {json.dumps(parsed, ensure_ascii=False)}")

        resources = resolve_resources(parsed, tenant_id)

        region_name = parsed.get('region_name') or ''
        total_people = parsed.get('total_people') or 30
        adults = parsed.get('adults') or 0
        children_half = parsed.get('children_half') or 0
        students = parsed.get('students') or 0
        elders = parsed.get('elders') or 0
        couples = parsed.get('couples') or 0
        teacher_count = parsed.get('teacher_count') or 0
        trip_days = parsed.get('trip_days') or 1
        departure_city = parsed.get('departure_city') or ''
        destination = parsed.get('destination') or ''

        # 人数校验
        adults, students = _validate_headcount(
            adults, students, children_half, elders, total_people, teacher_count
        )

        attraction_ids = []
        hotel_id = None
        attraction_matches = resources.get('attraction_matches', [])
        hotel_doc_id = resources.get('hotel_doc_id')
        hotel_stays = resources.get('hotel_stays', [])
        meal_tier = parsed.get('meal_tier', 'standard')
        guide_type = parsed.get('guide_type', 'local')
        vehicle_count = None
        include_insurance = True
    else:
        # 旧模式：向后兼容
        parsed = {}
        region_name = params.get('region_name') or ''
        total_people = params.get('total_people') or 30
        adults = params.get('adults') or 0
        children_half = params.get('children_half') or 0
        students = params.get('students') or 0
        elders = params.get('elders') or 0
        couples = params.get('couples') or 0
        teacher_count = params.get('teacher_count') or 0
        trip_days = params.get('trip_days') or 1
        attraction_ids = params.get('attraction_ids') or []
        hotel_id = params.get('hotel_id')
        attraction_matches = params.get('attraction_matches') or []
        hotel_doc_id = params.get('hotel_doc_id')
        hotel_stays = params.get('hotel_stays', [])
        meal_tier = params.get('meal_tier') or 'standard'
        guide_type = params.get('guide_type') or 'local'
        vehicle_count = params.get('vehicle_count')
        include_insurance = params.get('include_insurance') is not False
        departure_city = params.get('departure_city') or ''
        destination = params.get('destination') or ''

    # Step 1: 区域名称
    region_names = [region_name] if region_name else []

    # Step 2: 季节
    season_type, season_multiplier = determine_season(tenant_id, start_date)

    # Step 3: 距离计算
    route_distance_km, leg_details = _calculate_route_distance(
        parsed, itinerary_text, departure_city, destination, region_name
    )

    items = []

    # Step 4: 交通
    items, actual_vehicle_count = calculate_vehicle_cost(
        items, tenant_id, region_names, total_people, trip_days,
        season_type, vehicle_count, route_distance_km, leg_details
    )
    if actual_vehicle_count == 0:
        actual_vehicle_count = vehicle_count or 1

    # Step 5: 景点门票 + 游玩项目（统一入口）
    items = calculate_attraction_cost(
        items, tenant_id, attraction_matches,
        adults, children_half, students, elders,
        total_people, teacher_count=teacher_count,
        attraction_ids=attraction_ids or None,
    )

    # Step 6: 住宿
    if hotel_stays:
        items, single_supplement = calculate_hotel_stays(
            items, tenant_id, hotel_stays, total_people, teacher_count, couples
        )
    else:
        items, single_supplement = calculate_hotel_cost(
            items, tenant_id, hotel_id, total_people, couples, trip_days, season_type,
            hotel_doc_id=hotel_doc_id, teacher_count=teacher_count
        )

    # Step 7: 餐饮
    items = calculate_meal_cost(
        items, tenant_id, region_names, total_people, trip_days,
        meal_tier, season_type, teacher_count=teacher_count
    )

    # Step 8: 导游
    items = calculate_guide_cost(
        items, tenant_id, region_names, guide_type, trip_days, season_type,
        total_people=total_people
    )

    # Step 9: 其他费用
    items = calculate_other_fees(
        items, tenant_id, total_people, trip_days,
        actual_vehicle_count, include_insurance, teacher_count=teacher_count
    )

    # 过滤价格为0的项目
    items = [item for item in items if (item.get('subtotal') or 0) > 0 or (item.get('unit_price') or 0) > 0]

    # 汇总：单价已含利润，直接累加。先算总价再回推人均，确保 人均 × 人数 = 总价 恒等
    cost_per_person = round(sum(item.get('subtotal') or 0 for item in items), 2)
    quote_total = round(cost_per_person * total_people, 2)
    quote_per_person = round(quote_total / total_people, 2) if total_people > 0 else 0.0
    teacher_total = round(sum(item.get('teacher_subtotal') or 0 for item in items), 2)

    # 导出 Excel
    internal_data = {
        "course_name": course_name,
        "company_name": company_name,
        "region_name": region_name,
        "start_date": start_date,
        "trip_days": trip_days,
        "total_people": total_people,
        "teacher_count": teacher_count,
        "items": items,
        "cost_per_person": cost_per_person,
        "teacher_total": teacher_total,
        "single_supplement": single_supplement,
        "quote_per_person": quote_per_person,
        "quote_total": quote_total,
    }

    file_path = export_with_template(internal_data, template_path)

    # 返回给 LLM 的视图：字段名直接对应 Excel 表头，避免 LLM 误解英文 field 名后自行计算/拼装
    rows = [
        {
            "成本类别": item.get('category', ''),
            "项目": item.get('name', ''),
            "单价": item.get('unit_price', 0),
            "数量": item.get('quantity', 0),
            "单位": item.get('unit', ''),
            "次数": item.get('frequency', 0),
            "单位2": item.get('freq_unit', ''),
            "费用小计": item.get('subtotal', 0),
            "随队老师": item.get('teacher_subtotal', 0),
            "备注": item.get('remark', ''),
        }
        for item in items
    ]

    return {
        "course_name": course_name,
        "company_name": company_name,
        "start_date": start_date,
        "total_people": total_people,
        "teacher_count": teacher_count,
        "trip_days": trip_days,
        "rows": rows,
        "合计_费用小计": round(sum(item.get('subtotal') or 0 for item in items), 2),
        "合计_随队老师": round(sum(item.get('teacher_subtotal') or 0 for item in items), 2),
        "人均报价": quote_per_person,
        "总价": quote_total,
        "file_path": os.path.abspath(file_path),
    }


def _validate_headcount(adults, students, children_half, elders, total_people, teacher_count):
    """校验并修正 LLM 解析的人数"""
    pax_sum = adults + students + children_half + elders
    if pax_sum != total_people:
        if pax_sum > total_people and students > 0 and adults > 0:
            corrected_adults = max(0, total_people - students - children_half - elders)
            logger.warning(
                f"[travel-quote] 人数校验修正: adults {adults}→{corrected_adults}, "
                f"students={students}, total_people={total_people}"
            )
            adults = corrected_adults
        elif pax_sum < total_people:
            adults += total_people - pax_sum
            logger.warning(f"[travel-quote] 人数不足，补充 adults→{adults}")

    if adults > 0 and adults == teacher_count and students > 0 and students < total_people:
        logger.warning(
            f"[travel-quote] 疑似老师混入adults: students={students}→{total_people}, adults→0"
        )
        students = total_people
        adults = 0

    return adults, students


def _calculate_route_distance(parsed, itinerary_text, departure_city, destination, region_name):
    """计算导航距离"""
    route_distance_km = None
    leg_details = None
    daily_routes = parsed.get('daily_routes', []) if itinerary_text else []

    if daily_routes:
        try:
            route_distance_km, leg_details = calculate_multi_leg_distance(
                daily_routes, region=region_name
            )
            if leg_details:
                logger.info(f"[travel-quote] 多段距离: {route_distance_km}km, {len(leg_details)}段")
            else:
                route_distance_km = None
        except Exception as e:
            logger.warning(f"[travel-quote] 多段距离计算失败: {e}")

    if route_distance_km is None and departure_city and destination:
        dist = calculate_single_leg_distance(departure_city, destination)
        if dist:
            route_distance_km = dist
            logger.info(f"[travel-quote] 单段距离: {departure_city} → {destination}, {dist}km")

    return route_distance_km, leg_details


def main():
    """主入口：从 stdin、命令行参数或环境变量读取 JSON 参数"""
    # 加载 .env 环境变量（直接运行脚本时需要）
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    try:
        params = None

        if not sys.stdin.isatty():
            raw = sys.stdin.buffer.read()
            stdin_data = raw.decode('utf-8', errors='replace').strip()
            if stdin_data:
                params = json.loads(stdin_data)

        if params is None:
            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument('--params', help='JSON 参数字符串')
            parser.add_argument('--params-file', help='JSON 参数文件路径')
            parser.add_argument('extra', nargs='*', help='位置参数（JSON 字符串）')
            args = parser.parse_args()

            if args.params:
                params = json.loads(args.params)
            elif args.params_file:
                with open(args.params_file, 'r', encoding='utf-8') as f:
                    params = json.load(f)
            elif args.extra:
                extra_str = ' '.join(args.extra).strip()
                if extra_str.startswith('{'):
                    params = json.loads(extra_str)

        if params is None:
            print(json.dumps({"success": False, "error": "未收到参数"}, ensure_ascii=False))
            sys.exit(1)

        result = generate_quote(params)

        print(json.dumps({
            "success": True,
            "data": result,
        }, ensure_ascii=False, indent=2, default=str))

    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "error": f"JSON 解析错误: {e}"}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"[travel-quote] 报价生成失败: {e}\n{tb}")
        print(json.dumps({"success": False, "error": str(e), "traceback": tb}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
