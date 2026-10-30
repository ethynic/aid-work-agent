"""Agent 控制工具的构造、元数据和晚绑定。"""

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from src.tools.base import BaseTool


@dataclass(frozen=True)
class ControlToolDependencies:
    plan_manager: Any
    skill_registry: Any
    skill_executor: Any
    tool_registry: Any
    subagent_config: Any = None
    subagent_registry: Any = None
    delegation_authorizer: Any = None
    allow_delegate: bool = False
    # 非交互执行（scheduled 根+子）：装配时滤除 interactive_only 提问类
    # 控制工具（ask_user/clarify）——definitions 与 dispatch（controls.get）
    # 同一过滤点，LLM 不可见即不可调用（设计 §5.3）。
    non_interactive: bool = False


class ToolControlSet:
    def __init__(self, dependencies: ControlToolDependencies):
        from src.tools.agent.ask_user_tool import AskUserTool
        from src.tools.agent.clarify_tool import ClarifyTool
        from src.tools.agent.present_options_tool import PresentOptionsTool
        from src.tools.agent.sleep_tool import AgentSleepTool
        from src.tools.plan.create_plan_tool import CreatePlanTool
        from src.tools.skill.skill_execute_tool import SkillExecuteTool
        from src.tools.skill.use_skill_tool import UseSkillTool

        self._dependencies = dependencies
        skill_llm_env = {}
        config = dependencies.subagent_config
        if config and getattr(config, "llm_provider", None):
            skill_llm_env["SKILL_LLM_PROVIDER"] = config.llm_provider
            model = (getattr(config, "llm_model_codes", None) or {}).get(config.llm_provider)
            if model:
                skill_llm_env["SKILL_LLM_MODEL"] = model
        tools = (
            SkillExecuteTool(dependencies.skill_executor, dependencies.skill_registry, llm_env=skill_llm_env),
            CreatePlanTool(dependencies.plan_manager, dependencies.skill_registry,
                           dependencies.subagent_registry, dependencies.tool_registry),
            ClarifyTool(),
            AskUserTool(),
            PresentOptionsTool(),
            AgentSleepTool(),
            UseSkillTool(dependencies.skill_registry),
        )
        if dependencies.non_interactive:
            tools = tuple(tool for tool in tools if not getattr(tool, "interactive_only", False))
        self._tools = {tool.name: tool for tool in tools}
        self._delegate: Optional[BaseTool] = None

    def bind_delegate(self, *, subagent_registry, subagent_executor) -> None:
        if not self._dependencies.allow_delegate:
            raise RuntimeError("当前 Agent role 禁止绑定 delegate_to_subagent")
        if self._delegate is not None:
            raise RuntimeError("delegate_to_subagent 已绑定")
        from src.tools.agent.delegate_tool import DelegateToSubagentTool
        self._delegate = DelegateToSubagentTool(
            subagent_registry, subagent_executor,
            authorizer=self._dependencies.delegation_authorizer,
        )

    def get(self, name: str) -> Optional[BaseTool]:
        if name == "delegate_to_subagent":
            return self._delegate
        return self._tools.get(name)

    def definitions(self, *, available_subagents: Optional[Sequence[str]]) -> list[dict]:
        # 非交互装配下 clarify/ask_user 不在 _tools，按现存名称跳过（防 KeyError）
        definitions = [
            self._tools[name].to_tool_definition()
            for name in ("skill_execute", "create_plan", "clarify", "ask_user", "agent_sleep")
            if name in self._tools
        ]
        definitions.append(self._dependencies.skill_registry.get_skill_tool_definition())
        if self._delegate is not None:
            definition = self._delegate.subagent_registry.get_delegation_tool_definition(
                None if available_subagents is None else list(available_subagents)
            )
            if definition:
                definitions.append(definition)
        return definitions

    def usage_guides(
        self, *, subagent_descriptions: str = "", include_delegate: bool = False
    ) -> str:
        guides = []
        for name in ("skill_execute", "create_plan", "clarify", "ask_user"):
            if name not in self._tools:
                continue
            guide = self._tools[name].get_usage_guide()
            if guide:
                guides.append(f"### {name}\n{guide}")
        if include_delegate and self._delegate:
            guide = self._delegate.get_usage_guide(
                subagent_descriptions=subagent_descriptions
            )
            if guide:
                guides.append(f"### delegate_to_subagent\n{guide}")
        return "\n\n".join(guides)

    def get_display_name(self, name: str, args: dict) -> Optional[str]:
        if name == "use_skill":
            return f"加载技能「{args.get('skill', '')}」"
        if name == "delegate_to_subagent":
            return f"调用{args.get('subagent_name', '')}子智能体"
        tool = self.get(name)
        if tool:
            return tool.get_display_name(args or {})
        return None
