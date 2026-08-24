"""30 协会真机验证：官网领导页搜索 + 秘书长提取全链路探针。

目的（2026-08 官网采集优化验收）：
- 文心只用来要官网网址（其余信息不在本探针范围）；
- 跑生产同款 collect_official_profile（词表采集 max_pages=8 + 领导页
  LLM 逐层导航搜索 + 强化后的领导提取提示词）；
- 报告每家：官网 URL、词表采集页数、是否触发 LLM 搜索、找到的领导页、
  提取出的 秘书长/会员服务/办公室负责人 姓名。

用法：
  python scripts/probe-leadership-search.py --names 协会A 协会B
  python scripts/probe-leadership-search.py --file names.txt          # 每行一个协会名
  python scripts/probe-leadership-search.py --file names.txt --start 10   # 断点续跑
  python scripts/probe-leadership-search.py --file names.txt --dry   # 只解析URL不跑采集

前置：Chrome 装好即可（脚本自动拉起 9222 调试浏览器）；.env 有 DeepSeek key。
输出：probe-output/leadership-report.json（增量写，可中断续跑）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "clients" / "association-client-cli"))

REPORT_PATH = ROOT / "probe-output" / "leadership-report.json"
URL_QUERY_TMPL = "「{name}」的官网网址是什么？直接给完整网址，不要超链接。"
_URL_RE = re.compile(r"https?://[A-Za-z0-9.\-]+\.[A-Za-z]{2,}[^\s，。\"'\)）]*")
_RETRYABLE_NAV_ERRORS = (
    "ERR_CERT_COMMON_NAME_INVALID",
    "ERR_CERT_AUTHORITY_INVALID",
    "ERR_SSL_PROTOCOL_ERROR",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_RESET",
    "ERR_NAME_NOT_RESOLVED",
    "TimeoutError",
)


def _swap_scheme(url: str) -> str | None:
    if url.startswith("https://"):
        return "http://" + url[len("https://"):]
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return None


def _is_retryable_navigation_error(exc: Exception) -> bool:
    text = str(exc)
    return any(code in text for code in _RETRYABLE_NAV_ERRORS)


async def resolve_official_url(name: str) -> str | None:
    """文心要官网网址（生产链路同款 runtime.wenxin_collector，tab 隔离）。"""
    from runtime.wenxin_collector import collect_query

    result = await collect_query(URL_QUERY_TMPL.format(name=name))
    if not isinstance(result, dict) or not result.get("ok"):
        print(f"  [文心] 失败 note={result.get('note') if isinstance(result, dict) else type(result).__name__}")
        return None
    match = _URL_RE.search(result.get("answer") or "")
    if not match:
        print(f"  [文心] 回答无URL：{(result.get('answer') or '')[:80]}")
        return None
    url = match.group(0).rstrip("。，；")
    print(f"  [文心] 官网={url}")
    return url


async def probe_one(name: str, gateway, explicit_url: str | None = None) -> dict:
    # 名单文件带的 URL 优先（文心现场解析可能给错/死链）
    url = explicit_url or await resolve_official_url(name)
    if not url:
        return {"association": name, "error": "WENXIN_URL_NOT_FOUND"}

    events = []

    def audit(**event):
        events.append(
            {
                "stage": event.get("stage", ""),
                "kind": event.get("kind", ""),
                "summary": event.get("summary", ""),
            }
        )

    from src.services.association_enrichment_providers import (
        ProjectAssociationProviders,
    )

    providers = ProjectAssociationProviders(
        repository_root=ROOT, gateway=gateway, audit_callback=audit
    )
    # 与生产 enricher 相同的协议降级：https 证书错误/不可达时换 http 再试
    try:
        values = await providers.collect_official_profile(
            url, False, association_name=name
        )
    except Exception as first_exc:
        swapped = _swap_scheme(url)
        if not swapped or not _is_retryable_navigation_error(first_exc):
            return {
                "association": name,
                "url": url,
                "error": f"{type(first_exc).__name__}: {first_exc}",
                "events": events,
            }
        try:
            values = await providers.collect_official_profile(
                swapped, False, association_name=name
            )
        except Exception as exc:
            return {
                "association": name,
                "url": url,
                "error": f"{type(exc).__name__}: {exc}",
                "events": events,
            }

    searched = any(e["stage"] == "领导页搜索" for e in events)
    found_pages = [e["summary"] for e in events if e["kind"] == "leadership_page_found"]
    return {
        "association": name,
        "url": url,
        "secretary_general_name": values.get("secretary_general_name"),
        "member_director_name": values.get("member_director_name"),
        "office_director_name": values.get("office_director_name"),
        "llm_search_triggered": searched,
        "leadership_pages_found": found_pages,
        "events": events,
        "probed_at": datetime.now().isoformat(timespec="seconds"),
    }


def load_report() -> dict:
    if REPORT_PATH.exists():
        return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    return {"results": []}


def save_report(report: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def print_summary(report: dict) -> None:
    results = report["results"]
    done = [r for r in results if "error" not in r]
    print("\n" + "=" * 60)
    print(f"进度：{len(done)}/{len(results)} 家成功跑完采集")
    secretary_hit = sum(1 for r in done if r.get("secretary_general_name"))
    print(f"秘书长姓名命中：{secretary_hit}/{len(done)}")
    searched = [r for r in done if r.get("llm_search_triggered")]
    print(f"触发 LLM 领导页搜索：{len(searched)} 家")
    print("-" * 60)
    for r in results:
        if "error" in r:
            print(f"❌ {r['association']}: {r['error'][:60]}")
        else:
            mark = "✅" if r.get("secretary_general_name") else "❌"
            print(
                f"{mark} {r['association']}: 秘书长={r.get('secretary_general_name')} "
                f"会员={r.get('member_director_name')} 办公室={r.get('office_director_name')}"
                + (" [LLM搜索]" if r.get("llm_search_triggered") else "")
            )
    print("=" * 60)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", nargs="+", help="协会名列表")
    parser.add_argument("--file", help="协会名文件（每行一个，可 名称,官网URL）")
    parser.add_argument("--start", type=int, default=0, help="跳过前 N 家（断点续跑）")
    parser.add_argument("--limit", type=int, default=0, help="只跑 N 家（0=全部）")
    parser.add_argument("--dry", action="store_true", help="只解析URL，不跑采集")
    args = parser.parse_args()

    names: list[str] = []
    explicit_urls: dict[str, str] = {}
    if args.names:
        names.extend(args.names)
    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "," in line:
                name, url = line.split(",", 1)
                names.append(name.strip())
                if url.strip():
                    explicit_urls[name.strip()] = url.strip()
            else:
                names.append(line)

    if not names:
        parser.error("--names 或 --file 必填")
    names = names[args.start:]
    if args.limit:
        names = names[: args.limit]
    print(f"待验证 {len(names)} 家：{names}")

    from runtime.wenxin_browser import ensure_wenxin_browser

    await ensure_wenxin_browser()
    print("9222 调试浏览器就绪")

    if args.dry:
        for name in names:
            url = explicit_urls.get(name) or await resolve_official_url(name)
            print(f"{name}: {url}")
        return 0

    from src.llm.gateway import llm_gateway

    report = load_report()
    for name in names:
        # 只有已拿到秘书长的条目才算完成；空值/报错条目一律重跑
        if any(
            r["association"] == name and r.get("secretary_general_name")
            for r in report["results"]
        ):
            print(f"\n>>> {name} 已有结果，跳过")
            continue
        print(f"\n>>> {name}")
        try:
            result = await probe_one(
                name, llm_gateway, explicit_url=explicit_urls.get(name)
            )
        except Exception as exc:
            result = {"association": name, "error": f"{type(exc).__name__}: {exc}"}
        report["results"] = [
            r for r in report["results"] if r["association"] != name
        ] + [result]
        save_report(report)
        if "error" in result:
            print(f"  ❌ {result['error'][:80]}")
        else:
            print(
                f"  秘书长={result.get('secretary_general_name')} "
                f"会员={result.get('member_director_name')} "
                f"办公室={result.get('office_director_name')}"
            )
        await asyncio.sleep(2)

    print_summary(report)
    print(f"报告：{REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
