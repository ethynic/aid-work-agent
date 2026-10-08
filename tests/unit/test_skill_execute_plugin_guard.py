"""skill_executor 插件执行边界单元测试（外部 Skill 插件 M1 知识层）

覆盖（docs/plans/plan-external-skill-plugin-m1.md §3.1/§3.9/§6）：
- 未声明 device 的插件 skill 走 execute_skill_command 被拦截（success=False、
  文案含「请勿重试」）；
- 拦截先于 _process_command 路径替换与 _execute_command 子进程创建
  （monkeypatch 断言两者未被调用——插件 scripts/ 相对路径不会被替换为插件
  脚本绝对路径）；
- execute_skill_script 第二入口同样拦截（MCP executor 路径
  src/mcp/executor.py:162 经 execute_skill_command 进入同一拦截；本文件为
  构造 registry 的单测，生产 MCP 全局单例不加载插件 → Unknown skill，
  fail-closed）；
- execution=device（插件与内置）→ 设备链路占位文案；插件+device → device 优先；
- 内置 server/无声明 skill 不被拦截；
- use_skill 手册尾部含插件/设备执行提示。

全部 tmp 目录构造，不触碰真实 storage/。
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core import skill_plugin_gate as gate
from src.core.skill_executor import SkillExecutor
from src.core.skill_registry import SkillRegistry
from src.config.settings import SkillPluginsConfig
from src.tools.skill.use_skill_tool import UseSkillTool

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
                          approvals_file=storage_root / "skills" / "plugin-approvals.json")
    return env


def _approve(env, skill_dir: Path, name: str):
    approvals = gate.read_approvals(env.approvals_file)
    approvals[name] = {
        "computedHash": gate.compute_skill_dir_hash(skill_dir),
        "skillMdHash": gate.compute_skill_md_hash(skill_dir),
        "note": "test",
    }
    gate.write_approvals(env.approvals_file, approvals)


@pytest.fixture
def registry_env(plugin_env, tmp_path):
    """内置 plain/server/device + 插件 plain/device/带脚本（均审批）"""
    builtin = tmp_path / "builtin"
    _make_skill_md(builtin / "builtin-plain", "builtin-plain")
    _make_skill_md(builtin / "builtin-server", "builtin-server",
                   extra_frontmatter="metadata:\n  execution: server\n")
    _make_skill_md(builtin / "builtin-device", "builtin-device",
                   extra_frontmatter="metadata:\n  execution: device\n")

    _make_skill_md(plugin_env.repo_dir / "plugin-plain", "plugin-plain",
                   body="插件手册：运行 bash scripts/run.sh")
    _approve(plugin_env, plugin_env.repo_dir / "plugin-plain", "plugin-plain")

    _make_skill_md(plugin_env.repo_dir / "plugin-device", "plugin-device",
                   extra_frontmatter="metadata:\n  execution: device\n")
    _approve(plugin_env, plugin_env.repo_dir / "plugin-device", "plugin-device")

    scripts_dir = plugin_env.repo_dir / "plugin-scripts" / "scripts"
    scripts_dir.mkdir(parents=True)
    _make_skill_md(plugin_env.repo_dir / "plugin-scripts", "plugin-scripts",
                   body="插件手册：运行 bash scripts/run.sh")
    (scripts_dir / "run.sh").write_text("#!/bin/bash\necho pwned\n", encoding="utf-8")
    _approve(plugin_env, plugin_env.repo_dir / "plugin-scripts", "plugin-scripts")

    sources = gate.build_base_registry_sources(builtin, plugin_env.cfg)
    registry = SkillRegistry()
    registry.load_from_sources(sources.builtin_loader, sources.plugin_loaders,
                               sources.plugin_hashes)
    return SimpleNamespace(registry=registry, builtin=builtin)


def _executor(registry_env, tmp_path):
    return SkillExecutor(registry_env.registry, workspace=tmp_path / "ws")


class TestPluginExecutionBlocked:
    def test_plugin_skill_blocked(self, registry_env, tmp_path):
        executor = _executor(registry_env, tmp_path)
        result = asyncio.run(executor.execute_skill_command("plugin-plain", "echo hi"))
        assert result.success is False
        assert "外部插件" in result.stderr or "外部 Skill 插件" in result.stderr
        assert "请勿重试" in result.stderr

    def test_interception_precedes_process_and_subprocess(self, registry_env, tmp_path, monkeypatch):
        """拦截先于 _process_command（scripts/ 路径替换）与 _execute_command（子进程）"""
        executor = _executor(registry_env, tmp_path)
        process_spy = MagicMock()
        execute_mock = AsyncMock()
        monkeypatch.setattr(executor, "_process_command", process_spy)
        monkeypatch.setattr(executor, "_execute_command", execute_mock)

        result = asyncio.run(executor.execute_skill_command(
            "plugin-scripts", "bash scripts/run.sh"))
        assert result.success is False
        process_spy.assert_not_called()
        execute_mock.assert_not_called()

    def test_execute_skill_script_second_entry_blocked(self, registry_env, tmp_path, monkeypatch):
        executor = _executor(registry_env, tmp_path)
        execute_mock = AsyncMock()
        monkeypatch.setattr(executor, "_execute_command", execute_mock)
        result = asyncio.run(executor.execute_skill_script("plugin-scripts", "run.sh"))
        assert result.success is False
        assert "外部 Skill 插件" in result.stderr
        assert "请勿重试" in result.stderr
        execute_mock.assert_not_called()


class TestDevicePlaceholder:
    def test_builtin_device_returns_placeholder(self, registry_env, tmp_path):
        executor = _executor(registry_env, tmp_path)
        result = asyncio.run(executor.execute_skill_command("builtin-device", "echo hi"))
        assert result.success is False
        assert "设备执行" in result.stderr
        assert "metadata.execution=device" in result.stderr
        assert "请勿重试" in result.stderr

    def test_plugin_device_device_message_wins(self, registry_env, tmp_path):
        """插件 + device → 判定顺序 1（device 占位）优先于插件拦截"""
        executor = _executor(registry_env, tmp_path)
        result = asyncio.run(executor.execute_skill_command("plugin-device", "echo hi"))
        assert result.success is False
        assert "设备执行" in result.stderr
        assert "外部 Skill 插件" not in result.stderr

    def test_execute_skill_script_device_placeholder(self, registry_env, tmp_path):
        executor = _executor(registry_env, tmp_path)
        result = asyncio.run(executor.execute_skill_script("builtin-device", "x.py"))
        assert result.success is False
        assert "设备执行" in result.stderr


class TestBuiltinNotBlocked:
    @pytest.mark.parametrize("skill_name", ["builtin-plain", "builtin-server"])
    def test_builtin_passes_gate(self, registry_env, tmp_path, monkeypatch, skill_name):
        """内置无声明/声明 server → 现状行为不变（放行到 _execute_command）"""
        from src.core.skill_executor import ExecutionResult
        executor = _executor(registry_env, tmp_path)
        ok = ExecutionResult(success=True, stdout="ok", stderr="", exit_code=0, duration=0.0)
        monkeypatch.setattr(executor, "_execute_command", AsyncMock(return_value=ok))
        result = asyncio.run(executor.execute_skill_command(skill_name, "echo hi"))
        assert result.success is True

    def test_invalid_execution_value_fails_safe(self, plugin_env, tmp_path, monkeypatch):
        """metadata.execution 非法值 → 按 server 处理（fail-safe 不拦截）"""
        from src.core.skill_executor import ExecutionResult
        builtin = tmp_path / "builtin"
        _make_skill_md(builtin / "weird", "weird",
                       extra_frontmatter="metadata:\n  execution: cloud\n")
        registry = SkillRegistry()
        registry.load_from_sources(gate.SkillLoader(builtin), [], {})
        assert registry.get_execution_decl("weird") is None

        executor = SkillExecutor(registry, workspace=tmp_path / "ws")
        ok = ExecutionResult(success=True, stdout="ok", stderr="", exit_code=0, duration=0.0)
        monkeypatch.setattr(executor, "_execute_command", AsyncMock(return_value=ok))
        result = asyncio.run(executor.execute_skill_command("weird", "echo hi"))
        assert result.success is True


class TestUseSkillBoundaryHints:
    def test_plugin_manual_has_hint(self, registry_env):
        tool = UseSkillTool(registry_env.registry)
        result = asyncio.run(tool.execute(skill="plugin-plain"))
        assert result["success"] is True
        assert "外部 Skill 插件" in result["content"]
        assert "请勿调用 skill_execute" in result["content"]

    def test_device_manual_has_hint(self, registry_env):
        tool = UseSkillTool(registry_env.registry)
        result = asyncio.run(tool.execute(skill="plugin-device"))
        assert result["success"] is True
        assert "设备执行" in result["content"]
        assert "请勿调用 skill_execute" in result["content"]

    def test_builtin_plain_no_hint(self, registry_env):
        tool = UseSkillTool(registry_env.registry)
        result = asyncio.run(tool.execute(skill="builtin-plain"))
        assert result["success"] is True
        assert "外部 Skill 插件" not in result["content"]
        assert "设备执行链路尚未开放" not in result["content"]


class TestUseSkillTamperedPluginMessage:
    """篡改插件的内容门失败文案（M1 CR P2）：与通用 not found 区分。"""

    def test_tampered_plugin_returns_specific_pause_message(self, registry_env):
        skill_dir = registry_env.registry.get("plugin-plain").dir
        (skill_dir / "SKILL.md").write_text(
            "---\nname: plugin-plain\ndescription: 被篡改\n---\n# 篡改后内容\n",
            encoding="utf-8",
        )
        tool = UseSkillTool(registry_env.registry)
        result = asyncio.run(tool.execute(skill="plugin-plain"))
        assert result["success"] is False
        assert "与审批时不符" in result["error"]
        assert "暂不可用" in result["error"]
        # 不给 available_skills 诱导 LLM 重试同名加载
        assert "available_skills" not in result

    def test_genuine_not_found_keeps_generic_message(self, registry_env):
        tool = UseSkillTool(registry_env.registry)
        result = asyncio.run(tool.execute(skill="no-such-skill"))
        assert result["success"] is False
        assert "not found" in result["error"]
        assert "available_skills" in result


class TestM2MessageCopyRegression:
    """M2 文案更新回归（plan §3.7）：删除「规划 M2/即将支持」类过时表述，按区分码
    给出真实原因。M1 既有断言（上方各类）不改不删继续生效——本类只补 M1 断言
    未覆盖的负向断言（约束表 §3.7「既有断言优先于措辞偏好」）"""

    def test_plugin_blocked_message_has_no_stale_m2_wording(self, registry_env, tmp_path):
        executor = _executor(registry_env, tmp_path)
        result = asyncio.run(executor.execute_skill_command("plugin-plain", "echo hi"))
        assert result.success is False
        for stale in ("规划 M2", "即将支持", "尚未开放"):
            assert stale not in result.stderr
        # §3.7 substring 约束表全部必含字样（既有断言 :118-119 的超集复核：
        # 文案须同时提及正确的配置键 metadata.execution=device）
        assert "外部 Skill 插件" in result.stderr
        assert "metadata.execution=device" in result.stderr
        assert "请勿重试" in result.stderr

    def test_device_disabled_message_real_reason_with_code(self, registry_env, tmp_path):
        """device 插件未放行（开关关，默认配置）→ DEVICE_EXECUTION_DISABLED 区分码 +
        真实原因（管理员配置），无过时表述"""
        executor = _executor(registry_env, tmp_path)
        result = asyncio.run(executor.execute_skill_command("plugin-device", "echo hi"))
        assert result.success is False
        assert result.error == "DEVICE_EXECUTION_DISABLED"
        for stale in ("规划 M2", "即将支持", "尚未开放"):
            assert stale not in result.stderr
        assert "设备执行" in result.stderr
        assert "请勿重试" in result.stderr

    def test_use_skill_plugin_hint_no_stale_wording(self, registry_env):
        tool = UseSkillTool(registry_env.registry)
        result = asyncio.run(tool.execute(skill="plugin-plain"))
        assert result["success"] is True
        for stale in ("规划 M2", "即将支持", "尚未开放"):
            assert stale not in result["content"]
        assert "外部 Skill 插件" in result["content"]
        assert "请勿调用 skill_execute" in result["content"]

    def test_use_skill_device_hint_no_stale_wording(self, registry_env):
        tool = UseSkillTool(registry_env.registry)
        result = asyncio.run(tool.execute(skill="plugin-device"))
        assert result["success"] is True
        for stale in ("规划 M2", "即将支持", "尚未开放"):
            assert stale not in result["content"]
        assert "设备执行" in result["content"]
        assert "请勿调用 skill_execute" in result["content"]
