"""
工具执行器

执行工具调用并处理结果
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from .base import BaseTool
from .registry import ToolRegistry


class ToolExecutor:
    """
    工具执行器
    
    负责：
    - 执行工具调用
    - 处理执行结果
    - 异常处理
    """
    
    def __init__(self, registry: Optional[ToolRegistry] = None):
        """
        初始化工具执行器
        
        Args:
            registry: 工具注册表
        """
        self.registry = registry or ToolRegistry()
    
    async def execute(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        user_permissions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        执行工具
        
        Args:
            tool_name: 工具名称
            parameters: 工具参数
            user_permissions: 用户权限列表
        
        Returns:
            执行结果
        """
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
            if tool_name not in user_permissions:
                error_msg = f"没有权限使用工具: {tool_name}"
                logger.warning(error_msg)
                return {
                    "success": False,
                    "error": error_msg,
                    "permission_denied": True,
                }
        
        # 参数验证
        if not tool.validate_parameters(**parameters):
            missing = tool.get_missing_parameters(**parameters)
            error_msg = f"缺少必需参数: {missing}"
            logger.warning(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "missing_parameters": missing,
            }
        
        # 执行工具
        try:
            logger.debug(f"执行工具: {tool_name}, 参数: {parameters}")
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
    
    async def execute_batch(
        self,
        tasks: List[Dict[str, Any]],
        user_permissions: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        批量执行工具（快速失败策略）

        按顺序逐个执行任务，遇到第一个失败即停止并返回已完成的结果。
        此策略适用于任务之间存在依赖关系的场景（后续任务依赖前序结果）。

        Args:
            tasks: 任务列表，每个任务包含tool_name和parameters
            user_permissions: 用户权限列表

        Returns:
            执行结果列表（仅包含停止前已完成的任务）
        """
        results = []
        
        for task in tasks:
            tool_name = task.get("tool_name")
            parameters = task.get("parameters", {})
            
            result = await self.execute(
                tool_name=tool_name,
                parameters=parameters,
                user_permissions=user_permissions,
            )
            
            results.append({
                "tool_name": tool_name,
                "result": result,
            })
            
            # 快速失败：任务间存在依赖，前序失败则后续无意义
            if not result.get("success", False):
                break
        
        return results
