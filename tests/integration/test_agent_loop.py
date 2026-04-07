"""
集成测试：Agent 消息处理循环

使用 mock LLM，但真实 ToolRegistry + ToolExecutor + Memory
"""

import pytest

pytestmark = pytest.mark.agent
from unittest.mock import AsyncMock, MagicMock, patch

from src.memory.short_term import ShortTermMemory
from src.tools.registry import ToolRegistry
from src.tools.executor import ToolExecutor


class TestAgentLoopBasic:
    """Agent 循环基础测试"""

    @patch("src.core.agent.llm_gateway")
    @patch("src.core.agent.SkillRegistry")
    @patch("src.core.agent.SkillExecutor")
    @patch("src.core.agent.PlanManager")
    def test_agent_initialization_master_mode(self, mock_pm, mock_se, mock_sr, mock_llm):
        """测试 Master Agent 初始化"""
        from src.core.agent import Agent

        mock_llm.get_model_name.return_value = "test-model"
        mock_llm.get_provider_name.return_value = "test-provider"

        agent = Agent(is_master=True)
        assert agent.mode.value == "master"

    @patch("src.core.agent.llm_gateway")
    @patch("src.core.agent.SkillRegistry")
    @patch("src.core.agent.SkillExecutor")
    @patch("src.core.agent.PlanManager")
    def test_agent_initialization_subagent_mode(self, mock_pm, mock_se, mock_sr, mock_llm):
        """测试 SubAgent 初始化"""
        from src.core.agent import Agent, AgentMode
        from src.models.subagent import SubagentConfig

        mock_llm.get_model_name.return_value = "test-model"
        mock_llm.get_provider_name.return_value = "test-provider"

        config = SubagentConfig(
            name="test-subagent",
            description="测试子智能体",
        )
        agent = Agent(is_master=False, subagent_config=config)
        assert agent.mode == AgentMode.SUBAGENT


class TestAgentBuildSystemPrompt:
    """Agent system prompt 构建"""

    @patch("src.core.agent.llm_gateway")
    @patch("src.core.agent.SkillRegistry")
    @patch("src.core.agent.SkillExecutor")
    @patch("src.core.agent.PlanManager")
    def test_master_prompt_contains_delegation(self, mock_pm, mock_se, mock_sr, mock_llm):
        """Master Agent 的 system prompt 应包含委派指令"""
        from src.core.agent import Agent

        mock_llm.get_model_name.return_value = "test-model"
        mock_llm.get_provider_name.return_value = "test-provider"

        agent = Agent(is_master=True)
        prompt = agent._build_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    @patch("src.core.agent.llm_gateway")
    @patch("src.core.agent.SkillRegistry")
    @patch("src.core.agent.SkillExecutor")
    @patch("src.core.agent.PlanManager")
    def test_subagent_prompt_from_config(self, mock_pm, mock_se, mock_sr, mock_llm):
        """SubAgent 的 system prompt 应来自 SubagentConfig"""
        from src.core.agent import Agent
        from src.models.subagent import SubagentConfig

        mock_llm.get_model_name.return_value = "test-model"
        mock_llm.get_provider_name.return_value = "test-provider"

        config = SubagentConfig(
            name="test-subagent",
            description="测试子智能体",
            system_prompt="你是测试子智能体。",
        )
        agent = Agent(is_master=False, subagent_config=config)
        prompt = agent._build_system_prompt()
        assert "测试子智能体" in prompt
