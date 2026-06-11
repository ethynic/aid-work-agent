"""
file_write 工具单元测试

覆盖所有写入模式：overwrite、copy、append、replace_section
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.tools

from src.tools.file.text_file_writer import FileWriteTool, _strip_code_fences


@pytest.fixture
def tool(tmp_path):
    """创建 FileWriteTool 实例，将输出目录指向 tmp_path"""
    t = FileWriteTool()
    t._user_id = "test_user"
    t._tenant_id = "test_tenant"
    # monkeypatch _resolve_and_validate_path 的 allowed_base 到 tmp_path
    original_resolve = t._resolve_and_validate_path
    t._test_output_dir = tmp_path
    return t


@pytest.fixture
def mock_register_download():
    """mock _register_download 避免依赖 Redis"""
    with patch.object(FileWriteTool, "_register_download") as mock:
        mock.return_value = {
            "success": True,
            "file_id": "file_test123",
            "file_name": "test.html",
            "file_size": 100,
            "download_url": "/api/files/file_test123/download",
            "file_path": "/tmp/uploads/test_tenant/test_user/file_test123.html",
        }
        yield mock


def _resolve_to_tmp(self, file_path: str) -> Path:
    """测试用路径解析：将相对路径解析到 tmp_path"""
    p = Path(file_path)
    if not p.is_absolute():
        p = self._test_output_dir / p
    p = p.resolve()
    return p


# --- TestOverwriteMode ---


class TestOverwriteMode:
    """回归测试：确保现有 overwrite 行为不变"""

    @pytest.mark.asyncio
    async def test_overwrite_creates_file(self, tool, mock_register_download):
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(content="hello world", file_path="report.md")
        assert result["success"] is True
        assert Path(result["file_path"]).read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_overwrite_file_exists_no_overwrite(self, tool, mock_register_download):
        target = tool._test_output_dir / "report.md"
        target.write_text("old content")
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(content="new content", file_path="report.md")
        assert result["success"] is False
        assert "已存在" in result["error"]
        assert target.read_text() == "old content"

    @pytest.mark.asyncio
    async def test_overwrite_file_exists_with_overwrite(self, tool, mock_register_download):
        target = tool._test_output_dir / "report.md"
        target.write_text("old content")
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(content="new content", file_path="report.md", overwrite=True)
        assert result["success"] is True
        assert target.read_text() == "new content"

    @pytest.mark.asyncio
    async def test_overwrite_registers_download(self, tool, mock_register_download):
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(content="hello", file_path="report.md")
        assert result["success"] is True
        mock_register_download.assert_called_once()
        assert "download_url" in result


# --- TestCopyMode ---


class TestCopyMode:

    @pytest.mark.asyncio
    async def test_copy_with_download(self, tool, mock_register_download):
        """register_download=True（默认）：复制到下载目录，不写 target_path"""
        src = tool._test_output_dir / "source.html"
        src.write_text("<html>template</html>")

        with patch.object(FileWriteTool, "_resolve_source_path") as mock_resolve:
            mock_resolve.return_value = src
            result = await tool.execute(
                source_file_path="source.html", file_path="target.html"
            )
        assert result["success"] is True
        assert "复制" in result["message"]
        assert result["download_url"] == "/api/files/file_test123/download"
        assert result["file_id"] == "file_test123"
        # target_path 不应被创建
        target = tool._test_output_dir / "target.html"
        assert not target.exists()

    @pytest.mark.asyncio
    async def test_copy_without_download(self, tool):
        """register_download=False：复制到 target_path，不注册下载"""
        src = tool._test_output_dir / "source.html"
        src.write_text("<html>template</html>")

        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            with patch.object(FileWriteTool, "_resolve_source_path") as mock_resolve:
                mock_resolve.return_value = src
                result = await tool.execute(
                    source_file_path="source.html",
                    file_path="target.html",
                    register_download=False,
                )
        assert result["success"] is True
        target = tool._test_output_dir / "target.html"
        assert target.read_text() == "<html>template</html>"
        assert "download_url" not in result

    @pytest.mark.asyncio
    async def test_copy_source_not_exists(self, tool, mock_register_download):
        with patch.object(FileWriteTool, "_resolve_source_path") as mock_resolve:
            mock_resolve.side_effect = ValueError("源文件不存在: nonexistent.html")
            result = await tool.execute(
                source_file_path="nonexistent.html", file_path="target.html"
            )
        assert result["success"] is False
        assert "不存在" in result["error"]

    @pytest.mark.asyncio
    async def test_copy_target_exists_no_overwrite_no_download(self, tool):
        """register_download=False + 目标已存在 + 不覆盖"""
        src = tool._test_output_dir / "source.html"
        src.write_text("source content")
        target = tool._test_output_dir / "target.html"
        target.write_text("target content")

        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            with patch.object(FileWriteTool, "_resolve_source_path") as mock_resolve:
                mock_resolve.return_value = src
                result = await tool.execute(
                    source_file_path="source.html",
                    file_path="target.html",
                    register_download=False,
                )
        assert result["success"] is False
        assert "已存在" in result["error"]

    @pytest.mark.asyncio
    async def test_copy_target_exists_with_overwrite_no_download(self, tool):
        """register_download=False + 目标已存在 + 覆盖"""
        src = tool._test_output_dir / "source.html"
        src.write_text("source content")
        target = tool._test_output_dir / "target.html"
        target.write_text("old target")

        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            with patch.object(FileWriteTool, "_resolve_source_path") as mock_resolve:
                mock_resolve.return_value = src
                result = await tool.execute(
                    source_file_path="source.html",
                    file_path="target.html",
                    overwrite=True,
                    register_download=False,
                )
        assert result["success"] is True
        assert target.read_text() == "source content"

    @pytest.mark.asyncio
    async def test_copy_no_file_path_no_download(self, tool):
        """register_download=False 且不传 file_path → 报错"""
        src = tool._test_output_dir / "source.html"
        src.write_text("content")

        with patch.object(FileWriteTool, "_resolve_source_path") as mock_resolve:
            mock_resolve.return_value = src
            result = await tool.execute(
                source_file_path="source.html",
                register_download=False,
            )
        assert result["success"] is False
        assert "file_path" in result["error"]

    @pytest.mark.asyncio
    async def test_copy_forbidden_extension(self, tool, mock_register_download):
        src = tool._test_output_dir / "source.exe"
        src.write_text("binary")

        with patch.object(FileWriteTool, "_resolve_source_path") as mock_resolve:
            mock_resolve.return_value = src
            result = await tool.execute(
                source_file_path="source.exe", file_path="target.exe"
            )
        assert result["success"] is False
        assert "不允许" in result["error"]

    @pytest.mark.asyncio
    async def test_copy_no_file_path_with_download_ok(self, tool, mock_register_download):
        """register_download=True 且不传 file_path → OK，用临时文件"""
        src = tool._test_output_dir / "source.html"
        src.write_text("content")

        with patch.object(FileWriteTool, "_resolve_source_path") as mock_resolve:
            mock_resolve.return_value = src
            result = await tool.execute(
                source_file_path="source.html"
            )
        assert result["success"] is True
        assert "download_url" in result


# --- TestAppendMode ---


class TestAppendMode:

    @pytest.mark.asyncio
    async def test_append_creates_new_file(self, tool, mock_register_download):
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="first line", file_path="log.txt", mode="append"
            )
        assert result["success"] is True
        target = tool._test_output_dir / "log.txt"
        assert target.read_text() == "first line"

    @pytest.mark.asyncio
    async def test_append_to_existing(self, tool, mock_register_download):
        target = tool._test_output_dir / "log.txt"
        target.write_text("line 1")
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="line 2", file_path="log.txt", mode="append"
            )
        assert result["success"] is True
        assert target.read_text() == "line 1\nline 2"

    @pytest.mark.asyncio
    async def test_append_multiple(self, tool, mock_register_download):
        target = tool._test_output_dir / "log.txt"
        target.write_text("line 1")
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            await tool.execute(content="line 2", file_path="log.txt", mode="append")
            await tool.execute(content="line 3", file_path="log.txt", mode="append")
        assert target.read_text() == "line 1\nline 2\nline 3"

    @pytest.mark.asyncio
    async def test_append_to_file_with_trailing_newline(self, tool, mock_register_download):
        """文件末尾已有换行符时，不额外加空行"""
        target = tool._test_output_dir / "log.txt"
        target.write_text("line 1\n")
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            await tool.execute(content="line 2", file_path="log.txt", mode="append")
        assert target.read_text() == "line 1\nline 2"


# --- TestReplaceSectionMode ---


class TestReplaceSectionMode:

    @pytest.mark.asyncio
    async def test_replace_section_basic(self, tool, mock_register_download):
        target = tool._test_output_dir / "page.html"
        target.write_text(
            "HEADER\n<!-- START -->\nOLD CONTENT\n<!-- END -->\nFOOTER"
        )
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="NEW CONTENT",
                file_path="page.html",
                mode="replace_section",
                section_start="<!-- START -->",
                section_end="<!-- END -->",
            )
        assert result["success"] is True
        content = target.read_text()
        assert "HEADER" in content
        assert "FOOTER" in content
        assert "<!-- START -->" in content
        assert "<!-- END -->" in content
        assert "OLD CONTENT" not in content
        assert "NEW CONTENT" in content

    @pytest.mark.asyncio
    async def test_replace_section_start_not_found(self, tool, mock_register_download):
        target = tool._test_output_dir / "page.html"
        target.write_text("HEADER\nFOOTER")
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="new",
                file_path="page.html",
                mode="replace_section",
                section_start="<!-- START -->",
                section_end="<!-- END -->",
            )
        assert result["success"] is False
        assert "起始标记" in result["error"]
        assert target.read_text() == "HEADER\nFOOTER"

    @pytest.mark.asyncio
    async def test_replace_section_end_not_found(self, tool, mock_register_download):
        target = tool._test_output_dir / "page.html"
        target.write_text("HEADER\n<!-- START -->\nSOME CONTENT\nFOOTER")
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="new",
                file_path="page.html",
                mode="replace_section",
                section_start="<!-- START -->",
                section_end="<!-- END -->",
            )
        assert result["success"] is False
        assert "结束标记" in result["error"]

    @pytest.mark.asyncio
    async def test_replace_section_empty_content(self, tool, mock_register_download):
        target = tool._test_output_dir / "page.html"
        target.write_text(
            "HEADER\n<!-- START -->\nOLD\n<!-- END -->\nFOOTER"
        )
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="",
                file_path="page.html",
                mode="replace_section",
                section_start="<!-- START -->",
                section_end="<!-- END -->",
            )
        assert result["success"] is True
        content = target.read_text()
        assert "<!-- START -->" in content
        assert "<!-- END -->" in content
        assert "OLD" not in content

    @pytest.mark.asyncio
    async def test_replace_section_fuzzy_match(self, tool, mock_register_download):
        target = tool._test_output_dir / "page.html"
        target.write_text(
            "  <!-- START section A -->\nOLD\n  <!-- END section A -->\n"
        )
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="NEW",
                file_path="page.html",
                mode="replace_section",
                section_start="START section",
                section_end="END section",
            )
        assert result["success"] is True
        content = target.read_text()
        assert "OLD" not in content
        assert "NEW" in content

    @pytest.mark.asyncio
    async def test_replace_section_outside_preserved(self, tool, mock_register_download):
        target = tool._test_output_dir / "page.html"
        original = "CSS_BLOCK\n<!-- START -->\nOLD\n<!-- END -->\nJS_BLOCK"
        target.write_text(original)
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="REPLACED",
                file_path="page.html",
                mode="replace_section",
                section_start="<!-- START -->",
                section_end="<!-- END -->",
            )
        assert result["success"] is True
        content = target.read_text()
        assert content.startswith("CSS_BLOCK")
        assert content.endswith("JS_BLOCK")
        assert "REPLACED" in content

    @pytest.mark.asyncio
    async def test_replace_section_file_not_exists(self, tool, mock_register_download):
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="new",
                file_path="nonexistent.html",
                mode="replace_section",
                section_start="<!-- START -->",
                section_end="<!-- END -->",
            )
        assert result["success"] is False
        assert "不存在" in result["error"]

    @pytest.mark.asyncio
    async def test_replace_section_missing_markers(self, tool, mock_register_download):
        result = await tool.execute(
            content="new",
            file_path="page.html",
            mode="replace_section",
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_replace_section_missing_file_path(self, tool, mock_register_download):
        result = await tool.execute(
            content="new",
            mode="replace_section",
            section_start="<!-- START -->",
            section_end="<!-- END -->",
        )
        assert result["success"] is False


# --- TestResolveSourcePath ---


class TestResolveSourcePath:

    def test_relative_path_resolves_to_project_root(self, tmp_path):
        """相对路径解析到项目根目录，文件存在时返回正确路径"""
        tool = FileWriteTool()
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        src = project_root / "src" / "tools" / "file" / "text_file_writer.py"
        if not src.exists():
            pytest.skip("测试依赖文件不在预期路径")
        result = tool._resolve_source_path("src/tools/file/text_file_writer.py")
        assert result == src

    def test_path_traversal_blocked(self):
        tool = FileWriteTool()
        with pytest.raises(ValueError, match="超出允许范围"):
            tool._resolve_source_path("../../etc/passwd")

    def test_absolute_path_inside_project(self):
        """项目内绝对路径允许访问"""
        tool = FileWriteTool()
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        src = project_root / "src" / "tools" / "file" / "text_file_writer.py"
        if not src.exists():
            pytest.skip("测试依赖文件不在预期路径")
        result = tool._resolve_source_path(str(src))
        assert result == src

    def test_absolute_outside_project(self):
        """项目外绝对路径被拒绝"""
        tool = FileWriteTool()
        with pytest.raises(ValueError, match="超出允许范围"):
            tool._resolve_source_path("/etc/passwd")

    def test_nonexistent_file(self):
        tool = FileWriteTool()
        with pytest.raises(ValueError, match="不存在"):
            tool._resolve_source_path("nonexistent_dir_abc123/nonexistent_file.html")

    def test_directory_rejected(self):
        """目录路径（非文件）被拒绝"""
        tool = FileWriteTool()
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        src_dir = project_root / "src" / "tools"
        if not src_dir.exists():
            pytest.skip("测试依赖目录不在预期路径")
        with pytest.raises(ValueError, match="不是文件"):
            tool._resolve_source_path("src/tools")


# --- TestToolDefinition ---


class TestToolDefinition:

    def test_tool_name(self):
        tool = FileWriteTool()
        assert tool.name == "file_write"

    def test_input_model_has_new_fields(self):
        from src.tools.file.text_file_writer import FileWriteInput
        schema = FileWriteInput.model_json_schema()
        props = schema["properties"]
        assert "source_file_path" in props
        assert "mode" in props
        assert "section_start" in props
        assert "section_end" in props

    def test_description_has_bash_mapping(self):
        tool = FileWriteTool()
        assert "cp" in tool.description
        assert "replace_section" in tool.description
        assert "append" in tool.description

    def test_mode_default_is_overwrite(self):
        from src.tools.file.text_file_writer import FileWriteInput
        instance = FileWriteInput()
        assert instance.mode == "overwrite"

    @pytest.mark.asyncio
    async def test_invalid_mode_rejected(self):
        tool = FileWriteTool()
        result = await tool.execute(content="test", file_path="test.txt", mode="invalid")
        assert result["success"] is False
        assert "不支持" in result["error"]


# --- TestStripCodeFences ---


class TestStripCodeFences:

    def test_html_fence(self):
        assert _strip_code_fences("```html\n<div>hello</div>\n```") == "<div>hello</div>"

    def test_plain_fence(self):
        assert _strip_code_fences("```\ncontent\n```") == "content"

    def test_no_fence(self):
        assert _strip_code_fences("plain content") == "plain content"


# --- TestEdgeCases ---


class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_append_skips_overwrite_check(self, tool, mock_register_download):
        """append 模式下文件已存在不返回错误，直接追加"""
        target = tool._test_output_dir / "existing.md"
        target.write_text("original")
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="appended", file_path="existing.md", mode="append"
            )
        assert result["success"] is True
        assert target.read_text() == "original\nappended"

    @pytest.mark.asyncio
    async def test_replace_section_preserves_code_fences_when_not_generated(self, tool, mock_register_download):
        """用户直接传入 content 时，code fences 应原样保留"""
        target = tool._test_output_dir / "page.html"
        target.write_text("HEADER\n<!-- S -->\nOLD\n<!-- E -->\nFOOTER")
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="```html\n<section>NEW</section>\n```",
                file_path="page.html",
                mode="replace_section",
                section_start="<!-- S -->",
                section_end="<!-- E -->",
            )
        assert result["success"] is True
        content = target.read_text()
        assert "```html" in content
        assert "<section>NEW</section>" in content

    @pytest.mark.asyncio
    async def test_source_file_path_priority_over_content(self, tool, mock_register_download):
        """source_file_path 与 content 同时提供时，优先执行复制，忽略 content（与 description 一致）"""
        src = tool._test_output_dir / "source.html"
        src.write_text("template content")

        with patch.object(FileWriteTool, "_resolve_source_path") as mock_resolve:
            mock_resolve.return_value = src
            result = await tool.execute(
                source_file_path="source.html",
                file_path="target.html",
                content="actual content",
            )
        assert result["success"] is True
        assert "复制" in result["message"]
        assert "download_url" in result

    @pytest.mark.asyncio
    async def test_overwrite_default_mode(self, tool, mock_register_download):
        """不传 mode 参数时默认是 overwrite"""
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(content="test", file_path="default.txt")
        assert result["success"] is True
        assert (tool._test_output_dir / "default.txt").read_text() == "test"

    @pytest.mark.asyncio
    async def test_no_content_no_source_no_prompt(self, tool, mock_register_download):
        """三个内容来源都不提供时返回错误"""
        result = await tool.execute(file_path="test.txt")
        assert result["success"] is False
        assert "至少提供一个" in result["error"]

    @pytest.mark.asyncio
    async def test_copy_with_absolute_source_outside_project(self, tool, mock_register_download):
        """复制模式下源文件在项目外绝对路径，应被拒绝"""
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                source_file_path="/etc/passwd",
                file_path="target.txt",
            )
        assert result["success"] is False
        assert "超出允许范围" in result["error"]

    @pytest.mark.asyncio
    async def test_append_preserves_code_fences_when_not_generated(self, tool, mock_register_download):
        """用户直接传入 content 时，code fences 应原样保留"""
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="```html\n<div>hello</div>\n```",
                file_path="test.html",
                mode="append",
            )
        assert result["success"] is True
        file_content = (tool._test_output_dir / "test.html").read_text()
        assert "```html" in file_content
        assert "<div>hello</div>" in file_content

    @pytest.mark.asyncio
    async def test_replace_section_same_line(self, tool, mock_register_download):
        """起始和结束标记在同一行时也能正确替换"""
        target = tool._test_output_dir / "page.html"
        target.write_text(
            '<html>\n<head>\n<title>[必填] 替换为 PPT 标题 · Deck Title</title>\n</head>\n</html>',
            encoding="utf-8",
        )
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="<title>AI 科普 · 人工智能入门指南</title>",
                file_path="page.html",
                mode="replace_section",
                section_start="<title>[必填] 替换为 PPT 标题",
                section_end="</title>",
            )
        assert result["success"] is True
        content = target.read_text(encoding="utf-8")
        assert "AI 科普 · 人工智能入门指南" in content
        assert "[必填] 替换为 PPT 标题" not in content
        assert "<head>" in content
        assert "</head>" in content

    @pytest.mark.asyncio
    async def test_replace_section_same_line_preserves_rest(self, tool, mock_register_download):
        """同行替换时保留标记行之外的内容"""
        target = tool._test_output_dir / "page.html"
        target.write_text(
            'BEFORE\n<title>old title</title>\nAFTER',
            encoding="utf-8",
        )
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="<title>new title</title>",
                file_path="page.html",
                mode="replace_section",
                section_start="<title>old",
                section_end="</title>",
            )
        assert result["success"] is True
        content = target.read_text(encoding="utf-8")
        assert "BEFORE" in content
        assert "AFTER" in content
        assert "new title" in content
        assert "old title" not in content

    @pytest.mark.asyncio
    async def test_replace_section_with_multiple_markers(self, tool, mock_register_download):
        """多个相同标记时，取第一个 start 后的第一个 end"""
        target = tool._test_output_dir / "page.html"
        target.write_text(
            "A\n<!-- M -->\nOLD1\n<!-- M -->\nOLD2\n<!-- M -->\nB"
        )
        with patch.object(FileWriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="NEW",
                file_path="page.html",
                mode="replace_section",
                section_start="<!-- M -->",
                section_end="<!-- M -->",
            )
        assert result["success"] is True
        content = target.read_text()
        # 应该只替换第一个标记到第二个标记之间的内容
        assert "OLD1" not in content
        assert "OLD2" in content
        assert "NEW" in content
