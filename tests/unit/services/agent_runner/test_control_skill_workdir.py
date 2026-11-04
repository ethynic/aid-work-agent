"""runner skill_execute workdir 注入与工具层校验协同（2026-11-04 trace tr_runner_a7392491 回归）。

事故：controls.py 曾无条件 `args.update(workdir=workspace)` 覆盖 LLM 显式传参，
叠加工具层 fail-closed 校验（e48fdc94），带附件工作区的 runner 执行中所有
skill_execute 一律被拒，LLM 循环重试直至 ITERATION_LIMIT（74.73 积分）。

契约：
- LLM 显式传入的 workdir 不被覆盖（setdefault 语义）；
- LLM 省略 workdir 时兜底注入执行工作区（state.resources['workspace']）；
- 工作区经 ToolExecutionContext.skill_workdir 服务端注入（derive 传入），
  工具层 _validate_workdir 据此放行前缀，LLM 不可伪造。
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.agent

from src.services.agent_runner.runtime.controls import ControlToolAdapter
from src.tools.context import ToolExecutionContext, current_tool_execution_context

WORKSPACE = "/app/storage/agent_runner/workspaces/exec-hash"


def _state(workspace=WORKSPACE):
    return SimpleNamespace(
        identity=SimpleNamespace(
            tenant_id="tenant_1", user_id="u1", session_id="s1",
            source="chat", session_kind="web"),
        resources={"workspace": workspace} if workspace else {},
    )


def _adapter(captured):
    async def _execute(**kwargs):
        captured.update(kwargs)
        captured["injected_context"] = current_tool_execution_context()
        return {"success": True, "stdout": "", "stderr": "", "exit_code": 0}

    return ControlToolAdapter(
        controls={"skill_execute": SimpleNamespace(execute=_execute)},
        skills=SimpleNamespace(check=lambda state, skill: None), registry=MagicMock(),
        plan_manager=MagicMock(), clarification_store=MagicMock(),
        context=ToolExecutionContext(tenant_id="tenant_1", user_id="u1", session_id="s1"),
        user_input="x")


async def _dispatch(adapter, state, args):
    call = SimpleNamespace(id="call-1", name="skill_execute", arguments=args)
    return [item async for item in adapter.execute(state, call)]


class TestSkillExecuteWorkdirInjection:
    @pytest.mark.asyncio
    async def test_explicit_workdir_not_clobbered(self):
        """LLM 显式传 workdir 时保留原值，不被 runner 工作区覆盖。"""
        captured = {}
        adapter = _adapter(captured)
        await _dispatch(adapter, _state(), {"skill": "demo", "command": "pwd", "workdir": "storage/tmp"})
        assert captured["workdir"] == "storage/tmp"
        # 服务端注入的允许目录仍然生效（用于工具层校验放行）
        assert captured["injected_context"].skill_workdir == WORKSPACE

    @pytest.mark.asyncio
    async def test_omitted_workdir_defaults_to_workspace(self):
        """LLM 省略 workdir 时兜底注入执行工作区（上传文件暂存处）。"""
        captured = {}
        adapter = _adapter(captured)
        await _dispatch(adapter, _state(), {"skill": "demo", "command": "pwd"})
        assert captured["workdir"] == WORKSPACE
        assert captured["injected_context"].skill_workdir == WORKSPACE

    @pytest.mark.asyncio
    async def test_llm_null_workdir_gets_workspace(self):
        """LLM 显式传 workdir=null 时同样兜底注入工作区（贴近旧行为）。"""
        captured = {}
        adapter = _adapter(captured)
        await _dispatch(adapter, _state(), {"skill": "demo", "command": "pwd", "workdir": None})
        assert captured["workdir"] == WORKSPACE

    @pytest.mark.asyncio
    async def test_no_workspace_keeps_workdir_absent(self):
        """无附件工作区时不注入 workdir（维持缺省临时目录行为）。"""
        captured = {}
        adapter = _adapter(captured)
        await _dispatch(adapter, _state(workspace=None), {"skill": "demo", "command": "pwd"})
        assert captured.get("workdir") is None
        assert captured["injected_context"].skill_workdir is None

    @pytest.mark.asyncio
    async def test_session_identity_always_overridden(self):
        """session/user 身份仍由服务端注入（与旧行为一致，不因 setdefault 弱化）。"""
        captured = {}
        adapter = _adapter(captured)
        await _dispatch(adapter, _state(), {"skill": "demo", "command": "pwd",
                                      "session_id": "llm-forged", "user_id": "llm-forged"})
        assert captured["session_id"] == "s1"
        assert captured["user_id"] == "u1"
