"""C5: real file hot gates, isolated config only; no device or production writes."""
import os

import pytest

from src.session_tasks import config as task_config
from src.weixin_conversation import config as scenario_config


@pytest.mark.parametrize("section", ["session_tasks", "weixin_conversation"])
def test_scoped_hot_rollback_and_broken_file_fail_closed(tmp_path, monkeypatch, section):
    path = tmp_path / "gates.yaml"
    monkeypatch.setattr(task_config, "_gate_yaml_path", lambda: path)
    monkeypatch.setattr(scenario_config, "_gate_yaml_path", lambda: path)
    monkeypatch.setattr(task_config, "_gate_cache", None)
    monkeypatch.setattr(scenario_config, "_gate_cache", None)
    revision = 0

    def save(disabled=None):
        nonlocal revision
        revision += 1
        path.write_text("\n".join(
            f'{key}:\n  enabled: {str(key != disabled).lower()}\n  tenant_allowlist: [c5_tenant]'
            for key in ("session_tasks", "weixin_conversation")
        ), encoding="utf-8")
        # Force distinct file versions without sleep or filesystem timestamp races.
        stamp = 1_800_000_000_000_000_000 + revision * 1_000_000_000
        os.utime(path, ns=(stamp, stamp))

    def allowed(tenant):
        return task_config.tenant_allowed(tenant) and scenario_config.scenario_enabled(tenant)

    save()
    assert allowed("c5_tenant")
    assert not allowed("outside")
    save(section)
    assert not allowed("c5_tenant")
    # The unrelated gate stays enabled: rollback does not rewrite another section.
    other = (scenario_config.scenario_enabled("c5_tenant") if section == "session_tasks"
             else task_config.tenant_allowed("c5_tenant"))
    assert other
    save()
    assert allowed("c5_tenant")
    path.write_text("[broken YAML", encoding="utf-8")
    assert not task_config.tenant_allowed("c5_tenant")
    assert not scenario_config.scenario_enabled("c5_tenant")
    path.unlink()
    assert not allowed("c5_tenant")
