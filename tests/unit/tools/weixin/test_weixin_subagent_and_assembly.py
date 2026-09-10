"""微信营销子智能体接入冒烟测试（P3-B，R56）

覆盖三层接入：
1. SUBAGENT.md 可被 loader 解析（frontmatter 合法、body 即 system_prompt），
   dir_name 与目录名一致（/chat/weixin-marketing 路由匹配）；
2. 三工具经 Catalog 自动发现（discover_tool_classes 可见）；
3. 子智能体 allowlist 装配：以 SUBAGENT.md 声明的 tools.allowed 装配后，
   三专用工具在册、通用 create_scheduled_task 不在册（架构隔离）。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.tools

from src.tools.registry import discover_tool_classes

REPO_ROOT = Path(__file__).parents[4]
SUBAGENT_PATH = REPO_ROOT / "subagents" / "weixin-marketing" / "SUBAGENT.md"
WEIXIN_TOOLS = (
    "weixin_automation_prepare",
    "weixin_automation_publish",
    "weixin_automation_manage",
)


def _parse():
    from src.subagents.loader import SubagentLoader

    assert SUBAGENT_PATH.is_file(), f"缺少 {SUBAGENT_PATH}"
    # parse_subagent_md 是实例方法；不传目录避免触发全量加载
    return SubagentLoader().parse_subagent_md(SUBAGENT_PATH)


class TestSubagentMdSmoke:
    def test_frontmatter_parses_and_dir_name_matches(self):
        """frontmatter 可解析；loader 语义下 dir_name=目录名（weixin-marketing 路由）"""
        config = _parse()
        assert config is not None, "SUBAGENT.md 解析失败（frontmatter 或必需字段问题）"
        assert config.name == "微信营销智能体"
        assert config.description
        assert SUBAGENT_PATH.parent.name == "weixin-marketing"

    def test_tools_declares_dedicated_weixin_tools(self):
        config = _parse()
        allowed = config.tools.get("allowed") or []
        assert set(WEIXIN_TOOLS) <= set(allowed)
        assert config.tools.get("inherit") is False, "微信营销子智能体应白名单制，不继承全量工具"

    def test_system_prompt_body_covers_required_rules(self):
        """body 系统提示词声明：固定内容规则 / 授权边界 / unknown 处理（不宣称成功、转人工）"""
        prompt = _parse().system_prompt
        assert prompt, "system_prompt 必须来自 body（不得写在 frontmatter）"
        assert "固定内容" in prompt            # 固定内容规则
        assert "冻结不可变" in prompt          # 发布不可变
        assert "明确确认" in prompt            # 授权边界：发布/发送需用户明确确认
        assert "真实发送副作用" in prompt       # 试发与正式发均为真实副作用
        assert "不" in prompt and "宣称成功" in prompt   # unknown：不宣称成功
        assert "人工" in prompt                # 转人工核对
        # 不使用通用定时任务（路由约束的提示词侧防线）
        assert "create_scheduled_task" in prompt

    def test_load_all_registers_weixin_marketing(self):
        """真实 loader 全目录加载冒烟：weixin-marketing 出现在注册结果中"""
        from src.subagents.loader import SubagentLoader

        loader = SubagentLoader(REPO_ROOT / "subagents")
        configs = loader.load_all()
        matched = [c for c in configs.values() if c.dir_name == "weixin-marketing"]
        assert len(matched) == 1
        assert set(WEIXIN_TOOLS) <= set(matched[0].tools.get("allowed") or [])


class TestCatalogDiscovery:
    def test_weixin_tools_discovered_by_catalog(self):
        """三工具经 Catalog 自动发现（assembly 无需额外注册）"""
        discovered = discover_tool_classes()
        for name in WEIXIN_TOOLS:
            assert name in discovered, f"{name} 未进入工具自动发现目录"
            # 无参构造（discover 已校验，这里显式再证一次）
            assert discovered[name]().name == name


class TestSubagentAllowlistAssembly:
    def test_assembly_with_weixin_allowlist(self):
        """按 SUBAGENT.md 的 tools.allowed 装配子智能体工具集：专用工具在册、通用定时工具隔离"""
        from types import SimpleNamespace

        from src.models.subagent import SubagentConfig
        from src.tools.assembly import (
            ToolAssemblyRequest,
            ToolAssemblyRole,
            assemble_agent_tools,
        )

        config = _parse()
        subagent_config = SubagentConfig(
            name=config.name,
            description=config.description,
            tools=config.tools,
            skills=config.skills,
            system_prompt=config.system_prompt,
        )
        # loader 语义：dir_name 来自目录名
        object.__setattr__(subagent_config, "dir_name", "weixin-marketing")

        class FakeSkillRegistry:
            def get_skill_tool_definition(self):
                return {"name": "use_skill", "description": "", "input_schema": {}}

        request = ToolAssemblyRequest(
            role=ToolAssemblyRole.SUBAGENT,
            subagent_config=SimpleNamespace(
                name=subagent_config.name,
                tools=subagent_config.tools,
                get_allowed_tools=subagent_config.get_allowed_tools,
                get_excluded_tools=subagent_config.get_excluded_tools,
                llm_provider=None,
            ),
            plan_manager=object(),
            skill_registry=FakeSkillRegistry(),
            skill_executor=object(),
        )
        bundle = assemble_agent_tools(request)
        final = set(bundle.final_names)
        assert set(WEIXIN_TOOLS) <= final
        # 架构隔离：微信营销子智能体拿不到通用定时任务工具
        assert "create_scheduled_task" not in final
        assert "manage_scheduled_task" not in final
        # unknown 处理兜底：转人工工具在册
        assert "transfer_to_human" in final
