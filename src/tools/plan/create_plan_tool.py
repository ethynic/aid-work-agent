#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CreatePlanTool - 创建执行计划

将任务分解为步骤并创建执行计划。
"""

import json
from typing import Dict, Any, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class CreatePlanInput(BaseModel):
    """创建执行计划参数"""
    goal: str = Field(..., description="任务目标")
    steps: List[Dict[str, Any]] = Field(..., description="步骤列表，每步包含 description、tool、parameters 等")
    execution_mode: Optional[str] = Field("sequential", description="执行模式：sequential（顺序）、parallel（并行）")
    session_id: Optional[str] = Field(None, description="会话ID")
    user_query: Optional[str] = Field(None, description="用户原始查询")


class CreatePlanTool(BaseTool):
    """创建执行计划工具"""

    # 控制工具：不进普通 registry，由 ToolControlSet 带依赖构造。
    catalog = False
    name = "create_plan"
    description = "为复杂任务创建执行计划。⚠️ 如果任务只需要一个工具或一个子智能体，直接调用该工具，不需要创建计划！只有当任务需要多个步骤协调时才使用。"
    usage_guide = ""
    display_name = "创建执行计划"
    category = "plan"
    InputModel = CreatePlanInput

    def __init__(self, plan_manager, skill_registry, subagent_registry=None, tool_registry=None):
        """
        Args:
            plan_manager: PlanManager 实例
            skill_registry: SkillRegistry 实例
            subagent_registry: SubagentRegistry 实例（可选，主智能体才有）
            tool_registry: ToolRegistry 实例（可选，用于获取可用工具列表）
        """
        self.plan_manager = plan_manager
        self.skill_registry = skill_registry
        self.subagent_registry = subagent_registry
        self.tool_registry = tool_registry

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行创建计划

        Args:
            goal: 任务目标
            steps: 步骤列表
            execution_mode: 执行模式
            session_id: 会话ID
            user_query: 用户原始查询

        Returns:
            计划结果字典
        """
        goal = kwargs.get("goal", kwargs.get("user_query", ""))
        steps = kwargs.get("steps", [])
        execution_mode = kwargs.get("execution_mode", "sequential")
        session_id = kwargs.get("session_id", "")
        user_query = kwargs.get("user_query", goal)

        # 检查步骤是否使用了不可用的工具
        if self.tool_registry:
            available_tools = list(self.tool_registry.list_tools())
        else:
            # fallback: 从 _get_tools 生成
            available_tools = []
        available_skills = self.skill_registry.list_skills() if self.skill_registry else []
        available_subagents = self.subagent_registry.list_subagents() if self.subagent_registry else []

        unavailable_tools = []
        for step in steps:
            tool = step.get("tool", "")
            if tool and tool not in available_tools and tool not in available_skills:
                # skill_execute 和 delegate_to_subagent 是特殊工具
                if not tool.startswith("skill_") and tool != "delegate_to_subagent":
                    # 检查是否是可用的子智能体
                    if tool not in available_subagents:
                        unavailable_tools.append(tool)

        # 创建真实的执行计划
        plan = self.plan_manager.create_plan(
            session_id=session_id,
            user_query=user_query,
            steps=steps,
            execution_mode=execution_mode,
            available_tools=available_tools,
            available_skills=available_skills,
        )

        # 构建计划展示输出
        plan_output = []
        plan_output.append("=" * 60)
        plan_output.append("📋 执行计划 (Execution Plan)")
        plan_output.append("=" * 60)
        plan_output.append(f"🎯 目标: {goal}")
        plan_output.append(f"🆔 计划ID: {plan.plan_id}")
        plan_output.append(f"🔄 执行模式: {execution_mode}")
        plan_output.append(f"📝 步骤数: {len(steps)}")
        plan_output.append("-" * 60)
        plan_output.append("📝 步骤详情:")

        for i, step in enumerate(steps, 1):
            description = step.get("description", "")
            tool = step.get("tool", "N/A")
            params = step.get("parameters", {})
            expected = step.get("expected_output", "")

            plan_output.append(f"\n  步骤 {i}: {description}")
            if tool != "N/A":
                plan_output.append(f"    🔧 工具: {tool}")
                if params:
                    plan_output.append(f"    📊 参数: {json.dumps(params, ensure_ascii=False)}")
                if expected:
                    plan_output.append(f"    📤 预期输出: {expected}")

        plan_output.append("-" * 60)

        # 如果是简单任务（只有一个步骤），提示可以直接执行
        if len(steps) == 1:
            plan_output.append("✅ 单步任务，直接执行...")
        else:
            plan_output.append("✅ 计划创建完成，开始按步骤执行...")

        plan_output.append("=" * 60)

        # 如果有不可用的工具，添加警告
        if unavailable_tools:
            plan_output.append("\n⚠️ 注意: 以下工具不可用:")
            for tool in unavailable_tools:
                plan_output.append(f"  - {tool}")
            plan_output.append("\n建议: 这些能力可能需要其他方式实现，请参考可用工具和技能列表。")

        # Print to log
        plan_str = "\n".join(plan_output)
        logger.info(f"\n{plan_str}")

        # Also print to console for visibility
        print(plan_str)

        # 构建下一步执行提示
        next_step_prompt = ""
        if steps:
            first_step = steps[0]
            tool = first_step.get("tool", "")
            params = first_step.get("parameters", {})
            description = first_step.get("description", "")

            if tool:
                next_step_prompt = f"\n\n**下一步操作：** 立即调用 `{tool}` 工具执行步骤1。"
                if tool == "delegate_to_subagent" and "subagent_name" in params:
                    subagent_name = params["subagent_name"]
                    task_desc = params.get("task_description", description)
                    next_step_prompt += f"\n\n请调用：\n```\n{tool}(\n  subagent_name=\"{subagent_name}\",\n  task_description=\"{task_desc}\"\n)\n```"

        # 返回结果
        result = {
            "success": True,
            "plan_id": plan.plan_id,
            "plan": {
                "goal": goal,
                "steps": steps,
                "execution_mode": execution_mode
            },
            "message": f"计划创建成功，共{len(steps)}个步骤。计划已保存到: plans/{session_id}.md{next_step_prompt}",
            "is_simple_task": len(steps) == 1,
            "next_step": {
                "step_number": 1,
                "tool": steps[0].get("tool") if steps else None,
                "parameters": steps[0].get("parameters") if steps else None,
                "description": steps[0].get("description") if steps else None,
            } if steps else None
        }

        if unavailable_tools:
            result["warnings"] = {
                "unavailable_tools": unavailable_tools,
                "suggestion": "部分工具不可用，请检查或寻找替代方案"
            }

        return result
