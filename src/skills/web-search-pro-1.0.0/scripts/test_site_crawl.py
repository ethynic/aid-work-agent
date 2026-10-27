# -*- coding: utf-8 -*-
"""官网全站爬取：抓首页 → 抽取站内链接 → 逐页抓取 → 汇总邮箱/电话/联系人。"""
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_search

BASE = "https://www.aidingyi.cn"
START = BASE + "/"

html = web_search.fetch_raw(START)
print("首页抓取成功, 长度:", len(html))

# 抽取站内链接
links = set()
for m in re.finditer(r'href="([^"#]+)"', html):
    u = m.group(1).strip()
    if not u or u.startswith(("javascript:", "mailto:", "tel:")):
        continue
    full = urllib.parse.urljoin(BASE + "/", u)
    if full.startswith(BASE) and full not in (START,):
        links.add(full.split("#")[0])

# 常见静态页补充探测
for guess in ["/about.html", "/contact.html", "/positions.html", "/pricing.html",
              "/price.html", "/solutions.html", "/products.html", "/index.html",
              "/apply.html", "/join.html", "/about-us.html", "/cooperation.html",
              "/business.html", "/solution.html", "/agent.html", "/platform.html"]:
    links.add(BASE + guess)

print("待抓取页面:", len(links))

emails, phones, persons, metas = set(), set(), [], []
for u in sorted(links):
    try:
        h = web_search.fetch_raw(u)
    except Exception as e:
        print(f"  [跳过] {u}  ({type(e).__name__})")
        continue
    t = web_search.extract_text(h)
    c = web_search.extract_contacts(h, u)
    for e in c["emails"]:
        emails.add(e)
    for p in c["phones"]:
        phones.add(p)
    for p in c["persons"]:
        if p not in persons:
            persons.append(p)
    for m in c["meta"]:
        if m not in metas:
            metas.append(m)
    print(f"  [OK] {u}  title={c['title'][:34]!r} len={len(t)} "
          f"phones={c['phones']} emails={c['emails']}")

print()
print("=== 全站汇总 ===")
print("邮箱:", sorted(emails) or "无")
print("电话:", sorted(phones) or "无")
print("人名线索:", persons[:10] or "无")
print("备案/主体:", metas or "无")
