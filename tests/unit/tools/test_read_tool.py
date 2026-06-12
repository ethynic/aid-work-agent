"""
ReadTool 单元测试

覆盖：
- 整文件读取（小文件，返回 cat -n 格式）
- 行号范围（offset=0-based，返回 1-based 行号）
- 标记定位（section_start + section_end，含标记行）
- section_end 不传时读到末尾或 limit
- section_start 找不到时返回字符串错误
- Word 文档路由命中（mock）
- 不存在文件返回字符串错误
- 项目根目录外的绝对路径返回错误
- 空文件路径返回错误
- 返回的 dict 含 content / total_lines / read_lines 字段，截断时含 next_hint
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = [pytest.mark.tools]


# ---------------------------------------------------------------------------
# 工具定义
# ---------------------------------------------------------------------------

class TestReadToolDefinition:
    """工具定义测试"""

    def test_tool_name(self):
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()
        assert tool.name == "read"

    def test_tool_display_name(self):
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()
        assert tool.display_name == "读取文件"

    def test_tool_category(self):
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()
        assert tool.category == "file"

    def test_tool_definition_has_schema(self):
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()
        defn = tool.to_tool_definition()
        assert defn["name"] == "read"
        assert "input_schema" in defn
        schema = defn["input_schema"]
        props = schema.get("properties", {})
        assert "file_path" in props
        assert "offset" in props
        assert "limit" in props
        assert "section_start" in props
        assert "section_end" in props
        # encoding 不应存在
        assert "encoding" not in props

    def test_tool_has_description(self):
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()
        assert len(tool.description) > 50
        assert "cat -n" in tool.description
        assert "section_start" in tool.description

    def test_display_name_with_args(self):
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()
        name = tool.get_display_name({"file_path": "/some/dir/test.txt"})
        assert "test.txt" in name

    def test_display_name_without_args(self):
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()
        name = tool.get_display_name()
        assert name == "读取文件"


# ---------------------------------------------------------------------------
# 整文件读取
# ---------------------------------------------------------------------------

class TestReadFullFile:
    """整文件读取测试"""

    @pytest.mark.asyncio
    async def test_read_small_file(self, tmp_path):
        """小文件整文件读取，返回 cat -n 格式"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "hello.txt"
        f.write_text("line1\nline2\nline3", encoding="utf-8")

        result = await tool.execute(file_path=str(f))

        assert isinstance(result, dict)
        assert "content" in result
        assert "total_lines" in result
        assert "read_lines" in result
        assert result["total_lines"] == 3
        assert result["read_lines"] == 3

        # 验证 cat -n 格式：行号 + tab + 内容
        content = result["content"]
        assert "1\tline1" in content
        assert "2\tline2" in content
        assert "3\tline3" in content

    @pytest.mark.asyncio
    async def test_read_empty_file(self, tmp_path):
        """空文件读取"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")

        result = await tool.execute(file_path=str(f))

        assert isinstance(result, dict)
        assert result["total_lines"] == 0
        assert result["read_lines"] == 0
        assert result["content"] == ""

    @pytest.mark.asyncio
    async def test_read_single_line(self, tmp_path):
        """单行文件读取"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "oneline.txt"
        f.write_text("only line", encoding="utf-8")

        result = await tool.execute(file_path=str(f))
        assert isinstance(result, dict)
        assert result["total_lines"] == 1
        assert "1\tonly line" in result["content"]


# ---------------------------------------------------------------------------
# 行号范围读取（offset + limit）
# ---------------------------------------------------------------------------

class TestReadRange:
    """行号范围读取测试"""

    @pytest.mark.asyncio
    async def test_offset_and_limit(self, tmp_path):
        """offset + limit 读取指定范围"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "lines.txt"
        lines = [f"line{i}" for i in range(20)]
        f.write_text("\n".join(lines), encoding="utf-8")

        # offset=5（0-based），limit=3 → 读 line5, line6, line7
        result = await tool.execute(file_path=str(f), offset=5, limit=3)

        assert isinstance(result, dict)
        assert result["total_lines"] == 20
        assert result["read_lines"] == 3

        content = result["content"]
        # 行号是 1-based，所以 offset=5 对应第 6 行
        assert "6\tline5" in content
        assert "7\tline6" in content
        assert "8\tline7" in content
        # 不应包含其他行
        assert "line4" not in content
        assert "line8" not in content

    @pytest.mark.asyncio
    async def test_offset_zero(self, tmp_path):
        """offset=0 从第一行开始"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "data.txt"
        f.write_text("alpha\nbeta\ngamma", encoding="utf-8")

        result = await tool.execute(file_path=str(f), offset=0, limit=2)
        assert isinstance(result, dict)
        assert result["read_lines"] == 2
        assert "1\talpha" in result["content"]
        assert "2\tbeta" in result["content"]
        assert "gamma" not in result["content"]

    @pytest.mark.asyncio
    async def test_offset_without_limit(self, tmp_path):
        """只传 offset 不传 limit，读到末尾"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "data.txt"
        f.write_text("a\nb\nc\nd\ne", encoding="utf-8")

        result = await tool.execute(file_path=str(f), offset=3)
        assert isinstance(result, dict)
        assert result["read_lines"] == 2
        assert "4\td" in result["content"]
        assert "5\te" in result["content"]

    @pytest.mark.asyncio
    async def test_limit_without_offset(self, tmp_path):
        """只传 limit 不传 offset，从第一行开始"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "data.txt"
        f.write_text("x\ny\nz", encoding="utf-8")

        result = await tool.execute(file_path=str(f), limit=2)
        assert isinstance(result, dict)
        assert result["read_lines"] == 2
        assert "1\tx" in result["content"]
        assert "2\ty" in result["content"]

    @pytest.mark.asyncio
    async def test_offset_beyond_file(self, tmp_path):
        """offset 超出文件行数，返回空内容"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "short.txt"
        f.write_text("one\ntwo", encoding="utf-8")

        result = await tool.execute(file_path=str(f), offset=100)
        assert isinstance(result, dict)
        assert result["read_lines"] == 0
        assert result["content"] == ""

    @pytest.mark.asyncio
    async def test_truncation_with_next_hint(self, tmp_path):
        """截断时返回 next_hint"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        # 构造一个超过 MAX_LINES 的文件（通过 mock MAX_LINES 为小值来测试）
        f = tmp_path / "long.txt"
        lines = [f"line{i}" for i in range(10)]
        f.write_text("\n".join(lines), encoding="utf-8")

        with patch("src.tools.file.read_tool.MAX_LINES", 3):
            result = await tool.execute(file_path=str(f))

        assert isinstance(result, dict)
        assert result["read_lines"] == 3
        assert "next_hint" in result
        assert "offset=3" in result["next_hint"]

    @pytest.mark.asyncio
    async def test_negative_offset_treated_as_none(self, tmp_path):
        """负数 offset 视为 None（从头开始）"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "data.txt"
        f.write_text("a\nb\nc", encoding="utf-8")

        result = await tool.execute(file_path=str(f), offset=-1, limit=2)
        assert isinstance(result, dict)
        assert result["read_lines"] == 2
        assert "1\ta" in result["content"]

    @pytest.mark.asyncio
    async def test_negative_limit_treated_as_none(self, tmp_path):
        """负数 limit 视为 None（读到末尾）"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "data.txt"
        f.write_text("a\nb\nc", encoding="utf-8")

        result = await tool.execute(file_path=str(f), offset=1, limit=-5)
        assert isinstance(result, dict)
        assert result["read_lines"] == 2


# ---------------------------------------------------------------------------
# 标记定位读取（section_start / section_end）
# ---------------------------------------------------------------------------

class TestReadSection:
    """标记定位读取测试"""

    @pytest.mark.asyncio
    async def test_section_with_both_markers(self, tmp_path):
        """section_start + section_end，含标记行"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        content = (
            "<html>\n"
            "<head>\n"
            "<style type=\"text/css\">\n"
            "body { color: red; }\n"
            "</style>\n"
            "</head>\n"
            "<body>\n"
            "</body>\n"
            "</html>"
        )
        f = tmp_path / "page.html"
        f.write_text(content, encoding="utf-8")

        result = await tool.execute(
            file_path=str(f),
            section_start="<style",
            section_end="</style>",
        )

        assert isinstance(result, dict)
        assert result["total_lines"] == 9
        assert result["read_lines"] == 3  # <style>行, body行, </style>行
        assert "section_start_line" in result
        assert "section_end_line" in result

        c = result["content"]
        assert "<style" in c
        assert "color: red" in c
        assert "</style>" in c
        # 不应包含标记外的行
        assert "<html>" not in c
        assert "<body>" not in c

    @pytest.mark.asyncio
    async def test_section_without_end_marker(self, tmp_path):
        """section_end 不传时从 section_start 读到末尾"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        content = (
            "header\n"
            "<!-- START -->\n"
            "middle1\n"
            "middle2\n"
        )
        f = tmp_path / "data.txt"
        f.write_text(content, encoding="utf-8")

        result = await tool.execute(
            file_path=str(f),
            section_start="<!-- START -->",
        )

        assert isinstance(result, dict)
        assert result["read_lines"] == 3  # START行 + middle1 + middle2
        assert "section_start_line" in result
        assert "section_end_line" not in result

    @pytest.mark.asyncio
    async def test_section_without_end_with_limit(self, tmp_path):
        """section_end 不传 + limit 约束"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        lines = ["header", "<!-- MARK -->"] + [f"line{i}" for i in range(20)]
        f = tmp_path / "data.txt"
        f.write_text("\n".join(lines), encoding="utf-8")

        result = await tool.execute(
            file_path=str(f),
            section_start="<!-- MARK -->",
            limit=3,
        )

        assert isinstance(result, dict)
        assert result["read_lines"] == 3  # MARK行 + line0 + line1

    @pytest.mark.asyncio
    async def test_section_start_not_found(self, tmp_path):
        """section_start 找不到时返回字符串错误"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "data.txt"
        f.write_text("abc\ndef\nghi", encoding="utf-8")

        result = await tool.execute(
            file_path=str(f),
            section_start="NOT_EXIST",
        )

        assert isinstance(result, str)
        assert "未找到起始标记" in result

    @pytest.mark.asyncio
    async def test_section_end_not_found(self, tmp_path):
        """section_end 找不到时返回字符串错误"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "data.txt"
        f.write_text("abc\n<!-- START -->\ndef\nghi", encoding="utf-8")

        result = await tool.execute(
            file_path=str(f),
            section_start="<!-- START -->",
            section_end="<!-- END -->",
        )

        assert isinstance(result, str)
        assert "未找到结束标记" in result


# ---------------------------------------------------------------------------
# 错误处理
# ---------------------------------------------------------------------------

class TestReadErrors:
    """错误处理测试"""

    @pytest.mark.asyncio
    async def test_empty_file_path(self):
        """空文件路径返回字符串错误"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        result = await tool.execute(file_path="")
        assert isinstance(result, str)
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_none_file_path(self):
        """None 文件路径返回字符串错误"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        result = await tool.execute(file_path=None)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_file_not_exists(self, tmp_path):
        """不存在文件返回字符串错误"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        result = await tool.execute(file_path=str(tmp_path / "nonexistent.txt"))
        assert isinstance(result, str)
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_path_outside_project_root(self, tmp_path):
        """项目根目录外的绝对路径返回错误"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        # 构造一个肯定不在项目根目录和 temp 目录内的路径
        if Path("C:/").exists():
            outside = Path("C:/Windows/system32/drivers/etc/hosts")
        else:
            outside = Path("/etc/passwd")

        # 如果文件刚好存在（如 Linux /etc/passwd），仍然会因路径校验失败
        result = await tool.execute(file_path=str(outside))
        assert isinstance(result, str)
        assert ("超出允许范围" in result or "不存在" in result)

    @pytest.mark.asyncio
    async def test_directory_path(self, tmp_path):
        """传入目录路径返回错误"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        d = tmp_path / "subdir"
        d.mkdir()

        result = await tool.execute(file_path=str(d))
        assert isinstance(result, str)
        assert "不是文件" in result


# ---------------------------------------------------------------------------
# Word/Excel/PPT 路由
# ---------------------------------------------------------------------------

class TestOfficeRouting:
    """Office 文档路由测试"""

    @pytest.mark.asyncio
    async def test_word_document_routing(self, tmp_path):
        """Word 文档走专用解析器（mock）"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        # 创建一个假 docx 文件
        f = tmp_path / "test.docx"
        f.write_text("fake docx", encoding="utf-8")

        with patch("src.tools.file.read_tool.is_word_document", return_value=True), \
             patch.object(tool, "_read_word_document") as mock_word:
            mock_word.return_value = {
                "content": "1\tHello from Word",
                "total_lines": 1,
                "read_lines": 1,
            }
            result = await tool.execute(file_path=str(f))

        mock_word.assert_called_once()
        assert isinstance(result, dict)
        assert "content" in result

    @pytest.mark.asyncio
    async def test_excel_document_routing(self, tmp_path):
        """Excel 文档走专用解析器（mock）"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "test.xlsx"
        f.write_text("fake xlsx", encoding="utf-8")

        with patch("src.tools.file.read_tool.is_excel_document", return_value=True), \
             patch.object(tool, "_read_excel_document") as mock_excel:
            mock_excel.return_value = {
                "content": "1\tSheet data",
                "total_lines": 1,
                "read_lines": 1,
            }
            result = await tool.execute(file_path=str(f))

        mock_excel.assert_called_once()

    @pytest.mark.asyncio
    async def test_ppt_document_routing(self, tmp_path):
        """PPT 文档走专用解析器（mock）"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "test.pptx"
        f.write_text("fake pptx", encoding="utf-8")

        with patch("src.tools.file.read_tool.is_ppt_document", return_value=True), \
             patch.object(tool, "_read_ppt_document") as mock_ppt:
            mock_ppt.return_value = {
                "content": "1\tSlide content",
                "total_lines": 1,
                "read_lines": 1,
            }
            result = await tool.execute(file_path=str(f))

        mock_ppt.assert_called_once()


# ---------------------------------------------------------------------------
# 编码检测
# ---------------------------------------------------------------------------

class TestEncodingDetection:
    """编码检测测试"""

    @pytest.mark.asyncio
    async def test_gbk_file(self, tmp_path):
        """GBK 编码文件读取"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "gbk.txt"
        f.write_text("你好世界\n第二行", encoding="gbk")

        result = await tool.execute(file_path=str(f))
        assert isinstance(result, dict)
        assert "你好世界" in result["content"]

    @pytest.mark.asyncio
    async def test_utf8_bom_file(self, tmp_path):
        """UTF-8 BOM 文件读取"""
        from src.tools.file.read_tool import ReadTool
        tool = ReadTool()

        f = tmp_path / "bom.txt"
        f.write_bytes(b'\xef\xbb\xbfBOM content\nline2')

        result = await tool.execute(file_path=str(f))
        assert isinstance(result, dict)
        assert "BOM content" in result["content"]


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------

class TestFormatWithLineNumbers:
    """_format_with_line_numbers 测试"""

    def test_basic_formatting(self):
        from src.tools.file.read_tool import ReadTool
        result = ReadTool._format_with_line_numbers(["alpha", "beta", "gamma"], 1)
        lines = result.split("\n")
        assert len(lines) == 3
        assert lines[0] == "1\talpha"
        assert lines[1] == "2\tbeta"
        assert lines[2] == "3\tgamma"

    def test_start_offset(self):
        from src.tools.file.read_tool import ReadTool
        result = ReadTool._format_with_line_numbers(["x", "y"], 10)
        lines = result.split("\n")
        assert lines[0] == "10\tx"
        assert lines[1] == "11\ty"

    def test_empty_lines(self):
        from src.tools.file.read_tool import ReadTool
        result = ReadTool._format_with_line_numbers([], 1)
        assert result == ""

    def test_line_number_width_alignment(self):
        """行号右对齐：个位数和两位数混排时对齐"""
        from src.tools.file.read_tool import ReadTool
        lines = [f"line{i}" for i in range(9, 12)]  # 9, 10, 11
        result = ReadTool._format_with_line_numbers(lines, 9)
        output_lines = result.split("\n")
        assert output_lines[0] == " 9\tline9"
        assert output_lines[1] == "10\tline10"
        assert output_lines[2] == "11\tline11"
