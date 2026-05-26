"""
MCP Server 模块单元测试

覆盖: config、tools registry、executor、server 创建
"""

import os
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Config 测试
# ---------------------------------------------------------------------------

class TestMCPConfig:

    def test_defaults_when_no_settings(self):
        """settings.mcp 不存在时使用默认值"""
        with patch("src.mcp.config.settings") as mock_settings:
            mock_settings.mcp = None
            # 重新加载模块拿到新配置
            import importlib
            import src.mcp.config as cfg_mod
            importlib.reload(cfg_mod)

            config = cfg_mod.load_mcp_config()
            assert config.enabled is False
            assert config.transport == "sse"
            assert config.port == 8765
            assert config.auth_enabled is True
            assert config.api_keys == []
            assert config.tools_config == []

    def test_env_var_api_keys(self):
        """api_keys 从环境变量读取"""
        with patch("src.mcp.config.settings") as mock_settings, \
             patch.dict(os.environ, {"MCP_API_KEYS": "key1,key2"}):
            mock_settings.mcp = None
            import importlib
            import src.mcp.config as cfg_mod
            importlib.reload(cfg_mod)

            config = cfg_mod.load_mcp_config()
            assert config.api_keys == ["key1", "key2"]

    def test_invalid_transport_falls_back(self):
        """不支持的 transport 回退为 sse"""
        with patch("src.mcp.config.settings") as mock_settings:
            mock_mcp = MagicMock()
            mock_mcp.enabled = True
            mock_mcp.transport = "invalid"
            mock_mcp.host = "0.0.0.0"
            mock_mcp.port = 9999
            mock_mcp.auth = MagicMock(enabled=True, api_keys=[])
            mock_mcp.tools = []
            mock_settings.mcp = mock_mcp

            import importlib
            import src.mcp.config as cfg_mod
            importlib.reload(cfg_mod)

            config = cfg_mod.load_mcp_config()
            assert config.transport == "sse"


# ---------------------------------------------------------------------------
# Tool Registry 测试
# ---------------------------------------------------------------------------

class TestMCPToolRegistry:

    def test_empty_config(self):
        """空配置时无工具"""
        from src.mcp.tools import MCPToolRegistry
        registry = MCPToolRegistry([])
        # discover_tools 是 async，需要手动 mock skill_registry
        assert len(registry) == 0

    @pytest.mark.asyncio
    async def test_discover_tools_skill_not_found(self):
        """引用不存在的 skill 时跳过"""
        from src.mcp.tools import MCPToolRegistry

        config = [{"skill": "nonexistent-skill", "commands": ["test-cmd"]}]

        with patch("src.mcp.tools.skill_registry") as mock_sr:
            mock_sr.get.return_value = None
            registry = MCPToolRegistry(config)
            await registry.discover_tools()
            assert len(registry) == 0

    @pytest.mark.asyncio
    async def test_discover_tools_with_script(self):
        """有匹配脚本时正确注册工具"""
        from src.mcp.tools import MCPToolRegistry

        mock_skill = MagicMock()
        mock_skill.name = "trade-customer"
        mock_skill.description = "客户管理"
        mock_skill.dir = Path("/fake/skills/trade-customer-1.0.0")
        script_path = mock_skill.dir / "scripts" / "save-customer.py"
        mock_skill.scripts = [script_path]

        config = [{"skill": "trade-customer", "commands": ["save-customer"]}]

        with patch("src.mcp.tools.skill_registry") as mock_sr:
            mock_sr.get.return_value = mock_skill
            registry = MCPToolRegistry(config)
            await registry.discover_tools()

            assert len(registry) == 1
            tool = registry.get_tool("save-customer")
            assert tool is not None
            assert tool.skill_name == "trade-customer"
            assert "save-customer" in tool.script_command

    @pytest.mark.asyncio
    async def test_to_mcp_tool_list(self):
        """to_mcp_tool_list 返回协议格式"""
        from src.mcp.tools import MCPToolRegistry

        mock_skill = MagicMock()
        mock_skill.name = "test-skill"
        mock_skill.description = "测试技能"
        mock_skill.dir = Path("/fake/skills/test-skill-1.0.0")
        mock_skill.scripts = []

        config = [{"skill": "test-skill", "commands": ["my-cmd"]}]

        with patch("src.mcp.tools.skill_registry") as mock_sr:
            mock_sr.get.return_value = mock_skill
            registry = MCPToolRegistry(config)
            await registry.discover_tools()

            tools_list = registry.to_mcp_tool_list()
            assert len(tools_list) == 1
            assert tools_list[0]["name"] == "my-cmd"
            assert "inputSchema" in tools_list[0]


# ---------------------------------------------------------------------------
# Executor 测试
# ---------------------------------------------------------------------------

class TestMCPExecutor:

    def test_authenticate_empty_keys(self):
        """空 key 列表跳过认证"""
        from src.mcp.executor import authenticate
        assert authenticate("any", []) is True

    def test_authenticate_valid_key(self):
        """合法 key 通过认证"""
        from src.mcp.executor import authenticate
        assert authenticate("secret123", ["secret123", "other"]) is True

    def test_authenticate_invalid_key(self):
        """非法 key 拒绝"""
        from src.mcp.executor import authenticate
        assert authenticate("wrong", ["secret123"]) is False

    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self):
        """工具不存在返回 isError"""
        from src.mcp.executor import MCPExecutor

        mock_registry = MagicMock()
        mock_registry.get_tool.return_value = None
        mock_executor = MagicMock()

        mcp_exec = MCPExecutor(mock_executor, mock_registry)
        result = await mcp_exec.execute_tool("nonexistent", {})

        assert result["isError"] is True
        assert "未找到" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_execute_tool_success(self):
        """成功执行返回 stdout"""
        from src.mcp.executor import MCPExecutor
        from src.mcp.tools import MCPToolDefinition
        from src.core.skill_executor import ExecutionResult

        tool_def = MCPToolDefinition(
            name="test-cmd",
            description="测试",
            input_schema={},
            skill_name="test-skill",
            script_command="python3 scripts/test.py '<JSON>'",
        )

        mock_registry = MagicMock()
        mock_registry.get_tool.return_value = tool_def

        mock_result = ExecutionResult(
            success=True,
            stdout='{"result": "ok"}',
            stderr="",
            exit_code=0,
            duration=0.5,
        )
        mock_skill_exec = MagicMock()
        mock_skill_exec.execute_skill_command = AsyncMock(return_value=mock_result)

        mcp_exec = MCPExecutor(mock_skill_exec, mock_registry)
        result = await mcp_exec.execute_tool(
            "test-cmd",
            {"params": '{"action": "test"}'},
            tenant_id="t1",
        )

        assert result["isError"] is False
        assert "ok" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_execute_tool_failure(self):
        """执行失败返回 isError"""
        from src.mcp.executor import MCPExecutor
        from src.mcp.tools import MCPToolDefinition
        from src.core.skill_executor import ExecutionResult

        tool_def = MCPToolDefinition(
            name="fail-cmd",
            description="测试失败",
            input_schema={},
            skill_name="test-skill",
            script_command="python3 scripts/fail.py",
        )

        mock_registry = MagicMock()
        mock_registry.get_tool.return_value = tool_def

        mock_result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Error: something failed",
            exit_code=1,
            duration=0.3,
        )
        mock_skill_exec = MagicMock()
        mock_skill_exec.execute_skill_command = AsyncMock(return_value=mock_result)

        mcp_exec = MCPExecutor(mock_skill_exec, mock_registry)
        result = await mcp_exec.execute_tool("fail-cmd", {})

        assert result["isError"] is True
        assert "failed" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Server 创建测试
# ---------------------------------------------------------------------------

class TestMCPServer:

    def test_create_server(self):
        """create_mcp_server 返回 FastMCP 实例"""
        with patch("src.mcp.server.mcp_config") as mock_config:
            mock_config.enabled = False
            mock_config.transport = "sse"
            mock_config.host = "0.0.0.0"
            mock_config.port = 8765
            mock_config.tools_config = []

            from src.mcp.server import create_mcp_server
            server = create_mcp_server(mock_config)
            assert server is not None
            assert hasattr(server, "_mcp_tool_registry")
            assert hasattr(server, "_mcp_executor")

    def test_run_server_disabled(self):
        """MCP 未启用时不启动"""
        with patch("src.mcp.server.mcp_config") as mock_config:
            mock_config.enabled = False

            from src.mcp.server import run_server
            # 不应抛异常
            run_server(mock_config)
