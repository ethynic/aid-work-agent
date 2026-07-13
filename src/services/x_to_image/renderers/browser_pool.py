"""
Playwright 异步单例浏览器池。

完全复刻 src/channels/wecom_kf/renderer.py 的浏览器生命周期管理
（懒加载、asyncio.Lock 串行、is_available() 探测、close()），抽取为通用池，
支持任意宽度与 full_page 全页长截图。

关键约束：headless 硬编码为 True（内容均为本系统自生成，无反爬/登录需求），
绝不作为参数暴露给外部。

提供两种使用方式：
- ``shoot(html, width, out_dir)``：高级接口，set_content + 全页截图一步完成
- ``acquire_page(width)``：低级 async context manager，返回 Page 供调用方做
  元素级截图、query_selector 等自定义操作（在 lock 保护下）
"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from pathlib import Path

from loguru import logger


class BrowserPool:
    """
    Playwright Chromium 异步单例浏览器池。

    - 懒加载：首次使用时才启动浏览器
    - 串行：asyncio.Lock 保护页面，避免并发竞争
    - 自愈：页面断开时自动 _cleanup_browser 后重启
    - 探测：is_available() 缓存 Playwright 是否安装
    """

    def __init__(self):
        self._pw = None
        self._browser = None
        self._page = None
        self._lock = asyncio.Lock()
        self._available: Optional[bool] = None

    async def is_available(self) -> bool:
        """检查 Playwright 是否已安装（懒加载、缓存结果）。"""
        if self._available is not None:
            return self._available
        try:
            from playwright.async_api import async_playwright  # noqa: F401
            self._available = True
        except ImportError:
            logger.warning("Playwright 未安装，浏览器渲染功能不可用")
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

        self._pw = await async_playwright().start()
        # headless=True 硬编码：内容均为本系统自生成，无反爬/登录需求，绝不参数化
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
        )
        self._page = await self._browser.new_page()
        logger.info("BrowserPool 浏览器已启动")

    async def shoot(self, html: str, width: int, out_dir: Path) -> str:
        """
        把 HTML 设置进页面并做全页长截图，返回生成的 PNG 绝对路径。

        Args:
            html: 完整 HTML 字符串
            width: 视窗宽度（px）
            out_dir: 输出目录（截图文件落在此目录）

        Returns:
            生成的 PNG 文件绝对路径字符串。

        Raises:
            异常向上抛出，由 service.convert 的 try/except 统一捕获转为失败结果。
        """
        async with self._lock:
            await self._ensure_browser()
            await self._page.set_viewport_size({"width": width, "height": 800})
            await self._page.set_content(html, wait_until="networkidle", timeout=15000)
            path = out_dir / f"{uuid.uuid4().hex[:12]}.png"
            await self._page.screenshot(path=str(path), full_page=True, type="png")
            logger.info(f"BrowserPool 已生成全页截图: {path}")
            return str(path)

    @asynccontextmanager
    async def acquire_page(self, width: int = 800) -> AsyncIterator[object]:
        """获取浏览器页面上下文（async context manager）。

        在 lock 保护下确保浏览器就绪并设置视窗宽度，yield 出 Page 对象供调用方
        做 set_content / query_selector / 元素级截图等自定义操作。

        适用场景：需要元素级截图（如只截 table 元素）、多步页面交互等
        ``shoot()`` 无法覆盖的场景。

        Args:
            width: 视窗宽度（px），默认 800

        Yields:
            Playwright Page 对象

        Raises:
            异常向上抛出（如 Playwright 未安装、页面操作失败）。
        """
        async with self._lock:
            await self._ensure_browser()
            await self._page.set_viewport_size({"width": width, "height": 800})
            yield self._page

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
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._page = None
        self._browser = None
        self._pw = None

    async def close(self):
        """关闭浏览器，释放资源。"""
        await self._cleanup_browser()
        logger.info("BrowserPool 浏览器已关闭")


# 模块级单例：全进程共享一个浏览器实例
browser_pool = BrowserPool()
