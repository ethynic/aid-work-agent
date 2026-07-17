"""浏览器基础工具（旧版兼容）

BrowserSession 已迁移到 session.py。
本文件保留旧版工具类以兼容现有代码，新代码应使用 BrowserAutomationTool。
"""

import asyncio
from typing import Any, Dict, Optional, List
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.tools._helpers import sanitize_error
from src.config.settings import settings
from src.tools.browser.session import (
    BrowserSession,
    _browser_sessions,
    get_browser_session,
    close_browser_session,
    close_all_owned_browser_runs,
)


class BrowserOpenInput(BaseModel):
    """打开网页参数"""
    url: str = Field(..., description="要打开的网页URL，必须以http://或https://开头")
    session_id: Optional[str] = Field("default", description="浏览器会话ID")
    headless: Optional[bool] = Field(None, description="是否无头模式运行")
    wait_for: Optional[str] = Field("load", description="等待页面加载完成的策略，默认load")


class BrowserGetContentInput(BaseModel):
    """获取页面内容参数"""
    selector: Optional[str] = Field(None, description="CSS选择器")
    format: Optional[str] = Field("markdown", description="返回格式：text/html/markdown")
    session_id: Optional[str] = Field("default", description="浏览器会话ID")
    clean_content: Optional[bool] = Field(True, description="是否清理内容，仅在format为markdown时有效，默认true")


class BrowserNavigateInput(BaseModel):
    """页面导航参数"""
    action: str = Field(..., description="导航动作：back/forward/reload")
    session_id: Optional[str] = Field("default", description="浏览器会话ID")


class BrowserCloseInput(BaseModel):
    """关闭浏览器参数"""
    session_id: Optional[str] = Field(None, description="要关闭的会话ID")


class BrowserScreenshotInput(BaseModel):
    """网页截图参数"""
    path: Optional[str] = Field("./screenshot.png", description="截图保存路径")
    session_id: Optional[str] = Field("default", description="浏览器会话ID")
    full_page: Optional[bool] = Field(False, description="是否截取整个页面")


class BrowserOpenTool(BaseTool):
    """打开网页工具"""

    name = "browser_open"
    description = "打开指定网址的网页，等待页面加载完成"
    usage_guide = """工具使用规范（重要！）

**操作网页必须遵循以下流程：**

```
1. browser_open(url="...")        → 打开网页
2. browser_snapshot()             → 获取语义快照（必须！）
3. browser_click/fill/select(...) → 通过自然语言操作元素
4. browser_snapshot()             → 页面变化后重新获取快照
5. 重复 3-4 直到完成
```

**核心规则：**
- **browser_open 之后必须立即调用 browser_snapshot**，不要跳过这一步
- **每次页面发生变化后（点击、导航等），必须重新调用 browser_snapshot**
- browser_click、browser_fill、browser_select 通过**自然语言描述**定位元素，不需要 CSS 选择器
- browser_snapshot 返回的 JSON 中包含 `interactive_elements`（每个元素有 `ref` 和 `label`），分析这些信息来决定如何操作

**browser_click** - 点击元素
- description: 要点击元素的自然语言描述，如"登录按钮"、"报销申请"
- 示例: browser_click(description="登录按钮")

**browser_fill** - 填写表单
- field: 字段的自然语言描述，value: 要填写的值
- 示例: browser_fill(field="用户名", value="张三")

**browser_select** - 选择下拉选项
- field: 下拉框描述，option: 要选择的选项
- 示例: browser_select(field="部门", option="技术研发部")

**browser_find** - 查找元素（不执行操作，仅查找）
- description: 元素的自然语言描述
- 返回匹配的 ref 和置信度，可用于确认元素存在后再操作

**禁止事项：**
- 禁止使用 CSS 选择器（如 selector="#submit-btn"）
- 禁止在 browser_open 后直接操作元素而不获取快照
- 禁止跳过 browser_snapshot 直接猜测元素位置"""
    display_name = "打开网页"
    category = "browser"
    InputModel = BrowserOpenInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """动态显示名，展示URL（截断过长URL）"""
        base = self.display_name
        if tool_args:
            url = tool_args.get("url", "")
            if url:
                if len(url) > 30:
                    return f"{base}「{url[:30]}...」"
                return f"{base}「{url}」"
        return base

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

            logger.info("旧版浏览器打开页面成功")

            return {
                "success": True,
                "message": f"成功打开网页: {title}",
                "session_id": session_id,
                "url": current_url,
                "title": title,
            }

        except Exception as e:
            logger.error("打开网页失败: type={}", type(e).__name__)
            return {
                "success": False,
                "error": sanitize_error(e, fallback="打开网页失败"),
            }


class BrowserClickTool(BaseTool):
    """点击元素工具（CSS选择器版本 - 已废弃）

    @deprecated
    请使用 semantic 版本的 BrowserClickTool（browser_click with description parameter）
    """

    name = "browser_click"
    description = """点击网页中的指定元素（CSS选择器版本 - 已废弃）

    @deprecated
    请使用语义版本：browser_click(description="按钮名称")

    此版本使用 CSS 选择器，不推荐使用，因为：
    1. CSS 选择器不稳定，页面变化后容易失效
    2. 大模型难以准确生成正确的 CSS 选择器
    3. 无法处理动态生成的类名或 ID
    """
    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS选择器，如 '.submit-btn', '#submit', 'button[type=\"submit\"]'（已废弃）",
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
        """执行点击操作（已废弃）

        Args:
            selector: CSS选择器
            session_id: 会话ID
            timeout: 超时时间

        Returns:
            执行结果
        """
        logger.warning(
            "使用了已废弃的 CSS 选择器版本 browser_click。"
            "请使用语义版本：browser_click(description=\"按钮名称\")"
        )

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
                "deprecated": True,
                "deprecation_warning": "请使用语义版本：browser_click(description=\"按钮名称\")",
            }

        except Exception as e:
            logger.error("点击元素失败: type={}", type(e).__name__)
            return {
                "success": False,
                "error": sanitize_error(e, fallback="点击元素失败"),
            }


class BrowserFillTool(BaseTool):
    """填写表单工具（CSS选择器版本 - 已废弃）

    @deprecated
    请使用 semantic 版本的 BrowserFillTool（browser_fill with field parameter）
    """

    name = "browser_fill"
    description = """填写网页表单中的输入框、文本域等元素（CSS选择器版本 - 已废弃）

    @deprecated
    请使用语义版本：browser_fill(field="字段名称", value="值")

    此版本使用 CSS 选择器，不推荐使用，因为：
    1. CSS 选择器不稳定，页面变化后容易失效
    2. 大模型难以准确生成正确的 CSS 选择器
    3. 无法处理动态生成的类名或 ID
    """
    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS选择器，如 'input[name=\"username\"]', '#password'（已废弃）",
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
        """执行填写表单操作（已废弃）

        Args:
            selector: CSS选择器
            value: 要填写的值
            session_id: 会话ID
            timeout: 超时时间

        Returns:
            执行结果
        """
        logger.warning(
            "使用了已废弃的 CSS 选择器版本 browser_fill。"
            "请使用语义版本：browser_fill(field=\"字段名称\", value=\"值\")"
        )

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

            logger.info("旧版浏览器填写表单成功")

            return {
                "success": True,
                "message": f"成功填写表单字段: {selector}",
                "session_id": session_id,
                "selector": selector,
                "deprecated": True,
                "deprecation_warning": "请使用语义版本：browser_fill(field=\"字段名称\", value=\"值\")",
            }

        except Exception as e:
            logger.error("填写表单失败: type={}", type(e).__name__)
            return {
                "success": False,
                "error": sanitize_error(e, fallback="填写表单失败"),
            }


class BrowserGetContentTool(BaseTool):
    """获取页面内容工具"""

    name = "browser_get_content"
    description = "获取网页的内容，支持多种格式输出（默认Markdown格式）。可获取整个页面或特定元素的内容，并返回最终URL"
    display_name = "获取网页内容"
    category = "browser"
    InputModel = BrowserGetContentInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行获取内容操作

        Args:
            selector: CSS选择器（可选）
            format: 返回格式，默认markdown
            session_id: 会话ID
            clean_content: 是否清理内容（仅markdown格式）

        Returns:
            执行结果，包含最终URL和内容
        """
        selector = kwargs.get("selector", "")
        format_type = kwargs.get("format", "markdown")  # 默认改为markdown
        session_id = kwargs.get("session_id", "default")
        clean_content = kwargs.get("clean_content", True)

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
                    html = await session.page.inner_html(selector)
                    content = self._html_to_markdown(html)
            else:
                # 获取整个页面的内容
                if format_type == "text":
                    content = await session.page.inner_text("body")
                elif format_type == "html":
                    content = await session.page.content()
                else:  # markdown
                    # 如果启用清理，尝试提取主要内容
                    if clean_content:
                        html = await self._extract_main_content(session.page)
                    else:
                        html = await session.page.content()
                    content = self._html_to_markdown(html)

            # 获取页面标题和URL
            title = await session.page.title()
            url = session.page.url

            logger.info(f"成功获取页面内容，格式: {format_type}")

            # 根据格式类型调整返回字段
            result = {
                "success": True,
                "message": "成功获取页面内容",
                "session_id": session_id,
                "url": url,  # 最终URL
                "title": title,
                "format": format_type,
                "content_length": len(content),
                "truncated": len(content) > 50000,
            }
            
            # 统一使用content字段，如果是markdown也同时提供markdown字段
            if format_type == "markdown":
                result["content"] = content[:50000] if len(content) > 50000 else content
                result["markdown"] = result["content"]  # 兼容性
            else:
                result["content"] = content[:10000] if len(content) > 10000 else content
            
            return result

        except Exception as e:
            logger.error("获取内容失败: type={}", type(e).__name__)
            return {
                "success": False,
                "error": sanitize_error(e, fallback="获取网页内容失败"),
            }

    async def _extract_main_content(self, page) -> str:
        """提取页面的主要内容

        尝试识别并提取页面的正文内容，移除导航、广告等

        Args:
            page: Playwright页面对象

        Returns:
            主要内容的HTML
        """
        # 常见的内容容器选择器（按优先级）
        content_selectors = [
            'article',
            '[role="main"]',
            'main',
            '.post-content',
            '.article-content',
            '.entry-content',
            '.content',
            '#content',
            '.post',
            '.article',
        ]

        for selector in content_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    html = await element.inner_html()
                    if len(html) > 200:  # 确保内容足够长
                        logger.debug(f"使用内容选择器: {selector}")
                        return html
            except Exception:
                continue

        # 如果没有找到主要内容区域，返回整个body
        return await page.inner_html('body')

    def _html_to_markdown(self, html: str) -> str:
        """将HTML转换为Markdown格式

        优先使用markdownify库，如果不可用则使用简单的正则转换

        Args:
            html: HTML内容

        Returns:
            Markdown内容
        """
        try:
            # 尝试使用markdownify库进行专业转换
            from markdownify import markdownify as md

            # 配置转换选项
            markdown_content = md(
                html,
                heading_style="atx",  # 使用 # 风格的标题
                bullets="-",  # 使用 - 作为列表符号
                strip=['script', 'style', 'nav', 'footer', 'header', 'aside'],  # 移除这些标签
                escape_asterisks=False,
                escape_underscores=False
            )

            # 清理多余的空行
            import re
            markdown_content = re.sub(r'\n{3,}', '\n\n', markdown_content)

            # 限制内容长度
            if len(markdown_content) > 50000:
                markdown_content = markdown_content[:50000] + "\n\n... [内容已截断]"

            return markdown_content.strip()

        except ImportError:
            logger.warning("markdownify库未安装，使用简单的正则转换")
            return self._simple_html_to_markdown(html)
        except Exception as e:
            logger.warning(f"使用markdownify转换失败，回退到简单转换: {e}")
            return self._simple_html_to_markdown(html)

    def _simple_html_to_markdown(self, html: str) -> str:
        """简单的HTML转Markdown（备用方案）

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
            html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<aside[^>]*>.*?</aside>', '', html, flags=re.DOTALL | re.IGNORECASE)

            # 标题转换
            html = re.sub(r'<h1[^>]*>(.*?)</h1>', r'\n# \1\n', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<h3[^>]*>(.*?)</h3>', r'\n### \1\n', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<h4[^>]*>(.*?)</h4>', r'\n#### \1\n', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<h5[^>]*>(.*?)</h5>', r'\n##### \1\n', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<h6[^>]*>(.*?)</h6>', r'\n###### \1\n', html, flags=re.DOTALL | re.IGNORECASE)

            # 链接转换
            html = re.sub(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', html, flags=re.DOTALL | re.IGNORECASE)

            # 粗体和斜体
            html = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', html, flags=re.DOTALL | re.IGNORECASE)

            # 列表
            html = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1', html, flags=re.DOTALL | re.IGNORECASE)

            # 代码块
            html = re.sub(r'<pre[^>]*><code[^>]*>(.*?)</code></pre>', r'\n```\n\1\n```\n', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', html, flags=re.DOTALL | re.IGNORECASE)

            # 段落
            html = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\1\n', html, flags=re.DOTALL | re.IGNORECASE)

            # 换行
            html = re.sub(r'<br\s*/?>', r'\n', html, flags=re.IGNORECASE)

            # 移除所有其他HTML标签
            html = re.sub(r'<[^>]+>', '', html)

            # 清理多余的换行
            html = re.sub(r'\n{3,}', '\n\n', html)

            # 限制内容长度
            if len(html) > 50000:
                html = html[:50000] + "\n\n... [内容已截断]"

            return html.strip()

        except Exception as e:
            logger.warning(f"简单HTML转Markdown失败: {e}")
            return html[:50000] if len(html) > 50000 else html


class BrowserNavigateTool(BaseTool):
    """页面导航工具"""

    name = "browser_navigate"
    description = "在当前页面进行导航操作：前进、后退、刷新"
    display_name = "网页导航"
    category = "browser"
    InputModel = BrowserNavigateInput

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
            logger.error("导航操作失败: type={}", type(e).__name__)
            return {
                "success": False,
                "error": sanitize_error(e, fallback="导航操作失败"),
            }


class BrowserCloseTool(BaseTool):
    """关闭浏览器工具"""

    name = "browser_close"
    description = "[已弃用] 关闭旧版浏览器会话；主 Agent 不再注册此工具"
    display_name = "关闭浏览器"
    category = "browser"
    InputModel = BrowserCloseInput

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
                report = await close_browser_session(session_id, reason="legacy_close_tool")
                logger.info(f"已关闭浏览器会话: {session_id}")
                return {
                    "success": True,
                    "message": f"已关闭浏览器会话: {session_id}",
                    "session_id": session_id,
                    "close_report": report,
                }
            else:
                # 关闭所有会话
                report = await close_all_owned_browser_runs(reason="legacy_close_tool")
                closed_count = report["closed"]
                logger.info(f"已关闭所有浏览器会话，共 {closed_count} 个")
                return {
                    "success": True,
                    "message": f"已关闭所有浏览器会话，共 {closed_count} 个",
                    "closed_count": closed_count,
                    "close_report": report,
                }

        except Exception as e:
            logger.error("关闭浏览器失败: type={}", type(e).__name__)
            return {
                "success": False,
                "error": sanitize_error(e, fallback="关闭浏览器失败"),
            }




class BrowserScreenshotTool(BaseTool):
    """网页截图工具"""

    name = "browser_screenshot"
    description = "对当前网页进行截图并保存"
    display_name = "网页截图"
    category = "browser"
    InputModel = BrowserScreenshotInput

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
            logger.error("截图失败: type={}", type(e).__name__)
            return {
                "success": False,
                "error": sanitize_error(e, fallback="网页截图失败"),
            }


def create_browser_tools() -> List[BaseTool]:
    """
    创建已弃用的浏览器工具列表（仅兼容旧代码，主 Agent 不注册）

    注意：CSS 选择器版本的 browser_click 和 browser_fill 已废弃，
    请使用 semantic 版本（通过自然语言描述操作元素）。

    Returns:
        浏览器工具列表
    """
    return [
        BrowserOpenTool(),
        # 已废弃 CSS 选择器版本 - 请使用 semantic/tools_semantic.py 中的版本
        # BrowserClickTool(),  # 已废弃
        # BrowserFillTool(),  # 已废弃
        BrowserGetContentTool(),
        BrowserNavigateTool(),
        BrowserCloseTool(),
        BrowserScreenshotTool(),
    ]
