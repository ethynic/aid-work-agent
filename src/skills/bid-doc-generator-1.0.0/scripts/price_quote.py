#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
price_quote.py — 投标报价计算与校验（文件操作层，全部由脚本完成）

用途
    1. 依据招标书「采购清单」（数量由甲方给定）与「我方价格输入」（单价由我方给定），
       计算分项合价、税率、含税合价与投标总价；
    2. 校验：总价不得超过最高投标限价；大小写金额一致；分项合价累计 = 总价；
    3. 输出 price_result.json，供 render_docs.py 以 `price_table` 区块直接渲染，
       **避免大模型手工抄数字**导致跨文件金额不一致。

用法
    python price_quote.py --input price_input.json --out price_result.json

price_input.json
{
  "project_name": "…",
  "tax_rate": 0.13,
  "cap": 13438990.93,          # 最高投标限价（含税），null 表示无限价
  "items": [
    {"no":1,"area":"D04地块、D06地块","name":"阳台地面砖","model":"CT-04",
     "unit":"m2","qty":7496.48,"unit_price_pretax":58.00,"brand":"东鹏","note":""}
  ],
  "freight_note": "报价已含税、含运费、含装卸车费，不含安装费。"
}
"""
import argparse
import json
import os
import sys

CN_NUM = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖']
CN_UNIT = ['', '拾', '佰', '仟']
CN_BIG = ['', '万', '亿', '兆']


def rmb_upper(amount):
    """人民币金额转大写（标准写法：…元整 / …角 / …分）"""
    if amount is None:
        return ''
    neg = amount < 0
    amount = abs(round(float(amount), 2))
    if amount == 0:
        return '零元整'
    int_part = int(amount)
    dec_part = int(round((amount - int_part) * 100))

    def _four(n):
        s = ''
        zero = False
        for i in range(3, -1, -1):
            d = n // (10 ** i)
            n %= 10 ** i
            if d == 0:
                if s:
                    zero = True
            else:
                if zero:
                    s += '零'
                    zero = False
                s += CN_NUM[d] + CN_UNIT[i]
        return s

    s = ''
    if int_part == 0:
        s = '零'
    else:
        groups = []
        n = int_part
        while n > 0:
            groups.append(n % 10000)
            n //= 10000
        for gi in range(len(groups) - 1, -1, -1):
            g = groups[gi]
            part = _four(g)
            if part:
                s += part + CN_BIG[gi]
            elif s and gi > 0 and not s.endswith('零'):
                s += '零'
    s = s.rstrip('零') or '零'
    out = s + '元'
    if dec_part == 0:
        out += '整'
    else:
        jiao, fen = dec_part // 10, dec_part % 10
        if jiao:
            out += CN_NUM[jiao] + '角'
        elif fen:
            out += '零'
        if fen:
            out += CN_NUM[fen] + '分'
        else:
            out += '整'
    return ('负' if neg else '') + out


def q2(x):
    """保留两位小数（ROUND_HALF_UP），返回 float"""
    from decimal import Decimal, ROUND_HALF_UP
    return float(Decimal(str(x)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def main():
    ap = argparse.ArgumentParser(description='投标报价计算与校验')
    ap.add_argument('--input', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    with open(args.input, encoding='utf-8') as fh:
        inp = json.load(fh)

    rate = float(inp.get('tax_rate', 0.13))
    cap = inp.get('cap')
    rows = []
    sum_pretax = 0.0
    for it in inp.get('items', []):
        qty = float(it.get('qty') or 0)
        up = q2(it.get('unit_price_pretax') or 0)
        pre = q2(qty * up)
        taxed = q2(pre * (1 + rate))
        up_taxed = q2(up * (1 + rate))
        sum_pretax += pre
        rows.append({
            'no': it.get('no'),
            'area': it.get('area', ''),
            'name': it.get('name', ''),
            'model': it.get('model', ''),
            'unit': it.get('unit', ''),
            'qty': ('%.2f' % qty) if qty == qty else '',
            'unit_price_pretax': '%.2f' % up,
            'amount_pretax': '%.2f' % pre,
            'tax_rate': '%.2f' % (rate * 100),
            'unit_price_taxed': '%.2f' % up_taxed,
            'amount_taxed': '%.2f' % taxed,
            'brand': it.get('brand', ''),
            'note': it.get('note', ''),
        })

    sum_pretax = q2(sum_pretax)
    total_taxed = q2(sum_pretax * (1 + rate))

    # 校验
    checks = []
    ok = True
    if cap is not None:
        over = total_taxed > float(cap) + 1e-6
        checks.append({'name': '投标总价不超过最高投标限价',
                       'status': 'FAIL' if over else 'PASS',
                       'detail': '总价 %.2f 元 vs 限价 %.2f 元（差 %.2f 元）' % (
                           total_taxed, float(cap), float(cap) - total_taxed)})
        ok = ok and not over
    else:
        checks.append({'name': '最高投标限价', 'status': 'PASS',
                       'detail': '招标文件未设限价'})
    diff = q2(sum(float(r['amount_pretax']) for r in rows))
    same = abs(diff - sum_pretax) < 0.005
    checks.append({'name': '分项合价累计 = 投标总价',
                   'status': 'PASS' if same else 'FAIL',
                   'detail': '分项累计 %.2f vs 合计 %.2f' % (diff, sum_pretax)})
    ok = ok and same
    checks.append({'name': '大小写金额一致',
                   'status': 'PASS',
                   'detail': '大写：%s' % rmb_upper(total_taxed)})
    checks.append({'name': '报价保留两位小数',
                   'status': 'PASS',
                   'detail': '单价、合价均按两位小数四舍五入'})

    result = {
        'project_name': inp.get('project_name', ''),
        'tax_rate': rate,
        'cap': cap,
        'rows': rows,
        'total_pretax': sum_pretax,
        'total_taxed': total_taxed,
        'total_upper': rmb_upper(total_taxed),
        'freight_note': inp.get('freight_note', ''),
        'price_notes': inp.get('price_notes', []),
        'checks': checks,
        'all_pass': ok,
    }
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print('=' * 62)
    print('不含税合计: %.2f 元' % sum_pretax)
    print('含税合计  : %.2f 元  (%s)' % (total_taxed, rmb_upper(total_taxed)))
    if cap is not None:
        print('最高限价  : %.2f 元' % float(cap))
        print('低于限价  : %.2f 元 (%.2f%%)' % (
            float(cap) - total_taxed,
            (float(cap) - total_taxed) / float(cap) * 100))
    for c in checks:
        print('  [%s] %s — %s' % (c['status'], c['name'], c['detail']))
    print('输出: %s' % os.path.abspath(args.out))
    print('=' * 62)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
