from __future__ import annotations
from typing import Optional
from loguru import logger
from src.config.settings import settings
from src.core.agent_engine.contracts import AgentMode
from src.models.user import User

from src.core.agent_engine.context import render_prompt
class PromptSources:
    def __init__(self, *, mode, subagent_config, tenant_id, skill_registry,
                 style_manager, prompt_manager, tool_controls, subagent_registry,
                 tools, available_subagents, usage_guides):
        self.mode, self.subagent_config, self._init_tenant_id = mode, subagent_config, tenant_id
        self.skill_registry, self.style_manager, self.prompt_manager = skill_registry, style_manager, prompt_manager
        self._tool_controls, self.subagent_registry = tool_controls, subagent_registry
        self._get_tools, self._get_available_subagents = tools, available_subagents
        self._collect_tool_usage_guides = usage_guides
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
            for agent_id in available_subagents:
                config = self.subagent_registry._configs.get(agent_id)
                if config:
                    lines.append(f"- {config.name}（ID: {agent_id}）: {config.description}")
            subagent_descriptions = "\n".join(lines) if lines else "(no subagents available)"
        
        # 委派工具说明（仅主智能体使用）
        delegation_guide = ""
        subagent_matching_hint = ""

        if include_delegation and available_subagents:
            # 从 DelegateToSubagentTool 动态获取使用指南
            delegate_tool = self._tool_controls.get("delegate_to_subagent")
            if delegate_tool:
                delegation_guide = delegate_tool.get_usage_guide(subagent_descriptions=subagent_descriptions)
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

        return render_prompt(self.prompt_manager.load_template(template_name), variables)

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

                # 注入租户级知识库约束（区分栏目授权语义，2026-09-20 设计 §4.5）
                ks = self._load_knowledge_sources()
                if ks:
                    # 自有项判定与 tenant_range.load_authorized_source_types 保持一致
                    # （剔除空 source_type），避免软引导与硬约束不一致
                    owned = [
                        s for s in ks
                        if not s.get('owner_tenant_id') and (s.get('source_type') or '').strip()
                    ]
                    shared = [s for s in ks if s.get('owner_tenant_id')]
                    lines = ["", "## 可用知识库", ""]
                    if owned:
                        lines.append("你只能检索以下知识库栏目（其余栏目未授权，检索会被拒绝）：")
                    else:
                        lines.append("你可以检索本租户全部知识库栏目（未配置栏目授权）。")
                    for src in owned:
                        st = src.get('source_type', '')
                        dn = src.get('display_name', st)
                        lines.append(f"- {st}（{dn}）")
                    if shared:
                        lines.append("以下为跨租户共享的知识库栏目（不受栏目授权限制）：")
                        for src in shared:
                            st = src.get('source_type', '')
                            dn = src.get('display_name', st)
                            owner_company = src.get('owner_company_name')
                            if owner_company:
                                # 共享来源：标注来源公司，LLM 感知内容归属
                                lines.append(f"- {st}（{owner_company} · {dn}）")
                            else:
                                lines.append(f"- {st}（{dn}）")
                    if owned:
                        lines.append("调用时必须传入正确的 source_type 参数。")
                    subagent_constraint += "\n".join(lines)

            # 加载租户定制 extra.md
            extra_content = self._load_extra_md()
            if extra_content:
                subagent_constraint = subagent_constraint + "\n\n## 租户定制需求\n\n" + extra_content

            # 加载租户模板文件，在提示词末尾注入模板 file_id（工具自动解析，勿拼路径）
            templates = self._load_template_files()
            if templates:
                tpl_lines = [
                    "\n\n### 可用模板文件",
                    "",
                    "以下为模板的 file_id。调用 excel_process / word / pdf_process 等文档工具时，"
                    "将 file_id 原样放入 file_paths 参数即可，工具会自动解析为真实路径，"
                    "禁止自行拼接 storage/uploads 等目录路径。",
                    "",
                ]
                for t in templates:
                    tpl_lines.append(f"- {t.get('name', '')}：{t.get('file_id', '')}")
                subagent_constraint += "\n".join(tpl_lines)

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

    def _load_template_files(self) -> list:
        """加载租户为该子智能体配置的模板文件列表。

        从 subagent_template_files 表读取（per tenant+subagent），返回 [{name, file_id, ...}]。
        用于在 system prompt 末尾注入「### 可用模板文件」，模板以 file_id 标识并附用法说明，
        数字员工据此将 file_id 原样传入文档工具（word/excel/pdf_process 等）的 file_paths 套用模板，
        无需自行拼接路径。
        """
        if not self.subagent_config:
            return []

        tenant_id = self._init_tenant_id

        if not tenant_id or not self.subagent_config.dir_name:
            return []

        try:
            from src.db.subagent_template_file_db import SubagentTemplateFileDB
            files = SubagentTemplateFileDB.get(tenant_id, self.subagent_config.dir_name)
            if files:
                logger.debug(
                    f"Loaded template files (DB) for tenant {tenant_id}, "
                    f"subagent {self.subagent_config.dir_name}: {len(files)} 个"
                )
            return files or []
        except Exception as e:
            logger.warning(f"Failed to load template files from DB: {e}")
            return []

    def _load_knowledge_sources(self) -> list:
        """加载租户级子智能体知识库关联

        对共享来源项（owner_tenant_id 非空且非本租户）附加 owner_company_name，
        供注入提示词时标注来源公司。
        """
        if not self.subagent_config:
            return []

        tenant_id = self._init_tenant_id

        if not tenant_id:
            return []

        subagent_name = self.subagent_config.dir_name
        if not subagent_name:
            return []

        try:
            from src.db.subagent_knowledge_source_db import SubagentKnowledgeSourceDB
            sources = SubagentKnowledgeSourceDB.get(tenant_id, subagent_name)
            if sources:
                for s in sources:
                    owner = s.get("owner_tenant_id")
                    if owner and owner != tenant_id:
                        s["owner_company_name"] = self._tenant_company_name(owner)
                logger.debug(f"Loaded {len(sources)} knowledge sources for tenant {tenant_id}, subagent {subagent_name}")
            return sources
        except Exception as e:
            logger.warning(f"Failed to load knowledge sources: {e}")
            return []

    @staticmethod
    def _tenant_company_name(tenant_id: str) -> str:
        """查询租户公司名；失败时回退为租户 ID。"""
        try:
            from src.saas.db.tenant_db import TenantDB
            tenant = TenantDB.get_by_id(tenant_id)
            if tenant:
                name = tenant.get("company_name")
                if name:
                    return name
        except Exception as e:
            logger.warning(f"查询共享来源租户公司名失败: {e}")
        return tenant_id

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
        return tenant_id

