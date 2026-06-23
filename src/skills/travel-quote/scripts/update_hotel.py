#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
旅游报价酒店局部更新脚本

接收上次 generate.py 返回的 internal_data + 客户指定的新酒店映射（按 city 定位），
只重算住宿行、其他 items 原位置保留，导出新的 Excel 报价单。

设计要点：
- 保持 items 原顺序（住宿行原位置替换，不挪到末尾）
- file_path 是返回 JSON 的顶层独立字段（不放 internal_data 内）
- city 未匹配 / 新酒店无价格 / 住宿行数量不一致时立即报错，避免流出错误报价单
"""

import json
import os
import sys
from pathlib import Path

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

from db import init_tables
from hotel import calculate_hotel_stays
from hotel_retriever import HotelRetriever
from excel_export import export_with_template


def update_hotel(params: dict) -> dict:
    """执行酒店局部更新流程"""
    init_tables()

    tenant_id = params.get('tenant_id', '')
    internal = params.get('internal_data') or {}
    overrides = params.get('hotel_overrides') or []

    # —— 输入校验 ——
    _validate_inputs(internal, overrides)

    items = internal.get('items') or []
    hotel_stays = internal.get('hotel_stays') or []
    couples = internal.get('couples') or 0

    # 表头字段：优先用本次传入（客户可能微调），否则沿用 internal_data
    course_name = params.get('course_name') or internal.get('course_name') or ''
    company_name = params.get('company_name') or internal.get('company_name') or ''
    start_date = params.get('start_date') or internal.get('start_date') or ''
    total_people = params.get('total_people') or internal.get('total_people') or 0
    teacher_count = params.get('teacher_count') or internal.get('teacher_count') or 0
    trip_days = params.get('trip_days') or internal.get('trip_days') or 0
    template_path = params.get('template_path')

    if total_people <= 0:
        raise ValueError("total_people 缺失或为 0，无法计算")

    # —— 1. 校验每个 override 的 city 在 hotel_stays 中 ——
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

    # —— 2. 用酒店名反查 doc_id（LLM 只知道酒店名，不知道 doc_id）——
    retriever = HotelRetriever()
    from hotel import _parse_team_price

    for ov in overrides:
        city = ov['city']
        hotel_name = ov['hotel_name']
        matches = retriever.search_by_name(tenant_id, hotel_name, top_k=10)

        # 在匹配结果中精确比对"酒店名称"字段，避免 ILIKE 模糊匹配误命中多个
        exact_matches = []
        for m in matches:
            parsed_name = _parse_hotel_name(m.get('info', ''))
            if parsed_name and parsed_name == hotel_name:
                exact_matches.append(m)

        if not exact_matches:
            # 退化到 ILIKE 唯一匹配
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
        if _parse_team_price(price_table) == 0:
            raise ValueError(f"酒店 '{hotel_name}' (doc_id={doc_id}) 价格表无有效团队价格")

        # 写回 override，供后续步骤使用
        ov['hotel_doc_id'] = doc_id
        logger.info(f"[travel-quote/update_hotel] 酒店名 '{hotel_name}' → doc_id={doc_id}")

    # —— 3. 用 overrides 更新 hotel_stays（直接改 stay 对象，会反映回原列表）——
    name_overrides = {}
    for ov in overrides:
        city = ov['city']
        stay_by_city[city]['hotel_doc_id'] = ov['hotel_doc_id']
        name_overrides[city] = ov['hotel_name']

    # —— 4. 用 calculate_hotel_stays 重算所有住宿行（顺序与 hotel_stays 一致）——
    new_hotel_items, single_supplement = calculate_hotel_stays(
        [], tenant_id, hotel_stays, total_people, teacher_count, couples,
        name_overrides=name_overrides, start_date=start_date
    )

    # —— 5. 原位置替换 items 中的住宿行（保持原顺序）——
    old_hotel_indices = [i for i, it in enumerate(items) if it.get('category') == '住宿']
    if len(old_hotel_indices) != len(new_hotel_items):
        raise ValueError(
            f"住宿行数量不一致：旧报价 {len(old_hotel_indices)} 行，新算 {len(new_hotel_items)} 行。"
            f"可能 internal_data 已被修改，请重新生成完整报价。"
        )

    new_items = list(items)  # 浅拷贝保持顺序
    for idx, new_hotel_item in zip(old_hotel_indices, new_hotel_items):
        new_items[idx] = new_hotel_item

    # —— 6. 重新汇总（与 generate.py 一致：单价已含利润，先总价再回推人均）——
    cost_per_person = round(sum(it.get('subtotal') or 0 for it in new_items), 2)
    quote_total = round(cost_per_person * total_people, 2)
    quote_per_person = round(quote_total / total_people, 2) if total_people > 0 else 0.0
    # 合计_随队老师 = 每位老师人均合计 = Σ各行 teacher_subtotal（各行已是所有老师总价）÷ 老师人数
    teacher_total_sum = round(sum(it.get('teacher_subtotal') or 0 for it in new_items), 2)
    teacher_total = round(teacher_total_sum / teacher_count, 2) if teacher_count > 0 else 0

    # —— 7. 构造新的 internal_data（不含 file_path，file_path 是顶层字段）——
    new_internal_data = {
        "course_name": course_name,
        "company_name": company_name,
        "region_name": internal.get('region_name', ''),
        "start_date": start_date,
        "trip_days": trip_days,
        "total_people": total_people,
        "teacher_count": teacher_count,
        "couples": couples,
        "season_type": internal.get('season_type', ''),
        "hotel_stays": hotel_stays,  # 已含新 doc_id
        "items": new_items,
        "cost_per_person": cost_per_person,
        "teacher_total": teacher_total,
        "single_supplement": single_supplement,
        "quote_per_person": quote_per_person,
        "quote_total": quote_total,
    }

    # —— 8. 导出新 Excel ——
    file_path = export_with_template(new_internal_data, template_path)

    logger.info(
        f"[travel-quote/update_hotel] 替换 {len(overrides)} 个城市的酒店，"
        f"新报价：人均 {quote_per_person}，总价 {quote_total}"
    )

    # —— 9. 返回与 generate.py 同 schema ——
    rows = [
        {
            "成本类别": it.get('category', ''),
            "项目": it.get('name', ''),
            "单价": it.get('unit_price', 0),
            "数量": it.get('quantity', 0),
            "单位": it.get('unit', ''),
            "次数": it.get('frequency', 0),
            "单位2": it.get('freq_unit', ''),
            "费用小计": it.get('subtotal', 0),
            "随队老师": it.get('teacher_subtotal', 0),
            "备注": it.get('remark', ''),
        }
        for it in new_items
    ]

    return {
        "course_name": course_name,
        "company_name": company_name,
        "start_date": start_date,
        "total_people": total_people,
        "teacher_count": teacher_count,
        "trip_days": trip_days,
        "rows": rows,
        "合计_费用小计": cost_per_person,
        "合计_随队老师": teacher_total,
        "人均报价": quote_per_person,
        "总价": quote_total,
        "file_path": os.path.abspath(file_path),
        "internal_data": new_internal_data,
    }


def _parse_hotel_name(info_text: str) -> str:
    """从酒店信息文本中解析'酒店名称：'字段，与 hotel.py 中 calculate_hotel_stays 的解析口径一致"""
    if not info_text:
        return ''
    for line in info_text.split('\n'):
        if '酒店名称' in line or '名称' in line:
            parts = line.split('：', 1)
            if len(parts) > 1:
                return parts[-1].strip()
    return ''


def _validate_inputs(internal: dict, overrides: list):
    """校验输入完整性"""
    if not internal:
        raise ValueError("缺少 internal_data，请重新生成完整报价后再换酒店")
    if not isinstance(internal.get('items'), list) or not internal['items']:
        raise ValueError("internal_data.items 缺失或为空")
    if not isinstance(internal.get('hotel_stays'), list):
        raise ValueError("internal_data.hotel_stays 缺失或格式错误")
    if not overrides:
        raise ValueError("hotel_overrides 为空，没有要替换的酒店")
    for ov in overrides:
        if not ov.get('city'):
            raise ValueError(f"hotel_overrides 中存在缺少 city 的条目：{ov}")


def main():
    """主入口：从 stdin、命令行参数读取 JSON 参数"""
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

        result = update_hotel(params)

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
        logger.error(f"[travel-quote/update_hotel] 酒店更新失败: {e}\n{tb}")
        print(json.dumps({"success": False, "error": str(e), "traceback": tb}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
