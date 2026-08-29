# -*- coding: utf-8 -*-
"""Agent 核心结构守卫单元测试（运行时安全加固设计 §2.7）

设计文档：docs/system/agent-runtime-safety-hardening-design.md §2.7
- 行数守卫：src/core/agent.py 行数冻结在基线，只降不升；
  新逻辑优先落独立模块，核心文件 agent.py 只做接线。
- 依赖方向守卫：纯函数模块 src/core/agent_events.py 不得反向
  import 母体 src.core.agent，防止把有状态的主循环依赖引入纯函数层。

实现约束：只做文件读取与 AST 静态解析，不 import 任何 src 模块
（避免触发 LLM provider 初始化），无 LLM key 环境同样可运行。
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_PY = REPO_ROOT / "src" / "core" / "agent.py"
AGENT_EVENTS_PY = REPO_ROOT / "src" / "core" / "agent_events.py"

# agent.py 冻结增长基线（只降不升；真实降落后应手动下调基线）
# 取值依据（2026-08-29 统计）：
# - feature/runtime-safety-hardening HEAD：3918 行
# - origin/master：3977 行
# 取两者较大者 3977 作为基线，避免本分支合并回 master 后
# 因 master 侧行数更高而误报；下调基线时同样按此规则重新定值。
AGENT_PY_FROZEN_LINE_LIMIT = 3977

# agent_events.py 所属包（src.core），用于把相对 import 解析为绝对模块名
_AGENT_EVENTS_PACKAGE_PARTS = ("src", "core")

# 纯函数模块禁止反向依赖的母体模块
_FORBIDDEN_TARGET_MODULE = "src.core.agent"


def _is_forbidden(target: str) -> bool:
    """判断一个绝对模块名是否命中禁止依赖（含其子模块）。"""
    return target == _FORBIDDEN_TARGET_MODULE or target.startswith(
        _FORBIDDEN_TARGET_MODULE + "."
    )


def _iter_import_targets(tree: ast.AST, package_parts):
    """遍历 AST，产出源码中所有 import 的绝对模块名。

    覆盖四种形式：import x / from x import y / from . import y /
    from .x import y；相对 import 按 package_parts 解析。
    只做静态解析，不执行模块代码。
    已知边界：__import__("src.core.agent") / importlib.import_module(...)
    等动态导入不在静态检查范围，依赖 CodeReview 兜底。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield ("import", alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    yield ("from-import", node.module)
                continue
            # 相对 import：level=1 指当前包，每 +1 上移一级
            if node.level - 1 > len(package_parts):
                yield ("from-import", "<无法解析的相对 import level=%d>" % node.level)
                continue
            base = list(package_parts[: len(package_parts) - (node.level - 1)])
            if node.module:
                yield ("from-import", ".".join(base + [node.module]))
            else:
                # from . import y 形式：module 为 None，别名即子模块名
                for alias in node.names:
                    yield ("from-import", ".".join(base + [alias.name]))


def test_agent_py_line_count_frozen():
    """agent.py 行数不得超过冻结基线（只降不升）。

    新逻辑优先落独立模块，agent.py 核心文件只做接线；
    本测试失败说明有人把新逻辑直接堆进母体 Agent，属结构性回归。
    """
    assert AGENT_PY.exists(), "src/core/agent.py 不存在"

    content = AGENT_PY.read_text(encoding="utf-8")
    # splitlines() 对无尾换行的文件也会计入最后一行
    # （count("\n") 会少计一行导致守卫偏松）
    line_count = len(content.splitlines())

    assert line_count <= AGENT_PY_FROZEN_LINE_LIMIT, (
        f"agent.py 冻结增长（只降不升）：当前 {line_count} 行 > "
        f"基线 {AGENT_PY_FROZEN_LINE_LIMIT} 行。"
        f"新逻辑优先落 src 下的独立模块，agent.py 核心文件只做接线。"
        f"若属真实重构缩减后的基线过期，请下调本基线常量并更新取值注释。"
    )


def test_agent_events_does_not_import_agent():
    """agent_events.py 不得 import src.core.agent（纯函数模块反向依赖检查）。

    agent_events 是从 agent.py 拆出的纯函数层（安全加固设计 §2.7），
    反向 import 母体会引入有状态的主循环依赖与循环 import 风险，
    破坏「新逻辑优先落独立模块、核心文件只做接线」的收敛方向。
    """
    assert AGENT_EVENTS_PY.exists(), "src/core/agent_events.py 不存在"

    tree = ast.parse(AGENT_EVENTS_PY.read_text(encoding="utf-8"))
    for node_type, target in _iter_import_targets(
        tree, _AGENT_EVENTS_PACKAGE_PARTS
    ):
        assert not _is_forbidden(target), (
            f"agent_events.py 出现对 {_FORBIDDEN_TARGET_MODULE} 的 "
            f"{node_type}（解析目标：{target}）："
            f"纯函数模块不得反向依赖母体 Agent。"
            f"如需共享逻辑，请下沉到独立纯函数模块，由 agent.py 侧引用。"
        )
