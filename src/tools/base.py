"""
工具基类

定义所有工具的基础接口，支持 Pydantic InputModel 定义参数 schema。
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type

from pydantic import BaseModel

if TYPE_CHECKING:
    # 仅类型标注用，运行时不导入（避免与 src.core.verbose_feedback 潜在的导入环）
    from src.core.verbose_feedback import LongRunningFeedback

# 工具自动目录：catalog=True 且定义了 name 的 BaseTool 子类按稳定类身份登记，
# 供 registry.discover_tool_classes() 发现注册（docs/tools/tool-auto-discovery-design.md）。
# 只收集类引用不实例化；工具名冲突由 discovery 在生产边界过滤后统一报错。
_CATALOG: Dict[str, Type["BaseTool"]] = {}


def catalog_class_identity(cls: Type["BaseTool"]) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


class ExecutionTarget(str, Enum):
    """工具执行位置（设计 docs/design/recruiting/recruiting-cli-agent-integration-design.md §4.1）"""

    SERVER = "server"                  # 服务端执行（现有工具默认）
    LOCAL_REQUIRED = "local_required"  # 必须在用户本机 Runtime 执行（boss_* proxy 工具）
    EITHER = "either"                  # 两端均可（预留）


class BaseTool(ABC):
    """
    工具抽象基类

    所有工具都需要继承此类并实现execute方法。

    工具参数推荐使用 Pydantic InputModel 定义（自动生成 JSON Schema），
    也兼容旧的 parameters_schema 字典方式。
    """

    name: str = ""
    description: str = ""
    usage_guide: str = ""  # 工具使用指南，注入系统提示词。空字符串表示无指南。
    display_name: str = ""  # 中文显示名（用于 UI 展示）
    parameters_schema: Dict[str, Any] = {}
    category: str = "general"
    InputModel: Optional[Type[BaseModel]] = None  # Pydantic 参数模型
    execution_target: ExecutionTarget = ExecutionTarget.SERVER  # 执行位置，默认服务端，现有工具零改动
    # Agent 装配顺序优先级。默认工具按名称稳定排序；较大值用于兼容历史上后置注册的工具。
    assembly_order: int = 0
    # 是否进入自动发现目录 _CATALOG。以下工具应显式设为 False 退出：
    # 虚拟工具（create_plan/use_skill/skill_execute/clarify/delegate_to_subagent）、
    # 有构造依赖的控制工具、渠道层工具（speech_to_text）、
    # 仅子智能体按需注册的工具（boss_* 本地代理）、测试替身/中间基类。
    catalog: bool = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """子类定义时自动登记进 _CATALOG（仅收集类，不实例化）。

        __init_subclass__ 触发时子类类体属性已绑定到 cls，通过 cls 参数读取，
        中间基类未定义 name（继承空串）自然跳过。
        """
        super().__init_subclass__(**kwargs)
        if getattr(cls, "catalog", True) and getattr(cls, "name", None):
            _CATALOG[catalog_class_identity(cls)] = cls

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

    def get_user_feedback(self, tool_args: dict) -> Optional["LongRunningFeedback"]:
        """长任务等待提示钩子（verbose Phase 1，服务端专用方法）。

        默认返回 None（短任务，不产生业务提示）。个别执行路径真实稳定超过
        等待阈值的长任务工具（如 Excel 模板填充的 AI 分析路径，Phase 4 接入）
        可重写本方法，按 tool_args 动态返回 LongRunningFeedback。

        ⚠️ 上下文隔离（设计 §6.2）：本方法是服务端编排层专用钩子，只由
        resolve_feedback_policy 调用；to_tool_definition 生成的 tool schema
        不包含它，LLM 永远不可见。

        Args:
            tool_args: 本次工具调用参数（用于按任务类型动态判定）

        Returns:
            LongRunningFeedback（长任务，start_message 为等待文案）或 None（短任务）
        """
        return None

    def get_usage_guide(self, **kwargs) -> str:
        """
        获取工具使用指南文本（注入系统提示词用）。

        默认返回 self.usage_guide 静态属性。子类可重写此方法提供动态内容。

        Args:
            **kwargs: 动态参数（如子智能体描述列表）

        Returns:
            使用指南文本，空字符串表示无指南
        """
        return self.usage_guide

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

    def get_validation_errors(self, **kwargs) -> List[str]:
        """
        获取参数校验的可读错误信息列表（覆盖所有 Pydantic 错误类型，含类型错误）。

        原先 `get_missing_parameters` 只提取 `missing`/`value_error` 两类错误，
        当 LLM 传了类型错误（如 headers 传 JSON 字符串导致 `dict_type`）时会返回
        空列表，生成误导性的"缺少必需参数: []"，LLM 无法自纠导致死循环。

        Args:
            **kwargs: 工具参数

        Returns:
            可读错误信息列表，如
            ["headers: Input should be a valid dictionary（实际输入类型: str）"]
        """
        if self.InputModel is not None:
            try:
                self.InputModel(**kwargs)
                return []
            except Exception as e:
                errors: List[str] = []
                if hasattr(e, 'errors'):
                    for err in e.errors():
                        loc = '.'.join(str(x) for x in err.get('loc', ()))
                        msg = err.get('msg', '') or '参数无效'
                        input_type = self._describe_validation_input(err)
                        if loc:
                            errors.append(f"{loc}: {msg}（实际输入类型: {input_type}）")
                        else:
                            errors.append(f"{msg}（实际输入类型: {input_type}）")
                return errors

        # 旧的 schema 校验路径：无 InputModel，仅按必填字段判断
        required = self.parameters_schema.get("required", [])
        errors = []
        for param in required:
            if param not in kwargs or kwargs[param] is None:
                errors.append(f"{param}: 缺少必需参数（实际输入类型: 未提供）")
        return errors

    @staticmethod
    def _describe_validation_input(err: Dict[str, Any]) -> str:
        """从 Pydantic 错误条目中提取实际输入类型描述，便于 LLM 自纠。"""
        if err.get("type") == "missing":
            return "未提供"
        raw = err.get("input")
        if raw is None:
            return "None"
        return type(raw).__name__
