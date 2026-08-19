"""
工具注册表

管理所有可用工具的注册和发现
"""

import importlib
import inspect
import pkgutil

from typing import Any, Dict, List, Optional, Set, Type

from loguru import logger

from .base import BaseTool


class ToolRegistry:
    """
    工具注册表
    
    管理所有可用工具的注册和发现
    """
    
    def __init__(self):
        """初始化工具注册表"""
        self._tools: Dict[str, BaseTool] = {}
    
    def register(self, tool: BaseTool) -> None:
        """
        注册工具
        
        Args:
            tool: 工具实例
        """
        if not tool.name:
            raise ValueError("工具必须定义name属性")
        
        self._tools[tool.name] = tool
        # logger.info(f"注册工具: {tool.name}")
    
    def unregister(self, tool_name: str) -> None:
        """
        注销工具
        
        Args:
            tool_name: 工具名称
        """
        if tool_name in self._tools:
            del self._tools[tool_name]
            logger.info(f"注销工具: {tool_name}")
    
    def get_tool(self, tool_name: str) -> Optional[BaseTool]:
        """
        获取工具
        
        Args:
            tool_name: 工具名称
        
        Returns:
            工具实例或None
        """
        return self._tools.get(tool_name)
    
    def has_tool(self, tool_name: str) -> bool:
        """
        检查工具是否存在
        
        Args:
            tool_name: 工具名称
        
        Returns:
            是否存在
        """
        return tool_name in self._tools
    
    def list_tools(self) -> List[str]:
        """
        列出所有工具名称
        
        Returns:
            工具名称列表
        """
        return list(self._tools.keys())
    
    def list_tools_by_category(self, category: str) -> List[str]:
        """
        列出指定分类的工具
        
        Args:
            category: 分类名称
        
        Returns:
            工具名称列表
        """
        return [
            name for name, tool in self._tools.items()
            if tool.category == category
        ]
    
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """
        获取所有工具的定义（用于LLM）
        
        Returns:
            工具定义列表
        """
        return [tool.to_tool_definition() for tool in self._tools.values()]
    
    def get_tool_descriptions(self) -> str:
        """
        获取所有工具的描述文本

        Returns:
            工具描述文本
        """
        lines = []
        for name, tool in self._tools.items():
            lines.append(f"- {name}: {tool.description}")
        return "\n".join(lines)

    def get_usage_guides(self, **kwargs) -> str:
        """
        收集所有注册工具的使用指南，合并为一段 Markdown 文本。
        仅包含 usage_guide 非空的工具。通过 get_usage_guide() 方法收集，支持动态内容。

        Args:
            **kwargs: 传递给各工具 get_usage_guide() 的动态参数

        Returns:
            合并后的工具使用指南文本
        """
        guides = []
        for tool in self._tools.values():
            guide = tool.get_usage_guide(**kwargs)
            if guide:
                guides.append(f"### {tool.name}\n{guide}")
        return "\n\n".join(guides)


# 全局工具注册表
tool_registry = ToolRegistry()


def register_tool(tool: BaseTool) -> None:
    """
    注册工具到全局注册表

    Args:
        tool: 工具实例
    """
    tool_registry.register(tool)


# 已记录过导入失败的模块名（每个模块只 warning 一次，避免多 Agent 实例重复刷屏）
_DISCOVERY_FAILED_MODULES: Set[str] = set()


def _is_first_party_module(module_name: str) -> bool:
    """是否为首方 src 包（精确匹配 src / src.*，避免误伤 srcsomething 之类的第三方包名）"""
    return module_name == "src" or module_name.startswith("src.")


def _import_module_for_discovery(module_name: str):
    """import 单个模块用于工具发现，失败语义分两级（见 docs/tools/tool-auto-discovery-design.md §2）：

    - 第三方可选依赖缺失（ModuleNotFoundError 且缺失模块非首方 src 包）
      → warning 一次并跳过，等价于"该模块内工具没被注册"（如精简部署未装 playwright）。
    - 其余任何失败（首方模块导入错误、循环导入、语法错误、import 期异常）
      → 视为代码缺陷，log 后原样抛出，与改造前 agent.py 显式 import 的响亮失败语义一致，
      避免"核心工具因代码 bug 悄悄消失、用户表现为 AI 突然不会某功能"的静默降级。
    """
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        missing = e.name or ""
        if _is_first_party_module(missing):
            logger.error(
                f"[tool-discovery] 首方模块导入失败（代码缺陷，中止工具发现）: "
                f"{module_name}: 缺失 {missing}: {e}"
            )
            raise
        if module_name not in _DISCOVERY_FAILED_MODULES:
            _DISCOVERY_FAILED_MODULES.add(module_name)
            logger.warning(
                f"[tool-discovery] 可选依赖缺失（{missing}），跳过该模块的工具注册: "
                f"{module_name}: {e}"
            )
        return None
    except Exception as e:
        logger.error(
            f"[tool-discovery] 模块导入失败（代码缺陷，中止工具发现）: {module_name}: {e}"
        )
        raise


def _walk_and_import(package_path: List[str], prefix: str) -> None:
    """递归 import 包下所有模块，触发工具类 __init_subclass__ 登记 _CATALOG。

    先 import 各子包 __init__（多数工具经包导出链加载），再递归 import 包内其余模块
    （覆盖包 __init__ 未导出、藏在模块深处的工具类）。逐模块失败语义见
    _import_module_for_discovery：仅第三方可选依赖缺失降级跳过，首方缺陷中止发现。
    """
    for module_info in pkgutil.iter_modules(package_path, prefix=prefix):
        # iter_modules 的 prefix 参数已拼进 module_info.name，无需再拼
        module = _import_module_for_discovery(module_info.name)
        if module is None:
            continue
        if module_info.ispkg and getattr(module, "__path__", None):
            _walk_and_import(list(module.__path__), f"{module_info.name}.")


def discover_tool_classes() -> Dict[str, Type[BaseTool]]:
    """遍历 src.tools 包，返回自动目录 _CATALOG 的排序快照。

    Returns:
        {工具名: 工具类}，按工具名排序（注册顺序确定），可直接 ``cls()`` 无参构造注册。

    Raises:
        TypeError: 目录中存在不可无参构造的工具类（开发期暴露，需修复该工具
            的 __init__ 默认值，或为其设置 catalog = False 走手工注册）。
    """
    from .base import _CATALOG

    package = importlib.import_module("src.tools")
    _walk_and_import(list(package.__path__), "src.tools.")

    snapshot: Dict[str, Type[BaseTool]] = {}
    for tool_name in sorted(_CATALOG.keys()):
        cls = _CATALOG[tool_name]
        # 目录边界：只自动注册 src.tools 包内定义的工具类。测试替身（tests/）、
        # 本地代理（src/local_tools）等其他位置的 BaseTool 子类虽会登记 _CATALOG，
        # 但不进入发现快照，避免污染生产注册集。
        module = getattr(cls, "__module__", "") or ""
        if module != "src.tools" and not module.startswith("src.tools."):
            continue
        try:
            # inspect.signature(cls) 已剥掉 self，bind() 无参可绑定 == 可无参构造
            inspect.signature(cls).bind()
        except (TypeError, ValueError) as e:
            raise TypeError(
                f"[tool-discovery] 工具 {cls.__module__}.{cls.__qualname__}"
                f"（name={tool_name}）不可无参构造，无法自动注册；"
                f"请给 __init__ 参数补默认值，或设置 catalog = False 改为手工注册: {e}"
            ) from e
        snapshot[tool_name] = cls
    return snapshot
