"""
企业微信客服 Markdown 表格渲染器

使用 Playwright 将 markdown 表格渲染为图片，通过 image 消息发送。
- 单例浏览器实例（懒加载、长生命周期）
- HTML 模板针对微信聊天窗口优化
- 元素级截图（只截表格，不截整页）
- 文件名基于内容 hash，自然去重
"""
import asyncio
import hashlib
import os
from typing import Optional

from loguru import logger

from src.core.temp_logger import tlog


class WeComKfRenderer:
    """将 markdown 表格渲染为图片。"""

    # HTML 模板：白底、系统中文栈、窄宽度适配微信气泡
    HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{
    margin: 0;
    padding: 8px;
    background: #ffffff;
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", sans-serif;
    font-size: 14px;
    color: #333333;
  }}
  table {{
    border-collapse: collapse;
    width: auto;
    max-width: 420px;
  }}
  th {{
    background: #f0f0f0;
    font-weight: bold;
    text-align: left;
    padding: 6px 10px;
    border: 1px solid #d0d0d0;
    white-space: nowrap;
  }}
  td {{
    padding: 6px 10px;
    border: 1px solid #d0d0d0;
  }}
  tr:nth-child(even) td {{
    background: #fafafa;
  }}
</style>
</head>
<body>{html_content}</body>
</html>"""

    def __init__(self, upload_dir: str = "./storage/uploads/wecom_kf"):
        self._browser = None
        self._playwright = None
        self._page = None
        self._lock = asyncio.Lock()
        self._upload_dir = upload_dir
        self._available: Optional[bool] = None
        os.makedirs(self._upload_dir, exist_ok=True)

    async def is_available(self) -> bool:
        """检查 Playwright + Chromium 是否可用（懒加载、缓存结果）。"""
        if self._available is not None:
            return self._available
        try:
            from playwright.async_api import async_playwright  # noqa: F401
            self._available = True
        except ImportError:
            logger.warning("Playwright 未安装，表格渲染为图片功能不可用")
            self._available = False
        return self._available

    async def _ensure_browser(self):
        """懒加载启动浏览器单例（在 lock 保护下调用）。"""
        if self._browser and self._page:
            try:
                # 检查 page 是否仍然有效
                await self._page.evaluate("1")
                return
            except Exception:
                logger.warning("浏览器页面已断开，重新启动")
                await self._cleanup_browser()

        from playwright.async_api import async_playwright

        # TMPDIR 已在 Dockerfile 中全局设置，Playwright 自动使用

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
        )
        self._page = await self._browser.new_page(
            viewport={'width': 440, 'height': 800},
        )
        logger.info("WeComKfRenderer 浏览器已启动")

    async def render_table(self, markdown_table: str) -> Optional[str]:
        """
        将 markdown 表格渲染为 PNG 图片。

        Args:
            markdown_table: 原始 markdown 表格文本

        Returns:
            生成的 PNG 文件路径，失败返回 None
        """
        tlog(
            "wecom_kf表格图片",
            "render_table 入口：md_len={md_len}, md_head={md_head}",
            md_len=len(markdown_table) if markdown_table else 0,
            md_head=(markdown_table or "")[:300].replace("\n", "\\n"),
        )

        if not await self.is_available():
            tlog("wecom_kf表格图片", "render_table 不可用：Playwright 未安装，返回 None")
            return None

        async with self._lock:
            try:
                await self._ensure_browser()

                import markdown

                html = markdown.markdown(markdown_table, extensions=["tables"])
                full_html = self.HTML_TEMPLATE.format(html_content=html)
                tlog(
                    "wecom_kf表格图片",
                    "render_table HTML 转换完成：html_len={html_len}, html_head={html_head}",
                    html_len=len(html),
                    html_head=html[:500].replace("\n", "\\n"),
                )

                await self._page.set_content(full_html, wait_until="networkidle")

                table_element = await self._page.query_selector("table")
                if not table_element:
                    tlog(
                        "wecom_kf表格图片",
                        "render_table 失败：HTML 中未找到 table 元素，full_html_head={head}",
                        head=full_html[:500].replace("\n", "\\n"),
                    )
                    logger.warning("表格渲染失败：HTML 中未找到 table 元素")
                    return None

                content_hash = hashlib.md5(markdown_table.encode()).hexdigest()[:12]
                output_path = os.path.join(
                    self._upload_dir, f"table_{content_hash}.png"
                )

                await table_element.screenshot(path=output_path)
                file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                tlog(
                    "wecom_kf表格图片",
                    "render_table 截图成功：path={path}, size={size}, hash={h}",
                    path=output_path,
                    size=file_size,
                    h=content_hash,
                )
                logger.info(f"表格已渲染为图片: {output_path} ({file_size} bytes)")
                return output_path

            except Exception as e:
                tlog(
                    "wecom_kf表格图片",
                    "render_table 异常：{err}",
                    err=str(e),
                    level="ERROR",
                )
                logger.error(f"表格渲染失败: {e}", exc_info=True)
                return None

    async def _cleanup_browser(self):
        """清理浏览器资源（内部使用，不改变 _available 缓存）。"""
        try:
            if self._page:
                await self._page.close()
        except Exception:
            pass
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._browser = None
        self._playwright = None

    async def close(self):
        """关闭浏览器，释放资源。"""
        await self._cleanup_browser()
        logger.info("WeComKfRenderer 浏览器已关闭")
