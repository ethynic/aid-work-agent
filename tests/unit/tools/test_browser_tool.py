"""
浏览器工具单元测试

测试浏览器工具定义（mock Playwright）
"""

import pytest

pytestmark = [pytest.mark.tools, pytest.mark.browser]
from unittest.mock import MagicMock


class TestBrowserToolDefinitions:
    """浏览器工具定义测试"""

    def test_browser_open_tool_definition(self):
        from src.tools.browser import BrowserOpenTool
        tool = BrowserOpenTool()
        assert tool.name == "browser_open"
        defn = tool.to_tool_definition()
        assert "input_schema" in defn

    def test_browser_get_content_tool_definition(self):
        from src.tools.browser import BrowserGetContentTool
        tool = BrowserGetContentTool()
        assert tool.name == "browser_get_content"

    def test_browser_close_tool_definition(self):
        from src.tools.browser import BrowserCloseTool
        tool = BrowserCloseTool()
        assert tool.name == "browser_close"

    def test_browser_screenshot_tool_definition(self):
        from src.tools.browser import BrowserScreenshotTool
        tool = BrowserScreenshotTool()
        assert tool.name == "browser_screenshot"

    def test_browser_navigate_tool_definition(self):
        from src.tools.browser import BrowserNavigateTool
        tool = BrowserNavigateTool()
        assert tool.name == "browser_navigate"


class TestBrowserToolParameterValidation:
    """浏览器工具参数验证"""

    def test_browser_open_requires_url(self):
        from src.tools.browser import BrowserOpenTool
        tool = BrowserOpenTool()
        missing = tool.get_missing_parameters()
        assert "url" in missing

    def test_browser_open_valid_with_url(self):
        from src.tools.browser import BrowserOpenTool
        tool = BrowserOpenTool()
        assert tool.validate_parameters(url="https://example.com")
