import asyncio
from copy import deepcopy

from src.tools.base import BaseTool
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry
from loguru import logger


class FailingCompatibilityTool(BaseTool):
    name = "compat_fail"
    parameters_schema = {"type": "object", "required": ["value"]}

    def __init__(self): self.received = None

    async def execute(self, **kwargs):
        self.received = kwargs
        raise RuntimeError(f"legacy failure: {kwargs['value']}")


def test_default_executor_keeps_parameter_object_and_legacy_error_text():
    async def scenario():
        registry = ToolRegistry(); tool = FailingCompatibilityTool(); registry.register(tool)
        parameters = {"value": "visible", "_redact_parameter_logs": "ordinary-business-field"}
        before = deepcopy(parameters)
        result = await ToolExecutor(registry).execute("compat_fail", parameters)
        assert parameters == before
        assert tool.received == before
        assert result == {"success": False, "error": "legacy failure: visible"}

    asyncio.run(scenario())


def test_explicit_redacted_executor_hides_arguments_and_exception_text():
    async def scenario():
        registry = ToolRegistry(); tool = FailingCompatibilityTool(); registry.register(tool)
        parameters = {"value": "must-not-leak"}
        messages = []
        sink = logger.add(lambda message: messages.append(str(message)), format="{message}")
        try:
            result = await ToolExecutor(registry).execute("compat_fail", parameters, redact_parameter_logs=True)
        finally:
            logger.remove(sink)
        assert parameters == {"value": "must-not-leak"}
        assert result == {"success": False, "error": "Remote tool execution failed"}
        assert "must-not-leak" not in "".join(messages)

    asyncio.run(scenario())
