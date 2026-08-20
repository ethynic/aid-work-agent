"""工具自动发现注册黄金清单测试（docs/tools/tool-auto-discovery-design.md）

黄金清单基线取得方法（2026-08-19，HEAD e9af7173 + Phase 0 邮件工作区改动，
即 _register_builtin_tools 手工清单改造前的注册结果快照）：

    from types import SimpleNamespace
    from src.tools.registry import ToolRegistry
    from src.core.plan_manager import PlanManager
    from src.core.skill_registry import SkillRegistry
    from src.core.agent import Agent

    fake = SimpleNamespace(
        tool_registry=ToolRegistry(),
        plan_manager=PlanManager(),
        skill_registry=SkillRegistry(),
        skill_executor=None,
    )
    Agent._register_builtin_tools(fake)
    print(sorted(fake.tool_registry._tools.keys()))

改造后要求：26 个普通工具全部由 discover_tool_classes 自动发现，
工具名集合与该基线完全一致；控制工具仍以 catalog=False 排除。
新增可自动注册的工具时，应同步在 GOLDEN_TOOLS 中登记并在 PR 说明。
"""

import pytest

from src.tools.base import BaseTool, _CATALOG
from src.tools.registry import ToolRegistry, discover_tool_classes
# ============================================================
# 黄金清单：HEAD 手工注册清单的 26 个工具名（字母序）
# ============================================================
GOLDEN_TOOLS = [
    "ai_call",
    "analyze_data",
    "attraction_search",
    "browser_automation",
    "content_generate",
    "cp",
    "create_scheduled_task",
    "edit",
    "email_process",
    "excel_process",
    "grep",
    "hotel_search",
    "http_api",
    "knowledge_base_search",
    "manage_scheduled_task",
    "paddleocr_doc_parsing",
    "pdf_process",
    "ppt_process",
    "read",
    "submit_video_task",
    "transfer_to_human",
    "upload_data_file",
    "web_search",
    "word_process",
    "write",
    "x_to_image",
]

# Phase 4 后 26 个普通工具全部由 Catalog 自动发现。
SPECIAL_REGISTERED_TOOLS = set()

# 显式 catalog = False、绝不进 _CATALOG 的工具名
CATALOG_EXCLUDED_TOOLS = {
    "create_plan",
    "use_skill",
    "skill_execute",
    "clarify",
    "delegate_to_subagent",
    "speech_to_text",
}


def _assert_name_set_equal(actual: list, expected: list, label: str) -> None:
    """排序比较工具名集合，失败时输出差异，便于定位新增/遗漏工具。"""
    actual_sorted, expected_sorted = sorted(actual), sorted(expected)
    extra = sorted(set(actual_sorted) - set(expected_sorted))
    missing = sorted(set(expected_sorted) - set(actual_sorted))
    assert actual_sorted == expected_sorted, (
        f"{label} 不一致:\n"
        f"  意外多出（需加 catalog=False 或更新黄金清单并说明）: {extra}\n"
        f"  意外缺失（检查包 __init__ 导出/discover 遍历）: {missing}"
    )


class TestDiscoveryGoldenList:
    """断言一：discover 结果与 HEAD 基线一致"""

    def test_discovered_names_match_golden_auto_list(self):
        discovered = discover_tool_classes()
        expected_auto = sorted(set(GOLDEN_TOOLS) - SPECIAL_REGISTERED_TOOLS)
        _assert_name_set_equal(list(discovered.keys()), expected_auto, "discover 工具名集合")

    def test_discovered_classes_instantiate_with_matching_names(self):
        discovered = discover_tool_classes()
        instance_names = [cls().name for cls in discovered.values()]
        _assert_name_set_equal(instance_names, list(discovered.keys()), "实例 name 与目录键")

    def test_discovery_is_sorted(self):
        discovered = discover_tool_classes()
        assert list(discovered.keys()) == sorted(discovered.keys()), "discover 结果必须按名称排序（注册顺序确定）"

    def test_full_registration_matches_golden(self):
        """自动发现结果 = 完整黄金清单（等价于改造前 Agent 注册结果）"""
        registry = ToolRegistry()
        for cls in discover_tool_classes().values():
            registry.register(cls())

        _assert_name_set_equal(registry.list_tools(), GOLDEN_TOOLS, "完整注册工具名集合")


class TestCatalogExclusions:
    """断言二：catalog = False 的特殊工具不进 _CATALOG"""

    def test_special_tools_not_in_catalog(self):
        discover_tool_classes()
        leaked = CATALOG_EXCLUDED_TOOLS & {cls.name for cls in _CATALOG.values()}
        assert not leaked, f"特殊工具不应进入 _CATALOG（应设 catalog = False）: {leaked}"

    def test_local_proxy_tools_not_in_catalog(self):
        """boss_* 本地代理工具仅子智能体按 allowed 注册，不进自动目录"""
        from src.local_tools.proxy_tool import LOCAL_PROXY_TOOL_NAMES

        discover_tool_classes()
        leaked = LOCAL_PROXY_TOOL_NAMES & {cls.name for cls in _CATALOG.values()}
        assert not leaked, f"本地代理工具不应进入 _CATALOG: {leaked}"

    def test_legacy_browser_tools_not_in_catalog(self):
        """旧版浏览器工具保留兼容，agent 只注册统一入口 browser_automation"""
        legacy_names = {
            "browser_open", "browser_click", "browser_fill", "browser_get_content",
            "browser_navigate", "browser_close", "browser_screenshot", "browser_snapshot",
            "browser_select", "browser_find", "browser_find_all", "browser_get_path",
            "browser_backtrack",
        }
        discover_tool_classes()
        leaked = legacy_names & {cls.name for cls in _CATALOG.values()}
        assert not leaked, f"旧版浏览器工具不应进入 _CATALOG: {leaked}"


class TestDiscoveryIdempotent:
    """断言三：discover 幂等（连续调用结果一致）"""

    def test_discover_is_idempotent(self):
        first = discover_tool_classes()
        second = discover_tool_classes()
        assert list(first.keys()) == list(second.keys())
        assert all(first[name] is second[name] for name in first)


class TestCatalogFalseSubclass:
    """构造临时 catalog=False 子类，验证不进目录且测试后清理 _CATALOG 残留"""

    def test_catalog_false_subclass_excluded(self):
        probe_name = "__test_catalog_false_probe__"
        assert probe_name not in {cls.name for cls in _CATALOG.values()}
        try:
            class ProbeOffCatalogTool(BaseTool):
                catalog = False
                name = probe_name

                async def execute(self, **kwargs):
                    return {"success": True}

            assert probe_name not in {cls.name for cls in _CATALOG.values()}
            assert probe_name not in discover_tool_classes()
        finally:
            _CATALOG.pop(f"{ProbeOffCatalogTool.__module__}.{ProbeOffCatalogTool.__qualname__}", None)

    def test_catalog_true_subclass_registered_then_cleaned(self):
        """对照实验：默认 catalog=True 的子类会登记 _CATALOG（证明目录机制在工作），
        但因定义在 tests/ 模块不进入发现快照（发现边界=src.tools 包），测试后清理"""
        probe_name = "__test_catalog_true_probe__"
        assert probe_name not in {cls.name for cls in _CATALOG.values()}
        try:
            class ProbeOnCatalogTool(BaseTool):
                name = probe_name

                async def execute(self, **kwargs):
                    return {"success": True}

            identity = f"{ProbeOnCatalogTool.__module__}.{ProbeOnCatalogTool.__qualname__}"
            assert _CATALOG.get(identity) is ProbeOnCatalogTool
            assert probe_name not in discover_tool_classes(), (
                "tests/ 等非 src.tools 包内定义的工具类不应进入发现快照"
            )
        finally:
            _CATALOG.pop(identity, None)

    def test_test_doubles_do_not_leak_into_discovery(self):
        """全量测试套件下 tests/ 内定义的测试替身（test_echo/compat_fail 等）
        会登记 _CATALOG，但不得污染发现结果（黄金清单稳定性）"""
        discovered = discover_tool_classes()
        leaked = [
            name for name, cls in discovered.items()
            if not cls.__module__.startswith("src.tools")
        ]
        assert not leaked, f"非 src.tools 包的工具类泄漏进发现结果: {leaked}"

    def test_nameless_intermediate_base_skipped(self):
        """无 name 的中间基类自然跳过，不污染目录"""
        class IntermediateBase(BaseTool):
            """中间基类，不定义 name"""

            async def execute(self, **kwargs):
                return {"success": True}

        probe_name = "__test_intermediate_leaf_probe__"
        try:
            class LeafTool(IntermediateBase):
                name = probe_name

                async def execute(self, **kwargs):
                    return {"success": True}

            identity = f"{LeafTool.__module__}.{LeafTool.__qualname__}"
            assert _CATALOG.get(identity) is LeafTool
        finally:
            _CATALOG.pop(identity, None)


class TestNoArgConstructionValidation:
    """discover 对目录内类做无参构造校验，开发期暴露问题"""

    def test_non_noarg_constructible_class_raises(self):
        probe_name = "__test_requires_ctor_args_probe__"
        assert probe_name not in {cls.name for cls in _CATALOG.values()}
        try:
            class ProbeRequiresArgsTool(BaseTool):
                name = probe_name
                # 伪装模块路径越过 src.tools 发现边界，专门触发 discover 的构造签名校验
                __module__ = "src.tools.__test_probe__"

                def __init__(self, required_dep):
                    self.required_dep = required_dep

                async def execute(self, **kwargs):
                    return {"success": True}

            import src.tools.registry as registry_mod
            with pytest.MonkeyPatch.context() as monkeypatch:
                monkeypatch.setattr(
                    registry_mod,
                    "_walk_and_import",
                    lambda *args, **kwargs: {"src.tools.__test_probe__"},
                )
                with pytest.raises(TypeError, match=probe_name):
                    discover_tool_classes()
        finally:
            identity = f"{ProbeRequiresArgsTool.__module__}.{ProbeRequiresArgsTool.__qualname__}"
            _CATALOG.pop(identity, None)

    def test_candidate_from_unsuccessful_module_is_excluded(self):
        """import 中途失败后已经触发类定义的候选不得污染发现快照。"""
        probe_name = "__test_failed_module_probe__"
        try:
            class FailedModuleProbeTool(BaseTool):
                name = probe_name
                __module__ = "src.tools.__failed_module_probe__"

                async def execute(self, **kwargs):
                    return {"success": True}

            assert probe_name not in discover_tool_classes()
        finally:
            identity = (
                f"{FailedModuleProbeTool.__module__}."
                f"{FailedModuleProbeTool.__qualname__}"
            )
            _CATALOG.pop(identity, None)


class TestImportFailureSemantics:
    """发现器 import 失败两级语义（docs/tools/tool-auto-discovery-design.md §2）：

    - 第三方可选依赖缺失（ModuleNotFoundError 且缺失模块非 src 包）→ warning + 跳过
    - 首方导入失败/循环导入/语法错误/import 期异常 → 原样抛出（改造前 agent.py 显式
      import 的响亮失败语义，防止核心工具静默蒸发）
    """

    def test_third_party_missing_dependency_warns_and_skips(self, monkeypatch):
        import src.tools.registry as registry_mod

        def fake_import(module_name):
            raise ModuleNotFoundError(f"No module named 'playwright'", name="playwright")

        monkeypatch.setattr(registry_mod.importlib, "import_module", fake_import)
        registry_mod._DISCOVERY_FAILED_MODULES.discard("src.tools.__fake__")

        result = registry_mod._import_module_for_discovery("src.tools.__fake__")
        assert result is None, "第三方可选依赖缺失应降级返回 None 而非抛出"
        # 同一模块第二次失败不再重复 warning（去重集合生效）
        assert "src.tools.__fake__" in registry_mod._DISCOVERY_FAILED_MODULES
        registry_mod._DISCOVERY_FAILED_MODULES.discard("src.tools.__fake__")

    def test_first_party_missing_module_raises(self, monkeypatch):
        import src.tools.registry as registry_mod

        def fake_import(module_name):
            raise ModuleNotFoundError(
                f"No module named 'src.tools.__typo__'", name="src.tools.__typo__"
            )

        monkeypatch.setattr(registry_mod.importlib, "import_module", fake_import)

        with pytest.raises(ModuleNotFoundError, match="src.tools.__typo__"):
            registry_mod._import_module_for_discovery("src.tools.__fake__")

    def test_import_time_exception_raises(self, monkeypatch):
        """循环导入（ImportError 无 name）与 import 期异常一律中止发现"""
        import src.tools.registry as registry_mod

        def fake_import(module_name):
            raise RuntimeError("boom at import time")

        monkeypatch.setattr(registry_mod.importlib, "import_module", fake_import)

        with pytest.raises(RuntimeError, match="boom"):
            registry_mod._import_module_for_discovery("src.tools.__fake__")


class TestCatalogCollisionSafety:
    def test_production_duplicate_name_fails_with_all_class_paths(self, monkeypatch):
        import src.tools.registry as registry_mod

        class DuplicateOne(BaseTool):
            name = "__duplicate_probe__"
            __module__ = "src.tools.__duplicate_probe__"
            async def execute(self, **kwargs):
                return {"success": True}

        class DuplicateTwo(BaseTool):
            name = "__duplicate_probe__"
            __module__ = "src.tools.__duplicate_probe__"
            async def execute(self, **kwargs):
                return {"success": True}

        identities = [
            f"{DuplicateOne.__module__}.{DuplicateOne.__qualname__}",
            f"{DuplicateTwo.__module__}.{DuplicateTwo.__qualname__}",
        ]
        monkeypatch.setattr(
            registry_mod, "_walk_and_import",
            lambda *args, **kwargs: {"src.tools.__duplicate_probe__"},
        )
        try:
            with pytest.raises(ValueError, match="__duplicate_probe__") as exc:
                discover_tool_classes()
            assert all(identity in str(exc.value) for identity in identities)
        finally:
            for identity in identities:
                _CATALOG.pop(identity, None)
