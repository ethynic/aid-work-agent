"""Resolve per-call skill configuration without changing the parent environment."""

import os
import re
from pathlib import Path
from dotenv import dotenv_values


def tenant_skill_env_path(tenant_id, skill_name):
    from src.core.storage import get_tenant_storage_dir
    return Path(get_tenant_storage_dir(tenant_id, "skills")) / skill_name / ".env"


def _dotenv(path, inherited):
    if not path.is_file():
        return {}
    parsed = dotenv_values(path, interpolate=False)
    resolved = {}
    for name, value in parsed.items():
        if value is None:
            continue
        lookup = {**resolved, **inherited}
        resolved[name] = re.sub(r"\$\{([^}:]+)(?::-([^}]*))?\}",
            lambda match: lookup.get(match.group(1), match.group(2) or ""), value)
    return resolved


def base_environment():
    process = dict(os.environ)
    project = _dotenv(Path(__file__).resolve().parents[2] / ".env", process)
    return {**project, **process}


def bind_identity(environment, context=None, tenant_id=None):
    # Identity-bearing names never come from a skill/tenant .env file.
    names = ("CURRENT_TENANT_ID", "CURRENT_USER_ID", "CURRENT_SESSION_ID",
             "AID_TENANT_ID", "AID_USER_ID", "AID_SESSION_ID", "AID_SUBAGENT_ID",
             "AID_RUNNER_ID","AID_RUNNER_WORKER_ID","AID_RUNNER_ATTEMPT",
             "AID_RUNNER_EXECUTION_ID","AID_RUNNER_FAILURE_FILE","AID_RUNNER_PARENT_PGID","AID_RUNNER_PROCESS_FILE",
             "AID_RUNNER_TOOL_CALL_ID")
    for name in names:
        environment.pop(name, None)
    tenant = context.tenant_id if context is not None else tenant_id
    if context is not None and tenant_id is not None and tenant_id != tenant:
        raise ValueError("SKILL_CONTEXT_TENANT_MISMATCH")
    for name in ("CURRENT_TENANT_ID", "AID_TENANT_ID"):
        if tenant:
            environment[name] = tenant
    if context is not None:
        for field, names in (("user_id", ("AID_USER_ID", "CURRENT_USER_ID")),
                             ("session_id", ("AID_SESSION_ID", "CURRENT_SESSION_ID")),
                             ("subagent_id", ("AID_SUBAGENT_ID",))):
            value = getattr(context, field, None)
            if value:
                for name in names:
                    environment[name] = value
    return environment


def resolve_skill_environment(skill, *, tenant_id=None, env_vars=None, context=None):
    tenant = context.tenant_id if context is not None else tenant_id
    if context is not None and tenant_id is not None and tenant_id != tenant:
        raise ValueError("SKILL_CONTEXT_TENANT_MISMATCH")
    inherited = bind_identity(base_environment(), context, tenant_id)
    defaults = {item["name"]: str(item["default"]) for item in skill.env
                if item.get("name") and item.get("default") is not None}
    interpolation = bind_identity({**inherited, **dict(env_vars or {}),
        **(dict(context.env_vars) if context is not None else {})}, context, tenant_id)
    skill_values = _dotenv(skill.dir / ".env", {**defaults, **interpolation})
    tenant_values = _dotenv(tenant_skill_env_path(tenant, skill.name),
                            {**defaults, **skill_values, **interpolation}) if tenant else {}
    # Existing daemon configuration has precedence over skill defaults. Tenant
    # configuration overrides the shared skill file; request configuration wins.
    environment = {**defaults, **skill_values, **tenant_values, **inherited,
                   **dict(env_vars or {})}
    if context is not None:
        environment.update(dict(context.env_vars))
    bind_identity(environment,context,tenant_id)
    from src.llm.call_observer import prepare_child_environment
    prepare_child_environment(environment,context)
    return environment
