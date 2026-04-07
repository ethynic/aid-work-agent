"""
工具基类

定义所有工具的基础接口，支持 Pydantic InputModel 定义参数 schema。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel


class BaseTool(ABC):
    """
    工具抽象基类

    所有工具都需要继承此类并实现execute方法。

    工具参数推荐使用 Pydantic InputModel 定义（自动生成 JSON Schema），
    也兼容旧的 parameters_schema 字典方式。
    """

    name: str = ""
    description: str = ""
    display_name: str = ""  # 中文显示名（用于 UI 展示）
    parameters_schema: Dict[str, Any] = {}
    category: str = "general"
    InputModel: Optional[Type[BaseModel]] = None  # Pydantic 参数模型

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
            "input_schema": self._get_parameters_schema(),
        }

    def _get_parameters_schema(self) -> Dict[str, Any]:
        """
        获取参数 JSON Schema（优先使用 Pydantic InputModel）
        """
        if self.InputModel is not None:
            return self.InputModel.model_json_schema()
        return self.parameters_schema

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """
        获取工具的中文显示名

        子类可重写此方法实现动态显示名（如 "网络搜索「关键词」"）。

        Args:
            tool_args: 工具调用参数（用于动态显示名）

        Returns:
            显示名
        """
        return self.display_name or self.name

    def validate_parameters(self, **kwargs) -> bool:
        """
        验证参数

        优先使用 Pydantic InputModel 校验，否则 fallback 到 schema 校验。

        Args:
            **kwargs: 工具参数

        Returns:
            参数是否有效
        """
        if self.InputModel is not None:
            try:
                self.InputModel(**kwargs)
                return True
            except Exception:
                return False

        required = self.parameters_schema.get("required", [])
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
        if self.InputModel is not None:
            try:
                self.InputModel(**kwargs)
                return []
            except Exception as e:
                # 从 Pydantic 验证错误中提取缺失字段
                missing = []
                if hasattr(e, 'errors'):
                    for err in e.errors():
                        if err.get('type') in ('missing', 'value_error'):
                            missing.append('.'.join(str(x) for x in err.get('loc', ())))
                return missing

        required = self.parameters_schema.get("required", [])
        missing = []
        for param in required:
            if param not in kwargs or kwargs[param] is None:
                missing.append(param)
        return missing
