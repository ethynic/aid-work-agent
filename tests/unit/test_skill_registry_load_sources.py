"""SkillRegistry.load_from_sources 单元测试（外部 Skill 插件 M1 知识层）

覆盖（docs/plans/plan-external-skill-plugin-m1.md §6）：
- 合并顺序（内置 + 插件低→高）与 allowed 过滤；
- _all_skills 含已审批插件；_plugin_hashes 填充；_loader 为 None；
- get_content 内容门：SKILL.md 篡改后返回 None（fail-closed），一致时正常返回；
- loader miss 时不走 body last-resort（含 --revoke 半状态）；
- 插件 loader 不执行 init_script（run_init=False），内置 loader 照旧执行；
- load_from_sources 防御性复查「插件与内置同名拒绝」。

全部 tmp 目录构造，不触碰真实 storage/。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import skill_plugin_gate as gate
from src.core.skill_registry import SkillRegistry
from src.config.settings import SkillPluginsConfig

pytestmark = pytest.mark.skills


def _make_skill_md(skill_dir: Path, name: str, description: str = "", body: str = "",
                   extra_frontmatter: str = "") -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description or name}\n"
        f"{extra_frontmatter}"
        "---\n"
        f"# {name}\n{body or (name + ' 正文。')}\n",
        encoding="utf-8",
    )
    return skill_md


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
                          storage_dir=storage_root / "skills" / "plugins",
                          approvals_file=storage_root / "skills" / "plugin-approvals.json")
    env.storage_dir.mkdir(parents=True)
    return env


def _approve(env, skill_dir: Path, name: str):
    approvals = gate.read_approvals(env.approvals_file)
    approvals[name] = {
        "computedHash": gate.compute_skill_dir_hash(skill_dir),
        "skillMdHash": gate.compute_skill_md_hash(skill_dir),
        "note": "test",
    }
    gate.write_approvals(env.approvals_file, approvals)


def _setup_dirs(plugin_env, tmp_path):
    """内置 b1/b2 + 已审批插件 p1（repo 位）+ 未审批插件 p2"""
    builtin = tmp_path / "builtin"
    _make_skill_md(builtin / "b1", "b1", description="内置 b1")
    _make_skill_md(builtin / "b2", "b2", description="内置 b2")
    _make_skill_md(plugin_env.repo_dir / "p1", "p1", description="插件 p1", body="插件 p1 手册正文 MARKER-P1")
    _approve(plugin_env, plugin_env.repo_dir / "p1", "p1")
    _make_skill_md(plugin_env.repo_dir / "p2", "p2", description="未审批插件")
    return builtin


def _build_registry(builtin, plugin_env, allowed=None) -> SkillRegistry:
    sources = gate.build_base_registry_sources(builtin, plugin_env.cfg)
    registry = SkillRegistry()
    registry.load_from_sources(sources.builtin_loader, sources.plugin_loaders,
                               sources.plugin_hashes, allowed=allowed)
    return registry


class TestLoadFromSourcesMerge:
    def test_merge_and_allowed_filter(self, plugin_env, tmp_path):
        builtin = _setup_dirs(plugin_env, tmp_path)
        registry = _build_registry(builtin, plugin_env)
        assert sorted(registry.list_skills()) == ["b1", "b2", "p1"]  # p2 未审批不可见

        filtered = _build_registry(builtin, plugin_env, allowed=["b1", "p1"])
        assert sorted(filtered.list_skills()) == ["b1", "p1"]

    def test_all_skills_contains_plugin_even_if_filtered(self, plugin_env, tmp_path):
        builtin = _setup_dirs(plugin_env, tmp_path)
        registry = _build_registry(builtin, plugin_env, allowed=["b1"])
        assert "p1" in registry.list_all_loaded_skills()
        assert registry.get_all_skill("p1").description == "插件 p1"

    def test_plugin_hashes_filled_loader_none(self, plugin_env, tmp_path):
        builtin = _setup_dirs(plugin_env, tmp_path)
        registry = _build_registry(builtin, plugin_env)
        assert set(registry.plugin_hashes.keys()) == {"p1"}
        assert registry._loader is None

    def test_plugin_dir_priority_storage_over_repo(self, plugin_env, tmp_path):
        builtin = tmp_path / "builtin"
        _make_skill_md(builtin / "b1", "b1")
        # 两目录内容一致（同一 hash 可同时通过审批）→ 合并取高优先级（storage）
        _make_skill_md(plugin_env.repo_dir / "dup", "dup", body="相同内容")
        _make_skill_md(plugin_env.storage_dir / "dup", "dup", body="相同内容")
        _approve(plugin_env, plugin_env.storage_dir / "dup", "dup")

        registry = _build_registry(builtin, plugin_env)
        assert registry.get("dup").dir == plugin_env.storage_dir / "dup"
        assert registry.is_plugin_skill("dup") is True


class TestLoadFromSourcesContentGate:
    def test_plugin_content_ok_when_hash_consistent(self, plugin_env, tmp_path):
        builtin = _setup_dirs(plugin_env, tmp_path)
        registry = _build_registry(builtin, plugin_env)
        content = registry.get_content("p1")
        assert content is not None
        assert "MARKER-P1" in content

    def test_tampered_skill_md_returns_none(self, plugin_env, tmp_path):
        builtin = _setup_dirs(plugin_env, tmp_path)
        registry = _build_registry(builtin, plugin_env)
        # 审批并加载后磁盘 SKILL.md 被篡改（深层修改不触发签名变化的窗口）
        (plugin_env.repo_dir / "p1" / "SKILL.md").write_text(
            "---\nname: p1\ndescription: 插件 p1\n---\n# 篡改后的手册\n", encoding="utf-8")
        assert registry.get_content("p1") is None

    def test_loader_miss_plugin_name_no_body_leak(self, plugin_env, tmp_path):
        """_loaders miss + 名字在审批插件集 → None，不走 body last-resort 泄漏全文"""
        builtin = _setup_dirs(plugin_env, tmp_path)
        registry = _build_registry(builtin, plugin_env)
        stale_body = registry.get("p1").body
        del registry._loaders["p1"]
        assert registry.get_content("p1") is None
        assert stale_body  # 确认 body 确实有内容可泄漏（防呆）

    def test_revoke_half_state_get_content_not_found(self, plugin_env, tmp_path):
        """--revoke 后新实例不含插件；租户 TTL 窗口 _skills 旧值 → get_content None"""
        builtin = _setup_dirs(plugin_env, tmp_path)
        registry_old = _build_registry(builtin, plugin_env)
        stale_skill = registry_old.get("p1")

        # 撤销审批 → runner 签名链重建后的新实例（模拟 resource_cache 重建结果）
        approvals = gate.read_approvals(plugin_env.approvals_file)
        del approvals["p1"]
        gate.write_approvals(plugin_env.approvals_file, approvals)
        registry_new = _build_registry(builtin, plugin_env)
        assert "p1" not in registry_new.list_skills()

        # 租户 TTL 300s 窗口：合并链旧缓存仍带出 p1 的 _skills 旧值（loader miss）
        registry_new._skills = dict(registry_new._skills)
        registry_new._skills["p1"] = stale_skill
        assert "p1" in registry_new.list_skills()  # 工具描述仍列出（半状态定义）
        assert registry_new.get_content("p1") is None  # 内容 not found，无 body 泄漏

    def test_builtin_content_unchanged(self, plugin_env, tmp_path):
        builtin = _setup_dirs(plugin_env, tmp_path)
        registry = _build_registry(builtin, plugin_env)
        content = registry.get_content("b1")
        assert content is not None and "内置 b1" not in content  # description 不进正文
        assert "b1 正文" in content


class TestLoadFromSourcesDefensive:
    def test_plugin_with_builtin_name_rejected_in_load_from_sources(self, plugin_env, tmp_path):
        """防御性复查：手工构造与内置同名的插件 loader → 拒绝注册（内置优先）"""
        builtin = tmp_path / "builtin"
        _make_skill_md(builtin / "b1", "b1", description="内置 b1")
        _make_skill_md(plugin_env.repo_dir / "fake-b1", "b1", description="插件冒充")
        plugin_loader = gate.SkillLoader(plugin_env.repo_dir, include={"b1"}, run_init=False)

        registry = SkillRegistry()
        registry.load_from_sources(
            gate.SkillLoader(builtin), [plugin_loader],
            {"b1": ("computed-hash", "md-hash")}, allowed=None,
        )
        assert registry.get("b1").description == "内置 b1"
        assert "b1" not in registry.plugin_hashes  # 名字键控不残留

    def test_plugin_loader_skips_init_builtin_runs(self, plugin_env, tmp_path, monkeypatch):
        """插件 loader 构造不执行 init_script；内置 loader 照旧执行（现状回归）"""
        calls = []

        def _spy(self):
            calls.append(self.skills_dir)

        monkeypatch.setattr(gate.SkillLoader, "_init_skill_tables", _spy)
        builtin = _setup_dirs(plugin_env, tmp_path)

        sources = gate.build_base_registry_sources(builtin, plugin_env.cfg)
        assert calls == [builtin]  # 仅内置 loader 触发；插件 loader（p1）不触发

        registry = SkillRegistry()
        registry.load_from_sources(sources.builtin_loader, sources.plugin_loaders,
                                   sources.plugin_hashes)
        assert "p1" in registry.list_skills()


class TestDirectoryLoadClearsPluginState:
    """目录加载链切换时清空插件来源状态（M1 CR P2：防 hash/目录残留误判）。

    现实触发面：全局单例 skill_registry 先经 load_from_sources（AgentRunner 链），
    再被 skill_executor 兜底路径 load_from_directory 复用。
    """

    def test_load_from_directory_clears_plugin_residue(self, plugin_env, tmp_path):
        builtin = _setup_dirs(plugin_env, tmp_path)
        registry = _build_registry(builtin, plugin_env)
        assert "p1" in registry.plugin_hashes  # 前置：插件状态已填充

        plain = tmp_path / "plain"
        _make_skill_md(plain / "b1", "b1", description="目录链 b1")
        registry.load_from_directory(plain)

        assert dict(registry.plugin_hashes) == {}
        assert registry.is_plugin_skill("b1") is False
        assert registry.get_content("b1") is not None  # 不因残留被假阳性拦截

    def test_load_from_directories_clears_plugin_residue(self, plugin_env, tmp_path):
        builtin = _setup_dirs(plugin_env, tmp_path)
        registry = _build_registry(builtin, plugin_env)

        plain = tmp_path / "plain"
        _make_skill_md(plain / "b1", "b1", description="目录链 b1")
        registry.load_from_directories([plain])

        assert dict(registry.plugin_hashes) == {}
        assert registry.is_plugin_skill("b1") is False
        assert registry.get_content("b1") is not None
