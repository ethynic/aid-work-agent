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
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any, AsyncGenerator, Callable, Coroutine, Any
from loguru import logger

from enum import Enum

from src.config.settings import settings
from src.core.agent_logger import log_agent_iteration, log_skill_execute
from src.llm.gateway import llm_gateway
from src.tools.registry import ToolRegistry
from src.tools.executor import ToolExecutor
from src.memory.short_term import ShortTermMemory
from src.memory.manager import MemoryManager
from src.prompts import PromptManager
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
        # 主智能体的 pending clarifications: {session_id: {subagent_name, execution_id, task_description, question}}
        self._pending_clarifications: Dict[str, Dict[str, Any]] = {}

        # 共享组件
        self.llm = llm_gateway
        self.tool_registry = ToolRegistry()
        self.tool_executor = ToolExecutor(self.tool_registry)
        self.prompt_manager = PromptManager()
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
            custom_dir = Path("./storage/subagents2")
            from src.subagents.registry import SubagentRegistry
            self.subagent_registry = SubagentRegistry(subagents_dir, custom_dir=custom_dir)

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
        from src.tools.file.file_reader_tool import FileReaderTool, FileListTool
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
        self.tool_registry.register(FileReaderTool())
        self.tool_registry.register(FileListTool())
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
        if self.mode == AgentMode.MASTER and self.subagent_registry and len(self.subagent_registry) > 0:
            delegation_tool = self.subagent_registry.get_delegation_tool_definition()
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
            # 获取租户可用的子智能体（SaaS模式时从subscriptions表加载，演示模式用全部）
            from src.saas.context import get_current_tenant_id
            tenant_id = get_current_tenant_id()

            # SaaS模式且有租户ID时，从subscriptions表加载可用的子智能体
            if settings.saas.enabled and not settings.demo.enabled and tenant_id:
                from src.db.database import get_db_connection
                from src.saas.db.subscription_db import SubscriptionDB

                with get_db_connection() as conn:
                    allowed_subagent_types = SubscriptionDB.get_allowed_subagent_types(conn, tenant_id)
                # 过滤注册的子智能体，只保留租户订阅的
                # 注意：subagent_type 存储的是 dir_name（如 trade-specialist），_configs的key是name（如外贸获客智能体）
                filtered_configs = []
                for name, config in self.subagent_registry._configs.items():
                    agent_id = config.dir_name or name
                    # 只包含租户订阅的，排除主智能体（CEO智能体，agent_id为"main"）
                    if agent_id in allowed_subagent_types and agent_id != "main":
                        filtered_configs.append((name, config))
                available_subagents = [name for name, _ in filtered_configs]
                # 生成描述
                lines = []
                for name, config in filtered_configs:
                    capabilities = ", ".join(config.capabilities) if config.capabilities else "general"
                    lines.append(f"- {name}: {config.description} (capabilities: {capabilities})")
                subagent_descriptions = "\n".join(lines) if lines else "(no subagents available)"
            else:
                # 演示模式或非SaaS模式，使用全部子智能体（排除CEO智能体）
                all_configs = []
                for name, config in self.subagent_registry._configs.items():
                    agent_id = config.dir_name or name
                    if agent_id != "main":
                        all_configs.append((name, config))
                available_subagents = [name for name, _ in all_configs]
                # 生成描述
                lines = []
                for name, config in all_configs:
                    capabilities = ", ".join(config.capabilities) if config.capabilities else "general"
                    lines.append(f"- {name}: {config.description} (capabilities: {capabilities})")
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

## 专业领域约束

{subagent_constraint}
"""

        user_info_section = ""
        if user:
            user_info_section = f"\n\n## 当前用户\n姓名: {user.name}\nID: {user.user_id}\n"

        # 长期记忆注入点（Phase 3 预留）
        long_term_memory = ""

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
            # SUBAGENT 和 STANDALONE：使用配置中的系统提示词
            subagent_constraint = ""
            if self.subagent_config and self.subagent_config.system_prompt:
                subagent_constraint = self.subagent_config.system_prompt

            # 加载租户定制 extra.md
            extra_content = self._load_extra_md()
            if extra_content:
                subagent_constraint = subagent_constraint + "\n\n" + extra_content

            return self._build_base_system_prompt(
                include_delegation=False,
                subagent_constraint=subagent_constraint,
                user=user
            )

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
        logger.debug(f"[DEBUG] _build_messages: session_id={session_id}, history_count={len(history)}")

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
                    messages.append({
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls
                    })
                    assistant_tc_indices.append(len(messages) - 1)
                    pending_tool_calls = tc_ids
                else:
                    # 普通 assistant message，跳过空 content
                    if content:
                        messages.append({
                            "role": "assistant",
                            "content": content
                        })
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
        progress_callback: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None,
    ) -> Dict[str, Any]:
        """
        Handle delegate_to_subagent tool call - delegate task to a subagent

        Args:
            subagent_name: Name of the subagent to delegate to
            task_description: Description of the task
            context_needed: Keywords for context filtering (optional)
            session_id: Session ID for memory access
            progress_callback: 进度回调函数，用于实时传递子智能体执行进度

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
        
        # Check if subagent exists
        config = self.subagent_registry.get(subagent_name)
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

            # 创建子智能体专用的回调包装器
            # 子智能体的 send_progress/send_tool_start/send_tool_result 已经将消息包装为 dict，
            # 而主智能体的 progress_callback (send_progress) 会再包装一层 {"type": "progress", "data": ...}
            # 这里需要提取子智能体事件中的实际内容，作为字符串传给上层，避免重复包装
            async def subagent_progress_wrapper(event):
                """将子智能体的事件转发给上层 progress_callback，避免嵌套包装

                Args:
                    event: 子智能体传递的事件，可能是字符串或字典
                """
                if not progress_callback:
                    return
                if isinstance(event, str):
                    # 字符串直接传给上层，由 send_progress 包装一次
                    await progress_callback(event)
                elif isinstance(event, dict):
                    event_type = event.get("type", "")
                    event_data = event.get("data", "")
                    if event_type == "progress":
                        # progress 事件：提取 data 字符串，由上层 send_progress 包装一次
                        await progress_callback(event_data if isinstance(event_data, str) else str(event_data))
                    elif event_type in ("tool_start", "tool_result"):
                        # tool_start/tool_result 事件已经是完整格式，直接传给上层
                        # 上层 main.py 的 sync_progress_callback 会原样保存到 progress 列表
                        # 但由于 progress_callback 是 send_progress（只接受字符串），这里需要特殊处理
                        # 暂时将 tool 事件转为 progress 字符串传递，避免嵌套
                        tool_name = event.get("toolName", event.get("tool_name", ""))
                        if event_type == "tool_start":
                            await progress_callback(f"🔧 正在执行 {tool_name}...")
                        elif event_type == "tool_result":
                            success = event.get("success", True)
                            if success:
                                await progress_callback(f"✅ {tool_name} 执行完成")
                            else:
                                error = event.get("result", {}).get("error", "未知错误") if isinstance(event.get("result"), dict) else str(event.get("result", ""))
                                await progress_callback(f"❌ {tool_name} 执行失败: {error}")
                    elif event_type == "thinking":
                        await progress_callback(event_data if isinstance(event_data, str) else str(event_data))
                    else:
                        await progress_callback(str(event))
                else:
                    await progress_callback(str(event))

            # Delegate to subagent
            response = await self.subagent_executor.delegate(
                task_id=task_id,
                subagent_name=subagent_name,
                task_description=task_description,
                session_id=session_id or "default",
                progress_callback=subagent_progress_wrapper,
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
                    
                    # 保存 pending clarification 上下文，供用户回复后使用
                    if not hasattr(self, '_pending_clarifications'):
                        self._pending_clarifications = {}  # session_id -> clarification context
                    self._pending_clarifications[session_id or "default"] = {
                        "subagent_name": subagent_name,
                        "execution_id": response.execution_id,
                        "task_description": task_description,
                        "question": question,
                        "missing_info": record.clarification_answer,  # 暂时存空，后续用 answer_clarification 更新
                    }
                    
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
        progress_callback: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Process a user message and yield response chunks
        
        This is the main agent loop:
        1. Build context from memory
        2. Check for file attachments and auto-load relevant skills
        3. Save uploaded files to skill workspace
        4. Call LLM with tools
        5. Execute tool calls if any
        6. Return results to LLM
        7. Yield final response
        
        Args:
            user_input: User's text input
            session_id: Session identifier
            user: Optional user information
            attachments: Optional list of file attachments, each with:
                - type: attachment type (e.g., "file", "image")
                - name: filename
                - url: file URL or path (optional)
                - mime_type: MIME type (optional)
                - content: base64 encoded file content (optional)
        """
        import base64
        import tempfile
        from datetime import datetime

        # 进度消息辅助函数
        async def send_progress(message: str):
            if progress_callback:
                await progress_callback({"type": "progress", "data": message})

        # 工具开始执行回调
        async def send_tool_start(tool_name: str, tool_args: dict):
            if progress_callback:
                await progress_callback({
                    "type": "tool_start",
                    "toolName": tool_name,
                    "toolArgs": tool_args
                })

        # 工具执行结果回调
        async def send_tool_result(tool_name: str, result: any, success: bool):
            if progress_callback:
                await progress_callback({
                    "type": "tool_result",
                    "toolName": tool_name,
                    "result": result,
                    "success": success
                })

        # LLM思考中回调
        async def send_thinking(message: str):
            if progress_callback:
                await progress_callback({"type": "thinking", "data": message})

        # 澄清事件回调
        async def send_clarification(subagent_name: str, question: str):
            if progress_callback:
                await progress_callback({
                    "type": "clarification",
                    "subagent_name": subagent_name,
                    "question": question,
                })

        # 后端日志：检查是否有待处理的澄清请求
        pending_clarification = self._pending_clarifications.get(session_id)
        if pending_clarification and self.is_master:
            # 用户正在回复子智能体的澄清请求
            clarification = pending_clarification
            subagent_name = clarification["subagent_name"]
            original_task = clarification["task_description"]
            original_question = clarification["question"]
            
            logger.info(f"[AGENT] User replying to clarification from '{subagent_name}': {user_input[:100]}...")
            
            # 清除 pending 状态
            self._pending_clarifications.pop(session_id, None)
            
            # 构建增强的任务描述：原始任务 + 澄清问题和用户回答
            enhanced_task = (
                f"{original_task}\n\n"
                f"[补充信息]\n"
                f"在执行过程中需要确认以下问题：{original_question}\n"
                f"用户补充回答：{user_input}"
            )
            
            await send_progress(f"🔄 正在将补充信息提交给 {subagent_name}，继续执行任务...")
            
            # 重新委派给子智能体（携带补充信息）
            redelegate_result = await self._delegate_tool.execute(
                subagent_name=subagent_name,
                task_description=enhanced_task,
                context_needed=None,
                session_id=session_id,
                progress_callback=progress_callback,
                user_id=user.user_id if user else None,
            )
            
            # 发送重新委派的结果
            await send_tool_result(
                "delegate_to_subagent",
                redelegate_result,
                redelegate_result.get("success", False),
            )
            
            if redelegate_result.get("success"):
                summary = redelegate_result.get("summary", "")
                preview = summary[:100] if summary else ""
                await send_progress(f"✅ {subagent_name}任务完成（补充信息后）: {preview}...")
                
                # 输出子智能体的结果
                final_result = redelegate_result.get("result")
                if final_result:
                    if isinstance(final_result, str) and final_result.strip():
                        yield final_result.strip()
                    elif isinstance(final_result, dict):
                        content = final_result.get("content", "")
                        if content and isinstance(content, str):
                            yield content.strip()
            elif redelegate_result.get("status") == "clarifying":
                # 如果 re-delegate 后又需要澄清，再次保存 pending 状态
                new_question = redelegate_result.get("question", "需要补充信息")
                logger.info(f"[AGENT] Subagent '{subagent_name}' requesting clarification again: {new_question[:100]}...")
                self._pending_clarifications[session_id] = {
                    "subagent_name": subagent_name,
                    "execution_id": redelegate_result.get("execution_id", ""),
                    "task_description": enhanced_task,
                    "question": new_question,
                }
                await send_clarification(subagent_name, new_question)
                yield f"\n❓ **{subagent_name}** 需要进一步补充信息：{new_question}\n请提供以上信息以继续执行任务。"
            else:
                error = redelegate_result.get("error", "重新执行失败")
                await send_progress(f"❌ {subagent_name}重新执行失败: {error}")
                yield f"\n❌ 重新执行任务失败：{error}"
            
            # 将补充信息保存到记忆中
            self.memory.add(session_id, "user", f"[补充信息回复] {user_input}")
            return

        logger.info(f"Processing message for session {session_id}: {user_input[:50]}...")

        # 按需加载租户自定义 skills
        self._ensure_tenant_skills_loaded()

        # ========== 临时调试日志 ==========
        import time
        _debug_start_time = time.time()
        logger.info(f"[DEBUG] process_message called, session_id={session_id}, input_length={len(user_input)}")
        # ========== 临时调试日志 ==========

        # 恢复历史会话上下文：如果该 session 的 memory 为空，从 DB 加载历史消息
        if self.memory.get_message_count(session_id) == 0:
            try:
                from src.db.models import MessageDB
                logger.info(f"[DEBUG] Memory empty, loading history from DB, session_id={session_id}")
                db_messages = MessageDB.list_by_session(
                    session_id,
                    limit=self.memory.short_term.max_messages,
                    roles=["user", "assistant"],
                )
                if db_messages:
                    history_messages = [
                        {
                            "role": msg["role"],
                            "content": msg["content"] or "",
                            "timestamp": msg.get("created_at", ""),
                        }
                        for msg in db_messages
                    ]
                    self.memory.load_history(session_id, history_messages)
                    logger.info(f"[DEBUG] Loaded {len(history_messages)} history messages for session {session_id}, time_since_start={time.time() - _debug_start_time:.3f}s")
            except Exception as e:
                logger.warning(f"Failed to load history for session {session_id}: {e}")

        # 设置邮件工具的 user_id，使工具能从数据库读取用户邮箱配置
        if user:
            for tool_name in ("email_send", "email_read", "email_list_folders"):
                tool = self.tool_registry.get_tool(tool_name)
                if tool and hasattr(tool, 'set_user_id'):
                    tool.set_user_id(user.user_id)

        # 注入 tenant_id 到需要租户隔离的工具（子智能体线程中 ContextVar 不可用）
        _resolve_tenant_id = self._init_tenant_id
        if not _resolve_tenant_id:
            try:
                from src.saas.context import get_current_tenant_id
                _resolve_tenant_id = get_current_tenant_id()
            except Exception:
                pass
        if _resolve_tenant_id:
            for tool_name in ("attraction_search",):
                tool = self.tool_registry.get_tool(tool_name)
                if tool and hasattr(tool, 'set_tenant_id'):
                    tool.set_tenant_id(_resolve_tenant_id)
        
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
        logger.info(f"[DEBUG] Added user message to memory, session_id={session_id}, time_since_start={time.time() - _debug_start_time:.3f}s")

        messages = self._build_messages(session_id)
        logger.info(f"[DEBUG] Built messages, msg_count={len(messages)}, session_id={session_id}, time_since_start={time.time() - _debug_start_time:.3f}s")
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
        
        max_iterations = 20  # Prevent infinite loops
        iteration = 0
        logger.info(f"[DEBUG] Entering agent loop, session_id={session_id}, time_since_start={time.time() - _debug_start_time:.3f}s")

        while iteration < max_iterations:
            logger.info(f"[DEBUG] Agent loop iteration {iteration}, session_id={session_id}, time_since_start={time.time() - _debug_start_time:.3f}s")
            iteration += 1
            logger.debug(f"Agent iteration {iteration}")
            
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
                
            except asyncio.TimeoutError as e:
                llm_call_duration = time.time() - llm_call_start
                logger.error(f"[AGENT] LLM call TIMEOUT, session_id={session_id}, iteration={iteration}, duration={llm_call_duration:.2f}s")
                raise
            except Exception as e:
                llm_call_duration = time.time() - llm_call_start
                logger.error(f"[AGENT] LLM call FAILED, session_id={session_id}, iteration={iteration}, duration={llm_call_duration:.2f}s, error: {e}", exc_info=True)
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
                _record = SessionRecordManager.get_current_record()
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
                self.memory.add(session_id, "assistant", content)

                # 发送最终回复进度
                await send_progress("✅ 任务完成，正在生成回复...")

                # Yield the final response
                if content:
                    yield content
                break
            
            # Add assistant message with tool calls to history
            assistant_message = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls
            }
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

                # 获取工具的用户友好名称
                tool_display_name = self._get_tool_display_name(tool_name, tool_args)
                # 发送工具开始执行事件
                await send_tool_start(tool_name, tool_args)
                await send_progress(f"🔧 正在执行 {tool_display_name}...")

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
                                await send_tool_result(tool_name, {"success": False, "error": f"Tool '{tool_name}' not allowed"}, False)
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
                    self._create_scheduled_task_tool.set_context(user, session_id, send_progress)
                    task_result = await self._create_scheduled_task_tool.execute(**tool_args)
                    success = task_result.get("success", False)
                    await send_tool_result(tool_name, task_result, success)
                    if success:
                        name = task_result.get("name", "")
                        schedule_desc = task_result.get("schedule_description", "")
                        await send_progress(f"✅ 定时任务已创建: {name} ({schedule_desc})")
                    else:
                        await send_progress(f"❌ 定时任务创建失败")
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
                    await send_tool_result(tool_name, task_result, success)
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
                    await send_tool_result(tool_name, plan_result, plan_result.get("success", True))
                    await send_progress(f"📋 执行计划已创建")
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": plan_result
                    })
                    continue

                # Handle clarify - ask user for clarification (no external tool needed)
                if tool_name == "clarify":
                    clarify_result = await self._clarify_tool.execute(**tool_args)
                    # 发送工具执行结果
                    await send_tool_result(tool_name, clarify_result, True)
                    await send_progress(f"❓ 需要澄清: {clarify_result.get('question', '')[:50]}...")
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
                    await send_tool_result(tool_name, skill_result, skill_result.get("success", True))
                    await send_progress(f"📦 已加载技能: {skill_name}")
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
                        await send_progress(f"✅ 技能「{skill_name}」执行完成")
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
                    await send_tool_result(tool_name, skill_exec_result, skill_exec_result.get("success", True))
                    if skill_exec_result.get("success"):
                        stdout = skill_exec_result.get("stdout", "")
                        preview = stdout[:100] if stdout else ""
                        await send_progress(f"✅ 技能「{skill_name}」执行完成: {preview}...")
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
                        await send_progress(f"❌ 技能「{skill_name}」执行失败: {detail}")

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
                    await send_progress(f"🚀 正在调用{subagent_name}子智能体处理任务...")

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
                        progress_callback=send_progress,
                        user_id=user.user_id if user else None,
                    )

                    # 发送工具执行结果
                    await send_tool_result(tool_name, delegation_result, delegation_result.get("success", True))

                    # 检查子智能体是否需要澄清（需要用户补充信息）
                    if delegation_result.get("status") == "clarifying":
                        question = delegation_result.get("question", "需要补充信息")
                        await send_progress(f"❓ {subagent_name}需要补充信息: {question[:50]}...")
                        await send_clarification(subagent_name, question)
                        yield f"\n❓ **{subagent_name}** 需要补充信息：{question}\n请提供以上信息，系统将自动继续执行任务。"
                    elif delegation_result.get("success"):
                        # 子智能体执行完成进度
                        summary = delegation_result.get("summary", "")
                        preview = summary[:100] if summary else ""
                        await send_progress(f"✅ {subagent_name}子智能体任务完成: {preview}...")
                    else:
                        error = delegation_result.get("error", "未知错误")
                        await send_progress(f"❌ {subagent_name}子智能体执行失败: {error}")

                    # 如果子智能体生成了内容（content_generate），实时展示给用户
                    if delegation_result.get("generated_contents"):
                        for content in delegation_result["generated_contents"]:
                            yield f"\n📝 **内容生成结果：**\n\n{content}\n\n"


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
                        await send_tool_result(tool_name, result, success)
                        if success:
                            # 根据不同工具显示不同结果预览
                            if tool_name == "content_generate":
                                content = result.get("content", "")
                                preview = content[:80] + "..." if len(content) > 80 else content
                                await send_progress(f"✅ {tool_display_name}完成\n📝 {preview}")
                            elif tool_name == "web_search":
                                results = result.get("results", [])
                                await send_progress(f"✅ {tool_display_name}完成，找到{len(results)}条结果")
                            elif tool_name == "email_send":
                                await send_progress(f"✅ {tool_display_name}成功")
                            elif tool_name == "file_read":
                                content = result.get("content", "")
                                preview = content[:80] + "..." if len(content) > 80 else content
                                await send_progress(f"✅ {tool_display_name}完成\n📄 {preview}")
                            elif tool_name == "browser_automation":
                                result_text = result.get("result", result.get("message", ""))
                                if result_text:
                                    preview = result_text[:80] + "..." if len(result_text) > 80 else result_text
                                    await send_progress(f"✅ {tool_display_name}完成\n{preview}")
                                else:
                                    await send_progress(f"✅ {tool_display_name}成功")
                            else:
                                await send_progress(f"✅ {tool_display_name}执行完成")
                        else:
                            error = result.get("error", "未知错误")
                            await send_progress(f"❌ {tool_display_name}失败: {error}")
                    else:
                        await send_tool_result(tool_name, result, True)
                        await send_progress(f"✅ {tool_display_name}执行完成")

                    # 对于 content_generate 工具，将结果格式化为可展示的内容并立即输出
                    if tool_name == "content_generate":
                        if isinstance(result, dict):
                            success = result.get("success")
                            content = result.get("content", "")
                            logger.info(f"[CONTENT_GEN] success={success}, content_len={len(content) if content else 0}")
                            if success and content:
                                yield f"\n📝 **内容生成结果：**\n\n{content}\n\n"
                        else:
                            logger.warning(f"[CONTENT_GEN] Unexpected result type: {type(result)}")

                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": result
                    })
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
                    await send_tool_result(tool_name, {"error": error_msg}, False)
                    await send_progress(f"❌ {self._get_tool_display_name(tool_name, tool_args)}执行出错: {str(e)}")
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
            yield "I apologize, but the task is taking too long. Please try again or break it into smaller steps."
    
    async def process_message_sync(
        self,
        user_input: str,
        session_id: str,
        user: Optional[User] = None,
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """Process message and return complete response"""
        response_parts = []
        async for chunk in self.process_message(user_input, session_id, user, attachments):
            response_parts.append(chunk)
        return "".join(response_parts)
    
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

        流程：
          2. 执行计划中的任务
        3. 将执行记录同步到主智能体的计划管理器

        Args:
            task_description: 任务描述
            parent_session_id: 父智能体的session ID
            task_record: 任务记录（用于状态更新）
            progress_callback: 进度回调函数，用于实时传递执行进度到主界面

        Returns:
            执行结果
        """
        if self.mode != AgentMode.SUBAGENT:
            raise RuntimeError("execute_as_subagent() is only for subagent mode")

        # 按需加载租户自定义 skills
        self._ensure_tenant_skills_loaded()

        # 注入 tenant_id 到需要租户隔离的工具（子智能体线程中 ContextVar 不可用）
        if self._init_tenant_id:
            for tool_name in ("attraction_search",):
                tool = self.tool_registry.get_tool(tool_name)
                if tool and hasattr(tool, 'set_tenant_id'):
                    tool.set_tenant_id(self._init_tenant_id)

        # 进度消息辅助函数
        async def send_progress(message: str):
            if progress_callback:
                await progress_callback({"type": "progress", "data": message})

        # 工具开始执行回调
        async def send_tool_start(tool_name: str, tool_args: dict):
            if progress_callback:
                await progress_callback({
                    "type": "tool_start",
                    "toolName": tool_name,
                    "toolArgs": tool_args
                })

        # 工具执行结果回调
        async def send_tool_result(tool_name: str, result: any, success: bool):
            if progress_callback:
                await progress_callback({
                    "type": "tool_result",
                    "toolName": tool_name,
                    "result": result,
                    "success": success
                })

        # LLM思考中回调
        async def send_thinking(message: str):
            if progress_callback:
                await progress_callback({"type": "thinking", "data": message})

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
                    _record = SessionRecordManager.get_current_record()
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
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls
                })
                
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
                    await send_tool_start(tool_name, tool_args)
                    await send_progress(f"🔧 [{self.subagent_config.name}] 正在执行 {tool_display_name}...")

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

                        await send_progress(f"❓ [{self.subagent_config.name}] 需要补充信息: {question[:50]}...")
                        await send_tool_result(tool_name, {"success": True, "question": question, "missing_info": missing_info}, True)

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
                        await send_tool_result(tool_name, plan_result, plan_result.get("success", True))

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
                        await send_tool_result(tool_name, skill_result, skill_result.get("success", True))
                    elif tool_name == "skill_complete":
                        skill_name = tool_args.get("skill", "")
                        summary = tool_args.get("summary", "")
                        if skill_name in self._active_skill_sessions:
                            # 延迟压缩：等 tool_message 写入 messages 后再压缩
                            pending_skill_compressions.append((self.session_id, skill_name, summary))
                            tool_result = {"success": True, "message": f"技能 {skill_name} 已完成并清理上下文"}
                            await send_progress(f"✅ [{self.subagent_config.name}] 技能「{skill_name}」执行完成")
                        else:
                            tool_result = {"success": False, "error": f"没有找到活跃的技能会话: {skill_name}"}
                        await send_tool_result(tool_name, tool_result, tool_result.get("success", True))
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
                        await send_tool_result(tool_name, skill_exec_result, skill_exec_result.get("success", True))
                        if not skill_exec_result.get("success"):
                            error = skill_exec_result.get("error") or "未知错误"
                            stderr = skill_exec_result.get("stderr", "")
                            exit_code = skill_exec_result.get("exit_code", -1)
                            detail = stderr[-300:] if stderr else (f"exit_code={exit_code}" if exit_code else error)
                            await send_progress(f"❌ [{self.subagent_config.name}] 技能「{skill_name}」执行失败: {detail}")

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
                                await send_tool_result(tool_name, result, success)
                                if success:
                                    await send_progress(f"✅ [{self.subagent_config.name}] {tool_display_name}执行完成")
                                else:
                                    error = result.get("error", "未知错误")
                                    await send_progress(f"❌ [{self.subagent_config.name}] {tool_display_name}失败: {error}")
                            else:
                                await send_tool_result(tool_name, result, True)
                                await send_progress(f"✅ [{self.subagent_config.name}] {tool_display_name}执行完成")

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
                            await send_tool_result(tool_name, {"error": str(e)}, False)

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
            await send_progress(f"✅ [{self.subagent_config.name}] 任务完成，正在整合结果...")

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
                "token_usage": subagent_token_usage
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


# Global agent instance (默认为主智能体)
master_agent = Agent(is_master=True)
agent = master_agent  # 别名，向后兼容
