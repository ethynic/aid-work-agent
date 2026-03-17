"""浏览器自动化工具

实现基于Playwright的浏览器自动化功能，支持打开网页、点击元素、填写表单等操作
"""

import asyncio
from typing import Any, Dict, Optional, List
from pathlib import Path

from loguru import logger

from src.tools.base import BaseTool
from src.config.settings import settings


class BrowserSession:
    """浏览器会话管理类，维护浏览器实例和页面"""

    def __init__(self, headless: Optional[bool] = None):
        """初始化浏览器会话

        Args:
            headless: 是否无头模式，默认从配置读取
        """
        if headless is None:
            headless = settings.tools.browser.headless
        self.headless = headless
        self.browser = None
        self.playwright = None
        self.page = None
        self.context = None

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()

    async def start(self):
        """启动浏览器"""
        try:
            from playwright.async_api import async_playwright

            self.playwright = await async_playwright().start()

            # 启动浏览器
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=['--no-sandbox', '--disable-dev-shm-usage']
            )

            # 创建浏览器上下文
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )

            # 创建页面
            self.page = await self.context.new_page()

            logger.info("浏览器启动成功")

        except ImportError as e:
            logger.error("未安装Playwright，请运行: pip install playwright && python -m playwright install")
            raise
        except Exception as e:
            logger.error(f"浏览器启动失败: {e}")
            raise

    async def close(self):
        """关闭浏览器"""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("浏览器已关闭")

    def is_running(self) -> bool:
        """检查浏览器是否正在运行"""
        return self.browser is not None and self.page is not None


# 全局浏览器会话管理
_browser_sessions: Dict[str, BrowserSession] = {}


def get_browser_session(session_id: str, headless: Optional[bool] = None) -> BrowserSession:
    """获取或创建浏览器会话

    Args:
        session_id: 会话ID
        headless: 是否无头模式，默认从配置读取

    Returns:
        BrowserSession实例
    """
    if session_id not in _browser_sessions or not _browser_sessions[session_id].is_running():
        _browser_sessions[session_id] = BrowserSession(headless=headless)
    return _browser_sessions[session_id]


def close_browser_session(session_id: str):
    """关闭指定会话

    Args:
        session_id: 会话ID
    """
    if session_id in _browser_sessions:
        session = _browser_sessions[session_id]
        if session.is_running():
            asyncio.create_task(session.close())
        del _browser_sessions[session_id]


class BrowserOpenTool(BaseTool):
    """打开网页工具"""

    name = "browser_open"
    description = "打开指定网址的网页，等待页面加载完成"
    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要打开的网页URL，必须以http://或https://开头",
            },
            "session_id": {
                "type": "string",
                "description": "浏览器会话ID，用于管理多个会话，默认为'default'",
            },
            "headless": {
                "type": "boolean",
                "description": "是否无头模式运行，true为不显示浏览器窗口，false为显示窗口，默认false（从配置读取）",
            },
            "wait_for": {
                "type": "string",
                "enum": ["load", "domcontentloaded", "networkidle", "commit"],
                "description": "等待页面加载完成的策略，默认load",
            },
        },
        "required": ["url"],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行打开网页操作

        Args:
            url: 网页URL
            session_id: 会话ID
            headless: 是否无头模式
            wait_for: 等待策略

        Returns:
            执行结果
        """
        url = kwargs.get("url", "")
        session_id = kwargs.get("session_id", "default")
        headless = kwargs.get("headless", settings.tools.browser.headless)
        wait_for = kwargs.get("wait_for", "load")

        if not url:
            return {
                "success": False,
                "error": "URL不能为空",
            }

        # 验证URL格式
        if not url.startswith(("http://", "https://")):
            return {
                "success": False,
                "error": "URL必须以http://或https://开头",
            }

        try:
            # 获取或创建浏览器会话
            session = get_browser_session(session_id, headless)

            # 如果浏览器未启动，先启动
            if not session.is_running():
                await session.start()

            # 打开页面
            await session.page.goto(url, wait_until=wait_for, timeout=30000)

            # 获取页面基本信息
            title = await session.page.title()
            current_url = session.page.url

            logger.info(f"成功打开网页: {url}, 标题: {title}")

            return {
                "success": True,
                "message": f"成功打开网页: {title}",
                "session_id": session_id,
                "url": current_url,
                "title": title,
            }

        except Exception as e:
            logger.error(f"打开网页失败: {e}")
            return {
                "success": False,
                "error": f"打开网页失败: {str(e)}",
            }


class BrowserClickTool(BaseTool):
    """点击元素工具"""

    name = "browser_click"
    description = "点击网页中的指定元素（按钮、链接等）"
    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS选择器，如 '.submit-btn', '#submit', 'button[type=\"submit\"]'",
            },
            "session_id": {
                "type": "string",
                "description": "浏览器会话ID，默认为'default'",
            },
            "timeout": {
                "type": "integer",
                "description": "等待元素出现的超时时间（毫秒），默认10000",
            },
        },
        "required": ["selector"],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行点击操作

        Args:
            selector: CSS选择器
            session_id: 会话ID
            timeout: 超时时间

        Returns:
            执行结果
        """
        selector = kwargs.get("selector", "")
        session_id = kwargs.get("session_id", "default")
        timeout = kwargs.get("timeout", 10000)

        if not selector:
            return {
                "success": False,
                "error": "选择器不能为空",
            }

        try:
            # 获取浏览器会话
            if session_id not in _browser_sessions:
                return {
                    "success": False,
                    "error": f"会话 {session_id} 不存在，请先使用browser_open打开网页",
                }

            session = _browser_sessions[session_id]

            if not session.is_running():
                return {
                    "success": False,
                    "error": "浏览器未运行，请先使用browser_open打开网页",
                }

            # 等待元素可见并点击
            await session.page.click(selector, timeout=timeout)

            # 等待可能的页面跳转
            await session.page.wait_for_load_state("networkidle", timeout=5000)

            logger.info(f"成功点击元素: {selector}")

            # 获取点击后的页面信息
            title = await session.page.title()
            current_url = session.page.url

            return {
                "success": True,
                "message": f"成功点击元素: {selector}",
                "session_id": session_id,
                "current_url": current_url,
                "page_title": title,
            }

        except Exception as e:
            logger.error(f"点击元素失败: {e}")
            return {
                "success": False,
                "error": f"点击元素失败: {str(e)}",
            }


class BrowserFillTool(BaseTool):
    """填写表单工具"""

    name = "browser_fill"
    description = "填写网页表单中的输入框、文本域等元素"
    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS选择器，如 'input[name=\"username\"]', '#password'",
            },
            "value": {
                "type": "string",
                "description": "要填写的值",
            },
            "session_id": {
                "type": "string",
                "description": "浏览器会话ID，默认为'default'",
            },
            "timeout": {
                "type": "integer",
                "description": "等待元素出现的超时时间（毫秒），默认10000",
            },
        },
        "required": ["selector", "value"],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行填写表单操作

        Args:
            selector: CSS选择器
            value: 要填写的值
            session_id: 会话ID
            timeout: 超时时间

        Returns:
            执行结果
        """
        selector = kwargs.get("selector", "")
        value = kwargs.get("value", "")
        session_id = kwargs.get("session_id", "default")
        timeout = kwargs.get("timeout", 10000)

        if not selector or value is None:
            return {
                "success": False,
                "error": "选择器和值不能为空",
            }

        try:
            # 获取浏览器会话
            if session_id not in _browser_sessions:
                return {
                    "success": False,
                    "error": f"会话 {session_id} 不存在，请先使用browser_open打开网页",
                }

            session = _browser_sessions[session_id]

            if not session.is_running():
                return {
                    "success": False,
                    "error": "浏览器未运行，请先使用browser_open打开网页",
                }

            # 等待元素可见并填写
            await session.page.fill(selector, str(value), timeout=timeout)

            logger.info(f"成功填写表单: {selector} = {value}")

            return {
                "success": True,
                "message": f"成功填写表单字段: {selector}",
                "session_id": session_id,
                "selector": selector,
            }

        except Exception as e:
            logger.error(f"填写表单失败: {e}")
            return {
                "success": False,
                "error": f"填写表单失败: {str(e)}",
            }


class BrowserGetContentTool(BaseTool):
    """获取页面内容工具"""

    name = "browser_get_content"
    description = "获取网页的文本内容、HTML结构或特定元素的内容"
    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS选择器，如果为空则获取整个页面的内容",
            },
            "format": {
                "type": "string",
                "enum": ["text", "html", "markdown"],
                "description": "返回格式，text为纯文本，html为HTML源码，markdown为Markdown格式，默认text",
            },
            "session_id": {
                "type": "string",
                "description": "浏览器会话ID，默认为'default'",
            },
        },
        "required": [],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行获取内容操作

        Args:
            selector: CSS选择器（可选）
            format: 返回格式
            session_id: 会话ID

        Returns:
            执行结果
        """
        selector = kwargs.get("selector", "")
        format_type = kwargs.get("format", "text")
        session_id = kwargs.get("session_id", "default")

        try:
            # 获取浏览器会话
            if session_id not in _browser_sessions:
                return {
                    "success": False,
                    "error": f"会话 {session_id} 不存在，请先使用browser_open打开网页",
                }

            session = _browser_sessions[session_id]

            if not session.is_running():
                return {
                    "success": False,
                    "error": "浏览器未运行，请先使用browser_open打开网页",
                }

            # 获取内容
            if selector:
                # 获取特定元素的内容
                if format_type == "text":
                    content = await session.page.inner_text(selector)
                elif format_type == "html":
                    content = await session.page.inner_html(selector)
                else:  # markdown
                    # 简单的HTML转Markdown
                    html = await session.page.inner_html(selector)
                    content = self._html_to_markdown(html)
            else:
                # 获取整个页面的内容
                if format_type == "text":
                    content = await session.page.inner_text("body")
                elif format_type == "html":
                    content = await session.page.content()
                else:  # markdown
                    html = await session.page.content()
                    content = self._html_to_markdown(html)

            # 获取页面标题和URL
            title = await session.page.title()
            url = session.page.url

            logger.info(f"成功获取页面内容，格式: {format_type}")

            return {
                "success": True,
                "message": "成功获取页面内容",
                "session_id": session_id,
                "url": url,
                "title": title,
                "format": format_type,
                "content": content[:10000] if len(content) > 10000 else content,  # 限制内容长度
                "content_length": len(content),
                "truncated": len(content) > 10000,
            }

        except Exception as e:
            logger.error(f"获取内容失败: {e}")
            return {
                "success": False,
                "error": f"获取内容失败: {str(e)}",
            }

    def _html_to_markdown(self, html: str) -> str:
        """简单的HTML转Markdown

        Args:
            html: HTML内容

        Returns:
            Markdown内容
        """
        try:
            import re

            # 移除script和style标签
            html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)

            # 标题转换
            html = re.sub(r'<h1[^>]*>(.*?)</h1>', r'\n# \1\n', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<h3[^>]*>(.*?)</h3>', r'\n### \1\n', html, flags=re.DOTALL | re.IGNORECASE)

            # 链接转换
            html = re.sub(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', html, flags=re.DOTALL | re.IGNORECASE)

            # 粗体和斜体
            html = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', html, flags=re.DOTALL | re.IGNORECASE)

            # 列表
            html = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1', html, flags=re.DOTALL | re.IGNORECASE)

            # 段落
            html = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\1\n', html, flags=re.DOTALL | re.IGNORECASE)

            # 移除所有其他HTML标签
            html = re.sub(r'<[^>]+>', '', html)

            # 清理多余的换行
            html = re.sub(r'\n{3,}', '\n\n', html)

            return html.strip()

        except Exception as e:
            logger.warning(f"HTML转Markdown失败: {e}")
            return html


class BrowserNavigateTool(BaseTool):
    """页面导航工具"""

    name = "browser_navigate"
    description = "在当前页面进行导航操作：前进、后退、刷新"
    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["back", "forward", "reload"],
                "description": "导航动作：back后退，forward前进，reload刷新",
            },
            "session_id": {
                "type": "string",
                "description": "浏览器会话ID，默认为'default'",
            },
        },
        "required": ["action"],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行导航操作

        Args:
            action: 导航动作
            session_id: 会话ID

        Returns:
            执行结果
        """
        action = kwargs.get("action", "")
        session_id = kwargs.get("session_id", "default")

        if not action:
            return {
                "success": False,
                "error": "导航动作不能为空",
            }

        try:
            # 获取浏览器会话
            if session_id not in _browser_sessions:
                return {
                    "success": False,
                    "error": f"会话 {session_id} 不存在，请先使用browser_open打开网页",
                }

            session = _browser_sessions[session_id]

            if not session.is_running():
                return {
                    "success": False,
                    "error": "浏览器未运行，请先使用browser_open打开网页",
                }

            # 执行导航动作
            if action == "back":
                await session.page.go_back()
                action_name = "后退"
            elif action == "forward":
                await session.page.go_forward()
                action_name = "前进"
            elif action == "reload":
                await session.page.reload(wait_until="networkidle")
                action_name = "刷新"
            else:
                return {
                    "success": False,
                    "error": f"不支持的导航动作: {action}",
                }

            # 获取页面信息
            title = await session.page.title()
            url = session.page.url

            logger.info(f"成功执行导航操作: {action_name}")

            return {
                "success": True,
                "message": f"成功{action_name}页面",
                "session_id": session_id,
                "current_url": url,
                "page_title": title,
            }

        except Exception as e:
            logger.error(f"导航操作失败: {e}")
            return {
                "success": False,
                "error": f"导航操作失败: {str(e)}",
            }


class BrowserCloseTool(BaseTool):
    """关闭浏览器工具"""

    name = "browser_close"
    description = "关闭浏览器或特定会话"
    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "要关闭的会话ID，如果为空则关闭所有会话",
            },
        },
        "required": [],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行关闭操作

        Args:
            session_id: 会话ID

        Returns:
            执行结果
        """
        session_id = kwargs.get("session_id", "")

        try:
            if session_id:
                # 关闭指定会话
                close_browser_session(session_id)
                logger.info(f"已关闭浏览器会话: {session_id}")
                return {
                    "success": True,
                    "message": f"已关闭浏览器会话: {session_id}",
                    "session_id": session_id,
                }
            else:
                # 关闭所有会话
                closed_count = 0
                for sid in list(_browser_sessions.keys()):
                    close_browser_session(sid)
                    closed_count += 1
                logger.info(f"已关闭所有浏览器会话，共 {closed_count} 个")
                return {
                    "success": True,
                    "message": f"已关闭所有浏览器会话，共 {closed_count} 个",
                    "closed_count": closed_count,
                }

        except Exception as e:
            logger.error(f"关闭浏览器失败: {e}")
            return {
                "success": False,
                "error": f"关闭浏览器失败: {str(e)}",
            }


class BrowserScreenshotTool(BaseTool):
    """网页截图工具"""

    name = "browser_screenshot"
    description = "对当前网页进行截图并保存"
    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "截图保存路径，如 './screenshot.png'，默认为 './screenshot.png'",
            },
            "session_id": {
                "type": "string",
                "description": "浏览器会话ID，默认为'default'",
            },
            "full_page": {
                "type": "boolean",
                "description": "是否截取整个页面，默认false只截取当前可视区域",
            },
        },
        "required": [],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行截图操作

        Args:
            path: 保存路径
            session_id: 会话ID
            full_page: 是否截取整个页面

        Returns:
            执行结果
        """
        path = kwargs.get("path", "./screenshot.png")
        session_id = kwargs.get("session_id", "default")
        full_page = kwargs.get("full_page", False)

        try:
            # 获取浏览器会话
            if session_id not in _browser_sessions:
                return {
                    "success": False,
                    "error": f"会话 {session_id} 不存在，请先使用browser_open打开网页",
                }

            session = _browser_sessions[session_id]

            if not session.is_running():
                return {
                    "success": False,
                    "error": "浏览器未运行，请先使用browser_open打开网页",
                }

            # 截图
            await session.page.screenshot(
                path=path,
                full_page=full_page
            )

            logger.info(f"成功保存截图: {path}")

            return {
                "success": True,
                "message": f"成功保存截图到: {path}",
                "session_id": session_id,
                "screenshot_path": path,
            }

        except Exception as e:
            logger.error(f"截图失败: {e}")
            return {
                "success": False,
                "error": f"截图失败: {str(e)}",
            }


def create_browser_tools() -> List[BaseTool]:
    """
    创建浏览器工具列表

    Returns:
        浏览器工具列表
    """
    return [
        BrowserOpenTool(),
        BrowserClickTool(),
        BrowserFillTool(),
        BrowserGetContentTool(),
        BrowserNavigateTool(),
        BrowserCloseTool(),
        BrowserScreenshotTool(),
    ]
