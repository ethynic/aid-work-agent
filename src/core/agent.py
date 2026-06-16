#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Master Agent - LLM-Driven Agent Loop

The agent uses LLM for:
1. Intent understanding
2. Task planning and decomposition
3. Deciding which tool/agent/skill to call
4. Executing tools and integrating results
5. Loading and executing Skills
"""

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any, AsyncGenerator, Callable, Coroutine, Any
from loguru import logger

from enum import Enum

from src.config.settings import settings
from src.core.agent_logger import log_agent_iteration, log_skill_execute
from src.core.redis_client import redis_client
from src.llm.gateway import llm_gateway
from src.tools.registry import ToolRegistry
from src.tools.executor import ToolExecutor
from src.memory.short_term import ShortTermMemory
from src.memory.manager import MemoryManager
from src.prompts import PromptManager
from src.prompts.style_manager import StyleManager, get_style_manager
from src.models.message import UnifiedMessage
from src.models.user import User
from src.models.plan import TaskStatus
from src.core.skill_registry import SkillRegistry
from src.core.skill_executor import SkillExecutor
from src.core.plan_manager import PlanManager
from src.core.skill_session import SkillSession


class AgentMode(Enum):
    """智能体工作模式"""
    MASTER = "master"              # 主智能体模式：拥有完整能力，可委派任务
    SUBAGENT = "subagent"         # 子智能体（被委派）模式：由主智能体创建，无委派能力
    STANDALONE = "standalone"      # 子智能体独立模式：从入口直接进入，有子智能体约束，无委派能力


class Agent:
    """
    统一的智能体类 - 支持主智能体、子智能体和独立模式

    主智能体模式 (mode=MASTER):
    - 有委派任务给子智能体的能力
    - system prompt 包含委派规则
    - 管理子智能体的创建和执行

    子智能体模式 (mode=SUBAGENT):
    - 没有委派能力
    - system prompt 不包含委派规则
    - 由主智能体创建，在独立线程中运行
    - 有自己的 plan 和执行流程

    子智能体独立模式 (mode=STANDALONE):
    - 从入口直接进入，不经过主智能体委派
    - 使用子智能体的 system_prompt（专业约束）
    - 工具按 subagent_config 过滤
    - 禁止委派
    - 有独立的 ShortTermMemory（有会话记忆）

    The agent loop:
    1. Receive user message
    2. LLM understands intent and plans tasks (主智能体必须plan，闲聊除外)
    3. LLM decides which tools to call (子智能体不能委派)
    4. Execute tools and return results to LLM
    5. LLM integrates results and responds
    6. Repeat until task complete
    """

    def __init__(
        self,
        is_master: bool = True,
        subagent_config=None,
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        parent_plan_manager=None,
        mode: AgentMode = AgentMode.MASTER,
        tenant_id: Optional[str] = None,
    ):
        """
        初始化智能体

        Args:
            is_master: 是否为主智能体（向后兼容，优先使用 mode）
            subagent_config: 子智能体配置（子智能体模式时必需）
            session_id: 会话ID（子智能体模式时使用）
            execution_id: 执行ID（子智能体模式时使用）
            parent_plan_manager: 父智能体的计划管理器（子智能体模式时使用，用于记录执行过程）
            mode: 智能体工作模式 (MASTER / SUBAGENT / STANDALONE)
            tenant_id: 租户ID（SaaS模式下用于加载租户自定义 skills）
        """
        self.mode = mode
        self.subagent_config = subagent_config
        self.session_id = session_id
        self.execution_id = execution_id
        self.parent_plan_manager = parent_plan_manager

        # 向后兼容：通过 is_master 推断 mode（仅当 mode 未显式指定时）
        if mode == AgentMode.MASTER and not is_master:
            self.mode = AgentMode.SUBAGENT
        self.is_master = self.mode == AgentMode.MASTER

        # 子智能体澄清相关状态
        self._clarification_missing_info = []
        # pending clarifications 已迁移到 Redis: pending_clarification:{session_id}, TTL=3600s

        # 共享组件 — 子智能体可覆盖 LLM 提供者
        if subagent_config and hasattr(subagent_config, 'llm_provider') and subagent_config.llm_provider:
            from src.llm.gateway import LLMGateway
            self.llm = LLMGateway(provider_name=subagent_config.llm_provider)
            logger.info(f"Agent using override LLM provider: {subagent_config.llm_provider}")
        else:
            self.llm = llm_gateway
        self.tool_registry = ToolRegistry()
        self.tool_executor = ToolExecutor(self.tool_registry)
        self.prompt_manager = PromptManager()
        self.style_manager = get_style_manager()
        self.memory = MemoryManager(
            max_short_term_messages=settings.memory.short_term.max_messages,
            short_term_ttl=settings.memory.short_term.ttl,
        )

        # Skill 会话管理 - 跟踪活跃的 Skill 执行
        self._active_skill_sessions: Dict[str, SkillSession] = {}

        # 技能系统
        skills_dir = Path(__file__).parent.parent / "skills"

        # 读取配置的 allowed 列表
        if self.mode == AgentMode.MASTER:
            # 主智能体：从配置读取 allowed 列表
            allowed_skills = settings.skills.master_agent.allowed if settings.skills.master_agent.allowed else None
            self.skill_registry = SkillRegistry()
            self.skill_registry.load_from_directory(skills_dir, allowed=allowed_skills)
            logger.info(f"Master Agent loaded {len(self.skill_registry)} skills (allowed={allowed_skills})")
        else:
            # 子智能体（SUBAGENT 和 STANDALONE）：从 SUBAGENT.md 读取 allowed 列表
            allowed_skills = None
            if subagent_config and hasattr(subagent_config, 'get_allowed_skills'):
                subagent_allowed = subagent_config.get_allowed_skills()
                if subagent_allowed:
                    allowed_skills = subagent_allowed
            self.skill_registry = SkillRegistry()
            self.skill_registry.load_from_directory(skills_dir, allowed=allowed_skills)
            logger.info(f"{self.mode.value} agent '{subagent_config.name if subagent_config else 'unknown'}' loaded {len(self.skill_registry)} skills (allowed={allowed_skills})")

        self.skill_executor = SkillExecutor(self.skill_registry)

        # 计划管理器
        plans_dir = Path(__file__).parent.parent.parent / "plans"
        self.plan_manager = PlanManager(plans_dir)

        # 定时任务工具实例（在 _register_builtin_tools 中赋值）
        self._create_scheduled_task_tool = None
        self._manage_scheduled_task_tool = None

        # 租户 skills 按需加载状态
        self._init_tenant_id = tenant_id  # 初始化时传入的 tenant_id
        self._instance_id = None           # 当前关联的数字员工实例ID（运行时注入）
        self._loaded_tenant_id = None
        self._skills_loaded_at = 0.0

        if self.mode == AgentMode.STANDALONE:
            # 独立模式：不创建子智能体注册表和执行器
            self.subagent_registry = None
            self.subagent_executor = None
            self._register_builtin_tools()
            if subagent_config:
                self._filter_tools_by_config()
            logger.info(f"Standalone agent initialized: {subagent_config.name if subagent_config else 'unknown'}")
        elif self.mode == AgentMode.MASTER:
            # 主智能体模式
            subagents_dir = Path(__file__).parent.parent.parent / "subagents"
            from src.subagents.registry import SubagentRegistry
            self.subagent_registry = SubagentRegistry(subagents_dir)

            # 注册内置工具
            self._register_builtin_tools()

            # 初始化子智能体执行器
            from src.subagents.executor import SubagentExecutor
            self.subagent_executor = SubagentExecutor(
                self.memory,
                self.subagent_registry,
                self.tool_registry,
                self.skill_registry,
            )

            # 延迟初始化 delegate 工具（依赖 subagent_executor）
            self._init_delegate_tool()

            logger.info(f"Master Agent initialized with {len(self.skill_registry)} skills, {len(self.subagent_registry)} subagents")
        else:
            # 子智能体模式（被委派）
            self.subagent_registry = None
            self.subagent_executor = None

            # 注册受限的工具（根据子智能体配置）
            self._register_builtin_tools()
            self._filter_tools_by_config()

            logger.info(f"Subagent initialized: {subagent_config.name if subagent_config else 'unknown'}")

    # ==================== Pending Clarification (Redis) ====================

    def _pending_clarification_key(self, session_id: str) -> str:
        return redis_client.make_key("pending_clarification", session_id)

    def _save_pending_clarification(self, session_id: str, data: Dict[str, Any]) -> None:
        """保存待澄清上下文到 Redis Hash"""
        key = self._pending_clarification_key(session_id)
        for field, value in data.items():
            redis_client.hset(key, field, value)
        redis_client.expire(key, 3600)

    def _get_pending_clarification(self, session_id: str) -> Optional[Dict[str, Any]]:
        """从 Redis Hash 读取待澄清上下文"""
        key = self._pending_clarification_key(session_id)
        return redis_client.hgetall(key) or None

    def _clear_pending_clarification(self, session_id: str) -> None:
        """清除 Redis 中的待澄清上下文"""
        key = self._pending_clarification_key(session_id)
        redis_client.delete(key)

    def _ensure_tenant_skills_loaded(self):
        """按需加载租户自定义 skills

        tenant_id 来源（按优先级）：
        1. __init__ 时传入的 tenant_id 参数（Agent 创建时已知租户）
        2. ContextVar get_current_tenant_id()（HTTP 请求通过中间件设置）

        在 SaaS 模式下，首次为某租户处理请求时，
        从 storage/tenants/{tenant_id}/skills/ 加载租户 skills 并合并到 SkillRegistry。
        后续请求使用缓存，避免重复磁盘扫描。
        """
        if not settings.saas.enabled:
            return

        # 优先使用初始化时传入的 tenant_id
        tenant_id = self._init_tenant_id
        if not tenant_id:
            # 其次从 ContextVar 获取（HTTP 请求场景）
            from src.saas.context import get_current_tenant_id
            tenant_id = get_current_tenant_id()
        if not tenant_id:
            return

        # 已为该租户加载且缓存仍新鲜，跳过
        if self._loaded_tenant_id == tenant_id:
            from src.saas.services.tenant_skill_cache import tenant_skill_cache
            cached = tenant_skill_cache._cache.get(tenant_id)
            if cached and self._skills_loaded_at >= cached[1]:
                return

        # 加载合并后的 skills
        from src.saas.services.tenant_skill_cache import tenant_skill_cache
        from src.saas.services.skill_resolver import SkillResolver

        base_skills_dir = Path(__file__).parent.parent / "skills"
        skills_dict = tenant_skill_cache.get_or_load(
            tenant_id, base_skills_dir, self.skill_registry._allowed,
        )

        # 重建 _loaders 映射
        base_loader = self.skill_registry._loader
        tenant_dir = SkillResolver.get_tenant_skills_dir(tenant_id)
        tenant_loader = None
        if tenant_dir.exists():
            from src.core.skill_loader import SkillLoader
            tenant_loader = SkillLoader(tenant_dir)

        new_loaders = {}
        if base_loader:
            for name in base_loader.skills:
                new_loaders[name] = base_loader
        if tenant_loader:
            for name in tenant_loader.skills:
                new_loaders[name] = tenant_loader  # 租户覆盖基础

        # 应用到当前 registry
        self.skill_registry._skills = dict(skills_dict)
        self.skill_registry._loaders = new_loaders

        self._loaded_tenant_id = tenant_id
        self._skills_loaded_at = time.time()

    def _register_builtin_tools(self):
        """Register built-in tools"""
        from src.tools.email.email_tool import EmailSendTool, EmailReadTool, EmailListFoldersTool
        from src.tools.ocr import PaddleOCRDocParsingTool
        from src.tools.document.doc_tool import DocSummarizeTool, DocTranslateTool
        from src.tools.search.search_tool import WebSearchTool
        from src.tools.browser import BrowserAutomationTool
        from src.tools.file.read_tool import ReadTool
        from src.tools.file.write_tool import WriteTool
        from src.tools.file.edit_tool import EditTool
        from src.tools.file.cp_tool import CpTool
        from src.tools.file.upload_to_remote import UploadToRemoteTool
        from src.tools.file.register_download_tool import RegisterDownloadFileTool
        from src.tools.llm.content_generate_tool import ContentGenerateTool
        from src.tools.network.http_api import HttpApiTool

        # 注册邮件工具（不传配置，运行时通过 user_id 从数据库读取）
        self.tool_registry.register(EmailSendTool())
        self.tool_registry.register(EmailReadTool())
        self.tool_registry.register(EmailListFoldersTool())
        self.tool_registry.register(PaddleOCRDocParsingTool())
        self.tool_registry.register(DocSummarizeTool())
        self.tool_registry.register(DocTranslateTool())
        self.tool_registry.register(WebSearchTool())
        
        # 注册浏览器工具
        self.tool_registry.register(BrowserAutomationTool())
        
        # 注册文件工具
        self.tool_registry.register(ReadTool())
        self.tool_registry.register(WriteTool())
        self.tool_registry.register(EditTool())
        self.tool_registry.register(CpTool())
        self.tool_registry.register(UploadToRemoteTool())
        self.tool_registry.register(RegisterDownloadFileTool())
        
        # 注册LLM内容生成工具
        self.tool_registry.register(ContentGenerateTool())
        self.tool_registry.register(HttpApiTool())

        # 注册定时任务工具
        from src.tools.scheduler.scheduled_task_tool import CreateScheduledTaskTool, ManageScheduledTaskTool
        self._create_scheduled_task_tool = CreateScheduledTaskTool()
        self._manage_scheduled_task_tool = ManageScheduledTaskTool()
        self.tool_registry.register(self._create_scheduled_task_tool)
        self.tool_registry.register(self._manage_scheduled_task_tool)

        # 注册知识库工具
        from src.tools.knowledge.knowledge_base_tool import KnowledgeBaseTool
        self.tool_registry.register(KnowledgeBaseTool())

        # 注册景点知识库搜索工具
        from src.tools.knowledge.attraction_search_tool import AttractionSearchTool
        self.tool_registry.register(AttractionSearchTool())

        # 注册 Word 文档处理工具
        from src.tools.word.word_process_tool import WordProcessTool
        self.tool_registry.register(WordProcessTool())

        # 注册 Excel 电子表格处理工具
        from src.tools.excel.excel_process_tool import ExcelProcessTool
        self.tool_registry.register(ExcelProcessTool())

        # 注册 PDF 文档处理工具
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        self.tool_registry.register(PdfProcessTool())

        # 注册 PPT 生成工具
        from src.tools.ppt.ppt_process_tool import PptProcessTool
        self.tool_registry.register(PptProcessTool())

        # 注册转人工客服工具
        from src.tools.transfer_to_human import TransferToHumanTool
        self.tool_registry.register(TransferToHumanTool())

        # 注册 AI 外呼工具（Mock 实现）
        from src.tools.phone.ai_call_tool import AICallTool
        self.tool_registry.register(AICallTool())

        # 注册语音转文字工具
        from src.tools.asr.speech_to_text_tool import SpeechToTextTool
        self.tool_registry.register(SpeechToTextTool())

        # 注册智能数据分析工具
        from src.tools.data_analysis.smart_analysis_tool import SmartDataAnalysisTool
        self.tool_registry.register(SmartDataAnalysisTool())

        # 注册聊天附件数据文件上传工具
        from src.tools.data_analysis.upload_data_tool import UploadDataFileTool
        self.tool_registry.register(UploadDataFileTool())

        # 注册提取的虚拟工具（不放入 tool_registry，由 agent loop 特殊处理）
        from src.tools.plan.create_plan_tool import CreatePlanTool
        from src.tools.skill.use_skill_tool import UseSkillTool
        from src.tools.skill.skill_execute_tool import SkillExecuteTool
        from src.tools.skill.skill_complete_tool import SkillCompleteTool
        from src.tools.agent.clarify_tool import ClarifyTool
        from src.tools.agent.delegate_tool import DelegateToSubagentTool

        self._create_plan_tool = CreatePlanTool(
            plan_manager=self.plan_manager,
            skill_registry=self.skill_registry,
            subagent_registry=getattr(self, 'subagent_registry', None),
            tool_registry=self.tool_registry,
        )
        self._use_skill_tool = UseSkillTool(skill_registry=self.skill_registry)
        self._skill_execute_tool = SkillExecuteTool(
            skill_executor=self.skill_executor,
            skill_registry=self.skill_registry,
        )
        self._skill_complete_tool = SkillCompleteTool()
        self._clarify_tool = ClarifyTool()
        # delegate_to_subagent 工具需要 subagent_registry 和 subagent_executor
        # 对于 MASTER 模式延迟初始化（因为 subagent_executor 在此方法之后创建）
        # 对于非 MASTER 模式设为 None
        self._delegate_tool = None  # 将在 _init_delegate_tool 中初始化

        logger.info(f"Registered {len(self.tool_registry._tools)} tools")

    def _init_delegate_tool(self):
        """延迟初始化 delegate 工具（需要在 subagent_executor 创建后调用）"""
        if self.mode == AgentMode.MASTER and self.subagent_registry and self.subagent_executor:
            from src.tools.agent.delegate_tool import DelegateToSubagentTool
            self._delegate_tool = DelegateToSubagentTool(
                subagent_registry=self.subagent_registry,
                subagent_executor=self.subagent_executor,
            )
    
    def _filter_tools_by_config(self):
        """根据子智能体配置过滤可用工具"""
        if self.mode == AgentMode.MASTER or not self.subagent_config:
            return
        
        # 获取允许的工具列表
        allowed_tools = self.subagent_config.get_allowed_tools()
        
        # 如果配置为继承，保留所有工具
        if self.subagent_config.tools.get("inherit", False):
            logger.info(f"Subagent {self.subagent_config.name} inherits all tools")
            return
        
        # 否则只保留允许的工具
        if allowed_tools:
            all_tools = list(self.tool_registry._tools.keys())
            for tool_name in all_tools:
                if tool_name not in allowed_tools:
                    self.tool_registry._tools.pop(tool_name, None)
            logger.info(f"Subagent {self.subagent_config.name} filtered to {len(self.tool_registry._tools)} tools: {allowed_tools}")
        else:
            # 如果没有指定允许的工具，清除所有工具
            self.tool_registry._tools.clear()
            logger.info(f"Subagent {self.subagent_config.name} has no tools allowed")
    
    def _get_tools(self) -> List[Dict[str, Any]]:
        """
        Get tool definitions from ToolRegistry + virtual tools + dynamic tools

        Schema 来源：每个工具类通过 Pydantic InputModel 或 parameters_schema 定义。
        MASTER：包含委派工具
        SUBAGENT / STANDALONE：不包含委派工具
        """
        # 1. 从 ToolRegistry 获取所有已注册工具的 schema
        tools = self.tool_registry.get_tool_definitions()

        # 2. 添加虚拟工具定义（skill_execute, skill_complete, create_plan, clarify）
        # 这些工具不放入 tool_registry，但需要将定义暴露给 LLM
        virtual_tools = [
            self._skill_execute_tool,
            self._skill_complete_tool,
            self._create_plan_tool,
            self._clarify_tool,
        ]
        for vtool in virtual_tools:
            if vtool:
                tools.append(vtool.to_tool_definition())

        # 添加技能工具
        if self.skill_registry:
            skill_tool = self.skill_registry.get_skill_tool_definition()
            tools.append(skill_tool)

        # 仅 MASTER 模式：添加子智能体委派工具
        # 注意：available_subagents 必须按租户订阅过滤，否则 LLM 能看到无权使用的子智能体
        if self.mode == AgentMode.MASTER and self.subagent_registry and len(self.subagent_registry) > 0:
            available_subagents = self._get_available_subagents()
            delegation_tool = self.subagent_registry.get_delegation_tool_definition(available_subagents)
            if delegation_tool:
                tools.append(delegation_tool)
        
        return tools

    def _get_tool_display_name(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """
        将工具名称转换为用户友好的显示名称

        优先从 ToolRegistry / 虚拟工具实例的 get_display_name() 获取。

        Args:
            tool_name: 原始工具名称
            tool_args: 工具参数

        Returns:
            用户友好的显示名称
        """
        # 1. 从 ToolRegistry 查找
        tool = self.tool_registry.get_tool(tool_name)
        if tool:
            return tool.get_display_name(tool_args or {})

        # 2. 虚拟工具查找
        virtual_tool_map = {
            "create_plan": self._create_plan_tool,
            "clarify": self._clarify_tool,
            "skill_execute": self._skill_execute_tool,
            "skill_complete": self._skill_complete_tool,
        }
        vtool = virtual_tool_map.get(tool_name)
        if vtool:
            return vtool.get_display_name(tool_args or {})

        # 3. 动态工具
        if tool_name == "use_skill" and tool_args:
            skill = tool_args.get("skill", "")
            return f"加载技能「{skill}」"
        if tool_name == "delegate_to_subagent" and tool_args:
            subagent_name = tool_args.get("subagent_name", "")
            return f"调用{subagent_name}子智能体"

        # 4. Fallback
        return tool_name

    def _collect_tool_usage_guides(self) -> str:
        """
        从 ToolRegistry 和虚拟工具中收集 usage_guide，合并为系统提示词文本。
        仅包含 usage_guide 非空的工具。
        """
        # 从 ToolRegistry 收集注册工具的指南
        guides = self.tool_registry.get_usage_guides()

        # 虚拟工具指南（不在 registry 中，手动收集）
        virtual_tools = [
            self._skill_execute_tool,
            self._skill_complete_tool,
            self._create_plan_tool,
            self._clarify_tool,
        ]
        for vtool in virtual_tools:
            if vtool:
                guide = vtool.get_usage_guide()
                if guide:
                    if guides:
                        guides += "\n\n"
                    guides += f"### {vtool.name}\n{guide}"

        return guides

    def _get_available_subagents(self) -> List[str]:
        """
        获取当前请求可用的子智能体 name 列表（按租户订阅过滤）。

        过滤规则：
        - SaaS 模式 + 有 tenant_id：从 subscriptions 表读取租户订阅的 subagent_type（dir_name），
          再映射回注册表中对应的 name
        - 演示模式 / 非 SaaS / 无 tenant_id：返回全部子智能体（排除 dir_name == "main" 的主智能体）

        性能：_get_tools 在 Agent 主循环中每轮都被调用，因此本方法按 (tenant_id, saas.enabled,
        demo.enabled) 做实例级缓存，避免每轮查 DB。租户上下文切换或配置变化时缓存自动失效。

        Returns:
            可用的子智能体 name 列表（注册表 _configs 的 key）
        """
        if not self.subagent_registry:
            return []

        from src.saas.context import get_current_tenant_id
        tenant_id = get_current_tenant_id()
        cache_key = (tenant_id, bool(settings.saas.enabled), bool(settings.demo.enabled))

        # 实例级缓存：同一请求内多次调用复用结果
        cached_key = getattr(self, "_available_subagents_cache_key", None)
        if cached_key == cache_key:
            cached_value = getattr(self, "_available_subagents_cache_value", None)
            if cached_value is not None:
                return cached_value

        # SaaS 模式 + 有租户 ID：从订阅表查询
        if settings.saas.enabled and tenant_id:
            from src.db.database import get_db_connection
            from src.saas.db.subscription_db import SubscriptionDB

            with get_db_connection() as conn:
                allowed_subagent_types = SubscriptionDB.get_allowed_subagent_types(conn, tenant_id)
            # 过滤注册的子智能体，只保留租户订阅的
            # subagent_type 存的是 dir_name，_configs 的 key 是 name
            filtered = []
            for name, config in self.subagent_registry._configs.items():
                agent_id = config.dir_name or name
                if agent_id in allowed_subagent_types and agent_id != "main":
                    filtered.append(name)
        else:
            # 演示模式 / 非 SaaS：返回全部（排除主智能体）
            filtered = [
                name
                for name, config in self.subagent_registry._configs.items()
                if (config.dir_name or name) != "main"
            ]

        self._available_subagents_cache_key = cache_key
        self._available_subagents_cache_value = filtered
        return filtered

    def _build_base_system_prompt(
        self,
        include_delegation: bool = True,
        subagent_constraint: str = "",
        user: Optional[User] = None
    ) -> str:
        """
        构建基础系统提示词（可复用）
        
        Args:
            include_delegation: 是否包含子智能体委派相关内容（子智能体不应包含）
            subagent_constraint: 子智能体的额外约束（追加到基础提示词后面）
            user: 用户信息
            
        Returns:
            系统提示词
        """
        # 如果是子智能体或独立模式，强制不包含委派内容
        if self.mode != AgentMode.MASTER:
            include_delegation = False
        
        skill_descriptions = self.skill_registry.get_descriptions() if self.skill_registry else "(暂无可用技能)"
        available_tools = [t["name"] for t in self._get_tools()]
        available_skills = self.skill_registry.list_skills() if self.skill_registry else []
        
        # 子智能体信息（仅主智能体使用）
        subagent_descriptions = ""
        available_subagents = []
        if include_delegation and self.subagent_registry:
            # 复用 _get_available_subagents()，与 _get_tools() 保持同一份过滤逻辑
            available_subagents = self._get_available_subagents()
            lines = []
            for name in available_subagents:
                config = self.subagent_registry._configs.get(name)
                if config:
                    lines.append(f"- {name}: {config.description}")
            subagent_descriptions = "\n".join(lines) if lines else "(no subagents available)"
        
        # 委派工具说明（仅主智能体使用）
        delegation_guide = ""
        subagent_matching_hint = ""

        if include_delegation and available_subagents:
            # 从 DelegateToSubagentTool 动态获取使用指南
            if self._delegate_tool:
                delegation_guide = self._delegate_tool.get_usage_guide(subagent_descriptions=subagent_descriptions)
                if delegation_guide:
                    delegation_guide = f"\n### delegate_to_subagent{delegation_guide}\n"
            subagent_matching_hint = f"""
**⚡ 关键：优先判断是否可以直接委派**
在分析需求时，首先检查任务是否属于以下专业领域：
{subagent_descriptions}

**决策逻辑：**
1. 如果任务**只需要委派给一个子智能体**就能完成 → **直接调用`delegate_to_subagent`，不需要`create_plan`**
2. 如果任务**需要多个工具组合或多个步骤** → 先调用`create_plan`创建计划，然后在计划中指定委派
"""

        # 组装模板变量
        available_tools_list = ', '.join([f'`{t}`' for t in available_tools])

        subagent_constraint_section = ""
        if subagent_constraint:
            subagent_constraint_section = f"""

---

## 角色设定与行为约束

{subagent_constraint}
"""

        user_info_section = ""
        if user:
            user_info_section = f"\n\n## 当前用户\n姓名: {user.name}\nID: {user.user_id}\n"
            if user.phone:
                user_info_section += f"手机号：{user.phone}\n"

        # 长期记忆注入：从用户记忆文件加载
        long_term_memory = self._load_long_term_memory(user)

        # 回复风格注入
        reply_style_section = ""
        style_id = self._resolve_reply_style(user)
        if style_id:
            tenant_id_for_style = self._get_effective_tenant_id()
            style_content = self.style_manager.get_style(style_id, tenant_id_for_style)
            if style_content:
                reply_style_section = f"\n\n---\n\n## 回复风格\n\n{style_content}"
            else:
                logger.warning(f"Reply style '{style_id}' not found, skipping")

        if include_delegation:
            template_name = "master_agent.md"
            variables = {
                "subagent_matching_hint": subagent_matching_hint,
                "available_tools_list": available_tools_list,
                "skill_descriptions": skill_descriptions,
                "subagent_descriptions": subagent_descriptions,
                "tool_usage_guides": self._collect_tool_usage_guides(),
                "delegation_guide": delegation_guide,
                "subagent_constraint_section": subagent_constraint_section,
                "long_term_memory": long_term_memory,
                "user_info_section": user_info_section,
                "reply_style_section": reply_style_section,
            }
        else:
            template_name = "subagent_base.md"
            variables = {
                "available_tools_list": available_tools_list,
                "skill_descriptions": skill_descriptions,
                "tool_usage_guides": self._collect_tool_usage_guides(),
                "subagent_constraint_section": subagent_constraint_section,
                "long_term_memory": long_term_memory,
                "user_info_section": user_info_section,
                "reply_style_section": reply_style_section,
            }

        return self.prompt_manager.render(template_name, variables)
    
    def _build_system_prompt(self, user: Optional[User] = None) -> str:
        """
        Build system prompt for the agent

        MASTER：包含委派能力
        SUBAGENT / STANDALONE：不包含委派能力，使用子智能体配置的约束 + 租户定制 extra.md
        """
        if self.mode == AgentMode.MASTER:
            return self._build_base_system_prompt(include_delegation=True, user=user)
        else:
            subagent_constraint = ""
            if self.subagent_config:
                if getattr(self.subagent_config, 'from_db', False):
                    # DB 子智能体：模板 + 运行时变量渲染
                    subagent_constraint = self._resolve_db_subagent_prompt()
                else:
                    # 文件系统子智能体：直接用 system_prompt
                    subagent_constraint = self.subagent_config.system_prompt

                # 注入租户级知识库约束
                ks = self._load_knowledge_sources()
                if ks:
                    lines = ["", "## 可用知识库", ""]
                    lines.append("你可以通过 knowledge_base_search 工具检索以下知识库：")
                    for src in ks:
                        st = src.get('source_type', '')
                        dn = src.get('display_name', st)
                        lines.append(f"- {st}（{dn}）")
                    lines.append("调用时必须传入正确的 source_type 参数。")
                    subagent_constraint += "\n".join(lines)

            # 加载租户定制 extra.md
            extra_content = self._load_extra_md()
            if extra_content:
                subagent_constraint = subagent_constraint + "\n\n## 租户定制需求\n\n" + extra_content

            return self._build_base_system_prompt(
                include_delegation=False,
                subagent_constraint=subagent_constraint,
                user=user
            )

    def _resolve_db_subagent_prompt(self) -> str:
        """DB 子智能体的 system_prompt 实时渲染：模板 + sections 变量"""
        agent_id = self.subagent_config.dir_name

        # 1. 获取模板（从 prompt_versions production 版本，走缓存）
        from src.prompts.prompt_resolver import prompt_resolver
        template = prompt_resolver.resolve(scope="subagent", scope_id=agent_id)
        if not template:
            # 降级到 config.system_prompt（load_from_db 时存的模板内容）
            return self.subagent_config.system_prompt or ""

        # 2. 获取 sections 变量值（走缓存）
        from src.core.cache_utils import CacheKeys, get_cached, set_cached, delete_cached
        cached_sections = get_cached(CacheKeys.PROMPT_SECTIONS, agent_id)
        if cached_sections is not None:
            section_map = cached_sections
        else:
            from src.db.subagent_prompt_section_db import SubagentPromptSectionDB
            section_map = SubagentPromptSectionDB.get_sections_map(agent_id)
            set_cached(CacheKeys.PROMPT_SECTIONS, agent_id, value=section_map, ttl=300)

        # 3. 渲染模板
        if section_map:
            from src.prompts.renderer import render_template
            return render_template(template, section_map)

        return template

    def _load_extra_md(self) -> Optional[str]:
        """加载租户定制的 extra.md 文件"""
        if not self.subagent_config:
            return None

        # 解析 tenant_id
        tenant_id = self._init_tenant_id
        if not tenant_id:
            try:
                from src.saas.context import get_current_tenant_id
                tenant_id = get_current_tenant_id()
            except Exception:
                pass

        if not tenant_id or not self.subagent_config.dir_name:
            return None

        # 构建路径: storage/subagents/<dir_name>/extra_<tenant_id>.md
        from pathlib import Path
        extra_path = Path(f"storage/subagents/{self.subagent_config.dir_name}/extra_{tenant_id}.md")

        try:
            if extra_path.exists():
                content = extra_path.read_text(encoding='utf-8').strip()
                if content:
                    logger.debug(f"Loaded extra.md for tenant {tenant_id}, subagent {self.subagent_config.dir_name}")
                    return content
        except Exception as e:
            logger.warning(f"Failed to load extra.md from {extra_path}: {e}")

        return None

    def _load_knowledge_sources(self) -> list:
        """加载租户级子智能体知识库关联"""
        if not self.subagent_config:
            return []

        tenant_id = self._init_tenant_id
        if not tenant_id:
            try:
                from src.saas.context import get_current_tenant_id
                tenant_id = get_current_tenant_id()
            except Exception:
                pass

        if not tenant_id:
            return []

        subagent_name = self.subagent_config.dir_name
        if not subagent_name:
            return []

        try:
            from src.db.subagent_knowledge_source_db import SubagentKnowledgeSourceDB
            sources = SubagentKnowledgeSourceDB.get(tenant_id, subagent_name)
            if sources:
                logger.debug(f"Loaded {len(sources)} knowledge sources for tenant {tenant_id}, subagent {subagent_name}")
            return sources
        except Exception as e:
            logger.warning(f"Failed to load knowledge sources: {e}")
            return []

    def _load_long_term_memory(self, user: Optional[User] = None) -> str:
        """
        加载用户长期记忆，格式化为注入系统提示词的文本。

        Returns:
            格式化的记忆文本，或空字符串（未启用/无用户/无记忆时）
        """
        if not settings.memory.long_term.enabled:
            return ""

        if not user:
            return ""

        try:
            tenant_id = self._get_effective_tenant_id()
            from src.memory.long_term import LongTermMemory
            ltm = LongTermMemory(storage_dir=settings.memory.long_term.storage_dir)
            return ltm.get_memory_for_injection(
                tenant_id=tenant_id,
                user_id=user.user_id,
                max_tokens=settings.memory.long_term.max_inject_tokens,
            )
        except Exception as e:
            logger.warning(f"Failed to load long-term memory for user {user.user_id}: {e}")
            return ""

    def _resolve_reply_style(self, user: Optional[User] = None) -> Optional[str]:
        """
        解析当前应使用的回复风格（优先级从高到低）：
        0. 用户长期记忆中的 reply_style（用户主动设定，最高优先）
        1. 数字员工实例级别（agent_instances.reply_style_id）
        2. 子智能体/独立模式且配置了 reply_style
        3. 全局默认（config.yaml 中 agent.reply_style）
        """
        # 优先级 0（最高）：用户长期记忆中的 reply_style
        if user and settings.memory.long_term.enabled:
            try:
                tenant_id = self._get_effective_tenant_id()
                from src.memory.long_term import LongTermMemory
                ltm = LongTermMemory(storage_dir=settings.memory.long_term.storage_dir)
                user_style = ltm.get_reply_style(
                    tenant_id=tenant_id,
                    user_id=user.user_id,
                )
                if user_style:
                    return user_style
            except Exception as e:
                logger.warning(f"Failed to read user reply_style from memory: {e}")

        # 优先级 1：数字员工实例级别
        if self._instance_id:
            try:
                from src.saas.db.agent_instance_db import AgentInstanceDB
                inst = AgentInstanceDB.get_by_id(self._instance_id)
                if inst and inst.get("reply_style_id"):
                    return inst["reply_style_id"]
            except Exception as e:
                logger.debug(f"Failed to resolve instance reply_style: {e}")

        # 优先级 2：子智能体/独立模式且配置了 reply_style
        if self.mode != AgentMode.MASTER and self.subagent_config:
            if self.subagent_config.reply_style:
                return self.subagent_config.reply_style

        # 优先级 3：全局默认
        agent_cfg = getattr(settings, 'agent', None)
        if agent_cfg:
            return getattr(agent_cfg, 'reply_style', None)
        return None

    def _get_effective_tenant_id(self) -> Optional[str]:
        """获取当前有效的 tenant_id"""
        tenant_id = self._init_tenant_id
        if not tenant_id:
            try:
                from src.saas.context import get_current_tenant_id
                tenant_id = get_current_tenant_id()
            except Exception:
                pass
        return tenant_id

    async def _handle_remember_intent(self, user_input: str, user: Optional[User]) -> None:
        """
        检测用户"记住"意图，将内容写入长期记忆文件。

        支持的表达方式：帮我记住...、记住...、以后记住...、记一下...
        """
        if not settings.memory.long_term.enabled or not user:
            return

        # 检测"记住"类意图
        remember_patterns = [
            r'^帮我记住[：:\s]*(.+)',
            r'^记住[：:\s]*(.+)',
            r'^以后记住[：:\s]*(.+)',
            r'^记一下[：:\s]*(.+)',
            r'^请记住[：:\s]*(.+)',
            r'^帮我记[：:\s]*(.+)',
        ]

        content_to_remember = None
        for pattern in remember_patterns:
            match = re.match(pattern, user_input.strip(), re.IGNORECASE)
            if match:
                content_to_remember = match.group(1).strip()
                break

        if not content_to_remember:
            return

        try:
            tenant_id = self._get_effective_tenant_id()
            from src.memory.long_term import LongTermMemory
            ltm = LongTermMemory(storage_dir=settings.memory.long_term.storage_dir)

            # 检测回复风格设定意图
            style_match = re.match(
                r'^回复风格[是为用]\s*(.+)',
                content_to_remember,
                re.IGNORECASE,
            )
            if not style_match:
                style_match = re.match(
                    r'^(.+?)风格(?:回复|回答|交流)?',
                    content_to_remember,
                    re.IGNORECASE,
                )
            if style_match:
                raw_style = style_match.group(1).strip()
                # 模糊匹配风格 ID
                tenant_id_for_style = self._get_effective_tenant_id()
                available = self.style_manager.list_styles(tenant_id_for_style)
                matched_id = self._fuzzy_match_style(raw_style, available)
                if matched_id:
                    ltm.set_reply_style(tenant_id=tenant_id, user_id=user.user_id, style_id=matched_id)
                    logger.info(f"User {user.user_id} set reply style to: {matched_id}")
                    return
                else:
                    logger.warning(f"User tried to set unknown reply style: {raw_style}, available: {available}")

            ltm.merge_memory(
                tenant_id=tenant_id,
                user_id=user.user_id,
                new_sections={
                    "用户明确要求记住的事项": [content_to_remember]
                },
            )
            logger.info(f"Remembered for user {user.user_id}: {content_to_remember[:50]}")
        except Exception as e:
            logger.warning(f"Failed to save 'remember' intent: {e}")

    def _fuzzy_match_style(self, raw: str, available: list) -> Optional[str]:
        """模糊匹配风格 ID：精确匹配 > 包含匹配"""
        raw_lower = raw.lower().strip()
        # 精确匹配
        if raw_lower in available:
            return raw_lower
        # 包含匹配（用户说"拟人"匹配 "human-like"）
        style_aliases = {
            "拟人": "human-like",
            "拟人化": "human-like",
            "像人": "human-like",
            "像真人": "human-like",
            "专业": "professional",
            "极简": "concise",
            "简洁": "concise",
            "详尽": "detailed",
            "详细": "detailed",
        }
        for alias, style_id in style_aliases.items():
            if alias in raw_lower:
                if style_id in available:
                    return style_id
        return None

    def _load_channel_history(
        self,
        session_id: str,
        current_user_input: str,
    ) -> List[Dict[str, Any]]:
        """
        从 channel_messages 表加载渠道（企业微信/钉钉/飞书）的对话历史。

        仅在 chat_messages 表无数据时作为 fallback 使用。
        渠道处理器会预先将当前用户消息存入 channel_messages，
        因此需要跳过最后一条 user 消息以避免重复。
        """
        try:
            from src.channels.session import channel_session_manager
            # 多加载一条，用于判断最后一条是否是当前用户消息
            channel_msgs = channel_session_manager.get_messages(
                session_id,
                limit=self.memory.short_term.max_messages + 1,
            )
            if not channel_msgs:
                return []

            # get_messages 已返回 ASC（时间正序，SQL 中 ORDER BY created_at ASC）
            # 无需反转，无需截断（SQL 中 LIMIT 已控制数量）

            # 如果最后一条是 user 消息，说明是渠道处理器预先存入的当前消息，
            # 需要移除（process_message 后续会通过 memory.add 添加 enhanced 版本）
            if channel_msgs and channel_msgs[-1].get("role") == "user":
                last_content = channel_msgs[-1].get("content", "")
                if last_content == current_user_input:
                    channel_msgs.pop()

            return [
                {
                    "role": msg["role"],
                    "content": msg["content"] or "",
                    "timestamp": msg.get("created_at", ""),
                }
                for msg in channel_msgs
            ]
        except Exception as e:
            logger.warning(f"Failed to load channel history for session {session_id}: {e}")
            return []

    def _build_messages(
        self,
        session_id: str
    ) -> List[Dict[str, Any]]:
        """
        Build message list for LLM from memory.

        健壮性保障：
        1. 跳过空 content 的 user/assistant 消息（防止空 user 导致 API 报错）
        2. assistant(tool_calls) 后必须紧跟对应的 tool 消息，否则清理断裂的 tool_calls
        3. system 消息转为 user 消息（部分 LLM API 不允许在对话序列中插入 system）
        4. 孤立的 tool 消息（无 pending tool_call_id）会被跳过
        """
        messages = []

        history = self.memory.get_context(session_id)
        history_roles = []
        for m in history:
            role = m.get('role', '?')
            content = m.get('content', '')
            if not isinstance(content, str):
                content = str(content)[:30]
            else:
                content = content[:30]
            history_roles.append(f"{role}:{content}")
        logger.debug(f"_build_messages: session_id={session_id}, history_count={len(history)}, msgs={history_roles}")

        # 追踪待处理的 tool_call_ids
        pending_tool_calls = set()
        # 追踪每条带 tool_calls 的 assistant 消息在 messages 中的索引
        assistant_tc_indices = []

        for i, msg in enumerate(history):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            if role == "tool":
                # 工具结果消息：只添加属于 pending_tool_calls 的
                tc_id = msg.get("tool_call_id", "")
                if tc_id in pending_tool_calls:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": content
                    })
                    pending_tool_calls.discard(tc_id)
                else:
                    # 孤立的 tool 消息，跳过
                    logger.debug(f"后端日志：_build_messages 跳过孤立 tool 消息, tool_call_id={tc_id}")
            elif role == "assistant":
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    # 有待处理的 tool_calls 说明之前的 assistant(tool_calls) 缺少 tool 响应
                    # 清理之前未配对的 tool_calls
                    if pending_tool_calls:
                        logger.warning(
                            f"后端日志：_build_messages 发现 {len(pending_tool_calls)} 个未配对 tool_calls，"
                            f"移除前一条 assistant 的 tool_calls"
                        )
                        if assistant_tc_indices:
                            prev_idx = assistant_tc_indices[-1]
                            orphaned_ids = list(pending_tool_calls)
                            # 从前一条 assistant 中移除未配对的 tool_calls
                            prev_tc = messages[prev_idx].get("tool_calls", [])
                            remaining_tc = [tc for tc in prev_tc if tc.get("id", "") not in pending_tool_calls]
                            if remaining_tc:
                                messages[prev_idx]["tool_calls"] = remaining_tc
                            else:
                                # 所有 tool_calls 都未配对，降级为普通 assistant
                                messages[prev_idx].pop("tool_calls", None)
                            logger.warning(f"后端日志：已清理未配对 tool_call_ids: {orphaned_ids}")
                            pending_tool_calls.clear()
                            assistant_tc_indices.pop()
                    
                    # 记录待处理的 tool_call_ids
                    tc_ids = set()
                    for tc in tool_calls:
                        tc_id = tc.get("id", "")
                        if tc_id:
                            tc_ids.add(tc_id)
                    
                    # 添加带 tool_calls 的 assistant 消息
                    asst_msg = {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls
                    }
                    if msg.get("reasoning_content"):
                        asst_msg["reasoning_content"] = msg["reasoning_content"]
                    messages.append(asst_msg)
                    assistant_tc_indices.append(len(messages) - 1)
                    pending_tool_calls = tc_ids
                else:
                    # 普通 assistant message，跳过空 content
                    if content:
                        asst_msg = {"role": "assistant", "content": content}
                        if msg.get("reasoning_content"):
                            asst_msg["reasoning_content"] = msg["reasoning_content"]
                        messages.append(asst_msg)
            else:
                # system 消息转为 user 消息（LLM API 不允许对话序列中插入 system）
                # 跳过空 content 的消息
                if content:
                    messages.append({
                        "role": "user",
                        "content": content
                    })
        
        # 后端日志：检查末尾是否有未响应的 tool_calls
        if pending_tool_calls:
            logger.warning(
                f"后端日志：_build_messages 末尾存在 {len(pending_tool_calls)} 个未响应的 tool_call_ids: "
                f"{pending_tool_calls}，将清理对应的 tool_calls"
            )
            if assistant_tc_indices:
                last_idx = assistant_tc_indices[-1]
                prev_tc = messages[last_idx].get("tool_calls", [])
                remaining_tc = [tc for tc in prev_tc if tc.get("id", "") not in pending_tool_calls]
                if remaining_tc:
                    messages[last_idx]["tool_calls"] = remaining_tc
                else:
                    messages[last_idx].pop("tool_calls", None)
        
        # 最终清理：确保 messages 列表中不存在相邻的 assistant(tool_calls) + 非 tool 消息
        # 如果仍有断裂，移除断裂的 tool_calls
        self._repair_message_sequence(messages)

        result_roles = []
        for m in messages:
            role = m.get('role', '?')
            content = m.get('content', '')
            if not isinstance(content, str):
                content = str(content)[:30]
            else:
                content = content[:30]
            result_roles.append(f"{role}:{content}")
        logger.debug(f"_build_messages result: session_id={session_id}, count={len(messages)}, msgs={result_roles}")

        return messages
    
    def _repair_message_sequence(self, messages: List[Dict[str, Any]]) -> None:
        """
        修复消息序列：确保 assistant(tool_calls) 后面紧跟 tool 消息。
        如果 assistant(tool_calls) 后面是非 tool 消息，移除其 tool_calls。
        """
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # 检查下一条消息是否是 tool
                if i + 1 >= len(messages) or messages[i + 1].get("role") != "tool":
                    # 下一条不是 tool，需要检查这个 assistant 的 tool_calls
                    # 是否有对应的 tool 在后续消息中
                    tc_ids = {tc.get("id", "") for tc in msg["tool_calls"] if tc.get("id")}
                    # 向后查找所有 tool 消息
                    found_tool_ids = set()
                    for j in range(i + 1, len(messages)):
                        if messages[j].get("role") == "tool":
                            tc_id = messages[j].get("tool_call_id", "")
                            if tc_id in tc_ids:
                                found_tool_ids.add(tc_id)
                    
                    if not found_tool_ids:
                        # 完全没有对应的 tool 消息，移除 tool_calls
                        logger.warning(
                            f"后端日志：_repair_message_sequence 移除断裂的 tool_calls "
                            f"at index {i}, tc_ids={tc_ids}"
                        )
                        msg.pop("tool_calls", None)
                    else:
                        # 部分匹配，保留匹配的 tool_calls
                        remaining = [tc for tc in msg["tool_calls"] if tc.get("id", "") in found_tool_ids]
                        if remaining != msg["tool_calls"]:
                            removed = [tc.get("id") for tc in msg["tool_calls"] if tc.get("id", "") not in found_tool_ids]
                            logger.warning(
                                f"后端日志：_repair_message_sequence 部分移除断裂的 tool_calls "
                                f"at index {i}, removed={removed}"
                            )
                            msg["tool_calls"] = remaining
            i += 1

    # ─── 压缩 Skill 上下文（保留在 Agent 上，因为操作 Agent 内部状态） ───

    def _compress_skill_context(
        self,
        session_id: str,
        skill_name: str,
        summary: str
    ) -> None:
        """压缩 Skill 执行过程中的中间消息，仅保留摘要。委托给 SkillCompleteTool。"""
        self._skill_complete_tool.set_context(
            active_sessions=self._active_skill_sessions,
            memory_cache=self.memory._cache,
        )
        self._skill_complete_tool.compress(session_id, skill_name, summary)

    @property
    def has_active_skill_session(self) -> bool:
        """是否有活跃的 Skill Session"""
        return bool(self._active_skill_sessions)

    async def _delegate_to_subagent_direct(
        self,
        subagent_name: str,
        task_description: str,
        context_needed: Optional[List[str]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Handle delegate_to_subagent tool call - delegate task to a subagent

        Args:
            subagent_name: Name of the subagent to delegate to
            task_description: Description of the task
            context_needed: Keywords for context filtering (optional)
            session_id: Session ID for memory access

        Returns:
            Delegation result dictionary
        """
        if not subagent_name:
            return {
                "success": False,
                "error": "No subagent name provided"
            }
        
        if not task_description:
            return {
                "success": False,
                "error": "No task description provided"
            }
        
        # Check if subagent exists（registry → DB 按需加载）
        config = self.subagent_registry.get(subagent_name)
        if not config:
            from src.subagents.factory import AgentFactory
            config = AgentFactory._load_single_from_db(self.subagent_registry, subagent_name)
        if not config:
            return {
                "success": False,
                "error": f"Subagent '{subagent_name}' not found",
                "available_subagents": self.subagent_registry.list_subagents()
            }
        
        try:
            # Generate task ID
            import uuid
            task_id = f"delegate_{uuid.uuid4().hex[:8]}"

            # Delegate to subagent
            response = await self.subagent_executor.delegate(
                task_id=task_id,
                subagent_name=subagent_name,
                task_description=task_description,
                session_id=session_id or "default",
            )
            
            if not response.success:
                return {
                    "success": False,
                    "error": response.error or "Delegation failed"
                }
            
            # Wait for result
            record = await self.subagent_executor.wait_for_result(
                response.execution_id,
                timeout=7200  # 2 hours timeout
            )
            
            if record:
                # 检查是否为 CLARIFYING 状态（子智能体需要用户补充信息）
                if record.is_clarifying():
                    question = record.clarification_request or "需要补充信息"
                    logger.info(f"[AGENT] Subagent '{subagent_name}' requesting clarification: {question[:100]}...")
                    
                    # 保存 pending clarification 上下文到 Redis，供用户回复后使用
                    self._save_pending_clarification(
                        session_id or "default",
                        {
                            "subagent_name": subagent_name,
                            "execution_id": response.execution_id,
                            "task_description": task_description,
                            "question": question,
                            "missing_info": record.clarification_answer or "",  # 暂时存空，后续用 answer_clarification 更新
                        },
                    )
                    
                    return {
                        "success": False,
                        "subagent_name": subagent_name,
                        "execution_id": response.execution_id,
                        "status": "clarifying",
                        "question": question,
                        "error": f"子智能体 '{subagent_name}' 需要补充信息：{question}",
                    }
                
                return {
                    "success": record.status == "completed",
                    "subagent_name": subagent_name,
                    "execution_id": response.execution_id,
                    "result": record.result,
                    "summary": record.summary,
                    "error": record.error,
                    "token_usage": record.token_usage
                }
            
            return {
                "success": False,
                "error": "Delegation timed out"
            }
            
        except Exception as e:
            logger.error(f"Delegation failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def process_message(
        self,
        user_input: str,
        session_id: str,
        user: Optional[User] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Process a user message and yield AgentEvent dicts (trace-wrapped).

        Wrapper around `_process_message_impl` that attaches a TraceCollector
        when a SessionRecordService is available, so all channels (Web/wecom/
        wecom_kf/dingtalk/feishu) produce traces without any caller-side change.
        Trace failures are swallowed (debug log) and never affect business logic.

        See: docs/infrastructure/observability-channel-sessions-design.md
        """
        trace_collector = None
        try:
            from src.services.session_record import SessionRecordManager
            _record = getattr(self, '_explicit_record_service', None) \
                      or SessionRecordManager.get_current_record()
            if _record:
                try:
                    from src.core.trace_collector import TraceCollector
                    trace_collector = TraceCollector(
                        session_id=_record.session_id or session_id,
                        tenant_id=_record.tenant_id or '',
                        user_id=_record.user_id or '',
                        input_msg=_record.user_message or user_input,
                        source_type=_record.source_type or 'chat',
                        subagent_id=getattr(self, '_subagent_id', None),
                    )
                except Exception as e:
                    logger.debug(f"Trace collector init skipped: {e}")
                    trace_collector = None
        except Exception as e:
            logger.debug(f"Trace context resolve skipped: {e}")

        try:
            async for event in self._process_message_impl(
                user_input=user_input,
                session_id=session_id,
                user=user,
                attachments=attachments,
                cancel_check=cancel_check,
            ):
                if trace_collector:
                    try:
                        trace_collector.on_event(event)
                    except Exception as e:
                        logger.debug(f"Trace on_event failed: {e}")
                yield event
        except Exception as e:
            if trace_collector:
                try:
                    trace_collector.on_error(str(e))
                except Exception as ce:
                    logger.debug(f"Trace on_error failed: {ce}")
            raise
        finally:
            if trace_collector:
                try:
                    trace_collector.on_complete(_record)
                except Exception as e:
                    logger.debug(f"Trace on_complete failed: {e}")

    async def _process_message_impl(
        self,
        user_input: str,
        session_id: str,
        user: Optional[User] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Process a user message and yield AgentEvent dicts.

        Event types yielded:
        - response:      Text content for the user (frontend appends to message bubble)
        - progress:      Progress text
        - tool_start:    Tool execution started
        - tool_result:   Tool execution completed
        - thinking:      Thinking process
        - clarification: Subagent needs more info
        - complete:      Processing finished
        - error:         Error occurred
        - cancelled:     User cancelled
        """
        import base64
        import tempfile
        from datetime import datetime
        from src.core.agent_events import make_event

        # 后端日志：检查是否有待处理的澄清请求
        pending_clarification = self._get_pending_clarification(session_id)
        if pending_clarification and self.is_master:
            # 用户正在回复子智能体的澄清请求
            clarification = pending_clarification
            subagent_name = clarification["subagent_name"]
            original_task = clarification["task_description"]
            original_question = clarification["question"]
            
            logger.info(f"[AGENT] User replying to clarification from '{subagent_name}': {user_input[:100]}...")

            # 清除 pending 状态
            self._clear_pending_clarification(session_id)
            
            # 构建增强的任务描述：原始任务 + 澄清问题和用户回答
            enhanced_task = (
                f"{original_task}\n\n"
                f"[补充信息]\n"
                f"在执行过程中需要确认以下问题：{original_question}\n"
                f"用户补充回答：{user_input}"
            )
            
            yield make_event("progress", data=f"🔄 正在将补充信息提交给 {subagent_name}，继续执行任务...")
            
            # 重新委派给子智能体（携带补充信息）
            redelegate_result = await self._delegate_tool.execute(
                subagent_name=subagent_name,
                task_description=enhanced_task,
                context_needed=None,
                session_id=session_id,
                user_id=user.user_id if user else None,
            )

            # 发送重新委派的结果
            yield make_event("tool_result",
                toolName="delegate_to_subagent",
                result=redelegate_result,
                success=redelegate_result.get("success", False),
            )
            
            if redelegate_result.get("success"):
                summary = redelegate_result.get("summary", "")
                preview = summary[:100] if summary else ""
                yield make_event("progress", data=f"✅ {subagent_name}任务完成（补充信息后）: {preview}...")
                
                # 输出子智能体的结果
                final_result = redelegate_result.get("result")
                if final_result:
                    if isinstance(final_result, str) and final_result.strip():
                        yield make_event("response", data=final_result.strip())
                    elif isinstance(final_result, dict):
                        content = final_result.get("content", "")
                        if content and isinstance(content, str):
                            yield make_event("response", data=content.strip())
            elif redelegate_result.get("status") == "clarifying":
                # 如果 re-delegate 后又需要澄清，再次保存 pending 状态
                new_question = redelegate_result.get("question", "需要补充信息")
                logger.info(f"[AGENT] Subagent '{subagent_name}' requesting clarification again: {new_question[:100]}...")
                self._save_pending_clarification(
                    session_id,
                    {
                        "subagent_name": subagent_name,
                        "execution_id": redelegate_result.get("execution_id", ""),
                        "task_description": enhanced_task,
                        "question": new_question,
                    },
                )
                yield make_event("clarification", subagentName=subagent_name, question=new_question)
                yield make_event("response", data=f"\n❓ **{subagent_name}** 需要进一步补充信息：{new_question}\n请提供以上信息以继续执行任务。")
            else:
                error = redelegate_result.get("error", "重新执行失败")
                yield make_event("progress", data=f"❌ {subagent_name}重新执行失败: {error}")
                yield make_event("response", data=f"\n❌ 重新执行任务失败：{error}")
            
            # 将补充信息保存到记忆中
            self.memory.add(session_id, "user", f"[补充信息回复] {user_input}")
            return

        logger.info(f"Processing message for session {session_id}: {user_input[:50]}...")

        # 按需加载租户自定义 skills
        self._ensure_tenant_skills_loaded()

        # 每次处理前都从 DB 重建 memory，解决 Gunicorn 多 Worker 内存隔离导致的缓存不同步
        try:
            from src.db.models import MessageDB
            import json as _json
            db_messages = MessageDB.list_by_session(
                session_id,
                limit=self.memory.short_term.max_messages,
            )
            # 清除可能过时的内存数据，用 DB 最新历史重建
            self.memory.clear(session_id)
            if db_messages:
                # 重建时保留工具上下文：tool 角色、assistant 的 tool_calls/tool_call_id
                history_messages = []
                for msg in db_messages:
                    role = msg["role"]
                    content = msg["content"] or ""
                    meta = msg.get("metadata") or {}
                    if isinstance(meta, str):
                        try:
                            meta = _json.loads(meta)
                        except Exception:
                            meta = {}

                    if role == "tool":
                        history_messages.append({
                            "role": "tool",
                            "tool_call_id": meta.get("tool_call_id", ""),
                            "content": content,
                            "timestamp": msg.get("created_at", ""),
                        })
                    elif role == "assistant":
                        entry = {
                            "role": "assistant",
                            "content": content,
                            "timestamp": msg.get("created_at", ""),
                        }
                        # 带 tool_calls 的 assistant（决定调工具），从 metadata 恢复
                        if meta.get("tool_calls"):
                            entry["tool_calls"] = meta["tool_calls"]
                        if meta.get("reasoning_content"):
                            entry["reasoning_content"] = meta["reasoning_content"]
                        history_messages.append(entry)
                    else:  # user / system
                        history_messages.append({
                            "role": role,
                            "content": content,
                            "timestamp": msg.get("created_at", ""),
                        })

                self.memory.load_history(session_id, history_messages)
                loaded_roles = []
                for m in history_messages:
                    content = m.get('content', '')
                    if not isinstance(content, str):
                        content = str(content)[:30]
                    else:
                        content = content[:30]
                    tc_flag = ""
                    if m.get("tool_calls"):
                        tc_flag = "[tc]"
                    elif m.get("tool_call_id"):
                        tc_flag = "[tool]"
                    loaded_roles.append(f"{m['role']}{tc_flag}:{content}")
                logger.debug(
                    f"Rebuilt memory from DB for session {session_id}, "
                    f"loaded={len(history_messages)} msgs | {loaded_roles}"
                )
            else:
                # 渠道消息（企业微信/钉钉/飞书）存储在 channel_messages 表，
                # 与 chat_messages 是独立的表，需 fallback 查询
                history_messages = self._load_channel_history(session_id, user_input)
                if history_messages:
                    self.memory.load_history(session_id, history_messages)
                    logger.info(
                        f"Loaded {len(history_messages)} channel history messages "
                        f"for session {session_id}"
                    )
                else:
                    logger.debug(f"No DB history for session {session_id}, memory cleared")
        except Exception as e:
            logger.warning(f"Failed to rebuild memory from DB for session {session_id}: {e}")

        # 设置工具的 user_id / tenant_id
        download_tool = None
        file_output_tools = []  # 注册下载的文件工具（write / cp）
        if user:
            for tool_name in ("email_send", "email_read", "email_list_folders"):
                tool = self.tool_registry.get_tool(tool_name)
                if tool and hasattr(tool, 'set_user_id'):
                    tool.set_user_id(user.user_id)

            # 注入 user_id 到文件下载工具
            download_tool = self.tool_registry.get_tool("register_download_file")
            if download_tool and hasattr(download_tool, 'set_user_id'):
                download_tool.set_user_id(user.user_id)

            # 注入 user_id 到文件输出工具（write / cp 都会注册下载）
            for tool_name in ("write", "cp"):
                tool = self.tool_registry.get_tool(tool_name)
                if tool and hasattr(tool, 'set_user_id'):
                    tool.set_user_id(user.user_id)
                    file_output_tools.append(tool)

        # 注入 tenant_id 到需要租户隔离的工具（子智能体线程中 ContextVar 不可用）
        _resolve_tenant_id = self._init_tenant_id
        if not _resolve_tenant_id:
            try:
                from src.saas.context import get_current_tenant_id
                _resolve_tenant_id = get_current_tenant_id()
            except Exception:
                pass
        if _resolve_tenant_id:
            for tool_name in ("attraction_search", "knowledge_base_search"):
                tool = self.tool_registry.get_tool(tool_name)
                if tool and hasattr(tool, 'set_tenant_id'):
                    tool.set_tenant_id(_resolve_tenant_id)
            # 注入 tenant_id 到文件下载工具
            if download_tool and hasattr(download_tool, 'set_tenant_id'):
                download_tool.set_tenant_id(_resolve_tenant_id)
            # 注入 tenant_id 到文件输出工具
            for tool in file_output_tools:
                if hasattr(tool, 'set_tenant_id'):
                    tool.set_tenant_id(_resolve_tenant_id)

        # 子智能体环境变量注入：从 subagent_env_vars 表读取，设置为 os.environ，供 http_api 工具的 ${VAR} 替换
        _injected_env_vars = {}
        if _resolve_tenant_id and self.mode != AgentMode.MASTER and self.subagent_config:
            try:
                from src.db.subagent_env_var import SubagentEnvVarDB
                subagent_name = self.subagent_config.dir_name
                env_vars = SubagentEnvVarDB.get_vars(_resolve_tenant_id, subagent_name)
                for var in env_vars:
                    var_name = var["var_name"]
                    var_value = var.get("var_value", "")
                    if var_name and var_value:
                        os.environ[var_name] = var_value
                        _injected_env_vars[var_name] = True
                if _injected_env_vars:
                    logger.debug(f"[ENV] Injected {len(_injected_env_vars)} env vars for subagent {subagent_name}")
            except Exception as e:
                logger.warning(f"环境变量注入失败: {e}")
        
        # Add timestamp context to help LLM understand current time
        current_time = datetime.now()
        timestamp_context = (
            f"[当前时间: {current_time.strftime('%Y年%m月%d日 %H:%M:%S')}, "
            f"{current_time.strftime('%A')}, "
            f"今年是{current_time.year}年]\n\n"
        )
        
        enhanced_input = timestamp_context + user_input
        auto_loaded_skill = None
        uploaded_files_info = []
        # 追踪工具执行过程中生成的文件（用于确保下载链接出现在最终回复中）
        generated_files = []  # list of {"file_name": str, "download_url": str}
        session_workspace = None
        
        if attachments:
            attachment_info = []
            for att in attachments:
                att_type = att.get("type", "file")
                att_name = att.get("name", "unknown")
                att_url = att.get("url", "")
                att_mime = att.get("mime_type", "")
                att_content = att.get("content", "")
                
                attachment_info.append(f"- {att_name} ({att_type}, {att_mime or 'unknown type'})")
                
                if self.skill_registry:
                    matched_skill = self.skill_registry.match_by_file(att_name)
                    if matched_skill:
                        auto_loaded_skill = matched_skill
                        logger.info(f"Auto-matched skill '{matched_skill}' for file: {att_name}")
                
                if att_content or att_url:
                    if session_workspace is None:
                        session_workspace = Path(tempfile.mkdtemp(prefix=f"skill_ws_{session_id}_"))
                        logger.info(f"Created session workspace: {session_workspace}")
                    
                    file_path = session_workspace / att_name
                    
                    try:
                        if att_content:
                            file_bytes = base64.b64decode(att_content)
                            file_path.write_bytes(file_bytes)
                            uploaded_files_info.append({
                                "name": att_name,
                                "path": str(file_path),
                                "size": len(file_bytes)
                            })
                            logger.info(f"Saved uploaded file: {file_path} ({len(file_bytes)} bytes)")
                        elif att_url and Path(att_url).exists():
                            import shutil
                            shutil.copy(att_url, file_path)
                            uploaded_files_info.append({
                                "name": att_name,
                                "path": str(file_path),
                                "size": file_path.stat().st_size
                            })
                            logger.info(f"Copied file from URL: {file_path}")
                    except Exception as e:
                        logger.error(f"Failed to save file {att_name}: {e}")
            
            if attachment_info:
                # 构建文件路径信息（无论是否有 auto_loaded_skill 都添加）
                files_context = ""
                if uploaded_files_info:
                    files_context = "\n\n**📎 Uploaded files available:**\n"
                    for f in uploaded_files_info:
                        files_context += f"- File: `{f['name']}`\n"
                        files_context += f"  Full path: `{f['path']}`\n"
                        files_context += f"  Size: {f['size']} bytes\n"
                    files_context += "\n**IMPORTANT: When delegating to subagent, include the file paths above in task_description!**\n"
                
                enhanced_input = timestamp_context + f"{user_input}\n\n[Attachments]\n" + "\n".join(attachment_info) + files_context
        
        self.memory.add(session_id, "user", enhanced_input)

        # 检测用户"记住"意图，写入长期记忆
        await self._handle_remember_intent(user_input, user)

        messages = self._build_messages(session_id)
        system_prompt = self._build_system_prompt(user)
        
        if auto_loaded_skill:
            skill_content = self.skill_registry.get_content(auto_loaded_skill)
            if skill_content:
                skill_injection = f"""<skill-auto-loaded name="{auto_loaded_skill}">
{skill_content}
</skill-auto-loaded>

The above skill has been automatically loaded because you received a file that matches this skill. 
{files_context}
Analyze the user's request and choose the appropriate method from the skill to process the file.
Use `skill_execute` tool to run commands like pdftotext, python scripts, etc."""

                messages.append({
                    "role": "user",
                    "content": skill_injection
                })
                logger.info(f"Auto-injected skill '{auto_loaded_skill}' into conversation with {len(uploaded_files_info)} files")

        # 记录本轮开始时 messages 的长度，用于末尾收集本轮新增的 tool 消息序列
        initial_len = len(messages)

        max_iterations = 20  # Prevent infinite loops
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            logger.debug(f"Agent iteration {iteration}")

            # 检查用户是否已取消
            if cancel_check and cancel_check():
                logger.info(f"[AGENT] Cancelled by user at iteration {iteration}, session_id={session_id}")
                return
            
            tools = self._get_tools()
            
            if settings.app.llm_debug:
                logger.debug(f"\n{'='*60}\n"
                            f"[LLM_DEBUG] Agent Iteration {iteration} - Full Prompt\n"
                            f"{'='*60}\n"
                            f"[System Prompt]:\n{system_prompt}\n"
                            f"{'-'*60}\n"
                            f"[Messages]:\n{json.dumps(messages, ensure_ascii=False, indent=2)}\n"
                            f"{'-'*60}\n"
                            f"[Tools]: {json.dumps([t.get('name', t.get('function', {}).get('name', 'unknown')) for t in tools], ensure_ascii=False)}\n"
                            f"{'='*60}")
            
            # 后端日志：LLM调用开始
            import time
            llm_call_start = time.time()
            logger.info(f"[AGENT] LLM call starting, session_id={session_id}, iteration={iteration}, is_master={self.is_master}")
            
            try:
                response = await self.llm.chat_with_tools(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools
                )
                
                llm_call_duration = time.time() - llm_call_start
                logger.info(f"[AGENT] LLM call completed, session_id={session_id}, iteration={iteration}, duration={llm_call_duration:.2f}s")

                # 追踪：yield LLM 调用事件（含完整 messages 上下文）
                yield make_event("llm_call",
                    request_id=response.get("request_id", ""),
                    model=self.llm.get_model_name(),
                    provider=self.llm.get_provider_name(),
                    usage=response.get("usage", {}),
                    duration_ms=int(llm_call_duration * 1000),
                    messages=messages,
                    tools=tools,
                    system_prompt=system_prompt,
                    response_content=response.get("content", ""),
                )

            except asyncio.TimeoutError as e:
                llm_call_duration = time.time() - llm_call_start
                logger.error(f"[AGENT] LLM call TIMEOUT, session_id={session_id}, iteration={iteration}, duration={llm_call_duration:.2f}s")
                raise
            except Exception as e:
                llm_call_duration = time.time() - llm_call_start
                logger.error(f"[AGENT] LLM call FAILED, session_id={session_id}, iteration={iteration}, duration={llm_call_duration:.2f}s, error: {type(e).__name__}: {e}", exc_info=True)
                raise
            
            tool_calls = response.get("tool_calls", [])
            content = response.get("content", "")
            
            # 后端日志：记录Agent迭代信息（关联LLM request_id）
            log_agent_iteration(
                iteration=iteration,
                request_id=response.get("request_id", ""),
                user_id=user.user_id if user else "",
                session_id=session_id,
                tenant_id=getattr(self, '_init_tenant_id', '') or '',
                model=self.llm.get_model_name(),
                provider=self.llm.get_provider_name(),
                has_tool_calls=bool(tool_calls),
                tool_calls_count=len(tool_calls),
                tool_names=[tc.get("function", {}).get("name", tc.get("name", "")) for tc in tool_calls] if tool_calls else [],
                content_length=len(content) if content else 0,
                usage=response.get("usage"),
            )

            # 累加 token 用量到 SessionRecordService（纯内存操作，异常隔离）
            try:
                from src.services.session_record import SessionRecordManager
                _record = getattr(self, '_explicit_record_service', None) or SessionRecordManager.get_current_record()
                if _record:
                    _record.add_llm_usage(response.get("usage", {}))
                    _record.increment_iterations()
                    if not _record.provider:
                        _record.set_model(self.llm.get_model_name())
                        _record.set_provider(self.llm.get_provider_name())
            except Exception:
                logger.debug(f"Failed to record token usage", exc_info=True)

            if settings.app.llm_debug:
                logger.debug(f"\n{'='*60}\n"
                            f"[LLM_DEBUG] LLM Response - Iteration {iteration}\n"
                            f"{'='*60}\n"
                            f"[Content]:\n{content if content else '(None)'}\n"
                            f"{'-'*60}\n"
                            f"[Tool Calls]: {len(tool_calls)} call(s)\n"
                            f"{json.dumps(tool_calls, ensure_ascii=False, indent=2) if tool_calls else '(None)'}\n"
                            f"{'='*60}")
            
            # Filter out empty tool calls and parse tool info
            valid_tool_calls = []
            for tc in tool_calls:
                # Handle both OpenAI format (tc["function"]["name"]) and simplified format (tc["name"])
                if "function" in tc:
                    tool_name = tc["function"].get("name", "")
                    # arguments might be a JSON string, parse it
                    args_raw = tc["function"].get("arguments", "{}")
                    if isinstance(args_raw, str):
                        try:
                            tool_args = json.loads(args_raw) if args_raw else {}
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse tool arguments: {args_raw[:200]}...")
                            tool_args = {}
                    else:
                        tool_args = args_raw
                else:
                    tool_name = tc.get("name", "")
                    tool_args = tc.get("arguments", {})
                
                # Skip empty tool names
                if not tool_name:
                    logger.warning(f"Skipping tool call with empty name, args: {tool_args}")
                    continue
                
                valid_tool_calls.append({
                    "id": tc.get("id", ""),
                    "name": tool_name,
                    "arguments": tool_args
                })
            
            # If no valid tool calls, we're done
            if not valid_tool_calls:
                # 兜底：自动完成所有活跃的 Skill Session
                if self.has_active_skill_session:
                    for active_skill_name, session in list(self._active_skill_sessions.items()):
                        auto_summary = f"使用技能「{active_skill_name}」执行了相关任务"
                        self._compress_skill_context(session_id, active_skill_name, auto_summary)
                
                # Store assistant response in memory
                reasoning = response.get("reasoning_content")
                if reasoning:
                    self.memory.add_message(session_id, {
                        "role": "assistant",
                        "content": content,
                        "reasoning_content": reasoning,
                    })
                else:
                    self.memory.add(session_id, "assistant", content)

                # 发送最终回复进度
                yield make_event("progress", data="✅ 任务完成，正在生成回复...")

                # Yield the final response
                # DeepSeek 思考模式下 content 可能为空，但 reasoning_content 有内容
                yield_content = content or reasoning or ""
                if yield_content:
                    yield make_event("response", data=yield_content)
                break
            
            # Add assistant message with tool calls to history
            assistant_message = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }
            if response.get("reasoning_content"):
                assistant_message["reasoning_content"] = response["reasoning_content"]
            messages.append(assistant_message)
            
            # Save assistant message with tool calls to memory
            self.memory.add_message(session_id, assistant_message)
            
            # Execute each tool call
            tool_results = []
            # 收集需要延迟执行的 Skill 压缩操作（在 tool_message 写入 memory 后再压缩）
            pending_skill_compressions = []
            for tc in valid_tool_calls:
                tool_name = tc["name"]
                tool_args = tc["arguments"]
                tool_id = tc["id"]

                # 每个工具执行前检查取消
                if cancel_check and cancel_check():
                    logger.info(f"[AGENT] Cancelled by user before tool {tool_name}, session_id={session_id}")
                    return

                # 获取工具的用户友好名称
                tool_display_name = self._get_tool_display_name(tool_name, tool_args)
                # 发送工具开始执行事件
                yield make_event("tool_start", toolName=tool_name, toolArgs=tool_args)
                yield make_event("progress", data=f"🔧 正在执行 {tool_display_name}...")

                logger.info(f"Executing tool: {tool_name} with args: {json.dumps(tool_args, ensure_ascii=False)}")

                # AgentSkills 标准的 allowed-tools 权限检查
                # 如果当前有活跃的 skill session 且该 skill 设置了 allowed_tools，
                # 则只允许执行允许列表中的工具（生命周期工具除外）
                _LIFECYCLE_TOOLS = {"skill_complete", "skill_execute"}
                if self._active_skill_sessions and tool_name not in _LIFECYCLE_TOOLS:
                    for _active_skill_name, _ in self._active_skill_sessions.items():
                        _active_skill_obj = self.skill_registry.get(_active_skill_name) if self.skill_registry else None
                        if _active_skill_obj and _active_skill_obj.allowed_tools:
                            allowed_upper = [t.upper() for t in _active_skill_obj.allowed_tools]
                            if tool_name.upper() not in allowed_upper:
                                logger.warning(f"Tool '{tool_name}' blocked by skill '{_active_skill_name}' allowed_tools: {_active_skill_obj.allowed_tools}")
                                tool_results.append({
                                    "tool_call_id": tool_id,
                                    "content": {
                                        "success": False,
                                        "error": f"Tool '{tool_name}' is not allowed in skill '{_active_skill_name}'. Allowed: {_active_skill_obj.allowed_tools}"
                                    }
                                })
                                yield make_event("tool_result", toolName=tool_name, result={"success": False, "error": f"Tool '{tool_name}' not allowed"}, success=False)
                                continue
                            break  # 找到匹配的活跃 skill 后停止检查

                # Fallback: 如果 LLM 调用了一个不在工具列表中但匹配 skill 名称的工具，
                # 自动转为 use_skill 调用（LLM 有时会误把 skill 名称当成工具名直接调用）
                known_tool_names = {t["name"] for t in self._get_tools()}
                if tool_name not in known_tool_names and self.skill_registry:
                    matched_skill = self.skill_registry.get(tool_name)
                    if matched_skill:
                        original_name = tool_name
                        logger.info(f"[AGENT] Auto-mapping unknown tool '{original_name}' to use_skill(skill='{original_name}')")
                        tool_name = "use_skill"
                        tool_args = {"skill": original_name}
                        # 更新显示名称
                        tool_display_name = self._get_tool_display_name(tool_name, tool_args)

                # Handle create_scheduled_task - 创建定时任务（通过独立 tool 执行）
                if tool_name == "create_scheduled_task":
                    self._create_scheduled_task_tool.set_context(user, session_id, None)
                    task_result = await self._create_scheduled_task_tool.execute(**tool_args)
                    success = task_result.get("success", False)
                    yield make_event("tool_result", toolName=tool_name, result=task_result, success=success)
                    if success:
                        name = task_result.get("name", "")
                        schedule_desc = task_result.get("schedule_description", "")
                        yield make_event("progress", data=f"✅ 定时任务已创建: {name} ({schedule_desc})")
                    else:
                        yield make_event("progress", data=f"❌ 定时任务创建失败")
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": task_result
                    })
                    continue

                # Handle manage_scheduled_task - 管理定时任务（通过独立 tool 执行）
                if tool_name == "manage_scheduled_task":
                    self._manage_scheduled_task_tool.set_context(user)
                    task_result = await self._manage_scheduled_task_tool.execute(**tool_args)
                    success = task_result.get("success", False)
                    yield make_event("tool_result", toolName=tool_name, result=task_result, success=success)
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": task_result
                    })
                    continue

                # Handle create_plan specially - create real plan and save to MD
                if tool_name == "create_plan":
                    plan_result = await self._create_plan_tool.execute(
                        **tool_args,
                        session_id=session_id,
                        user_query=user_input,
                    )
                    # 发送工具执行结果
                    yield make_event("tool_result", toolName=tool_name, result=plan_result, success=plan_result.get("success", True))
                    yield make_event("progress", data=f"📋 执行计划已创建")
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": plan_result
                    })
                    continue

                # Handle clarify - ask user for clarification (no external tool needed)
                if tool_name == "clarify":
                    clarify_result = await self._clarify_tool.execute(**tool_args)
                    # 发送工具执行结果
                    yield make_event("tool_result", toolName=tool_name, result=clarify_result, success=True)
                    yield make_event("progress", data=f"❓ 需要澄清: {clarify_result.get('question', '')[:50]}...")
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": clarify_result
                    })
                    continue

                # Handle use_skill - load skill content and inject into conversation
                if tool_name == "use_skill":
                    skill_name = tool_args.get("skill", "")

                    # 构建 AgentSkills 标准字符串替换上下文
                    skill_obj = self.skill_registry.get(skill_name) if self.skill_registry else None
                    substitutions = {
                        "session_id": session_id,
                        "skill_dir": str(skill_obj.dir) if skill_obj else "",
                        "user_id": user.user_id if user else "",
                        "arguments": tool_args.get("arguments", ""),
                    }
                    skill_result = await self._use_skill_tool.execute(
                        **tool_args,
                        _substitutions=substitutions,
                    )

                    # 执行 onLoad hook（AgentSkills 标准）
                    if skill_result.get("success") and skill_obj and skill_obj.hooks:
                        from src.core.skill_hooks import SkillHooks
                        hook_output = await SkillHooks.run_on_load(skill_obj.hooks, skill_obj.dir)
                        if hook_output:
                            skill_result["content"] = skill_result.get("content", "") + f"\n\n**Hook output:**\n{hook_output}"

                    # 创建 Skill Session，记录当前 memory 消息数量
                    if skill_result.get("success") and skill_name not in self._active_skill_sessions:
                        msg_count = self.memory.get_message_count(session_id)
                        self._active_skill_sessions[skill_name] = SkillSession(
                            skill_name=skill_name,
                            start_index=msg_count,
                            message_count_before=msg_count,
                        )
                        logger.info(f"后端日志：创建 SkillSession", extra={
                            "skill_name": skill_name,
                            "message_count_before": msg_count
                        })
                    # 发送工具执行结果
                    yield make_event("tool_result", toolName=tool_name, result=skill_result, success=skill_result.get("success", True))
                    yield make_event("progress", data=f"📦 已加载技能: {skill_name}")
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": skill_result
                    })
                    continue

                # Handle skill_complete - compress skill context
                if tool_name == "skill_complete":
                    skill_name = tool_args.get("skill", "")
                    summary = tool_args.get("summary", "")
                    if skill_name in self._active_skill_sessions:
                        # 执行 onUnload hook（AgentSkills 标准）
                        skill_obj = self.skill_registry.get(skill_name) if self.skill_registry else None
                        if skill_obj and skill_obj.hooks:
                            from src.core.skill_hooks import SkillHooks
                            hook_output = await SkillHooks.run_on_unload(skill_obj.hooks, skill_obj.dir)
                            if hook_output:
                                summary = f"{summary}\n\n**Hook output:**\n{hook_output}"

                        # 延迟压缩：先记录压缩信息，等 tool_message 写入 memory 后再执行
                        pending_skill_compressions.append((session_id, skill_name, summary))
                        yield make_event("progress", data=f"✅ 技能「{skill_name}」执行完成")
                        tool_results.append({
                            "tool_call_id": tool_id,
                            "content": {"success": True, "message": f"技能 {skill_name} 已完成并清理上下文"}
                        })
                    else:
                        tool_results.append({
                            "tool_call_id": tool_id,
                            "content": {"success": False, "error": f"没有找到活跃的技能会话: {skill_name}"}
                        })
                    continue

                # Handle skill_execute - execute command directly
                if tool_name == "skill_execute":
                    skill_name = tool_args.get("skill", "")
                    command = tool_args.get("command", "") or None  # 空字符串转为 None
                    files = tool_args.get("files", {})
                    content = tool_args.get("content")

                    # 标记任务开始（如果计划中存在）
                    plan = self.plan_manager.get_plan(session_id)
                    skill_task_id = None
                    if plan:
                        task = self.plan_manager.get_next_pending_task(session_id)
                        if task and task.tool_name == "skill_execute":
                            skill_task_id = task.task_id
                            self.plan_manager.mark_task_running(session_id, skill_task_id)

                    skill_exec_result = await self._skill_execute_tool.execute(
                        skill=skill_name,
                        command=command,
                        files=files,
                        content=content,
                        session_id=session_id,
                        workdir=session_workspace
                    )

                    # 后端日志：记录 skill_execute 执行结果
                    log_skill_execute(
                        skill_name=skill_name,
                        command=command or "",
                        session_id=session_id,
                        user_id=user.user_id if user else "",
                        success=skill_exec_result.get("success", False),
                        exit_code=skill_exec_result.get("exit_code", 0),
                        stdout=skill_exec_result.get("stdout", ""),
                        stderr=skill_exec_result.get("stderr", ""),
                        error=skill_exec_result.get("error", ""),
                        duration=skill_exec_result.get("duration", 0),
                        input_content=str(content) if content else "",
                    )

                    # 发送技能执行完成进度
                    # 发送工具执行结果
                    yield make_event("tool_result", toolName=tool_name, result=skill_exec_result, success=skill_exec_result.get("success", True))
                    if skill_exec_result.get("success"):
                        stdout = skill_exec_result.get("stdout", "")
                        preview = stdout[:100] if stdout else ""
                        yield make_event("progress", data=f"✅ 技能「{skill_name}」执行完成: {preview}...")
                    else:
                        error = skill_exec_result.get("error") or "未知错误"
                        stderr = skill_exec_result.get("stderr", "")
                        exit_code = skill_exec_result.get("exit_code", -1)
                        detail = error
                        if stderr:
                            # 取 stderr 末尾 300 字符作为错误详情
                            detail = stderr[-300:] if len(stderr) > 300 else stderr
                        elif exit_code:
                            detail = f"exit_code={exit_code}"
                        yield make_event("progress", data=f"❌ 技能「{skill_name}」执行失败: {detail}")

                    # 标记任务完成（使用保存的task_id）
                    if skill_task_id:
                        if skill_exec_result.get("success"):
                            self.plan_manager.mark_task_completed(
                                session_id, skill_task_id, skill_exec_result
                            )
                        else:
                            self.plan_manager.mark_task_failed(
                                session_id, skill_task_id,
                                skill_exec_result.get("error", "Unknown error")
                            )

                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": skill_exec_result
                    })
                    continue
                
                # Handle delegate_to_subagent - delegate task to subagent
                if tool_name == "delegate_to_subagent":
                    subagent_name = tool_args.get("subagent_name", "")
                    task_description = tool_args.get("task_description", "")
                    context_needed = tool_args.get("context_needed", [])

                    logger.info(f"Delegating to subagent: {subagent_name}, task: {task_description[:50]}...")
                    yield make_event("progress", data=f"🚀 正在调用{subagent_name}子智能体处理任务...")

                    # 标记任务开始（如果计划中存在）
                    plan = self.plan_manager.get_plan(session_id)
                    delegate_task_id = None
                    if plan:
                        task = self.plan_manager.get_next_pending_task(session_id)
                        if task:
                            delegate_task_id = task.task_id
                            self.plan_manager.mark_task_running(session_id, delegate_task_id)

                    # 执行委派
                    delegation_result = await self._delegate_tool.execute(
                        subagent_name=subagent_name,
                        task_description=task_description,
                        context_needed=context_needed,
                        session_id=session_id,
                        user_id=user.user_id if user else None,
                    )

                    # 发送工具执行结果
                    yield make_event("tool_result", toolName=tool_name, result=delegation_result, success=delegation_result.get("success", True))

                    # 检查子智能体是否需要澄清（需要用户补充信息）
                    if delegation_result.get("status") == "clarifying":
                        question = delegation_result.get("question", "需要补充信息")
                        yield make_event("progress", data=f"❓ {subagent_name}需要补充信息: {question[:50]}...")
                        yield make_event("clarification", subagentName=subagent_name, question=question)
                        yield make_event("response", data=f"\n❓ **{subagent_name}** 需要补充信息：{question}\n请提供以上信息，系统将自动继续执行任务。")
                    elif delegation_result.get("success"):
                        # 子智能体执行完成进度
                        summary = delegation_result.get("summary", "")
                        preview = summary[:100] if summary else ""
                        yield make_event("progress", data=f"✅ {subagent_name}子智能体任务完成: {preview}...")
                    else:
                        error = delegation_result.get("error", "未知错误")
                        yield make_event("progress", data=f"❌ {subagent_name}子智能体执行失败: {error}")

                    # 如果子智能体生成了内容（content_generate），实时展示给用户
                    if delegation_result.get("generated_contents"):
                        for content in delegation_result["generated_contents"]:
                            yield make_event("response", data=f"\n<!--process-->\n📝 **内容生成结果：**\n\n{content}\n\n<!--/process-->\n")


                    # 标记任务完成（澄清状态不标记为失败）
                    if delegate_task_id:
                        if delegation_result.get("success"):
                            self.plan_manager.mark_task_completed(
                                session_id, delegate_task_id, delegation_result
                            )
                        elif delegation_result.get("status") != "clarifying":
                            # 只有非澄清状态的失败才标记为失败
                            self.plan_manager.mark_task_failed(
                                session_id, delegate_task_id,
                                delegation_result.get("error", "Unknown error")
                            )

                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": delegation_result
                    })
                    continue
                
                # 获取当前计划中的任务（用于状态跟踪）
                plan = self.plan_manager.get_plan(session_id)
                current_task_id = None
                if plan:
                    current_task = self.plan_manager.get_next_pending_task(session_id)
                    if current_task:
                        current_task_id = current_task.task_id
                        self.plan_manager.mark_task_running(session_id, current_task_id)

                # Execute the tool
                try:
                    result = await self.tool_executor.execute(tool_name, tool_args)
                    logger.info(f"[TOOL_RESULT] {tool_name}: type={type(result).__name__}")

                    # 发送工具执行完成事件
                    tool_display_name = self._get_tool_display_name(tool_name, tool_args)
                    if isinstance(result, dict):
                        success = result.get("success", True)
                        yield make_event("tool_result", toolName=tool_name, result=result, success=success)
                        if success:
                            # 根据不同工具显示不同结果预览
                            if tool_name == "content_generate":
                                content = result.get("content", "")
                                preview = content[:80] + "..." if len(content) > 80 else content
                                yield make_event("progress", data=f"✅ {tool_display_name}完成\n📝 {preview}")
                            elif tool_name == "web_search":
                                results = result.get("results", [])
                                yield make_event("progress", data=f"✅ {tool_display_name}完成，找到{len(results)}条结果")
                            elif tool_name == "email_send":
                                yield make_event("progress", data=f"✅ {tool_display_name}成功")
                            elif tool_name == "read":
                                content = result.get("content", "")
                                preview = content[:80] + "..." if len(content) > 80 else content
                                yield make_event("progress", data=f"✅ {tool_display_name}完成\n📄 {preview}")
                            elif tool_name == "browser_automation":
                                result_text = result.get("result", result.get("message", ""))
                                if result_text:
                                    preview = result_text[:80] + "..." if len(result_text) > 80 else result_text
                                    yield make_event("progress", data=f"✅ {tool_display_name}完成\n{preview}")
                                else:
                                    yield make_event("progress", data=f"✅ {tool_display_name}成功")
                            else:
                                yield make_event("progress", data=f"✅ {tool_display_name}执行完成")
                        else:
                            error = result.get("error", "未知错误")
                            yield make_event("progress", data=f"❌ {tool_display_name}失败: {error}")
                    else:
                        yield make_event("tool_result", toolName=tool_name, result=result, success=True)
                        yield make_event("progress", data=f"✅ {tool_display_name}执行完成")

                    # 对于 content_generate 工具，将结果格式化为可展示的内容并立即输出
                    if tool_name == "content_generate":
                        if isinstance(result, dict):
                            success = result.get("success")
                            content = result.get("content", "")
                            logger.info(f"[CONTENT_GEN] success={success}, content_len={len(content) if content else 0}")
                            if success and content:
                                yield make_event("response", data=f"\n<!--process-->\n📝 **内容生成结果：**\n\n{content}\n\n<!--/process-->\n")
                        else:
                            logger.warning(f"[CONTENT_GEN] Unexpected result type: {type(result)}")

                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": result
                    })
                    # 后端日志：追踪工具生成的文件，确保下载链接出现在最终回复中
                    if isinstance(result, dict) and result.get("download_url") and result.get("success"):
                        file_name = (
                            result.get("file_name")
                            or result.get("display_name")
                            or result.get("download_file_name")
                            or result.get("name")
                            or "生成的文件"
                        )
                        generated_files.append({
                            "file_name": file_name,
                            "download_url": result["download_url"],
                        })
                        logger.info(
                            f"后端日志：工具 {tool_name} 生成了文件: "
                            f"file_name={file_name}, download_url={result['download_url']}"
                        )
                    result_preview = str(result)[:200] if result else "None"
                    logger.debug(f"Tool result: {result_preview}...")

                    # 标记任务完成（使用保存的task_id）
                    if current_task_id:
                        if result.get("success", True):
                            self.plan_manager.mark_task_completed(
                                session_id, current_task_id, result
                            )
                        else:
                            self.plan_manager.mark_task_failed(
                                session_id, current_task_id,
                                result.get("error", "Tool execution failed")
                            )

                except Exception as e:
                    import traceback
                    error_trace = traceback.format_exc()
                    error_msg = f"Tool execution failed: {str(e)}"
                    logger.error(f"[AGENT] Tool execution error, session_id={session_id}, tool={tool_name}, error: {e}")
                    logger.error(f"[AGENT] Tool execution traceback:\n{error_trace}")
                    # 发送工具执行结果（失败）
                    yield make_event("tool_result", toolName=tool_name, result={"error": error_msg}, success=False)
                    yield make_event("progress", data=f"❌ {self._get_tool_display_name(tool_name, tool_args)}执行出错: {str(e)}")
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": error_msg,
                        "is_error": True
                    })
                    
                    # 标记任务失败
                    if current_task_id:
                        self.plan_manager.mark_task_failed(
                            session_id, current_task_id, error_msg
                        )
            
            # Add tool results to messages and memory
            # Each tool result should be a separate message with role "tool"
            for tool_result in tool_results:
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_result["tool_call_id"],
                    "content": tool_result["content"]
                }
                messages.append(tool_message)
                # Save tool result to memory
                self.memory.add_message(session_id, tool_message)
            
            # 延迟执行 Skill 上下文压缩（在 tool_message 写入 memory 之后）
            for comp_session_id, comp_skill_name, comp_summary in pending_skill_compressions:
                self._compress_skill_context(comp_session_id, comp_skill_name, comp_summary)
        
        if iteration >= max_iterations:
            logger.warning(f"Reached max iterations ({max_iterations})")
            yield make_event("response", data="I apologize, but the task is taking too long. Please try again or break it into smaller steps.")

        # 收集本轮 tool 消息序列，供 main.py 持久化
        tool_messages_for_persist = []
        for m in messages[initial_len:]:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                entry = {
                    "role": "assistant",
                    "content": m.get("content", ""),
                    "tool_calls": m["tool_calls"],
                }
                if m.get("reasoning_content"):
                    entry["reasoning_content"] = m["reasoning_content"]
                tool_messages_for_persist.append(entry)
            elif m.get("role") == "tool":
                tool_messages_for_persist.append({
                    "role": "tool",
                    "tool_call_id": m["tool_call_id"],
                    "content": m["content"],
                })

        if tool_messages_for_persist:
            yield make_event("tool_messages", messages=tool_messages_for_persist)

        # 清除子智能体临时注入的环境变量
        for var_name in _injected_env_vars:
            os.environ.pop(var_name, None)
    
    async def process_message_sync(
        self,
        user_input: str,
        session_id: str,
        user: Optional[User] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        record_service=None,
        progress_callback=None
    ) -> str:
        """Process message and return complete response

        Args:
            record_service: Optional SessionRecordService for token tracking.
                When provided (e.g. from channel routes running in asyncio),
                the agent accumulates token usage to this service instead of
                relying on thread-local SessionRecordManager.
            progress_callback: Optional async callback for progress events
                (tool_start, tool_result, etc.)
        """
        # Store explicit record_service so the inner process_message()
        # can access it without relying on thread-local storage
        self._explicit_record_service = record_service
        logger.info(f"[DEBUG] Agent.process_message_sync: user_input={user_input!r}, attachments={attachments}, session_id={session_id}")
        try:
            response_parts = []
            async for event in self.process_message(
                user_input, session_id, user, attachments
            ):
                if event.get("type") == "response":
                    response_parts.append(event.get("data", ""))
                # Forward events to external progress_callback (e.g. channel routes)
                if progress_callback:
                    if callable(progress_callback):
                        await progress_callback(event)
            return "".join(response_parts)
        finally:
            self._explicit_record_service = None
    
    def _update_task_record(self, record) -> None:
        """
        子智能体内部方法：更新任务记录到内部存储。
        
        由于子智能体不直接持有 executor 引用，task_record 在
        execute_as_subagent 中通过参数传入，此方法预留用于未来
        扩展（如需要通过事件回调同步状态时使用）。
        """
        logger.debug(f"[SUBAGENT] Task record updated: {record.execution_id}, status={record.status}")
    
    async def execute_as_subagent(
        self,
        task_description: str,
        parent_session_id: str,
        task_record=None,
        progress_callback: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None,
    ) -> Dict[str, Any]:
        """
        作为子智能体执行任务

        Args:
            task_description: 任务描述
            parent_session_id: 父智能体的session ID
            task_record: 任务记录（用于状态更新）
            progress_callback: 进度回调函数（保留兼容，内部收集事件并转发）

        Returns:
            执行结果
        """
        if self.mode != AgentMode.SUBAGENT:
            raise RuntimeError("execute_as_subagent() is only for subagent mode")

        # 按需加载租户自定义 skills
        self._ensure_tenant_skills_loaded()

        # 注入 tenant_id 到需要租户隔离的工具（子智能体线程中 ContextVar 不可用）
        if self._init_tenant_id:
            for tool_name in ("attraction_search", "knowledge_base_search"):
                tool = self.tool_registry.get_tool(tool_name)
                if tool and hasattr(tool, 'set_tenant_id'):
                    tool.set_tenant_id(self._init_tenant_id)

        # 注入子智能体环境变量（从 subagent_env_vars 表读取，设置为 os.environ）
        _injected_env_vars = {}
        if self._init_tenant_id and self.subagent_config:
            try:
                from src.db.subagent_env_var import SubagentEnvVarDB
                subagent_name = self.subagent_config.dir_name
                env_vars = SubagentEnvVarDB.get_vars(self._init_tenant_id, subagent_name)
                for var in env_vars:
                    var_name = var["var_name"]
                    var_value = var.get("var_value", "")
                    if var_name and var_value:
                        os.environ[var_name] = var_value
                        _injected_env_vars[var_name] = True
                if _injected_env_vars:
                    logger.info(f"[SUBAGENT] Injected {len(_injected_env_vars)} env vars for {subagent_name}")
            except Exception as e:
                logger.warning(f"[SUBAGENT] 环境变量注入失败: {e}")

        # 事件辅助函数 — 内部收集并转发给 progress_callback
        from src.core.agent_events import make_event
        collected_events = []

        def _emit(event: dict):
            collected_events.append(event)
            if progress_callback:
                # progress_callback 是 async，但这里同步收集
                # 实际调用方会在 await 后统一处理
                pass

        async def _emit_async(event: dict):
            collected_events.append(event)
            if progress_callback:
                await progress_callback(event)

        logger.info(f"\n{'='*60}\n[SUBAGENT] execute_as_subagent started\n{'='*60}")
        logger.info(f"[SUBAGENT] config.name: {self.subagent_config.name}")
        logger.info(f"[SUBAGENT] session_id: {self.session_id}")
        logger.info(f"[SUBAGENT] execution_id: {self.execution_id}")
        logger.info(f"[SUBAGENT] task_description: {task_description}")
        
        try:
            # 步骤1：构建消息（子智能体不使用历史消息，只使用任务描述）
            messages = []
            
            # 添加当前时间上下文
            from datetime import datetime
            current_time = datetime.now()
            timestamp_context = (
                f"[当前时间: {current_time.strftime('%Y年%m月%d日 %H:%M:%S')}, "
                f"{current_time.strftime('%A')}, "
                f"今年是{current_time.year}年]\n\n"
            )
            
            # 添加任务描述
            messages.append({
                "role": "user",
                "content": timestamp_context + task_description
            })
            
            system_prompt = self._build_system_prompt()
            tools = self._get_tools()
            
            # 步骤2：让LLM理解任务并创建计划（如果需要）
            # 子智能体在第一次迭代时可能会调用 create_plan
            max_iterations = 20
            iteration = 0
            final_result = None
            final_summary = ""
            subagent_plan_created = False
            generated_content_list = []  # 存储所有生成的内容
            subagent_token_usage = {"input": 0, "output": 0, "cached": 0}

            while iteration < max_iterations:
                iteration += 1
                logger.info(f"[SUBAGENT] Iteration {iteration}")

                # 发送迭代进度
                # await send_progress(f"🔄 [{self.subagent_config.name}] 第{iteration}轮思考中...")

                # 更新进度
                if task_record:
                    progress = min(90.0, iteration * 5.0)
                    task_record.update_progress(progress, f"Processing iteration {iteration}")
                
                # 打印LLM调用信息（与主智能体一致）
                if settings.app.llm_debug:
                    logger.debug(f"\n{'='*60}\n"
                                f"[LLM_DEBUG] Subagent Iteration {iteration} - Full Prompt\n"
                                f"{'='*60}\n"
                                f"[System Prompt]:\n{system_prompt}\n"
                                f"{'-'*60}\n"
                                f"[Messages]:\n{json.dumps(messages, ensure_ascii=False, indent=2)}\n"
                                f"{'-'*60}\n"
                                f"[Tools]: {json.dumps([t.get('name', t.get('function', {}).get('name', 'unknown')) for t in tools], ensure_ascii=False)}\n"
                                f"{'='*60}")
                
                # 调用LLM
                import time
                llm_call_start = time.time()
                logger.info(f"[SUBAGENT] LLM call starting, execution_id={self.execution_id}, iteration={iteration}")
                
                try:
                    response = await self.llm.chat_with_tools(
                        system_prompt=system_prompt,
                        messages=messages,
                        tools=tools
                    )
                    
                    llm_call_duration = time.time() - llm_call_start
                    logger.info(f"[SUBAGENT] LLM call completed, execution_id={self.execution_id}, iteration={iteration}, duration={llm_call_duration:.2f}s")

                    # 追踪：emit LLM 调用事件
                    await _emit_async(make_event("llm_call",
                        request_id=response.get("request_id", ""),
                        model=self.llm.get_model_name(),
                        provider=self.llm.get_provider_name(),
                        usage=response.get("usage", {}),
                        duration_ms=int(llm_call_duration * 1000),
                        messages=messages,
                        tools=tools,
                        system_prompt=system_prompt,
                        response_content=response.get("content", ""),
                    ))

                except asyncio.TimeoutError as e:
                    llm_call_duration = time.time() - llm_call_start
                    logger.error(f"[SUBAGENT] LLM call TIMEOUT, execution_id={self.execution_id}, iteration={iteration}, duration={llm_call_duration:.2f}s")
                    raise
                except Exception as e:
                    llm_call_duration = time.time() - llm_call_start
                    logger.error(f"[SUBAGENT] LLM call FAILED, execution_id={self.execution_id}, iteration={iteration}, duration={llm_call_duration:.2f}s, error: {e}", exc_info=True)
                    raise
                
                content = response.get("content", "")
                tool_calls = response.get("tool_calls", [])
                
                # 后端日志：记录子智能体迭代信息（关联LLM request_id）
                log_agent_iteration(
                    iteration=iteration,
                    request_id=response.get("request_id", ""),
                    user_id="",
                    session_id=self.session_id or "",
                    tenant_id=getattr(self, '_init_tenant_id', '') or '',
                    model=self.llm.get_model_name(),
                    provider=self.llm.get_provider_name(),
                    has_tool_calls=bool(tool_calls),
                    tool_calls_count=len(tool_calls),
                    tool_names=[tc.get("function", {}).get("name", tc.get("name", "")) for tc in tool_calls] if tool_calls else [],
                    content_length=len(content) if content else 0,
                    usage=response.get("usage"),
                )

                # 累加子智能体 token 用量到 SessionRecordService（纯内存操作，异常隔离）
                try:
                    from src.services.session_record import SessionRecordManager
                    _record = getattr(self, '_explicit_record_service', None) or SessionRecordManager.get_current_record()
                    if _record:
                        _record.add_llm_usage(response.get("usage", {}))
                        _record.increment_iterations()
                        if not _record.provider:
                            _record.set_model(self.llm.get_model_name())
                            _record.set_provider(self.llm.get_provider_name())
                    # 同时累加到本地计数器（用于返回值）
                    _usage = response.get("usage", {})
                    subagent_token_usage["input"] += _usage.get("prompt_tokens", 0)
                    subagent_token_usage["output"] += _usage.get("completion_tokens", 0)
                    subagent_token_usage["cached"] += _usage.get("cached_tokens", 0)
                except Exception:
                    logger.debug(f"Failed to record subagent token usage", exc_info=True)
                
                # 打印LLM响应信息
                if settings.app.llm_debug:
                    logger.debug(f"\n{'='*60}\n"
                                f"[LLM_DEBUG] Subagent LLM Response - Iteration {iteration}\n"
                                f"{'='*60}\n"
                                f"[Content]:\n{content if content else '(None)'}\n"
                                f"{'-'*60}\n"
                                f"[Tool Calls]: {len(tool_calls)} call(s)\n"
                                f"{json.dumps(tool_calls, ensure_ascii=False, indent=2) if tool_calls else '(None)'}\n"
                                f"{'='*60}")
                
                # 如果没有工具调用，任务完成
                if not tool_calls:
                    # 兜底：自动完成所有活跃的 Skill Session
                    if self.has_active_skill_session:
                        for active_skill_name, session in list(self._active_skill_sessions.items()):
                            auto_summary = f"使用技能「{active_skill_name}」执行了相关任务"
                            self._compress_skill_context(self.session_id, active_skill_name, auto_summary)
                    
                    final_result = {"content": content}
                    final_summary = content[:500] if content else "Task completed"
                    break
                
                # 添加助手消息
                assistant_msg = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls
                }
                if response.get("reasoning_content"):
                    assistant_msg["reasoning_content"] = response["reasoning_content"]
                messages.append(assistant_msg)
                
                # 执行工具调用
                pending_skill_compressions = []  # 收集需要延迟执行的 Skill 压缩
                for tc in tool_calls:
                    if "function" in tc:
                        tool_name = tc["function"].get("name", "")
                        args_raw = tc["function"].get("arguments", "{}")
                        if isinstance(args_raw, str):
                            try:
                                tool_args = json.loads(args_raw) if args_raw else {}
                            except json.JSONDecodeError:
                                logger.warning(f"[SUBAGENT] Failed to parse tool arguments: {args_raw[:200]}...")
                                tool_args = {}
                        else:
                            tool_args = args_raw
                    else:
                        tool_name = tc.get("name", "")
                        tool_args = tc.get("arguments", {})
                    
                    if not tool_name:
                        continue

                    logger.info(f"[SUBAGENT] Executing tool: {tool_name}")

                    # Fallback: 如果 LLM 调用了一个不在工具列表中但匹配 skill 名称的工具，
                    # 自动转为 use_skill 调用（LLM 有时会误把 skill 名称当成工具名直接调用）
                    known_tool_names_sub = {t["name"] for t in self._get_tools()}
                    if tool_name not in known_tool_names_sub and self.skill_registry:
                        matched_skill = self.skill_registry.get(tool_name)
                        if matched_skill:
                            original_name_sub = tool_name
                            logger.info(f"[SUBAGENT] Auto-mapping unknown tool '{original_name_sub}' to use_skill(skill='{original_name_sub}')")
                            tool_name = "use_skill"
                            tool_args = {"skill": original_name_sub}

                    # 发送工具执行进度
                    tool_display_name = self._get_tool_display_name(tool_name, tool_args)
                    # 发送工具开始执行事件
                    await _emit_async(make_event("tool_start", toolName=tool_name, toolArgs=tool_args))
                    await _emit_async(make_event("progress", data=f"🔧 [{self.subagent_config.name}] 正在执行 {tool_display_name}..."))

                    # 处理 clarify - 子智能体需要向用户询问补充信息
                    # 与主智能体不同，子智能体的 clarify 不会直接对话用户，
                    # 而是设置 CLARIFYING 状态并返回，由主智能体中转给用户
                    if tool_name == "clarify":
                        question = tool_args.get("question", "")
                        missing_info = tool_args.get("missing_info", [])
                        logger.info(f"[SUBAGENT] Clarify requested: question={question[:100]}...")

                        if task_record:
                            task_record.request_clarification(question)
                            self._clarification_missing_info = missing_info
                            self._update_task_record(task_record)

                        await _emit_async(make_event("progress", data=f"❓ [{self.subagent_config.name}] 需要补充信息: {question[:50]}..."))
                        await _emit_async(make_event("tool_result", toolName=tool_name, result={"success": True, "question": question, "missing_info": missing_info}, success=True))

                        # 添加工具结果到消息
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": json.dumps({"success": True, "question": question, "status": "clarifying"}, ensure_ascii=False)
                        })

                        # 返回特殊结果，告知主智能体需要澄清
                        return {
                            "result": {
                                "content": question,
                                "status": "clarifying",
                                "question": question,
                                "missing_info": missing_info,
                            },
                            "summary": f"需要补充信息: {question}",
                            "status": "clarifying",
                            "token_usage": {"input": 0, "output": 0}
                        }

                    # 处理 create_plan（子智能体创建自己的计划）
                    if tool_name == "create_plan":
                        plan_result = await self._create_plan_tool.execute(
                            **tool_args,
                            session_id=self.session_id,
                            user_query=task_description,
                        )
                        tool_result = plan_result
                        subagent_plan_created = True
                        # 发送工具执行结果
                        await _emit_async(make_event("tool_result", toolName=tool_name, result=plan_result, success=plan_result.get("success", True)))

                        # 同步到父智能体的计划管理器
                        if self.parent_plan_manager:
                            # 这里可以添加逻辑，将子智能体的计划同步到父智能体的计划记录中
                            logger.info(f"[SUBAGENT] Syncing plan to parent plan manager")
                    
                    # 处理技能工具
                    elif tool_name == "use_skill":
                        skill_name = tool_args.get("skill", "")
                        skill_result = await self._use_skill_tool.execute(**tool_args)
                        tool_result = skill_result
                        # 创建 Skill Session
                        if skill_result.get("success") and skill_name not in self._active_skill_sessions:
                            msg_count = self.memory.get_message_count(self.session_id)
                            self._active_skill_sessions[skill_name] = SkillSession(
                                skill_name=skill_name,
                                start_index=msg_count,
                                message_count_before=msg_count,
                            )
                        # 发送工具执行结果
                        await _emit_async(make_event("tool_result", toolName=tool_name, result=skill_result, success=skill_result.get("success", True)))
                    elif tool_name == "skill_complete":
                        skill_name = tool_args.get("skill", "")
                        summary = tool_args.get("summary", "")
                        if skill_name in self._active_skill_sessions:
                            # 延迟压缩：等 tool_message 写入 messages 后再压缩
                            pending_skill_compressions.append((self.session_id, skill_name, summary))
                            tool_result = {"success": True, "message": f"技能 {skill_name} 已完成并清理上下文"}
                            await _emit_async(make_event("progress", data=f"✅ [{self.subagent_config.name}] 技能「{skill_name}」执行完成"))
                        else:
                            tool_result = {"success": False, "error": f"没有找到活跃的技能会话: {skill_name}"}
                        await _emit_async(make_event("tool_result", toolName=tool_name, result=tool_result, success=tool_result.get("success", True)))
                    elif tool_name == "skill_execute":
                        skill_name = tool_args.get("skill", "")
                        command = tool_args.get("command", "") or None  # 空字符串转为 None
                        files = tool_args.get("files", {})
                        content = tool_args.get("content")
                        skill_exec_result = await self._skill_execute_tool.execute(
                            skill=skill_name,
                            command=command,
                            files=files,
                            content=content,
                            session_id=self.session_id,
                        )
                        tool_result = skill_exec_result
                        # 后端日志：记录 skill_execute 执行结果
                        log_skill_execute(
                            skill_name=skill_name,
                            command=command or "",
                            session_id=self.session_id,
                            success=skill_exec_result.get("success", False),
                            exit_code=skill_exec_result.get("exit_code", 0),
                            stdout=skill_exec_result.get("stdout", ""),
                            stderr=skill_exec_result.get("stderr", ""),
                            error=skill_exec_result.get("error", ""),
                            duration=skill_exec_result.get("duration", 0),
                            input_content=str(content) if content else "",
                        )
                        # 发送工具执行结果
                        await _emit_async(make_event("tool_result", toolName=tool_name, result=skill_exec_result, success=skill_exec_result.get("success", True)))
                        if not skill_exec_result.get("success"):
                            error = skill_exec_result.get("error") or "未知错误"
                            stderr = skill_exec_result.get("stderr", "")
                            exit_code = skill_exec_result.get("exit_code", -1)
                            detail = stderr[-300:] if stderr else (f"exit_code={exit_code}" if exit_code else error)
                            await _emit_async(make_event("progress", data=f"❌ [{self.subagent_config.name}] 技能「{skill_name}」执行失败: {detail}"))

                        # 同步到父智能体的计划管理器
                        if self.parent_plan_manager and subagent_plan_created:
                            # 获取当前计划中的任务
                            plan = self.plan_manager.get_plan(self.session_id)
                            if plan:
                                task = self.plan_manager.get_next_pending_task(self.session_id)
                                if task and task.tool_name == "skill_execute":
                                    self.plan_manager.mark_task_running(self.session_id, task.task_id)
                                    if skill_exec_result.get("success"):
                                        self.plan_manager.mark_task_completed(
                                            self.session_id, task.task_id, skill_exec_result
                                        )
                                    else:
                                        self.plan_manager.mark_task_failed(
                                            self.session_id, task.task_id,
                                            skill_exec_result.get("error", "Unknown error")
                                        )
                    else:
                        # 执行普通工具
                        try:
                            result = await self.tool_executor.execute(tool_name, tool_args)
                            tool_result = result

                            # 发送工具执行完成进度
                            if isinstance(result, dict):
                                success = result.get("success", True)
                                # 发送工具执行结果
                                await _emit_async(make_event("tool_result", toolName=tool_name, result=result, success=success))
                                if success:
                                    await _emit_async(make_event("progress", data=f"✅ [{self.subagent_config.name}] {tool_display_name}执行完成"))
                                else:
                                    error = result.get("error", "未知错误")
                                    await _emit_async(make_event("progress", data=f"❌ [{self.subagent_config.name}] {tool_display_name}失败: {error}"))
                            else:
                                await _emit_async(make_event("tool_result", toolName=tool_name, result=result, success=True))
                                await _emit_async(make_event("progress", data=f"✅ [{self.subagent_config.name}] {tool_display_name}执行完成"))

                            # 对于 content_generate 工具，保存生成的内容
                            if tool_name == "content_generate" and isinstance(result, dict):
                                generated_content = result.get("content", "")
                                if generated_content:
                                    generated_content_list.append(generated_content)
                                    logger.info(f"[SUBAGENT] content_generate: saved content length={len(generated_content)}")
                            
                            # 同步到父智能体的计划管理器
                            if self.parent_plan_manager and subagent_plan_created:
                                # 获取当前计划中的任务
                                plan = self.plan_manager.get_plan(self.session_id)
                                if plan:
                                    task = self.plan_manager.get_next_pending_task(self.session_id)
                                    if task:
                                        self.plan_manager.mark_task_running(self.session_id, task.task_id)
                                        if result.get("success", True):
                                            self.plan_manager.mark_task_completed(
                                                self.session_id, task.task_id, result
                                            )
                                        else:
                                            self.plan_manager.mark_task_failed(
                                                self.session_id, task.task_id,
                                                result.get("error", "Tool execution failed")
                                            )
                        except Exception as e:
                            tool_result = {"error": str(e)}
                            # 发送工具执行结果（失败）
                            await _emit_async(make_event("tool_result", toolName=tool_name, result={"error": str(e)}, success=False))

                            # 标记任务失败
                            if self.parent_plan_manager and subagent_plan_created:
                                plan = self.plan_manager.get_plan(self.session_id)
                                if plan:
                                    task = self.plan_manager.get_next_pending_task(self.session_id)
                                    if task:
                                        self.plan_manager.mark_task_failed(
                                            self.session_id, task.task_id, str(e)
                                        )
                    
                    # 添加工具结果
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps(tool_result, ensure_ascii=False) if isinstance(tool_result, dict) else str(tool_result)
                    })
                
                # 延迟执行 Skill 上下文压缩（在 tool_message 写入 messages 之后）
                for comp_session_id, comp_skill_name, comp_summary in pending_skill_compressions:
                    self._compress_skill_context(comp_session_id, comp_skill_name, comp_summary)

            # 发送子任务完成消息
            await _emit_async(make_event("progress", data=f"✅ [{self.subagent_config.name}] 任务完成，正在整合结果..."))

            logger.info(f"[SUBAGENT] Task completed with summary: {final_summary[:200]}")
            
            # 如果有生成的内容，合并到结果中
            if generated_content_list and final_result:
                combined_content = "\n\n".join(generated_content_list)
                if isinstance(final_result, dict):
                    final_result["generated_contents"] = generated_content_list
                    final_result["combined_content"] = combined_content
                else:
                    final_result = {
                        "content": str(final_result),
                        "generated_contents": generated_content_list,
                        "combined_content": combined_content
                    }
            
            return {
                "result": final_result,
                "summary": final_summary,
                "generated_contents": generated_content_list,
                "token_usage": subagent_token_usage,
                "events": collected_events,
            }
            
        except Exception as e:
            import traceback
            logger.error(f"[SUBAGENT] Execution failed: {e}")
            logger.error(f"[SUBAGENT] Traceback:\n{traceback.format_exc()}")
            return {
                "result": None,
                "summary": f"Failed: {e}",
                "error": str(e)
            }
        finally:
            # 清理注入的环境变量
            for var_name in _injected_env_vars:
                os.environ.pop(var_name, None)


# Global agent instance (默认为主智能体)
master_agent = Agent(is_master=True)
agent = master_agent  # 别名，向后兼容
