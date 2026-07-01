"""
write 工具单元测试

覆盖：
- overwrite / append 写入
- generate_prompt 内部生成
- 后缀白名单 / 禁止后缀 / JSON 校验
- register_download 开关
- 错误分支
"""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.tools]

from src.tools.file.write_tool import (
    FORBIDDEN_EXTENSIONS,
    TEXT_EXTENSIONS,
    WriteTool,
    _strip_code_fences,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tool(tmp_path: Path):
    """创建 WriteTool 实例，设置测试输出目录"""
    t = WriteTool()
    t._user_id = "test_user"
    t._tenant_id = "test_tenant"
    t._test_output_dir = tmp_path
    return t


@pytest.fixture
def mock_register_download():
    """write 已不再自动注册下载（统一走 cp 工具），此 fixture 保留为空操作以兼容旧测试签名"""
    yield None


def _resolve_to_tmp(self, file_path: str) -> Path:
    """测试用路径解析：将相对路径解析到 tmp_path"""
    p = Path(file_path)
    if not p.is_absolute():
        p = self._test_output_dir / p
    p = p.resolve()
    return p


# ---------------------------------------------------------------------------
# TestToolDefinition
# ---------------------------------------------------------------------------


class TestToolDefinition:
    def test_tool_name(self):
        tool = WriteTool()
        assert tool.name == "write"

    def test_tool_display_name(self):
        tool = WriteTool()
        assert tool.display_name == "生成文本文件"

    def test_tool_category(self):
        tool = WriteTool()
        assert tool.category == "file"

    def test_input_model_fields(self):
        from src.tools.file.write_tool import WriteInput

        schema = WriteInput.model_json_schema()
        props = schema["properties"]
        assert "content" in props
        assert "file_path" in props
        assert "mode" in props
        assert "generate_prompt" in props
        # 已删除的字段
        assert "source_file_path" not in props
        assert "register_download" not in props
        assert "display_name" not in props
        assert "section_start" not in props
        assert "section_end" not in props
        assert "encoding" not in props

    def test_mode_default_is_overwrite(self):
        from src.tools.file.write_tool import WriteInput

        instance = WriteInput()
        assert instance.mode == "overwrite"

    def test_to_tool_definition(self):
        tool = WriteTool()
        defn = tool.to_tool_definition()
        assert defn["name"] == "write"
        assert "input_schema" in defn


# ---------------------------------------------------------------------------
# TestStripCodeFences
# ---------------------------------------------------------------------------


class TestStripCodeFences:
    def test_html_fence(self):
        assert _strip_code_fences("```html\n<div>hello</div>\n```") == "<div>hello</div>"

    def test_plain_fence(self):
        assert _strip_code_fences("```\ncontent\n```") == "content"

    def test_no_fence(self):
        assert _strip_code_fences("plain content") == "plain content"

    def test_fence_with_whitespace(self):
        assert _strip_code_fences("  ```json\n{\"a\":1}\n```  ") == '{"a":1}'

    def test_fence_with_lang_tag(self):
        assert _strip_code_fences("```markdown\n# Title\n```") == "# Title"

    def test_html_fence_with_model_explanation(self):
        content = (
            "这是为您生成的一份可直接打印的 HTML 文档。\n"
            "```html\n"
            "<!DOCTYPE html><html><body><h1>贵州行程</h1></body></html>\n"
            "```\n"
            "如需调整请告诉我。"
        )
        assert _strip_code_fences(content) == (
            "<!DOCTYPE html><html><body><h1>贵州行程</h1></body></html>"
        )

    def test_markdown_with_embedded_code_block_is_preserved(self):
        content = "说明文字\n```python\nprint('hello')\n```\n后续文字"
        assert _strip_code_fences(content) == content


# ---------------------------------------------------------------------------
# TestOverwriteMode
# ---------------------------------------------------------------------------


class TestOverwriteMode:
    @pytest.mark.asyncio
    async def test_overwrite_creates_file(self, tool, mock_register_download):
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(content="hello world", file_path="report.md")
        assert isinstance(result, dict)
        assert Path(result["file_path"]).read_text() == "hello world"
        assert result["file_name"] == "report.md"
        assert result["is_temp"] is False

    @pytest.mark.asyncio
    async def test_overwrite_file_exists_no_overwrite(self, tool, mock_register_download):
        target = tool._test_output_dir / "report.md"
        target.write_text("old content")
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(content="new content", file_path="report.md")
        # 返回错误字符串
        assert isinstance(result, str)
        assert "已存在" in result
        assert target.read_text() == "old content"

    @pytest.mark.asyncio
    async def test_overwrite_file_exists_with_overwrite(self, tool, mock_register_download):
        target = tool._test_output_dir / "report.md"
        target.write_text("old content")
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(content="new content", file_path="report.md", overwrite=True)
        assert isinstance(result, dict)
        assert target.read_text() == "new content"


# ---------------------------------------------------------------------------
# TestAppendMode
# ---------------------------------------------------------------------------


class TestAppendMode:
    @pytest.mark.asyncio
    async def test_append_creates_new_file(self, tool, mock_register_download):
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="first line", file_path="log.txt", mode="append"
            )
        assert isinstance(result, dict)
        target = tool._test_output_dir / "log.txt"
        assert target.read_text() == "first line"

    @pytest.mark.asyncio
    async def test_append_to_existing(self, tool, mock_register_download):
        target = tool._test_output_dir / "log.txt"
        target.write_text("line 1")
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="line 2", file_path="log.txt", mode="append"
            )
        assert isinstance(result, dict)
        assert target.read_text() == "line 1\nline 2"

    @pytest.mark.asyncio
    async def test_append_multiple(self, tool, mock_register_download):
        target = tool._test_output_dir / "log.txt"
        target.write_text("line 1")
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            await tool.execute(content="line 2", file_path="log.txt", mode="append")
            await tool.execute(content="line 3", file_path="log.txt", mode="append")
        assert target.read_text() == "line 1\nline 2\nline 3"

    @pytest.mark.asyncio
    async def test_append_to_file_with_trailing_newline(self, tool, mock_register_download):
        target = tool._test_output_dir / "log.txt"
        target.write_text("line 1\n")
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            await tool.execute(content="line 2", file_path="log.txt", mode="append")
        assert target.read_text() == "line 1\nline 2"

    @pytest.mark.asyncio
    async def test_append_skips_overwrite_check(self, tool, mock_register_download):
        """append 模式下文件已存在不返回错误，直接追加"""
        target = tool._test_output_dir / "existing.md"
        target.write_text("original")
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="appended", file_path="existing.md", mode="append"
            )
        assert isinstance(result, dict)
        assert target.read_text() == "original\nappended"


# ---------------------------------------------------------------------------
# TestGeneratePrompt
# ---------------------------------------------------------------------------


class TestGeneratePrompt:
    @pytest.mark.asyncio
    async def test_generate_prompt_calls_llm(self, tool, mock_register_download):
        """generate_prompt 模式下 _generate_with_retry 被正确调用"""
        with (
            patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp),
            patch.object(
                WriteTool, "_generate_with_retry", new_callable=AsyncMock
            ) as mock_retry,
        ):
            mock_retry.return_value = "# Generated Report\n\nContent here."
            result = await tool.execute(
                file_path="report.md", generate_prompt="生成报告"
            )
        assert isinstance(result, dict)
        assert result["file_name"] == "report.md"
        mock_retry.assert_called_once_with("生成报告", "", "zh", ".md")

    @pytest.mark.asyncio
    async def test_generate_strips_code_fences(self, tool, mock_register_download):
        """_generate_with_retry 内部调用 _strip_code_fences"""
        with (
            patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp),
            patch.object(
                WriteTool, "_generate_content", new_callable=AsyncMock
            ) as mock_gen,
        ):
            # 模拟 LLM 返回带 code fence 的内容
            mock_gen.return_value = "```html\n<html><body>Hello</body></html>\n```"
            result = await tool.execute(
                file_path="page.html",
                generate_prompt="生成HTML页面",
            )
        assert isinstance(result, dict)
        content = Path(result["file_path"]).read_text(encoding="utf-8")
        # code fences 应被清理
        assert "```html" not in content
        assert "```" not in content
        assert "<html>" in content

    @pytest.mark.asyncio
    async def test_generate_failure_returns_error_string(self, tool, mock_register_download):
        with (
            patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp),
            patch.object(
                WriteTool, "_generate_with_retry", new_callable=AsyncMock
            ) as mock_retry,
        ):
            mock_retry.return_value = None
            result = await tool.execute(
                file_path="report.md", generate_prompt="生成报告"
            )
        assert isinstance(result, str)
        assert "生成失败" in result

    @pytest.mark.asyncio
    async def test_generate_empty_content_returns_error(self, tool, mock_register_download):
        with (
            patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp),
            patch.object(
                WriteTool, "_generate_content", new_callable=AsyncMock
            ) as mock_gen,
        ):
            mock_gen.return_value = ""
            result = await tool.execute(
                file_path="report.md", generate_prompt="生成报告"
            )
        assert isinstance(result, str)
        assert "生成失败" in result


# ---------------------------------------------------------------------------
# TestContentPriority
# ---------------------------------------------------------------------------


class TestContentPriority:
    @pytest.mark.asyncio
    async def test_content_over_generate_prompt(self, tool, mock_register_download):
        """同时传 content 和 generate_prompt 时，content 优先"""
        with (
            patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp),
            patch.object(
                WriteTool, "_generate_with_retry", new_callable=AsyncMock
            ) as mock_retry,
        ):
            mock_retry.return_value = "SHOULD NOT BE USED"
            result = await tool.execute(
                content="direct content",
                file_path="report.md",
                generate_prompt="生成报告",
            )
        assert isinstance(result, dict)
        assert Path(result["file_path"]).read_text() == "direct content"
        # _generate_with_retry 不应被调用
        mock_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_content_no_prompt_returns_error(self, tool):
        """不传 content 也不传 generate_prompt：返回错误字符串"""
        result = await tool.execute(file_path="test.txt")
        assert isinstance(result, str)
        assert "至少提供一个" in result


# ---------------------------------------------------------------------------
# TestInvalidMode
# ---------------------------------------------------------------------------


class TestInvalidMode:
    @pytest.mark.asyncio
    async def test_replace_section_returns_error(self, tool):
        """mode='replace_section' 已删除，返回错误字符串"""
        result = await tool.execute(
            content="test", file_path="test.txt", mode="replace_section"
        )
        assert isinstance(result, str)
        assert "不支持" in result

    @pytest.mark.asyncio
    async def test_invalid_mode_returns_error(self, tool):
        result = await tool.execute(
            content="test", file_path="test.txt", mode="invalid_mode"
        )
        assert isinstance(result, str)
        assert "不支持" in result


# ---------------------------------------------------------------------------
# TestExtensionValidation
# ---------------------------------------------------------------------------


class TestExtensionValidation:
    @pytest.mark.asyncio
    async def test_json_invalid_content_returns_error(self, tool, mock_register_download):
        """JSON 后缀 + content 不是有效 JSON -> 返回错误字符串"""
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="not valid json {", file_path="data.json"
            )
        assert isinstance(result, str)
        assert "JSON" in result

    @pytest.mark.asyncio
    async def test_json_valid_content_succeeds(self, tool, mock_register_download):
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content='{"key": "value"}', file_path="data.json"
            )
        assert isinstance(result, dict)
        assert result["file_name"] == "data.json"

    @pytest.mark.asyncio
    async def test_forbidden_extension_returns_error(self, tool, mock_register_download):
        """FORBIDDEN_EXTENSIONS（如 .exe）-> 返回错误字符串"""
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(
                content="binary", file_path="malware.exe"
            )
        assert isinstance(result, str)
        assert "不允许" in result

    @pytest.mark.asyncio
    async def test_text_extension_whitelist_outside_writes_with_warning(
        self, tool, mock_register_download
    ):
        """TEXT_EXTENSIONS 白名单外的后缀 -> 写入但 warning（不报错）"""
        with (
            patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp),
            patch("src.tools.file.write_tool.logger") as mock_logger,
        ):
            result = await tool.execute(
                content="some data", file_path="data.abc"
            )
        assert isinstance(result, dict)
        assert result["file_name"] == "data.abc"
        # 应该有 warning 日志
        mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# TestReturnFields
# ---------------------------------------------------------------------------


class TestReturnFields:
    @pytest.mark.asyncio
    async def test_success_result_has_no_legacy_fields(self, tool, mock_register_download):
        """成功返回值不应包含 success/encoding/message 等冗余字段"""
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(content="hello", file_path="report.md")
        assert isinstance(result, dict)
        assert "success" not in result
        assert "encoding" not in result
        assert "message" not in result
        # 应有这些字段
        assert "file_path" in result
        assert "file_name" in result
        assert "file_size" in result
        assert "is_temp" in result

    @pytest.mark.asyncio
    async def test_error_result_is_string(self, tool):
        """失败时返回字符串而非 dict"""
        result = await tool.execute(file_path="test.txt")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_temp_file_is_temp_true(self, tool, mock_register_download):
        """无 file_path 时 is_temp=True"""
        result = await tool.execute(content="temp content")
        assert isinstance(result, dict)
        assert result["is_temp"] is True

    @pytest.mark.asyncio
    async def test_explicit_path_is_temp_false(self, tool, mock_register_download):
        with patch.object(WriteTool, "_resolve_and_validate_path", _resolve_to_tmp):
            result = await tool.execute(content="hello", file_path="report.md")
        assert isinstance(result, dict)
        assert result["is_temp"] is False
