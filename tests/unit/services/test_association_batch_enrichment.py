import csv
import asyncio

import pytest
from openpyxl import Workbook, load_workbook

from src.services.association_batch_enrichment import (
    OUTPUT_FIELDS,
    AssociationBatchEnricher,
    parse_association_input,
    write_enrichment_workbook,
)
from src.services.association_enrichment_providers import ProjectAssociationProviders
from src.services.association_profile_extractor import PROFILE_FIELDS


pytestmark = pytest.mark.unit


def test_text_csv_and_excel_inputs_produce_one_ordered_deduplicated_list(tmp_path):
    csv_path = tmp_path / "input.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["协会名称", "备注"])
        writer.writerow(["中国日用玻璃协会", "重复"])
        writer.writerow(["中国缝制机械协会", "新增"])

    assert parse_association_input(
        text_values=["中国日用玻璃协会，中国  日用玻璃协会"],
        input_path=csv_path,
    ) == ["中国日用玻璃协会", "中国缝制机械协会"]

    xlsx_path = tmp_path / "input.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["单位名称"])
    worksheet.append(["中国物资再生协会"])
    workbook.save(xlsx_path)
    assert parse_association_input(input_path=xlsx_path) == ["中国物资再生协会"]


def test_input_without_association_column_fails_loud(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("姓名\n张三\n", encoding="utf-8")
    with pytest.raises(ValueError, match="INPUT_ASSOCIATION_COLUMN_NOT_FOUND"):
        parse_association_input(input_path=path)


def test_empty_and_corrupt_input_files_fail_loud(tmp_path):
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="INPUT_ASSOCIATION_LIST_EMPTY"):
        parse_association_input(input_path=empty_csv)

    corrupt_xlsx = tmp_path / "corrupt.xlsx"
    corrupt_xlsx.write_bytes(b"not an xlsx archive")
    with pytest.raises(Exception):
        parse_association_input(input_path=corrupt_xlsx)


@pytest.mark.asyncio
async def test_batch_falls_back_from_https_to_http_and_then_enriches_wechat():
    attempts = []

    async def resolve(_name):
        return "https://legacy.example.org/"

    async def collect(url, headless):
        attempts.append((url, headless))
        if url.startswith("https://"):
            raise ConnectionError("old site")
        return {
            "secretary_general_name": "王秘书",
            "official_website": url,
        }

    async def fallback(_name):
        raise AssertionError("official HTTP fallback succeeded")

    async def wechat(association, person, role):
        assert (association, person, role) == ("测试协会", "王秘书", "秘书长")
        return "18612345678"

    enricher = AssociationBatchEnricher(
        official_site_resolver=resolve,
        official_profile_collector=collect,
        fallback_profile_provider=fallback,
        wechat_mobile_provider=wechat,
        headless=False,
    )
    row = await enricher.enrich_one("测试协会")

    assert attempts == [
        ("https://legacy.example.org/", False),
        ("http://legacy.example.org/", False),
    ]
    assert row.values["secretary_general_mobile"] == "18612345678"
    assert row.processing_status == "complete"


@pytest.mark.asyncio
async def test_one_failed_association_does_not_stop_later_associations():
    async def resolve(name):
        if name == "坏协会":
            raise RuntimeError("resolver down")
        return None

    async def collect(_url, _headless):
        raise AssertionError("no official URL")

    async def fallback(name):
        if name == "坏协会":
            raise RuntimeError("search down")
        return {"address": "北京市"}

    async def wechat(_association, _person, _role):
        return None

    enricher = AssociationBatchEnricher(
        official_site_resolver=resolve,
        official_profile_collector=collect,
        fallback_profile_provider=fallback,
        wechat_mobile_provider=wechat,
    )
    rows = await enricher.enrich_many(["坏协会", "好协会"])

    assert [row.association_name for row in rows] == ["坏协会", "好协会"]
    assert rows[0].processing_status == "failed"
    assert rows[1].values["address"] == "北京市"


def test_excel_contains_business_and_audit_columns_but_error_is_redacted(tmp_path):
    from src.services.association_batch_enrichment import AssociationEnrichmentRow

    row = AssociationEnrichmentRow(
        association_name="测试协会",
        processing_status="partial",
        errors=["provider returned 18612345678"],
        processed_at="2026-07-29T00:00:00+00:00",
    )
    row.values["secretary_general_mobile"] = "18612345678"
    path = write_enrichment_workbook([row], tmp_path / "result.xlsx")
    workbook = load_workbook(path, data_only=True)
    try:
        worksheet = workbook.active
        headers = [cell.value for cell in worksheet[1]]
        values = dict(zip(headers, [cell.value for cell in worksheet[2]]))
    finally:
        workbook.close()

    assert tuple(headers) == OUTPUT_FIELDS
    assert values["secretary_general_mobile"] == "18612345678"
    assert values["error_summary"] == "provider returned 186****5678"


def test_excel_treats_external_formula_prefixes_as_text(tmp_path):
    from src.services.association_batch_enrichment import AssociationEnrichmentRow

    row = AssociationEnrichmentRow(
        association_name="=HYPERLINK(\"https://evil.invalid\")",
        processing_status="partial",
        sources=["+cmd"],
        errors=["@payload"],
        processed_at="-1",
    )
    row.values["address"] = "=1+1"
    path = write_enrichment_workbook([row], tmp_path / "safe.xlsx")
    workbook = load_workbook(path, data_only=False)
    try:
        worksheet = workbook.active
        headers = [cell.value for cell in worksheet[1]]
        cells = dict(zip(headers, [cell.value for cell in worksheet[2]]))
        types = dict(zip(headers, [cell.data_type for cell in worksheet[2]]))
    finally:
        workbook.close()

    for field_name in (
        "association_name",
        "address",
        "source_summary",
        "error_summary",
        "processed_at",
    ):
        assert cells[field_name].startswith("'")
        assert types[field_name] == "s"


def test_web_fallback_only_accepts_values_with_exact_search_evidence():
    parsed = {
        name: {"value": None, "evidence_quote": None, "source_url": None}
        for name in PROFILE_FIELDS
    }
    parsed["secretary_general_name"] = {
        "value": "潘华",
        "evidence_quote": "秘书长：潘  华",
        "source_url": "https://example.org/profile",
    }
    result = ProjectAssociationProviders._validate_fallback_evidence(
        parsed,
        [{
            "url": "https://example.org/profile",
            "title": "协会简介",
            "content": "第九届理事会秘书长：潘  华",
        }],
    )
    assert result["secretary_general_name"] == "潘华"

    parsed["secretary_general_name"]["value"] = "虚构姓名"
    with pytest.raises(ValueError, match="WEB_FALLBACK_EVIDENCE_INVALID"):
        ProjectAssociationProviders._validate_fallback_evidence(
            parsed,
            [{
                "url": "https://example.org/profile",
                "title": "协会简介",
                "content": "第九届理事会秘书长：潘  华",
            }],
        )


@pytest.mark.asyncio
async def test_timed_out_child_process_is_killed_and_reaped():
    class HangingProcess:
        def __init__(self):
            self.killed = False
            self.waited = False

        async def communicate(self):
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True

    process = HangingProcess()
    with pytest.raises(RuntimeError, match="CHILD_TIMEOUT"):
        await ProjectAssociationProviders._communicate_with_timeout(
            process,
            timeout_seconds=0.001,
            error_code="CHILD_TIMEOUT",
        )
    assert process.killed is True
    assert process.waited is True
