"""
竞品研究报告 HTML 合并脚本单元测试

测试 scripts/html_report_merger.py 中的合并功能：
- extract_html_body
- extract_html_head_styles
- extract_page_title
- merge_html_files
- CLI main()
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.tools]

# 被测模块路径
MERGER_SCRIPT = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "src" / "skills" / "competitor-research-1.0.0" / "scripts" / "html_report_merger.py"
)

# 导入被测模块（直接 import）
sys.path.insert(0, str(MERGER_SCRIPT.parent))
from html_report_merger import (
    extract_html_body,
    extract_html_head_styles,
    extract_page_title,
    merge_html_files,
)

# ---- 测试用 HTML 片段 ----

HTML_PAGE_1 = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>Test Page 1</title>
  <style>
    body { font-family: sans-serif; }
    h1 { color: #1a56db; }
  </style>
</head>
<body>
  <header>
    <h1>公司概况</h1>
  </header>
  <main>
    <p>This is test content for page 1.</p>
  </main>
</body>
</html>
"""

HTML_PAGE_2 = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>Test Page 2</title>
  <style>
    body { font-family: sans-serif; }
    .card { border: 1px solid #e5e7eb; }
  </style>
</head>
<body>
  <header>
    <h1>产品功能分析</h1>
  </header>
  <main>
    <p>This is test content for page 2.</p>
  </main>
</body>
</html>
"""

HTML_NO_BODY = "<h1>No Body Tag</h1><p>Some content</p>"

HTML_BODY_WITH_ATTRS = """\
<html>
<body class="main" data-page="1">
  <p>Content with body attributes</p>
</body>
</html>
"""


# ============================================================
# TestExtractHtmlBody
# ============================================================

class TestExtractHtmlBody:
    def test_normal_html_returns_body_content(self):
        result = extract_html_body(HTML_PAGE_1)
        assert "<header>" in result
        assert "<h1>公司概况</h1>" in result
        assert "<head>" not in result
        assert "</html>" not in result

    def test_no_body_tags_returns_entire_content(self):
        result = extract_html_body(HTML_NO_BODY)
        assert "<h1>No Body Tag</h1>" in result

    def test_body_with_attributes_extracts_correctly(self):
        result = extract_html_body(HTML_BODY_WITH_ATTRS)
        assert "<p>Content with body attributes</p>" in result
        assert '<body class="main"' not in result

    def test_empty_html_returns_empty(self):
        result = extract_html_body("")
        assert result == ""

    def test_only_body_open_tag_no_close(self):
        html = "<html><body><p>no closing body</p></html>"
        result = extract_html_body(html)
        assert "<p>no closing body</p>" in result

    def test_multiple_body_tags_uses_first_start_last_end(self):
        html = "<body><p>first</p></body><body><p>second</p></body>"
        result = extract_html_body(html)
        assert "<p>first</p>" in result
        assert "<p>second</p>" in result

    def test_body_tag_without_closing_bracket(self):
        html = "<body<p>broken</p></body>"
        result = extract_html_body(html)
        # body start tag not properly closed, returns entire content
        assert "broken" in result


# ============================================================
# TestExtractHtmlHeadStyles
# ============================================================

class TestExtractHtmlHeadStyles:
    def test_extracts_styles_from_head(self):
        styles = extract_html_head_styles(HTML_PAGE_1)
        assert len(styles) == 1
        assert "font-family: sans-serif" in styles[0]
        assert "color: #1a56db" in styles[0]

    def test_html_without_head_returns_empty(self):
        styles = extract_html_head_styles("<html><body><p>no head</p></body></html>")
        assert styles == []

    def test_styles_in_body_are_not_extracted(self):
        html = "<html><head></head><body><style>body{color:red;}</style></body></html>"
        styles = extract_html_head_styles(html)
        assert styles == []

    def test_multiple_style_blocks_in_head(self):
        html = "<html><head><style>a{}</style><style>b{}</style></head><body></body></html>"
        styles = extract_html_head_styles(html)
        assert len(styles) == 2

    def test_empty_head_returns_empty_list(self):
        html = "<html><head></head><body></body></html>"
        styles = extract_html_head_styles(html)
        assert styles == []

    def test_style_with_attributes(self):
        html = '<html><head><style type="text/css">body{}</style></head><body></body></html>'
        styles = extract_html_head_styles(html)
        assert len(styles) == 1
        assert "body{}" in styles[0]


# ============================================================
# TestExtractPageTitle
# ============================================================

class TestExtractPageTitle:
    def test_extracts_h1_text(self):
        title = extract_page_title(HTML_PAGE_1, "fallback")
        assert title == "公司概况"

    def test_no_h1_returns_fallback(self):
        title = extract_page_title("<html><body><p>no h1</p></body></html>", "fallback")
        assert title == "fallback"

    def test_h1_with_nested_tags_strips_them(self):
        title = extract_page_title("<h1><span>Inner</span> Title</h1>", "fb")
        assert title == "Inner Title"

    def test_empty_h1_returns_fallback(self):
        title = extract_page_title("<h1></h1>", "fallback")
        assert title == "fallback"

    def test_whitespace_only_h1_returns_fallback(self):
        title = extract_page_title("<h1>   </h1>", "fallback")
        assert title == "fallback"

    def test_h1_with_attributes(self):
        title = extract_page_title('<h1 class="title">My Title</h1>', "fb")
        assert title == "My Title"


# ============================================================
# TestMergeHtmlFiles
# ============================================================

class TestMergeHtmlFiles:
    @pytest.fixture
    def html_files(self, tmp_path):
        """创建两个测试 HTML 文件"""
        p1 = tmp_path / "page1.html"
        p1.write_text(HTML_PAGE_1, encoding="utf-8")
        p2 = tmp_path / "page2.html"
        p2.write_text(HTML_PAGE_2, encoding="utf-8")
        return [str(p1), str(p2)]

    def test_merge_two_files_produces_valid_html(self, html_files):
        result = merge_html_files(html_files)
        assert "<!DOCTYPE html>" in result
        assert "<html" in result
        assert "公司概况" in result
        assert "产品功能分析" in result

    def test_merge_skips_empty_files(self, tmp_path):
        p1 = tmp_path / "page1.html"
        p1.write_text(HTML_PAGE_1, encoding="utf-8")
        empty = tmp_path / "empty.html"
        empty.write_text("", encoding="utf-8")
        result = merge_html_files([str(p1), str(empty)])
        assert "公司概况" in result

    def test_merge_skips_nonexistent_files(self, tmp_path):
        p1 = tmp_path / "page1.html"
        p1.write_text(HTML_PAGE_1, encoding="utf-8")
        result = merge_html_files([str(p1), "/nonexistent/file.html"])
        assert "公司概况" in result

    def test_merge_produces_toc_with_titles(self, html_files):
        result = merge_html_files(html_files)
        assert "toc-item" in result
        assert "公司概况" in result
        assert "产品功能分析" in result

    def test_merge_produces_navigation_buttons(self, html_files):
        result = merge_html_files(html_files)
        assert "上一页" in result
        assert "下一页" in result

    def test_first_page_is_active_by_default(self, html_files):
        result = merge_html_files(html_files)
        assert 'class="page-section active"' in result
        assert 'id="page-0"' in result

    def test_merge_collects_styles_from_all_pages(self, html_files):
        result = merge_html_files(html_files)
        assert "color: #1a56db" in result
        assert "border: 1px solid #e5e7eb" in result

    def test_merge_no_valid_files_raises_error(self):
        with pytest.raises(ValueError, match="没有有效的文件"):
            merge_html_files(["/nonexistent1.html", "/nonexistent2.html"])

    def test_merge_page_info_shows_total(self, html_files):
        result = merge_html_files(html_files)
        assert "1 / 2" in result


# ============================================================
# TestCLI
# ============================================================

class TestCLI:
    @pytest.fixture
    def html_files(self, tmp_path):
        p1 = tmp_path / "page1.html"
        p1.write_text(HTML_PAGE_1, encoding="utf-8")
        p2 = tmp_path / "page2.html"
        p2.write_text(HTML_PAGE_2, encoding="utf-8")
        return [str(p1), str(p2)]

    def test_cli_merge_success(self, tmp_path, html_files):
        output = tmp_path / "report.html"
        params = json.dumps({
            "file_paths": html_files,
            "output_path": str(output),
            "title": "Test Report",
        })
        result = subprocess.run(
            [sys.executable, str(MERGER_SCRIPT), params],
            capture_output=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout.decode("utf-8"))
        assert data["success"] is True
        assert data["merged_count"] == 2
        assert output.exists()

    def test_cli_missing_params(self):
        result = subprocess.run(
            [sys.executable, str(MERGER_SCRIPT)],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert result.returncode != 0

    def test_cli_less_than_two_files(self, tmp_path):
        p1 = tmp_path / "p1.html"
        p1.write_text(HTML_PAGE_1, encoding="utf-8")
        params = json.dumps({"file_paths": [str(p1)], "output_path": str(tmp_path / "out.html")})
        result = subprocess.run(
            [sys.executable, str(MERGER_SCRIPT), params],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert result.returncode != 0

    def test_cli_no_output_path(self, tmp_path, html_files):
        params = json.dumps({"file_paths": html_files})
        result = subprocess.run(
            [sys.executable, str(MERGER_SCRIPT), params],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert result.returncode != 0

    def test_cli_output_file_has_navigation(self, tmp_path, html_files):
        output = tmp_path / "report.html"
        params = json.dumps({
            "file_paths": html_files,
            "output_path": str(output),
            "title": "Test Report",
        })
        subprocess.run(
            [sys.executable, str(MERGER_SCRIPT), params],
            capture_output=True,
        )
        content = output.read_text(encoding="utf-8")
        assert "toc-panel" in content
        assert "goToPage" in content
        assert "Test Report" in content
