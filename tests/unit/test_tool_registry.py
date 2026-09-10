"""
ToolRegistry 和 ToolExecutor 单元测试
"""

import pytest

pytestmark = pytest.mark.tools
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.tools.registry import ToolRegistry
from src.tools.executor import ToolExecutor


class TestToolRegistry:
    """工具注册表测试"""

    def test_register_and_get_tool(self, mock_tool):
        registry = ToolRegistry()
        tool = mock_tool(name="test_tool")
        registry.register(tool)
        assert registry.get_tool("test_tool") == tool

    def test_unregister_tool(self, mock_tool):
        registry = ToolRegistry()
        tool = mock_tool(name="test_tool")
        registry.register(tool)
        registry.unregister("test_tool")
        assert registry.get_tool("test_tool") is None

    def test_has_tool(self, mock_tool):
        registry = ToolRegistry()
        tool = mock_tool(name="test_tool")
        registry.register(tool)
        assert registry.has_tool("test_tool")
        assert not registry.has_tool("nonexistent")

    def test_list_tools(self, mock_tool):
        registry = ToolRegistry()
        registry.register(mock_tool(name="tool_a"))
        registry.register(mock_tool(name="tool_b"))
        names = registry.list_tools()
        assert "tool_a" in names
        assert "tool_b" in names

    def test_get_tool_definitions(self, mock_tool):
        registry = ToolRegistry()
        registry.register(mock_tool(name="tool_a"))
        defs = registry.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["name"] == "tool_a"

    def test_get_nonexistent_tool_returns_none(self):
        registry = ToolRegistry()
        assert registry.get_tool("nonexistent") is None


class TestToolExecutor:
    """工具执行器测试"""

    @pytest.mark.asyncio
    async def test_execute_known_tool(self, mock_tool):
        registry = ToolRegistry()
        registry.register(mock_tool(
            name="web_search",
            execute_return={"success": True, "results": []},
        ))
        executor = ToolExecutor(registry=registry)
        result = await executor.execute("web_search", {"keyword": "test"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_does_not_log_tool_parameters(self, mock_tool, monkeypatch):
        registry = ToolRegistry()
        registry.register(mock_tool(
            name="sensitive_tool",
            execute_return={"success": True},
        ))
        executor = ToolExecutor(registry=registry)
        logged = []
        monkeypatch.setattr("src.tools.executor.logger.info", logged.append)

        await executor.execute("sensitive_tool", {"token": "secret-value"})

        assert any("sensitive_tool" in message for message in logged)
        assert all("secret-value" not in message for message in logged)

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        executor = ToolExecutor(registry=registry)
        result = await executor.execute("nonexistent", {})
        assert result.get("success") is False

    @pytest.mark.asyncio
    async def test_execute_batch(self, mock_tool):
        registry = ToolRegistry()
        registry.register(mock_tool(name="tool_a", execute_return={"success": True}))
        registry.register(mock_tool(name="tool_b", execute_return={"success": True}))
        executor = ToolExecutor(registry=registry)

        results = await executor.execute_batch([
            {"tool_name": "tool_a", "parameters": {}},
            {"tool_name": "tool_b", "parameters": {}},
        ])
        assert len(results) == 2


class _SearchInput(BaseModel):
    """类型规范化测试用工具参数"""
    query: str = Field(..., description="查询")
    top_k: Optional[int] = Field(10, description="返回数量")


class _SearchToolForCoerce(BaseTool):
    name = "search_coerce"
    description = "test"
    InputModel = _SearchInput

    def __init__(self):
        self.last_kwargs = None

    async def execute(self, **kwargs):
        self.last_kwargs = dict(kwargs)
        return {"success": True}


class TestToolExecutorTypeCoercion:
    """ToolExecutor 参数类型规范化测试（LLM 把整数以字符串传出时强转）"""

    @pytest.mark.asyncio
    async def test_string_int_coerced_to_int(self):
        """top_k="10" 字符串被强转为 int 10，注入参数保留"""
        registry = ToolRegistry()
        tool = _SearchToolForCoerce()
        registry.register(tool)
        executor = ToolExecutor(registry=registry)

        result = await executor.execute(
            "search_coerce",
            {"query": "x", "top_k": "10", "_trusted_tenant_id": "t1"},
        )

        assert result["success"] is True
        assert tool.last_kwargs["top_k"] == 10
        assert isinstance(tool.last_kwargs["top_k"], int)
        # 内部注入参数必须原样保留
        assert tool.last_kwargs["_trusted_tenant_id"] == "t1"

    @pytest.mark.asyncio
    async def test_unset_field_not_filled_with_default(self):
        """未显式传入的字段不填充默认值，避免改变 kwargs 语义"""
        registry = ToolRegistry()
        tool = _SearchToolForCoerce()
        registry.register(tool)
        executor = ToolExecutor(registry=registry)

        await executor.execute("search_coerce", {"query": "x"})

        assert "top_k" not in tool.last_kwargs

    @pytest.mark.asyncio
    async def test_float_integer_value_coerced(self):
        """浮点整数值（如 3.0）被强转为 int"""
        registry = ToolRegistry()
        tool = _SearchToolForCoerce()
        registry.register(tool)
        executor = ToolExecutor(registry=registry)

        await executor.execute("search_coerce", {"query": "x", "top_k": 3.0})

        assert tool.last_kwargs["top_k"] == 3
        assert isinstance(tool.last_kwargs["top_k"], int)


class TestToolExecutorResultSanitize:
    """工具结果出口统一清洗孤立代理字符（SSE/落库前最后一道防线）"""

    @pytest.mark.asyncio
    async def test_execute_sanitizes_surrogates_in_result(self, mock_tool):
        registry = ToolRegistry()
        registry.register(mock_tool(
            name="pdf_read",
            execute_return={"success": True, "text": "标题\ud83c正文", "meta": {"page": 1}},
        ))
        executor = ToolExecutor(registry=registry)
        result = await executor.execute("pdf_read", {})
        assert result["text"] == "标题正文"
        result["text"].encode("utf-8")
