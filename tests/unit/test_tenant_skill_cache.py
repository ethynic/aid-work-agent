"""TenantSkillCache 单元测试

核心回归点：缓存按 tenant_id 存「全量」skills，allowed 白名单在返回时过滤，
避免首个加载的 Agent 白名单污染后续不同白名单的 Agent（缓存污染事故根因）。

外部 Skill 插件 M1 扩展（docs/system/runtime-plugin-host-architecture-design.md 第9节）：
- base 加载 mock 面已从 tenant_skill_cache.SkillLoader 迁移到
  skill_plugin_gate.build_base_registry_sources（base 改走 gate，语义保持）；
- base 含插件：已审批可见 / 未审批不可见；
- 租户 skill 与已审批插件同名：按 loader 目录身份判定为租户 skill——
  get_content 读租户内容、is_plugin_skill 为 False、skill_execute 不被插件
  拦截误伤（评审阻断级意见的回归断言）。

全部 tmp 目录构造，不触碰真实 storage/。
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core import skill_plugin_gate as gate
from src.core.skill_executor import SkillExecutor, ExecutionResult
from src.core.skill_registry import SkillRegistry
from src.saas.services.tenant_skill_cache import TenantSkillCache
from src.config.settings import SkillPluginsConfig
from src.tools.skill.use_skill_tool import UseSkillTool

BASE_SKILLS = {
    "lead-management": MagicMock(),
    "after-sales-core": MagicMock(),
    "pre-sales-api": MagicMock(),
}


@pytest.fixture
def cache(monkeypatch, tmp_path):
    """构造 TenantSkillCache；base 加载 mock 迁移到 gate（M1 后 base 走审批门）"""

    def _fake_build_base_sources(builtin_dir, plugins_cfg=None):
        fake_loader = MagicMock()
        fake_loader.skills = dict(BASE_SKILLS)
        return gate.BaseSources(builtin_loader=fake_loader,
                                plugin_loaders=[], plugin_hashes={}, warnings=[])

    monkeypatch.setattr(
        "src.core.skill_plugin_gate.build_base_registry_sources", _fake_build_base_sources
    )
    monkeypatch.setattr(
        "src.saas.services.tenant_skill_cache.SkillResolver.get_tenant_skills_dir",
        lambda tid: Path("/nonexistent_tenant_skills"),
    )
    return TenantSkillCache(ttl_seconds=300), tmp_path


class TestTenantSkillCache:
    def test_first_load_filters_by_allowed(self, cache):
        cache_inst, base_dir = cache
        allowed = {"lead-management", "pre-sales-api"}
        result = cache_inst.get_or_load("tenant_x", base_dir, allowed)
        assert set(result.keys()) == allowed

    def test_allowed_none_returns_full(self, cache):
        cache_inst, base_dir = cache
        result = cache_inst.get_or_load("tenant_x", base_dir, None)
        assert set(result.keys()) == set(BASE_SKILLS.keys())

    def test_cache_hit_different_allowed_isolated(self, cache):
        """核心回归：缓存命中时不同 allowed 必须返回各自过滤结果，互不污染"""
        cache_inst, base_dir = cache

        # after-sales 白名单先加载并填充缓存
        after_allowed = {"lead-management", "after-sales-core"}
        r1 = cache_inst.get_or_load("tenant_x", base_dir, after_allowed)
        assert set(r1.keys()) == after_allowed

        # 缓存命中：pre-sales 白名单必须拿到自己的 pre-sales-api
        # （修复前会命中 after-sales 过滤后的缓存，拿不到 pre-sales-api）
        pre_allowed = {"lead-management", "pre-sales-api"}
        r2 = cache_inst.get_or_load("tenant_x", base_dir, pre_allowed)
        assert set(r2.keys()) == pre_allowed

        # 返回的过滤结果不应改变缓存内全量内容
        assert set(cache_inst._cache["tenant_x"][0].keys()) == set(BASE_SKILLS.keys())

    def test_ttl_expiry_reloads(self, cache):
        import time
        cache_inst, base_dir = cache
        cache_inst.get_or_load("tenant_x", base_dir, None)

        # 手动将缓存时间戳置为过期，下次访问应重新加载
        cached_full, _ = cache_inst._cache["tenant_x"]
        cache_inst._cache["tenant_x"] = (cached_full, time.time() - 301)

        result = cache_inst.get_or_load("tenant_x", base_dir, {"pre-sales-api"})
        assert set(result.keys()) == {"pre-sales-api"}


# ---------------------------------------------------------------------------
# 以下为 M1 插件链真实 gate 集成（tmp 目录，无 mock base 加载）
# ---------------------------------------------------------------------------

def _make_skill_md(skill_dir: Path, name: str, description: str = "", body: str = "") -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description or name}\n"
        "---\n"
        f"# {name}\n{body or (name + ' 正文。')}\n",
        encoding="utf-8",
    )


@pytest.fixture
def plugin_env(monkeypatch, tmp_path):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(gate, "configured_storage_root", lambda: storage_root)
    repo_dir = tmp_path / "repo-plugins"
    repo_dir.mkdir(parents=True)
    cfg = SkillPluginsConfig(
        enabled=True, repo_dir=str(repo_dir),
        storage_subdir="skills/plugins", approvals_subpath="skills/plugin-approvals.json",
    )
    env = SimpleNamespace(cfg=cfg, repo_dir=repo_dir,
                          approvals_file=storage_root / "skills" / "plugin-approvals.json")
    builtin = tmp_path / "builtin"
    _make_skill_md(builtin / "b1", "b1")
    env.builtin = builtin
    return env


def _approve(env, skill_dir: Path, name: str):
    approvals = gate.read_approvals(env.approvals_file)
    approvals[name] = {
        "computedHash": gate.compute_skill_dir_hash(skill_dir),
        "skillMdHash": gate.compute_skill_md_hash(skill_dir),
        "note": "test",
    }
    gate.write_approvals(env.approvals_file, approvals)


class TestTenantSkillCachePluginBase:
    def test_approved_plugin_visible_in_base(self, plugin_env, monkeypatch):
        monkeypatch.setattr("src.core.skill_plugin_gate._plugins_cfg", lambda: plugin_env.cfg)
        monkeypatch.setattr(
            "src.saas.services.skill_resolver.SkillResolver.get_tenant_skills_dir",
            lambda tid: Path("/nonexistent_tenant_skills"),
        )
        _make_skill_md(plugin_env.repo_dir / "p1", "p1", body="插件 p1 手册")
        _approve(plugin_env, plugin_env.repo_dir / "p1", "p1")

        cache_inst = TenantSkillCache(ttl_seconds=300)
        merged = cache_inst._load_and_merge("tenant_x", plugin_env.builtin)
        assert set(merged.keys()) == {"b1", "p1"}

    def test_unapproved_plugin_invisible_in_base(self, plugin_env, monkeypatch):
        monkeypatch.setattr("src.core.skill_plugin_gate._plugins_cfg", lambda: plugin_env.cfg)
        monkeypatch.setattr(
            "src.saas.services.skill_resolver.SkillResolver.get_tenant_skills_dir",
            lambda tid: Path("/nonexistent_tenant_skills"),
        )
        _make_skill_md(plugin_env.repo_dir / "p1", "p1")  # 未审批

        cache_inst = TenantSkillCache(ttl_seconds=300)
        merged = cache_inst._load_and_merge("tenant_x", plugin_env.builtin)
        assert set(merged.keys()) == {"b1"}


class TestTenantOverlayPluginSameName:
    """租户 skill 与已审批插件同名：租户覆盖语义不动，不被插件门误伤（阻断级回归）"""

    def _setup(self, plugin_env, tmp_path):
        # 已审批插件 shared-name
        plugin_dir = plugin_env.repo_dir / "shared-name"
        _make_skill_md(plugin_dir, "shared-name", body="插件版手册 MARKER-PLUGIN")
        _approve(plugin_env, plugin_dir, "shared-name")
        # 租户目录同名 skill
        tenant_dir = tmp_path / "tenant-skills" / "tenant-1" / "skills"
        _make_skill_md(tenant_dir / "shared-name", "shared-name", body="租户版手册 MARKER-TENANT")
        return tenant_dir

    def test_tenant_overlay_wins_and_not_misjudged_as_plugin(self, plugin_env, tmp_path, monkeypatch):
        tenant_dir = self._setup(plugin_env, tmp_path)

        # registry 经 load_from_sources 加载（含已审批插件）
        sources = gate.build_base_registry_sources(plugin_env.builtin, plugin_env.cfg)
        registry = SkillRegistry()
        registry.load_from_sources(sources.builtin_loader, sources.plugin_loaders,
                                   sources.plugin_hashes)
        assert registry.is_plugin_skill("shared-name") is True  # 覆盖前是插件

        # 真实 SkillSession 租户合并链（fresh cache + tmp 租户目录）
        fresh_cache = TenantSkillCache(ttl_seconds=300)
        monkeypatch.setattr("src.saas.services.tenant_skill_cache.tenant_skill_cache", fresh_cache)
        monkeypatch.setattr(
            "src.saas.services.skill_resolver.SkillResolver.get_tenant_skills_dir",
            lambda tid: tenant_dir,
        )
        from src.services.agent_runner.runtime.skill_session import SkillSession
        session = SkillSession("tenant-1", registry, memory=None)
        session._ensure_tenant_skills_loaded()

        # (a) _skills/_loaders 指向租户版本
        assert registry.get("shared-name").dir == tenant_dir / "shared-name"
        # (b) 目录身份判定：不再是插件 skill（名字键控会误伤——阻断级意见核心断言）
        assert registry.is_plugin_skill("shared-name") is False
        # (c) get_content 读租户手册（内容门不拦截），不是插件手册也不是 None
        content = registry.get_content("shared-name")
        assert content is not None
        assert "MARKER-TENANT" in content
        assert "MARKER-PLUGIN" not in content

    def test_tenant_overlay_skill_execute_not_blocked(self, plugin_env, tmp_path, monkeypatch):
        """同名租户 skill 不被「外部插件执行未开放」拦截误伤（可执行语义保持）"""
        from unittest.mock import AsyncMock
        tenant_dir = self._setup(plugin_env, tmp_path)

        sources = gate.build_base_registry_sources(plugin_env.builtin, plugin_env.cfg)
        registry = SkillRegistry()
        registry.load_from_sources(sources.builtin_loader, sources.plugin_loaders,
                                   sources.plugin_hashes)

        fresh_cache = TenantSkillCache(ttl_seconds=300)
        monkeypatch.setattr("src.saas.services.tenant_skill_cache.tenant_skill_cache", fresh_cache)
        monkeypatch.setattr(
            "src.saas.services.skill_resolver.SkillResolver.get_tenant_skills_dir",
            lambda tid: tenant_dir,
        )
        from src.services.agent_runner.runtime.skill_session import SkillSession
        session = SkillSession("tenant-1", registry, memory=None)
        session._ensure_tenant_skills_loaded()

        executor = SkillExecutor(registry, workspace=tmp_path / "ws")
        ok = ExecutionResult(success=True, stdout="ok", stderr="", exit_code=0, duration=0.0)
        monkeypatch.setattr(executor, "_execute_command", AsyncMock(return_value=ok))
        result = asyncio.run(executor.execute_skill_command("shared-name", "echo hi"))
        assert result.success is True  # 放行（未被插件拦截误伤）


class TestCrossTenantLoaderResidue:
    """跨租户 loader 残留回归（CR 阻断级意见）。

    同进程共享 registry（resource_cache 按 allowed 缓存）+ 每次执行新建
    SkillSession 的生产形态下：租户 T1 覆盖/新增 skill 后原地改写
    registry._loaders，租户 T2 顺序执行起底不得带入 T1 的 loader 映射——
    否则 T2 get_content 经 stale loader 串读 T1 手册、插件同名场景
    is_plugin_skill=False 使 M1 执行拦截失效。
    """

    def _setup(self, plugin_env, tmp_path):
        # 内置：shared（将被 t1 覆盖）+ b1
        _make_skill_md(plugin_env.builtin / "shared", "shared", body="内置版手册 MARKER-BUILTIN")
        _make_skill_md(plugin_env.builtin / "b1", "b1")
        # 已审批插件 p1（将被 t1 覆盖）
        _make_skill_md(plugin_env.repo_dir / "p1", "p1", body="插件版手册 MARKER-PLUGIN")
        _approve(plugin_env, plugin_env.repo_dir / "p1", "p1")
        # 租户目录
        t1_dir = tmp_path / "tenant-skills" / "tenant-1" / "skills"
        _make_skill_md(t1_dir / "shared", "shared", body="租户1覆盖版 MARKER-T1")
        _make_skill_md(t1_dir / "p1", "p1", body="租户1覆盖插件版 MARKER-T1-P1")
        _make_skill_md(t1_dir / "t1-only", "t1-only", body="租户1专属 MARKER-T1-ONLY")
        t2_dir = tmp_path / "tenant-skills" / "tenant-2" / "skills"
        _make_skill_md(t2_dir / "t2-only", "t2-only", body="租户2专属 MARKER-T2-ONLY")
        return {"tenant-1": t1_dir, "tenant-2": t2_dir}

    def _patch_tenant_env(self, monkeypatch, fresh_cache, tenant_dirs):
        monkeypatch.setattr("src.saas.services.tenant_skill_cache.tenant_skill_cache", fresh_cache)
        monkeypatch.setattr(
            "src.saas.services.skill_resolver.SkillResolver.get_tenant_skills_dir",
            lambda tid: tenant_dirs[tid],
        )

    def test_t2_execution_has_no_t1_loader_residue(self, plugin_env, tmp_path, monkeypatch):
        """T1 覆盖同名 → T2 顺序执行：T2 读基础链内容，无 T1 残留（f0）"""
        tenant_dirs = self._setup(plugin_env, tmp_path)
        monkeypatch.setattr("src.core.skill_plugin_gate._plugins_cfg", lambda: plugin_env.cfg)

        sources = gate.build_base_registry_sources(plugin_env.builtin, plugin_env.cfg)
        registry = SkillRegistry()
        registry.load_from_sources(sources.builtin_loader, sources.plugin_loaders,
                                   sources.plugin_hashes)

        from src.services.agent_runner.runtime.skill_session import SkillSession
        self._patch_tenant_env(monkeypatch, TenantSkillCache(ttl_seconds=300), tenant_dirs)

        session_t1 = SkillSession("tenant-1", registry, memory=None)
        session_t1._ensure_tenant_skills_loaded()
        # T1 视角正常：覆盖生效、专属可见
        assert "MARKER-T1" in registry.get_content("shared")
        assert "MARKER-T1-P1" in registry.get_content("p1")
        assert "MARKER-T1-ONLY" in registry.get_content("t1-only")
        assert registry.is_plugin_skill("p1") is False  # T1 覆盖后按租户 skill 处理

        # T2 顺序执行（新 SkillSession、同一共享 registry 实例）
        session_t2 = SkillSession("tenant-2", registry, memory=None)
        session_t2._ensure_tenant_skills_loaded()

        # (a) 覆盖内置同名：T2 读内置版，不串读 T1 覆盖版
        content = registry.get_content("shared")
        assert content is not None and "MARKER-BUILTIN" in content
        assert "MARKER-T1" not in content
        # (b) 覆盖插件同名：T2 读插件版 + 目录身份恢复为插件（执行拦截不失效）
        content = registry.get_content("p1")
        assert content is not None and "MARKER-PLUGIN" in content
        assert "MARKER-T1-P1" not in content
        assert registry.is_plugin_skill("p1") is True
        # (c) T2 自己的覆盖仍生效
        assert "MARKER-T2-ONLY" in registry.get_content("t2-only")
        assert registry.get_content("b1") is not None

    def test_t2_cannot_read_t1_exclusive_skill(self, plugin_env, tmp_path, monkeypatch):
        """T1 专属 skill 不在 T2 视图：_loaders/_skills 均无残留（f4）"""
        tenant_dirs = self._setup(plugin_env, tmp_path)
        monkeypatch.setattr("src.core.skill_plugin_gate._plugins_cfg", lambda: plugin_env.cfg)

        sources = gate.build_base_registry_sources(plugin_env.builtin, plugin_env.cfg)
        registry = SkillRegistry()
        registry.load_from_sources(sources.builtin_loader, sources.plugin_loaders,
                                   sources.plugin_hashes)

        from src.services.agent_runner.runtime.skill_session import SkillSession
        self._patch_tenant_env(monkeypatch, TenantSkillCache(ttl_seconds=300), tenant_dirs)

        SkillSession("tenant-1", registry, memory=None)._ensure_tenant_skills_loaded()
        SkillSession("tenant-2", registry, memory=None)._ensure_tenant_skills_loaded()

        assert "t1-only" not in registry.list_skills()
        assert registry.get("t1-only") is None
        assert "t1-only" not in registry._loaders  # loader 映射不跨租户残留
        assert registry.get_content("t1-only") is None
        # use_skill 端到端：T2 无法加载 T1 专属 skill（修复前经 stale loader
        # 可读出 T1 手册并返回 success）
        result = asyncio.run(UseSkillTool(registry).execute(skill="t1-only"))
        assert result["success"] is False


class TestRevokeHalfStateExecutionGate:
    """撤销/hash 不符后的租户 TTL 300s 半状态：执行门不得被绕过（CR 阻断级意见）。

    场景：插件 p5 审批加载并进入租户缓存 → 撤销审批 → resource_cache 签名链
    重建出不含 p5 的新 registry（_plugin_hashes/_plugin_loader_dirs 为空）→
    租户 TTL 窗口内旧合并结果仍带出 p5。修复后：skill_session 注回前剔除
    stale 插件条目；即便 _skills 被注回（剔除逻辑被绕过的兜底），
    is_plugin_skill 按 skill.dir 目录身份 fail-closed 识别插件，skill_execute
    仍被拦截（计划 §3.1「插件来源全量拦截」覆盖该窗口）。
    """

    def test_revoke_window_execute_still_blocked(self, plugin_env, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock
        monkeypatch.setattr("src.core.skill_plugin_gate._plugins_cfg", lambda: plugin_env.cfg)

        # 插件 p5：手册引导执行脚本 + scripts/run.sh
        _make_skill_md(plugin_env.repo_dir / "p5", "p5", body="插件手册：运行 bash scripts/run.sh")
        scripts_dir = plugin_env.repo_dir / "p5" / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "run.sh").write_text("#!/bin/bash\necho pwned\n", encoding="utf-8")
        _approve(plugin_env, plugin_env.repo_dir / "p5", "p5")

        registry_old = SkillRegistry()
        sources = gate.build_base_registry_sources(plugin_env.builtin, plugin_env.cfg)
        registry_old.load_from_sources(sources.builtin_loader, sources.plugin_loaders,
                                       sources.plugin_hashes)
        stale_skill = registry_old.get("p5")

        # 撤销前租户缓存已填充（含 p5）
        fresh_cache = TenantSkillCache(ttl_seconds=300)
        monkeypatch.setattr("src.saas.services.tenant_skill_cache.tenant_skill_cache", fresh_cache)
        monkeypatch.setattr(
            "src.saas.services.skill_resolver.SkillResolver.get_tenant_skills_dir",
            lambda tid: Path("/nonexistent_tenant_skills"),
        )
        merged = fresh_cache.get_or_load("tenant-1", plugin_env.builtin, None)
        assert "p5" in merged

        # 撤销审批 → 签名链重建出的新 registry（不含 p5）
        approvals = gate.read_approvals(plugin_env.approvals_file)
        del approvals["p5"]
        gate.write_approvals(plugin_env.approvals_file, approvals)
        sources_new = gate.build_base_registry_sources(plugin_env.builtin, plugin_env.cfg)
        registry_new = SkillRegistry()
        registry_new.load_from_sources(sources_new.builtin_loader, sources_new.plugin_loaders,
                                       sources_new.plugin_hashes)
        assert "p5" not in registry_new.list_skills()

        # TTL 窗口内租户执行：skill_session 注回前剔除 stale 插件条目
        from src.services.agent_runner.runtime.skill_session import SkillSession
        SkillSession("tenant-1", registry_new, memory=None)._ensure_tenant_skills_loaded()
        assert "p5" not in registry_new.list_skills()
        assert registry_new.get("p5") is None
        assert registry_new.get_content("p5") is None  # 半状态 get_content not found 保持

        # 兜底锚点：模拟剔除逻辑失效（_skills 被直接注回 stale 插件 skill），
        # is_plugin_skill 按 skill.dir 目录身份识别 → skill_execute 仍被拦截
        registry_new._skills = dict(registry_new._skills)
        registry_new._skills["p5"] = stale_skill
        assert registry_new.is_plugin_skill("p5") is True

        executor = SkillExecutor(registry_new, workspace=tmp_path / "ws")
        execute_mock = AsyncMock()
        monkeypatch.setattr(executor, "_execute_command", execute_mock)
        result = asyncio.run(executor.execute_skill_command("p5", "bash scripts/run.sh"))
        assert result.success is False
        assert "外部 Skill 插件" in result.stderr
        assert "请勿重试" in result.stderr
        execute_mock.assert_not_called()  # 未到达子进程创建（插件脚本不执行）
