"""Real skill subprocess boundaries with fictional values and temporary files."""
import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import sys

import pytest

from src.tools.context import ToolExecutionContext, tool_execution_scope


@pytest.mark.asyncio
async def test_interleaved_tenant_guides_dynamic_hooks_and_execution_never_mutate_parent_environment(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.core.skill_executor import SkillExecutor
    from src.core.skill_hooks import SkillHooks
    from src.core.skill_registry import SkillRegistry
    import src.core.skill_environment as environment_owner
    marker = "RUNNER_ACCEPTANCE_FICTIONAL_VALUE"
    default = "RUNNER_ACCEPTANCE_FICTIONAL_DEFAULT"
    host = "RUNNER_ACCEPTANCE_FICTIONAL_HOST"
    alias = "RUNNER_ACCEPTANCE_IDENTITY_ALIAS"
    only_b = "RUNNER_ACCEPTANCE_ONLY_B"
    for key in (marker, default, alias, only_b):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(host, "host-fixture")
    monkeypatch.setenv("CURRENT_TENANT_ID", "wrong-global-fixture")
    monkeypatch.setattr(type(settings), "skills_security_allow_dynamic_context", True, raising=False)
    monkeypatch.setattr(environment_owner, "tenant_skill_env_path",
                        lambda tenant, skill: tmp_path / "private" / tenant / skill / ".env")
    registries = {}
    command_by_tenant = {}
    for tenant in ("tenant-a", "tenant-b"):
        directory = tmp_path / tenant / "same-skill"
        directory.mkdir(parents=True)
        script = directory / "read_env.py"
        script.write_text("import json, os\nprint(json.dumps({key: os.getenv(key) for key in " + repr([marker, default, host, alias, only_b, "CURRENT_TENANT_ID", "AID_TENANT_ID", "AID_USER_ID", "AID_SESSION_ID"]) + "}))\n")
        command = shlex.quote(sys.executable) + " " + shlex.quote(str(script))
        command_by_tenant[tenant] = command
        (directory / "SKILL.md").write_text(f"---\nname: same-skill\ndescription: Env acceptance\nversion: 1.0.0\nenv:\n  - name: {default}\n    default: default-{tenant}\nhooks:\n  onLoad: {command}\n---\nGuide dynamic output: !`{command}`\n")
        (directory / ".env").write_text(f"{marker}=base-{tenant}\n{host}=must-not-override-host\n")
        private = environment_owner.tenant_skill_env_path(tenant, "same-skill")
        private.parent.mkdir(parents=True)
        private.write_text(f"{marker}=private-{tenant}\nCURRENT_TENANT_ID=forged-private\nAID_TENANT_ID=forged-private\n{alias}=${{CURRENT_TENANT_ID}}\n")
        registry = SkillRegistry()
        registry.load_from_directory(directory.parent)
        registries[tenant] = registry
    before = dict(os.environ)
    both_guides_loaded = asyncio.Event()
    arrivals = []
    outputs = {}
    async def run(tenant):
        registry = registries[tenant]
        context = ToolExecutionContext(tenant_id=tenant, user_id=f"user-{tenant}", session_id=f"session-{tenant}")
        with tool_execution_scope(context):
            # Real guide + dynamic subprocess; run in a thread to interleave both tenants.
            guide = await asyncio.to_thread(registry.get_content, "same-skill", tenant_id=tenant)
            arrivals.append(tenant)
            if len(arrivals) == 2:
                both_guides_loaded.set()
            await asyncio.wait_for(both_guides_loaded.wait(), 10)
            env = registry.environment("same-skill", tenant_id=tenant, context=context)
            skill = registry.get("same-skill")
            hook = await SkillHooks.run_on_load(skill.hooks, skill.dir, env=env)
            executor = SkillExecutor(registry)
            monkeypatch.setattr(executor, "_create_workdir", lambda *args: Path(tmp_path / f"work-{tenant}"))
            (tmp_path / f"work-{tenant}").mkdir()
            result = await executor.execute_skill_command("same-skill", command_by_tenant[tenant],
                session_id=context.session_id, user_id=context.user_id)
            assert result.success
            outputs[tenant] = (guide, json.loads(hook), json.loads(result.stdout))
    try:
        await asyncio.gather(run("tenant-a"), run("tenant-b"))
        for tenant, (guide, hook, execution) in outputs.items():
            assert f'"{marker}": "private-{tenant}"' in guide
            for facts in (hook, execution):
                assert facts[marker] == f"private-{tenant}"
                assert facts[default] == f"default-{tenant}"
                assert facts[host] == "host-fixture"
                assert facts[alias] == tenant
                assert facts[only_b] is None
                assert facts["CURRENT_TENANT_ID"] == tenant
                assert facts["AID_TENANT_ID"] == tenant
                assert facts["AID_USER_ID"] == f"user-{tenant}"
                assert facts["AID_SESSION_ID"] == f"session-{tenant}"
        # Explicit public-call identity remains authoritative inside a different ambient scope.
        registry = registries["tenant-a"]
        explicit = ToolExecutionContext(tenant_id="tenant-a", user_id="user-tenant-a", session_id="session-tenant-a")
        ambient = ToolExecutionContext(tenant_id="tenant-b", user_id="user-tenant-b", session_id="session-tenant-b", env_vars={only_b: "b-only-fixture"})
        executor = SkillExecutor(registry)
        explicit_work = tmp_path / "explicit-work"
        explicit_work.mkdir()
        monkeypatch.setattr(executor, "_create_workdir", lambda *args: explicit_work)
        with tool_execution_scope(ambient):
            explicit_result = await executor.execute_skill_command("same-skill", command_by_tenant["tenant-a"],
                session_id=explicit.session_id, user_id=explicit.user_id, context=explicit)
        assert explicit_result.success
        facts = json.loads(explicit_result.stdout)
        assert facts[marker] == "private-tenant-a"
        assert facts["CURRENT_TENANT_ID"] == "tenant-a"
        assert facts["AID_TENANT_ID"] == "tenant-a"
        assert facts["AID_USER_ID"] == "user-tenant-a"
        assert facts["AID_SESSION_ID"] == "session-tenant-a"
        assert facts[only_b] is None
        changed_keys = sorted(key for key in set(before) | set(os.environ) if before.get(key) != os.environ.get(key))
        assert changed_keys == [], "Parent environment changed; compare names only, never values"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
