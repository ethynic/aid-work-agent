"""
回归测试：委派工具（delegate_to_subagent）的子智能体列表必须按租户订阅过滤。

33f9dba1 重构后的接入点（本文件据此移植）：
- 旧 Agent._get_tools 已不存在；现行接缝为 runtime ToolCatalog._get_tools()
  （MASTER 模式取 visibility.available() 作为 available_subagents 传给
  ToolControlSet.definitions）；
- 租户过滤逻辑落在 ProfileResolver._resolve_available_subagents
  （runtime/profile_resolver.py，tenant_id 来自执行 identity，每执行一个实例，
  _available 进程内缓存于实例上）；
- 系统提示词侧（prompt_sources）与工具 schema 侧共用同一份过滤结果，
  保持「租户/用户看不到未订阅智能体」的原始回归目标。
"""

import threading
import unittest
from unittest.mock import MagicMock, patch

import pytest

from src.core.agent_engine.contracts import AgentMode
from src.core.skill_registry import SkillRegistry
from src.services.agent_runner.runtime.profile_resolver import ProfileResolver
from src.services.agent_runner.runtime.tool_catalog import ToolCatalog
from src.subagents.registry import SubagentRegistry
from src.tools.control_set import ControlToolDependencies, ToolControlSet
from src.tools.registry import ToolRegistry


def _make_subagent_config(name: str, dir_name: str, description: str):
    """构造 SubagentConfig（仅含过滤链需要的关键字段）"""
    from src.models.subagent import SubagentConfig
    return SubagentConfig(
        name=name,
        dir_name=dir_name,
        description=description,
    )


def _register_subagents(registry: SubagentRegistry, items):
    """直接给注册表注入 SubagentConfig，模拟 loader 行为（key=dir_name）"""
    for cfg in items:
        registry._configs[cfg.dir_name or cfg.name] = cfg
    registry._builtin_names.update(registry._configs.keys())
    registry._build_indices()


def _build_catalog(subagent_registry: SubagentRegistry, tenant_id=None) -> ToolCatalog:
    """真实 ToolCatalog + 真实 ToolControlSet + 真实 ProfileResolver（现行接缝）。"""
    tool_registry = ToolRegistry()
    controls = ToolControlSet(ControlToolDependencies(
        plan_manager=MagicMock(),
        skill_registry=SkillRegistry(),
        skill_executor=MagicMock(),
        tool_registry=tool_registry,
        subagent_registry=subagent_registry,
        allow_delegate=True,
    ))
    controls.bind_delegate(
        subagent_registry=subagent_registry,
        subagent_executor=MagicMock(),
    )
    visibility = ProfileResolver(subagent_registry, tenant_id)
    return ToolCatalog(AgentMode.MASTER, tool_registry, controls, visibility)


def _extract_delegate_tool(tools):
    for t in tools:
        if t.get("name") == "delegate_to_subagent":
            return t
    return None


class TestDelegationToolTenantFilter(unittest.TestCase):
    """ToolCatalog._get_tools() 中 delegate_to_subagent 的子智能体列表按租户订阅过滤"""

    def setUp(self):
        # 构造注册表：3 个内置子智能体（不同 dir_name 用于租户订阅匹配）
        self.registry = SubagentRegistry()
        _register_subagents(self.registry, [
            _make_subagent_config("旅游咨询顾问", "travel-advisor", "旅游行业 AI 顾问"),
            _make_subagent_config("外贸获客智能体", "trade-specialist", "海外潜在客户获取"),
            _make_subagent_config("全筑合同归档自动化审核", "contract-audit", "合同审核"),
        ])
        # 预先 patch settings 为 MagicMock，避免 import 链触发真实配置；
        # 显式置 encryption_key=None，防止 subscription_db import 时
        # EncryptionManager(MagicMock) 触发 Fernet → base64 报错
        self._settings_patcher = patch("src.config.settings.settings")
        self.mock_settings = self._settings_patcher.start()
        self.mock_settings.encryption_key = None
        self.addCleanup(self._settings_patcher.stop)

        # 预先 patch get_db_connection，避免 SaaS 分支触发 PostgreSQL 连接池初始化
        # （测试中 SubscriptionDB.get_allowed_subagent_types 已被各用例单独 mock）
        self._db_patcher = patch("src.db.database.get_db_connection")
        mock_conn_cm = self._db_patcher.start()
        mock_conn_cm.return_value.__enter__.return_value = MagicMock()
        mock_conn_cm.return_value.__exit__.return_value = None
        self.addCleanup(self._db_patcher.stop)

    def test_get_tools_without_tenant_includes_all_subagents(self):
        """【基线】无租户上下文（platform_admin 全局视图/后台调用）：委派工具列出全部子智能体"""
        catalog = _build_catalog(self.registry, tenant_id=None)

        tools = catalog._get_tools()

        delegate = _extract_delegate_tool(tools)
        self.assertIsNotNone(delegate, "MASTER 模式必须有 delegate_to_subagent 工具")
        enum = delegate["input_schema"]["properties"]["subagent_name"]["enum"]
        # enum 取值为 dir_name（agent_id）
        self.assertEqual(set(enum), {"travel-advisor", "trade-specialist", "contract-audit"})

    def test_get_tools_filters_by_tenant_subscription(self):
        """【核心回归】租户上下文：委派工具的 enum 只包含租户订阅的子智能体"""
        catalog = _build_catalog(self.registry, tenant_id="tenant_001")

        with patch(
            "src.saas.db.subscription_db.SubscriptionDB.get_allowed_subagent_types",
            return_value=["travel-advisor"],
        ) as mock_get_allowed:
            tools = catalog._get_tools()
            # 必须查询过租户订阅
            mock_get_allowed.assert_called_once()

        delegate = _extract_delegate_tool(tools)
        self.assertIsNotNone(delegate, "MASTER 模式必须有 delegate_to_subagent 工具")
        enum = delegate["input_schema"]["properties"]["subagent_name"]["enum"]

        # 关键断言：未订阅的子智能体（外贸、合同）不应出现在 enum 中
        self.assertEqual(enum, ["travel-advisor"])

    def test_get_tools_filters_description_text_too(self):
        """description 文本（可用的子智能体说明）也只列出租户订阅的子智能体"""
        catalog = _build_catalog(self.registry, tenant_id="tenant_001")

        with patch(
            "src.saas.db.subscription_db.SubscriptionDB.get_allowed_subagent_types",
            return_value=["travel-advisor"],
        ):
            tools = catalog._get_tools()

        delegate = _extract_delegate_tool(tools)
        self.assertIsNotNone(delegate)
        desc = delegate["description"]

        # 描述文本展示显示名（+ agent_id），未订阅的不出现
        self.assertIn("旅游咨询顾问", desc)
        self.assertIn("travel-advisor", desc)
        self.assertNotIn("外贸获客智能体", desc)
        self.assertNotIn("全筑合同归档自动化审核", desc)

    def test_tenant_subscription_query_is_cached_on_resolver(self):
        """_get_tools 在主循环中每轮调用，订阅查询在 ProfileResolver 实例上缓存（一次）"""
        catalog = _build_catalog(self.registry, tenant_id="tenant_001")

        with patch(
            "src.saas.db.subscription_db.SubscriptionDB.get_allowed_subagent_types",
            return_value=["travel-advisor"],
        ) as mock_get_allowed:
            for _ in range(5):
                catalog._get_tools()

            self.assertEqual(
                mock_get_allowed.call_count, 1,
                f"重复调用 _get_tools 不应重复查 DB（实际 {mock_get_allowed.call_count} 次）",
            )

    def test_different_tenant_uses_its_own_subscription(self):
        """不同租户的执行各有独立 ProfileResolver（tenant 来自 identity），
        订阅结果互不串扰——对应旧「切租户必须重查」的语义（结构上保证）。"""
        catalog_a = _build_catalog(self.registry, tenant_id="tenant_001")
        catalog_b = _build_catalog(self.registry, tenant_id="tenant_002")

        with patch(
            "src.saas.db.subscription_db.SubscriptionDB.get_allowed_subagent_types",
            side_effect=lambda conn, tenant: {
                "tenant_001": ["travel-advisor"],
                "tenant_002": ["trade-specialist"],
            }[tenant],
        ):
            enum_a = _extract_delegate_tool(catalog_a._get_tools())["input_schema"]["properties"]["subagent_name"]["enum"]
            enum_b = _extract_delegate_tool(catalog_b._get_tools())["input_schema"]["properties"]["subagent_name"]["enum"]

        self.assertEqual(enum_a, ["travel-advisor"])
        self.assertEqual(enum_b, ["trade-specialist"])

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


@pytest.mark.asyncio
async def test_subscription_visibility_query_is_primed_off_event_loop():
    """ProfileResolver.prime 经 to_thread 预热，DB 查询不占事件循环线程。"""
    registry = SubagentRegistry()
    _register_subagents(registry, [
        _make_subagent_config("旅游咨询顾问", "travel-advisor", "旅游行业 AI 顾问"),
    ])
    event_loop_thread = threading.get_ident()
    query_threads = []

    def get_allowed(conn, tenant_id):
        query_threads.append(threading.get_ident())
        return ["travel-advisor"]

    with (
        patch("src.db.database.get_db_connection") as get_connection,
        patch(
            "src.saas.db.subscription_db.SubscriptionDB.get_allowed_subagent_types",
            side_effect=get_allowed,
        ),
    ):
        get_connection.return_value.__enter__.return_value = MagicMock()
        resolver = ProfileResolver(registry, "tenant_001")
        await resolver.prime()
        available = resolver.available()

    assert query_threads and query_threads[0] != event_loop_thread
    assert available == ["travel-advisor"]


if __name__ == "__main__":
    unittest.main()
