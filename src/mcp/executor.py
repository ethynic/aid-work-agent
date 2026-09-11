#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Tool 执行器

将 MCP 工具调用转发到现有的 Skill 执行系统。

主要功能:
1. 查找 MCP 工具对应的 skill 和命令模板
2. 将调用参数组装为命令并执行
3. 将 ExecutionResult 转换为 MCP 协议结果格式
"""

import hmac
from typing import Optional

from loguru import logger

from src.mcp.tools import MCPToolRegistry, MCPToolDefinition
from src.core.skill_executor import SkillExecutor, ExecutionResult


def authenticate(api_key: str, valid_keys: list[str]) -> bool:
    """
    校验 API 密钥是否合法。

    使用常量时间比较防止时序攻击。当 valid_keys 为空时表示禁用认证，直接返回 True。

    Args:
        api_key: 请求中提供的 API 密钥
        valid_keys: 合法密钥列表

    Returns:
        是否通过认证
    """
    if not valid_keys:
        return True

    for key in valid_keys:
        if hmac.compare_digest(api_key, key):
            return True

    return False


class MCPExecutor:
    """
    MCP 工具执行器

    将 MCP 协议的工具调用转换为 SkillExecutor 的命令执行，
    并将结果包装为 MCP 协议格式返回。
    """

    def __init__(
        self,
        skill_executor: SkillExecutor,
        tool_registry: MCPToolRegistry,
    ):
        """
        初始化 MCP 执行器

        Args:
            skill_executor: Skill 执行器实例
            tool_registry: MCP 工具注册表
        """
        self.skill_executor = skill_executor
        self.tool_registry = tool_registry

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict,
        tenant_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> dict:
        """
        执行 MCP 工具调用

        流程：
        1. 从注册表查找工具定义
        2. 用调用参数构建命令字符串
        3. 将 arguments["params"]（如果存在）作为 stdin 内容传入脚本
        4. 调用 SkillExecutor 执行命令
        5. 将结果转换为 MCP 协议格式

        Args:
            tool_name: 工具名称
            arguments: 工具调用参数
            tenant_id: 租户 ID（透传给 skill 执行上下文）
            api_key: 可选的认证密钥

        Returns:
            MCP 协议格式的结果字典，包含 content 和 isError 字段
        """
        # 查找工具定义
        tool_def: Optional[MCPToolDefinition] = self.tool_registry.get_tool(tool_name)
        if tool_def is None:
            logger.warning(f"MCP 工具未找到: {tool_name}")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"工具 '{tool_name}' 未找到",
                    }
                ],
                "isError": True,
            }

        # 构建命令
        command = self._build_command(tool_def, arguments)

        # 提取 stdin 内容
        stdin_content: Optional[bytes] = None
        params = arguments.get("params")
        if isinstance(params, str) and params:
            stdin_content = params.encode("utf-8")

        logger.info(
            f"MCP 执行工具: tool={tool_name}, "
            f"skill={tool_def.skill_name}, "
            f"tenant={tenant_id}"
        )

        try:
            # 跨租户路径防护：命令预检（与 skill_execute_tool 同规则）
            from src.core.tenant_path_guard import (
                check_text_for_foreign_tenant_paths,
                is_source_storage_reference,
            )

            if is_source_storage_reference(command):
                logger.warning(
                    f"[安全防护] MCP 命令引用 source_storage，已拦截: tool={tool_name}"
                )
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "命令包含对受限存储路径的访问，已被安全防护拦截。",
                        }
                    ],
                    "isError": True,
                }
            _blocked, violation_owner = check_text_for_foreign_tenant_paths(
                command, tenant_id
            )
            if violation_owner:
                logger.warning(
                    f"[安全防护] MCP 命令引用跨租户存储路径，已拦截: "
                    f"tool={tool_name}, tenant={tenant_id or '无'}, violation={violation_owner}"
                )
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "命令包含对其他租户存储目录的访问，已被安全防护拦截。",
                        }
                    ],
                    "isError": True,
                }

            result: ExecutionResult = await self.skill_executor.execute_skill_command(
                skill_name=tool_def.skill_name,
                command=command,
                session_id=None,
                user_id=tenant_id,
                stdin_content=stdin_content,
            )

            # 跨租户路径防护：输出后置脱敏
            from src.core.tenant_path_guard import redact_foreign_tenant_paths

            result.stdout, _ = redact_foreign_tenant_paths(result.stdout, tenant_id)
            result.stderr, _ = redact_foreign_tenant_paths(result.stderr, tenant_id)

            # 转换为 MCP 协议结果
            mcp_result = self._to_mcp_result(tool_name, result)

            logger.info(
                f"MCP 工具执行完成: tool={tool_name}, "
                f"success={result.success}, "
                f"duration={result.duration:.2f}s"
            )

            return mcp_result

        except Exception as e:
            logger.error(f"MCP 工具执行异常: tool={tool_name}, error={e}")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"工具执行异常: {e}",
                    }
                ],
                "isError": True,
            }

    def _build_command(self, tool_def: MCPToolDefinition, arguments: dict) -> str:
        """
        根据工具定义和调用参数构建命令字符串

        使用工具的 script_command 作为命令模板，将 arguments 中的参数
        序列化为 JSON 字符串附加到命令末尾（与 SkillExecutor 的标准调用方式一致）。

        Args:
            tool_def: MCP 工具定义
            arguments: 调用参数

        Returns:
            完整的命令字符串
        """
        import json

        command = tool_def.script_command

        # 移除 params 字段，避免重复传递（params 通过 stdin 传入）
        filtered_args = {k: v for k, v in arguments.items() if k != "params"}

        if filtered_args:
            args_json = json.dumps(filtered_args, ensure_ascii=False)
            command = f"{command} '{args_json}'"

        return command

    def _to_mcp_result(self, tool_name: str, result: ExecutionResult) -> dict:
        """
        将 ExecutionResult 转换为 MCP 协议结果格式

        Args:
            tool_name: 工具名称（用于日志）
            result: Skill 执行结果

        Returns:
            MCP 协议格式的结果字典
        """
        # 优先使用 stdout，如果为空则使用 stderr
        text = result.stdout or result.stderr or ""

        if result.timed_out:
            logger.warning(f"MCP 工具执行超时: tool={tool_name}, duration={result.duration:.2f}s")
            text = f"执行超时（{result.duration:.1f}秒）: {text}"

        return {
            "content": [
                {
                    "type": "text",
                    "text": text,
                }
            ],
            "isError": not result.success,
        }
