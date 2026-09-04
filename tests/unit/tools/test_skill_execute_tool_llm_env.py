"""
SkillExecuteTool 技能子进程 LLM 覆盖 env 透传测试

验证子智能体的 llm_provider / llm_model_codes 通过 env_extra 注入技能子进程，
供 travel-quote 等技能脚本的 llm_client 读取（与 agent.py 构造 SkillExecuteTool 的 llm_env 对应）。
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.skill.skill_execute_tool import SkillExecuteTool


def _fake_skill_registry():
    registry = MagicMock()
    skill = MagicMock()
    registry.get.return_value = skill
    return registry


class TestSkillExecuteToolLlmEnv:
    def test_llm_env_passed_to_execute_skill_command(self):
        registry = _fake_skill_registry()
        executor = MagicMock()
        executor.execute_skill_command = AsyncMock()

        tool = SkillExecuteTool(
            skill_executor=executor,
            skill_registry=registry,
            llm_env={
                "SKILL_LLM_PROVIDER": "deepseek",
                "SKILL_LLM_MODEL": "deepseek-v4-flash",
            },
        )

        import asyncio
        asyncio.run(tool.execute(
            skill="travel-quote",
            command="python scripts/generate.py",
            content=json.dumps({"itinerary_text": "test"}),
            session_id="test_session",
        ))

        executor.execute_skill_command.assert_awaited_once()
        kwargs = executor.execute_skill_command.await_args.kwargs
        assert kwargs["env_extra"] == {
            "SKILL_LLM_PROVIDER": "deepseek",
            "SKILL_LLM_MODEL": "deepseek-v4-flash",
        }

    def test_no_llm_env_defaults_empty(self):
        registry = _fake_skill_registry()
        executor = MagicMock()
        executor.execute_skill_command = AsyncMock()

        tool = SkillExecuteTool(
            skill_executor=executor,
            skill_registry=registry,
        )

        import asyncio
        asyncio.run(tool.execute(
            skill="travel-quote",
            command="python scripts/generate.py",
            session_id="test_session",
        ))

        kwargs = executor.execute_skill_command.await_args.kwargs
        assert kwargs["env_extra"] == {}


class TestSkillExecuteFilesStrTolerance:
    """files 参数传成 JSON 字符串时的边界容错（线上 qwen3.8-flash 传 '{}' 导致崩溃）"""

    def _run(self, files):
        registry = _fake_skill_registry()
        executor = MagicMock()
        executor.execute_skill_command = AsyncMock()
        tool = SkillExecuteTool(skill_executor=executor, skill_registry=registry)

        import asyncio
        asyncio.run(tool.execute(
            skill="pre-sales-api",
            command="python scripts/delegate_login.py",
            files=files,
            session_id="test_session",
        ))
        executor.execute_skill_command.assert_awaited_once()
        return executor.execute_skill_command.await_args.kwargs["files"]

    def test_files_empty_json_str_no_crash(self):
        # 线上事故场景：files='{}'（字符串），修复前 .items() 抛 AttributeError
        assert self._run("{}") is None

    def test_files_json_str_decoded(self):
        assert self._run('{"a.txt": "aGk="}') == {"a.txt": b"hi"}

    def test_files_invalid_str_treated_empty(self):
        assert self._run("not-json") is None

    def test_files_dict_still_works(self):
        assert self._run({"b.bin": "aGk="}) == {"b.bin": b"hi"}
