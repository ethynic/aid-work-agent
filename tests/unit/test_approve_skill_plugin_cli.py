# -*- coding: utf-8 -*-
"""审批 CLI（scripts/approve_skill_plugin.py）单元测试（外部 Skill 插件 M1，CR P2 补齐；
M2 扩展 entries/exec_hash/version/mutable 设备执行项写入与校验拒绝）

覆盖：--approve 的 note 必填与不存在名报错、--revoke 不存在名报错与正常撤销、
--scan 输出、_find_candidate 高优先级目录（storage 覆盖 repo）优先；
M2：--approve 对 device 技能写入设备执行项（entries/exec_hash/version/mutable）、
entries/mutable 相交与声明非法拒绝审批、device 无 entry 拒绝、--scan 展示设备执行项。
全部 tmp 目录构造，不触碰真实 storage/。
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config.settings import SkillPluginsConfig
from src.core import skill_plugin_gate as gate

pytestmark = pytest.mark.skills


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "approve_skill_plugin",
        Path(__file__).resolve().parents[2] / "scripts" / "approve_skill_plugin.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_skill_md(skill_dir: Path, name: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: test\n"
        "---\n"
        f"# {name}\n",
        encoding="utf-8",
    )


def _make_device_skill_md(skill_dir: Path, name: str, entries: list, mutable: list = None,
                          execution: str = "device") -> None:
    """构造带设备执行声明的 SKILL.md（entry/mutable 文件实际落盘，供存在性校验）"""
    skill_dir.mkdir(parents=True, exist_ok=True)
    for rel in list(entries or []) + list(mutable or []):
        p = skill_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# payload\n", encoding="utf-8")
    lines = [f"name: {name}", "description: test", "version: 2.0.0", "metadata:"]
    if execution:
        lines.append(f"  execution: {execution}")
    if entries is not None:
        lines.append("  entry: [" + ", ".join(f"'{e}'" for e in entries) + "]")
    if mutable is not None:
        lines.append("  mutable: [" + ", ".join(f"'{m}'" for m in mutable) + "]")
    (skill_dir / "SKILL.md").write_text("---\n" + "\n".join(lines) + "\n---\n# 正文\n", encoding="utf-8")


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(gate, "configured_storage_root", lambda: storage_root)
    repo_dir = tmp_path / "repo-plugins"
    repo_dir.mkdir(parents=True)
    cfg = SkillPluginsConfig(
        enabled=True, repo_dir=str(repo_dir),
        storage_subdir="skills/plugins", approvals_subpath="skills/plugin-approvals.json",
    )
    storage_dir = storage_root / "skills" / "plugins"
    storage_dir.mkdir(parents=True)
    cli = _load_cli()
    return SimpleNamespace(cfg=cfg, repo_dir=repo_dir, storage_dir=storage_dir,
                           approvals_file=storage_root / "skills" / "plugin-approvals.json",
                           cli=cli)


class TestApprove:
    def test_note_required_exit_2(self, cli_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", [
            "approve_skill_plugin.py", "--approve", "x",
            "--approvals", str(cli_env.approvals_file),
        ])
        assert cli_env.cli.main() == 2
        assert "--note" in capsys.readouterr().err

    def test_unknown_name_exit_1(self, cli_env, capsys):
        rc = cli_env.cli.cmd_approve("nope", "note", cli_env.approvals_file, cli_env.cfg)
        assert rc == 1
        assert "未找到" in capsys.readouterr().err

    def test_approve_writes_hashes_and_note(self, cli_env):
        _make_skill_md(cli_env.repo_dir / "demo", "demo")
        rc = cli_env.cli.cmd_approve("demo", "来源: workbuddy", cli_env.approvals_file, cli_env.cfg)
        assert rc == 0
        approvals = gate.read_approvals(cli_env.approvals_file)
        entry = approvals["demo"]
        assert entry["note"] == "来源: workbuddy"
        assert entry["computedHash"] == gate.compute_skill_dir_hash(cli_env.repo_dir / "demo")
        assert entry["skillMdHash"] == gate.compute_skill_md_hash(cli_env.repo_dir / "demo")


class TestApproveDeviceExecutionEntry:
    """M2（plan §3.6/§6）：--approve 对 device 技能写入设备执行项四字段；非法声明拒绝审批"""

    def test_approve_device_skill_writes_entries_exec_hash_version_mutable(self, cli_env):
        skill_dir = cli_env.repo_dir / "device-demo"
        _make_device_skill_md(skill_dir, "device-demo",
                              entries=["scripts/flow.py", "scripts/uicache.py"],
                              mutable=["references/ui-cache.json"])
        rc = cli_env.cli.cmd_approve("device-demo", "来源: workbuddy", cli_env.approvals_file, cli_env.cfg)
        assert rc == 0
        entry = gate.read_approvals(cli_env.approvals_file)["device-demo"]
        assert entry["entries"] == ["scripts/flow.py", "scripts/uicache.py"]
        assert entry["mutable"] == ["references/ui-cache.json"]
        assert entry["version"] == "2.0.0"
        assert entry["exec_hash"] == gate.compute_skill_exec_hash(
            skill_dir, ["references/ui-cache.json"])
        # 知识层两 hash 语义不变（M1 基线兼容：computedHash 仍含 mutable 文件）
        assert entry["computedHash"] == gate.compute_skill_dir_hash(skill_dir)
        assert entry["skillMdHash"] == gate.compute_skill_md_hash(skill_dir)

    def test_approve_server_skill_keeps_m1_shape_without_device_entry(self, cli_env):
        """非 device 技能（未声明 execution/声明 server）不写设备执行项（从紧：无 entries）"""
        for name, execution in [("plain-demo", None), ("server-demo", "server")]:
            skill_dir = cli_env.repo_dir / name
            _make_device_skill_md(skill_dir, name, entries=["scripts/run.py"],
                                  mutable=None, execution=execution)
            rc = cli_env.cli.cmd_approve(name, "n", cli_env.approvals_file, cli_env.cfg)
            assert rc == 0
        approvals = gate.read_approvals(cli_env.approvals_file)
        assert "entries" not in approvals["plain-demo"]
        assert "exec_hash" not in approvals["plain-demo"]
        assert "exec_hash" not in approvals["server-demo"]
        assert "note" in approvals["server-demo"]

    def test_approve_device_skill_without_entry_rejected(self, cli_env, capsys):
        """execution=device 但无 metadata.entry 声明 → CLI 报错拒绝审批设备执行项"""
        skill_dir = cli_env.repo_dir / "device-noentry"
        _make_device_skill_md(skill_dir, "device-noentry", entries=None, mutable=None)
        rc = cli_env.cli.cmd_approve("device-noentry", "n", cli_env.approvals_file, cli_env.cfg)
        assert rc == 1
        assert "metadata.entry" in capsys.readouterr().err
        # 清单未被写入
        assert "device-noentry" not in gate.read_approvals(cli_env.approvals_file)

    def test_approve_device_skill_with_empty_entry_list_rejected(self, cli_env, capsys):
        skill_dir = cli_env.repo_dir / "device-emptyentry"
        _make_device_skill_md(skill_dir, "device-emptyentry", entries=[], mutable=None)
        rc = cli_env.cli.cmd_approve("device-emptyentry", "n", cli_env.approvals_file, cli_env.cfg)
        assert rc == 1
        assert "metadata.entry" in capsys.readouterr().err

    def test_approve_entry_mutable_intersection_rejected(self, cli_env, capsys):
        """entries ∩ mutable 相交（入口脚本入 mutable，plan §3.6/风险 13）→ 拒绝审批"""
        skill_dir = cli_env.repo_dir / "device-intersect"
        _make_device_skill_md(skill_dir, "device-intersect",
                              entries=["scripts/flow.py"],
                              mutable=["scripts/flow.py", "references/ui-cache.json"])
        rc = cli_env.cli.cmd_approve("device-intersect", "n", cli_env.approvals_file, cli_env.cfg)
        assert rc == 1
        assert "拒绝审批" in capsys.readouterr().err
        assert "device-intersect" not in gate.read_approvals(cli_env.approvals_file)

    def test_approve_skill_md_in_mutable_rejected(self, cli_env, capsys):
        skill_dir = cli_env.repo_dir / "device-mdmutable"
        _make_device_skill_md(skill_dir, "device-mdmutable",
                              entries=["scripts/flow.py"],
                              mutable=["SKILL.md"])
        rc = cli_env.cli.cmd_approve("device-mdmutable", "n", cli_env.approvals_file, cli_env.cfg)
        assert rc == 1
        assert "device-mdmutable" not in gate.read_approvals(cli_env.approvals_file)

    def test_approve_escaping_paths_rejected(self, cli_env, capsys):
        """逃逸 skill 目录的声明（../ 与绝对路径）→ 拒绝审批"""
        skill_dir = cli_env.repo_dir / "device-escape"
        _make_device_skill_md(skill_dir, "device-escape",
                              entries=["../../outside.py"], mutable=None)
        rc = cli_env.cli.cmd_approve("device-escape", "n", cli_env.approvals_file, cli_env.cfg)
        assert rc == 1
        assert "device-escape" not in gate.read_approvals(cli_env.approvals_file)

    def test_approve_prints_device_entry_for_review(self, cli_env, capsys):
        """--approve 打印 entries/exec_hash/version/mutable 供审批人复核"""
        skill_dir = cli_env.repo_dir / "device-demo"
        _make_device_skill_md(skill_dir, "device-demo",
                              entries=["scripts/flow.py"], mutable=["references/ui-cache.json"])
        rc = cli_env.cli.cmd_approve("device-demo", "n", cli_env.approvals_file, cli_env.cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert "scripts/flow.py" in out
        assert "exec_hash:" in out
        assert "references/ui-cache.json" in out

    def test_scan_shows_device_entry_and_legacy_marker(self, cli_env, capsys):
        """--scan：已审批 device 技能展示 entries/exec_hash 摘要；存量条目（M1 时期无
        entries）标注「[设备执行未审批]」——device 路由技能派发侧将被拒绝（对账在
        routing 测试断言，plan §6）"""
        skill_dir = cli_env.repo_dir / "device-demo"
        _make_device_skill_md(skill_dir, "device-demo", entries=["scripts/flow.py"], mutable=None)
        cli_env.cli.cmd_approve("device-demo", "n", cli_env.approvals_file, cli_env.cfg)

        _make_skill_md(cli_env.repo_dir / "legacy-demo", "legacy-demo")
        cli_env.cli.cmd_approve("legacy-demo", "n", cli_env.approvals_file, cli_env.cfg)
        # 旧格式条目：手工剥去 M2 字段，模拟 M1 时期审批的存量清单
        approvals = gate.read_approvals(cli_env.approvals_file)
        approvals["legacy-demo"] = {k: v for k, v in approvals["legacy-demo"].items()
                                    if k not in ("entries", "exec_hash", "version", "mutable")}
        gate.write_approvals(cli_env.approvals_file, approvals)

        assert cli_env.cli.cmd_scan(cli_env.approvals_file, cli_env.cfg) == 0
        out = capsys.readouterr().out
        assert "[已审批] device-demo" in out
        assert "设备执行: entries=scripts/flow.py" in out
        assert "[已审批] legacy-demo" in out
        assert "[设备执行未审批]" in out

    def test_scan_lists_unapproved_device_candidate(self, cli_env, capsys):
        """未审批 device 候选行展示设备执行声明（entry/mutable）"""
        skill_dir = cli_env.repo_dir / "device-cand"
        _make_device_skill_md(skill_dir, "device-cand",
                              entries=["scripts/flow.py"], mutable=["references/ui-cache.json"])
        assert cli_env.cli.cmd_scan(cli_env.approvals_file, cli_env.cfg) == 0
        out = capsys.readouterr().out
        assert "[未审批] device-cand" in out
        assert "[设备执行" in out
        assert "scripts/flow.py" in out


class TestRevoke:
    def test_unknown_name_exit_1(self, cli_env, capsys):
        rc = cli_env.cli.cmd_revoke("nope", cli_env.approvals_file)
        assert rc == 1
        assert "不在审批清单" in capsys.readouterr().err

    def test_revoke_removes_entry(self, cli_env):
        _make_skill_md(cli_env.repo_dir / "demo", "demo")
        cli_env.cli.cmd_approve("demo", "n", cli_env.approvals_file, cli_env.cfg)
        assert cli_env.cli.cmd_revoke("demo", cli_env.approvals_file) == 0
        assert gate.read_approvals(cli_env.approvals_file) == {}


class TestFindCandidate:
    def test_storage_dir_wins_over_repo(self, cli_env):
        _make_skill_md(cli_env.repo_dir / "a-demo", "demo")
        _make_skill_md(cli_env.storage_dir / "b-demo", "demo")
        found = cli_env.cli._find_candidate("demo", cli_env.cfg)
        assert found == cli_env.storage_dir / "b-demo"

    def test_no_candidate_returns_none(self, cli_env):
        assert cli_env.cli._find_candidate("nothing", cli_env.cfg) is None


class TestScan:
    def test_scan_lists_approved_status(self, cli_env, capsys):
        _make_skill_md(cli_env.repo_dir / "demo", "demo")
        cli_env.cli.cmd_approve("demo", "n", cli_env.approvals_file, cli_env.cfg)
        assert cli_env.cli.cmd_scan(cli_env.approvals_file, cli_env.cfg) == 0
        out = capsys.readouterr().out
        assert "[已审批] demo" in out

    def test_scan_lists_unapproved_with_hashes(self, cli_env, capsys):
        _make_skill_md(cli_env.repo_dir / "cand", "cand")
        assert cli_env.cli.cmd_scan(cli_env.approvals_file, cli_env.cfg) == 0
        out = capsys.readouterr().out
        assert "[未审批] cand" in out
        assert "computedHash:" in out

    def test_scan_reports_hash_mismatch(self, cli_env, capsys):
        skill_dir = cli_env.repo_dir / "demo"
        _make_skill_md(skill_dir, "demo")
        cli_env.cli.cmd_approve("demo", "n", cli_env.approvals_file, cli_env.cfg)
        # 篡改内容（追加字节）→ 目录 hash 与审批时不符
        (skill_dir / "SKILL.md").write_text(
            (skill_dir / "SKILL.md").read_text(encoding="utf-8") + "\n篡改行\n",
            encoding="utf-8",
        )
        cli_env.cli.cmd_scan(cli_env.approvals_file, cli_env.cfg)
        out = capsys.readouterr().out
        assert "与审批时不符" in out
