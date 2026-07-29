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
