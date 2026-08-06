"""Playwright Chromium 检测 —— 启动时验证浏览器已安装。"""

from __future__ import annotations

import os
from pathlib import Path


def ensure_playwright_chromium() -> None:
    """检测 Playwright Chromium 是否已安装，缺失则 fail-loud 提示安装。

    Raises:
        RuntimeError: PLAYWRIGHT_NOT_INSTALLED
    """
    cache_dir = Path(os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright",
    ))
    chromium_dirs = list(cache_dir.glob("chromium-*"))
    if not chromium_dirs:
        raise RuntimeError(
            "PLAYWRIGHT_NOT_INSTALLED: 未检测到 Playwright Chromium 浏览器。\n"
            "请运行安装目录下的 install-playwright.cmd，或在命令行执行：\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )
