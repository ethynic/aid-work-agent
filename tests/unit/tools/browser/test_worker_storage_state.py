"""``BrowserWorker`` 登录态注入/导出（B0.5）单元测试。

验证 worker_main 的两条新能力：
- ``_start`` 接收 ``StartCommand.storage_state`` 并透传给 ``new_context``
- ``_dispatch`` 处理 ``ExportStorageStateCommand``，返回 ``context.storage_state()``

不启动真实子进程；直接构造 ``BrowserWorker`` 并 await 其 handler / _dispatch。
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.browser.executor.models import (
    BrowserRunSpec,
    ExportStorageStateCommand,
    StartCommand,
)
from src.tools.browser.worker_main import BrowserWorker


pytestmark = [pytest.mark.unit, pytest.mark.browser]


def _run_spec(run_id: str = "br_" + "a" * 32, tenant_id: str = "t1") -> BrowserRunSpec:
    return BrowserRunSpec(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id="u1",
        session_id="s1",
        execution_target="server",
        headless=True,
        viewport_width=1280,
        viewport_height=720,
    )


def _fields(run_id: str = "br_" + "a" * 32, seq: int = 1):
    return {
        "run_id": run_id,
        "seq": seq,
        # command_id 最小 8 字符；用 12 位 hex 保证唯一且够长。
        "command_id": f"bc_{seq:012d}",
        "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=30),
    }


def _stub_playwright(monkeypatch, *, context=None):
    """stub ``playwright.async_api.async_playwright``，返回 (manager, playwright, browser, context, page)。"""
    page = MagicMock()
    context = context or MagicMock()
    context.new_page = AsyncMock(return_value=page)
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    manager = MagicMock()
    manager.start = AsyncMock(return_value=playwright)
    fake = types.ModuleType("playwright.async_api")
    fake.async_playwright = MagicMock(return_value=manager)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake)
    return manager, playwright, browser, context, page


# ---------------------------------------------------------------------------
# _start(storage_state=...)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_start_threads_storage_state_into_new_context(monkeypatch):
    """``StartCommand.storage_state`` 必须作为 kwarg 传入 ``new_context``。"""
    _m, _pw, browser, _ctx, _page = _stub_playwright(monkeypatch)
    worker = BrowserWorker()

    state = {"cookies": [{"name": "k", "value": "v"}], "origins": []}
    cmd = StartCommand(**_fields(), run=_run_spec(), storage_state=state)
    await worker.handle(cmd)

    kwargs = browser.new_context.call_args.kwargs
    assert kwargs.get("storage_state") == state
    assert "viewport" in kwargs  # 既有参数仍在


@pytest.mark.asyncio
async def test_worker_start_without_storage_state_keeps_legacy_behavior(monkeypatch):
    """``storage_state=None``（默认）时 ``new_context`` 不收到 storage_state。"""
    _m, _pw, browser, _ctx, _page = _stub_playwright(monkeypatch)
    worker = BrowserWorker()

    cmd = StartCommand(**_fields(), run=_run_spec())  # storage_state 默认 None
    await worker.handle(cmd)

    kwargs = browser.new_context.call_args.kwargs
    assert "storage_state" not in kwargs


# ---------------------------------------------------------------------------
# ExportStorageStateCommand
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_export_storage_state_returns_context_state(monkeypatch):
    """``ExportStorageStateCommand`` 在 run 启动后返回 ``context.storage_state()`` 的 dict。"""
    captured = {"cookies": [{"name": "z", "value": "secret"}], "origins": []}
    context = MagicMock()
    context.storage_state = AsyncMock(return_value=captured)
    _m, _pw, _browser, _ctx, _page = _stub_playwright(monkeypatch, context=context)

    worker = BrowserWorker()
    run_id = "br_" + "b" * 32
    await worker.handle(StartCommand(**_fields(run_id=run_id, seq=1), run=_run_spec(run_id=run_id)))

    export_cmd = ExportStorageStateCommand(**_fields(run_id=run_id, seq=2))
    # 截图/导出等只读旁路在 worker.handle 里对 seq 没有 last_seq 递增约束？保留对 _dispatch 的直接调用以隔离。
    result = await worker._dispatch(export_cmd)

    context.storage_state.assert_awaited_once()
    assert result["status"] == "ok"
    assert result["storage_state"] == captured


@pytest.mark.asyncio
async def test_worker_export_storage_state_before_start_returns_run_not_started(monkeypatch):
    """未启动 run 时导出返回 RUN_NOT_STARTED（不调用 Playwright）。"""
    worker = BrowserWorker()
    cmd = ExportStorageStateCommand(**_fields())
    result = await worker._dispatch(cmd)
    assert result["status"] == "error"
    assert result["error_code"] == "RUN_NOT_STARTED"


@pytest.mark.asyncio
async def test_worker_export_storage_state_handles_failure_with_whitelisted_code(monkeypatch):
    """``context.storage_state()`` 抛错时只回白名单 code，不泄露异常正文。"""
    context = MagicMock()
    context.storage_state = AsyncMock(side_effect=RuntimeError("sensitive internal context"))
    _m, _pw, _browser, _ctx, _page = _stub_playwright(monkeypatch, context=context)

    worker = BrowserWorker()
    run_id = "br_" + "c" * 32
    await worker.handle(StartCommand(**_fields(run_id=run_id, seq=1), run=_run_spec(run_id=run_id)))

    result = await worker._dispatch(
        ExportStorageStateCommand(**_fields(run_id=run_id, seq=2))
    )
    assert result["status"] == "error"
    assert result["error_code"] == "STORAGE_STATE_FAILED"
    # 异常正文绝不进入结果
    assert "sensitive internal context" not in str(result)
    assert "storage_state" not in result
