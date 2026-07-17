"""有头浏览器手工验收。

默认不收集 e2e；即使显式选中 e2e，也只有设置
RUN_HEADED_BROWSER_E2E=1 才会打开 GUI 浏览器。
"""

import os
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
    pytest.mark.skipif(
        os.getenv("RUN_HEADED_BROWSER_E2E") != "1",
        reason="有头手工验收；设置 RUN_HEADED_BROWSER_E2E=1 显式运行",
    ),
]


@pytest.mark.asyncio
async def test_headed_browser_complete_workflow(tmp_path: Path):
    """手工观察打开、读取、导航、截图与关闭流程。"""
    from src.tools.browser import (
        BrowserCloseTool,
        BrowserGetContentTool,
        BrowserNavigateTool,
        BrowserOpenTool,
        BrowserScreenshotTool,
    )

    session_id = "headed_manual_workflow"
    screenshot_path = tmp_path / "workflow_screenshot.png"
    try:
        opened = await BrowserOpenTool().execute(
            url="https://www.example.com",
            session_id=session_id,
            headless=False,
            wait_for="load",
        )
        assert opened["success"]

        content = await BrowserGetContentTool().execute(session_id=session_id, format="text")
        assert content["success"]
        assert content["content_length"] > 0

        second_page = await BrowserOpenTool().execute(
            url="https://www.python.org",
            session_id=session_id,
            wait_for="load",
        )
        assert second_page["success"]

        backed = await BrowserNavigateTool().execute(action="back", session_id=session_id)
        assert backed["success"]

        forwarded = await BrowserNavigateTool().execute(action="forward", session_id=session_id)
        assert forwarded["success"]

        reloaded = await BrowserNavigateTool().execute(action="reload", session_id=session_id)
        assert reloaded["success"]

        screenshot = await BrowserScreenshotTool().execute(
            session_id=session_id,
            path=str(screenshot_path),
            full_page=False,
        )
        assert screenshot["success"]
        assert screenshot_path.is_file()
    finally:
        closed = await BrowserCloseTool().execute(session_id=session_id)
        assert closed["success"]
