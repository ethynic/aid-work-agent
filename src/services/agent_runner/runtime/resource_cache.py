"""Process-level caches for configuration-shaped resources shared across executions.

Only immutable or version-checked definitions live here: file-based skills and
subagent builtins (both code-shipped, immutable per process) plus the DB
subagent overlay (refreshed only when its (count, max updated_at) signature
changes, so profile fingerprints stay consistent with the database across
processes). Per-execution mutable state — memory, plans, execution identity,
tenant request context — never enters this module; see the M0 isolation
contract in docs/plans/plan-agent-runner-service.md.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

_skill_lock = threading.Lock()
_skill_registries: dict = {}

_registry_lock = threading.Lock()
_subagent_registries: dict = {}
_subagent_overlay_registries: dict = {}
_db_signatures: dict = {}

_REPO_ROOT = Path(__file__).resolve().parents[4]


def cached_skill_registry(allowed):
    """File-based skill definitions are global and immutable; build once per allowed-set.

    load_from_directory also runs skill init DDL, so caching removes the
    per-execution table initialization against the database.
    """
    from src.core.skill_registry import SkillRegistry
    key = frozenset(allowed) if allowed else None
    with _skill_lock:
        registry = _skill_registries.get(key)
        if registry is None:
            registry = SkillRegistry()
            registry.load_from_directory(_REPO_ROOT / "src" / "skills", allowed=allowed)
            _skill_registries[key] = registry
        return registry


def cached_builtin_subagent_registry(subagents_dir):
    """Disk builtins only, preserving the RuntimeResources delegation surface."""
    key = str(Path(subagents_dir))
    with _registry_lock:
        registry = _subagent_registries.get(key)
        if registry is None:
            from src.subagents.registry import SubagentRegistry
            registry = SubagentRegistry(Path(subagents_dir))
            _subagent_registries[key] = registry
        return registry


def _read_db_signature():
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # prompt_versions/prompt_labels 也参与签名：load_from_db 会把解析后的
        # system_prompt 烤进 config 并计入 profile 指纹，签名不含它们时一次
        # prompt-only 发布可能让 API 与 worker 的指纹持久分叉。prompt_versions
        # 无 updated_at，用 (count, max(version)) 表达内容水位。
        cursor.execute(
            "SELECT (SELECT COUNT(*) FROM subagent_definitions WHERE status = 'active') AS def_count,"
            " (SELECT COALESCE(MAX(updated_at)::text, '') FROM subagent_definitions) AS def_updated,"
            " (SELECT COUNT(*) FROM prompt_versions) AS pv_count,"
            " (SELECT COALESCE(MAX(version)::text, '') FROM prompt_versions) AS pv_max,"
            " (SELECT COALESCE(MAX(updated_at)::text, '') FROM prompt_labels) AS pl_updated"
        )
        row = cursor.fetchone()
        return (row["def_count"], row["def_updated"], row["pv_count"],
                row["pv_max"], row["pl_updated"])


def cached_subagent_registry(subagents_dir):
    """Builtin + DB overlay with signature-based refresh, copy-on-write.

    One cheap signature query replaces the full DB reload; a changed signature
    builds a fresh registry outside the lock and swaps it atomically, so
    concurrent readers always iterate a stable snapshot and a failed rebuild
    leaves the previous instance intact.
    """
    key = str(Path(subagents_dir))
    with _registry_lock:
        registry = _subagent_overlay_registries.get(key)
        signature = _read_db_signature()
        stale = registry is None or signature != _db_signatures.get(key)
    if not stale:
        return registry
    from src.subagents.registry import SubagentRegistry
    fresh = SubagentRegistry(Path(subagents_dir))
    fresh.load_from_db()
    with _registry_lock:
        current_signature = _read_db_signature()
        _subagent_overlay_registries[key] = fresh
        _db_signatures[key] = current_signature
        return fresh
