import json, re

old = json.load(open('/app/ext_package/lxy-bid-testcase/04-中间配置/requirements.json'))
new_raw = open('/tmp/lxy_bid_test/llm/requirements.json').read()

def keywords(text):
    t = re.sub(r'[，。；：、""（）()\s★*a-zA-Z0-9%/．.\-]', ' ', text)
    toks = [w for w in t.split() if len(w) >= 3]
    return toks[:12]

misses = ['Q13', 'Q14', 'Q18', 'Q19', 'Q20', 'Q22', 'Q24', 'T7', 'T8', 'C2', 'C3', 'C4', 'C5']
for it in old['requirements']:
    if it['req_id'] not in misses:
        continue
    kws = keywords(it['requirement'])
    hit = sum(1 for k in kws if k in new_raw)
    print(f"{it['req_id']} ({it['category']}) 关键词命中 {hit}/{len(kws)}")
    miss_kws = [k for k in kws if k not in new_raw]
    if miss_kws:
        print('   未命中词:', miss_kws)
    else:
        print('   -> 全文范围内全部命中（分类或措辞差异，非真遗漏）')
