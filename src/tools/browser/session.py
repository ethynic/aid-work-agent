"""浏览器会话生命周期管理。

每次 ``browser_automation`` 调用拥有独立会话。启动采用事务语义，关闭采用
幂等、并发合并和逐层 best-effort 回收；任何路径都不按进程名清理浏览器。
"""

import asyncio
from typing import Any, Dict, Optional

from loguru import logger

from src.config.settings import settings
from src.tools.browser.process_guard import BrowserProcessGuard


class BrowserSession:
    """维护单个 browser run 的 Playwright 资源。"""

    def __init__(self, headless: Optional[bool] = None):
        self.headless = settings.tools.browser.headless if headless is None else headless
        self.browser = None
        self.playwright = None
        self.page = None
        self.context = None
        self.process_guard = BrowserProcessGuard()
        self._start_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._close_task: Optional[asyncio.Task] = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close(reason="context_exit")

    async def start(self, storage_state: Optional[Dict[str, Any]] = None) -> None:
        """事务化启动；任一半失败都会逆序清理已创建资源。

        可选 ``storage_state``：Playwright 标准 storage_state dict（含 cookies 与
        origins/localStorage），用于跨 run 注入登录态（B0.5）。``None`` 时不注入，
        保持旧行为。幂等：若会话已运行则直接返回（不二次注入）；调用方需在重新注入前
        显式 ``close()``。锁语义不变——storage_state 仅作为 ``new_context`` 的入参，
        不影响 ``_start_lock`` 事务化或与 ``close`` 的合并行为。
        """
        try:
            async with self._start_lock:
                if self.is_running():
                    return
                if self._close_task is not None and not self._close_task.done():
                    await asyncio.shield(self._close_task)
                self._close_task = None

                from playwright.async_api import async_playwright

                self.playwright = await async_playwright().start()
                self.process_guard.track_inline_playwright(self.playwright)
                self.browser = await self.playwright.chromium.launch(
                    headless=self.headless,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context_kwargs = {
                    "viewport": {
                        "width": settings.tools.browser.viewport_width,
                        "height": settings.tools.browser.viewport_height,
                    },
                    "user_agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    ),
                }
                if storage_state is not None:
                    # 注入登录态：cookies + localStorage 由 Playwright 写入新 context。
                    context_kwargs["storage_state"] = storage_state
                self.context = await self.browser.new_context(**context_kwargs)
                self.page = await self.context.new_page()
                self.page.set_default_timeout(settings.tools.browser.timeout)
                self.page.set_default_navigation_timeout(settings.tools.browser.timeout)
                logger.info("浏览器会话启动成功")
        except BaseException:
            # 先释放启动锁，再让 close 与并发关闭合并，避免锁重入死锁。
            await self.close(reason="start_failed")
            raise

    async def export_storage_state(self) -> Dict[str, Any]:
        """导出当前 context 的 storage_state（cookies + origins/localStorage）。

        用于登录完成（人工接管完成或显式标记）后捕获登录态，由调用方加密回写到
        ``bs_outbound_account_sessions``（B0.5）。必须在会话运行中调用；否则抛出
        ``RuntimeError``。本方法不记日志——返回值含敏感 cookie，调用方负责加密持久化
        且不得将其写入日志/审计/Agent 上下文（对齐设计 §10）。
        """
        if self.context is None:
            raise RuntimeError("browser context 未启动，无法导出 storage_state")
        return await self.context.storage_state()

    async def close(self, reason: str = "completed") -> Dict[str, Any]:
        """幂等关闭，并发调用合并到同一个清理任务。"""
        async with self._close_lock:
            if self._close_task is None:
                # close 必须排在正在进行的 start 之后，防止启动中途提前关闭后
                # start 又写入新的 page/context/browser 引用。
                async with self._start_lock:
                    if self._close_task is None:
                        self._close_task = asyncio.create_task(self._close_impl(reason))
            close_task = self._close_task
        try:
            # 调用方取消不能中断共享的底层清理任务。
            return await asyncio.shield(close_task)
        except asyncio.CancelledError:
            # 取消仍需向上传播，但资源回收屏障必须先完成。
            await close_task
            raise

    async def _close_impl(self, reason: str) -> Dict[str, Any]:
        closed: list[str] = []
        errors: list[Dict[str, str]] = []
        had_resources = any(
            resource is not None
            for resource in (self.page, self.context, self.browser, self.playwright)
        )

        for stage, attr_name, method_name in (
            ("page", "page", "close"),
            ("context", "context", "close"),
            ("browser", "browser", "close"),
            ("playwright", "playwright", "stop"),
        ):
            resource = getattr(self, attr_name)
            # 先置空，保证重入和异常后状态都不再暴露失效引用。
            setattr(self, attr_name, None)
            if resource is None:
                continue
            try:
                await getattr(resource, method_name)()
                closed.append(stage)
            except asyncio.CancelledError:
                # 共享清理任务本身不应被取消；仍记录并继续后续层。
                errors.append({"stage": stage, "error_type": "CancelledError"})
            except Exception as exc:
                errors.append({"stage": stage, "error_type": type(exc).__name__})
                logger.warning("浏览器资源关闭失败: stage={}, type={}", stage, type(exc).__name__)

        guard_report = await self.process_guard.cleanup_owned()
        report = {
            "reason": reason,
            "already_closed": not had_resources,
            "closed": closed,
            "errors": errors,
            "process_guard": guard_report,
        }
        logger.info(
            "浏览器会话关闭完成: reason={}, closed_count={}, error_count={}",
            reason,
            len(closed),
            len(errors),
        )
        return report

    def is_running(self) -> bool:
        return self.browser is not None and self.page is not None


# 当前 worker 所拥有的会话；不用于跨请求恢复。
_browser_sessions: Dict[str, BrowserSession] = {}
_closing_sessions: Dict[str, BrowserSession] = {}
_registry_lock = asyncio.Lock()


def get_browser_session(session_id: str, headless: Optional[bool] = None) -> BrowserSession:
    """获取当前 worker 会话；生产入口为每次调用生成唯一 run id。"""
    session = _browser_sessions.get(session_id)
    if session is None or (not session.is_running() and session._close_task is not None):
        session = BrowserSession(headless=headless)
        _browser_sessions[session_id] = session
    return session


async def close_browser_session(
    session_id: str, reason: str = "completed"
) -> Dict[str, Any]:
    """关闭并移除指定会话；必须由调用方 await。"""
    async with _registry_lock:
        session = _browser_sessions.pop(session_id, None)
        if session is not None:
            _closing_sessions[session_id] = session
        else:
            session = _closing_sessions.get(session_id)
    if session is None:
        return {
            "reason": reason,
            "already_closed": True,
            "closed": [],
            "errors": [],
            "process_guard": {
                "tracked_count": 0,
                "terminated_count": 0,
                "status": "ownership_unavailable",
            },
        }
    try:
        return await session.close(reason=reason)
    finally:
        async with _registry_lock:
            if _closing_sessions.get(session_id) is session:
                _closing_sessions.pop(session_id, None)


async def close_all_owned_browser_runs(reason: str = "shutdown") -> Dict[str, Any]:
    """关闭当前 worker 登记的所有 browser run。

    超时预算由应用 lifespan 控制，本函数不隐藏超时或取消。
    """
    async with _registry_lock:
        owned_by_id = dict(_closing_sessions)
        owned_by_id.update(_browser_sessions)
        owned = list(owned_by_id.items())
        _browser_sessions.clear()
        _closing_sessions.update(owned_by_id)
    if not owned:
        return {"requested": 0, "closed": 0, "reports": {}}

    try:
        results = await asyncio.gather(
            *(session.close(reason=reason) for _, session in owned),
            return_exceptions=True,
        )
    finally:
        async with _registry_lock:
            for session_id, session in owned:
                if _closing_sessions.get(session_id) is session:
                    _closing_sessions.pop(session_id, None)
    reports: Dict[str, Any] = {}
    closed = 0
    for (session_id, _), result in zip(owned, results):
        if isinstance(result, BaseException):
            reports[session_id] = {"errors": [{"error_type": type(result).__name__}]}
        else:
            reports[session_id] = result
            closed += 1
    return {"requested": len(owned), "closed": closed, "reports": reports}


def has_browser_session(session_id: str) -> bool:
    return session_id in _browser_sessions and _browser_sessions[session_id].is_running()


def get_all_session_ids() -> list[str]:
    return list(_browser_sessions.keys())
