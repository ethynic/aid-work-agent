"""文心一言(wenxin.baidu.com) 联网采集协会基础信息原文（进程内调用单点）。

被 src/services/association_enrichment_providers.py 的 _collect_wenxin 直接
await。原 spawn 子进程模式已废弃——PyInstaller onefile 打包后找不到客户机
python，进程内调用改用 exe 内嵌的 playwright，客户机零安装。

机制（2026-08-07 已验证，见 scripts/probe-wenxin-batch.py + memory
wenxin-reuse-session-anti-captcha）：
- connect_over_cdp 复用常开调试浏览器(9222)，**禁止** launch 新实例
  （频繁新开 2 个就触发验证码）
- textarea.ci-textarea 回车发送
- .cosd-markdown-content 取最新回答，文本连续 1.5s 不变判完成
- 文心无需登录；首次若弹验证码，返回 note=captcha 由上层决定

9222 常驻浏览器由 runtime/wenxin_browser.py 的 ensure_wenxin_browser() 保证。
scripts/wenxin_collect.py 是本模块的 stdin/stdout IO 入口（开发期 probe 用）。
"""

from __future__ import annotations

import asyncio
from typing import Any

CDP_URL = "http://localhost:9222"
QUERY_TMPL = (
    "给出{name}的以下信息:地址、邮箱、官网网址、主管单位、单位等级、"
    "会员数量、分支机构数量、公众号名称、品牌会议连续次数，"
    "官网直接给网址，不要超链接。"
)
START_WAIT_SECONDS = 20    # 等新回答开始
STABLE_TICKS = 3           # 连续 3 次(每 0.5s)文本不变判完成
MAX_TOTAL_SECONDS = 90     # 单题等文本稳定的总超时上限


async def collect_one(name: str) -> dict[str, Any]:
    """对单个协会在文心提问，返回 {ok, answer, note}。"""
    # 延迟 import：避免模块加载即要求 playwright（照 llm_judge.py 范式）
    from playwright.async_api import async_playwright

    query = QUERY_TMPL.format(name=name)
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as exc:
            return {"ok": False, "note": f"cdp_attach_failed:{type(exc).__name__}"}

        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = next((p for p in ctx.pages if "wenxin" in p.url), None)
        if page is None:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://wenxin.baidu.com/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
        await page.bring_to_front()

        before = await page.evaluate(
            "() => document.querySelectorAll('.cosd-markdown-content').length"
        )
        ta = page.locator("textarea.ci-textarea").first
        await ta.click()
        await ta.fill(query)
        await asyncio.sleep(0.5)
        await ta.press("Enter")

        # 1) 验证码检测（发送后立即）
        captcha = await page.evaluate(
            "() => /安全验证|验证后继续|拖动左侧滑块/.test(document.body.innerText)"
        )
        if captcha:
            return {"ok": False, "note": "captcha"}

        # 2) 等新回答开始（.cosd-markdown-content 数量增加）
        started = False
        for _ in range(int(START_WAIT_SECONDS * 2)):
            after = await page.evaluate(
                "() => document.querySelectorAll('.cosd-markdown-content').length"
            )
            if after > before:
                started = True
                break
            await asyncio.sleep(0.5)
        if not started:
            return {"ok": False, "note": "answer_not_started"}

        # 3) 等文本稳定（连续 STABLE_TICKS 次不变）
        last = ""
        stable = 0
        ticks = 0
        while ticks < MAX_TOTAL_SECONDS * 2:
            cur = await page.evaluate(
                "() => {const els = document.querySelectorAll('.cosd-markdown-content');"
                "const el = els[els.length - 1]; return el ? el.innerText.trim() : '';}"
            )
            if cur and cur == last and len(cur) > 20:
                stable += 1
                if stable >= STABLE_TICKS:
                    break
            else:
                stable = 0
            last = cur
            ticks += 1
            await asyncio.sleep(0.5)

        if not last:
            return {"ok": False, "note": "empty_answer"}
        return {"ok": True, "answer": last, "note": ""}
        # attach 模式不 close browser —— async_playwright 退出自动断开 CDP，浏览器常驻
