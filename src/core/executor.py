"""
工具执行引擎

管理工具注册和执行
"""

from typing import Any, Callable, Dict, List, Optional, Type

from loguru import logger

from src.models.plan import Task, TaskStatus


class ToolRegistry:
    """
    工具注册表
    
    管理所有可用工具的注册和发现
    """
    
    def __init__(self):
        """初始化工具注册表"""
        self._tools: Dict[str, "BaseTool"] = {}
    
    def register(self, tool: "BaseTool") -> None:
        """
        注册工具
        
        Args:
            tool: 工具实例
        """
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
    
    def get_tool(self, tool_name: str) -> Optional["BaseTool"]:
        """
        获取工具
        
        Args:
            tool_name: 工具名称
        
        Returns:
            工具实例或None
        """
        return self._tools.get(tool_name)
    
    def list_tools(self) -> List[str]:
        """
        列出所有工具
        
        Returns:
            工具名称列表
        """
        return list(self._tools.keys())
    
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """
        获取所有工具的定义（用于LLM）
        
        Returns:
            工具定义列表
        """
        return [tool.to_tool_definition() for tool in self._tools.values()]


class BaseTool:
    """
    工具基类
    
    所有工具都需要继承此类
    """
    
    name: str = ""
    description: str = ""
    parameters_schema: Dict[str, Any] = {}
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行工具
        
        Args:
            **kwargs: 工具参数
        
        Returns:
            执行结果
        """
        raise NotImplementedError("子类需要实现execute方法")
    
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


class RespondTool(BaseTool):
    """响应工具（用于直接回复）"""
    
    name = "respond"
    description = "直接回复用户消息"
    parameters_schema = {
        "type": "object",
        "properties": {
            "response_type": {
                "type": "string",
                "description": "响应类型",
            },
            "message": {
                "type": "string",
                "description": "响应消息",
            },
        },
        "required": ["message"],
    }
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行响应"""
        message = kwargs.get("message", "")
        response_type = kwargs.get("response_type", "text")
        
        return {
            "success": True,
            "response_type": response_type,
            "message": message,
        }


class ClarifyTool(BaseTool):
    """澄清工具（用于请求更多信息）"""
    
    name = "clarify"
    description = "请求用户提供更多信息"
    parameters_schema = {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "description": "原始意图",
            },
            "missing_entities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "缺失的实体列表",
            },
            "questions": {
                "type": "string",
                "description": "澄清问题",
            },
        },
        "required": ["questions"],
    }
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行澄清"""
        questions = kwargs.get("questions", "")
        missing_entities = kwargs.get("missing_entities", [])
        
        return {
            "success": True,
            "need_clarification": True,
            "questions": questions,
            "missing_entities": missing_entities,
        }


class ToolExecutor:
    """
    工具执行引擎
    
    负责：
    - 管理工具注册和发现
    - 执行具体工具调用
    - 处理执行结果和异常
    """
    
    def __init__(self, registry: Optional[ToolRegistry] = None):
        """
        初始化工具执行引擎
        
        Args:
            registry: 工具注册表（可选）
        """
        self.registry = registry or ToolRegistry()
        self._register_builtin_tools()
    
    def _register_builtin_tools(self) -> None:
        """注册内置工具"""
        self.registry.register(RespondTool())
        self.registry.register(ClarifyTool())
    
    def register_tool(self, tool: BaseTool) -> None:
        """
        注册工具
        
        Args:
            tool: 工具实例
        """
        self.registry.register(tool)
    
    def get_available_tools(self) -> List[str]:
        """
        获取可用工具列表
        
        Returns:
            工具名称列表
        """
        return self.registry.list_tools()
    
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """
        获取工具定义（用于LLM）
        
        Returns:
            工具定义列表
        """
        return self.registry.get_tool_definitions()
    
    async def execute_task(
        self,
        task: Task,
        user_permissions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        执行任务
        
        Args:
            task: 任务实例
            user_permissions: 用户权限列表
        
        Returns:
            执行结果
        """
        tool_name = task.tool_name
        parameters = task.parameters
        
        # 获取工具
        tool = self.registry.get_tool(tool_name)
        if not tool:
            error_msg = f"工具不存在: {tool_name}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
            }
        
        # 权限检查
        if user_permissions is not None:
            if tool_name not in user_permissions and tool_name not in ["respond", "clarify"]:
                error_msg = f"没有权限使用工具: {tool_name}"
                logger.warning(error_msg)
                return {
                    "success": False,
                    "error": error_msg,
                    "permission_denied": True,
                }
        
        # 执行工具
        try:
            logger.info(f"执行工具: {tool_name}, 参数: {parameters}")
            result = await tool.execute(**parameters)
            logger.info(f"工具执行成功: {tool_name}")
            return result
        except Exception as e:
            error_msg = f"工具执行失败: {tool_name}, 错误: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": str(e),
            }
    
    async def execute_plan(
        self,
        plan,
        user_permissions: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        执行计划
        
        Args:
            plan: 执行计划
            user_permissions: 用户权限列表
        
        Returns:
            执行结果列表
        """
        results = []
        
        for task in plan.tasks:
            result = await self.execute_task(task, user_permissions)
            results.append({
                "task_id": task.task_id,
                "tool_name": task.tool_name,
                "result": result,
            })
            
            # 如果任务失败，停止执行
            if not result.get("success", False):
                break
        
        return results


# 全局工具注册表和执行器
tool_registry = ToolRegistry()
tool_executor = ToolExecutor(tool_registry)
