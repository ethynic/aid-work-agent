"""浏览器会话管理

管理 BrowserSession 实例的生命周期，同时保存浏览器任务的上下文信息，
支持多轮交互（如登录验证码流程）。
"""

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from src.config.settings import settings


class BrowserSession:
    """浏览器会话管理类，维护浏览器实例和页面"""

    def __init__(self, headless: Optional[bool] = None):
        if headless is None:
            headless = settings.tools.browser.headless
        self.headless = headless
        self.browser = None
        self.playwright = None
        self.page = None
        self.context = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        """启动浏览器"""
        try:
            from playwright.async_api import async_playwright

            self.playwright = await async_playwright().start()

            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=['--no-sandbox', '--disable-dev-shm-usage']
            )

            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )

            self.page = await self.context.new_page()

            logger.info("浏览器启动成功")

        except ImportError:
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
        return self.browser is not None and self.page is not None


class BrowserTaskContext:
    """浏览器任务上下文，用于在多轮交互之间保存状态"""

    def __init__(self):
        self.task: str = ""
        self.steps: List[Dict[str, Any]] = []
        self.ask_user_question: str = ""
        self.collected_content: List[str] = []
        self.last_url: Optional[str] = None
        self.same_url_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "steps": self.steps,
            "ask_user_question": self.ask_user_question,
            "collected_content": self.collected_content,
            "last_url": self.last_url,
            "same_url_count": self.same_url_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrowserTaskContext":
        ctx = cls()
        ctx.task = data.get("task", "")
        ctx.steps = data.get("steps", [])
        ctx.ask_user_question = data.get("ask_user_question", "")
        ctx.collected_content = data.get("collected_content", [])
        ctx.last_url = data.get("last_url")
        ctx.same_url_count = data.get("same_url_count", 0)
        return ctx


# 全局浏览器会话管理
_browser_sessions: Dict[str, BrowserSession] = {}
_browser_task_contexts: Dict[str, BrowserTaskContext] = {}


def get_browser_session(session_id: str, headless: Optional[bool] = None) -> BrowserSession:
    """获取或创建浏览器会话"""
    if session_id not in _browser_sessions or not _browser_sessions[session_id].is_running():
        _browser_sessions[session_id] = BrowserSession(headless=headless)
    return _browser_sessions[session_id]


def close_browser_session(session_id: str):
    """关闭指定会话"""
    if session_id in _browser_sessions:
        session = _browser_sessions[session_id]
        if session.is_running():
            asyncio.create_task(session.close())
        del _browser_sessions[session_id]
    if session_id in _browser_task_contexts:
        del _browser_task_contexts[session_id]


def has_browser_session(session_id: str) -> bool:
    """检查会话是否存在且运行中"""
    return session_id in _browser_sessions and _browser_sessions[session_id].is_running()


def get_all_session_ids() -> list:
    """获取所有活跃会话 ID"""
    return list(_browser_sessions.keys())


def save_task_context(session_id: str, context: BrowserTaskContext):
    """保存浏览器任务上下文"""
    _browser_task_contexts[session_id] = context


def get_task_context(session_id: str) -> Optional[BrowserTaskContext]:
    """获取浏览器任务上下文"""
    return _browser_task_contexts.get(session_id)


def clear_task_context(session_id: str):
    """清除浏览器任务上下文"""
    if session_id in _browser_task_contexts:
        del _browser_task_contexts[session_id]
