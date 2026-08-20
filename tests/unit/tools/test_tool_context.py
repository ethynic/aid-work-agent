import asyncio

import pytest

from src.tools.base import BaseTool
from src.tools.context import (
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
        return {"success": True, "tenant_id": context.tenant_id if context else None}


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
async def test_execute_batch_propagates_same_context():
    registry = ToolRegistry()
    registry.register(ContextEchoTool())
    results = await ToolExecutor(registry).execute_batch(
        [{"tool_name": "context_echo", "parameters": {}}] * 2,
        context=ToolExecutionContext(tenant_id="batch"),
    )
    assert [item["result"]["tenant_id"] for item in results] == ["batch", "batch"]


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
