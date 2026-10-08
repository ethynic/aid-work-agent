# -*- coding: utf-8 -*-
"""插件 skill 设备执行路由单元测试（外部 Skill 插件 M2 执行路由，plan §3.5/§3.7/§6）

覆盖：
- evaluate_device_execution 放行矩阵（§3.5 判定 a–d × 开关 × 审批形态）：
  内置 server/无声明 → ROUTE_SERVER；插件未声明 device → PLUGIN_BLOCKED；
  内置 device → BUILTIN_DEVICE_UNSUPPORTED；插件 device 审批缺 entries/exec_hash
  （含 M1 时期存量条目与清单缺失/损坏）→ PLUGIN_NOT_EXECUTABLE；
  开关关 → DEVICE_EXECUTION_DISABLED；全满足 → DEVICE_READY（entries/exec_hash/version）；
- TTL 半状态（registry._plugin_hashes 缺名）fail-closed；
- 审批数据二次读取 fail-closed：清单缺失/损坏/条目缺 entries → PLUGIN_NOT_EXECUTABLE；
- execute_skill_command 路由分支：放行 → dispatch（payload/identity 透传，
  _execute_command 不被调用，stdin_content 恒不透传）；
  files 非空拒绝（INVALID_DEVICE_INPUT）；命令不合法（INVALID_DEVICE_COMMAND 附 entries 清单）；
- parse_device_skill_command：解释器前缀剥离、./ 归一、引号/多空格、白名单外 entry
  （附清单文案）、args 上限（数量/单参长度/总长）；
- use_skill 提示：放行 → 设备执行说明（含「设备执行」、不含「请勿」）；
  未放行（PLUGIN_NOT_EXECUTABLE）→ 拦截提示（含「设备执行」「请勿调用 skill_execute」）。

全部 tmp 目录构造，不触碰真实 storage/；settings 开关经 monkeypatch（自动还原）。
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config.settings import SkillPluginsConfig, settings
from src.core import skill_plugin_gate as gate
from src.core.skill_executor import ExecutionResult, SkillExecutor
from src.core.skill_registry import SkillRegistry
from src.local_tools.skill_runner_proxy import (
    DeviceCommandLimits,
    DeviceCommandParse,
    evaluate_device_execution,
    parse_device_skill_command,
)
from src.tools.skill.use_skill_tool import UseSkillTool

pytestmark = pytest.mark.skills


def _write_skill_md(skill_dir: Path, name: str, *, execution: str = None,
                    entry: str = None, mutable: str = None, version: str = "2.0.0",
                    body: str = "") -> Path:
    """构造带设备执行声明的 SKILL.md（entry/mutable 声明的文件实际落盘）"""
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"name: {name}", f"description: {name}", f"version: {version}"]
    meta = []
    if execution is not None:
        meta.append(f"  execution: {execution}")
    if entry is not None:
        for item in [entry] if isinstance(entry, str) else list(entry):
            p = skill_dir / item
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# script\n", encoding="utf-8")
        items = [entry] if isinstance(entry, str) else list(entry)
        meta.append("  entry: [" + ", ".join(f"'{e}'" for e in items) + "]")
    if mutable is not None:
        for item in [mutable] if isinstance(mutable, str) else list(mutable):
            p = skill_dir / item
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")
        items = [mutable] if isinstance(mutable, str) else list(mutable)
        meta.append("  mutable: [" + ", ".join(f"'{m}'" for m in items) + "]")
    if meta:
        lines.append("metadata:")
        lines.extend(meta)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n" + "\n".join(lines) + "\n---\n" + (body or f"# {name}\n{name} 正文。\n"),
        encoding="utf-8",
    )
    return skill_md


def _approve_device_skill(env, skill_dir: Path, name: str, *, entries=None, mutable=None,
                          version="2.0.0", legacy=False, approvals_file=None):
    """写审批条目：legacy=True 写 M1 时期形态（无 entries/exec_hash/version/mutable）"""
    file = approvals_file or env.approvals_file
    approvals = gate.read_approvals(file)
    entry = {
        "computedHash": gate.compute_skill_dir_hash(skill_dir),
        "skillMdHash": gate.compute_skill_md_hash(skill_dir),
        "note": "test",
    }
    if not legacy:
        entry.update({
            "entries": list(entries),
            "exec_hash": gate.compute_skill_exec_hash(skill_dir, list(mutable or [])),
            "version": version,
            "mutable": list(mutable or []),
        })
    approvals[name] = entry
    gate.write_approvals(file, approvals)


@pytest.fixture
def plugin_env(monkeypatch, tmp_path):
    """tmp 插件环境（与 M1 guard 测试同构：monkeypatch gate.configured_storage_root——
    evaluate_device_execution 判定 d 的 gate.resolve_approvals_file() 经同一 storage
    根解析 approvals 路径，与 fixture approvals_file 汇聚到同一清单文件）"""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(gate, "configured_storage_root", lambda: storage_root)
    repo_dir = tmp_path / "repo-plugins"
    repo_dir.mkdir(parents=True)
    cfg = SkillPluginsConfig(
        enabled=True, repo_dir=str(repo_dir),
        storage_subdir="skills/plugins", approvals_subpath="skills/plugin-approvals.json",
    )
    return SimpleNamespace(cfg=cfg, repo_dir=repo_dir,
                           approvals_file=storage_root / "skills" / "plugin-approvals.json")


@pytest.fixture
def device_env(plugin_env, tmp_path, monkeypatch):
    """内置 plain/device + 插件 plain / device-legacy（M1 形态审批）/ device-approved
    （M2 设备执行项审批）三插件技能，全部经 gate 注册链加载"""
    builtin = tmp_path / "builtin"
    _write_skill_md(builtin / "builtin-plain", "builtin-plain", execution=None)
    _write_skill_md(builtin / "builtin-device", "builtin-device", execution="device")

    _write_skill_md(plugin_env.repo_dir / "plugin-plain", "plugin-plain", execution=None)
    _approve_device_skill(plugin_env, plugin_env.repo_dir / "plugin-plain", "plugin-plain", legacy=True)

    # device 声明但 M1 时期审批（无 entries/exec_hash——存量条目形态）
    _write_skill_md(plugin_env.repo_dir / "plugin-device-legacy", "plugin-device-legacy",
                    execution="device", entry=None)
    _approve_device_skill(plugin_env, plugin_env.repo_dir / "plugin-device-legacy",
                          "plugin-device-legacy", legacy=True)

    # device 声明 + M2 设备执行项（entries/exec_hash/version/mutable 齐备）
    approved_dir = plugin_env.repo_dir / "plugin-device-approved"
    _write_skill_md(approved_dir, "plugin-device-approved", execution="device",
                    entry=["scripts/flow.py", "scripts/uicache.py"],
                    mutable="references/ui-cache.json")
    _approve_device_skill(plugin_env, approved_dir, "plugin-device-approved",
                          entries=["scripts/flow.py", "scripts/uicache.py"],
                          mutable=["references/ui-cache.json"], version="2.0.0")

    sources = gate.build_base_registry_sources(builtin, plugin_env.cfg)
    registry = SkillRegistry()
    registry.load_from_sources(sources.builtin_loader, sources.plugin_loaders,
                               sources.plugin_hashes)
    return SimpleNamespace(registry=registry, builtin=builtin,
                           approved_dir=approved_dir, plugin_env=plugin_env)


@pytest.fixture
def device_enabled(monkeypatch):
    """开启 M2 设备执行开关（判定 c 项；settings 为全局单例，monkeypatch 自动还原）"""
    monkeypatch.setattr(settings.skills.plugins.device_execution, "enabled", True)


def _executor(device_env, tmp_path):
    return SkillExecutor(device_env.registry, workspace=tmp_path / "ws")


_OK_DEVICE_RESULT = ExecutionResult(
    success=True, stdout="seq verified", stderr="", exit_code=0, duration=1.0)


def _mock_dispatch(monkeypatch, return_value=None):
    """mock 掉 dispatch_device_skill_script（拦截 enqueue/设备闸门——路由层已由 dispatch
    单测覆盖，此处只断言 executor → dispatch 的对接参数）"""
    mock = AsyncMock(return_value=return_value or _OK_DEVICE_RESULT)
    monkeypatch.setattr(
        "src.local_tools.skill_runner_proxy.dispatch_device_skill_script", mock)
    return mock


# ---------------------------------------------------------------------------
# evaluate_device_execution 放行矩阵（§3.5 判定 a–d）
# ---------------------------------------------------------------------------

class TestEvaluateDecisionMatrix:
    def test_builtin_plain_and_server_route_server(self, device_env):
        assert evaluate_device_execution(device_env.registry, "builtin-plain").status == "server"
        assert not evaluate_device_execution(device_env.registry, "builtin-plain").executable

    def test_plugin_without_device_declaration_blocked(self, device_env):
        """插件未声明 device（无论开关）→ 维持 M1 规则拦截（不看开关——判定顺序在 c 之前）"""
        decision = evaluate_device_execution(device_env.registry, "plugin-plain")
        assert decision.status == "plugin_blocked"
        assert decision.executable is False

    def test_builtin_device_unsupported(self, device_env, device_enabled):
        """内置声明 device → 无审批 hash 可对账（BUILTIN_DEVICE_UNSUPPORTED）"""
        decision = evaluate_device_execution(device_env.registry, "builtin-device")
        assert decision.status == "builtin_device_unsupported"
        assert decision.reason_code == "BUILTIN_DEVICE_UNSUPPORTED"

    def test_plugin_device_legacy_approval_rejected_even_when_enabled(self, device_env, device_enabled):
        """M1 时期存量审批条目（无 entries/exec_hash）→ fail-closed 拒绝（判定 d）"""
        decision = evaluate_device_execution(device_env.registry, "plugin-device-legacy")
        assert decision.status == "plugin_not_executable"
        assert decision.reason_code == "PLUGIN_NOT_EXECUTABLE"
        assert decision.executable is False

    def test_plugin_device_disabled_when_switch_off(self, device_env):
        """开关关（默认）→ DEVICE_EXECUTION_DISABLED（不读审批清单——判定 c 在 d 之前）"""
        assert settings.skills.plugins.device_execution.enabled is False  # 前置：默认关
        decision = evaluate_device_execution(device_env.registry, "plugin-device-approved")
        assert decision.status == "device_execution_disabled"
        assert decision.reason_code == "DEVICE_EXECUTION_DISABLED"

    def test_plugin_device_ready_with_approval_snapshot(self, device_env, device_enabled):
        """全满足 → DEVICE_READY，entries/exec_hash/version 取审批快照（非当前磁盘声明）"""
        decision = evaluate_device_execution(device_env.registry, "plugin-device-approved")
        assert decision.status == "device_ready"
        assert decision.executable is True
        assert decision.entries == ("scripts/flow.py", "scripts/uicache.py")
        assert decision.exec_hash == gate.compute_skill_exec_hash(
            device_env.approved_dir, ["references/ui-cache.json"])
        assert decision.version == "2.0.0"


class TestEvaluateFailClosedSources:
    def test_plugin_hashes_missing_name_fail_closed(self, device_env, device_enabled):
        """_plugin_hashes 缺名（签名链重建/审批撤销后的缓存态——loader 目录身份仍在）→
        判定 b fail-closed → PLUGIN_NOT_EXECUTABLE（不因目录身份兜底放行）"""
        sources = gate.build_base_registry_sources(device_env.builtin, device_env.plugin_env.cfg)
        stale = SkillRegistry()
        # 空 hash 集加载：_plugin_loader_dirs/_loaders 保留插件目录身份，_plugin_hashes 为空
        stale.load_from_sources(sources.builtin_loader, sources.plugin_loaders, {})
        assert stale.is_plugin_skill("plugin-device-approved")  # 前置：目录身份仍判插件
        decision = evaluate_device_execution(stale, "plugin-device-approved")
        assert decision.status == "plugin_not_executable"
        assert decision.reason_code == "PLUGIN_NOT_EXECUTABLE"

    def test_missing_approvals_file_rejected(self, device_env, device_enabled, plugin_env):
        """审批清单文件缺失（read_approvals → {}）→ PLUGIN_NOT_EXECUTABLE——
        registry 缓存态（_plugin_hashes 有名）不兜底放行，两处独立 fail-closed"""
        plugin_env.approvals_file.unlink()
        decision = evaluate_device_execution(device_env.registry, "plugin-device-approved")
        assert decision.status == "plugin_not_executable"

    @pytest.mark.parametrize("corrupt", [
        "{ not valid json !!",                       # JSON 损坏
        '{"version": 1}',                            # 结构非法（无 skills 包装）
    ])
    def test_corrupt_approvals_rejected(self, device_env, device_enabled, plugin_env, corrupt):
        plugin_env.approvals_file.write_text(corrupt, encoding="utf-8")
        decision = evaluate_device_execution(device_env.registry, "plugin-device-approved")
        assert decision.status == "plugin_not_executable"

    @pytest.mark.parametrize("mutation, why", [
        ({"entries": []}, "entries 空列表"),
        ({"entries": ["scripts/flow.py"], "exec_hash": ""}, "exec_hash 空串"),
        ({"entries": ["scripts/flow.py"], "exec_hash": None}, "exec_hash None"),
    ])
    def test_entry_missing_entries_or_exec_hash_rejected(self, device_env, device_enabled,
                                                         plugin_env, mutation, why):
        """审批条目缺 entries/exec_hash 任一要素 → 拒绝（对账双要素齐备才放行，§3.5 判定 d）"""
        approvals = gate.read_approvals(plugin_env.approvals_file)
        approvals["plugin-device-approved"].update(mutation)
        gate.write_approvals(plugin_env.approvals_file, approvals)
        decision = evaluate_device_execution(device_env.registry, "plugin-device-approved")
        assert decision.status == "plugin_not_executable", why

    def test_entry_without_entries_key_rejected(self, device_env, device_enabled, plugin_env):
        """条目整体不含 entries 键（M2 之前的形态）→ 同样拒绝"""
        approvals = gate.read_approvals(plugin_env.approvals_file)
        base = approvals["plugin-device-approved"]
        approvals["plugin-device-approved"] = {
            k: v for k, v in base.items() if k != "entries"}
        gate.write_approvals(plugin_env.approvals_file, approvals)
        decision = evaluate_device_execution(device_env.registry, "plugin-device-approved")
        assert decision.status == "plugin_not_executable"

    def test_revoked_entry_rejected(self, device_env, device_enabled, plugin_env):
        """条目整体被撤销（清单无该名）→ PLUGIN_NOT_EXECUTABLE"""
        approvals = gate.read_approvals(plugin_env.approvals_file)
        approvals.pop("plugin-device-approved")
        gate.write_approvals(plugin_env.approvals_file, approvals)
        decision = evaluate_device_execution(device_env.registry, "plugin-device-approved")
        assert decision.status == "plugin_not_executable"


# ---------------------------------------------------------------------------
# execute_skill_command 路由分支（§4.2）
# ---------------------------------------------------------------------------

class TestExecutorRouteBranch:
    def _route(self, device_env, tmp_path, monkeypatch, skill="plugin-device-approved",
               command="python scripts/flow.py seq verify", files=None, stdin=b"x"):
        executor = _executor(device_env, tmp_path)
        dispatch_mock = _mock_dispatch(monkeypatch)
        execute_mock = AsyncMock()
        monkeypatch.setattr(executor, "_execute_command", execute_mock)
        return asyncio.run(executor.execute_skill_command(
            skill, command, files=files, session_id="sess-1", user_id="user-1",
            stdin_content=stdin)), dispatch_mock, execute_mock

    def test_device_ready_routes_dispatch_with_payload(self, device_env, tmp_path,
                                                       monkeypatch, device_enabled):
        """放行：dispatch 收到结构化 entry/args + 审批快照 exec_hash/version；本地子进程
        不被调用（插件目录路径不进容器执行——沿 M1 顺序保证）"""
        result, dispatch_mock, execute_mock = self._route(device_env, tmp_path, monkeypatch)
        assert result is _OK_DEVICE_RESULT
        dispatch_mock.assert_awaited_once()
        kwargs = dispatch_mock.call_args.kwargs
        assert kwargs["skill"] == "plugin-device-approved"
        assert kwargs["entry"] == "scripts/flow.py"
        assert kwargs["args"] == ("seq", "verify")
        assert kwargs["exec_hash"] == evaluate_device_execution(
            device_env.registry, "plugin-device-approved").exec_hash
        assert kwargs["version"] == "2.0.0"
        execute_mock.assert_not_called()

    def test_dispatch_never_receives_stdin_content(self, device_env, tmp_path,
                                                   monkeypatch, device_enabled):
        """stdin_content 恒不透传（§3.1：身份 JSON/用户文本经 stdin 的服务端语义在设备
        链路不成立，设备 spawn stdin 'ignore'）——dispatch 参数断言无该键"""
        result, dispatch_mock, _ = self._route(device_env, tmp_path, monkeypatch, stdin=b"secret")
        assert result is _OK_DEVICE_RESULT
        assert "stdin_content" not in dispatch_mock.call_args.kwargs

    def test_files_rejected_invalid_device_input(self, device_env, tmp_path, monkeypatch,
                                                 device_enabled):
        """files 非空 → INVALID_DEVICE_INPUT（${filename} 占位替换只在容器工作目录语义生效），
        不静默丢弃也不下发"""
        result, dispatch_mock, _ = self._route(
            device_env, tmp_path, monkeypatch,
            files={"input.pdf": b"%PDF-1.4"})
        assert result.success is False
        assert result.error == "INVALID_DEVICE_INPUT"
        assert "files" in result.stderr
        dispatch_mock.assert_not_called()

    def test_invalid_entry_rejected_with_entries_listing(self, device_env, tmp_path,
                                                         monkeypatch, device_enabled):
        """entry 不在审批白名单 → INVALID_DEVICE_COMMAND + stderr 附全部合法 entries 清单
        （LLM 可自纠；不写「请勿重试」类绝对化提示）"""
        result, dispatch_mock, _ = self._route(
            device_env, tmp_path, monkeypatch,
            command="python scripts/hack.py --steal")
        assert result.success is False
        assert result.error == "INVALID_DEVICE_COMMAND"
        assert "scripts/flow.py" in result.stderr
        assert "scripts/uicache.py" in result.stderr
        assert "hack.py" in result.stderr
        dispatch_mock.assert_not_called()

    def test_disabled_switch_blocks_with_real_reason(self, device_env, tmp_path, monkeypatch):
        """开关关（默认）→ DEVICE_EXECUTION_DISABLED 文案拦截（区分码真实原因，
        无「规划 M2/即将支持」过时表述；含「设备执行」「请勿重试」防试错）"""
        result, dispatch_mock, _ = self._route(device_env, tmp_path, monkeypatch)
        assert result.success is False
        assert result.error == "DEVICE_EXECUTION_DISABLED"
        assert "设备执行" in result.stderr
        assert "请勿重试" in result.stderr
        assert "规划 M2" not in result.stderr
        assert "尚未开放" not in result.stderr
        assert "外部 Skill 插件" not in result.stderr  # device 分支文案措辞（M1 断言 :161）
        dispatch_mock.assert_not_called()

    def test_legacy_approval_blocked_even_when_enabled(self, device_env, tmp_path,
                                                       monkeypatch, device_enabled):
        """存量审批条目（无 entries）开关开 → PLUGIN_NOT_EXECUTABLE（文案含「设备执行」）"""
        result, dispatch_mock, _ = self._route(
            device_env, tmp_path, monkeypatch, skill="plugin-device-legacy")
        assert result.success is False
        assert result.error == "PLUGIN_NOT_EXECUTABLE"
        assert "设备执行" in result.stderr
        assert "请勿重试" in result.stderr
        dispatch_mock.assert_not_called()

    def test_builtin_device_unsupported_message(self, device_env, tmp_path, monkeypatch,
                                                device_enabled):
        """内置 device 声明 → BUILTIN_DEVICE_UNSUPPORTED（含「设备执行」「metadata.execution=device」「请勿重试」）"""
        result, dispatch_mock, _ = self._route(
            device_env, tmp_path, monkeypatch, skill="builtin-device")
        assert result.success is False
        assert result.error == "BUILTIN_DEVICE_UNSUPPORTED"
        assert "设备执行" in result.stderr
        assert "metadata.execution=device" in result.stderr
        assert "请勿重试" in result.stderr
        dispatch_mock.assert_not_called()


class TestExecutorSecondEntryStillBlocked:
    def test_execute_skill_script_device_ready_blocked(self, device_env, tmp_path,
                                                       monkeypatch, device_enabled):
        """第二入口（execute_skill_script）对 device_ready 技能同样拦截（M1 全拦语义维持，
        防绕过——设备路由仅在 execute_skill_command 命令入口）"""
        executor = _executor(device_env, tmp_path)
        execute_mock = AsyncMock()
        monkeypatch.setattr(executor, "_execute_command", execute_mock)
        result = asyncio.run(executor.execute_skill_script("plugin-device-approved", "flow.py"))
        assert result.success is False
        assert "设备执行" in result.stderr
        assert "execute_skill_script" in result.stderr
        execute_mock.assert_not_called()


class TestSkillExecuteToolContentRejection:
    """skill_execute_tool 层的显式 content 提前拒绝（plan §3.1——executor 侧无法区分
    用户 content 与服务端注入的身份 JSON，二者都已是 stdin bytes，判定必须在 tool 层）"""

    @staticmethod
    def _make_tool(device_env, executor_mock):
        from src.tools.skill.skill_execute_tool import SkillExecuteTool
        return SkillExecuteTool(executor_mock, device_env.registry)

    @staticmethod
    def _executor_mock(return_value=None):
        """MagicMock 容器 + execute_skill_command AsyncMock（学既有
        test_skill_execute_tool_llm_env.py 的 mock 形态）"""
        from unittest.mock import MagicMock
        mock = MagicMock()
        mock.execute_skill_command = AsyncMock(return_value=return_value)
        return mock

    def test_device_route_skill_with_explicit_content_rejected(self, device_env, monkeypatch,
                                                               device_enabled):
        """device_ready 技能 + 显式 content → INVALID_DEVICE_INPUT 提前拒绝（不静默丢弃：
        设备链路 stdin_content 恒不透传，若不拒绝用户内容将无消费方丢失）"""
        executor_mock = self._executor_mock()
        tool = self._make_tool(device_env, executor_mock)
        result = asyncio.run(tool.execute(
            skill="plugin-device-approved", command="python scripts/flow.py seq verify",
            content='{"topic": "镜片小程序"}'))
        assert result["success"] is False
        assert "content" in result["error"]
        assert "命令参数" in result["error"]
        # 提前拒绝：executor 不被调用（连命令门禁都不进入）
        executor_mock.execute_skill_command.assert_not_called()

    def test_disabled_skill_with_content_not_rejected_at_tool_layer(self, device_env,
                                                                    monkeypatch):
        """开关关（非 device_ready）技能 + content → 不在 tool 层拦截（拦截由 executor
        路由层按区分码处理，tool 层只拦放行技能的 content——避免双重文案漂移）"""
        from src.core.skill_executor import ExecutionResult as _ER
        executor_mock = self._executor_mock(_ER(success=False, stdout="", stderr="",
                                                exit_code=-1, duration=0, error="DEVICE_EXECUTION_DISABLED"))
        tool = self._make_tool(device_env, executor_mock)
        asyncio.run(tool.execute(
            skill="plugin-device-approved", command="echo hi", content="任意文本"))
        executor_mock.execute_skill_command.assert_called_once()

    def test_normal_skill_content_stdin_path_not_regressed(self, device_env, monkeypatch):
        """普通（内置 server/无声明）技能 + content → 既有 stdin 传递路径不回归：
        executor 收到的 stdin_content 即用户 content 文本（tool 层零拦截）"""
        from src.core.skill_executor import ExecutionResult as _ER
        executor_mock = self._executor_mock(_ER(
            success=True, stdout="ok", stderr="", exit_code=0, duration=0.1))
        tool = self._make_tool(device_env, executor_mock)
        result = asyncio.run(tool.execute(skill="builtin-plain", command="echo hi",
                                          content='{"text": "hello"}'))
        assert result["success"] is True
        executor_mock.execute_skill_command.assert_called_once()
        kwargs = executor_mock.execute_skill_command.call_args.kwargs
        assert kwargs["stdin_content"] == b'{"text": "hello"}'


# ---------------------------------------------------------------------------
# parse_device_skill_command（§3.1 命令解析，纯函数）
# ---------------------------------------------------------------------------

class TestParseDeviceSkillCommand:
    LIMITS = DeviceCommandLimits(max_args=16, max_arg_chars=500, max_total_chars=4000)
    ENTRIES = ("scripts/flow.py", "scripts/uicache.py")

    def _parse(self, command):
        return parse_device_skill_command(command, self.ENTRIES, self.LIMITS)

    @pytest.mark.parametrize("prefix", ["python ", "python3 ", "py ", "PYTHON ", "", "./"])
    def test_interpreter_prefix_stripped(self, prefix):
        """python/python3/py（大小写归一）解释器前缀剥离；无前缀直传；./ 前缀归一"""
        parsed = self._parse(f"{prefix}scripts/flow.py seq verify")
        assert parsed.ok is True
        assert parsed.entry == "scripts/flow.py"
        assert parsed.args == ("seq", "verify")

    def test_dotslash_multi_layer_normalized(self):
        parsed = self._parse("././scripts/flow.py")
        assert parsed.ok is True
        assert parsed.entry == "scripts/flow.py"

    def test_quoted_and_collapsed_spaces(self):
        """引号内空格保留为单参数 token；多空格分隔正确归并"""
        parsed = self._parse('python  scripts/flow.py    "two words"    --flag=1')
        assert parsed.ok is True
        assert parsed.args == ("two words", "--flag=1")

    def test_entry_outside_whitelist_lists_all_entries(self):
        parsed = self._parse("python scripts/hack.py --x")
        assert parsed.ok is False
        assert "scripts/flow.py" in parsed.error
        assert "scripts/uicache.py" in parsed.error
        assert "scripts/hack.py" in parsed.error

    def test_empty_or_interpreter_only_command_rejected(self):
        assert self._parse("").ok is False
        assert self._parse("   ").ok is False
        assert self._parse("python").ok is False
        assert self._parse("python -u scripts/flow.py").ok is False  # 解释器参数不剥离（从紧）

    def test_args_count_over_limit(self):
        parsed = self._parse("scripts/flow.py " + " ".join(f"a{i}" for i in range(17)))
        assert parsed.ok is False
        assert "17" in parsed.error and "16" in parsed.error

    def test_single_arg_over_char_limit(self):
        long_arg = "x" * 501
        parsed = self._parse(f"scripts/flow.py {long_arg}")
        assert parsed.ok is False
        assert "501" in parsed.error and "500" in parsed.error

    def test_total_args_length_over_limit(self):
        # 16 个参数（数量合法），单参 ≤500（长度合法），总长超 4000
        ok_args = [f"{i:03d}{'y' * 245}" for i in range(16)]   # 每参 248 字符，总长 3968 ≤ 4000
        assert self._parse("scripts/flow.py " + " ".join(ok_args)).ok is True
        bad_args = [f"{i:03d}{'y' * 250}" for i in range(16)]  # 每参 253 字符，总长 4048 > 4000
        parsed = self._parse("scripts/flow.py " + " ".join(bad_args))
        assert parsed.ok is False
        assert "4000" in parsed.error


class TestUseSkillDeviceHints:
    def test_ready_skill_manual_has_device_guide_without_forbidden_word(self, device_env,
                                                                        device_enabled):
        """放行路由达成 → 手册尾部设备执行说明：含「设备执行」，不含「请勿」字样
        （正向说明而非拦截提示——§3.7 约束表最后一行）"""
        tool = UseSkillTool(device_env.registry)
        result = asyncio.run(tool.execute(skill="plugin-device-approved"))
        assert result["success"] is True
        assert "设备执行" in result["content"]
        assert "请勿" not in result["content"]
        assert "skill_execute" in result["content"]  # 说明调用入口
        # 提示与 executor 路由层共用同一决策（evaluate_device_execution）
        decision = evaluate_device_execution(device_env.registry, "plugin-device-approved")
        assert decision.executable is True

    def test_not_executable_skill_manual_keeps_blocking_hint(self, device_env, device_enabled):
        """存量条目（无 entries）开关开 → use_skill 提示仍为拦截形态（含「设备执行」
        「请勿调用 skill_execute」，§3.7 约束表 :206-211 对应形态）"""
        tool = UseSkillTool(device_env.registry)
        result = asyncio.run(tool.execute(skill="plugin-device-legacy"))
        assert result["success"] is True
        assert "设备执行" in result["content"]
        assert "请勿调用 skill_execute" in result["content"]
