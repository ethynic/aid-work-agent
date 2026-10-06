"""skill_plugin_gate 单元测试（外部 Skill 插件 M1 知识层）

覆盖（docs/plans/plan-external-skill-plugin-m1.md §6）：
- 目录 hash：内容变更敏感、排除项生效、文件创建顺序无关；
- 审批清单：缺失/损坏 JSON → {}（fail-closed）；原子写（replace 时刻临时文件内容完整）；
- scan_plugin_dir：未审批不可见、hash 不符剔除、与内置同名拒绝、通过者进入 include；
- build_base_registry_sources：插件间同名高优先级目录（storage）覆盖低优先级（repo）；
- plugin_signature：enabled=False → ()；对清单/子目录变化敏感；目录清单缺失 → 恒定；
- SkillLoader include / run_init 默认行为。

全部使用 tmp 目录构造插件与清单，不触碰真实 storage/。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import skill_plugin_gate as gate
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
    return skill_dir


@pytest.fixture
def plugin_env(monkeypatch, tmp_path):
    """tmp 插件环境：repo 插件位 + storage 插件位 + 审批清单路径（不触碰真实 storage/）"""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(gate, "configured_storage_root", lambda: storage_root)
    repo_dir = tmp_path / "repo-plugins"
    repo_dir.mkdir(parents=True)
    cfg = SkillPluginsConfig(
        enabled=True,
        repo_dir=str(repo_dir),
        storage_subdir="skills/plugins",
        approvals_subpath="skills/plugin-approvals.json",
    )
    env = SimpleNamespace(
        cfg=cfg,
        repo_dir=repo_dir,
        storage_dir=storage_root / "skills" / "plugins",
        approvals_file=storage_root / "skills" / "plugin-approvals.json",
    )
    env.storage_dir.mkdir(parents=True)
    return env


def _approve(env, skill_dir: Path, name: str, note: str = "test"):
    approvals = gate.read_approvals(env.approvals_file)
    approvals[name] = {
        "computedHash": gate.compute_skill_dir_hash(skill_dir),
        "skillMdHash": gate.compute_skill_md_hash(skill_dir),
        "note": note,
    }
    gate.write_approvals(env.approvals_file, approvals)


class TestComputeSkillDirHash:
    def test_content_change_changes_hash(self, tmp_path):
        skill_dir = tmp_path / "p1"
        _make_skill_md(skill_dir, "p1")
        h1 = gate.compute_skill_dir_hash(skill_dir)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: p1\ndescription: p1\n---\n# 篡改\n", encoding="utf-8")
        h2 = gate.compute_skill_dir_hash(skill_dir)
        assert h1 != h2

    def test_new_file_changes_hash(self, tmp_path):
        skill_dir = tmp_path / "p1"
        _make_skill_md(skill_dir, "p1")
        h1 = gate.compute_skill_dir_hash(skill_dir)
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "run.py").write_text("print('x')\n", encoding="utf-8")
        assert gate.compute_skill_dir_hash(skill_dir) != h1

    def test_excluded_entries_do_not_change_hash(self, tmp_path):
        skill_dir = tmp_path / "p1"
        _make_skill_md(skill_dir, "p1")
        h1 = gate.compute_skill_dir_hash(skill_dir)
        (skill_dir / "__pycache__").mkdir()
        (skill_dir / "__pycache__" / "a.py").write_text("x=1\n", encoding="utf-8")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "a.pyc").write_bytes(b"\x00")
        (skill_dir / ".DS_Store").write_bytes(b"junk")
        (skill_dir / "shots").mkdir()
        (skill_dir / "shots" / "s.png").write_bytes(b"png")
        (skill_dir / "log").mkdir()
        (skill_dir / "log" / "a.log").write_text("log\n", encoding="utf-8")
        assert gate.compute_skill_dir_hash(skill_dir) == h1

    def test_hash_independent_of_creation_order(self, tmp_path):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        _make_skill_md(d1, "same", body="相同内容")
        (d1 / "scripts").mkdir()
        (d1 / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
        (d1 / "references").mkdir()
        (d1 / "references" / "r.md").write_text("ref\n", encoding="utf-8")

        # 反序创建同样内容
        (d2 / "references").mkdir(parents=True)
        (d2 / "references" / "r.md").write_text("ref\n", encoding="utf-8")
        (d2 / "scripts").mkdir(parents=True)
        (d2 / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
        _make_skill_md(d2, "same", body="相同内容")

        assert gate.compute_skill_dir_hash(d1) == gate.compute_skill_dir_hash(d2)


class TestComputeSkillExecHash:
    """设备对账 hash（M2，plan §3.6）：与 computedHash 同算法/同内置排除集，
    额外排除 metadata.mutable 声明的自学习可变路径——解决样本 skill 运行时
    写回 references/ui-cache.json 导致 hash 永久漂移的问题。"""

    def test_no_mutable_equals_computed_hash(self, tmp_path):
        """无 mutable 时与 compute_skill_dir_hash 同值（双算法一致性的锚点断言）"""
        skill_dir = tmp_path / "p1"
        _make_skill_md(skill_dir, "p1")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
        assert gate.compute_skill_exec_hash(skill_dir, []) == gate.compute_skill_dir_hash(skill_dir)

    def test_mutable_excluded_from_exec_hash(self, tmp_path):
        """mutable 文件内容变化 → exec_hash 不变（运行时写回不漂移）、computedHash 变化"""
        skill_dir = tmp_path / "p1"
        _make_skill_md(skill_dir, "p1")
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "ui-cache.json").write_text("{}\n", encoding="utf-8")
        mutable = ["references/ui-cache.json"]
        exec_h1 = gate.compute_skill_exec_hash(skill_dir, mutable)
        dir_h1 = gate.compute_skill_dir_hash(skill_dir)
        # 自学习写回（内容变化）
        (skill_dir / "references" / "ui-cache.json").write_text('{"cache": "v2"}\n', encoding="utf-8")
        assert gate.compute_skill_exec_hash(skill_dir, mutable) == exec_h1
        assert gate.compute_skill_dir_hash(skill_dir) != dir_h1

    def test_builtin_exclusions_apply(self, tmp_path):
        """内置排除集（__pycache__/shots/log/*.pyc/.DS_Store）在 exec_hash 同样生效"""
        skill_dir = tmp_path / "p1"
        _make_skill_md(skill_dir, "p1")
        h1 = gate.compute_skill_exec_hash(skill_dir, [])
        (skill_dir / "__pycache__").mkdir()
        (skill_dir / "__pycache__" / "a.py").write_text("x=1\n", encoding="utf-8")
        (skill_dir / "shots").mkdir()
        (skill_dir / "shots" / "s.png").write_bytes(b"png")
        (skill_dir / "log").mkdir()
        (skill_dir / "log" / "a.log").write_text("log\n", encoding="utf-8")
        (skill_dir / "a.pyc").write_bytes(b"\x00")
        (skill_dir / ".DS_Store").write_bytes(b"junk")
        assert gate.compute_skill_exec_hash(skill_dir, []) == h1

    def test_non_mutable_content_change_changes_exec_hash(self, tmp_path):
        """非 mutable 文件（含 SKILL.md/入口脚本）变化 → exec_hash 变化（入口脚本必须受锁定）"""
        skill_dir = tmp_path / "p1"
        _make_skill_md(skill_dir, "p1")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "flow.py").write_text("print(1)\n", encoding="utf-8")
        mutable = ["references/ui-cache.json"]
        h1 = gate.compute_skill_exec_hash(skill_dir, mutable)
        # 篡改入口脚本（仅替换 entry 脚本内容、其余不变的篡改形态）
        (skill_dir / "scripts" / "flow.py").write_text("import os; os.system('rm -rf /')\n", encoding="utf-8")
        assert gate.compute_skill_exec_hash(skill_dir, mutable) != h1
        # 篡改 SKILL.md
        h2 = gate.compute_skill_exec_hash(skill_dir, mutable)
        (skill_dir / "SKILL.md").write_text("---\nname: p1\ndescription: 篡改\n---\n", encoding="utf-8")
        assert gate.compute_skill_exec_hash(skill_dir, mutable) != h2


# 共享 hash 向量（M2 防双端算法漂移锚点，plan §3.6/风险 1）：fixture 内容与
# 期望常量和设备端 clients/agent-tool-runtime/tests/skillRunner.test.ts 的
# FIXTURE_FILES / DEMO_DIR_HASH / DEMO_EXEC_HASH 一字不动地同源抄录两侧，
# Python/TS 两侧测试各自对本地实现断言同一组常量——任一端改 hash 算法即该端
# 测试红，另一端同组向量即对账来源。fixture 含 __pycache__/shots/.DS_Store
# 排除项，同一向量同时覆盖内置排除集语义。
SHARED_VECTOR_FILES = {
    "SKILL.md": "---\nname: demo-skill\nversion: 1.2.3\nmetadata:\n  entry: scripts/main.js\n  mutable:\n    - references/cache.json\n---\n\n手册正文。\n",
    "scripts/main.js": "// 入口（stub）：echo JSON\nconsole.log('hello from demo-skill')\n",
    "scripts/util.js": "export const answer = 42\n",
    "references/cache.json": '{"updated_at": "2026-01-01T00:00:00Z"}\n',
    "__pycache__/demo.cpython-312.pyc": "\x80\x04junk",
    "shots/shot-1.png": "PNGDATA",
    ".DS_Store": "desktop junk\n",
}
# 全量（mutable 为空集；= compute_skill_dir_hash 同值）→ 设备端 DEMO_DIR_HASH
SHARED_VECTOR_EXPECTED_EXEC_HASH = "a756571353dfe4f6aa9179d07d0568d5a8e258a5d82f96500f7440ba9efd8253"
# 排除 references/cache.json 后 → 设备端 DEMO_EXEC_HASH
SHARED_VECTOR_EXPECTED_EXEC_HASH_NO_CACHE = "5c53b7543e10f01c442d83d2715a581b6ed7a6a4e7c7d3f7a74bc06ec3492a57"


class TestComputeSkillExecHashSharedVector:
    def test_vector_full_and_excluded(self, tmp_path):
        skill_dir = tmp_path / "demo-skill"
        for rel, content in SHARED_VECTOR_FILES.items():
            p = skill_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        assert gate.compute_skill_exec_hash(skill_dir, []) == SHARED_VECTOR_EXPECTED_EXEC_HASH
        assert gate.compute_skill_exec_hash(skill_dir, []) == gate.compute_skill_dir_hash(skill_dir)
        assert gate.compute_skill_exec_hash(
            skill_dir, ["references/cache.json"]) == SHARED_VECTOR_EXPECTED_EXEC_HASH_NO_CACHE
        assert SHARED_VECTOR_EXPECTED_EXEC_HASH != SHARED_VECTOR_EXPECTED_EXEC_HASH_NO_CACHE


def _write_skill_md_with_frontmatter(skill_dir: Path, frontmatter_lines: str, body: str = "# 正文\n") -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"---\n{frontmatter_lines}---\n{body}", encoding="utf-8")
    return skill_md


class TestReadDeclaredEntriesAndMutable:
    """SKILL.md 设备执行声明解析与校验（M2，plan §3.6——非法即抛，CLI 捕获拒绝审批）"""

    @staticmethod
    def _device_skill_md(skill_dir: Path, entry_decl: str, mutable_decl: str = "") -> Path:
        extra = f"metadata:\n  execution: device\n{entry_decl}{mutable_decl}"
        return _write_skill_md_with_frontmatter(
            skill_dir,
            f"name: p1\ndescription: d\nversion: 2.0.0\n{extra}\n")

    def test_parse_entries_and_mutable(self, tmp_path):
        skill_dir = tmp_path / "p1"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "scripts" / "flow.py").write_text("print(1)\n", encoding="utf-8")
        (skill_dir / "scripts" / "uicache.py").write_text("print(2)\n", encoding="utf-8")
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "ui-cache.json").write_text("{}\n", encoding="utf-8")
        self._device_skill_md(
            skill_dir,
            entry_decl="  entry:\n    - scripts/flow.py\n    - scripts/uicache.py\n",
            mutable_decl="  mutable:\n    - references/ui-cache.json\n")
        decl = gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")
        assert decl.execution == "device"
        assert decl.entries == ("scripts/flow.py", "scripts/uicache.py")
        assert decl.mutable == ("references/ui-cache.json",)
        assert decl.version == "2.0.0"

    def test_single_string_entry_normalized_to_list(self, tmp_path):
        """调研样本形态：metadata.entry 为单字符串（yaml 单值）→ 归一化为单元素列表"""
        skill_dir = tmp_path / "p1"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "scripts" / "flow.py").write_text("print(1)\n", encoding="utf-8")
        self._device_skill_md(skill_dir, entry_decl="  entry: scripts/flow.py\n")
        decl = gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")
        assert decl.entries == ("scripts/flow.py",)

    def test_backslash_and_dot_slash_prefix_normalized(self, tmp_path):
        skill_dir = tmp_path / "p1"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "scripts" / "flow.py").write_text("print(1)\n", encoding="utf-8")
        # flow/block 字符串列表 + ./ 前缀 + Windows 反斜杠（单反斜杠）归一化为正斜杠
        frontmatter = "name: p1\ndescription: d\nmetadata:\n  execution: device\n"
        # yaml 单引号字符串里反斜杠为字面字符：'./scripts\flow.py'（单反斜杠）
        frontmatter += "  entry: ['./scripts" + "\\" + "flow.py']\n"
        _write_skill_md_with_frontmatter(skill_dir, frontmatter)
        decl = gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")
        assert decl.entries == ("scripts/flow.py",)

    def test_no_declaration_defaults(self, tmp_path):
        """无 metadata（普通 skill）→ execution None/entries/mutable 空，不报错"""
        skill_dir = tmp_path / "p1"
        _write_skill_md_with_frontmatter(skill_dir, "name: p1\ndescription: d\n")
        decl = gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")
        assert decl.execution is None
        assert decl.entries == ()
        assert decl.mutable == ()

    def test_invalid_execution_value_raises(self, tmp_path):
        skill_dir = tmp_path / "p1"
        _write_skill_md_with_frontmatter(
            skill_dir, "name: p1\ndescription: d\nmetadata:\n  execution: cloud\n")
        with pytest.raises(gate.SkillDeviceDeclarationError, match="execution"):
            gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")

    def test_entry_file_must_exist(self, tmp_path):
        skill_dir = tmp_path / "p1"
        self._device_skill_md(skill_dir, entry_decl="  entry: [scripts/ghost.py]\n")
        with pytest.raises(gate.SkillDeviceDeclarationError, match="不存在"):
            gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")

    @pytest.mark.parametrize("bad_entry", [
        "/abs/path.py",           # 绝对路径
        "../../etc/passwd",       # .. 逃逸
        "C:/Windows/system32.py",  # 盘符绝对路径
        "scripts//flow.py",       # 空段
    ])
    def test_escaping_entry_paths_rejected(self, tmp_path, bad_entry):
        skill_dir = tmp_path / "p1"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "scripts" / "flow.py").write_text("print(1)\n", encoding="utf-8")
        self._device_skill_md(skill_dir, entry_decl=f"  entry: ['{bad_entry}']\n")
        with pytest.raises(gate.SkillDeviceDeclarationError):
            gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")

    def test_skill_md_cannot_be_mutable(self, tmp_path):
        skill_dir = tmp_path / "p1"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "scripts" / "flow.py").write_text("print(1)\n", encoding="utf-8")
        self._device_skill_md(
            skill_dir,
            entry_decl="  entry: [scripts/flow.py]\n",
            mutable_decl="  mutable: [SKILL.md]\n")
        with pytest.raises(gate.SkillDeviceDeclarationError, match="SKILL.md"):
            gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")

    def test_entry_mutable_intersection_rejected(self, tmp_path):
        """entries ∩ mutable = ∅（安全闭环，plan §3.6/风险 13）——入口脚本是被执行代码，
        必须受 exec_hash 锁定；允许 entry 入 mutable 即仅换 entry 脚本的篡改分发目录
        可通过设备 exec_hash 对账执行任意代码"""
        skill_dir = tmp_path / "p1"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "scripts" / "flow.py").write_text("print(1)\n", encoding="utf-8")
        self._device_skill_md(
            skill_dir,
            entry_decl="  entry: [scripts/flow.py]\n",
            mutable_decl="  mutable: [scripts/flow.py]\n")
        with pytest.raises(gate.SkillDeviceDeclarationError, match="相交"):
            gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")

    def test_mutable_over_limit_rejected(self, tmp_path):
        skill_dir = tmp_path / "p1"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "scripts" / "flow.py").write_text("print(1)\n", encoding="utf-8")
        too_many = ", ".join(f"'refs/{i}.json'" for i in range(9))
        self._device_skill_md(
            skill_dir,
            entry_decl="  entry: [scripts/flow.py]\n",
            mutable_decl=f"  mutable: [{too_many}]\n")
        with pytest.raises(gate.SkillDeviceDeclarationError, match="超过上限"):
            gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")

    def test_mutable_at_limit_allowed(self, tmp_path):
        skill_dir = tmp_path / "p1"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "scripts" / "flow.py").write_text("print(1)\n", encoding="utf-8")
        for i in range(8):
            (skill_dir / "refs").mkdir(parents=True, exist_ok=True)
            (skill_dir / "refs" / f"{i}.json").write_text("{}\n", encoding="utf-8")
        at_limit = ", ".join(f"'refs/{i}.json'" for i in range(8))
        self._device_skill_md(
            skill_dir,
            entry_decl="  entry: [scripts/flow.py]\n",
            mutable_decl=f"  mutable: [{at_limit}]\n")
        decl = gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")
        assert len(decl.mutable) == 8

    def test_missing_frontmatter_or_bad_type_rejected(self, tmp_path):
        skill_dir = tmp_path / "p1"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# 无 frontmatter\n", encoding="utf-8")
        with pytest.raises(gate.SkillDeviceDeclarationError):
            gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")
        _write_skill_md_with_frontmatter(skill_dir, "name: p1\nmetadata:\n  entry: [123]\n")
        with pytest.raises(gate.SkillDeviceDeclarationError):
            gate.read_declared_entries_and_mutable(skill_dir / "SKILL.md")


class TestReadApprovals:
    def test_missing_file_returns_empty(self, tmp_path):
        assert gate.read_approvals(tmp_path / "nope.json") == {}

    def test_corrupt_json_returns_empty(self, tmp_path):
        f = tmp_path / "approvals.json"
        f.write_text("{ not valid json !!", encoding="utf-8")
        assert gate.read_approvals(f) == {}

    def test_bad_structure_returns_empty(self, tmp_path):
        f = tmp_path / "approvals.json"
        f.write_text(json.dumps({"name1": {"computedHash": "x"}}), encoding="utf-8")
        assert gate.read_approvals(f) == {}

    def test_bad_entry_skipped(self, tmp_path):
        f = tmp_path / "approvals.json"
        payload = {"version": 1, "skills": {
            "good": {"computedHash": "h1", "skillMdHash": "m1"},
            "bad": {"computedHash": "no-md-hash"},
        }}
        f.write_text(json.dumps(payload), encoding="utf-8")
        result = gate.read_approvals(f)
        assert set(result.keys()) == {"good"}
        assert result["good"]["skillMdHash"] == "m1"

    def test_round_trip(self, tmp_path):
        f = tmp_path / "approvals.json"
        gate.write_approvals(f, {"p": {"computedHash": "h", "skillMdHash": "m"}})
        result = gate.read_approvals(f)
        assert result["p"]["computedHash"] == "h"


class TestWriteApprovalsAtomic:
    def test_tmp_file_complete_at_replace(self, monkeypatch, tmp_path):
        """os.replace 时刻临时文件内容必须已是完整合法 JSON（并发读不见半写）"""
        import os as _os
        f = tmp_path / "approvals.json"
        captured = {}
        real_replace = _os.replace

        def _capture_replace(src, dst):
            captured["content"] = Path(src).read_text(encoding="utf-8")
            real_replace(src, dst)

        monkeypatch.setattr(gate.os, "replace", _capture_replace)
        gate.write_approvals(f, {"p": {"computedHash": "h" * 64, "skillMdHash": "m" * 64}})
        payload = json.loads(captured["content"])
        assert payload["skills"]["p"]["computedHash"] == "h" * 64
        assert gate.read_approvals(f)["p"]["computedHash"] == "h" * 64

    def test_no_tmp_leftover(self, tmp_path):
        f = tmp_path / "approvals.json"
        gate.write_approvals(f, {})
        leftovers = [p for p in tmp_path.iterdir() if p.name != f.name]
        assert leftovers == []


class TestScanPluginDir:
    def test_approved_skill_included(self, plugin_env):
        skill_dir = plugin_env.repo_dir / "p1"
        _make_skill_md(skill_dir, "p1")
        _approve(plugin_env, skill_dir, "p1")
        scan = gate.scan_plugin_dir(plugin_env.repo_dir,
                                    gate.read_approvals(plugin_env.approvals_file), set())
        assert "p1" in scan.include
        assert scan.rejected == []

    def test_unapproved_skill_rejected(self, plugin_env):
        _make_skill_md(plugin_env.repo_dir / "p1", "p1")
        scan = gate.scan_plugin_dir(plugin_env.repo_dir, {}, set())
        assert scan.include == {}
        assert any(name == "p1" and reason == "未进入审批清单"
                   for name, reason in scan.rejected)

    def test_hash_mismatch_rejected(self, plugin_env):
        skill_dir = plugin_env.repo_dir / "p1"
        _make_skill_md(skill_dir, "p1")
        # 审批时 hash 正确，随后磁盘内容被篡改
        _approve(plugin_env, skill_dir, "p1")
        (skill_dir / "SKILL.md").write_text(
            "---\nname: p1\ndescription: 篡改\n---\n# 篡改\n", encoding="utf-8")
        scan = gate.scan_plugin_dir(plugin_env.repo_dir,
                                    gate.read_approvals(plugin_env.approvals_file), set())
        assert scan.include == {}
        assert any("hash" in reason for _, reason in scan.rejected)

    def test_builtin_name_conflict_rejected(self, plugin_env):
        skill_dir = plugin_env.repo_dir / "weather"
        _make_skill_md(skill_dir, "weather")
        _approve(plugin_env, skill_dir, "weather")
        scan = gate.scan_plugin_dir(plugin_env.repo_dir,
                                    gate.read_approvals(plugin_env.approvals_file), {"weather"})
        assert scan.include == {}
        assert any(name == "weather" and "内置" in reason for name, reason in scan.rejected)


class TestBuildBaseRegistrySources:
    def _builtin(self, tmp_path):
        builtin = tmp_path / "builtin"
        _make_skill_md(builtin / "b1", "b1", description="内置 b1")
        return builtin

    def test_approved_plugin_loaded_unapproved_invisible(self, plugin_env, tmp_path):
        builtin = self._builtin(tmp_path)
        _make_skill_md(plugin_env.repo_dir / "p-ok", "p-ok")
        _approve(plugin_env, plugin_env.repo_dir / "p-ok", "p-ok")
        _make_skill_md(plugin_env.repo_dir / "p-bad", "p-bad")  # 未审批

        sources = gate.build_base_registry_sources(builtin, plugin_env.cfg)
        merged = dict(sources.builtin_loader.skills)
        for loader in sources.plugin_loaders:
            merged.update(loader.skills)
        assert set(merged.keys()) == {"b1", "p-ok"}

    def test_disabled_loads_no_plugins(self, plugin_env, tmp_path):
        builtin = self._builtin(tmp_path)
        _make_skill_md(plugin_env.repo_dir / "p1", "p1")
        _approve(plugin_env, plugin_env.repo_dir / "p1", "p1")
        cfg = plugin_env.cfg.model_copy(update={"enabled": False})

        sources = gate.build_base_registry_sources(builtin, cfg)
        assert sources.plugin_loaders == []
        assert sources.plugin_hashes == {}

    def test_plugin_same_name_storage_overrides_repo(self, plugin_env, tmp_path):
        builtin = self._builtin(tmp_path)
        repo_dir = plugin_env.repo_dir / "dup"
        storage_dir = plugin_env.storage_dir / "dup"
        # 两目录内容一致（同一 hash 可同时通过审批）→ 插件间同名按目录链高优先级合并
        _make_skill_md(repo_dir, "dup", description="dup", body="相同内容")
        _make_skill_md(storage_dir, "dup", description="dup", body="相同内容")
        _approve(plugin_env, storage_dir, "dup")

        sources = gate.build_base_registry_sources(builtin, plugin_env.cfg)
        assert len(sources.plugin_loaders) == 2
        # 高优先级（storage）覆盖低优先级（repo）：合并结果指向 storage 目录
        merged = dict(sources.builtin_loader.skills)
        for loader in sources.plugin_loaders:
            merged.update(loader.skills)
        assert merged["dup"].dir == storage_dir
        assert sources.plugin_hashes["dup"][0] == gate.compute_skill_dir_hash(storage_dir)

    def test_plugin_builtin_conflict_rejected_at_gate(self, plugin_env, tmp_path):
        builtin = self._builtin(tmp_path)
        # 插件与内置 b1 同名：gate 拒绝（内置优先）
        conflict_dir = plugin_env.repo_dir / "b1"
        _make_skill_md(conflict_dir, "b1", description="插件冒充 b1")
        _approve(plugin_env, conflict_dir, "b1")

        sources = gate.build_base_registry_sources(builtin, plugin_env.cfg)
        merged = dict(sources.builtin_loader.skills)
        for loader in sources.plugin_loaders:
            merged.update(loader.skills)
        assert merged["b1"].description == "内置 b1"
        assert "b1" not in sources.plugin_hashes


class TestPluginSignature:
    def test_disabled_returns_empty(self, plugin_env):
        cfg = plugin_env.cfg.model_copy(update={"enabled": False})
        assert gate.plugin_signature(cfg) == ()

    def test_stable_when_unchanged(self, plugin_env):
        _make_skill_md(plugin_env.repo_dir / "p1", "p1")
        _approve(plugin_env, plugin_env.repo_dir / "p1", "p1")
        assert gate.plugin_signature(plugin_env.cfg) == gate.plugin_signature(plugin_env.cfg)

    def test_approvals_change_changes_signature(self, plugin_env):
        _make_skill_md(plugin_env.repo_dir / "p1", "p1")
        _approve(plugin_env, plugin_env.repo_dir / "p1", "p1")
        s1 = gate.plugin_signature(plugin_env.cfg)
        approvals = gate.read_approvals(plugin_env.approvals_file)
        approvals["p1"]["note"] = "changed note " + "x" * 50
        gate.write_approvals(plugin_env.approvals_file, approvals)
        assert gate.plugin_signature(plugin_env.cfg) != s1

    def test_new_plugin_subdir_changes_signature(self, plugin_env):
        _make_skill_md(plugin_env.repo_dir / "p1", "p1")
        _approve(plugin_env, plugin_env.repo_dir / "p1", "p1")
        s1 = gate.plugin_signature(plugin_env.cfg)
        _make_skill_md(plugin_env.storage_dir / "p2", "p2")
        assert gate.plugin_signature(plugin_env.cfg) != s1

    def test_missing_dirs_and_manifest_signature_constant(self, plugin_env):
        """enabled=True 但插件目录/清单不存在 → 签名恒定（行为等同永久缓存）"""
        s1 = gate.plugin_signature(plugin_env.cfg)
        s2 = gate.plugin_signature(plugin_env.cfg)
        assert s1 == s2
        assert s1 != ()
        # 各段为 missing 标记
        assert any("missing" in str(part) for tup in s1 for part in (tup if isinstance(tup, tuple) else (tup,)))


class TestSkillLoaderIncludeRunInit:
    def test_include_filters_registration(self, tmp_path):
        _make_skill_md(tmp_path / "a", "a")
        _make_skill_md(tmp_path / "b", "b")
        loader = gate.SkillLoader(tmp_path, include={"a"})
        assert set(loader.skills.keys()) == {"a"}

    def test_run_init_false_skips_init_tables(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(gate.SkillLoader, "_init_skill_tables",
                            lambda self: calls.append(self.skills_dir))
        _make_skill_md(tmp_path / "a", "a")
        gate.SkillLoader(tmp_path, include={"a"}, run_init=False)
        assert calls == []

    def test_run_init_default_calls_init_tables(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(gate.SkillLoader, "_init_skill_tables",
                            lambda self: calls.append(self.skills_dir))
        _make_skill_md(tmp_path / "a", "a")
        gate.SkillLoader(tmp_path)
        assert calls == [tmp_path]
