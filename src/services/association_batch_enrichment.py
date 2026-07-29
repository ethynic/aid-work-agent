"""协会资料批量补全编排；外部能力通过可注入端口接入。"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Sequence
from urllib.parse import urlparse, urlunparse

from openpyxl import Workbook, load_workbook

from src.services.association_profile_extractor import PROFILE_FIELDS


AUDIT_FIELDS = (
    "association_name",
    "processing_status",
    "source_summary",
    "error_summary",
    "processed_at",
)
OUTPUT_FIELDS = ("association_name",) + PROFILE_FIELDS + AUDIT_FIELDS[1:]
ASSOCIATION_COLUMN_NAMES = {"协会名称", "association_name", "association", "单位名称"}
_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_FORMULA_PREFIXES = ("=", "+", "-", "@")

OfficialSiteResolver = Callable[[str], Awaitable[str | None]]
OfficialProfileCollector = Callable[
    [str, bool], Awaitable[Mapping[str, str | None]]
]
FallbackProfileProvider = Callable[
    [str], Awaitable[Mapping[str, str | None]]
]
WechatMobileProvider = Callable[[str, str, str], Awaitable[str | None]]


def _redact(text: str) -> str:
    return _MOBILE_RE.sub(lambda match: f"{match.group()[:3]}****{match.group()[-4:]}", text)


def _excel_safe_value(value: object) -> object:
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def _normalized_name(name: object) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()


def deduplicate_association_names(names: Sequence[object]) -> list[str]:
    """按去除全部空白后的协会名称去重，同时保留首次出现顺序。"""
    result: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        name = _normalized_name(raw_name)
        identity = re.sub(r"\s+", "", name).casefold()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(name)
    return result


def _find_association_column(headers: Sequence[object]) -> int:
    for index, header in enumerate(headers):
        if _normalized_name(header).casefold() in ASSOCIATION_COLUMN_NAMES:
            return index
    raise ValueError("INPUT_ASSOCIATION_COLUMN_NOT_FOUND")


def _read_csv_names(path: Path) -> list[str]:
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as stream:
                rows = list(csv.reader(stream))
            if not rows:
                return []
            column = _find_association_column(rows[0])
            return [
                row[column]
                for row in rows[1:]
                if column < len(row) and _normalized_name(row[column])
            ]
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError("INPUT_CSV_ENCODING_UNSUPPORTED") from last_error


def _read_xlsx_names(path: Path) -> list[str]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        names: list[str] = []
        found_column = False
        for worksheet in workbook.worksheets:
            rows = worksheet.iter_rows(values_only=True)
            headers = next(rows, None)
            if not headers:
                continue
            try:
                column = _find_association_column(headers)
            except ValueError:
                continue
            found_column = True
            names.extend(
                str(row[column])
                for row in rows
                if column < len(row) and _normalized_name(row[column])
            )
        if not found_column:
            raise ValueError("INPUT_ASSOCIATION_COLUMN_NOT_FOUND")
        return names
    finally:
        workbook.close()


def parse_association_input(
    *,
    text_values: Sequence[str] = (),
    input_path: str | Path | None = None,
) -> list[str]:
    names: list[object] = []
    for text in text_values:
        names.extend(re.split(r"[\r\n,，;；、]+", text))
    if input_path:
        path = Path(input_path)
        if not path.is_file():
            raise ValueError("INPUT_FILE_NOT_FOUND")
        if path.suffix.lower() == ".csv":
            names.extend(_read_csv_names(path))
        elif path.suffix.lower() == ".xlsx":
            names.extend(_read_xlsx_names(path))
        else:
            raise ValueError("INPUT_FILE_TYPE_UNSUPPORTED")
    result = deduplicate_association_names(names)
    if not result:
        raise ValueError("INPUT_ASSOCIATION_LIST_EMPTY")
    return result


def _scheme_candidates(url: str) -> list[str]:
    candidate = url.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("OFFICIAL_SITE_URL_INVALID")
    first = urlunparse(parsed)
    alternative_scheme = "http" if parsed.scheme == "https" else "https"
    second = urlunparse(parsed._replace(scheme=alternative_scheme))
    return [first, second] if first != second else [first]


@dataclass
class AssociationEnrichmentRow:
    association_name: str
    values: dict[str, str | None] = field(
        default_factory=lambda: {name: None for name in PROFILE_FIELDS}
    )
    processing_status: str = "pending"
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    processed_at: str = ""

    def as_excel_row(self) -> dict[str, str | None]:
        return {
            "association_name": self.association_name,
            **self.values,
            "processing_status": self.processing_status,
            "source_summary": "; ".join(dict.fromkeys(self.sources)),
            "error_summary": _redact("; ".join(self.errors)),
            "processed_at": self.processed_at,
        }


class AssociationBatchEnricher:
    def __init__(
        self,
        *,
        official_site_resolver: OfficialSiteResolver,
        official_profile_collector: OfficialProfileCollector,
        fallback_profile_provider: FallbackProfileProvider,
        wechat_mobile_provider: WechatMobileProvider,
        headless: bool = False,
    ):
        if headless is not False:
            raise ValueError("VISIBLE_BROWSER_REQUIRED")
        self._resolve_official_site = official_site_resolver
        self._collect_official_profile = official_profile_collector
        self._fallback_profile = fallback_profile_provider
        self._wechat_mobile = wechat_mobile_provider
        self._headless = headless

    async def enrich_one(self, association_name: str) -> AssociationEnrichmentRow:
        row = AssociationEnrichmentRow(association_name=association_name)
        official_succeeded = False
        try:
            official_url = await self._resolve_official_site(association_name)
        except Exception as exc:
            official_url = None
            row.errors.append(f"official_resolver:{type(exc).__name__}")

        if official_url:
            official_attempt_errors: list[str] = []
            for candidate in _scheme_candidates(official_url):
                try:
                    profile = await self._collect_official_profile(
                        candidate, self._headless
                    )
                    self._merge(row.values, profile)
                    row.sources.append(f"official:{candidate}")
                    official_succeeded = True
                    break
                except Exception as exc:
                    official_attempt_errors.append(
                        f"official_collect:{urlparse(candidate).scheme}:"
                        f"{type(exc).__name__}"
                    )
            if not official_succeeded:
                row.errors.extend(official_attempt_errors)
        else:
            row.errors.append("official_site:not_found")

        if not official_succeeded:
            try:
                fallback = await self._fallback_profile(association_name)
                self._merge(row.values, fallback)
                row.sources.append("web_search_fallback")
            except Exception as exc:
                row.errors.append(f"web_fallback:{type(exc).__name__}")

        for role, name_field, mobile_field in (
            ("会长", "president_name", "president_mobile"),
            ("秘书长", "secretary_general_name", "secretary_general_mobile"),
        ):
            person_name = row.values.get(name_field)
            if not person_name or row.values.get(mobile_field):
                continue
            try:
                mobile = await self._wechat_mobile(
                    association_name, str(person_name), role
                )
                if mobile:
                    if not _MOBILE_RE.fullmatch(str(mobile)):
                        row.errors.append(f"wechat:{role}:invalid_mobile")
                    else:
                        row.values[mobile_field] = str(mobile)
                        row.sources.append(f"wechat:{role}")
            except Exception as exc:
                row.errors.append(f"wechat:{role}:{type(exc).__name__}")

        populated = sum(bool(row.values.get(name)) for name in PROFILE_FIELDS)
        row.processing_status = (
            "complete" if populated and not row.errors else
            "partial" if populated else
            "failed"
        )
        row.processed_at = datetime.now(timezone.utc).isoformat()
        return row

    async def enrich_many(
        self, association_names: Sequence[str]
    ) -> list[AssociationEnrichmentRow]:
        rows = []
        for association_name in deduplicate_association_names(association_names):
            try:
                rows.append(await self.enrich_one(association_name))
            except Exception as exc:
                rows.append(
                    AssociationEnrichmentRow(
                        association_name=association_name,
                        processing_status="failed",
                        errors=[f"unexpected:{type(exc).__name__}"],
                        processed_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
        return rows

    @staticmethod
    def _merge(
        target: dict[str, str | None],
        incoming: Mapping[str, str | None],
    ) -> None:
        for field_name in PROFILE_FIELDS:
            if not target.get(field_name) and incoming.get(field_name):
                target[field_name] = str(incoming[field_name]).strip()


def write_enrichment_workbook(
    rows: Sequence[AssociationEnrichmentRow], output_path: str | Path
) -> Path:
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "协会信息"
    worksheet.append(list(OUTPUT_FIELDS))
    for item in rows:
        row = item.as_excel_row()
        worksheet.append([_excel_safe_value(row.get(name)) for name in OUTPUT_FIELDS])
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    for column_cells in worksheet.columns:
        width = min(
            60,
            max(12, max(len(str(cell.value or "")) for cell in column_cells) + 2),
        )
        worksheet.column_dimensions[column_cells[0].column_letter].width = width
    workbook.save(path)
    return path
