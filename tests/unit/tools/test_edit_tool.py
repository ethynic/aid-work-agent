"""
edit 工具单元测试

覆盖三种编辑模式（replace_string / replace_section / replace_lines）的：
- 正常路径
- 边界条件
- 异常路径（失败时原文件字节级不变、临时文件不残留）
"""

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from src.tools.file.edit_tool import EditError, EditTool

pytestmark = [pytest.mark.tools]


# ---- helpers ----

def _sha256(path: Path) -> str:
    """计算文件的 sha256 哈希"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, content: str) -> None:
    """写入测试文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ============================================================
# replace_string 模式
# ============================================================

class TestReplaceString:
    """replace_string：精确字符串替换，强制唯一匹配"""

    @pytest.mark.asyncio
    async def test_unique_match_success(self, tmp_path: Path):
        """唯一匹配时替换成功，验证替换后内容正确"""
        f = tmp_path / "test.html"
        _write(f, "<html>\n<title>旧标题</title>\n</html>")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_string",
            old_string="<title>旧标题</title>",
            new_string="<title>新标题</title>",
        )

        assert isinstance(result, dict)
        assert result["file_path"] == str(f)
        assert result["matched_lines"] == 2  # 第 2 行
        assert f.read_text(encoding="utf-8") == "<html>\n<title>新标题</title>\n</html>"

    @pytest.mark.asyncio
    async def test_multiple_matches_error_file_unchanged(self, tmp_path: Path):
        """多匹配时报错，用 sha256 验证原文件字节级不变"""
        f = tmp_path / "test.html"
        original_content = "line1 foo\nline2 foo\nline3 foo\n"
        _write(f, original_content)
        original_hash = _sha256(f)

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_string",
            old_string="foo",
            new_string="bar",
        )

        assert isinstance(result, str)
        assert "出现 3 次" in result
        assert "无法唯一匹配" in result
        assert _sha256(f) == original_hash

    @pytest.mark.asyncio
    async def test_zero_matches_error(self, tmp_path: Path):
        """0 匹配时报错"""
        f = tmp_path / "test.html"
        _write(f, "hello world\n")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_string",
            old_string="not_found",
            new_string="replacement",
        )

        assert isinstance(result, str)
        assert "未在文件中找到 old_string" in result

    @pytest.mark.asyncio
    async def test_multiline_old_string(self, tmp_path: Path):
        """多行 old_string 替换成功（Python str.replace 天然支持多行）"""
        f = tmp_path / "test.css"
        original = "  --ink: #1a1a1a;\n  --ink-rgb: 26,26,26;\n  --bg: #fff;"
        replacement = "  --ink: #0a2540;\n  --ink-rgb: 10,37,64;"
        _write(f, original)

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_string",
            old_string="--ink: #1a1a1a;\n  --ink-rgb: 26,26,26;",
            new_string=replacement,
        )

        assert isinstance(result, dict)
        content = f.read_text(encoding="utf-8")
        assert "--ink: #0a2540;" in content
        assert "--ink-rgb: 10,37,64;" in content
        assert "--ink-rgb: 26,26,26;" not in content

    @pytest.mark.asyncio
    async def test_special_regex_chars_treated_as_literal(self, tmp_path: Path):
        """old_string 包含特殊正则字符（如 .*+?）时正常工作（str.replace 不是 regex）"""
        f = tmp_path / "test.txt"
        _write(f, "pattern: a.*+?b\ncode: x.*+?b\n")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_string",
            old_string="a.*+?b",
            new_string="REPLACED",
        )

        assert isinstance(result, dict)
        content = f.read_text(encoding="utf-8")
        assert "REPLACED" in content
        # 另一个 .*+?b 不受影响（只在第一个匹配处替换）

    @pytest.mark.asyncio
    async def test_failure_preserves_original_file(self, tmp_path: Path):
        """replace_string 失败时原文件 sha256 不变"""
        f = tmp_path / "test.txt"
        _write(f, "original content\n")
        original_hash = _sha256(f)

        tool = EditTool()
        # 0 匹配
        result = await tool.execute(
            file_path=str(f),
            mode="replace_string",
            old_string="nonexistent",
            new_string="x",
        )
        assert isinstance(result, str)
        assert _sha256(f) == original_hash


# ============================================================
# replace_section 模式
# ============================================================

class TestReplaceSection:
    """replace_section：标记之间内容替换"""

    @pytest.mark.asyncio
    async def test_cross_line_markers_preserved(self, tmp_path: Path):
        """跨行：起始和结束标记在不同行，标记行保留，中间内容替换"""
        f = tmp_path / "test.html"
        _write(f, (
            "<html>\n"
            "<!-- SLIDES_HERE -->\n"
            "<section>old slide 1</section>\n"
            "<section>old slide 2</section>\n"
            "<!-- END_SLIDES -->\n"
            "</html>\n"
        ))

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_section",
            section_start="<!-- SLIDES_HERE -->",
            section_end="<!-- END_SLIDES -->",
            content="<section>new slide A</section>\n<section>new slide B</section>",
        )

        assert isinstance(result, dict)
        assert result["matched_lines"] == [2, 5]  # 1-based
        content = f.read_text(encoding="utf-8")
        assert "<!-- SLIDES_HERE -->" in content
        assert "<!-- END_SLIDES -->" in content
        assert "old slide 1" not in content
        assert "new slide A" in content

    @pytest.mark.asyncio
    async def test_same_line_markers_replaced(self, tmp_path: Path):
        """同行：起始和结束标记在同一行，整段含标记本身替换为 content"""
        f = tmp_path / "test.html"
        _write(f, (
            "<html>\n"
            "before <!-- START -->old content<!-- END --> after\n"
            "</html>\n"
        ))

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_section",
            section_start="<!-- START -->",
            section_end="<!-- END -->",
            content="NEW CONTENT",
        )

        assert isinstance(result, dict)
        content = f.read_text(encoding="utf-8")
        # 标记本身被替换
        assert "<!-- START -->" not in content
        assert "<!-- END -->" not in content
        assert "NEW CONTENT" in content
        assert "before " in content
        assert " after" in content

    @pytest.mark.asyncio
    async def test_section_start_not_found_error(self, tmp_path: Path):
        """找不到 section_start 报错"""
        f = tmp_path / "test.html"
        _write(f, "no markers here\n")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_section",
            section_start="<!-- NOT_FOUND -->",
            section_end="<!-- END -->",
            content="x",
        )

        assert isinstance(result, str)
        assert "未找到起始标记" in result

    @pytest.mark.asyncio
    async def test_section_end_not_found_error(self, tmp_path: Path):
        """找到 start 但找不到后续 section_end 报错"""
        f = tmp_path / "test.html"
        _write(f, "<!-- START -->\ncontent\nno end marker\n")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_section",
            section_start="<!-- START -->",
            section_end="<!-- END -->",
            content="x",
        )

        assert isinstance(result, str)
        assert "未在起始标记之后找到结束标记" in result

    @pytest.mark.asyncio
    async def test_multiline_content_ppt_slides(self, tmp_path: Path):
        """中间内容含多行（PPT slides 场景）"""
        f = tmp_path / "test.html"
        _write(f, (
            "<!-- SLIDES_HERE -->\n"
            "placeholder\n"
            "<!-- END_SLIDES -->\n"
        ))

        slides = (
            "<section class='slide hero'>Page 1</section>\n"
            "<section class='slide light'>Page 2</section>\n"
            "<section class='slide dark'>Page 3</section>"
        )
        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_section",
            section_start="<!-- SLIDES_HERE -->",
            section_end="<!-- END_SLIDES -->",
            content=slides,
        )

        assert isinstance(result, dict)
        content = f.read_text(encoding="utf-8")
        assert "Page 1" in content
        assert "Page 2" in content
        assert "Page 3" in content
        assert "placeholder" not in content

    @pytest.mark.asyncio
    async def test_failure_preserves_original_file(self, tmp_path: Path):
        """replace_section 失败时原文件 sha256 不变"""
        f = tmp_path / "test.txt"
        _write(f, "some content\n")
        original_hash = _sha256(f)

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_section",
            section_start="<!-- NOT_FOUND -->",
            section_end="<!-- END -->",
            content="x",
        )
        assert isinstance(result, str)
        assert _sha256(f) == original_hash


# ============================================================
# replace_lines 模式
# ============================================================

class TestReplaceLines:
    """replace_lines：按行号范围替换"""

    @pytest.mark.asyncio
    async def test_normal_range_replacement(self, tmp_path: Path):
        """正常范围替换（offset=2, limit=3）"""
        f = tmp_path / "test.txt"
        _write(f, "line0\nline1\nline2\nline3\nline4\nline5\n")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_lines",
            offset=2,
            limit=3,
            content="REPLACED",
        )

        assert isinstance(result, dict)
        assert result["matched_lines"] == [3, 5]  # 1-based: lines 3-5
        content = f.read_text(encoding="utf-8")
        assert "line0" in content
        assert "line1" in content
        assert "REPLACED" in content
        assert "line5" in content
        assert "line2" not in content

    @pytest.mark.asyncio
    async def test_replace_last_lines(self, tmp_path: Path):
        """替换最后几行（offset 接近文件末尾）"""
        f = tmp_path / "test.txt"
        _write(f, "line0\nline1\nline2\n")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_lines",
            offset=2,
            limit=5,
            content="TAIL",
        )

        assert isinstance(result, dict)
        content = f.read_text(encoding="utf-8")
        assert "line0" in content
        assert "line1" in content
        assert "TAIL" in content

    @pytest.mark.asyncio
    async def test_offset_exceeds_file_lines_error(self, tmp_path: Path):
        """offset 超出文件行数报错"""
        f = tmp_path / "test.txt"
        _write(f, "line0\nline1\n")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_lines",
            offset=10,
            limit=1,
            content="x",
        )

        assert isinstance(result, str)
        assert "offset=10 超出文件行数" in result

    @pytest.mark.asyncio
    async def test_limit_exceeds_remaining_auto_truncate(self, tmp_path: Path):
        """limit 超出剩余行数：自动截断到文件末尾（不报错）"""
        f = tmp_path / "test.txt"
        _write(f, "line0\nline1\nline2\n")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_lines",
            offset=1,
            limit=100,
            content="BIG_REPLACE",
        )

        assert isinstance(result, dict)
        content = f.read_text(encoding="utf-8")
        assert "line0" in content
        assert "BIG_REPLACE" in content
        # line1 and line2 should be gone
        assert "line1" not in content
        assert "line2" not in content

    @pytest.mark.asyncio
    async def test_failure_preserves_original_file(self, tmp_path: Path):
        """replace_lines 失败时原文件 sha256 不变"""
        f = tmp_path / "test.txt"
        _write(f, "line0\nline1\n")
        original_hash = _sha256(f)

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_lines",
            offset=50,
            limit=1,
            content="x",
        )
        assert isinstance(result, str)
        assert _sha256(f) == original_hash


# ============================================================
# 通用测试
# ============================================================

class TestEditToolGeneral:
    """通用测试：文件不存在、不支持 mode、临时文件清理等"""

    @pytest.mark.asyncio
    async def test_target_file_not_exists(self, tmp_path: Path):
        """目标文件不存在报错"""
        tool = EditTool()
        result = await tool.execute(
            file_path=str(tmp_path / "nonexistent.txt"),
            mode="replace_string",
            old_string="x",
            new_string="y",
        )

        assert isinstance(result, str)
        assert "目标文件不存在" in result

    @pytest.mark.asyncio
    async def test_unsupported_mode_error(self, tmp_path: Path):
        """不支持的 mode 报错"""
        f = tmp_path / "test.txt"
        _write(f, "content\n")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="delete",
            old_string="x",
            new_string="y",
        )

        assert isinstance(result, str)
        assert "不支持的编辑模式: delete" in result

    @pytest.mark.asyncio
    async def test_write_failure_cleans_up_tmp(self, tmp_path: Path):
        """写入失败时（mock tmp.replace 抛异常）返回错误字符串 + 临时文件已清理"""
        f = tmp_path / "test.txt"
        _write(f, "original\n")
        original_hash = _sha256(f)

        tool = EditTool()
        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            result = await tool.execute(
                file_path=str(f),
                mode="replace_string",
                old_string="original",
                new_string="modified",
            )

        assert isinstance(result, str)
        assert "写入文件失败" in result
        # 临时文件应已清理
        tmp_path_file = f.with_suffix(f.suffix + ".tmp")
        assert not tmp_path_file.exists()
        # 原文件不变
        assert _sha256(f) == original_hash

    @pytest.mark.asyncio
    async def test_success_returns_dict_with_required_fields(self, tmp_path: Path):
        """成功返回 dict 含 file_path / file_size / matched_lines"""
        f = tmp_path / "test.txt"
        _write(f, "hello world\n")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_string",
            old_string="hello",
            new_string="hi",
        )

        assert isinstance(result, dict)
        assert "file_path" in result
        assert "file_size" in result
        assert "matched_lines" in result
        assert result["file_size"] > 0

    @pytest.mark.asyncio
    async def test_empty_file_path_error(self, tmp_path: Path):
        """空文件路径报错"""
        tool = EditTool()
        result = await tool.execute(
            file_path="",
            mode="replace_string",
            old_string="x",
            new_string="y",
        )

        assert isinstance(result, str)
        assert "文件路径不能为空" in result

    @pytest.mark.asyncio
    async def test_replace_string_missing_old_string(self, tmp_path: Path):
        """replace_string 缺少 old_string 参数报错"""
        f = tmp_path / "test.txt"
        _write(f, "content\n")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_string",
            new_string="y",
        )

        assert isinstance(result, str)
        assert "old_string" in result

    @pytest.mark.asyncio
    async def test_replace_lines_missing_content(self, tmp_path: Path):
        """replace_lines 缺少 content 参数报错"""
        f = tmp_path / "test.txt"
        _write(f, "content\n")

        tool = EditTool()
        result = await tool.execute(
            file_path=str(f),
            mode="replace_lines",
            offset=0,
            limit=1,
        )

        assert isinstance(result, str)
        assert "content" in result

    @pytest.mark.asyncio
    async def test_path_outside_project_rejected(self, tmp_path: Path):
        """项目根目录外的绝对路径被拒绝"""
        tool = EditTool()
        result = await tool.execute(
            file_path="/etc/passwd",
            mode="replace_string",
            old_string="x",
            new_string="y",
        )

        # 要么文件不存在，要么路径超出范围
        assert isinstance(result, str)
        assert ("目标文件不存在" in result or "超出允许范围" in result
                or "文件路径超出允许范围" in result)

    @pytest.mark.asyncio
    async def test_all_modes_failure_preserve_sha256(self, tmp_path: Path):
        """所有 mode 失败时原文件 sha256 不变（核心安全保证）"""
        f = tmp_path / "test.txt"
        _write(f, "line0\nline1\nline2\n<!-- START -->\ncontent\n<!-- END -->\n")
        original_hash = _sha256(f)

        tool = EditTool()

        # replace_string: 0 matches
        result = await tool.execute(
            file_path=str(f), mode="replace_string",
            old_string="NOTEXIST", new_string="x",
        )
        assert isinstance(result, str)
        assert _sha256(f) == original_hash

        # replace_section: start not found
        result = await tool.execute(
            file_path=str(f), mode="replace_section",
            section_start="<!-- XXX -->", section_end="<!-- END -->", content="x",
        )
        assert isinstance(result, str)
        assert _sha256(f) == original_hash

        # replace_lines: offset out of range
        result = await tool.execute(
            file_path=str(f), mode="replace_lines",
            offset=999, limit=1, content="x",
        )
        assert isinstance(result, str)
        assert _sha256(f) == original_hash
