import argparse
import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit
CLI_PATH = (
    Path(__file__).resolve().parents[3]
    / "clients"
    / "association-enrichment-cli"
    / "association_enrichment_cli.py"
)


def _load_cli():
    spec = importlib.util.spec_from_file_location("association_enrichment_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_dry_run_does_not_construct_project_providers(monkeypatch, tmp_path):
    cli = _load_cli()
    import src.services.association_enrichment_providers as providers

    monkeypatch.setattr(
        providers,
        "ProjectAssociationProviders",
        lambda **_kwargs: pytest.fail("dry-run must not construct providers"),
    )
    result = await cli.run(
        argparse.Namespace(
            association=["甲协会,乙协会,甲协会"],
            input=None,
            output=str(tmp_path / "unused.xlsx"),
            dry_run=True,
        )
    )

    assert result["associations"] == ["甲协会", "乙协会"]
    assert not (tmp_path / "unused.xlsx").exists()


@pytest.mark.asyncio
async def test_dry_run_redacts_mobile_from_stdout_payload(tmp_path):
    cli = _load_cli()
    result = await cli.run(
        argparse.Namespace(
            association=["测试协会 18612345678"],
            input=None,
            output=str(tmp_path / "unused.xlsx"),
            dry_run=True,
        )
    )

    assert result["associations"] == ["测试协会 186****5678"]


def test_cli_error_code_does_not_echo_sensitive_exception_text():
    cli = _load_cli()

    assert cli._safe_error_code(ValueError("provider 18612345678 failed")) == "ValueError"
    assert cli._safe_error_code(ValueError("INPUT_FILE_NOT_FOUND")) == "INPUT_FILE_NOT_FOUND"


def test_cli_business_failure_has_nonzero_exit_without_hiding_output(monkeypatch, capsys):
    cli = _load_cli()

    async def fake_run(_args):
        return {
            "ok": False,
            "business_status": "failed",
            "failed_count": 1,
            "output": "result.xlsx",
        }

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(cli, "build_parser", lambda: type("Parser", (), {
        "parse_args": lambda self: argparse.Namespace()
    })())

    assert cli.main() == 2
    output = capsys.readouterr().out
    assert '"business_status": "failed"' in output
    assert '"output": "result.xlsx"' in output


@pytest.mark.asyncio
async def test_run_exports_mixed_rows_but_reports_business_failure(monkeypatch, tmp_path):
    cli = _load_cli()
    import src.services.association_enrichment_providers as providers
    from src.services.association_batch_enrichment import AssociationEnrichmentRow

    class FakeProviders:
        def __init__(self, **_kwargs):
            self.resolve_official_site = None
            self.collect_official_profile = None
            self.fallback_profile = None
            self.wechat_mobile = None

    class FakeEnricher:
        def __init__(self, **_kwargs):
            pass

        async def enrich_many(self, names):
            assert names == ["失败协会", "正常协会"]
            return [
                AssociationEnrichmentRow(
                    association_name=names[0],
                    processing_status="failed",
                    errors=["profile:not_found"],
                ),
                AssociationEnrichmentRow(
                    association_name=names[1],
                    processing_status="complete",
                ),
            ]

    monkeypatch.setattr(providers, "ProjectAssociationProviders", FakeProviders)
    monkeypatch.setattr(cli, "AssociationBatchEnricher", FakeEnricher)
    output = tmp_path / "mixed.xlsx"

    result = await cli.run(
        argparse.Namespace(
            association=["失败协会\n正常协会"],
            input=None,
            output=str(output),
            dry_run=False,
        )
    )

    assert output.is_file()
    assert result == {
        "ok": False,
        "business_status": "failed",
        "association_count": 2,
        "processed_count": 2,
        "aborted_count": 0,
        "abort_error_code": None,
        "complete_count": 1,
        "partial_count": 0,
        "failed_count": 1,
        "output": str(output),
    }


@pytest.mark.asyncio
async def test_run_reports_session_abort_as_business_failure(monkeypatch, tmp_path):
    cli = _load_cli()
    import src.services.association_enrichment_providers as providers
    from src.services.association_batch_enrichment import (
        AssociationBatchResult,
        AssociationEnrichmentRow,
    )

    class FakeProviders:
        def __init__(self, **_kwargs):
            self.resolve_official_site = None
            self.collect_official_profile = None
            self.fallback_profile = None
            self.wechat_mobile = None

    class AbortedEnricher:
        def __init__(self, **_kwargs):
            pass

        async def enrich_many(self, names):
            return AssociationBatchResult([
                AssociationEnrichmentRow(
                    association_name=names[0],
                    processing_status="partial",
                )
            ], aborted=True, abort_error_code="PLUGIN_CLOSE_TIMEOUT")

    monkeypatch.setattr(providers, "ProjectAssociationProviders", FakeProviders)
    monkeypatch.setattr(cli, "AssociationBatchEnricher", AbortedEnricher)
    output = tmp_path / "aborted.xlsx"

    result = await cli.run(
        argparse.Namespace(
            association=["协会一\n协会二"],
            input=None,
            output=str(output),
            dry_run=False,
        )
    )

    assert output.is_file()
    assert result["ok"] is False
    assert result["business_status"] == "failed"
    assert result["association_count"] == 2
    assert result["processed_count"] == 1
    assert result["aborted_count"] == 2
    assert result["abort_error_code"] == "PLUGIN_CLOSE_TIMEOUT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("names", "row_count"),
    [(["单协会"], 1), (["协会一", "最后协会"], 2)],
)
async def test_run_reports_explicit_abort_even_when_no_rows_are_missing(
    monkeypatch, tmp_path, names, row_count
):
    cli = _load_cli()
    import src.services.association_enrichment_providers as providers
    from src.services.association_batch_enrichment import (
        AssociationBatchResult,
        AssociationEnrichmentRow,
    )

    class FakeProviders:
        def __init__(self, **_kwargs):
            self.resolve_official_site = None
            self.collect_official_profile = None
            self.fallback_profile = None
            self.wechat_mobile = None

    class AbortedEnricher:
        def __init__(self, **_kwargs):
            pass

        async def enrich_many(self, requested_names):
            assert requested_names == names
            return AssociationBatchResult(
                [
                    AssociationEnrichmentRow(
                        association_name=name,
                        processing_status="partial",
                    )
                    for name in requested_names[:row_count]
                ],
                aborted=True,
                abort_error_code="SESSION_CLEANUP_FAILED",
            )

    monkeypatch.setattr(providers, "ProjectAssociationProviders", FakeProviders)
    monkeypatch.setattr(cli, "AssociationBatchEnricher", AbortedEnricher)
    result = await cli.run(argparse.Namespace(
        association=["\n".join(names)], input=None,
        output=str(tmp_path / f"aborted-{row_count}.xlsx"), dry_run=False,
    ))

    assert result["ok"] is False
    assert result["business_status"] == "failed"
    assert result["processed_count"] == row_count
    assert result["aborted_count"] == 1
    assert result["abort_error_code"] == "SESSION_CLEANUP_FAILED"
