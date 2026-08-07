"""批量采集：直接在文心一言（wenxin.baidu.com）提问「{协会名} 官网」，提取 AI 回答。

已验证（2026-08）：
- 未登录即可提问（无登录墙）
- 输入框：textarea.ci-textarea，fill + 回车发送
- 主体回答容器：.cosd-markdown-content（每轮一个，取最后一个=最新回答）
- 官网链接：回答内的 <a href^=http>（标题通常含协会名，直接给真实 URL）
- 完成判断：发送后等 .cosd-markdown-content 数量增加，再等文本连续 1.5s 不变

路线背景：百度搜索页 AI 摘要（wenda_generate）触发不稳定（无登录态常不出现），
改走文心直接提问——回答结构化、官网链接直接给，质量与稳定性都更好。

用法：
  python scripts/probe-wenxin-batch.py                 # 跑默认 9 个协会
  python scripts/probe-wenxin-batch.py --names 协会A 协会B

前置：需先启动带 CDP 调试端口的 Chrome（独立 user-data-dir，不碰日常 Chrome）：
  chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\\Users\\ethyn\\.cdp-chrome-assoc
脚本 attach 到 9222，跑完不关浏览器。
"""

from __future__ import annotations

import asyncio
import json
import sys

DEFAULT_ASSOCIATIONS = [
    "中国游艺机游乐园协会",
    "中国轮胎循环利用协会",
    "中国机电设备工程协会",
    "中国物资再生协会",
    "中国缝制机械协会",
    "中国口腔清洁护理用品工业协会",
    "中国日用玻璃协会",
    "中国衡器协会",
    "中国黄金协会",
]

CDP_URL = "http://localhost:9222"
TIMEOUT_PER_ONE = 60  # 单题最多等 60s
INTERVAL_SECONDS = 1  # 每题之间间隔


QUERY_TMPL = (
    "给出{name}的以下信息:地址、邮箱、官网网址、主管单位、单位等级、"
    "会员数量、分支机构数量、公众号名称、品牌会议连续次数，"
    "官网直接给网址，不要超链接。"
)


async def ask_one(page, name: str) -> dict:
    """在文心当前对话里问一个协会，等回答稳定，返回结果。"""
    query = QUERY_TMPL.format(name=name)
    before = await page.evaluate(
        "() => document.querySelectorAll('.cosd-markdown-content').length"
    )
    ta = page.locator("textarea.ci-textarea").first
    await ta.click()
    await ta.fill(query)
    await asyncio.sleep(0.5)
    await ta.press("Enter")

    # 1) 等新回答开始（.cosd-markdown-content 数量增加）
    started = False
    for _ in range(40):  # 最多 20s
        after = await page.evaluate(
            "() => document.querySelectorAll('.cosd-markdown-content').length"
        )
        if after > before:
            started = True
            break
        await asyncio.sleep(0.5)
    if not started:
        return {"name": name, "query": query, "note": "超时：新回答未开始（可能被风控）"}

    # 2) 等文本稳定（连续 3 次=1.5s 不变）
    last = ""
    stable = 0
    for _ in range(TIMEOUT_PER_ONE * 2):
        cur = await page.evaluate(
            "() => {const els = document.querySelectorAll('.cosd-markdown-content');"
            "const el = els[els.length - 1]; return el ? el.innerText.trim() : '';}"
        )
        if cur and cur == last and len(cur) > 20:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        last = cur
        await asyncio.sleep(0.5)

    answer = last
    # 3) 取回答内第一个 http 链接（通常是官网）
    link = await page.evaluate(
        r"""() => {
            const els = document.querySelectorAll('.cosd-markdown-content');
            const el = els[els.length - 1];
            if (!el) return null;
            const a = el.querySelector('a[href^="http"]');
            return a ? {text: a.innerText.trim(), href: a.href} : null;
        }"""
    )
    return {"name": name, "query": query, "official_link": link, "answer": answer}


async def start_new_chat(page) -> bool:
    """点侧边栏「开启新对话」，清空当前对话上下文。找不到按钮返回 False。"""
    clicked = await page.evaluate(
        r"""() => {
            const els = [...document.querySelectorAll('div,span,a,button')];
            const t = els.find(e => e.children.length === 0 && (e.innerText || '').trim() === '开启新对话');
            if (t) { t.click(); return true; }
            return false;
        }"""
    )
    if clicked:
        await asyncio.sleep(2.5)
    return clicked


async def main() -> None:
    from playwright.async_api import async_playwright

    names = DEFAULT_ASSOCIATIONS
    if "--names" in sys.argv:
        idx = sys.argv.index("--names")
        names = sys.argv[idx + 1 :]

    print(f"文心批量采集 {len(names)} 个协会（attach {CDP_URL}，跑完不关浏览器）", flush=True)
    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = next((p for p in ctx.pages if "wenxin" in p.url), None)
        if not page:
            page = await ctx.new_page()
            await page.goto("https://wenxin.baidu.com/")
            await asyncio.sleep(5)
        await page.bring_to_front()
        ok = await start_new_chat(page)
        print(f"[新对话] {'已开启（清空旧上下文）' if ok else '未找到按钮，沿用当前对话'}", flush=True)

        for i, name in enumerate(names, 1):
            print(f"\n{'='*50}", flush=True)
            print(f"[{i}/{len(names)}] {name}", flush=True)
            r = await ask_one(page, name)
            results.append(r)
            link = r.get("official_link")
            print(
                f"  官网: {link['href'] if link else '(回答内无链接)'}"
                + (f"  [{link['text']}]" if link else ""),
                flush=True,
            )
            print(f"  回答长度: {len(r.get('answer', ''))} 字", flush=True)
            if r.get("note"):
                print(f"  问题: {r['note']}", flush=True)
            with open("scripts/tmp-wenxin-batch.json", "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            if i < len(names):
                await asyncio.sleep(INTERVAL_SECONDS)

    print(f"\n[已写入] scripts/tmp-wenxin-batch.json", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
