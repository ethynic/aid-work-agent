import asyncio

import pytest

from src.tools.base import BaseTool
from src.tools.context import (
    ExecutionContextFactory,
    ToolExecutionContext,
    current_tool_execution_context,
    tool_execution_scope,
)
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry


class ContextEchoTool(BaseTool):
    catalog = False
    name = "context_echo"

    async def execute(self, **kwargs):
        await asyncio.sleep(0)
        context = current_tool_execution_context()
        if kwargs.get("raise_error"):
            raise RuntimeError("boom")
        return {
            "success": True,
            "tenant_id": context.tenant_id if context else None,
            "request_data": context.request_data if context else None,
        }


class BlockingContextTool(BaseTool):
    catalog = False
    name = "blocking_context"

    async def execute(self, **kwargs):
        await asyncio.sleep(60)
        return {"success": True}


@pytest.mark.asyncio
async def test_concurrent_execute_isolates_context():
    registry = ToolRegistry()
    registry.register(ContextEchoTool())
    executor = ToolExecutor(registry)
    results = await asyncio.gather(*(
        executor.execute(
            "context_echo", {}, context=ToolExecutionContext(tenant_id=tenant_id)
        )
        for tenant_id in ("tenant-a", "tenant-b")
    ))
    assert [item["tenant_id"] for item in results] == ["tenant-a", "tenant-b"]
    assert current_tool_execution_context() is None


@pytest.mark.asyncio
async def test_concurrent_execute_isolates_request_data():
    registry = ToolRegistry()
    registry.register(ContextEchoTool())
    executor = ToolExecutor(registry)

    results = await asyncio.gather(*(
        executor.execute(
            "context_echo",
            {},
            context=ToolExecutionContext(
                tenant_id=f"tenant-{mode}",
                request_data={"video_params": {"mode": mode}},
            ),
        )
        for mode in ("refine", "agile")
    ))

    assert [
        item["request_data"]["video_params"]["mode"]
        for item in results
    ] == ["refine", "agile"]
    assert current_tool_execution_context() is None


@pytest.mark.asyncio
async def test_exception_resets_outer_context():
    registry = ToolRegistry()
    registry.register(ContextEchoTool())
    executor = ToolExecutor(registry)
    outer = ToolExecutionContext(tenant_id="outer")
    with tool_execution_scope(outer):
        result = await executor.execute(
            "context_echo", {"raise_error": True},
            context=ToolExecutionContext(tenant_id="inner"),
        )
        assert result["success"] is False
        assert current_tool_execution_context() is outer
    assert current_tool_execution_context() is None


@pytest.mark.asyncio
async def test_cancellation_resets_outer_context_in_same_task():
    registry = ToolRegistry()
    registry.register(BlockingContextTool())
    executor = ToolExecutor(registry)
    outer = ToolExecutionContext(tenant_id="outer")
    current_task = asyncio.current_task()
    asyncio.get_running_loop().call_later(0.01, current_task.cancel)

    with tool_execution_scope(outer):
        with pytest.raises(asyncio.CancelledError):
            await executor.execute(
                "blocking_context", {},
                context=ToolExecutionContext(tenant_id="inner"),
            )
        assert current_tool_execution_context() is outer
    assert current_tool_execution_context() is None


@pytest.mark.asyncio
async def test_execute_batch_propagates_same_context():
    registry = ToolRegistry()
    registry.register(ContextEchoTool())
    results = await ToolExecutor(registry).execute_batch(
        [{"tool_name": "context_echo", "parameters": {}}] * 2,
        context=ToolExecutionContext(tenant_id="batch"),
    )
    assert [item["result"]["tenant_id"] for item in results] == ["batch", "batch"]


@pytest.mark.asyncio
async def test_execute_batch_propagates_frozen_request_data():
    registry = ToolRegistry()
    registry.register(ContextEchoTool())
    context = ToolExecutionContext(request_data={"request": {"items": [1, 2]}})

    results = await ToolExecutor(registry).execute_batch(
        [{"tool_name": "context_echo", "parameters": {}}] * 2,
        context=context,
    )

    assert [
        item["result"]["request_data"]["request"]["items"]
        for item in results
    ] == [(1, 2), (1, 2)]


@pytest.mark.asyncio
async def test_explicit_empty_context_blocks_ambient_request_data():
    registry = ToolRegistry()
    registry.register(ContextEchoTool())
    executor = ToolExecutor(registry)
    ambient = ToolExecutionContext(request_data={"secret": "outer"})

    with tool_execution_scope(ambient):
        fresh = ExecutionContextFactory.for_agent_call(request_data={})
        result = await executor.execute("context_echo", {}, context=fresh)

    assert dict(result["request_data"]) == {}


@pytest.mark.asyncio
async def test_thread_propagation_contract_is_explicit():
    context = ToolExecutionContext(tenant_id="tenant-thread")
    with tool_execution_scope(context):
        copied = await asyncio.to_thread(current_tool_execution_context)
        loop = asyncio.get_running_loop()
        implicit = await loop.run_in_executor(None, current_tool_execution_context)

        def explicit_target():
            with tool_execution_scope(context):
                return current_tool_execution_context()

        explicit = await loop.run_in_executor(None, explicit_target)
    assert copied is context
    assert implicit is None
    assert explicit is context


def test_nested_factory_and_derive_propagate_frozen_request_data():
    source = {"feature": {"values": [1, 2]}}
    outer = ToolExecutionContext(tenant_id="outer", request_data=source)
    source["feature"]["values"].append(3)

    with tool_execution_scope(outer):
        nested = ExecutionContextFactory.for_agent_call(session_id="nested")
        derived = nested.derive(tool_call_id="call-1")

    assert derived.request_data["feature"]["values"] == (1, 2)
    with pytest.raises(TypeError):
        derived.request_data["feature"] = {"values": ()}


def test_factory_inherits_tenant_env_vars_from_parent():
    with tool_execution_scope(ToolExecutionContext(env_vars={"AGENT_TOKEN": "tk"})):
        nested = ExecutionContextFactory.for_agent_call(session_id="nested")
    assert nested.env_vars["AGENT_TOKEN"] == "tk"


def test_factory_passes_llm_gateway():
    gateway = object()
    ctx = ExecutionContextFactory.for_agent_call(llm_gateway=gateway)
    assert ctx.llm_gateway is gateway


def test_factory_inherits_llm_gateway_from_parent():
    gateway = object()
    with tool_execution_scope(ToolExecutionContext(llm_gateway=gateway)):
        nested = ExecutionContextFactory.for_agent_call(session_id="nested")
    assert nested.llm_gateway is gateway


def test_factory_llm_gateway_defaults_to_none():
    ctx = ExecutionContextFactory.for_agent_call(session_id="s")
    assert ctx.llm_gateway is None


def test_env_vars_frozen_against_mutation():
    source = {"AGENT_TOKEN": "tk"}
    ctx = ToolExecutionContext(env_vars=source)
    source["AGENT_TOKEN"] = "changed"
    assert ctx.env_vars["AGENT_TOKEN"] == "tk"
    with pytest.raises(TypeError):
        ctx.env_vars["OTHER"] = "x"
