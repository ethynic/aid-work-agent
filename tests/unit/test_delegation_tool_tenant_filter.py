"""
回归测试：_get_tools() 中 delegate_to_subagent 工具的子智能体列表必须按租户订阅过滤。

背景：
- src/core/agent.py 的 _build_system_prompt() 已经按租户过滤了可用子智能体（用于系统提示词文本）
- 但 _get_tools()（生成 LLM function calling schema）之前未传 available_subagents，
  导致 delegate_to_subagent 工具的 enum 和 description 包含所有子智能体，
  租户/用户能看到无权使用的智能体。

本测试验证修复后 _get_tools() 与 _build_system_prompt() 使用同一份过滤逻辑。
"""

import threading
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

from src.core.agent import Agent, AgentMode
from src.subagents.registry import SubagentRegistry
from src.tools.registry import ToolRegistry
from src.core.skill_registry import SkillRegistry
from src.tools.control_set import ControlToolDependencies, ToolControlSet


def _make_subagent_config(name: str, dir_name: str, description: str):
    """构造 SubagentConfig（仅含 _get_tools 过滤需要的关键字段）"""
    from src.models.subagent import SubagentConfig
    return SubagentConfig(
        name=name,
        dir_name=dir_name,
        description=description,
    )


def _build_minimal_master_agent(subagent_registry: SubagentRegistry) -> Agent:
    """构造一个最小可用的 MASTER Agent，跳过 __init__ 全部重组件。

    _get_tools() 依赖的 self 属性：
      - mode
      - tool_registry
      - _skill_execute_tool, _create_plan_tool, _clarify_tool
      - skill_registry
      - subagent_registry
    """
    agent = Agent.__new__(Agent)  # 绕过 __init__
    agent.mode = AgentMode.MASTER
    agent.tool_registry = ToolRegistry()
    agent.subagent_registry = subagent_registry
    agent.skill_registry = SkillRegistry()
    agent._skill_execute_tool = None
    agent._create_plan_tool = None
    agent._clarify_tool = None
    agent._tool_controls = ToolControlSet(ControlToolDependencies(
        plan_manager=MagicMock(),
        skill_registry=agent.skill_registry,
        skill_executor=MagicMock(),
        tool_registry=agent.tool_registry,
        subagent_registry=subagent_registry,
        allow_delegate=True,
    ))
    agent._tool_controls.bind_delegate(
        subagent_registry=subagent_registry,
        subagent_executor=MagicMock(),
    )
    return agent


def _register_subagents(registry: SubagentRegistry, items):
    """直接给注册表注入 SubagentConfig，模拟 loader 行为（key=dir_name）"""
    for cfg in items:
        registry._configs[cfg.dir_name or cfg.name] = cfg
    registry._builtin_names.update(registry._configs.keys())
    registry._build_indices()


def _extract_delegate_tool(tools):
    for t in tools:
        if t.get("name") == "delegate_to_subagent":
            return t
    return None


class TestDelegationToolTenantFilter(unittest.TestCase):
    """_get_tools() 中 delegate_to_subagent 工具的子智能体列表必须按租户订阅过滤"""

    def setUp(self):
        # 构造注册表：3 个内置子智能体（不同 dir_name 用于租户订阅匹配）
        self.registry = SubagentRegistry()
        _register_subagents(self.registry, [
            _make_subagent_config("旅游咨询顾问", "travel-advisor", "旅游行业 AI 顾问"),
            _make_subagent_config("外贸获客智能体", "trade-specialist", "海外潜在客户获取"),
            _make_subagent_config("全筑合同归档自动化审核", "contract-audit", "合同审核"),
        ])
        # 预先 patch settings 为 MagicMock，避免每个 with patch 重复；
        # 显式置 encryption_key=None，防止 subscription_db import 时
        # EncryptionManager(MagicMock) 触发 Fernet → base64 报错
        self._settings_patcher = patch("src.config.settings.settings")
        self.mock_settings = self._settings_patcher.start()
        self.mock_settings.encryption_key = None
        self.addCleanup(self._settings_patcher.stop)

        # 预先 patch get_db_connection，避免 SaaS 分支触发 PostgreSQL 连接池初始化
        # （测试中 SubscriptionDB.get_allowed_subagent_types 已被各 SaaS 测试单独 mock）
        self._db_patcher = patch("src.db.database.get_db_connection")
        mock_conn_cm = self._db_patcher.start()
        mock_conn_cm.return_value.__enter__.return_value = MagicMock()
        mock_conn_cm.return_value.__exit__.return_value = None
        self.addCleanup(self._db_patcher.stop)

    def test_get_tools_without_filter_includes_all_subagents(self):
        """【基线】无租户上下文（platform_admin 全局视图/后台调用）：委派工具列出全部子智能体"""
        agent = _build_minimal_master_agent(self.registry)

        # 无租户：available_subagents 为 None → 委派工具使用全部注册子智能体
        with patch("src.saas.context.get_current_tenant_id", return_value=None):
            tools = agent._get_tools()

        delegate = _extract_delegate_tool(tools)
        self.assertIsNotNone(delegate, "MASTER 模式必须有 delegate_to_subagent 工具")
        enum = delegate["input_schema"]["properties"]["subagent_name"]["enum"]
        # enum 取值为 dir_name（agent_id）
        self.assertEqual(set(enum), {"travel-advisor", "trade-specialist", "contract-audit"})

    def test_get_tools_filters_by_tenant_subscription_in_saas_mode(self):
        """【核心修复】租户上下文：委派工具的 enum 必须只包含租户订阅的子智能体"""
        agent = _build_minimal_master_agent(self.registry)

        # 模拟租户 tenant_001 只订阅了 travel-advisor
        with patch("src.saas.context.get_current_tenant_id", return_value="tenant_001"):
            with patch(
                "src.saas.db.subscription_db.SubscriptionDB.get_allowed_subagent_types",
                return_value=["travel-advisor"],
            ) as mock_get_allowed:
                tools = agent._get_tools()
                # 必须查询过租户订阅
                mock_get_allowed.assert_called_once()

        delegate = _extract_delegate_tool(tools)
        self.assertIsNotNone(delegate, "MASTER 模式必须有 delegate_to_subagent 工具")
        enum = delegate["input_schema"]["properties"]["subagent_name"]["enum"]

        # 关键断言：未订阅的子智能体（外贸、合同）不应出现在 enum 中
        self.assertIn("travel-advisor", enum)
        self.assertNotIn("trade-specialist", enum)
        self.assertNotIn("contract-audit", enum)
        self.assertEqual(enum, ["travel-advisor"])

    def test_get_tools_filters_description_text_too(self):
        """description 文本（可用的子智能体说明）也必须只列出租户订阅的子智能体"""
        agent = _build_minimal_master_agent(self.registry)

        with patch("src.saas.context.get_current_tenant_id", return_value="tenant_001"):
            with patch(
                "src.saas.db.subscription_db.SubscriptionDB.get_allowed_subagent_types",
                return_value=["travel-advisor"],
            ):
                tools = agent._get_tools()

        delegate = _extract_delegate_tool(tools)
        self.assertIsNotNone(delegate)
        desc = delegate["description"]

        # 描述文本展示显示名（+ agent_id），未订阅的不出现
        self.assertIn("旅游咨询顾问", desc)
        self.assertIn("travel-advisor", desc)
        self.assertNotIn("外贸获客智能体", desc)
        self.assertNotIn("全筑合同归档自动化审核", desc)

    def test_subagent_registry_direct_call_filters_correctly(self):
        """【回归保护】SubagentRegistry.get_delegation_tool_definition 直接传 available_subagents 的行为
        （available_subagents 是注册表 _configs 的 key，即 dir_name/agent_id）"""
        tool = self.registry.get_delegation_tool_definition(["travel-advisor"])

        self.assertIsNotNone(tool)
        enum = tool["input_schema"]["properties"]["subagent_name"]["enum"]
        self.assertEqual(enum, ["travel-advisor"])
        self.assertIn("旅游咨询顾问", tool["description"])
        self.assertIn("travel-advisor", tool["description"])
        self.assertNotIn("外贸获客智能体", tool["description"])

    def test_tenant_subscription_query_is_cached_across_calls(self):
        """_get_tools 在 Agent 主循环中每轮都被调用，必须缓存订阅查询，避免每轮查 DB"""
        agent = _build_minimal_master_agent(self.registry)

        with patch("src.saas.context.get_current_tenant_id", return_value="tenant_001"):
            with patch(
                "src.saas.db.subscription_db.SubscriptionDB.get_allowed_subagent_types",
                return_value=["travel-advisor"],
            ) as mock_get_allowed:
                # 多次调用 _get_tools 模拟 Agent 循环
                for _ in range(5):
                    agent._get_tools()

                # 不论缓存与否，订阅查询次数应该远小于 5
                self.assertLess(
                    mock_get_allowed.call_count,
                    5,
                    f"_get_tools 重复调用 5 次不应触发 5 次 DB 查询（实际 {mock_get_allowed.call_count} 次）",
                )

    def test_tenant_subscription_query_is_invalidated_when_tenant_changes(self):
        """切换租户上下文后，必须重新查询订阅列表"""
        agent = _build_minimal_master_agent(self.registry)

        with patch("src.saas.context.get_current_tenant_id", return_value="tenant_001"):
            with patch(
                "src.saas.db.subscription_db.SubscriptionDB.get_allowed_subagent_types",
                return_value=["travel-advisor"],
            ) as mock_get_allowed:
                agent._get_tools()
                first_tenant_calls = mock_get_allowed.call_count
                self.assertEqual(first_tenant_calls, 1)

        # 切换到不同租户
        with patch("src.saas.context.get_current_tenant_id", return_value="tenant_002"):
            with patch(
                "src.saas.db.subscription_db.SubscriptionDB.get_allowed_subagent_types",
                return_value=["trade-specialist"],
            ) as mock_get_allowed_2:
                tools = agent._get_tools()
                mock_get_allowed_2.assert_called_once()
                enum = _extract_delegate_tool(tools)["input_schema"]["properties"]["subagent_name"]["enum"]
                self.assertEqual(enum, ["trade-specialist"])


@pytest.mark.asyncio
async def test_subscription_visibility_query_is_primed_off_event_loop():
    registry = SubagentRegistry()
    _register_subagents(registry, [
        _make_subagent_config("旅游咨询顾问", "travel-advisor", "旅游行业 AI 顾问"),
    ])
    agent = _build_minimal_master_agent(registry)
    event_loop_thread = threading.get_ident()
    query_threads = []

    def get_allowed(conn, tenant_id):
        query_threads.append(threading.get_ident())
        return ["travel-advisor"]

    with (
        patch("src.saas.context.get_current_tenant_id", return_value="tenant_001"),
        patch("src.db.database.get_db_connection") as get_connection,
        patch(
            "src.saas.db.subscription_db.SubscriptionDB.get_allowed_subagent_types",
            side_effect=get_allowed,
        ),
    ):
        get_connection.return_value.__enter__.return_value = MagicMock()
        await agent._prime_available_subagents_cache()
        tools = agent._get_tools()

    assert query_threads and query_threads[0] != event_loop_thread
    assert _extract_delegate_tool(tools) is not None


if __name__ == "__main__":
    unittest.main()
