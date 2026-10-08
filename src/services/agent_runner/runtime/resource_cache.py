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
_skill_registry_signatures: dict = {}

_registry_lock = threading.Lock()
_subagent_registries: dict = {}
_subagent_overlay_registries: dict = {}
_db_signatures: dict = {}

_REPO_ROOT = Path(__file__).resolve().parents[4]


def cached_skill_registry(allowed):
    """Skill registry with plugin-approval signature refresh (M1, plan §3.6).

    仿 cached_subagent_registry 的「签名轮询 + 锁外重建 + 原子换新」机制，但
    保证范围不同（如实界定）：

    - 换新只保证「重建时刻」的快照一致——skill registry 实例并非不可变：
      skill_session._ensure_tenant_skills_loaded 会原地改写共享实例的
      _skills/_loaders（现状已有的跨执行/跨租户 overlay 污染，M1 不修复）。
      两次重建之间实例可能携带某租户的 overlay；重建换新后新实例不含 overlay，
      由下一次租户执行重新叠加（该语义有并发交错测试锚定）。
    - 签名为轻量 stat（审批清单 mtime+size + 插件目录一级子目录 mtime）；
      skills.plugins.enabled=False → 签名恒 ()，行为与现状完全一致（永久缓存）。
    - 缓存以「触发重建的签名」标记：重建期间插件/清单再变（毫秒级窗口）时，
      下一次轮询必失配并再重建，最终一致且不驻留旧快照（与
      cached_subagent_registry 同语义）。
    - 多 worker/多进程各自持有进程缓存，靠签名轮询最终一致——审批/安装/撤销
      后下一次执行生效，无需重启或跨进程通知。
    - 重建会重放内置 init DDL（builtin SkillLoader 重新构造）：频率低（仅插件/
      清单变更时）且 init_tables 应幂等（计划 §3.6 第 5 点 / 风险 7 的显式约束）。
    - 重建失败抛出异常时旧 registry 保留在缓存中，下一次调用重试。

    load_from_sources also runs builtin skill init DDL, so caching removes the
    per-execution table initialization against the database.
    """
    from src.core.skill_registry import SkillRegistry
    from src.core import skill_plugin_gate
    key = frozenset(allowed) if allowed else None
    with _skill_lock:
        registry = _skill_registries.get(key)
        signature = skill_plugin_gate.plugin_signature()
        stale = registry is None or signature != _skill_registry_signatures.get(key)
    if not stale:
        return registry

    builtin_dir = _REPO_ROOT / "src" / "skills"
    sources = skill_plugin_gate.build_base_registry_sources(builtin_dir)
    fresh = SkillRegistry()
    fresh.load_from_sources(
        sources.builtin_loader, sources.plugin_loaders, sources.plugin_hashes,
        allowed=allowed,
    )
    with _skill_lock:
        # 以「触发本次重建的签名」标记缓存：重建期间审批/插件目录再变时，存下
        # 的签名必与下次轮询失配并触发再重建——不会把旧快照打上新签名标签
        # 驻留缓存（最终一致，代价是至多多一次重建，无需锁内二次重建）
        _skill_registries[key] = fresh
        _skill_registry_signatures[key] = signature
        return fresh


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
        # 同 cached_skill_registry：以触发重建的签名标记，重建期间的 DB 变更
        # 由下一次签名轮询捕获（至多多一次重建），不驻留旧快照
        _subagent_overlay_registries[key] = fresh
        _db_signatures[key] = signature
        return fresh
