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
        *,
        redact_parameter_logs: bool = False,
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
            if tool_name == "browser_automation":
                logger.info("执行工具: browser_automation, 参数已脱敏")
            elif redact_parameter_logs:
                logger.info(f"执行工具: {tool_name}, 参数已脱敏")
            else:
                # 过滤 _ 前缀的注入参数（_trusted_tenant_id/_progress_queue 等），
                # 避免受信身份与内部对象 repr 落日志
                log_params = {k: v for k, v in parameters.items() if not k.startswith("_")}
                logger.info(f"执行工具: {tool_name}, 参数: {log_params}")
            result = await tool.execute(**parameters)
            logger.info(f"工具执行成功: {tool_name}")
            return result
        except Exception as e:
            # Desktop arguments may contain credentials. Tool exceptions often
            # interpolate their inputs, so never log or return exception text on
            # the explicitly redacted Remote Gateway path.
            public_error = "Remote tool execution failed" if redact_parameter_logs else str(e)
            error_msg = f"工具执行失败: {tool_name}, 错误: {public_error}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": public_error,
            }
    
    async def execute_batch(
        self,
        tasks: List[Dict[str, Any]],
        user_permissions: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        批量执行工具
        
        Args:
            tasks: 任务列表，每个任务包含tool_name和parameters
            user_permissions: 用户权限列表
        
        Returns:
            执行结果列表
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
            
            # 如果执行失败，停止后续执行
            if not result.get("success", False):
                break
        
        return results
