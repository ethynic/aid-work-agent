from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import AsyncMock

import pytest

from src.tools.assembly import (
    ToolAssemblyRequest,
    ToolAssemblyRole,
    assemble_agent_tools,
)
from src.tools.base import BaseTool
from src.tools.registry import ToolRegistry


class AssemblyProbeTool(BaseTool):
    catalog = False
    name = "probe"

    async def execute(self, **kwargs):
        return {"success": True}


class AssemblyLateProbeTool(BaseTool):
    catalog = False
    name = "a_late_probe"
    assembly_order = 100

    async def execute(self, **kwargs):
        return {"success": True}


class FakeSkillRegistry:
    def get_skill_tool_definition(self):
        return {"name": "use_skill", "description": "", "input_schema": {}}


def request(role, config=None, subagent_registry=None):
    return ToolAssemblyRequest(
        role=role, subagent_config=config, plan_manager=object(),
        skill_registry=FakeSkillRegistry(), skill_executor=object(),
        subagent_registry=subagent_registry,
    )


def test_master_requires_subagent_registry():
    with pytest.raises(ValueError, match="subagent_registry"):
        assemble_agent_tools(request(ToolAssemblyRole.MASTER))


def test_subagent_requires_config():
    with pytest.raises(ValueError, match="subagent_config"):
        assemble_agent_tools(request(ToolAssemblyRole.SUBAGENT))


def test_invalid_role_and_missing_dependencies_fail_fast():
    invalid = request(ToolAssemblyRole.STANDALONE)
    object.__setattr__(invalid, "role", "master")
    with pytest.raises(ValueError, match="非法"):
        assemble_agent_tools(invalid)
    missing = request(ToolAssemblyRole.STANDALONE)
    object.__setattr__(missing, "plan_manager", None)
    with pytest.raises(ValueError, match="plan_manager"):
        assemble_agent_tools(missing)


def test_allowed_and_excluded_filter_uses_public_registry_api():
    config = SimpleNamespace(
        name="x", tools={"inherit": False},
        get_allowed_tools=lambda: ["probe"],
        get_excluded_tools=lambda: [],
        llm_provider=None,
    )
    with patch("src.tools.assembly.discover_tool_classes", return_value={"probe": AssemblyProbeTool}):
        bundle = assemble_agent_tools(request(ToolAssemblyRole.SUBAGENT, config))
    assert bundle.final_names == ("probe",)


def test_registry_duplicate_and_collection_apis():
    registry = ToolRegistry()
    registry.register(AssemblyProbeTool())
    with pytest.raises(ValueError, match="重复"):
        registry.register(AssemblyProbeTool())
    assert tuple(registry.snapshot()) == ("probe",)
    registry.retain_only({"other"})
    assert registry.list_tools() == []


def test_assembly_order_preserves_explicit_compatibility_priority():
    with patch(
        "src.tools.assembly.discover_tool_classes",
        return_value={
            "a_late_probe": AssemblyLateProbeTool,
            "probe": AssemblyProbeTool,
        },
    ):
        bundle = assemble_agent_tools(request(ToolAssemblyRole.STANDALONE))
    assert bundle.discovered_names == ("a_late_probe", "probe")
    assert bundle.final_names == ("probe", "a_late_probe")


def test_master_assembles_catalog_business_candidates_without_internal_steps():
    from src.local_tools.catalog import TRUSTED_PROVIDERS, agent_tools
    from src.local_tools.manifest import LOCAL_PROXY_TOOL_NAMES
    from src.local_tools.wecom_business import BUSINESS_TOOLS
    from src.tools.base import ExecutionTarget

    with patch("src.tools.assembly.discover_tool_classes", return_value={"probe": AssemblyProbeTool}):
        bundle = assemble_agent_tools(request(
            ToolAssemblyRole.MASTER, subagent_registry=object()
        ))
    business = {name for key in TRUSTED_PROVIDERS for name in agent_tools(key)}
    assert set(bundle.final_names) & LOCAL_PROXY_TOOL_NAMES == business & LOCAL_PROXY_TOOL_NAMES
    assert {name for name in bundle.final_names if name.startswith('wecom_')} == BUSINESS_TOOLS
    assert "probe" in bundle.final_names
    for name in BUSINESS_TOOLS:
        tool = bundle.registry.get_tool(name)
        assert tool.execution_target is ExecutionTarget.LOCAL_REQUIRED
        properties = tool.to_tool_definition()["input_schema"]["properties"]
        assert not {"target_ref", "target_name", "resolve_only", "send_invocation_id"} & properties.keys()


def test_non_master_controls_cannot_bind_delegate():
    with patch(
        "src.tools.assembly.discover_tool_classes",
        return_value={"probe": AssemblyProbeTool},
    ):
        bundle = assemble_agent_tools(request(ToolAssemblyRole.STANDALONE))
    with pytest.raises(RuntimeError, match="禁止绑定"):
        bundle.controls.bind_delegate(
            subagent_registry=object(), subagent_executor=object()
        )


@pytest.mark.asyncio
async def test_wecom_batch_executor_validates_business_fields_and_preserves_injected_context():
    from src.local_tools.proxy_tool import WecomReadSessionsTool
    from src.tools.executor import ToolExecutor
    from src.services.agent_runner.runtime.local_tools import ProgressQueue

    tool = WecomReadSessionsTool()
    registry = ToolRegistry()
    registry.register(tool)
    progress = ProgressQueue(maxsize=1)
    injected = {'_trusted_tenant_id': 'tenant', '_trusted_user_id': 'user',
                '_session_id': 'session', '_progress_queue': progress}
    with patch.object(tool, 'execute', AsyncMock(return_value={'success': True})) as dispatch:
        result = await ToolExecutor(registry).execute(tool.name, {'limit': '5', **injected})
    assert result['success']
    assert dispatch.await_args.kwargs == {'limit': 5, **injected}
    assert dispatch.await_args.kwargs['_progress_queue'] is progress


@pytest.mark.asyncio
async def test_wecom_batch_executor_still_rejects_unknown_business_fields_before_dispatch():
    from src.local_tools.proxy_tool import WecomReadSessionsTool
    from src.tools.executor import ToolExecutor

    tool = WecomReadSessionsTool()
    registry = ToolRegistry()
    registry.register(tool)
    with patch.object(tool, 'execute', AsyncMock()) as dispatch:
        result = await ToolExecutor(registry).execute(tool.name, {'limit': 5, 'target_name': 'ignored scope',
            '_trusted_tenant_id': 'tenant', '_trusted_user_id': 'user', '_session_id': 'session'})
    assert not result['success'] and any('target_name' in item for item in result['validation_errors'])
    dispatch.assert_not_awaited()
