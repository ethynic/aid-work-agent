"""
跨租户文件路径防护单元测试

覆盖：
- src.core.tenant_path_guard：find_foreign_tenant_owner /
  check_text_for_foreign_tenant_paths / redact_foreign_tenant_paths /
  is_source_storage_reference
- src.tools.file.cp_tool._resolve_source：其他租户路径拒绝、本租户放行
  （tenant_ 前缀等价）、无上下文拒绝
- src.tools.skill.skill_execute_tool：命令预检拦截（其他租户路径 /
  source_storage）+ 输出后置脱敏

背景（2026-09-11）：售前会话 skill_execute 执行 `find /app -iname '*爱定义*'`
枚举到其他租户 storage/tenants/ 下的文件路径，且 cp 可将其他租户文件
复制后作为附件交付给本租户用户。
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.tenant_path_guard import (
    check_text_for_foreign_tenant_paths,
    find_foreign_tenant_owner,
    is_source_storage_reference,
    redact_foreign_tenant_paths,
)
from src.tools.context import ToolExecutionContext, tool_execution_scope

pytestmark = [pytest.mark.tools]


class TestFindForeignTenantOwner:
    def test_foreign_tenant_path_denied(self, tmp_path):
        p = tmp_path / "tenants" / "tenant_bbb" / "conversation" / "x.xlsx"
        assert find_foreign_tenant_owner(p, "tenant_aaa") == "tenant_bbb"

    def test_own_tenant_allowed(self, tmp_path):
        p = tmp_path / "tenants" / "tenant_aaa" / "conversation" / "x.xlsx"
        assert find_foreign_tenant_owner(p, "tenant_aaa") is None

    def test_prefix_equivalent_allowed(self, tmp_path):
        """存储目录不带 tenant_ 前缀、上下文带前缀，视为同一租户"""
        p = tmp_path / "tenants" / "aaa" / "conversation" / "x.xlsx"
        assert find_foreign_tenant_owner(p, "tenant_aaa") is None

    def test_no_tenants_segment_allowed(self, tmp_path):
        p = tmp_path / "tmp" / "a.txt"
        assert find_foreign_tenant_owner(p, "tenant_aaa") is None

    def test_source_storage_mount_detected(self, tmp_path):
        """生产存储挂载点（/app/source_storage）下的租户目录同样识别"""
        p = tmp_path / "source_storage" / "tenants" / "bbb" / "conversation" / "y.xlsx"
        assert find_foreign_tenant_owner(p, "tenant_aaa") == "bbb"

    def test_no_context_denies_any_tenants_path(self, tmp_path):
        """无租户上下文时，任何 tenants 目录一律视为越界"""
        p = tmp_path / "tenants" / "aaa" / "x.xlsx"
        assert find_foreign_tenant_owner(p, None) == "aaa"


class TestCheckTextForForeignTenantPaths:
    def test_foreign_reference_blocked(self):
        blocked, owner = check_text_for_foreign_tenant_paths(
            "ls /app/storage/tenants/tenant_bbb/conversation/", "tenant_aaa"
        )
        assert blocked
        assert owner == "tenant_bbb"

    def test_own_reference_allowed(self):
        blocked, owner = check_text_for_foreign_tenant_paths(
            "ls storage/tenants/aaa/conversation/", "tenant_aaa"
        )
        assert not blocked
        assert owner is None

    def test_no_reference_allowed(self):
        blocked, owner = check_text_for_foreign_tenant_paths(
            "find /app -iname '*报价单*'", "tenant_aaa"
        )
        assert not blocked

    def test_no_context_blocked(self):
        blocked, owner = check_text_for_foreign_tenant_paths(
            "ls /app/storage/tenants/aaa/", None
        )
        assert blocked

    def test_source_storage_reference(self):
        assert is_source_storage_reference("cat /app/source_storage/tenants/aaa/x")
        assert not is_source_storage_reference("ls /app/storage/tenants/aaa/")

    def test_cd_chain_bare_tenants_blocked(self):
        """裸 tenants 词 + shell 续接的绕过写法（CodeReview P1）"""
        blocked, owner = check_text_for_foreign_tenant_paths(
            "cd /app/storage/tenants && cd tenant_bbb && cat conversation/x.xlsx",
            "tenant_aaa",
        )
        assert blocked
        assert owner == "bare_tenants"

    def test_bare_tenants_enumeration_blocked(self):
        """枚举全部租户目录（无 owner 段）一律拦截"""
        blocked, owner = check_text_for_foreign_tenant_paths(
            "ls /app/storage/tenants", "tenant_aaa"
        )
        assert blocked
        assert owner == "bare_tenants"

    def test_own_path_with_bare_tenants_blocked(self):
        """同时含本租户完整路径与裸 tenants 时仍拦截枚举部分"""
        blocked, owner = check_text_for_foreign_tenant_paths(
            "ls storage/tenants/aaa/conversation/; ls storage/tenants",
            "tenant_aaa",
        )
        assert blocked
        assert owner == "bare_tenants"

    def test_word_containing_tenants_not_blocked(self):
        """子串误报防护：mytenants 等普通单词不受影响"""
        blocked, _ = check_text_for_foreign_tenant_paths(
            "echo mytenants", "tenant_aaa"
        )
        assert not blocked


class TestRedactForeignTenantPaths:
    def test_foreign_line_redacted_own_line_kept(self):
        text = (
            "header\n"
            "/app/storage/tenants/tenant_bbb/conversation/爱定义运动服饰报价单.xlsx\n"
            "own /app/storage/tenants/tenant_aaa/knowledge/kb_a.md\n"
            "footer"
        )
        out, violations = redact_foreign_tenant_paths(text, "tenant_aaa")
        assert "tenant_bbb" not in out
        assert "爱定义运动服饰报价单" not in out
        assert "[安全防护]" in out
        assert "own /app/storage/tenants/tenant_aaa/knowledge/kb_a.md" in out
        assert violations == ["tenant_bbb"]

    def test_no_violations_unchanged(self):
        text = "hello\nworld"
        out, violations = redact_foreign_tenant_paths(text, "tenant_aaa")
        assert out == text
        assert violations == []

    def test_empty_text(self):
        out, violations = redact_foreign_tenant_paths("", "tenant_aaa")
        assert out == ""
        assert violations == []

    def test_crlf_line_ending_redacted(self):
        """\\r\\n 行尾的跨租户路径同样脱敏（CodeReview P2）"""
        text = "/app/storage/tenants/tenant_bbb/conversation/x.xlsx\r\nnext"
        out, violations = redact_foreign_tenant_paths(text, "tenant_aaa")
        assert "tenant_bbb" not in out
        assert violations == ["tenant_bbb"]


def _make_file(tmp_path: Path, rel: str) -> Path:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("data", encoding="utf-8")
    return f


class TestCpSourceTenantBoundary:
    def test_foreign_tenant_source_denied(self, tmp_path):
        from src.tools.file.cp_tool import CpTool

        src = _make_file(tmp_path, "tenants/tenant_bbb/conversation/q.xlsx")
        tool = CpTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="tenant_aaa", session_id="s1")):
            with pytest.raises(PermissionError):
                tool._resolve_source(str(src))

    def test_own_tenant_source_allowed(self, tmp_path):
        from src.tools.file.cp_tool import CpTool

        src = _make_file(tmp_path, "tenants/tenant_aaa/conversation/q.xlsx")
        tool = CpTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="tenant_aaa", session_id="s1")):
            assert tool._resolve_source(str(src)) == src.resolve()

    def test_tmp_source_allowed(self, tmp_path):
        """系统临时目录（无 tenants 段）不受影响"""
        from src.tools.file.cp_tool import CpTool

        src = _make_file(tmp_path, "tmp_build/quote.xlsx")
        tool = CpTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="tenant_aaa", session_id="s1")):
            assert tool._resolve_source(str(src)) == src.resolve()

    def test_no_context_denied(self, tmp_path):
        from src.tools.file.cp_tool import CpTool

        src = _make_file(tmp_path, "tenants/aaa/conversation/q.xlsx")
        tool = CpTool()
        with pytest.raises(PermissionError):
            tool._resolve_source(str(src))

    @pytest.mark.asyncio
    async def test_execute_foreign_source_returns_error(self, tmp_path):
        from src.tools.file.cp_tool import CpTool

        src = _make_file(tmp_path, "tenants/tenant_bbb/conversation/q.xlsx")
        tool = CpTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="tenant_aaa", session_id="s1")):
            resp = await tool.execute(source_file_path=str(src), register_download=False)
        assert resp["success"] is False
        assert "其他租户" in resp["error"]


def _make_skill_tool():
    from src.tools.skill.skill_execute_tool import SkillExecuteTool

    executor = MagicMock()
    executor.execute_skill_command = AsyncMock(
        return_value=MagicMock(
            success=True, stdout="", stderr="", exit_code=0,
            duration=0.1, timed_out=False, error=None,
        )
    )
    registry = MagicMock()
    registry.get.return_value = MagicMock()
    return SkillExecuteTool(skill_executor=executor, skill_registry=registry), executor


class TestSkillExecuteTenantGuard:
    @pytest.mark.asyncio
    async def test_foreign_tenant_command_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "src.saas.context.get_current_tenant_id", lambda: "tenant_aaa"
        )
        tool, executor = _make_skill_tool()
        resp = await tool.execute(
            skill="pre-sales-api",
            command="ls /app/storage/tenants/tenant_bbb/conversation/",
        )
        assert resp["success"] is False
        assert "安全防护拦截" in resp["error"]
        executor.execute_skill_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_source_storage_command_blocked(self, monkeypatch):
        """source_storage（生产存储挂载点）引用无论归属一律拦截"""
        monkeypatch.setattr(
            "src.saas.context.get_current_tenant_id", lambda: "tenant_aaa"
        )
        tool, executor = _make_skill_tool()
        resp = await tool.execute(
            skill="pre-sales-api",
            command="cat /app/source_storage/tenants/aaa/conversation/x.xlsx",
        )
        assert resp["success"] is False
        assert "安全防护拦截" in resp["error"]
        executor.execute_skill_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_own_tenant_command_allowed(self, monkeypatch):
        monkeypatch.setattr(
            "src.saas.context.get_current_tenant_id", lambda: "tenant_aaa"
        )
        tool, executor = _make_skill_tool()
        resp = await tool.execute(
            skill="pre-sales-api",
            command="ls storage/tenants/aaa/conversation/",
        )
        assert resp["success"] is True
        executor.execute_skill_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_output_foreign_path_redacted(self, monkeypatch):
        """find /app 全盘枚举场景：预检不命中，输出中其他租户路径被脱敏"""
        monkeypatch.setattr(
            "src.saas.context.get_current_tenant_id", lambda: "tenant_aaa"
        )
        tool, executor = _make_skill_tool()
        from src.core.skill_executor import ExecutionResult

        executor.execute_skill_command = AsyncMock(
            return_value=ExecutionResult(
                success=False,
                stdout=(
                    "/app/storage/tenants/tenant_bbb/conversation/爱定义运动服饰报价单.xlsx\n"
                    "--- kb ---"
                ),
                stderr="",
                exit_code=2,
                duration=0.2,
            )
        )
        resp = await tool.execute(
            skill="pre-sales-api",
            command="find /app -iname '*爱定义*'",
        )
        assert "tenant_bbb" not in resp["stdout"]
        assert "爱定义运动服饰报价单" not in resp["stdout"]
        assert "[安全防护]" in resp["stdout"]
        assert "security_note" in resp
