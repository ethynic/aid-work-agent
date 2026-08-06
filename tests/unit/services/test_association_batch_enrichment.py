import csv
import asyncio
import json

import pytest
from openpyxl import Workbook, load_workbook

from src.services.association_batch_enrichment import (
    AssociationBatchEnricher,
    parse_association_input,
    write_enrichment_workbook,
)
from src.services.association_enrichment_providers import ProjectAssociationProviders
from src.services.association_profile_extractor import PROFILE_FIELDS


pytestmark = pytest.mark.unit


class _SearchStub:
    def __init__(self, responses):
        self.responses = list(responses)
        self.keywords = []

    async def execute(self, *, keyword, **_kwargs):
        self.keywords.append(keyword)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _GatewayStub:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = 0

    async def chat(self, **_kwargs):
        self.calls += 1
        return {"content": self.contents.pop(0)}


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
    assert row.processing_status == "partial"
    assert "profile:president_not_found" in row.errors


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


@pytest.mark.asyncio
async def test_cleanup_failure_keeps_verified_mobile_and_stops_batch():
    from src.services.association_batch_enrichment import WechatRpaError

    calls = []

    async def resolve(_name):
        return "https://association.example.cn/"

    async def collect(_url, _headless):
        return {
            "president_name": "杨晓京",
            "secretary_general_name": "陈戟",
        }

    async def fallback(_name):
        raise AssertionError("official collection succeeded")

    async def wechat(_association, person, _role):
        calls.append(person)
        if person == "杨晓京":
            raise WechatRpaError(
                "PLUGIN_CLOSE_TIMEOUT",
                stage="cleanup",
                session_fatal=True,
                recovered_mobile="18512345678",
            )
        return "18612345678"

    enricher = AssociationBatchEnricher(
        official_site_resolver=resolve,
        official_profile_collector=collect,
        fallback_profile_provider=fallback,
        wechat_mobile_provider=wechat,
    )

    rows = await enricher.enrich_many(["测试协会", "不得继续协会"])

    assert calls == ["杨晓京"]
    assert rows.aborted is True
    assert rows.abort_error_code == "PLUGIN_CLOSE_TIMEOUT"
    assert [row.association_name for row in rows] == ["测试协会"]
    assert rows[0].values["president_mobile"] == "18512345678"
    assert "wechat:会长" in rows[0].sources
    assert any(
        error == "wechat:会长:PLUGIN_CLOSE_TIMEOUT:cleanup"
        for error in rows[0].errors
    )


@pytest.mark.asyncio
async def test_nonfatal_wechat_failure_allows_next_person():
    calls = []

    async def resolve(_name):
        return "https://example.cn"

    async def collect(_url, _headless):
        return {
            "president_name": "会长甲",
            "secretary_general_name": "秘书长乙",
        }

    async def fallback(_name):
        return {}

    async def wechat(_association, person, _role):
        calls.append(person)
        if person == "会长甲":
            raise RuntimeError("CARD_LOCATE_FAILED")
        return None

    enricher = AssociationBatchEnricher(
        official_site_resolver=resolve,
        official_profile_collector=collect,
        fallback_profile_provider=fallback,
        wechat_mobile_provider=wechat,
    )

    await enricher.enrich_one("测试协会")

    assert calls == ["会长甲", "秘书长乙"]


@pytest.mark.asyncio
async def test_last_association_session_failure_is_explicitly_aborted():
    from src.services.association_batch_enrichment import WechatRpaError

    async def resolve(_name):
        return "https://example.cn"

    async def collect(_url, _headless):
        return {"president_name": "会长甲"}

    async def fallback(_name):
        return {}

    async def wechat(association, _person, _role):
        if association == "最后协会":
            raise WechatRpaError(
                "SESSION_CLEANUP_FAILED",
                stage="cleanup",
                session_fatal=True,
            )
        return None

    enricher = AssociationBatchEnricher(
        official_site_resolver=resolve,
        official_profile_collector=collect,
        fallback_profile_provider=fallback,
        wechat_mobile_provider=wechat,
    )

    rows = await enricher.enrich_many(["正常协会", "最后协会"])

    assert len(rows) == 2
    assert rows.aborted is True
    assert rows.abort_error_code == "SESSION_CLEANUP_FAILED"


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

    # 表头按客户参考表输出中文标签，额外职务占位列已包含在内。
    from src.services.association_batch_enrichment import _EXCEL_HEADERS
    assert headers == [label for label, _ in _EXCEL_HEADERS]
    assert values["秘书长\n手机"] == "18612345678"
    assert values["错误摘要"] == "provider returned 186****5678"
    # 未采集的职务占位列存在且为空
    assert "副秘书长\n姓名" in values
    assert values["副秘书长\n姓名"] is None


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

    for header_name in (
        "客户名称",
        "单位地址",
        "来源摘要",
        "错误摘要",
        "处理时间",
    ):
        assert cells[header_name].startswith("'")
        assert types[header_name] == "s"


def test_web_fallback_accepts_model_values_without_verbatim_quote_check():
    """删除逐字校验后，fallback 路径只做 schema/类型/手机号禁止校验；
    模型给出的 value 一律接受，不再要求 value 逐字出现在 quote 中。"""
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
    rejected = []
    result = ProjectAssociationProviders._validate_fallback_evidence(
        parsed,
        [{
            "url": "https://example.org/profile",
            "title": "协会简介",
            "content": "第九届理事会秘书长：潘  华",
        }],
        rejected,
    )
    assert result["secretary_general_name"] == "虚构姓名"
    assert rejected == []


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


@pytest.mark.asyncio
async def test_timed_out_child_preserves_code_when_it_exits_before_kill():
    class ExitedProcess:
        def __init__(self):
            self.waited = False

        async def communicate(self):
            await asyncio.Event().wait()

        def kill(self):
            raise ProcessLookupError

        async def wait(self):
            self.waited = True

    process = ExitedProcess()
    with pytest.raises(RuntimeError, match="WECHAT_RPA_TIMEOUT"):
        await ProjectAssociationProviders._communicate_with_timeout(
            process,
            timeout_seconds=0.001,
            error_code="WECHAT_RPA_TIMEOUT",
        )
    assert process.waited is True


@pytest.mark.asyncio
async def test_wechat_audit_failure_does_not_discard_found_mobile(
    monkeypatch, tmp_path
):
    import src.services.association_enrichment_providers as module

    class CompletedProcess:
        def __init__(self, stdout):
            self.returncode = 0
            self._stdout = stdout

        async def communicate(self):
            return self._stdout, b""

    processes = iter(
        [
            CompletedProcess(
                json.dumps(
                    {
                        "ok": True,
                        "session_closed": True,
                        "status": "found",
                        "artifact_ref": "artifact.dpapi",
                    }
                ).encode("utf-8")
            ),
            CompletedProcess(
                json.dumps(
                    {"matched": True, "mobile": "13912345678"}
                ).encode("utf-8")
            ),
        ]
    )

    async def fake_subprocess(*_args, **_kwargs):
        return next(processes)

    providers = ProjectAssociationProviders(repository_root=tmp_path)

    async def broken_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(providers, "_audit_wechat_artifact", broken_audit)
    monkeypatch.setattr(
        providers,
        "_validated_wechat_artifact_path",
        lambda _ref: tmp_path / "artifact.dpapi",
    )

    assert (
        await providers.wechat_mobile("测试协会", "张三", "会长")
        == "13912345678"
    )
    assert providers._wechat_handoff_ready is True


@pytest.mark.asyncio
async def test_wechat_inter_query_handoff_runs_once_only_after_confirmed_close(
    monkeypatch, tmp_path
):
    providers = ProjectAssociationProviders(repository_root=tmp_path)
    events = []

    monkeypatch.setattr(
        providers, "_send_alt_tab_once", lambda: events.append("alt_tab")
    )

    async def fake_sleep(seconds):
        events.append(("sleep", seconds))

    providers._wechat_handoff_sleep = fake_sleep

    assert await providers._prepare_wechat_query_handoff() is False
    assert events == []

    providers._wechat_handoff_ready = True
    assert await providers._prepare_wechat_query_handoff() is True
    assert events == ["alt_tab", ("sleep", 1.0)]

    assert await providers._prepare_wechat_query_handoff() is False
    assert events == ["alt_tab", ("sleep", 1.0)]


@pytest.mark.asyncio
async def test_wechat_inter_query_handoff_failure_consumes_authorization(
    monkeypatch, tmp_path
):
    providers = ProjectAssociationProviders(repository_root=tmp_path)
    providers._wechat_handoff_ready = True

    def fail_alt_tab():
        raise RuntimeError("injection failed")

    monkeypatch.setattr(providers, "_send_alt_tab_once", fail_alt_tab)
    with pytest.raises(RuntimeError, match="injection failed"):
        await providers._prepare_wechat_query_handoff()

    assert providers._wechat_handoff_ready is False


def test_wechat_artifact_ref_is_scoped_to_local_uuid_directory(
    monkeypatch, tmp_path
):
    artifact_root = (
        tmp_path / "AidWorkAgent" / "wechat-souyisou-rpa" / "artifacts"
    )
    artifact_root.mkdir(parents=True)
    valid = artifact_root / f"{'a' * 32}.dpapi"
    valid.write_bytes(b"encrypted")
    outside = tmp_path / f"{'b' * 32}.dpapi"
    outside.write_bytes(b"encrypted")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert (
        ProjectAssociationProviders._validated_wechat_artifact_path(str(valid))
        == valid.resolve()
    )
    with pytest.raises(ValueError, match="WECHAT_ARTIFACT_REF_INVALID"):
        ProjectAssociationProviders._validated_wechat_artifact_path(str(outside))
    with pytest.raises(ValueError, match="WECHAT_ARTIFACT_REF_INVALID"):
        ProjectAssociationProviders._validated_wechat_artifact_path(
            str(artifact_root / ".." / valid.name)
        )


@pytest.mark.asyncio
async def test_official_site_resolver_combines_queries_when_first_has_no_result(
    monkeypatch, tmp_path
):
    import src.services.association_enrichment_providers as module

    providers = ProjectAssociationProviders(repository_root=tmp_path)
    providers._search = _SearchStub([
        {"success": True, "results": []},
        {
            "success": True,
            "results": [{
                "url": "https://association.example.cn/",
                "title": "测试协会官网",
                "content": "测试协会官方网站",
            }],
        },
        {"success": True, "results": []},
    ])
    gateway = _GatewayStub([
        json.dumps({"official_url": "https://association.example.cn/"})
    ])
    monkeypatch.setattr(module, "llm_gateway", gateway)

    assert (
        await providers.resolve_official_site("测试协会")
        == "https://association.example.cn/"
    )
    assert providers._search.keywords == [
        "测试协会",
        "测试协会",
        "测试协会 官网",
        "测试协会 官方网站",
    ]


@pytest.mark.asyncio
async def test_official_site_resolver_accepts_normalized_candidate_but_returns_original(
    monkeypatch, tmp_path
):
    import src.services.association_enrichment_providers as module

    providers = ProjectAssociationProviders(repository_root=tmp_path)
    original = "HTTPS://Association.Example.CN:443/about/"
    providers._search = _SearchStub([
        {
            "success": True,
            "results": [{"url": original, "title": "官网", "content": "官方"}],
        },
        {"success": True, "results": []},
        {"success": True, "results": []},
    ])
    monkeypatch.setattr(
        module,
        "llm_gateway",
        _GatewayStub(['{"official_url":"https://association.example.cn/about"}']),
    )

    assert await providers.resolve_official_site("测试协会") == original


@pytest.mark.asyncio
async def test_website_only_official_profile_uses_fallback_then_wechat():
    async def resolve(_name):
        return "https://association.example.cn/"

    async def collect(url, _headless):
        return {"official_website": url}

    async def fallback(_name):
        return {"secretary_general_name": "潘华"}

    async def wechat(association, person, role):
        assert (association, person, role) == ("测试协会", "潘华", "秘书长")
        return "18612345678"

    enricher = AssociationBatchEnricher(
        official_site_resolver=resolve,
        official_profile_collector=collect,
        fallback_profile_provider=fallback,
        wechat_mobile_provider=wechat,
    )

    row = await enricher.enrich_one("测试协会")

    assert row.values["official_website"] == "https://association.example.cn/"
    assert row.values["secretary_general_name"] == "潘华"
    assert row.values["secretary_general_mobile"] == "18612345678"
    assert "web_search_fallback" in row.sources


@pytest.mark.asyncio
async def test_official_site_resolver_ignores_one_failed_query_and_retries_bad_json(
    monkeypatch, tmp_path
):
    import src.services.association_enrichment_providers as module

    providers = ProjectAssociationProviders(repository_root=tmp_path)
    providers._search = _SearchStub([
        RuntimeError("temporary search failure"),
        {
            "success": True,
            "results": [{
                "url": "http://association.example.cn/",
                "title": "测试协会",
                "content": "官方网站",
            }],
        },
        {"success": True, "results": []},
    ])
    gateway = _GatewayStub([
        "not json",
        '{"official_url":"http://association.example.cn/"}',
    ])
    monkeypatch.setattr(module, "llm_gateway", gateway)

    assert (
        await providers.resolve_official_site("测试协会")
        == "http://association.example.cn/"
    )
    assert gateway.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selected_url",
    [
        "ftp://association.example.cn/",
        "https://hallucinated.example.cn/",
    ],
)
async def test_official_site_resolver_rejects_non_http_or_non_candidate_url(
    monkeypatch, tmp_path, selected_url
):
    import src.services.association_enrichment_providers as module

    providers = ProjectAssociationProviders(repository_root=tmp_path)
    search_result = {
        "success": True,
        "results": [
            {
                "url": "https://association.example.cn/",
                "title": "测试协会官网",
                "content": "测试协会官方网站",
            },
            {
                "url": "ftp://association.example.cn/",
                "title": "下载站",
                "content": "非网页协议",
            },
        ],
    }
    providers._search = _SearchStub(
        [search_result, {"success": True, "results": []}, {"success": True, "results": []}]
    )
    monkeypatch.setattr(
        module,
        "llm_gateway",
        _GatewayStub([json.dumps({"official_url": selected_url})]),
    )

    with pytest.raises(ValueError, match="OFFICIAL_RESOLVER_INVALID_RESPONSE"):
        await providers.resolve_official_site("测试协会")


@pytest.mark.asyncio
async def test_fallback_retries_invalid_structure(monkeypatch, tmp_path):
    import src.services.association_enrichment_providers as module

    providers = ProjectAssociationProviders(repository_root=tmp_path)
    providers._search = _SearchStub([{
        "success": True,
        "results": [{
            "url": "https://news.example.cn/a",
            "title": "测试协会简介",
            "content": "地址：北京市测试路1号",
        }],
    }])
    valid = {
        name: {"value": None, "evidence_quote": None, "source_url": None}
        for name in PROFILE_FIELDS
    }
    valid["address"] = {
        "value": "北京市测试路1号",
        "evidence_quote": "地址：北京市测试路1号",
        "source_url": "https://news.example.cn/a",
    }
    gateway = _GatewayStub([
        '{"unexpected":"shape"}',
        json.dumps(valid, ensure_ascii=False),
    ])
    monkeypatch.setattr(module, "llm_gateway", gateway)

    result = await providers.fallback_profile("测试协会")

    assert result["address"] == "北京市测试路1号"
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_progress_reports_stages_and_redacts_mobile():
    messages = []

    async def resolve(_name):
        return "https://association.example.cn/"

    async def collect(_url, _headless):
        return {"president_name": "张三"}

    async def fallback(_name):
        raise AssertionError("official collection succeeded")

    async def wechat(_association, _person, _role):
        return "18612345678"

    enricher = AssociationBatchEnricher(
        official_site_resolver=resolve,
        official_profile_collector=collect,
        fallback_profile_provider=fallback,
        wechat_mobile_provider=wechat,
        progress_reporter=messages.append,
    )
    row = await enricher.enrich_one("测试协会 18612345678")

    assert row.values["president_mobile"] == "18612345678"
    assert any("发现官网" in message for message in messages)
    assert any("可见浏览器" in message for message in messages)
    assert any("微信检索" in message for message in messages)
    assert all("18612345678" not in message for message in messages)
    assert any("186****5678" in message for message in messages)


@pytest.mark.asyncio
async def test_progress_reporter_failure_does_not_interrupt_enrichment():
    async def resolve(_name):
        return None

    async def fallback(_name):
        return {"address": "北京市"}

    async def wechat(_association, _person, _role):
        return None

    def broken_reporter(_message):
        raise RuntimeError("terminal closed")

    enricher = AssociationBatchEnricher(
        official_site_resolver=resolve,
        official_profile_collector=lambda *_args: None,
        fallback_profile_provider=fallback,
        wechat_mobile_provider=wechat,
        progress_reporter=broken_reporter,
    )

    row = await enricher.enrich_one("测试协会")

    assert row.values["address"] == "北京市"
    assert row.processing_status == "partial"


@pytest.mark.asyncio
async def test_official_profile_passes_hostname_as_verified_domain(
    monkeypatch, tmp_path
):
    import src.services.association_enrichment_providers as module
    from src.services.association_profile_extractor import (
        AssociationProfile,
        ExtractionResult,
        FieldEvidence,
    )

    providers = ProjectAssociationProviders(repository_root=tmp_path)
    captured = {}

    async def collect(entry_url, domain, *, headless, **budgets):
        captured["collector"] = (entry_url, domain, headless, budgets)
        return []

    async def extract(pages, verified_domain):
        captured["extractor"] = (pages, verified_domain)
        return ExtractionResult(
            status="success",
            profile=AssociationProfile(
                **{name: FieldEvidence() for name in PROFILE_FIELDS}
            ),
        )

    monkeypatch.setattr(module, "collect_official_pages_with_playwright", collect)
    monkeypatch.setattr(module, "extract_association_profile", extract)

    await providers.collect_official_profile(
        "https://www.example.cn/about/index.html", False
    )

    assert captured["collector"][1] == "www.example.cn"
    assert captured["extractor"][1] == "www.example.cn"


@pytest.mark.asyncio
async def test_official_profile_retries_invalid_llm_evidence_once(
    monkeypatch, tmp_path
):
    import src.services.association_enrichment_providers as module
    from src.services.association_profile_extractor import (
        AssociationProfile,
        ExtractionResult,
        FieldEvidence,
    )

    providers = ProjectAssociationProviders(repository_root=tmp_path)
    calls = []

    async def collect(_entry_url, _domain, *, headless, **_budgets):
        assert headless is False
        return []

    async def extract(pages, verified_domain):
        calls.append((pages, verified_domain))
        if len(calls) == 1:
            return ExtractionResult(
                status="inconclusive",
                reason_code="INVALID_EVIDENCE",
            )
        return ExtractionResult(
            status="success",
            profile=AssociationProfile(
                **{name: FieldEvidence() for name in PROFILE_FIELDS}
            ),
        )

    monkeypatch.setattr(module, "collect_official_pages_with_playwright", collect)
    monkeypatch.setattr(module, "extract_association_profile", extract)

    result = await providers.collect_official_profile(
        "https://www.example.cn/about/index.html", False
    )

    assert len(calls) == 2
    assert calls[0][1] == calls[1][1] == "www.example.cn"
    assert set(result) == set(PROFILE_FIELDS)


@pytest.mark.asyncio
async def test_official_main_extraction_failure_still_runs_leadership_fallback(
    monkeypatch, tmp_path
):
    import src.services.association_enrichment_providers as module
    from src.services.association_profile_extractor import (
        ExtractionResult,
        VerifiedOfficialPage,
    )

    providers = ProjectAssociationProviders(repository_root=tmp_path)
    leadership_page = VerifiedOfficialPage(
        url="https://example.cn/leaders",
        title="领导机构",
        content="现任会长张会长",
        verified_official=True,
    )

    async def collect(*_args, **_kwargs):
        return [leadership_page]

    async def extract(*_args, **_kwargs):
        return ExtractionResult(
            status="inconclusive",
            reason_code="STRICT_JSON_INVALID",
        )

    async def focused(pages, domain):
        assert pages == [leadership_page]
        assert domain == "example.cn"
        return {"president_name": "张会长", "secretary_general_name": None}

    monkeypatch.setattr(module, "collect_official_pages_with_playwright", collect)
    monkeypatch.setattr(module, "extract_association_profile", extract)
    monkeypatch.setattr(providers, "_extract_leadership", focused)

    result = await providers.collect_official_profile("https://example.cn", False)

    assert result["president_name"] == "张会长"
    assert set(result) == set(PROFILE_FIELDS)


@pytest.mark.asyncio
async def test_wechat_nonzero_exit_audits_failure_and_available_artifact(
    monkeypatch, tmp_path
):
    import src.services.association_enrichment_providers as module

    class FailedProcess:
        returncode = 1

        async def communicate(self):
            return (
                json.dumps(
                    {
                        "ok": False,
                        "session_closed": False,
                        "status": "failed",
                        "artifact_ref": "failure.dpapi",
                    }
                ).encode("utf-8"),
                b"",
            )

        def kill(self):
            pass

        async def wait(self):
            pass

    async def fake_subprocess(*_args, **_kwargs):
        return FailedProcess()

    events = []
    providers = ProjectAssociationProviders(
        repository_root=tmp_path,
        audit_callback=lambda **event: events.append(event),
    )
    artifact_reads = []

    async def fake_audit_artifact(ref, **_kwargs):
        artifact_reads.append(ref)

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(providers, "_audit_wechat_artifact", fake_audit_artifact)

    with pytest.raises(RuntimeError, match="WECHAT_RPA_FAILED"):
        await providers.wechat_mobile("测试协会", "张三", "会长")

    assert artifact_reads == ["failure.dpapi"]
    assert [event["kind"] for event in events] == [
        "wechat_start",
        "wechat_failed",
    ]


def test_workbook_token_usage_uses_separate_sheet_without_changing_main_contract(tmp_path):
    from src.services.association_batch_enrichment import AssociationEnrichmentRow

    path = write_enrichment_workbook(
        [AssociationEnrichmentRow(association_name="协会一")],
        tmp_path / "tokens.xlsx",
        token_usage={
            "associations": {
                "协会一": {
                    "input_tokens": 100,
                    "cached_input_tokens": 40,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "call_count": 1,
                }
            },
            "batch_overhead": {
                "input_tokens": 10,
                "cached_input_tokens": 2,
                "output_tokens": 3,
                "total_tokens": 13,
                "call_count": 1,
            },
            "batch": {
                "input_tokens": 110,
                "cached_input_tokens": 42,
                "output_tokens": 23,
                "total_tokens": 133,
                "call_count": 2,
                "average_input_tokens": 110,
                "average_cached_input_tokens": 42,
                "average_output_tokens": 23,
                "average_total_tokens": 133,
            },
        },
    )
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        from src.services.association_batch_enrichment import _EXCEL_HEADERS
        assert workbook["协会信息"].max_column == len(_EXCEL_HEADERS)
        token_sheet = workbook["Token用量"]
        assert token_sheet.cell(2, 1).value == "协会一"
        assert token_sheet.cell(2, 2).value == 100
        assert token_sheet.cell(2, 3).value == 40
        assert token_sheet.cell(3, 1).value == "批次开销（清单解析）"
        assert token_sheet.cell(3, 5).value == 13
        assert token_sheet.cell(7, 1).value == "cached_input_tokens"
    finally:
        workbook.close()


@pytest.mark.asyncio
async def test_wechat_session_cleanup_failure_keeps_original_error_after_audit(
    monkeypatch, tmp_path
):
    import src.services.association_enrichment_providers as module

    class CompletedProcess:
        returncode = 1

        async def communicate(self):
            return (
                json.dumps(
                    {
                        "ok": False,
                        "session_closed": False,
                        "status": "failed",
                        "error_code": "PLUGIN_CLOSE_TIMEOUT",
                        "stage": "cleanup",
                        "result_status": "found",
                        "artifact_ref": "cleanup.dpapi",
                    }
                ).encode("utf-8"),
                "可能包含手机号 18512345678".encode("utf-8"),
            )

    async def fake_subprocess(*_args, **_kwargs):
        return CompletedProcess()

    events = []
    providers = ProjectAssociationProviders(
        repository_root=tmp_path,
        audit_callback=lambda **event: events.append(event),
    )

    async def broken_audit_artifact(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(providers, "_audit_wechat_artifact", broken_audit_artifact)
    async def recovered_mobile(*_args, **_kwargs):
        return "18512345678"

    monkeypatch.setattr(providers, "_extract_wechat_mobile", recovered_mobile)

    with pytest.raises(RuntimeError, match="PLUGIN_CLOSE_TIMEOUT") as caught:
        await providers.wechat_mobile("测试协会", "张三", "会长")

    assert caught.value.stage == "cleanup"
    assert caught.value.session_fatal is True
    assert caught.value.recovered_mobile == "18512345678"

    assert [event["kind"] for event in events] == [
        "wechat_start",
        "wechat_audit_failed",
        "wechat_failed",
    ]
    assert events[-1]["summary"] == "PLUGIN_CLOSE_TIMEOUT"
    assert events[-1]["detail"]["stderr_bytes"] > 0
    assert len(events[-1]["detail"]["stderr_sha256"]) == 64
    assert "18512345678" not in json.dumps(events[-1], ensure_ascii=False)


@pytest.mark.asyncio
async def test_wechat_unsubmitted_input_failure_is_nonfatal_after_clean_close(
    monkeypatch, tmp_path
):
    import src.services.association_enrichment_providers as module

    class CompletedProcess:
        returncode = 1

        async def communicate(self):
            return (
                json.dumps(
                    {
                        "ok": False,
                        "error_code": "SEARCH_INPUT_READBACK_MISMATCH",
                        "stage": "input_verify",
                        "session_closed": True,
                    }
                ).encode("utf-8"),
                b"",
            )

    async def fake_subprocess(*_args, **_kwargs):
        return CompletedProcess()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_subprocess)
    providers = ProjectAssociationProviders(repository_root=tmp_path)

    with pytest.raises(RuntimeError, match="SEARCH_INPUT_READBACK_MISMATCH") as caught:
        await providers.wechat_mobile("测试协会", "张三", "会长")

    assert caught.value.stage == "input_verify"
    assert caught.value.session_fatal is False
    assert caught.value.recovered_mobile is None


@pytest.mark.asyncio
async def test_wechat_input_failure_is_fatal_when_cleanup_is_not_confirmed(
    monkeypatch, tmp_path
):
    import src.services.association_enrichment_providers as module

    class CompletedProcess:
        returncode = 1

        async def communicate(self):
            return (
                json.dumps(
                    {
                        "ok": False,
                        "error_code": "SEARCH_INPUT_LOCATOR_FAILED",
                        "stage": "input_verify",
                        "session_closed": False,
                    }
                ).encode("utf-8"),
                b"",
            )

    async def fake_subprocess(*_args, **_kwargs):
        return CompletedProcess()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_subprocess)
    providers = ProjectAssociationProviders(repository_root=tmp_path)

    with pytest.raises(RuntimeError, match="SEARCH_INPUT_LOCATOR_FAILED") as caught:
        await providers.wechat_mobile("测试协会", "张三", "会长")

    assert caught.value.session_fatal is True


@pytest.mark.asyncio
async def test_wechat_timeout_audits_stable_code_and_reraises(monkeypatch, tmp_path):
    import src.services.association_enrichment_providers as module

    class HangingProcess:
        returncode = None

    async def fake_subprocess(*_args, **_kwargs):
        return HangingProcess()

    timeout_calls = []

    async def fake_communicate(*_args, **kwargs):
        timeout_calls.append(kwargs)
        raise RuntimeError("WECHAT_RPA_TIMEOUT")

    events = []
    providers = ProjectAssociationProviders(
        repository_root=tmp_path,
        audit_callback=lambda **event: events.append(event),
    )
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(
        providers,
        "_communicate_with_timeout",
        fake_communicate,
    )

    with pytest.raises(RuntimeError, match="WECHAT_RPA_TIMEOUT"):
        await providers.wechat_mobile("测试协会", "张三", "会长")

    assert [event["kind"] for event in events] == [
        "wechat_start",
        "wechat_failed",
    ]
    assert events[-1]["summary"] == "WECHAT_RPA_TIMEOUT"
    assert timeout_calls == [{
        "timeout_seconds": 600,
        "error_code": "WECHAT_RPA_TIMEOUT",
    }]


@pytest.mark.asyncio
async def test_wechat_invalid_json_audits_response_error(monkeypatch, tmp_path):
    import src.services.association_enrichment_providers as module

    class CompletedProcess:
        returncode = 0

        async def communicate(self):
            return b"not-json", b""

    async def fake_subprocess(*_args, **_kwargs):
        return CompletedProcess()

    events = []
    providers = ProjectAssociationProviders(
        repository_root=tmp_path,
        audit_callback=lambda **event: events.append(event),
    )
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_subprocess)

    with pytest.raises(RuntimeError, match="WECHAT_RPA_RESPONSE_INVALID"):
        await providers.wechat_mobile("测试协会", "张三", "会长")

    assert events[-1]["kind"] == "wechat_failed"
    assert events[-1]["summary"] == "WECHAT_RPA_RESPONSE_INVALID"
