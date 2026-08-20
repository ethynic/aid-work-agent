from types import SimpleNamespace
from unittest.mock import patch
import builtins

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


def test_master_assembly_never_imports_local_proxy(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "src.local_tools.proxy_tool":
            raise AssertionError("MASTER 不得加载 local proxy 实现")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with patch("src.tools.assembly.discover_tool_classes", return_value={"probe": AssemblyProbeTool}):
        bundle = assemble_agent_tools(request(
            ToolAssemblyRole.MASTER, subagent_registry=object()
        ))
    assert bundle.final_names == ("probe",)


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
