"""
工具基类

定义所有工具的基础接口
"""

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseTool(ABC):
    """
    工具抽象基类
    
    所有工具都需要继承此类并实现execute方法
    """
    
    name: str = ""
    description: str = ""
    parameters_schema: Dict[str, Any] = {}
    category: str = "general"
    
    @abstractmethod
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行工具
        
        Args:
            **kwargs: 工具参数
        
        Returns:
            执行结果字典，至少包含success字段
        """
        pass
    
    def to_tool_definition(self) -> Dict[str, Any]:
        """
        转换为LLM工具定义格式
        
        Returns:
            工具定义字典
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters_schema,
        }
    
    def validate_parameters(self, **kwargs) -> bool:
        """
        验证参数
        
        Args:
            **kwargs: 工具参数
        
        Returns:
            参数是否有效
        """
        required = self.parameters_schema.get("required", [])
        properties = self.parameters_schema.get("properties", {})
        
        for param in required:
            if param not in kwargs or kwargs[param] is None:
                return False
        
        return True
    
    def get_missing_parameters(self, **kwargs) -> list:
        """
        获取缺失的必需参数
        
        Args:
            **kwargs: 工具参数
        
        Returns:
            缺失的参数名列表
        """
        required = self.parameters_schema.get("required", [])
        missing = []
        
        for param in required:
            if param not in kwargs or kwargs[param] is None:
                missing.append(param)
        
        return missing
