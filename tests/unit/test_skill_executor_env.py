"""
skill_executor._execute_command 子进程环境变量注入测试（Excel ETL Phase 2 计量接线）

验证：tool_execution_scope 安装的请求级上下文（tenant/session/user）透传为
子进程 env 的 AID_TENANT_ID / AID_SESSION_ID / AID_USER_ID；上下文缺失或字段
为 None 时不设置对应变量。mock asyncio.create_subprocess_shell 捕获 env，
不真正起子进程。
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.skill_executor import SkillExecutor
from src.tools.context import ToolExecutionContext, tool_execution_scope

pytestmark = [pytest.mark.skills]


@pytest.fixture(autouse=True)
def _clean_aid_env(monkeypatch):
    for name in ("AID_TENANT_ID", "AID_SESSION_ID", "AID_USER_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def captured_env(monkeypatch, tmp_path):
    """mock create_subprocess_shell，捕获传给子进程的 env"""
    holder = {}

    def _fake_create_shell(command, **kwargs):
        holder["command"] = command
        holder["env"] = kwargs.get("env")
        process = MagicMock()
        process.returncode = 0
        process.communicate = AsyncMock(return_value=(b"ok", b""))
        fut = asyncio.get_event_loop().create_future()
        fut.set_result(process)
        return fut

    monkeypatch.setattr(
        "src.core.skill_executor.asyncio.create_subprocess_shell", _fake_create_shell
    )
    return holder


def _executor(tmp_path):
    return SkillExecutor(skill_registry=MagicMock(), workspace=Path(tmp_path))


def _run(command_executor, workdir):
    return asyncio.run(command_executor._execute_command("echo hi", Path(workdir)))


class TestSkillExecutorAidEnv:
    def test_full_context_injected(self, captured_env, tmp_path):
        ctx = ToolExecutionContext(
            tenant_id="tenant-1", user_id="user-1", session_id="sess-1",
        )
        with tool_execution_scope(ctx):
            result = _run(_executor(tmp_path), tmp_path)
        assert result.success is True
        env = captured_env["env"]
        assert env["AID_TENANT_ID"] == "tenant-1"
        assert env["AID_SESSION_ID"] == "sess-1"
        assert env["AID_USER_ID"] == "user-1"

    def test_no_context_no_aid_vars(self, captured_env, tmp_path):
        # 无 tool_execution_scope（后台调度等场景）：不设置 AID_*，其余环境仍继承
        result = _run(_executor(tmp_path), tmp_path)
        assert result.success is True
        env = captured_env["env"]
        assert "AID_TENANT_ID" not in env
        assert "AID_SESSION_ID" not in env
        assert "AID_USER_ID" not in env
        assert env["PYTHONIOENCODING"] == "utf-8"  # 既有注入不受影响

    def test_partial_context_only_set_fields(self, captured_env, tmp_path):
        ctx = ToolExecutionContext(tenant_id="tenant-only")
        with tool_execution_scope(ctx):
            _run(_executor(tmp_path), tmp_path)
        env = captured_env["env"]
        assert env["AID_TENANT_ID"] == "tenant-only"
        assert "AID_SESSION_ID" not in env
        assert "AID_USER_ID" not in env

    def test_env_extra_still_applied(self, captured_env, tmp_path):
        ctx = ToolExecutionContext(tenant_id="t", user_id="u", session_id="s")
        with tool_execution_scope(ctx):
            asyncio.run(_executor(tmp_path)._execute_command(
                "echo hi", Path(tmp_path), env_extra={"SKILL_LLM_PROVIDER": "deepseek"},
            ))
        env = captured_env["env"]
        assert env["SKILL_LLM_PROVIDER"] == "deepseek"
        assert env["AID_TENANT_ID"] == "t"
