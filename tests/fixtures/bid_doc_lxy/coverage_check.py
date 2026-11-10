import json, re

old = json.load(open('/app/ext_package/lxy-bid-testcase/04-中间配置/requirements.json'))
new = json.load(open('/tmp/lxy_bid_test/llm/requirements.json'))

CAT_MAP = {'qualification': 'qualification', 'technical': 'technical_specs',
           'commercial': 'commercial_terms', 'veto': 'disqualification'}

def new_texts(cat):
    if cat == 'scoring':
        sc = new.get('scoring') or {}
        return [json.dumps(k, ensure_ascii=False) for k in (sc.get('key_items') or [])] + [sc.get('price_formula','') or '', sc.get('method','') or '']
    if cat == 'format':
        return [f'{k}={v}' for k, v in (new.get('format_requirements') or {}).items()]
    return [json.dumps(x, ensure_ascii=False) for x in (new.get(CAT_MAP.get(cat, cat)) or [])]

def keywords(text):
    t = re.sub(r'[，。；：、""（）()\s★*a-zA-Z0-9%/．.\-]', ' ', text)
    toks = [w for w in t.split() if len(w) >= 3]
    return toks[:12]

total, full_hit, part_hit, misses = 0, 0, 0, []
for it in old['requirements']:
    total += 1
    cat = it['category']
    pool = new_texts(cat)
    kws = keywords(it['requirement'])
    joined = ' '.join(pool)
    hit = sum(1 for k in kws if k in joined)
    ratio = hit / max(len(kws), 1)
    if ratio >= 0.7: full_hit += 1
    elif ratio >= 0.3: part_hit += 1
    else: misses.append((it['req_id'], cat, it['requirement'][:60]))

print(f'包内 50 条逐条覆盖对照（关键词命中比例）：')
print(f'  完整覆盖(>=70%): {full_hit}  部分覆盖(30-70%): {part_hit}  未覆盖(<30%): {len(misses)}')
for m in misses: print('   缺:', m[0], m[1], m[2])
