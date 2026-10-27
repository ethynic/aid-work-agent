#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py —— 检索 → 抓取（必要时渲染）→ 内容级摘要 的组合管线。

解决什么问题
------------
单用 `web_search.py search` 只能拿到搜索引擎给的原始 snippet（几十个字），
信息密度低；而宿主内置的 WebSearch 会把目标页面的**内容**提炼进 snippet
（内容级摘要），这是两者最大的差距。

本脚本把整条链路串成一条命令：

0. **快照优先（第一优先级）**：拿到检索结果后，**先**对每条结果的「摘要/snippet」跑
   联系方式抽取（`extract_contacts_from_text`）。搜索引擎摘要是公开可读的，而详情页常被
   405/登录墙挡住，因此「先读摘要」是拿到电话/邮箱/人名的最快路径，也是绕过拦截的关键。
   一旦摘要里确认到**未掩码的电话或邮箱**，即判定为准确信息并**提前停止**——不再抓取
   其它平台页面（可由 `--no-snippet-stop` 关闭该提前停止行为）。
1. **检索**：`web_search.search()` 多引擎智能路由 + 降级链。
2. **抓取**：（仅当摘要未确认时才执行）对 top-N 结果逐个抓正文。抓取失败、或正文过薄
   （疑似 SPA 空壳）时，自动转 `render.py`（Playwright 无头渲染）重试一次。
3. **结构化抽取**：可选，对每个页面跑 `extract_contacts()`（邮箱/电话/负责人/备案）；
   掩码号码会与摘要里抽到的掩码号码一起跨窗口合并还原。
4. **内容级摘要**：可选。若配置了 LLM（OpenAI 兼容接口），把收集到的正文喂给模型，
   按 `--prompt` 生成聚合答案——等价于宿主 WebSearch 的内容级摘要；
   未配置 LLM 时降级为「采集模式」，输出结构化正文交由调用方智能体自行总结
   （任何智能体宿主本身都有 LLM 能力，因此此路径零额外依赖、始终可用）。

LLM 配置（任一即可，均为 OpenAI 兼容的 chat/completions 接口）
--------------------------------------------------------------
环境变量：
    WSP_LLM_BASE_URL   例 https://api.example.com/v1
    WSP_LLM_API_KEY    例 sk-xxx
    WSP_LLM_MODEL      例 gpt-4o-mini / qwen-plus / deepseek-chat
命令行：
    --llm-base-url / --llm-api-key / --llm-model（优先级高于环境变量）

用法
----
```bash
# 采集模式：检索 + 抓取 + 结构化字段（不依赖 LLM）
python pipeline.py search "爱定义（上海）科技有限公司" --top 3 --extract

# 内容级摘要（需配置 LLM）：一句话问出法人与联系方式
python pipeline.py search "爱定义（上海）科技有限公司" --top 3 \
    --prompt "提取公司法定代表人的姓名和所有对外联系电话"

# 直接给 URL（跳过检索），做摘要或抽字段
python pipeline.py urls "https://a.com,https://b.com" --extract
python pipeline.py urls-file urls.txt --prompt "总结这些页面的核心信息"
```

退出码：0 有结果；2 全部失败；3 参数错误。
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import httpx
except ImportError:
    sys.exit("[错误] 需要 httpx：pip install httpx")

import web_search
import render as render_mod

# 正文过薄阈值：低于此长度视为「疑似 SPA 空壳 / 抓取被拦」，转渲染重试
THIN_TEXT = 200
# 单页送入 LLM 的正文上限
MAX_PAGE_CHARS = 6000
# 送入 LLM 的正文总上限
MAX_TOTAL_CHARS = 40000

# 搜索引擎自身的页面（首页/地图/新闻列表）不是「内容页」，抓它们毫无意义，
# 必须从 SERP 里剔除。实测 360 引擎常把「百度搜索」「360地图」混进结果。
SEARCH_PORTAL_DOMAINS = (
    "baidu.com", "sogou.com", "so.com", "360.cn", "bing.com", "google.com",
    "duckduckgo.com", "yandex.", "naver.com", "seznam.cz", "qwant.com",
    "swisscows.com", "startpage.com", "brave.com", "goo.ne.jp", "yahoo.co.jp",
    "daum.net", "coccoc.com", "parsijoo.com", "map.baidu.com", "ditu.amap.com",
)


def _clean_serp(results, query):
    """SERP 清洗：剔除搜索引擎自身页面 + 逐条相关度过滤 + URL 去重。

    v1 的 `relevance_ok` 只要求「前 5 条里任意一条相关」即通过整批结果，因此
    一批噪音可以混在合格结果旁边返回。本函数改为**逐条**判定：
      1. URL 命中搜索引擎自身域名（baidu.com / 360.cn / bing.com…）→ 丢弃；
      2. 标题/摘要/URL 对 query 的加权命中率 < 0.34 → 丢弃。

    两档阈值：严格档（0.34）全灭时放宽到 0.15 再试一次；两档都空则返回空列表
    （宁可明确报「无结果」，也不把整批噪音放行——v1 的「全滤空就回退」在这里
    反而会放行百度首页这类垃圾）。
    """
    words = [w.lower() for w in
             re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fa5]{2,}", query)]
    if not words:
        return list(_dedup(results))
    weights = {w: (2 if len(w) >= 4 else 1) for w in words}
    total = sum(weights.values())

    def _pass(r, threshold):
        url = r.get("url", "")
        blob = f"{r.get('title', '')} {r.get('snippet', '')} {url}".lower()
        hit = sum(wt for w, wt in weights.items() if w in blob)
        return bool(total) and hit / total >= threshold

    def _portal(r):
        low = r.get("url", "").lower()
        return any(d in low for d in SEARCH_PORTAL_DOMAINS)

    def _pick(threshold):
        out = []
        for r in results:
            if _portal(r):
                continue
            if _pass(r, threshold):
                out.append(r)
        return list(_dedup(out))

    kept = _pick(0.34)
    if kept:
        return kept
    kept = _pick(0.15)
    if kept:
        return kept
    return []


def _dedup(results):
    """按规范化 URL 去重（去掉协议、www 前缀与查询串差异）。"""
    seen, out = set(), []
    for r in results:
        u = r.get("url", "")
        key = re.sub(r"^https?://", "", u.lower())
        key = re.sub(r"^www\.", "", key)
        key = key.split("?")[0].split("#")[0].rstrip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ---------------------------------------------------------------- 抓取层

def _has_key_fields(html):
    """页面里是否已有关键字段（电话/邮箱）。用于判断「正文有量但关键字段是客户端渲染的」。"""
    try:
        c = web_search.extract_contacts(html)
        return bool(c["emails"] or c["phones"])
    except Exception:
        return False


def _fetch_page(url, use_render=True, timeout=30, delay=1.5):
    """抓取一个页面，返回 (html, text, method, error)。

    method: "http"（直连成功）/ "render"（渲染成功）/ None（全失败）
    """
    errors = []
    html = None
    text = ""
    try:
        html = web_search.fetch_raw(url)
        text = web_search.extract_text(html)
        # 判定是否需要渲染：
        #   a) 正文为空或过薄 → 疑似 SPA 空壳，必渲染
        #   b) 正文有量但**关键字段全缺**，且该站已知为 JS 渲染型
        #      （企业信息平台普遍把电话/邮箱放在客户端渲染里）→ 也渲染
        if not text or len(text) < THIN_TEXT:
            need_render = True
            errors.append(f"http 正文过薄({len(text)}字)，转渲染")
        elif render_mod.is_js_heavy(url) and not _has_key_fields(html):
            need_render = True
            errors.append("http 有关键字段缺失（疑似客户端渲染），转渲染")
        else:
            need_render = False
        if not need_render:
            return html, text, "http", None
    except Exception as e:
        errors.append(f"http 失败: {type(e).__name__} {str(e)[:120]}")

    if not use_render:
        return html or "", text, None, "; ".join(errors)

    try:
        r = render_mod.render_html(url, timeout=timeout, delay=delay)
        return r["html"], r["text"], "render", "; ".join(errors)
    except Exception as e:
        errors.append(f"render 失败: {type(e).__name__} {str(e)[:160]}")
        return html or "", text, None, "; ".join(errors)


def _collect(urls, use_render=True, timeout=30, delay=1.5, extract=False,
             per_page_delay=1.0):
    """逐个抓取 URL，返回页面记录列表。"""
    pages = []
    for i, u in enumerate(urls, 1):
        print(f"[抓取 {i}/{len(urls)}] {u}", file=sys.stderr)
        html, text, method, err = _fetch_page(u, use_render=use_render,
                                              timeout=timeout, delay=delay)
        rec = {
            "url": u,
            "title": web_search.page_title(html) if html else "",
            "method": method,
            "text_len": len(text),
            "text": text[:MAX_PAGE_CHARS],
            "error": err,
        }
        if extract and html:
            try:
                c = web_search.extract_contacts(html, u)
                rec["emails"] = c["emails"]
                rec["phones"] = c["phones"]
                rec["persons"] = c["persons"]
                rec["meta"] = c["meta"]
            except Exception as e:
                rec["extract_error"] = f"{type(e).__name__}: {str(e)[:100]}"
        pages.append(rec)
        if i < len(urls):
            time.sleep(per_page_delay)
    return pages


# ---------------------------------------------------------------- LLM 层

def _llm_config(args):
    base = args.llm_base_url or os.environ.get("WSP_LLM_BASE_URL")
    key = args.llm_api_key or os.environ.get("WSP_LLM_API_KEY")
    model = args.llm_model or os.environ.get("WSP_LLM_MODEL")
    if not (base and model):
        return None, "未配置 LLM（WSP_LLM_BASE_URL / WSP_LLM_API_KEY / WSP_LLM_MODEL）"
    return {"base": base.rstrip("/"), "key": key or "", "model": model}, None


def _llm_summarize(cfg, prompt, pages):
    """调用 OpenAI 兼容 chat/completions 生成内容级摘要。失败抛异常。"""
    chunks = []
    total = 0
    for p in pages:
        if not p.get("text"):
            continue
        body = p["text"][:MAX_PAGE_CHARS]
        block = f"### 来源：{p['url']}\n{body}"
        if total + len(block) > MAX_TOTAL_CHARS:
            break
        chunks.append(block)
        total += len(block)

    if not chunks:
        raise RuntimeError("没有可用于摘要的正文内容")

    context = "\n\n---\n\n".join(chunks)
    sys_prompt = (
        "你是网页信息提炼助手。基于给定的多个网页正文回答用户问题。\n"
        "原则：1) 优先给出可直接使用的具体信息（公司全称、人名+职务、电话、邮箱、地址、"
        "备案号）；2) 明确区分「页面明确写出」与「推断」，不要编造；"
        "3) 若多个来源冲突，列出各自说法并注明来源；"
        "4) 保留原文中的号码/邮箱原样输出，不要改写格式；"
        "5) 若确实没有相关信息，直接说明「未找到」。"
    )

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user",
             "content": f"网页正文：\n\n{context}\n\n---\n\n问题：{prompt}"},
        ],
        "temperature": 0.1,
    }
    headers = {"Content-Type": "application/json"}
    if cfg.get("key"):
        headers["Authorization"] = f"Bearer {cfg['key']}"

    with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=120.0,
                                            write=10.0, pool=10.0),
                      follow_redirects=True) as c:
        r = c.post(f"{cfg['base']}/chat/completions", json=payload, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError(f"LLM 返回结构异常: {str(data)[:200]}")


# ---------------------------------------------------------------- 命令

def merge_masked_phones(pages):
    """跨页面合并掩码号码，还原完整手机号。

    原理：企业信息平台（爱企查 / 水滴信用 / 企查查 / 启信宝…）普遍对登记电话做
    部分打码，但**不同页面、不同 tab 的打码窗口不同**——例如
        企业详情页  →  1391680****   （掩后 4 位）
        核心人员页  →  139****7580   （掩中 4 位）
        水滴信用    →  139******80   （掩中 6 位）
    三个掩码的**并集**恰好覆盖 11 位全号。脚本负责抽掩码，本函数负责按位合并：
    两掩码若已知位不冲突且长度一致，则视为同一号码的不同视图，合并后未知位减少；
    合并到无 '*' 即还原完整号码。

    返回 [{number, sources, merged_from}]，number 为还原后的完整号码（无 '*'）。
    """
    items = []          # [{pattern, sources:[{url, raw}]}]
    for p in pages:
        for mp in p.get("masked_phones", []) or []:
            pat = mp.get("pattern", "")
            if not pat:
                continue
            placed = False
            for it in items:
                if len(it["pattern"]) != len(pat):
                    continue
                merged, conflict = _merge_two(it["pattern"], pat)
                if not conflict:
                    it["pattern"] = merged
                    it["sources"].append({"url": p.get("url"), "raw": mp.get("raw")})
                    placed = True
                    break
            if not placed:
                items.append({"pattern": pat,
                              "sources": [{"url": p.get("url"), "raw": mp.get("raw")}]})

    out = []
    for it in items:
        if "*" not in it["pattern"]:
            digits = re.sub(r"\D", "", it["pattern"])
            if 7 <= len(digits) <= 15:
                out.append({
                    "number": it["pattern"],
                    "digits": digits,
                    "merged_from": [s["raw"] for s in it["sources"]],
                    "sources": [s["url"] for s in it["sources"]],
                    "confidence": "high" if len(it["sources"]) > 1 else "medium",
                })
    return out


def _merge_two(a, b):
    """按位合并两个掩码。返回 (合并结果, 是否冲突)。冲突=已知位不一致=不同号码。"""
    out = []
    for ca, cb in zip(a, b):
        if ca == "*":
            out.append(cb)
        elif cb == "*":
            out.append(ca)
        elif ca == cb:
            out.append(ca)
        else:
            return a, True
    return "".join(out), False


def _render_status(use_render):
    """检查渲染能力是否可用，返回 (可用, 说明)。

    playwright 未安装或内核未下载时，渲染路径直接关闭（而非逐页失败），
    并把原因回传给调用方，让输出 JSON 自带「为何没走渲染」的线索。
    """
    if not use_render:
        return False, "渲染兜底已被 --no-render 禁用"
    ok, err = render_mod.playwright_available()
    return ok, err or "渲染可用"


def cmd_search(a):
    res = web_search.search(a.query, limit=max(a.top * 3, 6), engine=a.engine,
                             companies_only=a.companies_only)
    if not res["results"]:
        print(json.dumps({"query": a.query, "engine": res.get("engine"),
                          "results": [], "pages": [],
                          "errors": res.get("errors", []),
                          "hint": res.get("hint", "")},
                         ensure_ascii=False, indent=2))
        return 2

    render_ok, render_note = _render_status(not a.no_render)
    serp = res["results"]
    if not a.no_clean_serp:
        cleaned = _clean_serp(serp, a.query)
        if len(cleaned) != len(serp):
            out_serp_note = f"SERP 清洗：剔除 {len(serp) - len(cleaned)} 条噪音结果"
        else:
            out_serp_note = None
        serp = cleaned
    else:
        out_serp_note = None

    urls = [r["url"] for r in serp[:a.top]]
    if not urls:
        print(json.dumps({
            "query": a.query, "engine": res.get("engine"), "mode": "collect",
            "serp": [], "pages": [], "errors": res.get("errors", []),
            "hint": ("SERP 清洗后无可用结果：搜索引擎返回的多为自身页面或与查询不相关的"
                     "条目。建议：换更长的查询词（如加「有限公司」「品牌」等上下文）、"
                     "或改用 --engine 指定其它引擎、或直接用 urls 模式抓已知页面。"),
        }, ensure_ascii=False, indent=2))
        return 2

    # ---------------------------------------------------------------
    # 快照优先（第一优先级）：先从每个结果的「摘要/snippet」抽取联系方式，
    # 摘要里往往就藏着电话/邮箱/人名，无需再抓目标页面。
    # 很多详情页（企查查/天眼查等）被 405/登录墙挡住，但搜索引擎摘要是公开可读的，
    # 因此「先读摘要」是绕过拦截、拿到准确信息的最快路径。
    # ---------------------------------------------------------------
    snippet_findings = []
    for r in serp[:a.top]:
        sn = r.get("snippet", "") or ""
        if not sn:
            continue
        c = web_search.extract_contacts_from_text(sn, r.get("url", ""))
        if c["emails"] or c["phones"] or c["persons"] or c["meta"] or c["masked_phones"]:
            snippet_findings.append({
                "url": r.get("url"),
                "title": r.get("title"),
                "emails": c["emails"],
                "phones": c["phones"],
                "masked_phones": [m["pattern"] for m in c["masked_phones"]],
                "persons": c["persons"],
                "meta": c["meta"],
            })

    # 确认标准：摘要里出现了「未掩码的电话」或「邮箱」→ 视为已确认准确信息。
    confirmed = bool(snippet_findings) and any(
        (f["emails"] or f["phones"]) for f in snippet_findings)

    early_stop = False
    if confirmed and not a.no_snippet_stop:
        # 快照已确认到准确联系方式 → 提前停止，不再抓取其它平台页面
        # （用户需求：一旦确认是准确信息，其他平台也不需要去找了）
        pages = []
        early_stop = True
    else:
        pages = _collect(urls, use_render=render_ok, timeout=a.timeout,
                         delay=a.delay, extract=a.extract)

    out = {
        "query": a.query,
        "engine": res.get("engine"),
        "route": res.get("route", []),
        "serp": serp[:a.top],
        # 第一优先级：搜索引擎快照（摘要）里直接抽到的联系方式
        "snippet_findings": snippet_findings,
        "confirmed_from_snippet": confirmed,
        "early_stop": early_stop,
        "pages": pages,
        "errors": res.get("errors", []),
        "render": {"enabled": render_ok, "note": render_note},
    }
    if out_serp_note:
        out["serp_clean_note"] = out_serp_note
    if early_stop:
        out["early_stop_note"] = (
            "快照已确认到准确联系方式（电话/邮箱），按策略提前停止，"
            "未再抓取其它平台页面。")

    if a.extract:
        # 合并「页面」与「快照」里的掩码号码，提升跨窗口还原成功率
        merge_src = list(pages)
        for f in snippet_findings:
            merge_src.append({
                "url": f["url"],
                "masked_phones": [{"pattern": p, "raw": p}
                                  for p in f["masked_phones"]],
            })
        recovered = merge_masked_phones(merge_src)
        if recovered:
            out["recovered_phones"] = recovered

    if a.prompt:
        cfg, cerr = _llm_config(a)
        if cfg:
            try:
                out["mode"] = "llm"
                out["answer"] = _llm_summarize(cfg, a.prompt, pages)
            except Exception as e:
                out["mode"] = "collect"
                out["llm_error"] = f"{type(e).__name__}: {str(e)[:200]}"
                out["hint"] = ("LLM 调用失败，已降级为采集模式：由调用方智能体基于 "
                               "pages[].text 自行总结。")
        else:
            out["mode"] = "collect"
            out["hint"] = (f"{cerr}；已降级为采集模式。由调用方智能体基于 pages[].text "
                           "自行总结，或配置 LLM 后重跑。")
    else:
        out["mode"] = "collect"

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if pages else 2


def cmd_urls(a):
    if a.urls_file:
        with open(a.urls_file, encoding="utf-8") as f:
            urls = [l.strip() for l in f
                    if l.strip() and not l.startswith("#")]
    else:
        raw = a.url_list or a.urls or ""
        urls = [u.strip() for u in raw.split(",") if u.strip()]
    if not urls:
        print("[错误] 未提供 URL", file=sys.stderr)
        return 3

    render_ok, render_note = _render_status(not a.no_render)
    pages = _collect(urls, use_render=render_ok, timeout=a.timeout,
                     delay=a.delay, extract=a.extract)
    out = {"mode": "collect", "pages": pages, "count": len(pages),
           "render": {"enabled": render_ok, "note": render_note}}
    if a.extract:
        recovered = merge_masked_phones(pages)
        if recovered:
            out["recovered_phones"] = recovered

    if a.prompt:
        cfg, cerr = _llm_config(a)
        if cfg:
            try:
                out["mode"] = "llm"
                out["answer"] = _llm_summarize(cfg, a.prompt, pages)
            except Exception as e:
                out["llm_error"] = f"{type(e).__name__}: {str(e)[:200]}"
                out["hint"] = "LLM 调用失败，已降级为采集模式。"
        else:
            out["hint"] = f"{cerr}；已降级为采集模式。"

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if pages else 2


def main():
    ap = argparse.ArgumentParser(
        description="检索+抓取(+渲染)+内容级摘要 的组合管线")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="检索 → 抓取 → 摘要")
    p.add_argument("query")
    p.add_argument("--top", type=int, default=3, help="处理前 N 条结果，默认 3")
    p.add_argument("--no-snippet-stop", action="store_true",
                   help="关闭「快照确认后停止」：即使摘要已确认到电话/邮箱，"
                        "仍继续抓取前 N 个页面（默认：快照确认即停止，不再找其它平台）")
    p.add_argument("--prompt", help="摘要指令（配合 LLM 使用）")
    p.add_argument("--extract", action="store_true", help="额外抽取邮箱/电话/负责人/备案")
    p.add_argument("--engine", default="auto", help="搜索引擎，默认 auto")
    p.add_argument("--companies-only", action="store_true", help="搜官网：过滤噪音域名")
    p.add_argument("--no-clean-serp", action="store_true",
                   help="不清洗 SERP（保留搜索引擎自身页面等噪音）")
    p.add_argument("--no-render", action="store_true", help="禁用渲染兜底")
    p.add_argument("--timeout", type=int, default=30, help="渲染超时秒数")
    p.add_argument("--delay", type=float, default=1.5, help="渲染后等待秒数")
    p.add_argument("--llm-base-url", help="OpenAI 兼容接口 base url")
    p.add_argument("--llm-api-key", help="API key")
    p.add_argument("--llm-model", help="模型名")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("urls", help="直接抓取给定 URL 并摘要")
    p.add_argument("url_list", nargs="?",
                   help="逗号分隔的 URL 列表（位置参数，与 --urls 等价）")
    p.add_argument("--urls", help="逗号分隔的 URL 列表")
    p.add_argument("--urls-file", help="文件，每行一个 URL")
    p.add_argument("--prompt", help="摘要指令")
    p.add_argument("--extract", action="store_true", help="额外抽取邮箱/电话/负责人")
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--delay", type=float, default=1.5)
    p.add_argument("--llm-base-url")
    p.add_argument("--llm-api-key")
    p.add_argument("--llm-model")
    p.set_defaults(func=cmd_urls)

    a = ap.parse_args()
    try:
        return a.func(a)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[失败] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
