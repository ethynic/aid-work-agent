"""
工具注册表

管理所有可用工具的注册和发现
"""

from typing import Any, Dict, List, Optional, Type

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
        logger.info(f"注册工具: {tool.name}")
    
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


# 全局工具注册表
tool_registry = ToolRegistry()


def register_tool(tool: BaseTool) -> None:
    """
    注册工具到全局注册表
    
    Args:
        tool: 工具实例
    """
    tool_registry.register(tool)
