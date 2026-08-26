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
- 每个协会开新 tab 拿全新会话（旧会话上下文变长会降准）；先开新 tab 并就绪、
  再关旧 wenxin tab，始终至少留一个 tab，浏览器常驻不关。新 tab 复用同一
  profile/cookie 会话，不会像新开浏览器那样触发验证码

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
    "以及现任秘书长、会员服务相关部门负责人、办公室或综合办负责人的姓名，"
    "官网直接给网址字符串，不要超链接。"
)
START_WAIT_SECONDS = 20    # 等新回答开始
STABLE_TICKS = 3           # 连续 3 次(每 0.5s)文本不变判完成
MAX_TOTAL_SECONDS = 90     # 单题等文本稳定的总超时上限

# 文心偶发「规划/拒答」型回答（真机 2026-08-26：银行保险资产管理业协会首次
# 回答输出 replan JSON 拒答；同一会话原样重发同一提示词，第二次即给出完整
# 信息含官网。重试必须在同一 tab 同一会话——新开 tab 是全新会话会重复拒答）
_REPLAN_MARKERS = (
    "重新规划检索",
    "网页素材",
    "rewrite_query",
    "无法完整满足",
    "需要重新规划",
)


def looks_like_replan(answer: str) -> bool:
    """回答是否是文心的规划/拒答型输出（无实际信息，值得同会话重发一次）。"""
    if not answer:
        return False
    return any(marker in answer for marker in _REPLAN_MARKERS)


async def _send_and_wait(page, query: str, before_count: int) -> tuple[str, int, str]:
    """在给定 tab 发送 query 并等新回答文本稳定。

    返回 (回答文本, 最新回答计数, 失败 note)。note 非空表示失败（验证码/
    未开始/空回答）。计数取 DOM 实际值，供同会话重发时判断「新回答出现」。
    """
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
        return "", before_count, "captcha"

    # 2) 等新回答开始（.cosd-markdown-content 数量增加）
    started = False
    for _ in range(int(START_WAIT_SECONDS * 2)):
        after = await page.evaluate(
            "() => document.querySelectorAll('.cosd-markdown-content').length"
        )
        if after > before_count:
            started = True
            break
        await asyncio.sleep(0.5)
    if not started:
        return "", before_count, "answer_not_started"

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
        return "", before_count, "empty_answer"
    count = await page.evaluate(
        "() => document.querySelectorAll('.cosd-markdown-content').length"
    )
    return last, count, ""


async def collect_query(query: str) -> dict[str, Any]:
    """对文心发送任意 query，返回 {ok, answer, note}。

    query 进、answer 文本出，不绑定提问模板。复用 9222 常驻浏览器（由
    runtime/wenxin_browser.ensure_wenxin_browser 保证）。collect_one 用
    QUERY_TMPL 调本函数。

    首次回答是规划/拒答型（looks_like_replan）时，在同一 tab 同一会话
    **原样重发同一提示词**再试一次——真机（2026-08-26 银行保险资产管理业
    协会）：新开 tab 是全新会话会重复同样的拒答；同会话原样重发，第二次
    即给出完整信息（地址/官网/主管单位）。重试仍拒答/失败则返回首答，
    上层解析自然走 DeepSeek 兜底。
    """
    # 延迟 import：避免模块加载即要求 playwright（照 llm_judge.py 范式）
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as exc:
            return {"ok": False, "note": f"cdp_attach_failed:{type(exc).__name__}"}

        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        # 每个协会开新 tab 拿全新会话（旧会话上下文变长，联网回答易失准）。
        # 先开新 tab 并就绪，再关旧 wenxin tab —— 始终至少留一个 tab，不整关浏览器。
        # 只关 URL 含 wenxin 的旧 tab，不碰官网采集等其他域页面（同一 9222 浏览器）。
        stale_pages = [p for p in ctx.pages if "wenxin" in (p.url or "")]
        page = await ctx.new_page()
        await page.goto("https://wenxin.baidu.com/", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        await page.bring_to_front()
        for stale in stale_pages:
            try:
                await stale.close()
            except Exception:
                pass

        before = await page.evaluate(
            "() => document.querySelectorAll('.cosd-markdown-content').length"
        )
        answer, count, note = await _send_and_wait(page, query, before)
        if note:
            return {"ok": False, "note": note}

        if looks_like_replan(answer):
            # 同一会话原样重发（间隔 2s 模拟真人节奏）
            await asyncio.sleep(2)
            retry_answer, _retry_count, retry_note = await _send_and_wait(
                page, query, count
            )
            if not retry_note and retry_answer:
                return {"ok": True, "answer": retry_answer, "note": ""}
            # 重试失败（验证码/超时）：返回首答，上层按原文质量自行兜底
        return {"ok": True, "answer": answer, "note": ""}
        # attach 模式不 close browser —— async_playwright 退出自动断开 CDP，浏览器常驻


async def collect_one(name: str) -> dict[str, Any]:
    """对单个协会用基础信息模板（QUERY_TMPL）在文心提问，返回 {ok, answer, note}。"""
    return await collect_query(QUERY_TMPL.format(name=name))
