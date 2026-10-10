"""E2E 凭证门禁的离线回归；不请求真实 LLM。"""
import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


spec = importlib.util.spec_from_file_location(
    "e2e_collection_under_test", Path(__file__).resolve().parents[1] / "e2e/conftest.py",
)
e2e_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e_config)


@pytest.mark.parametrize("primary_keys,fallback_keys,enabled,skipped", [
    ([], [], False, True),
    (["primary-placeholder"], [], False, False),
    ([], ["fallback-placeholder"], True, False),
    ([], ["fallback-placeholder"], False, True),
])
def test_collection_uses_configured_provider_chain(monkeypatch, primary_keys, fallback_keys, enabled, skipped):
    settings_module = importlib.import_module("src.config.settings")

    def provider(keys):
        return SimpleNamespace(get_effective_keys=lambda: keys)

    settings = SimpleNamespace(llm=SimpleNamespace(
        provider="qwen", qwen=provider(primary_keys), zhipu=provider(fallback_keys),
        failover=SimpleNamespace(enabled=enabled, providers=["zhipu"]),
    ))
    monkeypatch.setattr(settings_module, "settings", settings)
    # 旧全局假 key 不能再放行没有实际 provider 配置的 E2E。
    monkeypatch.setenv("API_KEYS", "legacy-placeholder")
    item = MagicMock(keywords={"e2e": True, "llm": True})

    e2e_config.pytest_collection_modifyitems(None, [item])

    assert item.add_marker.called is skipped
    if skipped:
        assert item.add_marker.call_args.args[0].name == "skip"


def test_e2e_hook_does_not_skip_unit_llm_tests(monkeypatch):
    def unexpected_credential_check():
        pytest.fail("非 E2E 测试不应执行外部凭证门禁")

    monkeypatch.setattr(e2e_config, "_has_llm_credentials", unexpected_credential_check)
    item = MagicMock(keywords={"llm": True, "unit": True})

    e2e_config.pytest_collection_modifyitems(None, [item])

    item.add_marker.assert_not_called()
