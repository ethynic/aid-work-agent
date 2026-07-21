"""``BrowserSession`` 登录态注入/导出（B0.5）单元测试 + 锁语义回归。

关键约束：``start`` 的 ``_start_lock`` 事务化、``close`` 的 ``_close_lock`` 合并、
``_close_task`` 屏蔽取消都不能被新加的 ``storage_state`` 参数破坏。本测试文件同时：

- 验证 storage_state 注入到 ``new_context``
- 验证 export_storage_state 来自 ``context.storage_state()``
- 复跑 close 幂等 / 并发合并 / start-half-failure 回收（与既有契约一致）
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.browser.session import BrowserSession


pytestmark = [pytest.mark.unit, pytest.mark.browser]


# ---------------------------------------------------------------------------
# Playwright stub 工厂
# ---------------------------------------------------------------------------


def _stub_playwright(monkeypatch, *, context=None, new_context_side_effect=None):
    """构造最小 playwright.async_api stub 并注入 sys.modules。

    返回 (manager, playwright, browser, context)，方便断言。
    """
    context = context or MagicMock(close=AsyncMock())
    page = MagicMock(close=AsyncMock())
    page.set_default_timeout = MagicMock()
    page.set_default_navigation_timeout = MagicMock()
    if isinstance(context, MagicMock):
        context.new_page = AsyncMock(return_value=page)

    browser = MagicMock(close=AsyncMock())
    if new_context_side_effect is not None:
        browser.new_context = AsyncMock(side_effect=new_context_side_effect)
    else:
        browser.new_context = AsyncMock(return_value=context)

    playwright = MagicMock(stop=AsyncMock())
    playwright.chromium.launch = AsyncMock(return_value=browser)

    manager = MagicMock(start=AsyncMock(return_value=playwright))
    fake_module = types.ModuleType("playwright.async_api")
    fake_module.async_playwright = MagicMock(return_value=manager)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)
    return manager, playwright, browser, context, page


# ---------------------------------------------------------------------------
# start(storage_state=...) 注入
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_passes_storage_state_to_new_context(monkeypatch):
    """``start(storage_state=dict)`` 时 ``new_context`` 必须收到 storage_state kwarg。"""
    _m, _pw, browser, _ctx, _page = _stub_playwright(monkeypatch)

    session = BrowserSession(headless=True)
    state = {"cookies": [{"name": "k", "value": "v", "domain": ".x.com"}], "origins": []}
    await session.start(storage_state=state)

    browser.new_context.assert_awaited_once()
    kwargs = browser.new_context.call_args.kwargs
    assert "storage_state" in kwargs
    assert kwargs["storage_state"] == state
    # 其他默认参数仍正确
    assert "viewport" in kwargs and "user_agent" in kwargs


@pytest.mark.asyncio
async def test_start_without_storage_state_does_not_pass_it(monkeypatch):
    """``start()`` 不传或传 ``None`` 时 ``new_context`` 不得收到 storage_state（旧行为）。"""
    _m, _pw, browser, _ctx, _page = _stub_playwright(monkeypatch)

    session = BrowserSession(headless=True)
    # 1) 默认无参
    await session.start()
    kwargs1 = browser.new_context.call_args.kwargs
    assert "storage_state" not in kwargs1

    # 关闭后再试显式 None
    await session.close()
    session2 = BrowserSession(headless=True)
    await session2.start(storage_state=None)
    kwargs2 = browser.new_context.call_args.kwargs
    assert "storage_state" not in kwargs2


@pytest.mark.asyncio
async def test_start_failure_with_storage_state_still_rolls_back(monkeypatch):
    """注入 storage_state 时 start 半失败仍走事务化 close 逆序回收（锁语义不变）。"""
    _m, _pw, browser, _ctx, _page = _stub_playwright(
        monkeypatch, new_context_side_effect=RuntimeError("context failed")
    )

    session = BrowserSession(headless=True)
    with pytest.raises(RuntimeError, match="context failed"):
        await session.start(storage_state={"cookies": [], "origins": []})

    # launch 成功 → close 应被调用回收
    browser.close.assert_awaited_once()
    assert session.browser is None
    assert session.context is None


# ---------------------------------------------------------------------------
# export_storage_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_storage_state_returns_context_state(monkeypatch):
    """``export_storage_state`` 返回 ``context.storage_state()`` 的结果。"""
    captured = {"cookies": [{"name": "z", "value": "secret"}], "origins": []}
    _m, _pw, _browser, context, _page = _stub_playwright(monkeypatch)
    context.storage_state = AsyncMock(return_value=captured)

    session = BrowserSession(headless=True)
    await session.start(storage_state=captured)

    result = await session.export_storage_state()
    context.storage_state.assert_awaited_once()
    assert result is captured


@pytest.mark.asyncio
async def test_export_storage_state_raises_when_context_not_started():
    """context 未启动时 export 抛 RuntimeError（不调用 Playwright）。"""
    session = BrowserSession(headless=True)
    with pytest.raises(RuntimeError, match="未启动"):
        await session.export_storage_state()


# ---------------------------------------------------------------------------
# 锁语义回归（不被 storage_state 破坏）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_remains_idempotent_after_start_with_state(monkeypatch):
    """``start(storage_state=...)`` 后 ``close`` 多次调用仍只关闭一次（幂等）。"""
    _m, _pw, browser, context, page = _stub_playwright(monkeypatch)
    _playwright_obj = _m.start.return_value

    session = BrowserSession(headless=True)
    await session.start(storage_state={"cookies": [], "origins": []})

    reports = [await session.close() for _ in range(5)]
    page.close.assert_awaited_once()
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()
    _playwright_obj.stop.assert_awaited_once()
    assert reports[0] == reports[-1]


@pytest.mark.asyncio
async def test_concurrent_close_remains_coalesced_after_start_with_state(monkeypatch):
    """并发 ``close`` 必须合并为一次底层回收（锁语义不被新参数破坏）。"""
    _m, _pw, browser, context, page = _stub_playwright(monkeypatch)
    _playwright_obj = _m.start.return_value

    session = BrowserSession(headless=True)
    await session.start(storage_state={"cookies": [], "origins": []})

    release = asyncio.Event()

    async def slow_page_close():
        await release.wait()

    page.close.side_effect = slow_page_close
    first = asyncio.create_task(session.close())
    second = asyncio.create_task(session.close())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)

    page.close.assert_awaited_once()
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()
    _playwright_obj.stop.assert_awaited_once()
