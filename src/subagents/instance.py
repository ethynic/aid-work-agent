#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Subagent Instance - 子智能体运行实例

支持两种模式：
1. 独立主智能体模式：处理用户交互循环，可委派任务给子智能体
2. 委派模式：执行主智能体委托的任务
"""

import asyncio
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from datetime import datetime

from loguru import logger

from src.models.subagent import SubagentConfig, SubagentTaskStatus
from src.subagents.protocol import SubagentTaskRecord

if TYPE_CHECKING:
    from src.memory.short_term import ShortTermMemory
    from src.llm.gateway import LLMGateway
    from src.tools.registry import ToolRegistry
    from src.core.skill_registry import SkillRegistry


class SubagentInstance:
    """
    子智能体运行实例
    
    支持两种运行模式：
    1. 独立主智能体模式（standalone）：execution_id为None
    2. 委派模式（delegated）：execution_id不为None
    
    使用示例:
        # 独立模式
        instance = SubagentInstance(config, memory, session_id, None)
        await instance.run_standalone()
        
        # 委派模式
        instance = SubagentInstance(config, memory, session_id, execution_id)
        await instance.run(record)
    """
    
    def __init__(
        self,
        config: SubagentConfig,
        session_memory: 'ShortTermMemory',
        session_id: str,
        execution_id: Optional[str] = None,
        llm: Optional['LLMGateway'] = None,
        tool_registry: Optional['ToolRegistry'] = None,
        skill_registry: Optional['SkillRegistry'] = None,
    ):
        """
        初始化子智能体实例
        
        Args:
            config: Subagent配置
            session_memory: 共享的session记忆
            session_id: Session ID
            execution_id: 执行ID（None表示独立模式）
            llm: LLM网关（可选，默认使用全局）
            tool_registry: 工具注册表（可选，默认创建受限的）
            skill_registry: 技能注册表（可选，默认创建受限的）
        """
        self.config = config
        self.memory = session_memory
        self.session_id = session_id
        self.execution_id = execution_id
        
        # LLM
        if llm is None:
            from src.llm.gateway import llm_gateway
            self.llm = llm_gateway
        else:
            self.llm = llm
        
        # 工具注册表（受限）
        self.tool_registry = self._build_tool_registry(tool_registry)
        
        # 技能注册表（受限）
        self.skill_registry = self._build_skill_registry(skill_registry)
        
        # 子智能体执行器（如果允许委派）
        self._subagent_executor = None
        if config.allow_delegation and config.delegatable_to:
            from src.subagents.registry import SubagentRegistry
            from src.subagents.executor import SubagentExecutor
            # 创建只包含允许委派的subagent的注册表
            sub_reg = SubagentRegistry()
            self._subagent_executor = SubagentExecutor(session_memory, sub_reg)
        
        # 执行状态
        self._is_running = False
        self._clarification_event = asyncio.Event()
        self._clarification_answer: Optional[str] = None
        
        # Token统计
        self._token_usage = {"input": 0, "output": 0}
    
    @property
    def is_subagent_mode(self) -> bool:
        """是否为委派模式"""
        return self.execution_id is not None
    
    def _build_tool_registry(
        self, 
        parent_registry: Optional['ToolRegistry'] = None
    ) -> 'ToolRegistry':
        """
        构建受限的工具注册表
        
        Args:
            parent_registry: 父级工具注册表（用于继承）
            
        Returns:
            受限的工具注册表
        """
        from src.tools.registry import ToolRegistry
        
        logger.info(f"\n{'='*60}\n[SUBAGENT_INSTANCE] _build_tool_registry\n{'='*60}")
        logger.info(f"[SUBAGENT_INSTANCE] parent_registry: {parent_registry}")
        logger.info(f"[SUBAGENT_INSTANCE] config.tools: {self.config.tools}")
        
        registry = ToolRegistry()
        
        # 获取允许的工具列表
        allowed_tools = self.config.get_allowed_tools()
        logger.info(f"[SUBAGENT_INSTANCE] allowed_tools: {allowed_tools}")
        
        # 如果配置为继承且提供了父注册表
        if self.config.tools.get("inherit", False):
            logger.info(f"[SUBAGENT_INSTANCE] Inherit mode enabled")
            if parent_registry:
                logger.info(f"[SUBAGENT_INSTANCE] Inheriting from parent_registry")
                logger.info(f"[SUBAGENT_INSTANCE] Parent has tools: {parent_registry.list_tools()}")
                # 继承所有工具
                for tool_name in parent_registry.list_tools():
                    tool = parent_registry.get_tool(tool_name)
                    if tool:
                        registry.register(tool)
                        logger.info(f"[SUBAGENT_INSTANCE] Registered inherited tool: {tool_name}")
            else:
                logger.warning(f"[SUBAGENT_INSTANCE] Inherit=True but no parent_registry provided!")
                logger.warning(f"[SUBAGENT_INSTANCE] This subagent will have NO tools!")
        elif allowed_tools and parent_registry:
            # 只注册允许的工具
            logger.info(f"[SUBAGENT_INSTANCE] Filtering tools by allowed list")
            for tool_name in allowed_tools:
                tool = parent_registry.get_tool(tool_name)
                if tool:
                    registry.register(tool)
                    logger.info(f"[SUBAGENT_INSTANCE] Registered allowed tool: {tool_name}")
        
        logger.info(f"[SUBAGENT_INSTANCE] Final registry has tools: {registry.list_tools()}")
        
        return registry
    
    def _build_skill_registry(
        self,
        parent_registry: Optional['SkillRegistry'] = None
    ) -> 'SkillRegistry':
        """
        构建受限的技能注册表
        
        Args:
            parent_registry: 父级技能注册表
            
        Returns:
            受限的技能注册表
        """
        from pathlib import Path
        from src.core.skill_registry import SkillRegistry
        
        allowed_skills = self.config.get_allowed_skills()
        
        if not allowed_skills:
            return SkillRegistry()
        
        # 创建只包含允许技能的注册表
        registry = SkillRegistry()
        
        if parent_registry:
            for skill_name in allowed_skills:
                skill = parent_registry.get(skill_name)
                if skill:
                    registry.register(skill)
        
        return registry
    
    def _get_tool_definitions(self, include_delegation: bool = False) -> List[Dict]:
        """
        获取工具定义列表
        
        Args:
            include_delegation: 是否包含委派工具
            
        Returns:
            工具定义列表
        """
        logger.info(f"\n{'='*60}\n[SUBAGENT_INSTANCE] _get_tool_definitions\n{'='*60}")
        logger.info(f"[SUBAGENT_INSTANCE] include_delegation: {include_delegation}")
        logger.info(f"[SUBAGENT_INSTANCE] tool_registry type: {type(self.tool_registry)}")
        
        definitions = []
        
        # 从工具注册表获取工具定义
        if self.tool_registry:
            try:
                tool_defs = self.tool_registry.get_tool_definitions()
                logger.info(f"[SUBAGENT_INSTANCE] tool_registry returned {len(tool_defs)} definitions")
                definitions.extend(tool_defs)
            except Exception as e:
                logger.error(f"[SUBAGENT_INSTANCE] Failed to get tool definitions: {e}")
        else:
            logger.warning(f"[SUBAGENT_INSTANCE] tool_registry is None!")
        
        # 添加技能工具
        if self.skill_registry:
            try:
                skill_def = self.skill_registry.get_skill_tool_definition()
                if skill_def:
                    definitions.append(skill_def)
                    logger.info(f"[SUBAGENT_INSTANCE] Added skill tool definition")
            except Exception as e:
                logger.error(f"[SUBAGENT_INSTANCE] Failed to get skill definition: {e}")
        
        # 添加委派工具
        if include_delegation and self._subagent_executor:
            try:
                from src.subagents.registry import SubagentRegistry
                temp_reg = SubagentRegistry()
                delegation_def = temp_reg.get_delegation_tool_definition(
                    self.config.delegatable_to
                )
                if delegation_def:
                    definitions.append(delegation_def)
                    logger.info(f"[SUBAGENT_INSTANCE] Added delegation tool definition")
            except Exception as e:
                logger.error(f"[SUBAGENT_INSTANCE] Failed to get delegation definition: {e}")
        
        logger.info(f"[SUBAGENT_INSTANCE] Total tool definitions: {len(definitions)}")
        logger.info(f"[SUBAGENT_INSTANCE] Tool names: {[d.get('name') for d in definitions]}")
        
        return definitions
    
    def _build_messages(
        self,
        task_description: str,
        context: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """
        构建LLM消息列表
        
        Args:
            task_description: 任务描述
            context: 额外上下文
            
        Returns:
            消息列表
        """
        messages = []
        
        # 获取session历史
        history = self.memory.get_context(self.session_id)
        
        # 添加最近的历史（限制数量）
        max_history = self.config.context.get("max_history_messages", 5)
        for msg in history[-max_history:]:
            role = msg.get("role", "user")
            if role in ["user", "assistant"]:
                messages.append({
                    "role": role,
                    "content": msg.get("content", "")
                })
        
        # 添加任务描述
        messages.append({
            "role": "user",
            "content": task_description
        })
        
        # 添加额外上下文
        if context:
            for ctx in context:
                if "role" in ctx and "content" in ctx:
                    messages.append(ctx)
        
        return messages
    
    async def run(self, record: SubagentTaskRecord) -> Optional[Dict[str, Any]]:
        """
        执行委托的任务（委派模式）
        
        Args:
            record: 任务记录
            
        Returns:
            执行结果
        """
        if not self.is_subagent_mode:
            raise RuntimeError("run() method is only for delegated mode")
        
        self._is_running = True
        logger.info(f"\n{'='*60}\n[SUBAGENT_INSTANCE] run() started\n{'='*60}")
        logger.info(f"[SUBAGENT_INSTANCE] config.name: {self.config.name}")
        logger.info(f"[SUBAGENT_INSTANCE] session_id: {self.session_id}")
        logger.info(f"[SUBAGENT_INSTANCE] execution_id: {self.execution_id}")
        logger.info(f"[SUBAGENT_INSTANCE] task_description: {record.task_description}")
        
        try:
            # 构建消息
            logger.info(f"[SUBAGENT_INSTANCE] Building messages...")
            messages = self._build_messages(record.task_description)
            logger.info(f"[SUBAGENT_INSTANCE] Built {len(messages)} messages")
            for i, msg in enumerate(messages):
                logger.debug(f"[SUBAGENT_INSTANCE] Message {i}: role={msg.get('role')}, content={msg.get('content', '')[:100]}...")
            
            # 执行循环
            logger.info(f"[SUBAGENT_INSTANCE] Starting execute loop...")
            result = await self._execute_loop(messages, record)
            
            logger.info(f"[SUBAGENT_INSTANCE] Execute loop finished")
            logger.info(f"[SUBAGENT_INSTANCE] Result: {result}")
            
            return result
            
        except Exception as e:
            import traceback
            logger.error(f"[SUBAGENT_INSTANCE] Execution failed: {e}")
            logger.error(f"[SUBAGENT_INSTANCE] Traceback:\n{traceback.format_exc()}")
            raise
            
        finally:
            self._is_running = False
            logger.info(f"[SUBAGENT_INSTANCE] run() finished")
    
    async def _execute_loop(
        self,
        messages: List[Dict],
        record: SubagentTaskRecord,
        max_iterations: int = 20,
    ) -> Dict[str, Any]:
        """
        执行LLM循环
        
        Args:
            messages: 消息列表
            record: 任务记录
            max_iterations: 最大迭代次数
            
        Returns:
            执行结果
        """
        iteration = 0
        final_result = None
        final_summary = ""
        
        logger.info(f"\n{'='*60}\n[SUBAGENT_INSTANCE] _execute_loop started\n{'='*60}")
        logger.info(f"[SUBAGENT_INSTANCE] max_iterations: {max_iterations}")
        logger.info(f"[SUBAGENT_INSTANCE] system_prompt: {self.config.system_prompt[:200]}...")
        
        # 获取工具定义
        tool_defs = self._get_tool_definitions(include_delegation=True)
        logger.info(f"[SUBAGENT_INSTANCE] Tool definitions count: {len(tool_defs)}")
        logger.info(f"[SUBAGENT_INSTANCE] Tool names: {[t.get('name') for t in tool_defs]}")
        
        while iteration < max_iterations:
            iteration += 1
            logger.info(f"\n{'='*40}\n[SUBAGENT_INSTANCE] Iteration {iteration}\n{'='*40}")
            
            # 更新进度
            progress = min(90.0, iteration * 5.0)
            record.update_progress(progress, f"Processing iteration {iteration}")
            
            # 调用LLM
            try:
                logger.info(f"[SUBAGENT_INSTANCE] Calling LLM...")
                logger.debug(f"[SUBAGENT_INSTANCE] Messages: {messages}")
                
                response = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=tool_defs,
                    system_prompt=self.config.system_prompt,
                )
                
                logger.info(f"[SUBAGENT_INSTANCE] LLM response received")
                
                # 更新token统计
                if hasattr(response, 'usage'):
                    self._token_usage["input"] += getattr(response.usage, 'prompt_tokens', 0)
                    self._token_usage["output"] += getattr(response.usage, 'completion_tokens', 0)
                    logger.info(f"[SUBAGENT_INSTANCE] Token usage: input={self._token_usage['input']}, output={self._token_usage['output']}")
                
            except Exception as e:
                import traceback
                logger.error(f"[SUBAGENT_INSTANCE] LLM call failed: {e}")
                logger.error(f"[SUBAGENT_INSTANCE] Traceback:\n{traceback.format_exc()}")
                record.fail(f"LLM call failed: {e}")
                return {"result": None, "summary": f"Failed: {e}", "token_usage": self._token_usage}
            
            # 检查是否有工具调用
            content = getattr(response, 'content', '') or ''
            tool_calls = getattr(response, 'tool_calls', None) or []
            
            logger.info(f"[SUBAGENT_INSTANCE] Response content length: {len(content)}")
            logger.info(f"[SUBAGENT_INSTANCE] Tool calls count: {len(tool_calls)}")
            
            if content:
                logger.info(f"[SUBAGENT_INSTANCE] Content preview: {content[:300]}...")
            
            if not tool_calls:
                # 没有工具调用，任务完成
                logger.info(f"[SUBAGENT_INSTANCE] No tool calls, task completed")
                final_result = {"content": content}
                final_summary = content[:500] if content else "Task completed"
                break
            
            # 记录工具调用
            for tc in tool_calls:
                logger.info(f"[SUBAGENT_INSTANCE] Tool call: {tc.function.name}")
                logger.debug(f"[SUBAGENT_INSTANCE] Tool args: {tc.function.arguments}")
            
            # 添加助手消息
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                    for tc in tool_calls
                ]
            })
            
            # 执行工具调用
            for tool_call in tool_calls:
                logger.info(f"[SUBAGENT_INSTANCE] Executing tool: {tool_call.function.name}")
                tool_result = await self._execute_tool(tool_call, record)
                logger.info(f"[SUBAGENT_INSTANCE] Tool result: {str(tool_result)[:200]}...")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(tool_result)
                })
        
        if iteration >= max_iterations:
            logger.warning(f"[SUBAGENT_INSTANCE] Max iterations reached")
            final_summary = "Max iterations reached"
        
        logger.info(f"[SUBAGENT_INSTANCE] Execute loop finished with summary: {final_summary[:200] if final_summary else 'N/A'}")
        
        return {
            "result": final_result,
            "summary": final_summary,
            "token_usage": self._token_usage
        }
    
    async def _execute_tool(
        self,
        tool_call,
        record: SubagentTaskRecord
    ) -> Dict[str, Any]:
        """
        执行工具调用
        
        Args:
            tool_call: 工具调用对象
            record: 任务记录
            
        Returns:
            工具结果
        """
        import json
        
        tool_name = tool_call.function.name
        
        try:
            arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            arguments = {}
        
        logger.debug(f"Subagent {self.config.name} executing tool: {tool_name}")
        
        # 处理委派工具
        if tool_name == "delegate_to_subagent" and self._subagent_executor:
            return await self._handle_delegation_tool(arguments, record)
        
        # 处理技能工具
        if tool_name == "use_skill":
            return await self._handle_skill_tool(arguments)
        
        # 执行普通工具
        tool = self.tool_registry.get_tool(tool_name)
        if not tool:
            return {"error": f"Tool not found: {tool_name}"}
        
        try:
            result = await tool.execute(**arguments)
            return result
        except Exception as e:
            return {"error": str(e)}
    
    async def _handle_delegation_tool(
        self,
        arguments: Dict[str, Any],
        record: SubagentTaskRecord
    ) -> Dict[str, Any]:
        """
        处理委派工具调用
        
        Args:
            arguments: 工具参数
            record: 任务记录
            
        Returns:
            委派结果
        """
        subagent_name = arguments.get("subagent_name")
        task_description = arguments.get("task_description", "")
        
        if not subagent_name:
            return {"error": "Missing subagent_name"}
        
        if subagent_name not in self.config.delegatable_to:
            return {"error": f"Not allowed to delegate to: {subagent_name}"}
        
        # 委派任务
        response = await self._subagent_executor.delegate(
            task_id=f"{record.task_id}_sub_{subagent_name}",
            subagent_name=subagent_name,
            task_description=task_description,
            session_id=self.session_id,
        )
        
        if not response.success:
            return {"error": response.error or "Delegation failed"}
        
        # 等待结果
        result_record = await self._subagent_executor.wait_for_result(
            response.execution_id
        )
        
        if result_record:
            return {
                "success": result_record.status == SubagentTaskStatus.COMPLETED,
                "result": result_record.result,
                "summary": result_record.summary,
                "error": result_record.error
            }
        
        return {"error": "Delegation timed out"}
    
    async def _handle_skill_tool(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理技能工具调用
        
        Args:
            arguments: 工具参数
            
        Returns:
            技能内容
        """
        skill_name = arguments.get("skill")
        
        if not skill_name:
            return {"error": "Missing skill name"}
        
        allowed_skills = self.config.get_allowed_skills()
        if allowed_skills and skill_name not in allowed_skills:
            return {"error": f"Skill not allowed: {skill_name}"}
        
        content = self.skill_registry.get_content(skill_name)
        if content:
            return {"content": content}
        
        return {"error": f"Skill not found: {skill_name}"}
    
    async def resume_from_clarification(self, answer: str) -> None:
        """
        从澄清等待中恢复
        
        Args:
            answer: 澄清答案
        """
        self._clarification_answer = answer
        self._clarification_event.set()
    
    async def run_standalone(self) -> None:
        """
        独立主智能体模式运行
        
        处理用户交互循环
        """
        if self.is_subagent_mode:
            raise RuntimeError("run_standalone() is only for standalone mode")
        
        logger.info(f"Subagent {self.config.name} starting in standalone mode")
        
        # 这个方法需要在具体的应用中实现
        # 通常涉及读取用户输入、调用LLM、输出响应的循环
        raise NotImplementedError("Standalone mode requires application-specific implementation")
    
    def get_token_usage(self) -> Dict[str, int]:
        """获取Token使用统计"""
        return self._token_usage.copy()
