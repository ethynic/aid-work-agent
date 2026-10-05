"""Stable private execution workspace namespaces on the configured shared mount."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path


def workspace_directory(resource_directory, identity, execution_id):
    binding = json.dumps([asdict(identity), execution_id], sort_keys=True, separators=(',', ':'))
    namespace = hashlib.sha256(binding.encode()).hexdigest()
    return Path(resource_directory).resolve() / 'workspaces' / namespace


def validate_workspaces(resource_directory, state):
    expected = workspace_directory(resource_directory, state.identity, state.execution_id)
    paths = [state.resources.get('workspace'), *state.resources.get('reply_workspaces', [])]
    for value in paths:
        if not value:
            continue
        path = Path(value)
        if (not path.is_dir() or path.is_symlink() or path.resolve().parent != expected
                or not path.name.startswith('skill_ws_')):
            from .contracts import RunnerError
            raise RunnerError('RECOVERY_RESOURCE_SCOPE_INVALID', 409)


def cleanup_workspaces(resource_directory, identity, checkpoint):
    """Delete only proven execution-owned resources, after terminal commit."""
    import shutil
    from loguru import logger
    execution_id = checkpoint.get('execution_id')
    if not isinstance(execution_id,str) or not execution_id:
        return
    expected = workspace_directory(resource_directory,identity,execution_id)
    resources = checkpoint.get('resources') or {}
    for artifact in resources.get('image_artifacts') or []:
        # 引用产物与 workspace 同一 owner 命名空间：只删登记且包含性可证的文件。
        if not isinstance(artifact,dict):
            continue
        path = Path(str(artifact.get('path') or ''))
        if (not path.name.startswith('image_') or path.is_symlink()
                or path.resolve().parent!=expected
                or any(parent.is_symlink() for parent in path.parents)):
            logger.warning('AgentRunner resource cleanup skipped code=RESOURCE_SCOPE_INVALID')
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning('AgentRunner image artifact cleanup unavailable')
    for value in [resources.get('workspace'), *resources.get('reply_workspaces',[])]:
        if not value:
            continue
        path = Path(value)
        if (path.is_symlink() or path.resolve().parent!=expected
                or not path.name.startswith('skill_ws_')
                or any(parent.is_symlink() for parent in path.parents)):
            logger.warning('AgentRunner resource cleanup skipped code=RESOURCE_SCOPE_INVALID')
            continue
        if path.is_dir():
            shutil.rmtree(path)
