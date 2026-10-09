"""
跨租户文件路径防护单元测试

覆盖：
- src.core.tenant_path_guard：find_foreign_tenant_owner /
  check_text_for_foreign_tenant_paths / redact_foreign_tenant_paths /
  is_source_storage_reference / extract_knowledge_refs
- src.tools.file.cp_tool._resolve_source：其他租户路径拒绝、本租户放行
  （tenant_ 前缀等价）、无上下文拒绝、共享知识库授权放行（2026-10-10）
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
    extract_flat_knowledge_ref,
    extract_knowledge_refs,
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


class TestExtractKnowledgeRefs:
    def test_normal_knowledge_path(self):
        p = Path("/app/storage/tenants/tenant_bbb/knowledge/attraction_resource/file_abc.pdf")
        assert extract_knowledge_refs(p) == [("tenant_bbb", "attraction_resource")]

    def test_no_tenants_segment(self):
        assert extract_knowledge_refs(Path("/tmp/build/quote.xlsx")) == []

    def test_non_knowledge_scene_fail_closed(self):
        """conversation 等非 knowledge 场景无法确认共享授权，返回 None"""
        p = Path("/app/storage/tenants/tenant_bbb/conversation/x.xlsx")
        assert extract_knowledge_refs(p) is None

    def test_knowledge_then_filename_fail_closed(self):
        """旧版无分类子目录路径（knowledge/ 后直接是文件名）返回 None"""
        p = Path("/app/storage/tenants/tenant_bbb/knowledge/file_abc.pdf")
        assert extract_knowledge_refs(p) is None

    def test_knowledge_last_segment_fail_closed(self):
        assert extract_knowledge_refs(Path("/app/storage/tenants/bbb/knowledge")) is None

    def test_multiple_owner_segments(self):
        p = Path(
            "/x/tenants/aaa/knowledge/st1/f1/tenants/bbb/knowledge/st2/f2"
        )
        assert extract_knowledge_refs(p) == [
            ("aaa", "st1"),
            ("bbb", "st2"),
        ]

    def test_multiple_segments_one_bad_fail_closed(self):
        p = Path("/x/tenants/aaa/knowledge/st1/f1/tenants/bbb/conversation/f2")
        assert extract_knowledge_refs(p) is None

    def test_literal_tenants_as_owner_segment(self):
        """owner 段或 source_type 段本身是字面 tenants 时的跳段解析"""
        p = Path("/app/tenants/tenants/knowledge/st/f1")
        assert extract_knowledge_refs(p) == [("tenants", "st")]
        p2 = Path("/app/tenants/aaa/knowledge/tenants/f1")
        assert extract_knowledge_refs(p2) == [("aaa", "tenants")]


class TestExtractFlatKnowledgeRef:
    """extract_flat_knowledge_ref：旧版平铺知识库路径识别（fail-closed）"""

    def test_flat_path_matched(self):
        p = Path("/app/storage/tenants/bbb/knowledge/kb_68d5ec23082f.pdf")
        assert extract_flat_knowledge_ref(p) == ("bbb", "kb_68d5ec23082f.pdf")

    def test_owner_segment_returned_verbatim(self):
        """owner 段原样返回（tenant_ 前缀等价由调用方归一化比对）"""
        p = Path("/app/storage/tenants/tenant_bbb/knowledge/file_abc.pdf")
        assert extract_flat_knowledge_ref(p) == ("tenant_bbb", "file_abc.pdf")

    def test_no_tenants_segment(self):
        assert extract_flat_knowledge_ref(Path("/app/storage/tmp_build/quote.xlsx")) is None

    def test_conversation_scene_not_matched(self):
        p = Path("/app/storage/tenants/bbb/conversation/q.xlsx")
        assert extract_flat_knowledge_ref(p) is None

    def test_new_style_four_segment_not_matched(self):
        """新版四段路径（有 source_type 段）不属于平铺形态"""
        p = Path("/app/storage/tenants/bbb/knowledge/attraction_resource/f.pdf")
        assert extract_flat_knowledge_ref(p) is None

    def test_deeper_nesting_not_matched(self):
        """knowledge/ 下还有更深层级（filename 非末段）不匹配"""
        p = Path("/app/storage/tenants/bbb/knowledge/st/sub/f.pdf")
        assert extract_flat_knowledge_ref(p) is None

    def test_knowledge_as_last_segment_not_matched(self):
        p = Path("/app/storage/tenants/bbb/knowledge")
        assert extract_flat_knowledge_ref(p) is None

    def test_multiple_tenant_segments_not_matched(self):
        p = Path("/x/tenants/aaa/knowledge/f1/tenants/bbb/knowledge/f2")
        assert extract_flat_knowledge_ref(p) is None


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


class TestCpSharedKnowledgeAllowance:
    """cp 对共享知识库文件的放行/拒绝（2026-10-10，与 load_shared_ranges 同一授权边界）"""

    def _patch_ranges(self, monkeypatch, ranges):
        monkeypatch.setattr(
            "src.knowledge.retriever.tenant_range.load_shared_ranges",
            lambda tenant_id, subagent_id, source_type=None: ranges,
        )

    def _shared_ctx(self):
        return tool_execution_scope(
            ToolExecutionContext(
                tenant_id="tenant_aaa", subagent_id="pre-sales", session_id="s1"
            )
        )

    def test_shared_knowledge_source_allowed(self, tmp_path, monkeypatch):
        from src.tools.file.cp_tool import CpTool

        self._patch_ranges(monkeypatch, [("tenant_bbb", "attraction_resource")])
        src = _make_file(
            tmp_path, "tenants/bbb/knowledge/attraction_resource/file_abc.pdf"
        )
        tool = CpTool()
        with self._shared_ctx():
            assert tool._resolve_source(str(src)) == src.resolve()

    def test_prefix_equivalent_allowed(self, tmp_path, monkeypatch):
        """路径 owner 段 bbb 与 DB tenant_bbb 经 normalize 后视为同一租户"""
        from src.tools.file.cp_tool import CpTool

        self._patch_ranges(monkeypatch, [("bbb", "attraction_resource")])
        src = _make_file(
            tmp_path, "tenants/tenant_bbb/knowledge/attraction_resource/file_abc.pdf"
        )
        tool = CpTool()
        with self._shared_ctx():
            assert tool._resolve_source(str(src)) == src.resolve()

    def test_source_type_not_authorized_denied(self, tmp_path, monkeypatch):
        """owner 命中但 source_type 不在授权对内，拒绝"""
        from src.tools.file.cp_tool import CpTool

        self._patch_ranges(monkeypatch, [("tenant_bbb", "hotel_resource")])
        src = _make_file(
            tmp_path, "tenants/tenant_bbb/knowledge/attraction_resource/file_abc.pdf"
        )
        tool = CpTool()
        with self._shared_ctx():
            with pytest.raises(PermissionError):
                tool._resolve_source(str(src))

    def test_no_subagent_context_denied(self, tmp_path, monkeypatch):
        """主智能体（subagent_id 为空）不享共享放行，即使授权表有记录"""
        from src.tools.file.cp_tool import CpTool

        ranges_mock = MagicMock(return_value=[("tenant_bbb", "attraction_resource")])
        monkeypatch.setattr(
            "src.knowledge.retriever.tenant_range.load_shared_ranges", ranges_mock
        )
        src = _make_file(
            tmp_path, "tenants/tenant_bbb/knowledge/attraction_resource/file_abc.pdf"
        )
        tool = CpTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="tenant_aaa", session_id="s1")):
            with pytest.raises(PermissionError):
                tool._resolve_source(str(src))
        ranges_mock.assert_not_called()

    def test_revoked_authorization_denied(self, tmp_path, monkeypatch):
        """授权撤销（交集为空）后立即失效"""
        from src.tools.file.cp_tool import CpTool

        self._patch_ranges(monkeypatch, [])
        src = _make_file(
            tmp_path, "tenants/tenant_bbb/knowledge/attraction_resource/file_abc.pdf"
        )
        tool = CpTool()
        with self._shared_ctx():
            with pytest.raises(PermissionError):
                tool._resolve_source(str(src))

    def test_non_knowledge_scene_fail_closed(self, tmp_path, monkeypatch):
        """conversation 等非 knowledge 场景即使授权对存在也拒绝（解析失败 fail-closed）"""
        from src.tools.file.cp_tool import CpTool

        self._patch_ranges(monkeypatch, [("tenant_bbb", "attraction_resource")])
        src = _make_file(tmp_path, "tenants/tenant_bbb/conversation/q.xlsx")
        tool = CpTool()
        with self._shared_ctx():
            with pytest.raises(PermissionError):
                tool._resolve_source(str(src))

    def _patch_resolve(self, monkeypatch, source_types, calls=None):
        def _resolve(owner, filename):
            if calls is not None:
                calls.append((owner, filename))
            return source_types

        monkeypatch.setattr(
            "src.knowledge.retriever.tenant_range.resolve_source_types_by_file",
            _resolve,
        )

    def test_flat_knowledge_path_allowed_via_db_lookup(self, tmp_path, monkeypatch):
        """旧版平铺路径：DB 回查 source_type 命中授权对，放行（兜底修复）"""
        from src.tools.file.cp_tool import CpTool

        self._patch_ranges(monkeypatch, [("tenant_bbb", "k_389b41101269")])
        calls = []
        self._patch_resolve(monkeypatch, ["k_389b41101269"], calls)
        src = _make_file(tmp_path, "tenants/tenant_bbb/knowledge/file_abc.pdf")
        tool = CpTool()
        with self._shared_ctx():
            assert tool._resolve_source(str(src)) == src.resolve()
        assert calls == [("tenant_bbb", "file_abc.pdf")]

    def test_flat_knowledge_path_unauthorized_source_type_denied(self, tmp_path, monkeypatch):
        """旧版平铺路径：DB 登记的 source_type 不在授权对内，拒绝"""
        from src.tools.file.cp_tool import CpTool

        self._patch_ranges(monkeypatch, [("tenant_bbb", "k_other")])
        self._patch_resolve(monkeypatch, ["k_389b41101269"])
        src = _make_file(tmp_path, "tenants/tenant_bbb/knowledge/file_abc.pdf")
        tool = CpTool()
        with self._shared_ctx():
            with pytest.raises(PermissionError):
                tool._resolve_source(str(src))

    def test_legacy_knowledge_path_no_registration_fail_closed(self, tmp_path, monkeypatch):
        """旧版平铺路径：DB 查无 active 登记（已删除/过期/无登记），fail-closed 拒绝"""
        from src.tools.file.cp_tool import CpTool

        self._patch_ranges(monkeypatch, [("tenant_bbb", "attraction_resource")])
        self._patch_resolve(monkeypatch, [])
        src = _make_file(tmp_path, "tenants/tenant_bbb/knowledge/file_abc.pdf")
        tool = CpTool()
        with self._shared_ctx():
            with pytest.raises(PermissionError):
                tool._resolve_source(str(src))

    def test_flat_path_conversation_scene_no_db_lookup(self, tmp_path, monkeypatch):
        """conversation 场景不属于平铺知识库形态，不触发 DB 回查，直接拒绝"""
        from src.tools.file.cp_tool import CpTool

        resolve_mock = MagicMock(return_value=["attraction_resource"])
        monkeypatch.setattr(
            "src.knowledge.retriever.tenant_range.resolve_source_types_by_file",
            resolve_mock,
        )
        src = _make_file(tmp_path, "tenants/tenant_bbb/conversation/q.xlsx")
        tool = CpTool()
        with self._shared_ctx():
            with pytest.raises(PermissionError):
                tool._resolve_source(str(src))
        resolve_mock.assert_not_called()

    def test_flat_multi_tenant_segments_no_db_lookup(self, tmp_path, monkeypatch):
        """多租户段路径无法确认平铺形态，不触发 DB 回查，fail-closed 拒绝"""
        from src.tools.file.cp_tool import CpTool

        resolve_mock = MagicMock(return_value=["attraction_resource"])
        monkeypatch.setattr(
            "src.knowledge.retriever.tenant_range.resolve_source_types_by_file",
            resolve_mock,
        )
        src = _make_file(
            tmp_path,
            "tenants/tenant_bbb/knowledge/attraction_resource/f1"
            "/__x__/tenants/tenant_ccc/knowledge/f2",
        )
        tool = CpTool()
        with self._shared_ctx():
            with pytest.raises(PermissionError):
                tool._resolve_source(str(src))
        resolve_mock.assert_not_called()

    def test_flat_new_style_path_not_routed_to_db_lookup(self, tmp_path, monkeypatch):
        """新版四段路径不走 DB 回查兜底（仍走路径段解析）"""
        from src.tools.file.cp_tool import CpTool

        self._patch_ranges(monkeypatch, [("tenant_bbb", "attraction_resource")])
        resolve_mock = MagicMock(return_value=["attraction_resource"])
        monkeypatch.setattr(
            "src.knowledge.retriever.tenant_range.resolve_source_types_by_file",
            resolve_mock,
        )
        src = _make_file(
            tmp_path, "tenants/tenant_bbb/knowledge/attraction_resource/file_abc.pdf"
        )
        tool = CpTool()
        with self._shared_ctx():
            assert tool._resolve_source(str(src)) == src.resolve()
        resolve_mock.assert_not_called()

    def test_multi_owner_partial_authorized_denied(self, tmp_path, monkeypatch):
        """路径含两个租户段、仅一段命中授权对，拒绝"""
        from src.tools.file.cp_tool import CpTool

        self._patch_ranges(monkeypatch, [("tenant_bbb", "attraction_resource")])
        src = _make_file(
            tmp_path,
            "tenants/tenant_bbb/knowledge/attraction_resource/f1"
            "/__x__/tenants/tenant_ccc/knowledge/attraction_resource/f2",
        )
        tool = CpTool()
        with self._shared_ctx():
            with pytest.raises(PermissionError):
                tool._resolve_source(str(src))


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


class TestResolveSourceTypesByFile:
    """resolve_source_types_by_file：documents 登记 DB 回查（fail-closed）"""

    def _install_db(self, monkeypatch, rows=None, error=None, captured=None):
        class _FakeCursor:
            def execute(self, sql, params):
                if captured is not None:
                    captured["sql"] = sql
                    captured["params"] = params
                if error is not None:
                    raise error

            def fetchall(self):
                return rows or []

        class _FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def cursor(self):
                return _FakeCursor()

        monkeypatch.setattr("src.db.database.get_db_connection", lambda: _FakeConn())

    def test_returns_registered_source_types(self, monkeypatch):
        from src.knowledge.retriever.tenant_range import resolve_source_types_by_file

        self._install_db(
            monkeypatch,
            rows=[
                {"source_type": "k_389b41101269"},
                {"source_type": "k_dup"},
                {"source_type": None},
                {"source_type": ""},
            ],
        )
        assert resolve_source_types_by_file("bbb", "file_abc.pdf") == [
            "k_389b41101269",
            "k_dup",
        ]

    def test_suffix_and_tenant_forms_in_query(self, monkeypatch):
        from src.knowledge.retriever.tenant_range import resolve_source_types_by_file

        captured = {}
        self._install_db(monkeypatch, rows=[], captured=captured)
        resolve_source_types_by_file("bbb", "file_abc.pdf")
        assert captured["params"][0] == ["tenant_bbb", "bbb"]
        assert captured["params"][1] == "tenants/bbb/knowledge/file_abc.pdf"
        assert "RIGHT(file_path" in captured["sql"]
        assert "status = 'active'" in captured["sql"]
        assert "expires_at" in captured["sql"]

    def test_db_error_fail_closed(self, monkeypatch):
        from src.knowledge.retriever.tenant_range import resolve_source_types_by_file

        self._install_db(monkeypatch, error=RuntimeError("db down"))
        assert resolve_source_types_by_file("bbb", "file_abc.pdf") == []

    def test_empty_args_no_query(self, monkeypatch):
        from src.knowledge.retriever.tenant_range import resolve_source_types_by_file

        called = []

        def _boom():
            called.append(1)
            raise AssertionError("不应触达 DB")

        monkeypatch.setattr("src.db.database.get_db_connection", _boom)
        assert resolve_source_types_by_file("", "f.pdf") == []
        assert resolve_source_types_by_file("bbb", "") == []
        assert not called
