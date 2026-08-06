"""无界面的协会批量资料补全 CLI。"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.association_batch_enrichment import (  # noqa: E402
    AssociationBatchEnricher,
    parse_association_input,
    write_enrichment_workbook,
)

_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _redact_mobiles(text: str) -> str:
    return _MOBILE_RE.sub(
        lambda match: f"{match.group()[:3]}****{match.group()[-4:]}",
        text,
    )


def _safe_error_code(exc: Exception) -> str:
    message = str(exc)
    if re.fullmatch(r"[A-Z][A-Z0-9_]{0,159}", message):
        return message
    return type(exc).__name__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量采集协会官网与微信联系人资料")
    parser.add_argument(
        "--association",
        action="append",
        default=[],
        help="一个或多个协会名称；可重复传参，也可用换行/逗号分隔",
    )
    parser.add_argument("--input", help="包含协会名称列的 CSV 或 XLSX")
    parser.add_argument("--output", required=True, help="输出 XLSX 路径")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只解析并去重输入，不访问网站、LLM 或微信",
    )
    return parser


async def run(args: argparse.Namespace) -> dict[str, object]:
    names = parse_association_input(
        text_values=args.association,
        input_path=args.input,
    )
    if args.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "association_count": len(names),
            "associations": [_redact_mobiles(name) for name in names],
        }

    from src.services.association_enrichment_providers import (
        ProjectAssociationProviders,
    )

    providers = ProjectAssociationProviders(repository_root=ROOT)
    def report_progress(message: str) -> None:
        print(_redact_mobiles(message), file=sys.stderr, flush=True)

    enricher = AssociationBatchEnricher(
        official_site_resolver=providers.resolve_official_site,
        official_profile_collector=providers.collect_official_profile,
        fallback_profile_provider=providers.search_profile,
        wechat_mobile_provider=providers.wechat_mobile,
        wechat_leader_name_provider=providers.wechat_search_leader_name,
        headless=False,
        progress_reporter=report_progress,
    )
    rows = await enricher.enrich_many(names)
    report_progress("正在写入 Excel 结果")
    output = write_enrichment_workbook(rows, args.output)
    failed_count = sum(row.processing_status == "failed" for row in rows)
    batch_aborted = bool(getattr(rows, "aborted", False))
    aborted_count = (len(names) - len(rows)) + (1 if batch_aborted else 0)
    business_failed = failed_count > 0 or batch_aborted
    return {
        "ok": not business_failed,
        "business_status": "failed" if business_failed else "completed",
        "association_count": len(names),
        "processed_count": len(rows),
        "aborted_count": aborted_count,
        "abort_error_code": getattr(rows, "abort_error_code", None),
        "complete_count": sum(row.processing_status == "complete" for row in rows),
        "partial_count": sum(row.processing_status == "partial" for row in rows),
        "failed_count": failed_count,
        "output": _redact_mobiles(str(output)),
    }


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 2
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error_code": _safe_error_code(exc)},
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
