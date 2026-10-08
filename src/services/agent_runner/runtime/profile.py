"""Bootstrap resources for one execution; no shared request identity or scratch state."""

from pathlib import Path
import hashlib
import json
import uuid
from src.config.settings import settings
from src.core.agent_engine.contracts import AgentMode
from src.core.plan_manager import PlanManager
from src.core.skill_executor import SkillExecutor
from src.core.skill_registry import SkillRegistry
from src.llm.gateway import llm_gateway
from src.memory.manager import MemoryManager
from src.prompts import PromptManager
from src.prompts.style_manager import get_style_manager
from src.services.agent_runner.runtime.resource_cache import (
    cached_builtin_subagent_registry, cached_skill_registry)
from src.tools.assembly import ToolAssemblyRequest, ToolAssemblyRole, assemble_agent_tools
from src.tools.executor import ToolExecutor


class RuntimeResources:
    def __init__(self, is_master=True, subagent_config=None, session_id=None,
                 execution_id=None, parent_plan_manager=None, mode=AgentMode.MASTER,
                 tenant_id=None, user_id=None, resource_directory=None, isolate_plan=False, session_kind='web',
                 plan_store=None, plan_scope=None):
        self.mode = AgentMode.SUBAGENT if mode == AgentMode.MASTER and not is_master else mode
        self.is_master = self.mode == AgentMode.MASTER
        self.subagent_config = subagent_config
        self.session_id = session_id
        self.execution_id = execution_id or f"ae_{uuid.uuid4().hex}"
        self.parent_plan_manager = parent_plan_manager
        if subagent_config and getattr(subagent_config, "llm_provider", None):
            from src.llm.gateway import LLMGateway
            self.llm = LLMGateway(provider_name=subagent_config.llm_provider,
                model_codes=getattr(subagent_config, "llm_model_codes", None))
        else:
            self.llm = llm_gateway
        self.prompt_manager, self.style_manager = PromptManager(), get_style_manager()
        self.memory = MemoryManager(max_short_term_messages=settings.memory.short_term.max_messages,
                                    short_term_ttl=settings.memory.short_term.ttl)
        root = Path(__file__).resolve().parents[4]
        allowed = settings.skills.master_agent.allowed or None if self.is_master else None
        if not self.is_master and subagent_config:
            allowed = subagent_config.get_allowed_skills() or None
        self.skill_registry = cached_skill_registry(allowed)
        self.skill_executor = SkillExecutor(self.skill_registry)
        self.resource_directory = Path(resource_directory) if resource_directory is not None else root
        self.isolate_plan = isolate_plan
        plan_directory = self.resource_directory / "plans"
        if plan_scope is None and (self.mode == AgentMode.SUBAGENT or isolate_plan):
            parts = [tenant_id,session_kind,session_id]
            if self.mode == AgentMode.SUBAGENT:
                parts.append(self.execution_id)
            plan_scope = json.dumps(parts,separators=(',',':'))
        if plan_scope is not None:
            namespace = 'children' if self.mode == AgentMode.SUBAGENT else 'conversations'
            plan_directory = plan_directory / namespace / hashlib.sha256(plan_scope.encode()).hexdigest()
        self.plan_manager = PlanManager(plan_directory, execution_scope=plan_scope, plan_store=plan_store)
        self.subagent_registry = None
        self.subagent_executor = None
        if self.is_master:
            self.subagent_registry = cached_builtin_subagent_registry(root / "subagents")
        self._tool_bundle = assemble_agent_tools(ToolAssemblyRequest(
            role=ToolAssemblyRole(self.mode.value), subagent_config=subagent_config,
            plan_manager=self.plan_manager, skill_registry=self.skill_registry,
            skill_executor=self.skill_executor, subagent_registry=self.subagent_registry))
        self.tool_registry, self._tool_controls = self._tool_bundle.registry, self._tool_bundle.controls
        self.tool_executor = ToolExecutor(self.tool_registry)
        if self.is_master:
            from src.subagents.executor import SubagentExecutor
            self.subagent_executor = SubagentExecutor(self.memory, self.subagent_registry,
                self.tool_registry, self.skill_registry, parent_plan_manager=self.plan_manager)
            self._tool_controls.bind_delegate(subagent_registry=self.subagent_registry,
                                             subagent_executor=self.subagent_executor)
