#!/usr/bin/env python3
"""研学旅游报价计算脚本"""

import argparse
import json
import sys


def calculate_quote(items, total_people):
    """计算报价

    Args:
        items: 报价项列表，每项包含 category, name, unit_price, quantity, unit, frequency, freq_unit, remark
        total_people: 总人数

    Returns:
        results: 计算后的报价项列表
        total: 总费用
    """
    results = []
    for item in items:
        unit_price = float(item.get('unit_price', 0))
        frequency = float(item.get('frequency', 1))
        unit = item.get('unit', '人')
        name = item.get('name', '')
        category = item.get('category', '')

        # 计算费用小计
        if unit == '团':
            # 按团均摊
            subtotal = unit_price / total_people * frequency
        elif unit == '间' or '住宿' in name or '酒店' in name or category == '住宿':
            # 住宿：两人一间
            subtotal = unit_price / 2 * frequency
        else:
            # 按人计算
            subtotal = unit_price * frequency

        result = {
            'category': item.get('category', ''),
            'name': name,
            'unit_price': unit_price,
            'quantity': item.get('quantity', 1),
            'unit': unit,
            'frequency': item.get('frequency', 1),
            'freq_unit': item.get('freq_unit', '次'),
            'subtotal': round(subtotal, 2),
            'remark': item.get('remark', '')
        }
        results.append(result)

    total = round(sum(r['subtotal'] for r in results), 2)
    return results, total


def main():
    parser = argparse.ArgumentParser(description='研学旅游报价计算')
    parser.add_argument('--items', help='报价项JSON字符串（短数据可用此参数，长数据请用stdin）')
    parser.add_argument('--items-file', help='报价项JSON文件路径')
    parser.add_argument('--people', type=int, required=True, help='总人数')

    try:
        args = parser.parse_args()

        # 按优先级获取 items JSON：--items > --items-file > stdin
        if args.items:
            items = json.loads(args.items)
        elif args.items_file:
            with open(args.items_file, 'r', encoding='utf-8') as f:
                items = json.load(f)
        elif not sys.stdin.isatty():
            items = json.load(sys.stdin)
        else:
            print(json.dumps({
                'success': False,
                'error': '请通过 --items、--items-file 或 stdin 提供报价项数据'
            }, ensure_ascii=False))
            sys.exit(1)

        if not isinstance(items, list) or len(items) == 0:
            print(json.dumps({
                'success': False,
                'error': 'items必须是非空数组'
            }, ensure_ascii=False))
            sys.exit(1)

        if args.people <= 0:
            print(json.dumps({
                'success': False,
                'error': '人数必须大于0'
            }, ensure_ascii=False))
            sys.exit(1)

        results, total = calculate_quote(items, args.people)

        print(json.dumps({
            'success': True,
            'items': results,
            'total': total,
            'per_person': total,
            'people_count': args.people
        }, ensure_ascii=False, indent=2))

    except json.JSONDecodeError as e:
        print(json.dumps({
            'success': False,
            'error': f'JSON解析错误: {str(e)}'
        }, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
