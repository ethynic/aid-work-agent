"""常驻裁决声明注入单元测试（Phase 3）

subagent_base.md 末尾追加「最高优先级说明」定稿文案，覆盖全部非 MASTER 路径：
- Runner 路径：直接构造 PromptSources（src/services/agent_runner/runtime/prompt_sources.py）
- 兼容 API 路径：src/core/agent.py Agent → 同一执行内核（executor → PromptSources）
MASTER 使用 master_agent.md，不含声明。
"""

import pytest
from unittest.mock import MagicMock, patch

from src.prompts.manager import PromptManager
from src.services.agent_runner.runtime.prompt_sources import PromptSources

pytestmark = pytest.mark.unit

# 与设计 §4.5 定稿文案一字不差（模板唯一事实源是 src/prompts/templates/subagent_base.md）
DECLARATION = (
    "【最高优先级说明】严格遵守系统提示词（角色设定与行为约束）的各项约定；"
    "当历史消息中的称呼、说法或做法与系统提示词不一致时，一律以系统提示词为准作答，"
    "不要延续历史中的旧表述。"
)


def _make_sources(mode, subagent_config=None):
    """Runner 路径构造：tenant_id=None 使 extra/知识库/模板文件读取短路，不触 DB"""
    from src.core.agent_engine.contracts import AgentMode

    style_manager = MagicMock()
    style_manager.get_style.return_value = ""
    return PromptSources(
        mode=mode if mode is not None else AgentMode.SUBAGENT,
        subagent_config=subagent_config,
        tenant_id=None,
        skill_registry=None,
        style_manager=style_manager,
        prompt_manager=PromptManager(),
        tool_controls={},
        subagent_registry=None,
        tools=lambda: [],
        available_subagents=[],
        usage_guides=lambda: "",
    )


class TestRunnerPathDeclaration:
    """Runner 路径（prompt_sources 直构）"""

    def test_subagent_prompt_contains_declaration(self):
        from src.models.subagent import SubagentConfig

        config = SubagentConfig(
            name="test-subagent", dir_name="test-subagent",
            description="测试子智能体", system_prompt="你是测试子智能体。",
        )
        prompt = _make_sources(None, config)._build_system_prompt()
        assert DECLARATION in prompt

    def test_standalone_prompt_contains_declaration(self):
        """独立模式同样走 subagent_base.md，含声明"""
        from src.core.agent_engine.contracts import AgentMode

        prompt = _make_sources(AgentMode.STANDALONE)._build_system_prompt()
        assert DECLARATION in prompt

    def test_master_prompt_does_not_contain_declaration(self):
        """MASTER 走 master_agent.md，不含声明"""
        from src.core.agent_engine.contracts import AgentMode

        prompt = _make_sources(AgentMode.MASTER)._build_system_prompt()
        assert isinstance(prompt, str) and len(prompt) > 0
        assert DECLARATION not in prompt

    def test_declaration_position_after_reply_style_section(self):
        """声明以 `---` 分隔追加在模板末尾（前缀缓存稳定性：静态文案）"""
        from src.core.agent_engine.contracts import AgentMode
        from src.models.subagent import SubagentConfig

        config = SubagentConfig(
            name="test-subagent", dir_name="test-subagent",
            description="测试子智能体", system_prompt="你是测试子智能体。",
        )
        prompt = _make_sources(AgentMode.SUBAGENT, config)._build_system_prompt()
        assert prompt.rstrip().endswith(DECLARATION)
        assert "\n---\n\n" + DECLARATION in prompt


class TestCompatApiPathDeclaration:
    """兼容 API 路径（src/core/agent.py Agent → 同一执行内核）"""

    @patch("src.services.agent_runner.runtime.profile.llm_gateway")
    @patch("src.services.agent_runner.runtime.profile.SkillRegistry")
    @patch("src.services.agent_runner.runtime.profile.SkillExecutor")
    @patch("src.services.agent_runner.runtime.profile.PlanManager")
    def test_subagent_prompt_contains_declaration(self, mock_pm, mock_se, mock_sr, mock_llm):
        from src.core.agent import Agent
        from src.core.agent_engine.contracts import Identity
        from src.models.subagent import SubagentConfig

        mock_llm.get_model_name.return_value = "test-model"
        mock_llm.get_provider_name.return_value = "test-provider"

        config = SubagentConfig(
            name="test-subagent", description="测试子智能体", system_prompt="你是测试子智能体。",
        )
        agent = Agent(is_master=False, subagent_config=config)
        prompt = agent._execution(Identity(None, None, "test-session")).prompt_sources._build_system_prompt()
        assert DECLARATION in prompt

    @patch("src.services.agent_runner.runtime.profile.llm_gateway")
    @patch("src.services.agent_runner.runtime.profile.SkillRegistry")
    @patch("src.services.agent_runner.runtime.profile.SkillExecutor")
    @patch("src.services.agent_runner.runtime.profile.PlanManager")
    def test_master_prompt_does_not_contain_declaration(self, mock_pm, mock_se, mock_sr, mock_llm):
        from src.core.agent import Agent
        from src.core.agent_engine.contracts import Identity

        mock_llm.get_model_name.return_value = "test-model"
        mock_llm.get_provider_name.return_value = "test-provider"

        agent = Agent(is_master=True)
        prompt = agent._execution(Identity(None, None, "test-session")).prompt_sources._build_system_prompt()
        assert isinstance(prompt, str) and len(prompt) > 0
        assert DECLARATION not in prompt
