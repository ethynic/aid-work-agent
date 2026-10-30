"""按 Agent 角色装配普通工具与控制工具。"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from src.tools.control_set import ControlToolDependencies, ToolControlSet
from src.tools.delegation_policy import DelegationAuthorizer
from src.tools.registry import ToolRegistry, discover_tool_classes


class ToolAssemblyRole(str, Enum):
    MASTER = "master"
    SUBAGENT = "subagent"
    STANDALONE = "standalone"


@dataclass(frozen=True)
class ToolAssemblyRequest:
    role: ToolAssemblyRole
    subagent_config: Any
    plan_manager: Any
    skill_registry: Any
    skill_executor: Any
    subagent_registry: Any = None
    # 非交互执行（scheduled）：控制工具装配滤除 interactive_only 提问工具
    non_interactive: bool = False


@dataclass
class AgentToolBundle:
    registry: ToolRegistry
    controls: ToolControlSet
    discovered_names: tuple[str, ...]
    final_names: tuple[str, ...]


def _validate_request(request: ToolAssemblyRequest) -> None:
    if not isinstance(request.role, ToolAssemblyRole):
        raise ValueError(f"非法工具装配 role: {request.role!r}")
    missing = [
        name for name in ("plan_manager", "skill_registry", "skill_executor")
        if getattr(request, name) is None
    ]
    if missing:
        raise ValueError(
            f"role={request.role.value} 缺少工具装配依赖: {', '.join(missing)}"
        )
    if request.role is ToolAssemblyRole.MASTER and request.subagent_registry is None:
        raise ValueError("role=master 缺少 subagent_registry")
    if request.role is ToolAssemblyRole.SUBAGENT and request.subagent_config is None:
        raise ValueError("role=subagent 缺少 subagent_config")


def assemble_agent_tools(request: ToolAssemblyRequest) -> AgentToolBundle:
    _validate_request(request)
    registry = ToolRegistry()
    discovered = discover_tool_classes()
    for cls in sorted(
        discovered.values(),
        key=lambda tool_cls: (tool_cls.assembly_order, tool_cls.name),
    ):
        registry.register(cls())

    config = request.subagent_config
    from src.local_tools.catalog import TRUSTED_PROVIDERS, agent_tools, expand_agent_aliases
    if request.role is ToolAssemblyRole.MASTER or (config and config.tools.get('inherit', False)):
        business_names = {name for key in TRUSTED_PROVIDERS for name in agent_tools(key)}
        from src.local_tools.proxy_tool import LOCAL_PROXY_TOOL_CLASSES
        for cls in LOCAL_PROXY_TOOL_CLASSES:
            if cls.name in business_names and not registry.has_tool(cls.name):
                registry.register(cls())
    if request.role is not ToolAssemblyRole.MASTER and config is not None:
        allowed = expand_agent_aliases(config.get_allowed_tools())
        from src.local_tools.manifest import LOCAL_PROXY_TOOL_NAMES
        proxy_names = set(allowed) & LOCAL_PROXY_TOOL_NAMES
        if proxy_names:
            from src.local_tools.proxy_tool import LOCAL_PROXY_TOOL_CLASSES
            for cls in LOCAL_PROXY_TOOL_CLASSES:
                if cls.name in proxy_names and not registry.has_tool(cls.name):
                    registry.register(cls())

        excluded = expand_agent_aliases(config.get_excluded_tools())
        if config.tools.get("inherit", False):
            registry.remove_many(excluded)
        elif allowed:
            registry.retain_only(allowed)
            registry.remove_many(excluded)
        else:
            registry.clear()

    controls = ToolControlSet(ControlToolDependencies(
        plan_manager=request.plan_manager,
        skill_registry=request.skill_registry,
        skill_executor=request.skill_executor,
        tool_registry=registry,
        subagent_config=config,
        subagent_registry=request.subagent_registry,
        delegation_authorizer=DelegationAuthorizer(),
        allow_delegate=request.role is ToolAssemblyRole.MASTER,
        non_interactive=request.non_interactive,
    ))
    return AgentToolBundle(
        registry=registry,
        controls=controls,
        discovered_names=tuple(discovered),
        final_names=tuple(registry.list_tools()),
    )
