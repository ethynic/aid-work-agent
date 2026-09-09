"""R47 架构隔离测试：src/weixin_marketing 不得依赖通用定时任务执行链

- 源码层（AST）：扫描全部模块 import/引用，禁止 src.tools.scheduler / src.scheduler、
  ScheduledTaskExecutor / create_scheduled_task。
- 源码层（文本）：模块全部 .py 内容不得出现 'src.scheduler' / 'src.tools.scheduler'
  字样——可检出函数内懒 import（__import__/importlib 字符串形态）。
- 运行时层：import src.weixin_marketing 全模块后 sys.modules 不含这两个包。
"""

import ast
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# tests/unit/weixin_marketing/test_architecture.py → parents[3] = 仓库根
MODULE_DIR = Path(__file__).parents[3] / "src" / "weixin_marketing"

FORBIDDEN_PREFIXES = ("src.tools.scheduler", "src.scheduler")
FORBIDDEN_NAMES = ("ScheduledTaskExecutor", "create_scheduled_task")


def test_module_dir_exists():
    """路径修正锚点（必修 C）：MODULE_DIR 必须真实存在，防止扫描空转"""
    assert MODULE_DIR.is_dir()
    assert len(list(MODULE_DIR.glob("*.py"))) >= 10


def test_no_scheduler_imports_in_source():
    violations = []
    for py_file in sorted(MODULE_DIR.glob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(FORBIDDEN_PREFIXES):
                        violations.append(f"{py_file.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(FORBIDDEN_PREFIXES):
                    violations.append(f"{py_file.name}: from {module} import ...")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                violations.append(f"{py_file.name}: 引用 {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
                violations.append(f"{py_file.name}: 属性引用 {node.attr}")
    assert not violations, violations


def test_no_scheduler_references_in_source_text():
    """文本扫描（必修 C）：任何出现形态（含函数内懒 import 字符串/注释引用）都算违规"""
    violations = []
    for py_file in sorted(MODULE_DIR.glob("*.py")):
        text = py_file.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_PREFIXES:
            if forbidden in text:
                violations.append(f"{py_file.name}: 含 {forbidden} 字样")
    assert not violations, violations


def test_no_scheduler_modules_loaded_at_runtime():
    """增量语义：import weixin 各模块不得**新引入** src.tools.scheduler / src.scheduler
    （全量套件下其他测试可能已合法加载这些包——sys.modules 是进程全局，
    断言全局不存在会与测试顺序耦合而误报）"""
    before = set(sys.modules.keys())
    import src.weixin_marketing.adapters  # noqa: F401
    import src.weixin_marketing.content  # noqa: F401
    import src.weixin_marketing.init_tables  # noqa: F401
    import src.weixin_marketing.models  # noqa: F401
    import src.weixin_marketing.quota_map  # noqa: F401
    import src.weixin_marketing.registration  # noqa: F401
    import src.weixin_marketing.service  # noqa: F401
    import src.weixin_marketing.triggers  # noqa: F401

    newly = [m for m in set(sys.modules) - before if m.startswith(FORBIDDEN_PREFIXES)]
    assert not newly, newly


def test_registration_disabled_by_default_no_adapter():
    """R42：enabled=false（默认）时受信注册点零注册"""
    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from src.weixin_marketing.config import WeixinMarketingConfig
    from src.weixin_marketing.registration import ensure_registered, reset_registration

    reset_registration()
    try:
        # 模拟默认关闭配置：临时替换 settings 读取结果
        import src.weixin_marketing.registration as registration_mod

        original = registration_mod.get_weixin_marketing_config
        registration_mod.get_weixin_marketing_config = (
            lambda: WeixinMarketingConfig(enabled=False)
        )
        try:
            assert ensure_registered() is False
            assert TrustedAdapterRegistry.get("weixin.fixed_content.v1") is None
        finally:
            registration_mod.get_weixin_marketing_config = original
    finally:
        reset_registration()


def test_registration_enabled_registers_once():
    from dataclasses import replace

    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from src.weixin_marketing.adapters import WeixinFixedContentAdapter
    from src.weixin_marketing.config import get_weixin_marketing_config
    from src.weixin_marketing.registration import ensure_registered, reset_registration

    reset_registration()
    try:
        import src.weixin_marketing.registration as registration_mod

        original = registration_mod.get_weixin_marketing_config
        enabled_cfg = replace(get_weixin_marketing_config(), enabled=True)
        registration_mod.get_weixin_marketing_config = lambda: enabled_cfg
        try:
            assert ensure_registered() is True
            first = TrustedAdapterRegistry.get("weixin.fixed_content.v1")
            assert isinstance(first, WeixinFixedContentAdapter)
            assert ensure_registered() is True  # 幂等
            assert TrustedAdapterRegistry.get("weixin.fixed_content.v1") is first
        finally:
            registration_mod.get_weixin_marketing_config = original
    finally:
        reset_registration()
