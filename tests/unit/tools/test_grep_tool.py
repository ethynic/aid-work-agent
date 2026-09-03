"""
grep 工具单元测试

覆盖：
- 正则匹配、行号正确（1-based）
- context / before_context / after_context
- 三种 output_mode（content / files_with_matches / count）
- glob 过滤
- max_matches 截断
- 无匹配（success=True, count=0）
- 路径越权拒绝
- rg 不可用降级（FileNotFoundError → 明确错误，不崩溃）
- 目录搜索 + 文件搜索
- ignore_case

注意：单测用 tmp_path 创建临时文件来搜，不依赖项目现有文件。
pytest 的 tmp_path 位于系统临时目录下，命中 read/grep 的路径白名单。
"""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.tools]

# rg 是否可用（决定部分测试是否跳过）
_RG_AVAILABLE = shutil.which("rg") is not None
skip_if_no_rg = pytest.mark.skipif(not _RG_AVAILABLE, reason="ripgrep 未安装")


# ---------------------------------------------------------------------------
# 工具定义
# ---------------------------------------------------------------------------

class TestGrepToolDefinition:
    """工具定义测试"""

    def test_tool_name(self):
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()
        assert tool.name == "grep"

    def test_tool_category(self):
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()
        assert tool.category == "file"

    def test_tool_definition_has_schema(self):
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()
        defn = tool.to_tool_definition()
        assert defn["name"] == "grep"
        assert "input_schema" in defn
        props = defn["input_schema"].get("properties", {})
        assert "pattern" in props
        assert "path" in props
        assert "glob" in props
        assert "output_mode" in props
        assert "ignore_case" in props
        assert "context" in props
        assert "before_context" in props
        assert "after_context" in props
        assert "max_matches" in props


# ---------------------------------------------------------------------------
# content 模式
# ---------------------------------------------------------------------------

@skip_if_no_rg
class TestGrepContentMode:
    """content 模式测试"""

    @pytest.mark.asyncio
    async def test_basic_match_and_line_number(self, tmp_path):
        """基本匹配 + 行号 1-based 正确"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        f = tmp_path / "sample.txt"
        f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

        result = await tool.execute(pattern="beta", path=str(f))

        assert result["success"] is True
        assert result["count"] == 1
        m = result["matches"][0]
        assert m["line"] == 2  # beta 在第 2 行（1-based）
        assert "beta" in m["content"]
        assert result["truncated"] is False

    @pytest.mark.asyncio
    async def test_multiple_matches(self, tmp_path):
        """多匹配：每个匹配项独立返回"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        f = tmp_path / "multi.txt"
        f.write_text("foo\nbar\nfoo\nbaz\nfoo\n", encoding="utf-8")

        result = await tool.execute(pattern="foo", path=str(f))

        assert result["success"] is True
        assert result["count"] == 3
        # 行号 1, 3, 5
        lines = [m["line"] for m in result["matches"]]
        assert lines == [1, 3, 5]

    @pytest.mark.asyncio
    async def test_regex_pattern(self, tmp_path):
        """正则匹配"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        f = tmp_path / "log.txt"
        f.write_text("error: 404\ninfo: ok\nerror: 500\n", encoding="utf-8")

        result = await tool.execute(pattern=r"error: \d+", path=str(f))

        assert result["success"] is True
        assert result["count"] == 2
        lines = [m["line"] for m in result["matches"]]
        assert lines == [1, 3]

    @pytest.mark.asyncio
    async def test_ignore_case(self, tmp_path):
        """ignore_case 忽略大小写"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        f = tmp_path / "case.txt"
        f.write_text("Hello\nHELLO\nhello\nworld\n", encoding="utf-8")

        # 不忽略大小写：只匹配小写 hello（第 3 行）
        result = await tool.execute(pattern="hello", path=str(f))
        assert result["count"] == 1
        assert result["matches"][0]["line"] == 3

        # 忽略大小写：匹配 3 行
        result = await tool.execute(pattern="hello", path=str(f), ignore_case=True)
        assert result["count"] == 3

    @pytest.mark.asyncio
    async def test_no_match(self, tmp_path):
        """无匹配：success=True, count=0（不是错误）"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        f = tmp_path / "nomatch.txt"
        f.write_text("alpha\nbeta\n", encoding="utf-8")

        result = await tool.execute(pattern="zzzznotfound", path=str(f))

        assert result["success"] is True
        assert result["count"] == 0
        assert result["matches"] == []
        assert result["truncated"] is False

    @pytest.mark.asyncio
    async def test_directory_search(self, tmp_path):
        """目录搜索：跨多个文件"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        (tmp_path / "a.txt").write_text("target\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("other\ntarget line\n", encoding="utf-8")

        result = await tool.execute(pattern="target", path=str(tmp_path))

        assert result["success"] is True
        assert result["count"] == 2
        # 两个不同文件
        files = {m["file"] for m in result["matches"]}
        assert len(files) == 2


# ---------------------------------------------------------------------------
# 上下文
# ---------------------------------------------------------------------------

@skip_if_no_rg
class TestGrepContext:
    """上下文行测试"""

    @pytest.mark.asyncio
    async def test_context_flag(self, tmp_path):
        """context 同时设前后上下文（匹配行本身仍计入 matches）"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        f = tmp_path / "ctx.txt"
        f.write_text("l1\nl2\nl3\nTARGET\nl5\nl6\nl7\n", encoding="utf-8")

        result = await tool.execute(pattern="TARGET", path=str(f), context=2)

        assert result["success"] is True
        assert result["count"] == 1
        assert result["matches"][0]["line"] == 4
        assert "TARGET" in result["matches"][0]["content"]

    @pytest.mark.asyncio
    async def test_before_and_after_context(self, tmp_path):
        """before_context / after_context 单独设置"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        f = tmp_path / "ctx2.txt"
        f.write_text("l1\nl2\nTARGET\nl4\nl5\n", encoding="utf-8")

        result = await tool.execute(
            pattern="TARGET", path=str(f), before_context=1, after_context=1
        )
        assert result["success"] is True
        assert result["count"] == 1
        assert result["matches"][0]["line"] == 3


# ---------------------------------------------------------------------------
# output_mode
# ---------------------------------------------------------------------------

@skip_if_no_rg
class TestGrepOutputModes:
    """三种 output_mode 测试"""

    @pytest.mark.asyncio
    async def test_files_with_matches(self, tmp_path):
        """files_with_matches：返回文件名列表"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("nothing\n", encoding="utf-8")
        (tmp_path / "c.txt").write_text("needle here\n", encoding="utf-8")

        result = await tool.execute(
            pattern="needle", path=str(tmp_path), output_mode="files_with_matches"
        )

        assert result["success"] is True
        assert "files" in result
        assert "matches" not in result  # files_with_matches 不返回 matches
        # 只有 a.txt 和 c.txt 含匹配
        file_names = {Path(f).name for f in result["files"]}
        assert file_names == {"a.txt", "c.txt"}
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_count_mode(self, tmp_path):
        """count：返回每文件匹配数"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        (tmp_path / "a.txt").write_text("foo\nfoo\nfoo\n", encoding="utf-8")  # 3
        (tmp_path / "b.txt").write_text("foo\nbar\n", encoding="utf-8")  # 1

        result = await tool.execute(
            pattern="foo", path=str(tmp_path), output_mode="count"
        )

        assert result["success"] is True
        assert "counts" in result
        counts_map = {Path(c["file"]).name: c["count"] for c in result["counts"]}
        assert counts_map["a.txt"] == 3
        assert counts_map["b.txt"] == 1


# ---------------------------------------------------------------------------
# glob 过滤
# ---------------------------------------------------------------------------

@skip_if_no_rg
class TestGrepGlob:
    """glob 过滤测试"""

    @pytest.mark.asyncio
    async def test_glob_filters_by_extension(self, tmp_path):
        """glob 按扩展名过滤"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("needle\n", encoding="utf-8")
        (tmp_path / "c.py").write_text("needle\n", encoding="utf-8")

        result = await tool.execute(
            pattern="needle", path=str(tmp_path), glob="*.py"
        )

        assert result["success"] is True
        files = {Path(m["file"]).name for m in result["matches"]}
        assert files == {"a.py", "c.py"}
        # b.txt 不应出现
        assert "b.txt" not in files


# ---------------------------------------------------------------------------
# max_matches 截断
# ---------------------------------------------------------------------------

@skip_if_no_rg
class TestGrepMaxMatches:
    """max_matches 截断测试"""

    @pytest.mark.asyncio
    async def test_max_matches_truncates(self, tmp_path):
        """超过 max_matches 时 truncated=True 且只返回 max_matches 条"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        f = tmp_path / "many.txt"
        # 100 行 match
        f.write_text("\n".join(["match"] * 100) + "\n", encoding="utf-8")

        result = await tool.execute(pattern="match", path=str(f), max_matches=10)

        assert result["success"] is True
        assert result["count"] == 10  # 只返回 10 条
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_max_matches_no_truncate_when_under_limit(self, tmp_path):
        """未超 max_matches 时 truncated=False"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        f = tmp_path / "few.txt"
        f.write_text("match\nmatch\nmatch\n", encoding="utf-8")

        result = await tool.execute(pattern="match", path=str(f), max_matches=50)
        assert result["count"] == 3
        assert result["truncated"] is False


# ---------------------------------------------------------------------------
# 错误与降级
# ---------------------------------------------------------------------------

class TestGrepErrors:
    """错误处理与降级测试"""

    @pytest.mark.asyncio
    async def test_path_not_found(self, tmp_path):
        """路径不存在：返回明确错误"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        result = await tool.execute(
            pattern="foo", path=str(tmp_path / "nonexistent.txt")
        )
        assert result["success"] is False
        assert "不存在" in result["error"]

    @pytest.mark.asyncio
    async def test_path_outside_whitelist(self):
        """路径越权拒绝：项目根 + 临时目录之外的路径"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        # Windows 系统目录，不在项目根也不在临时目录
        # 用一个确定存在的、绝对在白名单外的路径
        import tempfile as _tmp
        # 构造一个肯定不在白名单的路径：往上跳多层
        outside = Path(_tmp.gettempdir()).parent.parent / "definitely_outside_aid_12345"
        # 即使不存在，白名单检查也应先拦（path.exists 在白名单通过后才检查，
        # 但越权路径会在 relative_to 校验时抛 ValueError）
        result = await tool.execute(pattern="foo", path=str(outside))
        assert result["success"] is False
        assert "允许范围" in result["error"] or "超出" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_pattern(self, tmp_path):
        """空 pattern：返回错误"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        f = tmp_path / "x.txt"
        f.write_text("data\n", encoding="utf-8")

        result = await tool.execute(pattern="", path=str(f))
        assert result["success"] is False
        assert "pattern" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_path(self):
        """空 path：返回错误"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        result = await tool.execute(pattern="foo", path="")
        assert result["success"] is False
        assert "path" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_regex(self, tmp_path):
        """非法正则：rg 返回退出码 2，返回明确错误"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        f = tmp_path / "x.txt"
        f.write_text("data\n", encoding="utf-8")

        result = await tool.execute(pattern="[invalid", path=str(f))
        assert result["success"] is False
        assert "搜索失败" in result["error"]

    @pytest.mark.asyncio
    async def test_rg_not_available_graceful(self, tmp_path):
        """rg 不可用时优雅降级（返回明确错误，不崩溃）"""
        from src.tools.file.grep_tool import GrepTool
        tool = GrepTool()

        f = tmp_path / "x.txt"
        f.write_text("data\n", encoding="utf-8")

        # mock subprocess.run 抛 FileNotFoundError（rg 不存在）
        with patch(
            "src.tools.file.grep_tool.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            result = await tool.execute(pattern="data", path=str(f))

        assert result["success"] is False
        assert "ripgrep" in result["error"]
        assert "未安装" in result["error"]


# ---------------------------------------------------------------------------
# 配合 spill 文件的真实闭环（集成性质，但用临时文件）
# ---------------------------------------------------------------------------

@skip_if_no_rg
class TestGrepOnSpilledFile:
    """在 _spill 落盘的大文件上 grep，验证闭环"""

    @pytest.mark.asyncio
    async def test_grep_spilled_large_content(self, tmp_path, monkeypatch):
        """落盘一个大文件，grep 能搜到并返回正确行号"""
        # 隔离 spill 目录到 tmp_path
        monkeypatch.setattr(
            "src.tools._spill.tempfile.gettempdir", lambda: str(tmp_path)
        )
        from src.tools._spill import spill_large_content
        from src.tools.file.grep_tool import GrepTool

        tool = GrepTool()

        # 构造大内容：每行一个标记，目标字段在第 100 行
        lines = [f"line {i}" for i in range(200)]
        lines[99] = "TARGET_FIELD: found me"  # 第 100 行（0-based idx 99）
        content = "\n".join(lines)

        spilled = spill_large_content(content, prefix="httpapi_response_", suffix=".json")
        file_path = spilled["file_path"]

        # grep 搜目标字段
        result = await tool.execute(pattern="TARGET_FIELD", path=file_path)

        assert result["success"] is True
        assert result["count"] == 1
        assert result["matches"][0]["line"] == 100  # 1-based
        assert "TARGET_FIELD: found me" in result["matches"][0]["content"]
