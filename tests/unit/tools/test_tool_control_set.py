from types import SimpleNamespace
import threading
from unittest.mock import patch

import pytest

from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.control_set import ControlToolDependencies, ToolControlSet
from src.tools.delegation_policy import DelegationAuthorizer


class FakeSkillRegistry:
    def get_skill_tool_definition(self):
        return {"name": "use_skill", "description": "skills", "input_schema": {}}


class FakeSubagentRegistry:
    def __init__(self):
        self.available_seen = "unset"

    def get_delegation_tool_definition(self, available_subagents=None):
        self.available_seen = available_subagents
        if available_subagents == []:
            return None
        return {"name": "delegate_to_subagent", "input_schema": {}}

    def get(self, name):
        return SimpleNamespace(name=name, dir_name=name)

    def get_descriptions(self):
        return "- x"


class FakeExecutor:
    async def delegate(self, **kwargs):
        raise AssertionError("unauthorized execution reached executor")


class DenyAuthorizer:
    def authorize(self, context, name, config):
        return False


class FailingAuthorizer:
    def authorize(self, context, name, config):
        raise RuntimeError("database password leaked")


def make_controls(authorizer=None):
    registry = FakeSubagentRegistry()
    controls = ToolControlSet(ControlToolDependencies(
        plan_manager=object(), skill_registry=FakeSkillRegistry(),
        skill_executor=object(), tool_registry=object(),
        subagent_registry=registry, delegation_authorizer=authorizer,
        allow_delegate=True,
    ))
    controls.bind_delegate(subagent_registry=registry, subagent_executor=FakeExecutor())
    return controls, registry


def test_empty_available_subagents_hides_delegate_schema():
    controls, registry = make_controls()
    names = [item["name"] for item in controls.definitions(available_subagents=[])]
    assert "delegate_to_subagent" not in names
    assert registry.available_seen == []


def test_none_available_subagents_keeps_unrestricted_semantics():
    controls, registry = make_controls()
    names = [item["name"] for item in controls.definitions(available_subagents=None)]
    assert "delegate_to_subagent" in names
    assert registry.available_seen is None


def test_delegate_definition_keeps_multimodal_contract():
    controls, _ = make_controls()
    definition = next(
        item for item in controls.definitions(available_subagents=None)
        if item["name"] == "delegate_to_subagent"
    )
    # FakeSubagentRegistry only exercises delegation visibility. The production
    # registry contract is covered in the integration-style assertion below.
    from src.subagents.registry import SubagentRegistry
    registry = SubagentRegistry()
    registry._configs["video"] = SimpleNamespace(description="video", dir_name="video")
    production = registry.get_delegation_tool_definition(["video"])
    assert "image_paths" in production["input_schema"]["properties"]


@pytest.mark.asyncio
async def test_direct_delegate_denied_before_executor():
    controls, _ = make_controls(DenyAuthorizer())
    with tool_execution_scope(ToolExecutionContext(tenant_id="tenant-a")):
        result = await controls.get("delegate_to_subagent").execute(
            subagent_name="x", task_description="task"
        )
    assert result["permission_denied"] is True


@pytest.mark.asyncio
async def test_delegate_authorization_failure_is_safely_isolated():
    controls, _ = make_controls(FailingAuthorizer())
    with tool_execution_scope(ToolExecutionContext(tenant_id="tenant-a")):
        result = await controls.get("delegate_to_subagent").execute(
            subagent_name="x", task_description="task"
        )
    assert result["error_code"] == "SUBAGENT_AUTHORIZATION_UNAVAILABLE"
    assert "password" not in result["error"]


@pytest.mark.asyncio
async def test_delegate_db_fallback_runs_off_event_loop():
    from src.tools.agent.delegate_tool import DelegateToSubagentTool

    class MissingRegistry(FakeSubagentRegistry):
        def get(self, name):
            return None

    event_loop_thread = threading.get_ident()
    lookup_threads = []

    def load_from_db(registry, name):
        lookup_threads.append(threading.get_ident())
        return SimpleNamespace(name=name, dir_name=name)

    tool = DelegateToSubagentTool(MissingRegistry(), FakeExecutor(), DenyAuthorizer())
    with (
        patch("src.subagents.factory.AgentFactory._load_single_from_db", side_effect=load_from_db),
        tool_execution_scope(ToolExecutionContext(tenant_id="tenant-a")),
    ):
        result = await tool.execute(subagent_name="x", task_description="task")

    assert result["permission_denied"] is True
    assert lookup_threads and lookup_threads[0] != event_loop_thread


def test_delegation_authorizer_isolates_tenants():
    """租户间委派授权隔离（SaaS 开关已移除，恒为 SaaS 模式）"""
    authorizer = DelegationAuthorizer(
        lambda tenant_id: {"travel"} if tenant_id == "tenant-a" else set()
    )
    config = SimpleNamespace(dir_name="travel")
    assert authorizer.authorize(
        ToolExecutionContext(tenant_id="tenant-a"), "旅行顾问", config
    )
    assert not authorizer.authorize(
        ToolExecutionContext(tenant_id="tenant-b"), "旅行顾问", config
    )
