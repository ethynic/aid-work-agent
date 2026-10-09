"""resource_cache.cached_skill_registry 签名刷新单元测试（外部 Skill 插件 M1）

覆盖（docs/system/runtime-plugin-host-architecture-design.md 第9节）：
- enabled=False → 签名恒 ()，行为与现状一致（永久缓存，不重扫）；
- enabled=True 但插件目录/清单不存在 → 签名恒定，不重复重建；
- 插件目录/审批清单变更触发重建（换新实例，插件可见）；
- 未变更命中缓存（同实例）；
- 重建重放内置 init DDL（init_tables 被再次调用，幂等前提为显式约束）；
- 租户 overlay 与签名重建并发交错：实例被改写 _skills/_loaders 后重建换新 →
  新实例不含 overlay 且行为正常（§3.6 第 2 点语义断言）。

全部 tmp 目录构造（monkeypatch resource_cache._REPO_ROOT 与 gate 路径解析），
不触碰真实 storage/ 与真实 src/skills（避免真实 init DDL）。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import src.services.agent_runner.runtime.resource_cache as resource_cache
from src.core import skill_plugin_gate as gate
from src.config.settings import SkillPluginsConfig

pytestmark = pytest.mark.skills


def _make_skill_md(skill_dir: Path, name: str, extra_frontmatter: str = "") -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {name}\n"
        f"{extra_frontmatter}"
        "---\n"
        f"# {name}\n{name} 正文。\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _reset_skill_caches():
    resource_cache._skill_registries.clear()
    resource_cache._skill_registry_signatures.clear()
    yield
    resource_cache._skill_registries.clear()
    resource_cache._skill_registry_signatures.clear()


@pytest.fixture
def env(monkeypatch, tmp_path):
    """tmp 环境：builtin= tmp/src/skills（含 init DDL 标记 skill），插件链指向 tmp"""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(gate, "configured_storage_root", lambda: storage_root)
    monkeypatch.setattr(resource_cache, "_REPO_ROOT", tmp_path)
    repo_dir = tmp_path / "repo-plugins"
    repo_dir.mkdir(parents=True)
    cfg = SkillPluginsConfig(
        enabled=True, repo_dir=str(repo_dir),
        storage_subdir="skills/plugins", approvals_subpath="skills/plugin-approvals.json",
    )
    monkeypatch.setattr(gate, "_plugins_cfg", lambda: cfg)

    builtin = tmp_path / "src" / "skills"
    _make_skill_md(builtin / "b1", "b1")
    # init DDL 标记 skill：init_tables 每次执行向 marker 追加计数
    _make_skill_md(builtin / "b-init", "b-init", extra_frontmatter="init_script: init_marker.py\n")
    scripts = builtin / "b-init" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "init_marker.py").write_text(
        "from pathlib import Path\n\n"
        "def init_tables():\n"
        "    marker = Path(__file__).resolve().parents[1] / 'init_calls.txt'\n"
        "    count = int(marker.read_text()) if marker.exists() else 0\n"
        "    marker.write_text(str(count + 1))\n",
        encoding="utf-8",
    )
    return SimpleNamespace(
        cfg=cfg, builtin=builtin, repo_dir=repo_dir,
        storage_dir=storage_root / "skills" / "plugins",
        approvals_file=storage_root / "skills" / "plugin-approvals.json",
        init_marker=builtin / "b-init" / "init_calls.txt",
    )


def _count_builds(monkeypatch):
    counter = {"n": 0}
    original = gate.build_base_registry_sources

    def _counting(*args, **kwargs):
        counter["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(gate, "build_base_registry_sources", _counting)
    return counter


def _install_plugin(env, name: str = "p1"):
    skill_dir = env.repo_dir / name
    _make_skill_md(skill_dir, name, extra_frontmatter="metadata:\n  execution: server\n")
    approvals = gate.read_approvals(env.approvals_file)
    approvals[name] = {
        "computedHash": gate.compute_skill_dir_hash(skill_dir),
        "skillMdHash": gate.compute_skill_md_hash(skill_dir),
        "note": "test",
    }
    gate.write_approvals(env.approvals_file, approvals)


class TestCacheWithoutPlugins:
    def test_disabled_signature_empty_permanent_cache(self, env, monkeypatch):
        monkeypatch.setattr(gate, "_plugins_cfg",
                            lambda: env.cfg.model_copy(update={"enabled": False}))
        counter = _count_builds(monkeypatch)
        r1 = resource_cache.cached_skill_registry(None)
        r2 = resource_cache.cached_skill_registry(None)
        assert r1 is r2
        assert counter["n"] == 1  # 现状等价：不重扫
        assert "b1" in r1.list_skills()

    def test_enabled_missing_dirs_no_rebuild(self, env, monkeypatch):
        """enabled=True 但插件目录/清单不存在 → 签名恒定，不重复重建"""
        counter = _count_builds(monkeypatch)
        r1 = resource_cache.cached_skill_registry(None)
        r2 = resource_cache.cached_skill_registry(None)
        assert r1 is r2
        assert counter["n"] == 1

    def test_unchanged_hits_cache(self, env, monkeypatch):
        _install_plugin(env)
        counter = _count_builds(monkeypatch)
        r1 = resource_cache.cached_skill_registry(None)
        r2 = resource_cache.cached_skill_registry(None)
        assert r1 is r2
        assert counter["n"] == 1
        assert "p1" in r1.list_skills()


class TestSignatureRebuild:
    def test_plugin_install_triggers_rebuild(self, env, monkeypatch):
        r1 = resource_cache.cached_skill_registry(None)
        assert "p1" not in r1.list_skills()

        _install_plugin(env)  # 新插件子目录 + 清单 mtime/size 变化
        r2 = resource_cache.cached_skill_registry(None)
        assert r2 is not r1
        assert "p1" in r2.list_skills()
        assert r2.is_plugin_skill("p1") is True

    def test_approvals_change_triggers_rebuild(self, env):
        _install_plugin(env)
        r1 = resource_cache.cached_skill_registry(None)

        approvals = gate.read_approvals(env.approvals_file)
        approvals["p1"]["note"] = "updated note with different size " + "y" * 80
        gate.write_approvals(env.approvals_file, approvals)
        r2 = resource_cache.cached_skill_registry(None)
        assert r2 is not r1

    def test_rebuild_replays_builtin_init_ddl(self, env):
        """重建重放内置 init DDL：init_tables 被再次调用（幂等为显式前提）"""
        assert not env.init_marker.exists()
        resource_cache.cached_skill_registry(None)
        assert env.init_marker.read_text() == "1"

        _install_plugin(env)  # 触发签名变化 → 整体重建
        resource_cache.cached_skill_registry(None)
        assert env.init_marker.read_text() == "2"

    def test_rebuild_failure_keeps_old_registry(self, env, monkeypatch):
        r1 = resource_cache.cached_skill_registry(None)
        original_build = gate.build_base_registry_sources

        def _broken_build(*args, **kwargs):
            raise RuntimeError("simulated rebuild failure")

        _install_plugin(env)  # 签名已变化，但重建失败
        monkeypatch.setattr(gate, "build_base_registry_sources", _broken_build)
        with pytest.raises(RuntimeError):
            resource_cache.cached_skill_registry(None)
        # 旧 registry 保留在缓存中，下一次调用（修复后）仍可用
        monkeypatch.setattr(gate, "build_base_registry_sources", original_build)
        r3 = resource_cache.cached_skill_registry(None)
        assert r3 is not r1
        assert "p1" in r3.list_skills()


class TestOverlayInterleave:
    def test_rebuilt_instance_has_no_overlay(self, env):
        """§3.6 第 2 点：skill_session 原地改写共享实例后重建换新 → 新实例不含 overlay"""
        r1 = resource_cache.cached_skill_registry(None)
        # 模拟 skill_session._ensure_tenant_skills_loaded 的原地改写
        r1._skills = dict(r1._skills)
        r1._skills["tenant-overlay"] = r1._skills["b1"]
        r1._loaders = dict(r1._loaders)
        r1._loaders["tenant-overlay"] = r1._loaders["b1"]
        assert "tenant-overlay" in r1.list_skills()

        _install_plugin(env)  # 触发重建换新
        r2 = resource_cache.cached_skill_registry(None)
        assert r2 is not r1
        assert "tenant-overlay" not in r2.list_skills()  # 新实例不含 overlay
        assert "b1" in r2.list_skills()
        assert r2.get_content("b1") is not None  # 新实例行为正常
        assert "tenant-overlay" in r1.list_skills()  # 旧实例（含 overlay）不受影响


class TestTriggerSignatureTagging:
    """缓存以「触发重建的签名」标记（M1 CR P2：锁内复核 TOCTOU 收敛）。

    旧行为缺陷：重建期间签名再变时，旧快照被打上锁内重读的新签名标签驻留缓存，
    直到下一次签名变化才纠正。现行为：以触发签名标记，下次轮询必失配再重建。
    """

    def test_mid_rebuild_change_rebuilds_again_not_stale_cached(self, env, monkeypatch):
        values = ["S1", "S2"]  # 第 1 次调用读到 S1；重建期间变为 S2
        calls = {"n": 0}

        def _fake_signature(*args, **kwargs):
            index = min(calls["n"], len(values) - 1)
            calls["n"] += 1
            return values[index]

        monkeypatch.setattr(gate, "plugin_signature", _fake_signature)

        r1 = resource_cache.cached_skill_registry(None)  # 空 → 重建，标记 S1
        r2 = resource_cache.cached_skill_registry(None)  # S2≠S1 → 再重建，标记 S2
        r3 = resource_cache.cached_skill_registry(None)  # S2 命中缓存

        assert r1 is not r2, "重建期间签名变化后，下一次调用必须再重建（不驻留混合快照）"
        assert r3 is r2, "签名稳定后应命中缓存"
