"""skill_execute 脚本路径解析加固单测（2026-10-27 trace 实证三类缺陷）。

根因背景：`_process_command` 原用裸 str.replace 替换 `scripts/<name>`：
- 命令写绝对路径 /app/src/skills/<skill>/scripts/xxx.py 时被二次拼接为
  /app/src/skills/<skill>//app/src/skills/<skill>/scripts/xxx.py；
- 命令写裸文件名（python lead_db.py init）完全不替换，落到临时会话 cwd
  报 No such file。

2026-10-30 扩展（T2 残余缺陷，trace tr_runner_2eb58fcd 实证）：
- executor 层 workdir 覆盖：caller-provided 目录不 cleanup，多步技能跨调用
  复用文件；缺省时一次性临时目录仍被清理；
- 工具层 _validate_workdir：仅放行当前租户存储目录与 storage/tmp，
  fail-closed。
"""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.skills

from src.core.skill_executor import ExecutionResult, SkillExecutionContext, SkillExecutor


@pytest.fixture
def executor(tmp_path):
    registry = MagicMock()
    skill = MagicMock()
    skill.scripts = [tmp_path / "src" / "skills" / "demo-1.0.0" / "scripts" / "lead_db.py"]
    registry.get.return_value = skill
    return SkillExecutor(registry, workspace=tmp_path / "ws")


def _ctx(tmp_path):
    return SkillExecutionContext(skill_name="demo", workdir=tmp_path / "work")


class TestScriptPathResolution:
    def test_relative_scripts_path_replaced(self, executor, tmp_path):
        cmd = executor._process_command("python scripts/lead_db.py init", _ctx(tmp_path))
        assert cmd.endswith("/scripts/lead_db.py init")
        assert cmd.count("lead_db.py") == 1

    def test_dot_slash_scripts_path_replaced(self, executor, tmp_path):
        cmd = executor._process_command("python ./scripts/lead_db.py init", _ctx(tmp_path))
        assert cmd.endswith("/scripts/lead_db.py init")
        assert cmd.count("lead_db.py") == 1

    def test_absolute_path_not_double_prefixed(self, executor, tmp_path):
        """绝对路径中的 scripts/xxx 不得被再拼一次技能目录（双前缀 bug）。"""
        abs_path = (tmp_path / "src" / "skills" / "demo-1.0.0" / "scripts" / "lead_db.py").as_posix()
        cmd = executor._process_command(f"python {abs_path} init", _ctx(tmp_path))
        assert cmd == f"python {abs_path} init"

    def test_bare_filename_resolved_to_script_path(self, executor, tmp_path):
        """裸文件名应解析到技能 scripts 目录，而非临时会话 cwd。"""
        cmd = executor._process_command('python lead_db.py init --name "x"', _ctx(tmp_path))
        assert "/scripts/lead_db.py init" in cmd
        assert cmd.count("lead_db.py") == 1

    def test_bare_filename_inside_other_token_not_replaced(self, executor, tmp_path):
        """路径上下文中的同名文件（--body-file /tmp/other_lead_db.py）不动。"""
        cmd = executor._process_command(
            "python lead_db.py add --file /tmp/other_lead_db.py", _ctx(tmp_path)
        )
        assert "/scripts/lead_db.py add" in cmd
        assert "/tmp/other_lead_db.py" in cmd

    def test_no_skill_scripts_untouched(self, tmp_path):
        registry = MagicMock()
        registry.get.return_value = None
        executor = SkillExecutor(registry, workspace=tmp_path / "ws")
        cmd = executor._process_command("python other.py run", _ctx(tmp_path))
        assert cmd == "python other.py run"


def _make_executor(tmp_path) -> SkillExecutor:
    registry = MagicMock()
    skill = MagicMock()
    skill.scripts = [tmp_path / "src" / "skills" / "demo-1.0.0" / "scripts" / "lead_db.py"]
    skill.dependencies = []
    registry.get.return_value = skill
    executor = SkillExecutor(registry, workspace=tmp_path / "ws")
    # 屏蔽设备路由 / 执行闸门 / 环境解析，聚焦 workdir 生命周期
    executor._evaluate_device_execution = lambda name: SimpleNamespace(executable=False)
    executor._check_execution_gate = lambda *a, **k: None
    executor.environment = lambda *a, **k: {}
    return executor


class TestWorkdirOverride:
    """execute_skill_command 的 workdir 覆盖（T2 修复：caller 目录绝不 rmtree）"""

    @pytest.mark.asyncio
    async def test_caller_workdir_used_and_preserved(self, tmp_path):
        executor = _make_executor(tmp_path)
        captured = {}

        async def fake_execute(cmd, cwd, timeout=None, stdin_content=None, env=None):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            return ExecutionResult(success=True, stdout="ok", stderr="", exit_code=0, duration=0)

        executor._execute_command = fake_execute

        workdir = tmp_path / "tenants" / "abc" / "conversation"
        workdir.mkdir(parents=True)
        (workdir / "sites_176.txt").write_text("data", encoding="utf-8")

        result = await executor.execute_skill_command(
            "demo", "python lead_db.py list", session_id="s1",
            files={"out.txt": b"result"}, workdir=workdir,
        )

        assert result.success is True
        assert captured["cwd"] == workdir.resolve()
        # LLM 先写入的文件在执行目录可见（本次修复的核心场景）
        assert (workdir / "sites_176.txt").read_text(encoding="utf-8") == "data"
        # files 落到 caller 目录且执行后保留（不 cleanup）
        assert (workdir / "out.txt").read_bytes() == b"result"
        # 未创建一次性临时目录
        assert list(executor.workspace.iterdir()) == []

    @pytest.mark.asyncio
    async def test_default_tmp_workdir_still_cleaned(self, tmp_path):
        executor = _make_executor(tmp_path)
        captured = {}

        async def fake_execute(cmd, cwd, timeout=None, stdin_content=None, env=None):
            captured["cwd"] = cwd
            return ExecutionResult(success=True, stdout="ok", stderr="", exit_code=0, duration=0)

        executor._execute_command = fake_execute

        result = await executor.execute_skill_command(
            "demo", "python lead_db.py list", session_id="s2"
        )

        assert result.success is True
        assert captured["cwd"].is_relative_to(executor.workspace.resolve())
        assert not captured["cwd"].exists()

    @pytest.mark.asyncio
    async def test_workdir_accepts_str_and_creates_missing_dir(self, tmp_path):
        executor = _make_executor(tmp_path)
        executor._execute_command = AsyncMock(
            return_value=ExecutionResult(success=True, stdout="", stderr="", exit_code=0, duration=0)
        )

        workdir = tmp_path / "tenants" / "abc" / "conversation"
        result = await executor.execute_skill_command(
            "demo", "python lead_db.py list", workdir=str(workdir)
        )

        assert result.success is True
        assert workdir.is_dir()

    @pytest.mark.asyncio
    async def test_caller_workdir_preserved_on_command_failure(self, tmp_path):
        """命令失败（非零退出）同样不得 rmtree 租户目录"""
        executor = _make_executor(tmp_path)
        executor._execute_command = AsyncMock(
            return_value=ExecutionResult(
                success=False, stdout="", stderr="boom", exit_code=1, duration=0
            )
        )

        workdir = tmp_path / "tenants" / "abc" / "conversation"
        workdir.mkdir(parents=True)

        result = await executor.execute_skill_command(
            "demo", "python lead_db.py list", files={"out.txt": b"result"}, workdir=workdir,
        )

        assert result.success is False
        assert (workdir / "out.txt").read_bytes() == b"result"
        assert executor._active_contexts == {}

    @pytest.mark.asyncio
    async def test_caller_workdir_reused_across_calls(self, tmp_path):
        """核心修复场景：第一步写入的文件，第二步在同 workdir 可见"""
        executor = _make_executor(tmp_path)
        seen = {}

        async def fake_execute(cmd, cwd, timeout=None, stdin_content=None, env=None):
            f = cwd / "out.txt"
            seen["exists"] = f.exists()
            seen["content"] = f.read_bytes() if f.exists() else None
            return ExecutionResult(success=True, stdout="ok", stderr="", exit_code=0, duration=0)

        executor._execute_command = fake_execute
        workdir = tmp_path / "tenants" / "abc" / "conversation"
        workdir.mkdir(parents=True)

        r1 = await executor.execute_skill_command(
            "demo", "python lead_db.py write", session_id="s1",
            files={"out.txt": b"v1"}, workdir=workdir,
        )
        assert r1.success is True
        # 第一步执行后文件仍在（未清理）
        assert (workdir / "out.txt").read_bytes() == b"v1"

        r2 = await executor.execute_skill_command(
            "demo", "python lead_db.py read", session_id="s2", workdir=workdir,
        )
        assert r2.success is True
        # 第二步执行期间文件可见且内容为第一步所写
        assert seen["exists"] is True
        assert seen["content"] == b"v1"

    @pytest.mark.asyncio
    async def test_caller_workdir_pointing_to_file_raises(self, tmp_path):
        """workdir 指向已存在文件（非目录）：executor 侧 mkdir 抛 FileExistsError，
        且不留活跃上下文残留（tool 层负责捕获转为错误响应）"""
        executor = _make_executor(tmp_path)
        executor._execute_command = AsyncMock(
            return_value=ExecutionResult(success=True, stdout="", stderr="", exit_code=0, duration=0)
        )

        file_path = tmp_path / "tenants" / "abc" / "data.txt"
        file_path.parent.mkdir(parents=True)
        file_path.write_bytes(b"data")

        with pytest.raises(FileExistsError):
            await executor.execute_skill_command(
                "demo", "python lead_db.py list", workdir=file_path,
            )

        assert executor._active_contexts == {}
        assert file_path.read_bytes() == b"data"

    @pytest.mark.asyncio
    async def test_files_keyname_traversal_rejected(self, tmp_path):
        """files 键名含绝对路径或 .. 段时拒绝，防止写出执行目录
        （workdir 现可为租户目录，逃逸后果实际）"""
        executor = _make_executor(tmp_path)

        ctx = SkillExecutionContext(skill_name="demo", workdir=tmp_path / "work")
        for bad in ("/tmp/evil.txt", "../evil.txt", "sub/../../evil.txt"):
            with pytest.raises(ValueError):
                ctx.add_file(bad, b"x")
        # 相对子目录键名仍然放行（save_files 支持 filepath.parent.mkdir）
        ctx.add_file("sub/dir/ok.txt", b"x")
        assert ctx.files["sub/dir/ok.txt"] == b"x"

        executor._execute_command = AsyncMock(
            return_value=ExecutionResult(success=True, stdout="", stderr="", exit_code=0, duration=0)
        )
        # executor 层直接抛出（tool 层 except 负责转为错误响应，不触达子进程）
        with pytest.raises(ValueError):
            await executor.execute_skill_command(
                "demo", "python lead_db.py list", files={"../evil.txt": b"x"}
            )
        executor._execute_command.assert_not_awaited()


class TestValidateWorkdir:
    """工具层 _validate_workdir：租户边界 fail-closed"""

    @pytest.fixture
    def storage_layout(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        conv = tmp_path / "storage" / "tenants" / "abc" / "conversation"
        conv.mkdir(parents=True)
        other = tmp_path / "storage" / "tenants" / "other" / "conversation"
        other.mkdir(parents=True)
        tmp_dir = tmp_path / "storage" / "tmp"
        tmp_dir.mkdir(parents=True)
        return SimpleNamespace(conv=conv, other=other, tmp_dir=tmp_dir)

    def test_tenant_conversation_dir_allowed(self, storage_layout):
        from src.tools.skill.skill_execute_tool import _validate_workdir

        assert _validate_workdir(str(storage_layout.conv), "tenant_abc") is True
        # 不带前缀形式同样放行
        assert _validate_workdir(str(storage_layout.conv), "abc") is True

    def test_storage_tmp_allowed_without_tenant(self, storage_layout):
        from src.tools.skill.skill_execute_tool import _validate_workdir

        assert _validate_workdir(str(storage_layout.tmp_dir), None) is True
        sub = storage_layout.tmp_dir / "drafts"
        sub.mkdir()
        assert _validate_workdir(str(sub), "tenant_abc") is True

    def test_other_tenant_dir_rejected(self, storage_layout):
        from src.tools.skill.skill_execute_tool import _validate_workdir

        assert _validate_workdir(str(storage_layout.other), "tenant_abc") is False

    def test_tenant_dir_rejected_without_tenant_context(self, storage_layout):
        from src.tools.skill.skill_execute_tool import _validate_workdir

        assert _validate_workdir(str(storage_layout.conv), None) is False

    def test_platform_path_rejected(self, storage_layout):
        from src.tools.skill.skill_execute_tool import _validate_workdir

        assert _validate_workdir("/app/src", "tenant_abc") is False
        assert _validate_workdir("/app/src/skills/demo-1.0.0/scripts", "tenant_abc") is False

    def test_traversal_escape_rejected(self, storage_layout, tmp_path):
        from src.tools.skill.skill_execute_tool import _validate_workdir

        escape = str(storage_layout.conv / ".." / ".." / "other" / "conversation")
        assert os.path.realpath(escape) == str(storage_layout.other)
        assert _validate_workdir(escape, "tenant_abc") is False

    @pytest.mark.asyncio
    async def test_execute_rejects_invalid_workdir_before_executor(self, monkeypatch, tmp_path):
        from src.tools.skill.skill_execute_tool import (
            _WORKDIR_REJECT_MSG,
            SkillExecuteTool,
        )
        import src.tools.context as tools_context

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(tools_context, "current_tool_execution_context", lambda: None)

        tool = SkillExecuteTool(skill_executor=MagicMock(), skill_registry=MagicMock())
        result = await tool.execute(skill="demo", command="python x.py", workdir="/app/src")

        assert result["success"] is False
        assert _WORKDIR_REJECT_MSG in result["error"]
        tool.skill_executor.execute_skill_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_passes_valid_workdir_to_executor(self, monkeypatch, tmp_path):
        from src.tools.skill.skill_execute_tool import SkillExecuteTool
        import src.tools.context as tools_context

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(tools_context, "current_tool_execution_context", lambda: None)

        workdir = tmp_path / "storage" / "tmp" / "session_x"
        workdir.mkdir(parents=True)

        executor = MagicMock()
        executor.execute_skill_command = AsyncMock(
            return_value=ExecutionResult(success=True, stdout="ok", stderr="", exit_code=0, duration=0)
        )
        tool = SkillExecuteTool(skill_executor=executor, skill_registry=MagicMock())
        result = await tool.execute(skill="demo", command="python x.py", workdir=str(workdir))

        assert result["success"] is True
        kwargs = executor.execute_skill_command.await_args.kwargs
        assert kwargs["workdir"] == str(workdir)

    def test_symlink_escape_rejected(self, storage_layout, tmp_path):
        """symlink 指向租户目录外：realpath 解析后必须拒绝（防双重解析缺口）"""
        from src.tools.skill.skill_execute_tool import _validate_workdir

        outside = tmp_path / "outside_src"
        outside.mkdir()
        link = storage_layout.conv / "escape_link"
        link.symlink_to(outside)
        assert os.path.realpath(str(link)) == str(outside)
        assert _validate_workdir(str(link), "tenant_abc") is False
        # 无租户上下文同样拒绝
        assert _validate_workdir(str(link), None) is False

    def test_symlink_into_tenant_allowed(self, storage_layout):
        """symlink 解析后落在租户目录内：放行（不过严）"""
        from src.tools.skill.skill_execute_tool import _validate_workdir

        sub = storage_layout.conv / "subdir"
        sub.mkdir()
        link = storage_layout.tmp_dir / "tenant_link"
        link.symlink_to(sub)
        assert _validate_workdir(str(link), "tenant_abc") is True

    def test_validate_workdir_rejects_nul_byte_path(self, storage_layout):
        """含 NUL 字节的非法路径：realpath 抛 ValueError 或规范化后不命中白名单，均拒绝"""
        from src.tools.skill.skill_execute_tool import _validate_workdir

        assert _validate_workdir("storage/tenants/abc/\x00evil", "tenant_abc") is False

    @pytest.mark.asyncio
    async def test_execute_empty_workdir_string_passes_none_to_executor(
        self, monkeypatch, tmp_path
    ):
        """workdir 为空字符串：`if workdir` 跳过校验，`workdir or None` 传 None
        给 executor（沿用默认一次性临时目录）"""
        from src.tools.skill.skill_execute_tool import SkillExecuteTool
        import src.tools.context as tools_context

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(tools_context, "current_tool_execution_context", lambda: None)

        executor = MagicMock()
        executor.execute_skill_command = AsyncMock(
            return_value=ExecutionResult(success=True, stdout="ok", stderr="", exit_code=0, duration=0)
        )
        tool = SkillExecuteTool(skill_executor=executor, skill_registry=MagicMock())
        result = await tool.execute(skill="demo", command="python x.py", workdir="")

        assert result["success"] is True
        kwargs = executor.execute_skill_command.await_args.kwargs
        assert kwargs["workdir"] is None
