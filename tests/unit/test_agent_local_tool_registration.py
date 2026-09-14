"""Agent 本地工具注册可见性 + recruiting-operator SUBAGENT.md 加载测试

覆盖（对应 m05-implementation-spec.md §7）：
- master agent 注册表无 boss 工具
- inherit=true 子智能体无 boss 工具
- recruiting-operator 配置下 18 个 boss 工具齐全且只有这 18 个
  （Phase 3 新增 boss_list_jobs / boss_select_job / boss_jobs_list；
  面试通知 Phase 1 新增 boss_interview_notify；
  boss-cli 0.2.4 新增沟通会话只读能力 boss_read_chat / boss_open_chat）
- SUBAGENT.md 加载：frontmatter 解析正确、system_prompt 取 body
  （防 architecture.md 记录的「frontmatter 未闭合导致静默不加载」陷阱）
"""

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

SUBAGENTS_DIR = Path(__file__).parent.parent.parent / "subagents"

from src.local_tools.manifest import LOCAL_PROXY_TOOL_NAMES as _MANIFEST_NAMES


def test_manifest_matches_proxy_tool_classes():
    """manifest.py 静态清单必须与 LOCAL_PROXY_TOOL_CLASSES 完全一致——
    新增代理工具漏更 manifest 会导致装配（tools/assembly.py）不注册（2026-08-19 合并时踩过）"""
    from src.local_tools.proxy_tool import LOCAL_PROXY_TOOL_CLASSES
    assert _MANIFEST_NAMES == {c.name for c in LOCAL_PROXY_TOOL_CLASSES}


BOSS_TOOLS = {
    "boss_filter",
    "boss_clear_filter",
    "boss_goto",
    "boss_greet",
    "boss_accept_resume",
    "boss_reject_current",
    "boss_interview_demo",
    "boss_interview_notify",
    "boss_list_jobs",
    "boss_select_job",
    "boss_jobs_list",
    "boss_resume_detail",
    "boss_resume_batch",
    "boss_send_to",
    "boss_send_current",
    "boss_filter_options",
    "boss_read_chat",
    "boss_open_chat",
}


def _make_agent(is_master: bool, config=None):
    """构造 Agent（patch LLM/Skill/Plan 依赖，与 test_agent_loop.py 同一模式）"""
    with patch("src.core.agent.llm_gateway") as mock_llm, \
         patch("src.core.agent.SkillRegistry"), \
         patch("src.core.agent.SkillExecutor"), \
         patch("src.core.agent.PlanManager"):
        mock_llm.get_model_name.return_value = "test-model"
        mock_llm.get_provider_name.return_value = "test-provider"
        from src.core.agent import Agent
        return Agent(is_master=is_master, subagent_config=config)


def _load_recruiting_config():
    from src.subagents.loader import SubagentLoader
    loader = SubagentLoader(SUBAGENTS_DIR)
    config = loader.get("recruiting-operator")
    assert config is not None, "recruiting-operator SUBAGENT.md 未被加载（检查 frontmatter 闭合）"
    return config


class TestLocalToolVisibility:
    def test_master_has_no_boss_tools(self):
        """主智能体注册表不含任何 boss_* 工具（设计 §11）"""
        agent = _make_agent(is_master=True)
        assert not (set(agent.tool_registry._tools.keys()) & BOSS_TOOLS)

    def test_inherit_true_subagent_has_no_boss_tools(self):
        """inherit=true 的子智能体不获得 boss 工具"""
        from src.models.subagent import SubagentConfig
        config = SubagentConfig(name="测试子智能体", tools={"inherit": True})
        agent = _make_agent(is_master=False, config=config)
        assert not (set(agent.tool_registry._tools.keys()) & BOSS_TOOLS)

    def test_unrelated_allowed_subagent_has_no_boss_tools(self):
        """allowed 与 boss 无交集的子智能体不注册 boss 工具"""
        from src.models.subagent import SubagentConfig
        config = SubagentConfig(
            name="测试子智能体",
            tools={"inherit": False, "allowed": ["web_search"]},
        )
        agent = _make_agent(is_master=False, config=config)
        assert not (set(agent.tool_registry._tools.keys()) & BOSS_TOOLS)

    def test_recruiting_operator_has_exactly_eighteen_boss_tools(self):
        """recruiting-operator 配置下：18 个 boss 工具齐全且只有这 18 个

        boss_jobs_list / boss_interview_notify 为混合模式（云端执行逻辑 + 代理注册），同样以 LOCAL_REQUIRED 注册
        """
        config = _load_recruiting_config()
        agent = _make_agent(is_master=False, config=config)
        assert set(agent.tool_registry._tools.keys()) == BOSS_TOOLS
        for tool in agent.tool_registry._tools.values():
            from src.tools.base import ExecutionTarget
            assert tool.execution_target == ExecutionTarget.LOCAL_REQUIRED


class TestRecruitingSubagentMd:
    def test_frontmatter_and_body_parsed(self):
        """frontmatter 解析正确，system_prompt 取 body（非空、含正文、不含 YAML 键）"""
        config = _load_recruiting_config()
        assert config.dir_name == "recruiting-operator"
        assert config.tools.get("inherit") is False
        assert set(config.get_allowed_tools()) == BOSS_TOOLS
        assert config.system_prompt, "system_prompt 为空（body 未被采用）"
        assert "授权规则" in config.system_prompt
        assert "capabilities:" not in config.system_prompt  # 证明是 body 而非 frontmatter 串入

    def test_registry_lookup_by_dir_name(self):
        """SubagentRegistry 支持按 dir_name（URL 路由 /chat/recruiting-operator）查找"""
        from src.subagents.registry import SubagentRegistry
        registry = SubagentRegistry(SUBAGENTS_DIR)
        config = registry.get("recruiting-operator")
        assert config is not None
        assert config.name == "招聘操作智能体"
        # 委派工具定义中可被发现（master LLM 可见描述 + enum）
        delegation = registry.get_delegation_tool_definition()
        assert "recruiting-operator" in str(delegation) or "招聘操作智能体" in str(delegation)
