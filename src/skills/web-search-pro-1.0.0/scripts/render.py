#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render.py —— 无头浏览器渲染兜底（解决 JS 重站点抓不到内容的问题）。

为什么需要这个脚本
------------------
`web_search.py` 的 fetch 是纯 HTTP 抓取，对「首屏由 JavaScript 渲染」的站点只能拿到
骨架 HTML，正文为空。典型场景（本技能实测）：

- `aiqicha.baidu.com`（爱企查）：SPA 结构，纯 HTTP 只返回标题「爱企查」，工商信息全部
  在客户端渲染后写入 DOM；
- `qcc.com` / `tianyancha.com`：同上，且有反爬指纹；
- `zhipin.com` / `m.zhaopin.com`：职位内容懒加载；
- 各类官网的 `positions.html` 等由前端框架生成的页面。

本脚本用 Playwright 驱动 Chromium 真正渲染页面，把渲染后的 HTML / 正文交出去，
作为纯 HTTP 抓取失败时的降级通道。

与 pipeline.py 的关系
---------------------
`pipeline.py` 会在「HTTP 抓取抛异常」或「正文过薄（疑似 SPA）」时自动调用本脚本，
无需手工串联。本脚本也可单独使用。

依赖（可选）
------------
本脚本是**可选增强**：未安装 Playwright 时以退出码 4 + 安装指引退出，不会让上层流程崩掉。

```bash
pip install playwright
python -m playwright install chromium      # 下载浏览器内核（约 150MB，一次性）
```

退出码：0 成功；2 渲染失败；3 参数错误；4 Playwright 不可用（未装包 / 未下内核）。
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# 常见的「必须渲染才有内容」域名。命中时 pipeline 会**优先**走渲染。
JS_HEAVY_DOMAINS = (
    "aiqicha.baidu.com", "qcc.com", "tianyancha.com", "shuidi.cn",
    "zhipin.com", "zhaopin.com", "liepin.com", "lagou.com",
    "weibo.com", "zhihu.com", "xiaohongshu.com",
)

# 这些域名直接抓 HTTP 即可成功，渲染反而慢（且可能被风控），跳过渲染。
RENDER_SKIP_DOMAINS = (
    "google.com", "bing.com", "baidu.com", "sogou.com", "so.com",
)


def _playwright():
    """返回 (sync_playwright, error_msg)。未安装时 error_msg 非空。"""
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright, None
    except ImportError:
        return None, ("未安装 playwright。安装命令："
                      "pip install playwright && python -m playwright install chromium")


def _is_env_missing(exc):
    """判断异常是否为「Playwright 环境缺失」（未装包 / 未下浏览器内核）。

    Playwright 在浏览器内核缺失时抛的是 `Error: Executable doesn't exist at ...,
    run "playwright install" to install browser binaries`，不是 ImportError，
    因此必须按消息特征识别，否则会被当成普通渲染失败（退出码 2）而误导使用者。
    """
    msg = str(exc).lower()
    return ("playwright install" in msg
            or "executable doesn't exist" in msg
            or "browsertype.launch" in msg and "executable" in msg)


def playwright_available():
    """返回 (可用, 错误信息)。包已装但内核未下载时同样返回不可用。"""
    sync_playwright, err = _playwright()
    if err:
        return False, err
    try:
        with sync_playwright() as p:
            p.chromium.launch(headless=True, args=["--no-sandbox"]).close()
        return True, None
    except Exception as e:
        if _is_env_missing(e):
            return False, ("已安装 playwright 但缺少浏览器内核。安装命令："
                           "python -m playwright install chromium")
        return True, None   # 其他异常属于渲染问题，交给主流程处理


def is_js_heavy(url):
    """判断 URL 是否属于「需渲染才有内容」的站点。"""
    low = (url or "").lower()
    if any(d in low for d in RENDER_SKIP_DOMAINS):
        return False
    return any(d in low for d in JS_HEAVY_DOMAINS)


def render_html(url, timeout=30, wait_until="domcontentloaded", delay=1.5,
                wait_for=None, block_resources=True, user_agent=UA,
                viewport="1440x900", scroll=True, warmup=None,
                storage=None, save_storage=None, stealth=True, proxy=None):
    """渲染页面，返回 {url, title, html, text, elapsed_ms}。

    失败抛异常（由调用方决定降级策略）。资源拦截默认开启（图片/字体/媒体），
    可显著提速；需要完整资源时传 block_resources=False。

    反爬对抗参数：
      warmup      先访问该 URL 拿到 cookie（再跳目标页），显著提升有 cookie 校验的站点成功率
      storage     storage_state JSON 路径，跨次运行复用 cookie / localStorage
      save_storage 渲染后把会话存到该路径
      stealth     去掉 navigator.webdriver 等自动化特征
      proxy       浏览器代理，形如 http://127.0.0.1:7890（本机在代理网络下时必需）
    """
    sync_playwright, err = _playwright()
    if err:
        raise RuntimeError(err)

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    started = time.time()
    with sync_playwright() as p:
        try:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ]
            launch_kwargs = {"headless": True, "args": launch_args}
            if proxy:
                p_url = proxy if "://" in proxy else f"http://{proxy}"
                launch_kwargs["proxy"] = {"server": p_url}
            browser = p.chromium.launch(**launch_kwargs)
        except Exception as e:
            if _is_env_missing(e):
                raise RuntimeError(
                    "已安装 playwright 但缺少浏览器内核。安装命令："
                    "python -m playwright install chromium")
            raise
        try:
            ctx_kwargs = {
                "user_agent": user_agent,
                "locale": "zh-CN",
                "viewport": {"width": int(viewport.split("x")[0]),
                             "height": int(viewport.split("x")[1])},
            }
            if storage and os.path.exists(storage):
                try:
                    ctx_kwargs["storage_state"] = storage
                except Exception:
                    pass
            ctx = browser.new_context(**ctx_kwargs)
            page = ctx.new_page()

            if stealth:
                # 抹掉最明显的自动化指纹
                try:
                    page.add_init_script(
                        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                        "window.chrome={runtime:{}};"
                        "Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh']});"
                        "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});")
                except Exception:
                    pass

            if block_resources:
                def _route(r):
                    if r.request.resource_type in ("image", "media", "font"):
                        r.abort()
                    else:
                        r.continue_()
                page.route("**/*", _route)

            # cookie 预热：先访问引导页拿会话，再打开目标页
            if warmup:
                try:
                    page.goto(warmup, timeout=timeout * 1000, wait_until="load")
                    page.wait_for_timeout(1200)
                except Exception:
                    pass

            page.goto(url, timeout=timeout * 1000, wait_until=wait_until)

            # 等指定元素出现（可选）——比固定 sleep 更稳
            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=timeout * 1000)
                except Exception:
                    pass

            if scroll:
                # 触发懒加载：滚到底再回顶
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(600)
                    page.evaluate("window.scrollTo(0, 0)")
                except Exception:
                    pass

            if delay > 0:
                page.wait_for_timeout(int(delay * 1000))

            html = page.content()
            title = page.title()

            if save_storage:
                try:
                    ctx.storage_state(path=save_storage)
                except Exception:
                    pass
        finally:
            browser.close()

    text = ""
    try:
        from web_search import extract_text
        text = extract_text(html)
    except Exception:
        pass

    return {
        "url": url,
        "title": title or "",
        "html": html,
        "text": text,
        "html_len": len(html),
        "text_len": len(text),
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def _print_hint(err):
    print(json.dumps({
        "error": "playwright_unavailable",
        "message": err,
        "hint": ("安装后重试：pip install playwright && python -m playwright install chromium。"
                 "若浏览器内核已装但仍报错，多为网络代理问题，可用 --proxy 参数。"),
    }, ensure_ascii=False), file=sys.stderr)
    return 4


def main():
    ap = argparse.ArgumentParser(
        description="无头浏览器渲染兜底：把 JS 渲染后的页面 HTML/正文取出来")
    ap.add_argument("url", nargs="?", help="目标 URL")
    ap.add_argument("--output", help="把渲染后的 HTML 写入文件（默认输出到 stdout）")
    ap.add_argument("--timeout", type=int, default=30, help="单页超时秒数，默认 30")
    ap.add_argument("--wait-until", default="domcontentloaded",
                    choices=["load", "domcontentloaded", "networkidle", "commit"],
                    help="页面加载完成判定，默认 domcontentloaded")
    ap.add_argument("--delay", type=float, default=1.5,
                    help="渲染后额外等待秒数，给异步内容落地时间，默认 1.5")
    ap.add_argument("--wait-for", help="等待某个 CSS 选择器出现后再取内容")
    ap.add_argument("--no-block-resources", action="store_true",
                    help="不拦截图片/字体/媒体（更慢但更完整）")
    ap.add_argument("--no-scroll", action="store_true", help="不滚动页面（不触发懒加载）")
    ap.add_argument("--viewport", default="1440x900", help="视口尺寸，默认 1440x900")
    ap.add_argument("--user-agent", default=UA)
    ap.add_argument("--screenshot", help="同时截图保存到该路径")
    ap.add_argument("--warmup", help="先访问该 URL 拿 cookie，再打开目标页（对抗风控）")
    ap.add_argument("--storage", help="storage_state JSON 路径，跨次运行复用会话")
    ap.add_argument("--save-storage", help="渲染后把会话存到该路径")
    ap.add_argument("--no-stealth", action="store_true", help="不隐藏自动化指纹")
    ap.add_argument("--proxy", help="浏览器代理，形如 http://127.0.0.1:7890")
    ap.add_argument("--json", action="store_true", help="输出 JSON（含正文，不含 html 全文）")
    ap.add_argument("--text", action="store_true", help="只输出纯正文")
    ap.add_argument("--batch", help="文件，每行一个 URL，批量渲染")
    ap.add_argument("--delay-between", type=float, default=2.0, help="批量模式每页间隔秒数")
    a = ap.parse_args()

    if not a.url and not a.batch:
        ap.error("需要提供 url 或 --batch")

    # 前置体检：包没装或内核没下载时立刻退出 4 + 安装指引，避免逐页失败浪费时间
    ok, env_err = playwright_available()
    if not ok:
        return _print_hint(env_err)

    def _do(u):
        return render_html(
            u, timeout=a.timeout, wait_until=a.wait_until, delay=a.delay,
            wait_for=a.wait_for, block_resources=not a.no_block_resources,
            user_agent=a.user_agent, viewport=a.viewport, scroll=not a.no_scroll,
            warmup=a.warmup, storage=a.storage,
            save_storage=a.save_storage if not a.batch else None,
            stealth=not a.no_stealth, proxy=a.proxy,
        )

    try:
        if a.batch:
            with open(a.batch, encoding="utf-8") as f:
                urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            out = []
            for i, u in enumerate(urls, 1):
                print(f"[{i}/{len(urls)}] {u}", file=sys.stderr)
                try:
                    r = _do(u)
                    r.pop("html", None)
                    out.append(r)
                except RuntimeError as e:
                    if "playwright" in str(e).lower():
                        return _print_hint(str(e))
                    out.append({"url": u, "error": f"{type(e).__name__}: {str(e)[:200]}"})
                time.sleep(a.delay_between)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        try:
            r = _do(a.url)
        except RuntimeError as e:
            msg = str(e)
            if "playwright" in msg.lower() and "install" in msg.lower():
                return _print_hint(msg)
            print(f"[失败] {msg}", file=sys.stderr)
            return 2
        except Exception as e:
            print(f"[失败] {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)
            return 2

        if a.screenshot:
            try:
                sync_playwright, _ = _playwright()
                with sync_playwright() as p:
                    b = p.chromium.launch(headless=True, args=["--no-sandbox"])
                    pg = b.new_context(user_agent=a.user_agent).new_page()
                    pg.goto(r["url"], timeout=a.timeout * 1000)
                    pg.wait_for_timeout(int(a.delay * 1000))
                    pg.screenshot(path=a.screenshot, full_page=True)
                    b.close()
            except Exception as e:
                print(f"[截图失败] {type(e).__name__}: {str(e)[:160]}", file=sys.stderr)

        if a.output:
            with open(a.output, "w", encoding="utf-8") as f:
                f.write(r["html"])
            print(f"[已保存] {a.output}  ({r['html_len']} 字节, {r['elapsed_ms']}ms)")

        if a.text:
            print(r["text"])
        elif a.json:
            d = dict(r)
            d.pop("html", None)
            print(json.dumps(d, ensure_ascii=False, indent=2))
        elif not a.output:
            print(r["html"])
        return 0
    except SystemExit:
        raise
    except Exception as e:
        print(f"[失败] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
