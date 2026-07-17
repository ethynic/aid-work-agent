"""浏览器会话生命周期契约。"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.browser.session import (
    BrowserSession,
    _browser_sessions,
    close_all_owned_browser_runs,
    close_browser_session,
)


pytestmark = [pytest.mark.unit, pytest.mark.browser]


def _session_with_resources() -> BrowserSession:
    session = BrowserSession(headless=True)
    session.page = MagicMock(close=AsyncMock())
    session.context = MagicMock(close=AsyncMock())
    session.browser = MagicMock(close=AsyncMock())
    session.playwright = MagicMock(stop=AsyncMock())
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_stage", ["launch", "context", "page"])
async def test_start_half_failure_releases_started_resources(monkeypatch, failing_stage):
    """任一启动层失败时，已创建的下层资源都应逆序回收。"""
    playwright = MagicMock()
    playwright.stop = AsyncMock()
    browser = MagicMock()
    browser.close = AsyncMock()
    context = MagicMock()
    context.close = AsyncMock()
    context.new_page = AsyncMock()
    page = MagicMock(close=AsyncMock())

    if failing_stage == "launch":
        playwright.chromium.launch = AsyncMock(side_effect=RuntimeError("launch failed"))
    else:
        playwright.chromium.launch = AsyncMock(return_value=browser)
        if failing_stage == "context":
            browser.new_context = AsyncMock(side_effect=RuntimeError("context failed"))
        else:
            browser.new_context = AsyncMock(return_value=context)
            context.new_page = AsyncMock(side_effect=RuntimeError("page failed"))

    manager = MagicMock()
    manager.start = AsyncMock(return_value=playwright)
    fake_module = types.ModuleType("playwright.async_api")
    fake_module.async_playwright = MagicMock(return_value=manager)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    session = BrowserSession(headless=True)
    with pytest.raises(RuntimeError, match=failing_stage):
        await session.start()

    if failing_stage != "launch":
        browser.close.assert_awaited_once()
    if failing_stage == "page":
        context.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()
    page.close.assert_not_awaited()
    assert session.page is None
    assert session.context is None
    assert session.browser is None
    assert session.playwright is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_layer", ["page", "context", "browser", "playwright"])
async def test_close_continues_after_layer_error(failing_layer):
    """page/context 关闭抛错不得阻断后续层的 best-effort 回收。"""
    session = _session_with_resources()
    resources = {
        "page": session.page,
        "context": session.context,
        "browser": session.browser,
        "playwright": session.playwright,
    }
    method = "stop" if failing_layer == "playwright" else "close"
    getattr(resources[failing_layer], method).side_effect = RuntimeError(
        f"{failing_layer} close failed"
    )

    close_error = None
    try:
        await session.close()
    except Exception as exc:  # 旧实现会在首个关闭异常处中断
        close_error = exc

    assert close_error is None, f"关闭不应因 {failing_layer} 层异常中断: {close_error}"

    if failing_layer == "page":
        resources["context"].close.assert_awaited_once()
    resources["browser"].close.assert_awaited_once()
    resources["playwright"].stop.assert_awaited_once()
    assert session.page is None
    assert session.context is None
    assert session.browser is None
    assert session.playwright is None


@pytest.mark.asyncio
async def test_close_is_idempotent():
    """重复 close 不应重复关闭底层资源。"""
    session = _session_with_resources()

    resources = (session.page, session.context, session.browser, session.playwright)
    reports = [await session.close() for _ in range(10)]

    resources[0].close.assert_awaited_once()
    resources[1].close.assert_awaited_once()
    resources[2].close.assert_awaited_once()
    resources[3].stop.assert_awaited_once()
    assert reports[0] == reports[-1]


@pytest.mark.asyncio
async def test_concurrent_close_coalesces_to_one_cleanup():
    """并发 close 必须合并为一次底层回收。"""
    session = _session_with_resources()
    release = asyncio.Event()

    async def slow_page_close():
        await release.wait()

    resources = (session.page, session.context, session.browser, session.playwright)
    resources[0].close.side_effect = slow_page_close
    first = asyncio.create_task(session.close())
    second = asyncio.create_task(session.close())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)

    resources[0].close.assert_awaited_once()
    resources[1].close.assert_awaited_once()
    resources[2].close.assert_awaited_once()
    resources[3].stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_close_waits_for_cleanup_then_propagates():
    """取消关闭调用时，必须完成底层回收屏障后再传播取消。"""
    session = _session_with_resources()
    release = asyncio.Event()
    page = session.page
    context = session.context
    browser = session.browser
    playwright = session.playwright

    async def slow_page_close():
        await release.wait()

    page.close.side_effect = slow_page_close
    close_call = asyncio.create_task(session.close())
    await asyncio.sleep(0)
    close_call.cancel()
    await asyncio.sleep(0)
    assert not close_call.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await close_call
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_waits_for_inflight_start_then_releases_new_resources(monkeypatch):
    """start/close 竞态中 close 不能在资源写入前提前完成。"""
    start_entered = asyncio.Event()
    release_start = asyncio.Event()
    page = MagicMock(close=AsyncMock())
    page.set_default_timeout = MagicMock()
    page.set_default_navigation_timeout = MagicMock()
    context = MagicMock(close=AsyncMock())

    async def delayed_new_page():
        start_entered.set()
        await release_start.wait()
        return page

    context.new_page = AsyncMock(side_effect=delayed_new_page)
    browser = MagicMock(close=AsyncMock())
    browser.new_context = AsyncMock(return_value=context)
    playwright = MagicMock(stop=AsyncMock())
    playwright.chromium.launch = AsyncMock(return_value=browser)
    manager = MagicMock(start=AsyncMock(return_value=playwright))
    fake_module = types.ModuleType("playwright.async_api")
    fake_module.async_playwright = MagicMock(return_value=manager)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    session = BrowserSession(headless=True)
    start_call = asyncio.create_task(session.start())
    await start_entered.wait()
    close_call = asyncio.create_task(session.close(reason="race"))
    await asyncio.sleep(0)
    assert not close_call.done()

    release_start.set()
    await start_call
    await close_call
    page.close.assert_awaited_once()
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()
    assert not session.is_running()


@pytest.mark.asyncio
async def test_concurrent_close_browser_session_coalesces_registry_cleanup():
    """并发关闭同一 registry run 时，底层资源只关闭一次。"""
    session = _session_with_resources()
    resources = (session.page, session.context, session.browser, session.playwright)
    _browser_sessions["concurrent"] = session

    reports = await asyncio.gather(
        close_browser_session("concurrent", reason="first"),
        close_browser_session("concurrent", reason="second"),
    )

    resources[0].close.assert_awaited_once()
    resources[1].close.assert_awaited_once()
    resources[2].close.assert_awaited_once()
    resources[3].stop.assert_awaited_once()
    assert reports[0] == reports[1]
    assert all(not report["already_closed"] for report in reports)
    assert "concurrent" not in _browser_sessions


@pytest.mark.asyncio
async def test_shutdown_closes_all_registered_runs():
    """shutdown 入口使用的批量函数必须等待所有已登记 run。"""
    first = _session_with_resources()
    second = _session_with_resources()
    _browser_sessions.update({"first": first, "second": second})

    report = await close_all_owned_browser_runs(reason="shutdown")

    assert report["requested"] == 2
    assert report["closed"] == 2
    assert not _browser_sessions
    assert first.playwright is None
    assert second.playwright is None


def test_main_lifespan_registers_15_second_browser_shutdown_budget():
    """shutdown 必须在 finally 内回收，且浏览器关闭异常不能跳过后续清理。"""
    import ast
    from pathlib import Path

    main_source = (Path(__file__).resolve().parents[4] / "src" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "await _close_browser_runs_on_shutdown()" in main_source
    assert 'close_all_owned_browser_runs(reason="shutdown")' in main_source
    assert "asyncio.wait_for(asyncio.shield(close_task), timeout=15.0)" in main_source

    module = ast.parse(main_source)
    lifespan = next(
        node
        for node in module.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == "lifespan"
    )
    shutdown_try = next(
        node
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Try)
        and any(isinstance(child, (ast.Yield, ast.YieldFrom)) for child in ast.walk(node))
    )
    assert shutdown_try.finalbody
    final_source = "\n".join(
        ast.get_source_segment(main_source, statement) or ""
        for statement in shutdown_try.finalbody
    )
    assert "Application shutting down" in final_source
    assert "await _close_browser_runs_on_shutdown()" in final_source
    assert final_source.index("await _close_browser_runs_on_shutdown()") < final_source.index(
        "close_postgres_pool()"
    )
