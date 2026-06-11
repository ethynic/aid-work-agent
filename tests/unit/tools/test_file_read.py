"""
file_read 工具单元测试

测试覆盖：行号范围读取、标记范围读取、返回格式、工具定义、边界情况、模式优先级
"""

import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.tools


@pytest.fixture
def tool():
    """创建 FileReaderTool 实例"""
    from src.tools.file.file_reader_tool import FileReaderTool
    return FileReaderTool()


@pytest.fixture
def tmp_dir():
    """创建临时目录"""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def sample_file(tmp_dir):
    """创建 100 行的测试文件"""
    path = os.path.join(tmp_dir, "sample.txt")
    lines = [f"line {i}" for i in range(100)]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


@pytest.fixture
def large_file(tmp_dir):
    """创建 3000 行的大文件"""
    path = os.path.join(tmp_dir, "large.txt")
    lines = [f"line {i}" for i in range(3000)]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


@pytest.fixture
def html_file(tmp_dir):
    """创建 HTML 测试文件，含 style 块"""
    path = os.path.join(tmp_dir, "test.html")
    content = """<!DOCTYPE html>
<html>
<head>
<title>Test</title>
<style type="text/css">
body { margin: 0; }
.container { width: 100%; }
</style>
</head>
<body>
<!-- SLIDES_HERE -->
<div class="slide">Slide 1</div>
<div class="slide">Slide 2</div>
<!-- END_SLIDES -->
<footer>Footer</footer>
</body>
</html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ── TestReadRange: 行号范围读取 ──


class TestReadRange:

    @pytest.mark.asyncio
    async def test_read_full_file(self, tool, sample_file):
        """不传参数 → 返回全部内容，带行号"""
        result = await tool.execute(file_path=sample_file)
        assert isinstance(result, dict)
        assert result["total_lines"] == 100
        assert result["read_lines"] == 100
        assert "content" in result
        lines = result["content"].split("\n")
        assert lines[0].strip().startswith("1\t")
        assert "line 0" in lines[0]

    @pytest.mark.asyncio
    async def test_read_with_limit(self, tool, sample_file):
        """limit=50 → 返回前 50 行"""
        result = await tool.execute(file_path=sample_file, limit=50)
        assert result["read_lines"] == 50
        assert result["total_lines"] == 100

    @pytest.mark.asyncio
    async def test_read_with_offset(self, tool, sample_file):
        """offset=100 → 从第 101 行开始（但文件只有 100 行，返回空）"""
        result = await tool.execute(file_path=sample_file, offset=100)
        assert result["read_lines"] == 0
        assert result["total_lines"] == 100

    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, tool, sample_file):
        """offset=10, limit=50 → 返回第 11-60 行"""
        result = await tool.execute(file_path=sample_file, offset=10, limit=50)
        assert result["read_lines"] == 50
        assert result["total_lines"] == 100
        lines = result["content"].split("\n")
        assert "line 10" in lines[0]
        assert "line 59" in lines[-1]

    @pytest.mark.asyncio
    async def test_read_offset_exceeds_total(self, tool, sample_file):
        """offset=99999 → 返回空内容（不报错）"""
        result = await tool.execute(file_path=sample_file, offset=99999)
        assert result["read_lines"] == 0
        assert result["total_lines"] == 100

    @pytest.mark.asyncio
    async def test_read_truncated_with_hint(self, tool, large_file):
        """文件 3000 行 → 截断在 2000 行，含 next_hint"""
        result = await tool.execute(file_path=large_file)
        assert result["read_lines"] == 2000
        assert result["total_lines"] == 3000
        assert "next_hint" in result
        assert "offset=2000" in result["next_hint"]

    @pytest.mark.asyncio
    async def test_line_number_format(self, tool, sample_file):
        """行号格式为 cat -n（右对齐 + tab）"""
        result = await tool.execute(file_path=sample_file, limit=3)
        lines = result["content"].split("\n")
        # 第一行应该是 "     1\tline 0"
        assert lines[0] == "     1\tline 0"
        assert lines[1] == "     2\tline 1"
        assert lines[2] == "     3\tline 2"

    @pytest.mark.asyncio
    async def test_negative_offset_treated_as_none(self, tool, sample_file):
        """offset 为负数时视为未指定，从文件开头读取"""
        result = await tool.execute(file_path=sample_file, offset=-1)
        assert result["read_lines"] == 100
        lines = result["content"].split("\n")
        assert "line 0" in lines[0]

    @pytest.mark.asyncio
    async def test_negative_limit_treated_as_none(self, tool, sample_file):
        """limit 为负数时视为未指定，读取全部"""
        result = await tool.execute(file_path=sample_file, limit=-5)
        assert result["read_lines"] == 100


# ── TestReadSection: 标记范围读取 ──


class TestReadSection:

    @pytest.mark.asyncio
    async def test_read_section_basic(self, tool, html_file):
        """两个标记之间的内容正确返回"""
        result = await tool.execute(
            file_path=html_file,
            section_start="<style",
            section_end="</style>"
        )
        assert isinstance(result, dict)
        assert "section_start_line" in result
        assert "section_end_line" in result
        content = result["content"]
        assert "body { margin: 0; }" in content

    @pytest.mark.asyncio
    async def test_read_section_start_not_found(self, tool, html_file):
        """起始标记不存在 → 返回错误字符串"""
        result = await tool.execute(
            file_path=html_file,
            section_start="NOT_EXIST"
        )
        assert isinstance(result, str)
        assert "未找到起始标记" in result

    @pytest.mark.asyncio
    async def test_read_section_end_not_found(self, tool, html_file):
        """结束标记不存在 → 返回错误字符串"""
        result = await tool.execute(
            file_path=html_file,
            section_start="<style",
            section_end="NOT_EXIST"
        )
        assert isinstance(result, str)
        assert "未找到结束标记" in result

    @pytest.mark.asyncio
    async def test_read_section_no_end(self, tool, html_file):
        """不传 section_end → 读到文件末尾"""
        result = await tool.execute(
            file_path=html_file,
            section_start="SLIDES_HERE"
        )
        assert isinstance(result, dict)
        assert result["read_lines"] > 0
        content = result["content"]
        assert "Footer" in content

    @pytest.mark.asyncio
    async def test_read_section_no_end_with_limit(self, tool, html_file):
        """不传 section_end + limit → 从标记行读 limit 行"""
        result = await tool.execute(
            file_path=html_file,
            section_start="SLIDES_HERE",
            limit=2
        )
        assert isinstance(result, dict)
        assert result["read_lines"] == 2

    @pytest.mark.asyncio
    async def test_read_section_lines_reported(self, tool, html_file):
        """返回 section_start_line 和 section_end_line"""
        result = await tool.execute(
            file_path=html_file,
            section_start="SLIDES_HERE",
            section_end="END_SLIDES"
        )
        assert isinstance(result, dict)
        assert result["section_start_line"] >= 1
        assert result["section_end_line"] >= result["section_start_line"]

    @pytest.mark.asyncio
    async def test_read_section_fuzzy_match(self, tool, html_file):
        """标记行包含额外文本时也能匹配（in 匹配）"""
        result = await tool.execute(
            file_path=html_file,
            section_start='<style type="text/css">',
            section_end="</style>"
        )
        assert isinstance(result, dict)
        assert result["read_lines"] > 0


# ── TestReturnFormat: 返回格式 ──


class TestReturnFormat:

    @pytest.mark.asyncio
    async def test_success_no_extra_fields(self, tool, sample_file):
        """成功时不含 success/message/file_path/file_size"""
        result = await tool.execute(file_path=sample_file)
        assert isinstance(result, dict)
        assert "success" not in result
        assert "message" not in result
        assert "file_path" not in result
        assert "file_size" not in result
        assert "encoding" not in result
        assert "start_line" not in result
        assert "end_line" not in result

    @pytest.mark.asyncio
    async def test_failure_returns_string(self, tool):
        """失败时返回 str 而非 dict"""
        result = await tool.execute(file_path="/nonexistent/file.txt")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_content_has_line_numbers(self, tool, sample_file):
        """content 每行带行号前缀"""
        result = await tool.execute(file_path=sample_file, limit=5)
        for line in result["content"].split("\n"):
            assert "\t" in line

    @pytest.mark.asyncio
    async def test_next_hint_only_when_truncated(self, tool, large_file, sample_file):
        """next_hint 仅在截断时出现"""
        # 大文件有截断
        result_big = await tool.execute(file_path=large_file)
        assert "next_hint" in result_big

        # 小文件无截断
        result_small = await tool.execute(file_path=sample_file)
        assert "next_hint" not in result_small

    @pytest.mark.asyncio
    async def test_empty_path_returns_string(self, tool):
        """空路径返回错误字符串"""
        result = await tool.execute(file_path="")
        assert isinstance(result, str)
        assert "不能为空" in result


# ── TestToolDefinition: 工具定义 ──


class TestToolDefinition:

    def test_tool_name(self, tool):
        """name 为 file_read"""
        assert tool.name == "file_read"

    def test_input_model_has_new_fields(self, tool):
        """InputModel 包含 offset/limit/section_start/section_end"""
        from src.tools.file.file_reader_tool import FileReaderInput
        fields = FileReaderInput.model_fields
        assert "offset" in fields
        assert "limit" in fields
        assert "section_start" in fields
        assert "section_end" in fields

    def test_input_model_no_old_fields(self):
        """InputModel 不含 start_line/end_line/max_size"""
        from src.tools.file.file_reader_tool import FileReaderInput
        fields = FileReaderInput.model_fields
        assert "start_line" not in fields
        assert "end_line" not in fields
        assert "max_size" not in fields

    def test_description_has_read_modes(self, tool):
        """description 包含 3 种读取模式说明"""
        assert "整文件读取" in tool.description
        assert "行号范围" in tool.description
        assert "按标记定位" in tool.description


# ── TestEdgeCases: 边界情况 ──


class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_empty_file(self, tool, tmp_dir):
        """空文件 → 返回空 content，total_lines=0"""
        path = os.path.join(tmp_dir, "empty.txt")
        with open(path, "w") as f:
            f.write("")
        result = await tool.execute(file_path=path)
        assert result["total_lines"] == 0
        assert result["read_lines"] == 0

    @pytest.mark.asyncio
    async def test_single_line_file(self, tool, tmp_dir):
        """单行文件 → 正常返回"""
        path = os.path.join(tmp_dir, "single.txt")
        with open(path, "w") as f:
            f.write("hello")
        result = await tool.execute(file_path=path)
        assert result["total_lines"] == 1
        assert result["read_lines"] == 1
        assert "hello" in result["content"]

    @pytest.mark.asyncio
    async def test_file_not_found(self, tool):
        """不存在的相对路径 → 返回错误字符串"""
        result = await tool.execute(file_path="nonexistent_file_12345.txt")
        assert isinstance(result, str)
        assert "文件不存在" in result

    @pytest.mark.asyncio
    async def test_not_a_file(self, tool, tmp_dir):
        """目录路径 → 返回错误字符串"""
        result = await tool.execute(file_path=tmp_dir)
        assert isinstance(result, str)
        assert "不是文件" in result

    @pytest.mark.asyncio
    async def test_encoding_detection_gbk(self, tool, tmp_dir):
        """GBK 编码文件 → 自动检测并正确读取"""
        path = os.path.join(tmp_dir, "gbk.txt")
        with open(path, "w", encoding="gbk") as f:
            f.write("中文测试内容\n第二行中文")
        result = await tool.execute(file_path=path)
        assert isinstance(result, dict)
        assert "中文测试内容" in result["content"]

    @pytest.mark.asyncio
    async def test_encoding_detection_utf8_bom(self, tool, tmp_dir):
        """UTF-8 BOM 文件 → 正确处理"""
        path = os.path.join(tmp_dir, "bom.txt")
        with open(path, "wb") as f:
            f.write(b'\xef\xbb\xbfUTF-8 BOM content\nSecond line')
        result = await tool.execute(file_path=path)
        assert isinstance(result, dict)
        assert "UTF-8 BOM content" in result["content"]


# ── TestPriority: 模式优先级 ──


class TestPriority:

    @pytest.mark.asyncio
    async def test_section_takes_priority_over_offset(self, tool, html_file):
        """同时传 section_start 和 offset → 走标记定位模式"""
        result = await tool.execute(
            file_path=html_file,
            offset=100,
            section_start="SLIDES_HERE",
            section_end="END_SLIDES"
        )
        assert isinstance(result, dict)
        assert "section_start_line" in result
        # 应该包含 SLIDES_HERE 区域的内容，而不是 offset=100 的内容
        assert "Slide" in result["content"]


# ── TestPathSecurity: 路径安全校验 ──


class TestPathSecurity:

    @pytest.mark.asyncio
    async def test_absolute_path_outside_project_rejected(self, tool):
        """绝对路径在项目外且非临时目录 → 拒绝"""
        result = await tool.execute(file_path="C:/Windows/System32/drivers/etc/hosts")
        assert isinstance(result, str)
        assert "超出允许范围" in result

    @pytest.mark.asyncio
    async def test_relative_path_traversal_rejected(self, tool):
        """相对路径穿越到项目外 → 拒绝"""
        result = await tool.execute(file_path="../../etc/passwd")
        assert isinstance(result, str)
        assert "超出允许范围" in result

    @pytest.mark.asyncio
    async def test_temp_file_allowed(self, tool, tmp_dir):
        """临时目录内的文件允许读取"""
        import tempfile
        # tmp_dir 就是 tempfile 创建的，属于临时目录
        path = os.path.join(tmp_dir, "temp_test.txt")
        with open(path, "w") as f:
            f.write("temp content")
        result = await tool.execute(file_path=path)
        assert isinstance(result, dict)
        assert result["read_lines"] == 1
