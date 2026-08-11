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
from src.core.temp_logger import tlog
from src.llm.gateway import llm_gateway
from src.tools.registry import ToolRegistry
from src.tools.executor import ToolExecutor
from src.tools.base import ExecutionTarget
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


def _extract_image_refs_from_tool_result(result: Any) -> List[Dict[str, Any]]:
    """从工具返回结果中提取所有 ImageRef dict。

    识别规则（约定优于类型约束，不修改 BaseTool）：
    - ``result["images"]``：list[dict]，每个 dict 含 file_id（顶层批量图）
    - ``result["cover_image"]``：单个 dict 或 None（顶层封面图）
    - ``result["results"]``：list，遍历每项的 ``cover_image`` 字段
      （attraction_search 这类返回列表的工具用此模式）

    Args:
        result: 工具 execute 返回值（通常是 dict，也可能是其他类型）

    Returns:
        ImageRef dict 列表（不含 None / 缺 file_id 的项）
    """
    refs: List[Dict[str, Any]] = []
    if not isinstance(result, dict):
        return refs

    # 顶层 images 键
    images_val = result.get("images")
    if isinstance(images_val, list):
        for img in images_val:
            if isinstance(img, dict) and img.get("file_id"):
                refs.append(img)

    # 顶层 cover_image 键
    cover = result.get("cover_image")
    if isinstance(cover, dict) and cover.get("file_id"):
        refs.append(cover)

    # results 列表中的 cover_image（attraction_search 模式）
    results_val = result.get("results")
    if isinstance(results_val, list):
        for item in results_val:
            if isinstance(item, dict):
                nested_cover = item.get("cover_image")
                if isinstance(nested_cover, dict) and nested_cover.get("file_id"):
                    refs.append(nested_cover)

    return refs


def _normalize_image_placement(refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """给每个 ImageRef dict 补 placement 默认值（``after_text``）。

    Phase 2 不实现 inline 智能定位：``placement="inline"`` 也保留原值，
    前端 P2.7 会兜底按 after_text 渲染。空字符串/None 统一改为 after_text。
    """
    for ref in refs:
        if not ref.get("placement"):
            ref["placement"] = "after_text"
    return refs


def _preserve_suspension_sibling_results(
    messages: List[Dict[str, Any]],
    memory: Any,
    session_id: str,
    executed_results: List[Dict[str, Any]],
    pending_calls: List[Dict[str, Any]],
) -> None:
    """挂起前配对同轮兄弟 tool_call，避免丢结果或形成孤儿调用。"""
    sibling_results = list(executed_results)
    sibling_results.extend({
        "tool_call_id": pending["id"],
        "content": {
            "success": False,
            "error_code": "TOOL_DEFERRED_BY_HUMAN_ASSISTANCE",
            "error": "浏览器人工协助完成后由 Agent 重新决定是否执行",
        },
    } for pending in pending_calls)
    for sibling_result in sibling_results:
        sibling_message = {
            "role": "tool",
            "tool_call_id": sibling_result["tool_call_id"],
            "content": sibling_result["content"],
        }
        messages.append(sibling_message)
        memory.add_message(session_id, sibling_message)


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
        user_id: Optional[str] = None,
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
            user_id: 可信用户ID（子智能体工具执行身份）
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

        # Phase 7 §7.1：暂存压缩完成事件，由 _process_message_impl 在压缩阶段后
        # yield 给上层 process_message → TraceCollector.on_event 消费
        self._pending_compression_event: Optional[dict] = None

        # 共享组件 — 子智能体可覆盖 LLM 提供者
        if subagent_config and hasattr(subagent_config, 'llm_provider') and subagent_config.llm_provider:
            from src.llm.gateway import LLMGateway
            self.llm = LLMGateway(
                provider_name=subagent_config.llm_provider,
                model_codes=getattr(subagent_config, 'llm_model_codes', None),
            )
            logger.info(
                f"Agent using override LLM: provider={subagent_config.llm_provider}, "
                f"model_codes={getattr(subagent_config, 'llm_model_codes', None)}"
            )
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
        self._init_user_id = user_id
        self._loaded_tenant_id = None
        self._skills_loaded_at = 0.0

        if self.mode == AgentMode.STANDALONE:
            # 独立模式：不创建子智能体注册表和执行器
            self.subagent_registry = None
            self.subagent_executor = None
            self._register_builtin_tools()
            self._register_local_proxy_tools()
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
            self._register_local_proxy_tools()
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
        try:
            tenant_dir = SkillResolver.get_tenant_skills_dir(tenant_id)
        except Exception as e:
            # mkdir 失败（权限/磁盘满）不应抛垮 processor，跳过租户自定义 skills
            logger.warning(f"获取租户 {tenant_id} skills 目录失败，跳过租户自定义 skills: {e}")
            tenant_dir = None
        tenant_loader = None
        if tenant_dir and tenant_dir.exists():
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
        from src.tools.search.search_tool import WebSearchTool
        from src.tools.browser import BrowserAutomationTool
        from src.tools.file.read_tool import ReadTool
        from src.tools.file.write_tool import WriteTool
        from src.tools.file.edit_tool import EditTool
        from src.tools.file.cp_tool import CpTool
        from src.tools.file.grep_tool import GrepTool
        from src.tools.llm.content_generate_tool import ContentGenerateTool
        from src.tools.network.http_api import HttpApiTool

        # 注册邮件工具（不传配置，运行时通过 user_id 从数据库读取）
        self.tool_registry.register(EmailSendTool())
        self.tool_registry.register(EmailReadTool())
        self.tool_registry.register(EmailListFoldersTool())
        self.tool_registry.register(PaddleOCRDocParsingTool())
        self.tool_registry.register(WebSearchTool())
        
        # 注册浏览器工具
        self.tool_registry.register(BrowserAutomationTool())
        
        # 注册文件工具
        self.tool_registry.register(ReadTool())
        self.tool_registry.register(WriteTool())
        self.tool_registry.register(EditTool())
        self.tool_registry.register(CpTool())
        self.tool_registry.register(GrepTool())

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

        # 注册酒店知识库搜索工具
        from src.tools.knowledge.hotel_search_tool import HotelSearchTool
        self.tool_registry.register(HotelSearchTool())

        # 注册 Word 文档处理工具
        from src.tools.word.word_process_tool import WordProcessTool
        self.tool_registry.register(WordProcessTool())

        # 注册 Excel 电子表格处理工具
        from src.tools.excel.excel_process_tool import ExcelProcessTool
        self.tool_registry.register(ExcelProcessTool())

        # 注册 PDF 文档处理工具
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        self.tool_registry.register(PdfProcessTool())

        # 注册 x-to-image 内容转图片工具
        from src.tools.image.x_to_image_tool import XToImageTool
        self.tool_registry.register(XToImageTool())

        # 注册 PPT 生成工具
        from src.tools.ppt.ppt_process_tool import PptProcessTool
        self.tool_registry.register(PptProcessTool())

        # 注册视频创作提交工具（video-agent 子智能体使用，从 _video_params 读取前端参数）
        from src.tools.video.submit_video_task_tool import SubmitVideoTaskTool
        self.tool_registry.register(SubmitVideoTaskTool())

        # 注册转人工客服工具
        from src.tools.transfer_to_human import TransferToHumanTool
        self.tool_registry.register(TransferToHumanTool())

        # 注册 AI 外呼工具（Mock 实现）
        from src.tools.phone.ai_call_tool import AICallTool
        self.tool_registry.register(AICallTool())

        # 语音转文字（ASR）已在渠道层（channel_routes.py）完成，不注册为 LLM 工具
        # from src.tools.asr.speech_to_text_tool import SpeechToTextTool
        # self.tool_registry.register(SpeechToTextTool())

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
        excluded_tools = self.subagent_config.get_excluded_tools()

        # 如果配置为继承，保留所有工具，再 pop 黑名单
        if self.subagent_config.tools.get("inherit", False):
            for tool_name in excluded_tools:
                self.tool_registry._tools.pop(tool_name, None)
            if excluded_tools:
                logger.info(
                    f"Subagent {self.subagent_config.name} inherits all tools, "
                    f"excluded: {excluded_tools}"
                )
            else:
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

        # allowed 模式下也应用 excluded 黑名单
        for tool_name in excluded_tools:
            self.tool_registry._tools.pop(tool_name, None)
        if excluded_tools:
            logger.info(
                f"Subagent {self.subagent_config.name} excluded tools: {excluded_tools}"
            )

    def _register_local_proxy_tools(self):
        """注册本地代理工具（boss_* proxy，LOCAL_REQUIRED）

        仅非主智能体且 subagent_config 的 allowed 工具列表与本地代理工具名有交集时注册。
        主智能体、inherit=true（get_allowed_tools 返回空）或无交集的子智能体
        永远看不到这些工具（设计 §11：主 Agent 和其他子智能体不获得 BOSS 工具）。
        """
        if self.mode == AgentMode.MASTER or not self.subagent_config:
            return
        from src.local_tools.proxy_tool import LOCAL_PROXY_TOOL_CLASSES

        allowed = set(self.subagent_config.get_allowed_tools())
        registered = 0
        for tool_cls in LOCAL_PROXY_TOOL_CLASSES:
            if tool_cls.name in allowed:
                self.tool_registry.register(tool_cls())
                registered += 1
        if registered:
            logger.info(
                f"Registered {registered} local proxy tools for subagent "
                f"{self.subagent_config.name}"
            )

    async def _run_local_required_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        tenant_id: Optional[str],
        user_id: Optional[str],
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> AsyncGenerator[tuple, None]:
        """执行 LOCAL_REQUIRED 本地工具并流式产出进度（m05-implementation-spec §5）

        依次产出 ("progress", text)（本机执行事件），最后产出一次 ("result", result_dict)。
        取消时只 request_cancel，不中断等待——等 proxy 自身到终态，
        保证 tool_call 有配对结果。
        """
        from src.local_tools import repository

        execution_args = dict(tool_args)
        execution_args["_trusted_tenant_id"] = tenant_id
        execution_args["_trusted_user_id"] = user_id
        progress_queue: asyncio.Queue = asyncio.Queue()
        execution_args["_progress_queue"] = progress_queue
        task = asyncio.create_task(self.tool_executor.execute(tool_name, execution_args))
        current_invocation_id = None
        cancel_requested = False
        try:
            while not task.done():
                evt = None
                try:
                    evt = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                if evt is not None:
                    if evt.get("type") == "started":
                        current_invocation_id = evt.get("invocation_id")
                    text = evt.get("text")
                    if text:
                        yield ("progress", text)
                if (
                    not cancel_requested
                    and cancel_check
                    and cancel_check()
                    and current_invocation_id
                    and tenant_id
                ):
                    cancel_requested = True
                    await asyncio.to_thread(
                        repository.request_cancel, current_invocation_id, tenant_id
                    )
            # 队列可能还有 proxy 收尾前推入的事件，排空
            while not progress_queue.empty():
                evt = progress_queue.get_nowait()
                text = evt.get("text")
                if text:
                    yield ("progress", text)
            yield ("result", await task)
        finally:
            if not task.done():
                task.cancel()
                # 防止孤儿 invocation：生成器被提前关闭（如客户端断开）时 proxy 被取消，
                # 但 invocation 仍为 queued/running，设备稍后会领取并执行
                # 用户已看不到结果的写动作——必须请求取消
                if current_invocation_id and tenant_id:
                    try:
                        await asyncio.to_thread(
                            repository.request_cancel, current_invocation_id, tenant_id
                        )
                        logger.info(
                            f"后端日志：本地工具生成器提前关闭，已请求取消孤儿 invocation "
                            f"id={current_invocation_id} tool={tool_name}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"后端日志：取消孤儿 invocation 失败 id={current_invocation_id}: {e}"
                        )
    
    def _get_tools(self) -> List[Dict[str, Any]]:
        """
        Get tool definitions from ToolRegistry + virtual tools + dynamic tools

        Schema 来源：每个工具类通过 Pydantic InputModel 或 parameters_schema 定义。
        MASTER：包含委派工具
        SUBAGENT / STANDALONE：不包含委派工具
        """
        # 1. 从 ToolRegistry 获取所有已注册工具的 schema
        tools = self.tool_registry.get_tool_definitions()

        # 2. 添加虚拟工具定义（skill_execute, create_plan, clarify）
        # 这些工具不放入 tool_registry，但需要将定义暴露给 LLM
        virtual_tools = [
            self._skill_execute_tool,
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

    # 支持的图片扩展名 -> MIME 映射（用于构造多模态 image_url data URL）
    _IMAGE_EXT_MIME = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
    }
    _MULTIMODAL_MAX_IMAGES = 3
    _MULTIMODAL_MAX_BYTES = 5 * 1024 * 1024  # 单张 5MB

    def _build_multimodal_user_content(
        self, text: str, image_paths: Optional[List[str]]
    ) -> Optional[List[Dict[str, Any]]]:
        """构造 OpenAI 多模态 user content（text + image_url data:base64）。

        - image_paths 为空或全部无效时返回 None（调用方按纯文本处理）
        - 单张图片超过 _MULTIMODAL_MAX_BYTES 跳过并记 warning
        - 最多 _MULTIMODAL_MAX_IMAGES 张，超出忽略
        - 不支持的扩展名跳过
        - base64 仅 in-memory 传给 LLM，不持久化

        返回的 content 格式：
            [
                {"type": "text", "text": <text>},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
            ]
        """
        if not image_paths:
            return None

        parts: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        attached = 0

        for path in image_paths:
            if attached >= self._MULTIMODAL_MAX_IMAGES:
                logger.warning(
                    f"[SUBAGENT] image_paths 超过 {self._MULTIMODAL_MAX_IMAGES} 张上限，忽略后续: {path}"
                )
                break

            ext = Path(path).suffix.lstrip(".").lower()
            mime = self._IMAGE_EXT_MIME.get(ext)
            if not mime:
                logger.warning(f"[SUBAGENT] 不支持的图片格式，跳过: {path}")
                continue

            try:
                data = Path(path).read_bytes()
            except Exception as e:
                logger.warning(f"[SUBAGENT] 读取图片失败，跳过: {path}, err={e}")
                continue

            if len(data) > self._MULTIMODAL_MAX_BYTES:
                logger.warning(
                    f"[SUBAGENT] 图片过大（{len(data)} bytes > {self._MULTIMODAL_MAX_BYTES}），跳过: {path}"
                )
                continue

            import base64
            b64 = base64.b64encode(data).decode("ascii")
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
            attached += 1

        if attached == 0:
            return None
        return parts

    def _build_system_prompt(
        self,
        user: Optional[User] = None,
        extra_system_prompt: Optional[str] = None,
    ) -> str:
        """
        Build system prompt for the agent

        MASTER：包含委派能力
        SUBAGENT / STANDALONE：不包含委派能力，使用子智能体配置的约束 + 租户定制 extra.md

        Args:
            user: 用户信息
            extra_system_prompt: 渠道级额外提示词（如 wecom_kf 的渠道能力约束），
                追加到基础 system prompt 末尾。仅主流程调用方传入，子智能体不传。
        """
        if self.mode == AgentMode.MASTER:
            base_prompt = self._build_base_system_prompt(include_delegation=True, user=user)
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

            base_prompt = self._build_base_system_prompt(
                include_delegation=False,
                subagent_constraint=subagent_constraint,
                user=user
            )

        # 追加渠道级额外提示词（如 wecom_kf 的渠道能力约束）
        if extra_system_prompt:
            base_prompt = base_prompt + "\n" + extra_system_prompt
        return base_prompt

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
            from src.prompts.renderer import render_sections
            return render_sections(template, section_map)

        return template

    def _load_extra_md(self) -> Optional[str]:
        """加载租户定制的 extra.md（Phase 4 起从数据库加载）

        从 prompt_versions 表读取 scope='tenant_extra' 的 production 版本。
        数据库无记录时返回 None（等价于"该租户未配置定制内容"）。
        """
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

        try:
            from src.prompts.prompt_resolver import prompt_resolver
            content = prompt_resolver.resolve(
                scope="tenant_extra",
                scope_id=f"extra:{self.subagent_config.dir_name}:{tenant_id}",
                tenant_id=tenant_id,
            )
            if content:
                logger.debug(
                    f"Loaded extra.md (DB) for tenant {tenant_id}, "
                    f"subagent {self.subagent_config.dir_name}"
                )
                return content.strip()
        except Exception as e:
            logger.warning(f"Failed to load extra.md from DB: {e}")

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
        1. 子智能体/独立模式且配置了 reply_style
        2. 全局默认（config.yaml 中 agent.reply_style）
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

        # 优先级 1：子智能体/独立模式且配置了 reply_style
        if self.mode != AgentMode.MASTER and self.subagent_config:
            if self.subagent_config.reply_style:
                return self.subagent_config.reply_style

        # 优先级 2：全局默认
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

    def _detect_source_type(self) -> str:
        """检测当前请求的 source_type（v3.1 Phase 4）。

        优先级：
        1. 显式 SessionRecordService 的 source_type 字段（trace_collector 流程写入）
        2. self.session_id 是否在 channel_sessions 表中（渠道消息走渠道表）
        3. 默认 'chat'（Web 渠道，走 chat_sessions/chat_messages）

        Returns:
            'chat' / 'wecom_kf' / 'dingtalk' / 'feishu' / 'wecom_personal_rpa'
        """
        # 优先从 SessionRecordService 读（最准确，由调用方在请求入口写入）
        try:
            from src.services.session_record import SessionRecordManager
            _record = (
                getattr(self, '_explicit_record_service', None)
                or SessionRecordManager.get_current_record()
            )
            if _record and getattr(_record, "source_type", None):
                return _record.source_type
        except Exception:
            pass
        # 默认 Web 渠道（chat_sessions/chat_messages）
        return "chat"

    async def _run_compression_phase(self, session_id: str) -> "Optional[Any]":
        """v3.2 压缩阶段（v3.2.1 P1-3：从 _process_message_impl 提取为独立方法）。

        执行 v3.2 顺序优化后的压缩流程：
        1. check_threshold（O(1) 单行查询 + COUNT 走索引，不拉 messages）
        2. should=True 时调 compress_now（拉 messages + 分段 + LLM 摘要 + 持久化）
        3. reason 透传给 compress_now（v3.2.1 P0-1：保留精确触发原因）

        Phase 7 §7.1：压缩成功后构造 ContextCompressedEvent 并存到
        `self._pending_compression_event`，由 _process_message_impl 在压缩阶段
        之后 yield 给上层 process_message → TraceCollector.on_event 消费，
        在 trace 中留下紫色 span。同时记录压缩耗时和指标。

        异常隔离：本方法内任何异常都仅记录日志，不向上抛出。压缩失败不影响主流程。

        Args:
            session_id: 会话 ID

        Returns:
            CompressionResult（成功压缩）或 None（未达阈值 / 压缩失败 / 服务禁用）。
            主流程不需要消费返回值（memory 重建天然过滤 compacted=true），
            返回值主要用于测试与可观测性。
        """
        if not settings.memory.mid_term.enabled:
            return None
        try:
            from src.memory.mid_term import get_compression_service
            compression_service = get_compression_service()
            source_type = self._detect_source_type()
            # O(1) 单行查询 + COUNT 查询，不做完整 IO
            should_compress, reason, session_meta = await compression_service.check_threshold(
                session_id, source_type
            )
            if should_compress:
                # 仅在需要压缩时才执行（compress_now 内部会读全部 messages）
                # v3.2.1（P0-1）：透传 check_threshold 的精确 reason
                import time as _time
                _t0 = _time.perf_counter()
                result = await compression_service.compress_now(
                    session_id, source_type, session_meta, trigger_reason=reason
                )
                duration_ms = int((_time.perf_counter() - _t0) * 1000)
                if result is not None:
                    logger.info(
                        f"ContextCompression triggered: sid={session_id}, "
                        f"summary_id={result.summary_id}, "
                        f"reason={result.trigger_reason}, "
                        f"compacted={result.compressed_message_count} msgs, "
                        f"token {result.original_token_count}->{result.compressed_token_count}, "
                        f"fallback={result.fallback_used}"
                    )
                    # Phase 7 §7.1：构造 trace 事件（由 _process_message_impl yield 出去）
                    # Phase 7 §7.2：同步打点（success / fallback / duration / ratio）
                    try:
                        from src.core.agent_events import ContextCompressedEvent
                        from src.core.compression_metrics import get_compression_metrics
                        event = ContextCompressedEvent(
                            summary_id=result.summary_id,
                            compressed_message_count=result.compressed_message_count,
                            original_token_count=result.original_token_count,
                            compressed_token_count=result.compressed_token_count,
                            compression_ratio=result.compression_ratio,
                            fallback_used=result.fallback_used,
                            trigger_reason=result.trigger_reason,
                            llm_provider=result.llm_provider,
                            llm_model=result.llm_model,
                            duration_ms=duration_ms,
                        )
                        # 暂存，让 _process_message_impl 在压缩后立即 yield
                        self._pending_compression_event = event.to_dict()
                        # 指标埋点
                        metrics = get_compression_metrics()
                        metrics.record_invocation(
                            "fallback" if result.fallback_used else "success",
                            source_type,
                        )
                        metrics.record_duration(duration_ms / 1000.0)
                        metrics.record_ratio(result.compression_ratio)
                    except Exception as oe:
                        logger.debug(f"compression metrics/trace emit failed: {oe}")
                    return result
            else:
                # 未触发阈值：skipped 计数
                try:
                    from src.core.compression_metrics import get_compression_metrics
                    get_compression_metrics().record_invocation("skipped", source_type)
                except Exception as oe:
                    logger.debug(f"compression metrics skipped-emit failed: {oe}")
        except Exception as e:
            # 压缩失败不影响主流程，本轮跳过压缩，下一轮再试
            # v3.2.1（P1-4）：用 action=skipped_due_to_error 明确标识「本轮因异常跳过」，
            # 便于指标埋点过滤「正常未触发阈值（reason 空）」vs「服务挂了」
            logger.exception(
                f"ContextCompression skipped_due_to_error, sid={session_id}: {e}"
            )
            try:
                from src.core.compression_metrics import get_compression_metrics
                get_compression_metrics().record_invocation("failed", "")
            except Exception as oe:
                logger.debug(f"compression metrics failed-emit failed: {oe}")
        return None

    def _reload_memory_from_db(self, session_id: str) -> None:
        """从 DB 重新加载 memory（v3.1 Phase 4）。

        压缩完成后调用：compacted=true 已写入 DB，重新加载后 memory 中自动
        过滤掉被压缩的消息，本轮 _build_messages 拿到的就是压缩后的新上下文。

        复用 _process_message_impl 中既有的重建逻辑，区分 chat / channel 两套消息表。
        """
        try:
            import json as _json
            source_type = self._detect_source_type()
            if source_type == "chat":
                from src.db.models import MessageDB
                db_messages = MessageDB.list_by_session(
                    session_id,
                    limit=self.memory.short_term.max_messages,
                )
                self.memory.clear(session_id)
                if not db_messages:
                    return
                history_messages: List[Dict[str, Any]] = []
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
                        })
                    elif role == "assistant":
                        entry: Dict[str, Any] = {
                            "role": "assistant",
                            "content": content,
                        }
                        if meta.get("tool_calls"):
                            entry["tool_calls"] = meta["tool_calls"]
                        history_messages.append(entry)
                    else:
                        history_messages.append({"role": role, "content": content})
                self.memory.load_history(session_id, history_messages)
            else:
                # 渠道消息：走 channel_messages 表
                history_messages = self._load_channel_history(session_id, "")
                self.memory.clear(session_id)
                if history_messages:
                    self.memory.load_history(session_id, history_messages)
        except Exception as e:
            logger.warning(
                f"ContextCompression _reload_memory_from_db failed, sid={session_id}: {e}"
            )

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
                include_recalled=False,  # LLM 上下文剔除已撤回消息
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

            # 字段映射与 chat_messages 主分支（agent.py:1602-1626）对称：
            # 从 metadata 恢复 tool_calls / tool_call_id / reasoning_content
            history: List[Dict[str, Any]] = []
            for msg in channel_msgs:
                role = msg["role"]
                content = msg["content"] or ""
                meta = msg.get("metadata") or {}
                if role == "tool":
                    history.append({
                        "role": "tool",
                        "tool_call_id": meta.get("tool_call_id", ""),
                        "content": content,
                        "timestamp": msg.get("created_at", ""),
                    })
                elif role == "assistant":
                    entry: Dict[str, Any] = {
                        "role": "assistant",
                        "content": content,
                        "timestamp": msg.get("created_at", ""),
                    }
                    if meta.get("tool_calls"):
                        entry["tool_calls"] = meta["tool_calls"]
                    if meta.get("reasoning_content"):
                        entry["reasoning_content"] = meta["reasoning_content"]
                    history.append(entry)
                else:  # user / system
                    history.append({
                        "role": role,
                        "content": content,
                        "timestamp": msg.get("created_at", ""),
                    })
            # 诊断：记录加载到的历史消息概览，用于对比上下文是否缺消息
            try:
                # from src.core.temp_logger import tlog as _tlog
                _roles_preview = []
                for m in history:
                    c = m.get("content", "")
                    if not isinstance(c, str):
                        c = str(c)
                    _roles_preview.append(f"{m.get('role')}:{c[:40]!r}")
                # _tlog(
                #     "语音合并",
                #     "[加载历史] session={sid}..., count={n}, msgs={preview}, "
                #     "current_user_input_len={cu_len}, current_user_input_preview={cu_prev!r}",
                #     sid=session_id[:20],
                #     n=len(history),
                #     preview=_roles_preview,
                #     cu_len=len(current_user_input),
                #     cu_prev=current_user_input[:80],
                # )
            except Exception:
                pass
            return history
        except Exception as e:
            logger.warning(f"Failed to load channel history for session {session_id}: {e}")
            return []

    def _build_messages(
        self,
        session_id: str
    ) -> List[Dict[str, Any]]:
        """
        Build message list for LLM from memory.

        健壮性保障（输出无论 DB 返回顺序如何都满足 LLM API 约束）：
        1. 跳过空 content 的 user/assistant 消息（防止空 user 导致 API 报错）
        2. 每条 assistant(tool_calls) 后「立即、连续」跟随其匹配的 tool 结果（按 tool_calls
           声明顺序）；无任何匹配结果的 tool_calls 被丢弃，assistant 降级为普通内容
        3. system 消息转为 user 消息（部分 LLM API 不允许在对话序列中插入 system）
        4. 孤立的 tool 消息（无对应 assistant(tool_calls)）一律跳过
        背景：单事务批量写入会让同轮消息 created_at 相同，若查询缺二级排序键，返回顺序会
        错乱；这里按 tool_call_id 重新配对重建合法序列，不依赖 DB 返回顺序。
        """
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

        # v3.1 Phase 4: 注入 active 摘要到最前面（user+assistant 对，避免连续 user 被合并）
        try:
            if settings.memory.mid_term.enabled:
                from src.memory.mid_term import get_compression_service
                compression_service = get_compression_service()
                source_type = self._detect_source_type()
                active_summary = compression_service.get_active_summary(session_id, source_type)
                if active_summary:
                    history = [
                        {"role": "user", "content": f"[📋 之前对话摘要]\n{active_summary}"},
                        {"role": "assistant", "content": "好的，已了解之前对话的要点。"},
                        *history,
                    ]
        except Exception as e:
            logger.warning(f"读取 active summary 失败, sid={session_id}: {e}")

        messages = self._reorder_messages_for_llm(history)

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

    @staticmethod
    def _reorder_messages_for_llm(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        将（可能因 DB 排序错乱而乱序的）历史消息重建为 LLM API 合法序列。

        不依赖输入顺序：按 tool_call_id 重新配对，确保每条 assistant(tool_calls)
        后立即、连续地跟随其匹配 tool 结果（按声明顺序）；丢弃孤儿 tool，降级悬空 tool_calls。
        """
        messages: List[Dict[str, Any]] = []

        # 第一遍：tool_call_id -> tool 结果消息（tool_call_id 唯一，取首条，忽略重复）
        tool_result_by_id: Dict[str, Dict[str, Any]] = {}
        for msg in history:
            if msg.get("role") == "tool":
                tc_id = msg.get("tool_call_id", "")
                if tc_id and tc_id not in tool_result_by_id:
                    tool_result_by_id[tc_id] = msg

        # 第二遍：按历史顺序输出；assistant(tool_calls) 立即消费其全部匹配 tool 结果
        consumed_tool_ids: set = set()  # 已随某条 assistant 输出的 tool_call_id
        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "assistant" and msg.get("tool_calls"):
                tool_calls = msg.get("tool_calls", []) or []
                # 按 tool_calls 声明顺序，收集「存在结果且尚未被消费」的配对
                matched = [
                    (tc, tool_result_by_id[tc["id"]])
                    for tc in tool_calls
                    if tc.get("id") and tc["id"] in tool_result_by_id and tc["id"] not in consumed_tool_ids
                ]
                if matched:
                    kept_tc = [tc for tc, _ in matched]
                    asst_msg = {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": kept_tc,
                    }
                    if msg.get("reasoning_content"):
                        asst_msg["reasoning_content"] = msg["reasoning_content"]
                    messages.append(asst_msg)
                    for tc, tool_msg in matched:
                        consumed_tool_ids.add(tc["id"])
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": tool_msg.get("content", ""),
                        })
                else:
                    # 悬空 tool_calls：无任何匹配结果，降级为普通 assistant 避免 API 400
                    tc_id_list = [tc.get("id", "") for tc in tool_calls]
                    logger.warning(
                        f"后端日志：_build_messages 降级悬空 tool_calls（无匹配 tool 结果），"
                        f"tc_ids={tc_id_list}"
                    )
                    if content:
                        asst_msg = {"role": "assistant", "content": content}
                        if msg.get("reasoning_content"):
                            asst_msg["reasoning_content"] = msg["reasoning_content"]
                        messages.append(asst_msg)
            elif role == "tool":
                # tool 结果已在 assistant(tool_calls) 分支随其调用方连续输出，此处跳过
                continue
            elif role == "assistant":
                # 普通 assistant 消息，跳过空 content
                if content:
                    asst_msg = {"role": "assistant", "content": content}
                    if msg.get("reasoning_content"):
                        asst_msg["reasoning_content"] = msg["reasoning_content"]
                    messages.append(asst_msg)
            else:
                # system 消息转为 user 消息（LLM API 不允许对话序列中插入 system）
                # 跳过空 content 的消息
                if content:
                    messages.append({"role": "user", "content": content})

        # P0-3 兜底：清洗连续 user（保留最新一条），防御历史脏数据 / 极端 race。
        # 连续 user 会导致 LLM API 行为异常（多数提供商把第二条 user 视作新轮次输入，
        # 历史 assistant 上下文失效）。此处丢弃较早的，仅保留最新 user。
        cleaned: List[Dict[str, Any]] = []
        prev_role: Optional[str] = None
        dropped_user_count = 0
        dropped_users: List[Dict[str, Any]] = []  # 诊断：记录被丢弃的 user 消息
        for msg in messages:
            role = msg.get("role")
            if role == "user" and prev_role == "user":
                # 前一条 user 已 append，弹出它（丢弃较早的），保留当前最新一条
                dropped = cleaned.pop()
                dropped_user_count += 1
                dropped_users.append(dropped)
            cleaned.append(msg)
            prev_role = role
        if dropped_user_count > 0:
            # 诊断：输出被丢弃的 user 内容 + 完整序列概览，定位"连续 user"来源
            # 可能来源：① 上一轮 agent 返回空响应 -> assistant 空内容被跳过
            #          ② 飞书事件去重失效（多 worker 下 _feishu_event_dedup 是进程内 dict）
            #          ③ 批量写入部分失败
            def _preview(m: Dict[str, Any], n: int = 120) -> str:
                c = m.get("content", "")
                if not isinstance(c, str):
                    c = str(c)
                return repr(c[:n])

            dropped_preview = [_preview(m) for m in dropped_users]
            seq_preview = [
                f"{m.get('role')}:{_preview(m, 60)}"
                for m in messages[:30]
            ]
            logger.warning(
                f"后端日志：_reorder_messages_for_llm 检测到连续 user，"
                f"已丢弃较早的 {dropped_user_count} 条（保留最新）。"
                f"被丢弃 user 内容: {dropped_preview}. "
                f"完整序列前30条: {seq_preview}"
            )

        # 裁剪窗口起始边界：长会话取最近 N 条后，开头可能落在 assistant（甚至 tool 结果）上，
        # 而多数 LLM API（DeepSeek/OpenAI 兼容）要求序列首条（system 之后）必须是 user，
        # 否则报 400。丢弃开头的非 user 消息，直到第一条 user，使截断边界对齐到安全位置。
        # （孤儿 tool 已在上方被跳过，不会出现在开头；此处主要裁掉开头的 assistant。
        #   连同其后的 tool 一起被丢弃，不会产生新的孤儿 tool。）
        start = 0
        while start < len(cleaned) and cleaned[start].get("role") != "user":
            start += 1
        if 0 < start < len(cleaned):
            logger.info(
                f"后端日志：_reorder_messages_for_llm 裁剪窗口起始 {start} 条非 user 消息"
                f"（长会话截断边界对齐到 user，避免首条非 user 触发 LLM API 400）"
            )
            cleaned = cleaned[start:]

        return cleaned

    def _check_skill_version_consistency(
        self, session_id: str, skill_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        校验该 skill 在本会话上下文中的版本是否与 registry 当前版本一致。

        Args:
            session_id: 会话ID
            skill_name: 技能名称

        Returns:
            None  → 通过，可继续执行
            dict  → 拦截结果（含 success=False 和提示消息），直接作为 skill_execute 返回给 LLM
        """
        if not self.skill_registry:
            return None
        skill_obj = self.skill_registry.get(skill_name)
        if not skill_obj:
            # skill 不存在交给后续原逻辑报错
            return None
        current_version = skill_obj.version

        historical_version = self._get_last_use_skill_version(session_id, skill_name)

        if historical_version == current_version:
            return None

        # 不一致 / 无历史记录 / 历史返回缺字段 → 拦截
        if historical_version is None:
            reason = f"本会话尚未加载过该技能的最新指南（当前版本 v{current_version}）"
        else:
            reason = f"技能版本已更新（历史 v{historical_version} → 当前 v{current_version}）"
        logger.info(f"后端日志：skill_execute 被版本校验拦截", extra={
            "skill_name": skill_name,
            "historical_version": historical_version,
            "current_version": current_version,
        })
        return {
            "success": False,
            "error": (
                f"⚠️ {reason}。请先调用 use_skill(skill=\"{skill_name}\") 重新加载最新操作指南，"
                f"然后再调用 skill_execute 执行脚本。"
            ),
            "skill_name": skill_name,
            "historical_version": historical_version,
            "current_version": current_version,
        }

    def _get_last_use_skill_version(
        self, session_id: str, skill_name: str
    ) -> Optional[str]:
        """
        扫描 session 历史消息，找到该 skill 最近一次 use_skill 调用返回的 skill_version。

        识别方式：
        - 遍历 memory 中 role=assistant 且带 tool_calls 的消息，找到 function.name == "use_skill"
          且 arguments.skill == skill_name 的调用，记录其 tool_call_id。
        - 再在 role=tool 的消息里按 tool_call_id 取出 content（JSON 字符串），
          解析后取 skill_version 字段。

        Args:
            session_id: 会话ID
            skill_name: 技能名称

        Returns:
            最近一次 use_skill 返回的 skill_version；没找到返回 None。
        """
        import json as _json
        try:
            messages = self.memory.get_context(session_id) or []
        except Exception as e:
            logger.warning(f"后端日志：读取 memory 失败: {e}")
            return None

        # 第一遍：找到该 skill 最近一次 use_skill 调用的 tool_call_id
        target_tool_call_id = None
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                try:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    if fn.get("name") != "use_skill":
                        continue
                    args_raw = fn.get("arguments", "{}")
                    args = _json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                    if args.get("skill") == skill_name:
                        target_tool_call_id = tc.get("id")
                        break
                except (ValueError, TypeError):
                    continue
            if target_tool_call_id:
                break

        if not target_tool_call_id:
            return None

        # 第二遍：按 tool_call_id 找 tool 返回中的 skill_version
        for msg in reversed(messages):
            if msg.get("role") != "tool":
                continue
            if msg.get("tool_call_id") != target_tool_call_id:
                continue
            content = msg.get("content", "")
            try:
                payload = _json.loads(content) if isinstance(content, str) else content
            except (ValueError, TypeError):
                return None
            if isinstance(payload, dict):
                return payload.get("skill_version")
        return None

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
        extra_system_prompt: Optional[str] = None,
        _continuation_tool_result: Optional[Dict[str, Any]] = None,
        video_params: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Process a user message and yield AgentEvent dicts (trace-wrapped).

        Wrapper around `_process_message_impl` that attaches a TraceCollector
        when a SessionRecordService is available, so all channels (Web/wecom/
        wecom_kf/dingtalk/feishu) produce traces without any caller-side change.
        Trace failures are swallowed (debug log) and never affect business logic.

        See: docs/infrastructure/observability-channel-sessions-design.md

        Args:
            video_params: 前端工具栏传入的视频创作参数（仅 video-agent 子智能体使用）。
                         通过 _current_video_params 实例变量供工具读取，执行完成后清理。
        """
        # 视频创作参数存到实例变量，供工具执行时读取（同请求期间有效，finally 清理）
        self._current_video_params = video_params
        trace_collector = None
        try:
            from src.services.session_record import SessionRecordManager
            _record = getattr(self, '_explicit_record_service', None) \
                      or SessionRecordManager.get_current_record()
            if _record:
                try:
                    from src.core.trace_collector import TraceCollector
                    # 优先用 user_input（语音合并 / 追加消息后的最终输入），
                    # _record.user_message 是渠道入口 start_record 时设置的单条原始消息，
                    # 在 wecom_kf 等渠道的语音合并场景下只有首句，会导致 trace.input 丢失追加内容。
                    trace_collector = TraceCollector(
                        session_id=_record.session_id or session_id,
                        tenant_id=_record.tenant_id or '',
                        user_id=_record.user_id or '',
                        input_msg=user_input or _record.user_message,
                        source_type=_record.source_type or 'chat',
                        subagent_id=getattr(self, '_subagent_id', None),
                    )
                    # 注入 trace_collector 引用，process_and_persist 写入
                    # channel_messages 后通过它回填 user_message_id（用于
                    # monitor.py 精确匹配撤回状态）
                    if _record:
                        _record.trace_collector = trace_collector
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
                extra_system_prompt=extra_system_prompt,
                _continuation_tool_result=_continuation_tool_result,
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
            # 清理视频创作参数（同请求期间持有，避免跨请求污染）
            if hasattr(self, '_current_video_params'):
                delattr(self, '_current_video_params')

    def _format_video_params_for_llm(self, video_params: Dict[str, Any]) -> str:
        """把前端工具栏选择的视频参数格式化为 LLM 可读说明文本

        仅 video-agent 子智能体在 process_message 期间持有 _current_video_params。
        注入到对话上下文后，LLM 能直接看到用户已确定的参数，避免重复询问时长/比例/模式。
        """
        mode = video_params.get("mode", "refine")
        mode_label = "精修（refine）" if mode == "refine" else "敏捷（agile）"
        duration = int(video_params.get("duration_sec", 5))
        ratio = video_params.get("ratio", "9:16")
        resolution = video_params.get("resolution", "720P")
        card_count = int(video_params.get("card_count", 1))
        prompt_model = video_params.get("prompt_model", "qwen-vl-plus")
        return (
            "<video-params>\n"
            "用户已通过前端「视频生成参数」面板指定本次视频创作参数，请直接遵循，"
            "除非用户明确表示要修改，否则不要向用户重复询问以下信息：\n"
            f"- 创作模式：{mode_label}\n"
            f"- 视频时长：{duration} 秒\n"
            f"- 视频比例：{ratio}\n"
            f"- 分辨率：{resolution}\n"
            f"- 生成条数：{card_count} 条\n"
            "</video-params>"
        )

    async def _process_message_impl(
        self,
        user_input: str,
        session_id: str,
        user: Optional[User] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        extra_system_prompt: Optional[str] = None,
        _continuation_tool_result: Optional[Dict[str, Any]] = None,
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
        from src.core.agent_events import make_event, make_image_event

        # 后端日志：检查是否有待处理的澄清请求
        pending_clarification = (
            None if _continuation_tool_result else self._get_pending_clarification(session_id)
        )
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

        # === [v3.2] 先快速阈值检查，再决定是否走完整 IO 路径 ===
        # 原执行顺序：先重建 memory（完整 IO）→ 再 compress_session（重复 IO）
        # 新执行顺序：先 check_threshold（O(1) + COUNT）→ 仅在需要时 compress_now →
        #   最后重建 memory（唯一一次完整 IO，自动过滤 compacted=true）
        # v3.2.1（P1-3）：压缩段提取为 _run_compression_phase 独立方法，
        # 便于 test_process_message_order.py 通过真实方法调用做回归保护
        if not _continuation_tool_result:
            await self._run_compression_phase(session_id)
        # Phase 7 §7.1：压缩成功后 yield context_compressed 事件，
        # 上层 process_message 的 trace_collector 会接收并在 trace 中留下紫色 span
        if self._pending_compression_event is not None:
            _ev = self._pending_compression_event
            self._pending_compression_event = None
            yield _ev
        # === [v3.2 压缩阶段结束] ===

        # === 重建 memory from DB（v3.2：唯一一次完整 IO，自动过滤 compacted=true）===
        # 压缩发生时 compacted=true 已写入 DB，本次重建天然过滤掉被压缩消息，
        # 无需再调用 _reload_memory_from_db 二次重建。
        try:
            from src.db.models import MessageDB
            import json as _json
            # 清除可能过时的内存数据，用 DB 最新历史重建
            self.memory.clear(session_id)

            # 会话来源分流：渠道会话读 channel_messages，web 会话读 chat_messages，两者严格分离。
            # 历史误写会让渠道会话的 chat_messages 残留陈旧行，若误用作上下文会劫持真实对话
            # （详见 docs/research/wecom-kf-context-loss-research.md §9）
            from src.channels.session import channel_session_manager
            is_channel = channel_session_manager.is_channel_session(session_id)
            # 渠道会话不查 chat_messages（避免浪费 + 防止陈旧数据混入）
            db_messages = [] if is_channel else MessageDB.list_by_session(
                session_id,
                limit=self.memory.short_term.max_messages,
            )

            if is_channel:
                # 渠道会话（企业微信/钉钉/飞书）：仅从 channel_messages 重建，忽略 chat_messages
                history_messages = self._load_channel_history(session_id, user_input)
                if history_messages:
                    self.memory.load_history(session_id, history_messages)
                    logger.info(
                        f"Loaded {len(history_messages)} channel history messages "
                        f"for session {session_id}"
                    )
                else:
                    logger.debug(f"No channel history for session {session_id}, memory cleared")
            elif db_messages:
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
                # web 会话且 chat_messages 无历史
                logger.debug(f"No DB history for session {session_id}, memory cleared")
        except Exception as e:
            logger.warning(f"Failed to rebuild memory from DB for session {session_id}: {e}")

        # 设置工具的 user_id / tenant_id
        file_output_tools = []  # 注册下载的文件工具（write / cp）
        if user:
            for tool_name in ("email_send", "email_read", "email_list_folders", "browser_automation"):
                tool = self.tool_registry.get_tool(tool_name)
                if tool and hasattr(tool, 'set_user_id'):
                    tool.set_user_id(user.user_id)

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
            for tool_name in ("attraction_search", "hotel_search", "knowledge_base_search"):
                tool = self.tool_registry.get_tool(tool_name)
                if tool and hasattr(tool, 'set_tenant_id'):
                    tool.set_tenant_id(_resolve_tenant_id)
            # 注入 tenant_id 到文件输出工具
            for tool in file_output_tools:
                if hasattr(tool, 'set_tenant_id'):
                    tool.set_tenant_id(_resolve_tenant_id)
            browser_tool = self.tool_registry.get_tool("browser_automation")
            if browser_tool and hasattr(browser_tool, 'set_tenant_id'):
                browser_tool.set_tenant_id(_resolve_tenant_id)

        # 设置工具执行上下文（session_id / channel / subagent_id / chat_record_id）
        # tenant_id / user_id 已由 src.saas.context 提供（HTTP 中间件设置）
        # 设计文档 docs/system/work-outcome-record-design.md §5.3
        try:
            from src.tools._helpers import set_tool_execution_context
            _subagent_dir = (
                self.subagent_config.dir_name
                if self.subagent_config and getattr(self.subagent_config, 'dir_name', None)
                else None
            )
            set_tool_execution_context(
                session_id=session_id,
                channel=None,  # Phase 1 暂为 None，复盘任务从 channel_sessions 表反查
                subagent_id=_subagent_dir,
                chat_record_id=None,  # Phase 1 暂为 None，后续阶段补
            )
        except Exception as e:
            logger.debug(f"set_tool_execution_context 失败（不影响主流程）: {e}")

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
                        # 工作目录必须落在租户存储目录下，避免在项目根目录产生 skill_ws_* 散落目录
                        ws_tenant_id = self._get_effective_tenant_id()
                        ws_root = None
                        if ws_tenant_id:
                            from src.core.storage import ensure_tenant_storage_dir
                            ws_root = os.path.abspath(ensure_tenant_storage_dir(ws_tenant_id, "temp"))
                        session_workspace = Path(tempfile.mkdtemp(prefix=f"skill_ws_{session_id}_", dir=ws_root))
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
        
        if not _continuation_tool_result:
            self.memory.add(session_id, "user", enhanced_input)

        # 检测用户"记住"意图，写入长期记忆
        await self._handle_remember_intent(user_input, user)

        messages = self._build_messages(session_id)
        system_prompt = self._build_system_prompt(user, extra_system_prompt=extra_system_prompt)

        # 视频创作参数注入上下文（video-agent 前端工具栏选择）。
        # 让 LLM 在对话轮次直接看到用户已确定的参数，避免重复询问时长/比例/模式。
        video_params = getattr(self, '_current_video_params', None)
        if video_params:
            messages.append({
                "role": "user",
                "content": self._format_video_params_for_llm(video_params),
            })
            logger.info(f"[video_params] 注入视频创作参数到上下文: {video_params}")
        else:
            # 临时调试：video-agent 会话未收到 video_params（前端未传 / 链路断裂）时记录
            tlog(
                "视频创作",
                "[注入检查] 当前无 video_params（mode 未注入），subagent={sub}, is_master={m}",
                sub=getattr(getattr(self, "subagent_config", None), "dir_name", None),
                m=self.is_master,
            )

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

        # 记录本轮开始时 messages 的长度，用于末尾收集本轮新增的 tool 消息序列。
        # continuation 不制造新的 user 消息，只把恢复结果接回原 tool_call_id。
        initial_len = len(messages)
        if _continuation_tool_result:
            continuation_tool_call_id = str(
                _continuation_tool_result.get("tool_call_id") or ""
            )
            if not continuation_tool_call_id:
                raise RuntimeError("CONTINUATION_TOOL_CALL_REQUIRED")
            unresolved = False
            for index in range(len(messages) - 1, -1, -1):
                message = messages[index]
                if (
                    message.get("role") == "tool"
                    and message.get("tool_call_id") == continuation_tool_call_id
                ):
                    break
                if message.get("role") == "assistant":
                    unresolved = any(
                        call.get("id") == continuation_tool_call_id
                        for call in message.get("tool_calls", [])
                    )
                    break
            if not unresolved:
                raise RuntimeError("CONTINUATION_CONTEXT_LOST")
            tool_message = {
                "role": "tool",
                "tool_call_id": continuation_tool_call_id,
                "content": _continuation_tool_result.get("content"),
            }
            messages.append(tool_message)
            self.memory.add_message(session_id, tool_message)

        max_iterations = 20  # Prevent infinite loops
        iteration = 0
        # Phase 2 P2.3：累积所有工具返回的 ImageRef，在工具调用结束后、最终回复生成前
        # 统一推送一次 images SSE 事件（避免事件流太碎）
        collected_images: List[Dict[str, Any]] = []

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
                logger.error(f"[AGENT] LLM call FAILED, session_id={session_id}, iteration={iteration}, duration={llm_call_duration:.2f}s, error: {type(e).__name__}", exc_info=True)
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

            # v3.1 Phase 4: 更新 session 上下文 token 缓存
            # 循环内每次 LLM 调用后写入，最后一次写入获胜（PG 行级锁 + session_queue 串行化保证不冲突）。
            # 设计文档 §4.4：取最后一次 prompt+completion，而非累加（避免重复计算历史）。
            try:
                _last_usage = response.get("usage", {}) or {}
                _prompt_tok = int(_last_usage.get("prompt_tokens", 0) or 0)
                _completion_tok = int(_last_usage.get("completion_tokens", 0) or 0)
                _total_tok = _prompt_tok + _completion_tok
                if _total_tok > 0 and session_id:
                    _src_type = self._detect_source_type()
                    if _src_type == "chat":
                        from src.db.models import SessionDB
                        SessionDB.update_context_token_count(session_id, _total_tok)
                    else:
                        from src.channels.session import channel_session_manager
                        channel_session_manager.update_context_token_count(session_id, _total_tok)
            except Exception as _e:
                logger.debug(f"更新 session token 缓存失败: {_e}", exc_info=True)

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

                # 部分 OpenAI-compatible provider 可能省略 id；必须在保存
                # assistant(tool_calls) 前生成一次，并让执行/恢复共用同一 id。
                tool_call_id = tc.get("id") or f"call_{uuid.uuid4().hex}"
                tc["id"] = tool_call_id
                valid_tool_calls.append({
                    "id": tool_call_id,
                    "name": tool_name,
                    "arguments": tool_args
                })
            
            # If no valid tool calls, we're done
            if not valid_tool_calls:
                # 临时调试：视频会话 LLM 未调用任何工具直接输出文本时记录（排查"无后续"问题）
                if getattr(self, '_current_video_params', None):
                    tlog(
                        "视频创作",
                        "[循环结束] LLM 无工具调用直接输出文本, iteration={i}, content={c}",
                        i=iteration,
                        c=(content or "")[:120],
                    )
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

                # Yield the final response — 只发 content，绝不把 reasoning_content
                # 暴露给用户（reasoning 是 LLM 内部思考，不属于用户可见回答）
                if not content and reasoning:
                    logger.warning(
                        f"[AGENT] LLM returned empty content with non-empty reasoning_content, "
                        f"session_id={session_id}, iteration={iteration}, "
                        f"reasoning_len={len(reasoning)}"
                    )
                if content:
                    yield make_event("response", data=content)
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
            for tool_index, tc in enumerate(valid_tool_calls):
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

                if tool_name == "browser_automation":
                    logger.info("Executing tool: browser_automation (arguments redacted)")
                else:
                    logger.info(f"Executing tool: {tool_name} with args: {json.dumps(tool_args, ensure_ascii=False)}")

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

                    # 发送工具执行结果
                    yield make_event("tool_result", toolName=tool_name, result=skill_result, success=skill_result.get("success", True))
                    yield make_event("progress", data=f"📦 已加载技能: {skill_name}")
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": skill_result
                    })
                    continue

                # skill_complete 已废弃（2026-07-14 移除）
                # 仍可能被历史会话上下文或模型记忆触发，静默吞掉避免污染
                if tool_name == "skill_complete":
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": {"success": True, "message": "skill_complete 已废弃，无需调用"}
                    })
                    yield make_event("tool_result", toolName=tool_name,
                                     result={"success": True, "message": "skill_complete 已废弃"},
                                     success=True)
                    continue

                # Handle skill_execute - execute command directly
                if tool_name == "skill_execute":
                    skill_name = tool_args.get("skill", "")
                    command = tool_args.get("command", "") or None  # 空字符串转为 None
                    files = tool_args.get("files", {})
                    content = tool_args.get("content")

                    # ⚠️ Skill 版本校验拦截：执行脚本前，必须确认本会话上下文中
                    # 该 skill 最近一次 use_skill 返回的 skill_version 等于当前 registry 版本。
                    # 不一致 / 历史无记录 / 历史返回缺 skill_version 字段 → 拒绝执行，
                    # 要求 LLM 先重新 use_skill 加载最新指南。
                    version_block = self._check_skill_version_consistency(session_id, skill_name)
                    if version_block:
                        skill_exec_result = version_block
                        yield make_event("tool_result", toolName=tool_name, result=skill_exec_result, success=False)
                        tool_results.append({
                            "tool_call_id": tool_id,
                            "content": skill_exec_result
                        })
                        continue

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
                    image_paths = tool_args.get("image_paths")

                    logger.info(f"Delegating to subagent: {subagent_name}, task: {task_description[:50]}...")
                    if image_paths:
                        logger.info(f"[DELEGATE] image_paths from LLM: {image_paths}")
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
                        image_paths=image_paths,
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
                    _exec_tool = self.tool_registry.get_tool(tool_name)
                    if _exec_tool is not None and getattr(_exec_tool, "execution_target", None) == ExecutionTarget.LOCAL_REQUIRED:
                        # 本地工具：注入受信身份 + 进度队列，流式转发本机执行进度
                        result = {"success": False, "error": "本地工具未返回结果"}
                        async for _evt_kind, _evt_payload in self._run_local_required_tool(
                            tool_name, tool_args, _resolve_tenant_id,
                            user.user_id if user else None, cancel_check,
                        ):
                            if _evt_kind == "progress":
                                yield make_event("progress", data=_evt_payload)
                            else:
                                result = _evt_payload
                    else:
                        execution_args = tool_args
                        if tool_name == "browser_automation":
                            execution_args = dict(tool_args)
                            execution_args["_audit_session_id"] = session_id
                            execution_args["_trusted_tenant_id"] = _resolve_tenant_id
                            execution_args["_trusted_user_id"] = user.user_id if user else None
                            execution_args["_agent_execution_id"] = f"ae_{uuid.uuid4().hex}"
                            execution_args["_tool_call_id"] = tool_id
                        # 注入视频创作参数（前端工具栏选择，供 submit_video_task 等工具读取）
                        video_params = getattr(self, '_current_video_params', None)
                        if video_params:
                            if execution_args is tool_args:
                                execution_args = dict(tool_args)
                            execution_args["_video_params"] = video_params
                        result = await self.tool_executor.execute(tool_name, execution_args)
                    logger.info(f"[TOOL_RESULT] {tool_name}: type={type(result).__name__}")

                    from src.core.tool_suspension import ToolSuspension
                    if isinstance(result, ToolSuspension):
                        # 一等控制结果：当前调用暂不写 role=tool、不完成计划、不继续 LLM。
                        from src.tools.browser.resume_store import ResumeStore
                        resume_store = ResumeStore()
                        assistance = await resume_store.get_assistance(
                            result.tenant_id, result.assistance_id
                        )
                        bound = assistance is not None and await resume_store.bind_agent(
                            assistance,
                            self.subagent_config.dir_name
                            if self.subagent_config else None,
                        )
                        if not bound:
                            raise RuntimeError("TOOL_SUSPEND_FAILED")
                        # tool_results 要到整轮工具全部执行后才会统一接入 messages；
                        # 挂起会提前 return，因此先保存已经执行过的兄弟调用，并为
                        # 尚未执行的调用写显式 deferred 结果，保证 assistant 的每个
                        # tool_call 都有且仅有一个配对结果。当前浏览器调用仍保持未解，
                        # 后台 continuation 会用原 tool_call_id 注入真实结果。
                        _preserve_suspension_sibling_results(
                            messages,
                            self.memory,
                            session_id,
                            tool_results,
                            valid_tool_calls[tool_index + 1:],
                        )
                        suspended_messages = []
                        for message in messages[initial_len:]:
                            if message.get("role") == "assistant" and message.get("tool_calls"):
                                suspended_messages.append({
                                    "role": "assistant",
                                    "content": message.get("content", ""),
                                    "tool_calls": message["tool_calls"],
                                })
                            elif message.get("role") == "tool":
                                suspended_messages.append({
                                    "role": "tool",
                                    "tool_call_id": message["tool_call_id"],
                                    "content": message["content"],
                                })
                        if suspended_messages:
                            yield make_event(
                                "tool_messages", messages=suspended_messages,
                                suspended=True,
                            )
                        yield result.event
                        yield make_event(
                            "progress", data="等待你的操作；完成后系统会自动继续"
                        )
                        for var_name in _injected_env_vars:
                            os.environ.pop(var_name, None)
                        return

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

            # Phase 2 P2.3：扫描本轮工具结果，提取 ImageRef dict
            # 在工具调用结束、进入最终回复生成之前，统一 yield 一次 images 事件
            # （计划要求：避免每个工具后单独推导致事件流太碎）
            _round_image_refs: List[Dict[str, Any]] = []
            for _tr in tool_results:
                _content = _tr.get("content")
                if isinstance(_content, dict):
                    _round_image_refs.extend(_extract_image_refs_from_tool_result(_content))
            if _round_image_refs:
                collected_images.extend(_round_image_refs)
                try:
                    _normalize_image_placement(_round_image_refs)
                    yield make_image_event(_round_image_refs, placement="after_text")
                except Exception as _img_e:
                    logger.warning(f"[AGENT] 推送 images SSE 事件失败: {_img_e}", exc_info=True)

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

    async def continue_tool_call(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        result: Dict[str, Any],
        user: Optional[User] = None,
    ) -> AsyncGenerator[dict, None]:
        """从已持久化的 assistant(tool_calls) 接回一次工具结果并继续 LLM。"""
        async for event in self.process_message(
            user_input="",
            session_id=session_id,
            user=user,
            _continuation_tool_result={
                "tool_call_id": tool_call_id,
                "content": result,
            },
        ):
            yield event
    
    async def process_message_sync(
        self,
        user_input: str,
        session_id: str,
        user: Optional[User] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        record_service=None,
        progress_callback=None,
        cancel_check=None,
        extra_system_prompt: Optional[str] = None,
    ) -> str:
        """Process message and return complete response

        Args:
            record_service: Optional SessionRecordService for token tracking.
                When provided (e.g. from channel routes running in asyncio),
                the agent accumulates token usage to this service instead of
                relying on thread-local SessionRecordManager.
            progress_callback: Optional async callback for progress events
                (tool_start, tool_result, etc.)
            cancel_check: Optional callable returning True to cancel processing.
                Used by channel message serialization to cancel stale requests.
            extra_system_prompt: 渠道级额外提示词（如 wecom_kf 的渠道能力约束），
                透传给 process_message → _build_system_prompt。
        """
        # Store explicit record_service so the inner process_message()
        # can access it without relying on thread-local storage
        self._explicit_record_service = record_service
        # Phase 2 P2.3 CodeReview P0 修复：每次调用前清空，供渠道层读取
        # （process_message 内部的 collected_images 是局部变量，外部无法访问；
        #  通过 images 事件 + 实例属性桥接，让 channels/session.process_and_persist
        #  能拿到 ImageRef 列表写入 UnifiedResponse.content.images）
        self._last_response_images: List[Dict[str, Any]] = []
        logger.info(f"[DEBUG] Agent.process_message_sync: user_input={user_input!r}, attachments={attachments}, session_id={session_id}")
        try:
            response_parts = []
            async for event in self.process_message(
                user_input, session_id, user, attachments,
                cancel_check=cancel_check,
                extra_system_prompt=extra_system_prompt,
            ):
                if event.get("type") == "response":
                    response_parts.append(event.get("data", ""))
                # Phase 2 P2.3 CodeReview P0 修复：累积 images 事件到实例属性
                # 供渠道层（process_and_persist）读取后写入 UnifiedResponse.content.images
                if event.get("type") == "images":
                    ev_images = event.get("images") or []
                    if isinstance(ev_images, list):
                        for img in ev_images:
                            if isinstance(img, dict) and img.get("file_id"):
                                # 按 file_id 去重
                                if not any(existing.get("file_id") == img["file_id"]
                                            for existing in self._last_response_images):
                                    self._last_response_images.append(img)
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
        image_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        作为子智能体执行任务

        Args:
            task_description: 任务描述
            parent_session_id: 父智能体的session ID
            task_record: 任务记录（用于状态更新）
            progress_callback: 进度回调函数（保留兼容，内部收集事件并转发）
            image_paths: 用户上传图片的完整路径列表（可选，多模态子智能体如 video-agent 用）
                传入时构造 OpenAI 多模态 content（text + image_url data:base64），
                直接传给 LLM；不持久化到 memory/chat_messages 表。

        Returns:
            执行结果
        """
        if self.mode != AgentMode.SUBAGENT:
            raise RuntimeError("execute_as_subagent() is only for subagent mode")

        # 按需加载租户自定义 skills
        self._ensure_tenant_skills_loaded()

        # 注入 tenant_id 到需要租户隔离的工具（子智能体线程中 ContextVar 不可用）
        if self._init_tenant_id:
            for tool_name in (
                "attraction_search", "hotel_search", "knowledge_base_search", "browser_automation"
            ):
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

        # 设置工具执行上下文（子智能体场景）
        # 子智能体走 execute_as_subagent 而非 process_message，需要在入口补设
        # ContextVar，否则 cp 工具登记工作成果时 subagent_id 会丢失
        # （asyncio.create_task 复制主智能体 context，subagent_id=None 会被继承）
        # 设计文档 docs/system/work-outcome-record-design.md §5.3
        try:
            from src.tools._helpers import set_tool_execution_context
            _subagent_dir = (
                self.subagent_config.dir_name
                if self.subagent_config and getattr(self.subagent_config, 'dir_name', None)
                else None
            )
            set_tool_execution_context(
                session_id=self.session_id or parent_session_id,
                channel=None,  # Phase 1 暂为 None
                subagent_id=_subagent_dir,
                chat_record_id=None,
            )
        except Exception as e:
            logger.debug(f"set_tool_execution_context (subagent) 失败（不影响主流程）: {e}")

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
            
            # 添加任务描述（若提供 image_paths，构造 OpenAI 多模态 content）
            user_text = timestamp_context + task_description
            multimodal_parts = self._build_multimodal_user_content(user_text, image_paths)
            if multimodal_parts is not None:
                messages.append({"role": "user", "content": multimodal_parts})
                logger.info(
                    f"[SUBAGENT] multimodal messages built, "
                    f"{len(multimodal_parts) - 1} images attached"
                )
            else:
                messages.append({"role": "user", "content": user_text})
            
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
                        # 发送工具执行结果
                        await _emit_async(make_event("tool_result", toolName=tool_name, result=skill_result, success=skill_result.get("success", True)))
                    # skill_complete 已废弃（2026-07-14 移除），静默吞掉避免污染
                    elif tool_name == "skill_complete":
                        tool_result = {"success": True, "message": "skill_complete 已废弃，无需调用"}
                        await _emit_async(make_event("tool_result", toolName=tool_name,
                                                     result=tool_result, success=True))
                    elif tool_name == "skill_execute":
                        skill_name = tool_args.get("skill", "")
                        command = tool_args.get("command", "") or None  # 空字符串转为 None
                        files = tool_args.get("files", {})
                        content = tool_args.get("content")

                        # ⚠️ Skill 版本校验拦截（与主循环一致）
                        version_block = self._check_skill_version_consistency(self.session_id, skill_name)
                        if version_block:
                            tool_result = version_block
                            await _emit_async(make_event("tool_result", toolName=tool_name, result=tool_result, success=False))
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": json.dumps(tool_result, ensure_ascii=False)
                            })
                            continue
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
                            _exec_tool = self.tool_registry.get_tool(tool_name)
                            if _exec_tool is not None and getattr(_exec_tool, "execution_target", None) == ExecutionTarget.LOCAL_REQUIRED:
                                # 本地工具：注入受信身份 + 进度队列，流式转发本机执行进度
                                result = {"success": False, "error": "本地工具未返回结果"}
                                async for _evt_kind, _evt_payload in self._run_local_required_tool(
                                    tool_name, tool_args,
                                    self._init_tenant_id, self._init_user_id,
                                ):
                                    if _evt_kind == "progress":
                                        await _emit_async(make_event("progress", data=_evt_payload))
                                    else:
                                        result = _evt_payload
                            else:
                                execution_args = tool_args
                                if tool_name == "browser_automation":
                                    execution_args = dict(tool_args)
                                    execution_args["_audit_session_id"] = parent_session_id
                                    execution_args["_trusted_tenant_id"] = self._init_tenant_id
                                    execution_args["_trusted_user_id"] = self._init_user_id
                                # 注入视频创作参数（前端工具栏选择，供 submit_video_task 等工具读取）
                                video_params = getattr(self, '_current_video_params', None)
                                if video_params:
                                    if execution_args is tool_args:
                                        execution_args = dict(tool_args)
                                    execution_args["_video_params"] = video_params
                                result = await self.tool_executor.execute(tool_name, execution_args)
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


# master_agent 单例：延迟构造
# Agent 构造时会注册全部工具、加载 skills 和 subagents，开销很大；放在模块顶层
# 会让任何 import src.core.* 的代码被迫拉起整套环境。改为按需构造。
_master_agent_instance: Optional["Agent"] = None


def get_master_agent() -> "Agent":
    """返回 master_agent 单例，第一次调用时构造"""
    global _master_agent_instance
    if _master_agent_instance is None:
        _master_agent_instance = Agent(is_master=True)
    return _master_agent_instance


def __getattr__(name: str):
    """模块级 __getattr__：让 `from src.core.agent import master_agent` 仍能工作，
    但只有在真正访问时才构造单例。"""
    if name in ("master_agent", "agent"):
        return get_master_agent()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# 兼容直接属性访问（极少数场景：src.core.agent.master_agent）
# 注意：不能写 `master_agent = get_master_agent()`，会立刻触发构造。
