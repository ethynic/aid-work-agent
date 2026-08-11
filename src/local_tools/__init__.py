"""本地工具基础设施（M0.3）

⚠️ 不要在本 __init__.py 顶层 import 子模块。任何对 src.local_tools.* 的
import 都会触发本 __init__.py 执行，顶层 import 会拉起 DB 依赖链。
按需导出，参照 src/core/__init__.py 的懒加载模式。
"""

import importlib
from typing import Any

_SUBMODULES = ("api", "catalog", "models", "pairing", "repository", "security")


def __getattr__(name: str) -> Any:
    """按需导出子模块，避免顶层 import 副作用"""
    if name in _SUBMODULES:
        return importlib.import_module(f"src.local_tools.{name}")
    raise AttributeError(f"module 'src.local_tools' has no attribute {name!r}")


def __dir__():
    return list(_SUBMODULES)


__all__ = list(_SUBMODULES)
