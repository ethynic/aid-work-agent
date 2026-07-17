"""进程守卫只接受可信所有权身份。"""

import pytest

from src.tools.browser.process_guard import BrowserProcessGuard


pytestmark = [pytest.mark.unit, pytest.mark.browser]


@pytest.mark.asyncio
async def test_inline_playwright_without_public_pid_is_conservative():
    guard = BrowserProcessGuard()

    assert guard.track_inline_playwright(object()) is False
    assert guard.register_owned_process(123, None) is False
    assert guard.register_owned_process(None, 123.0) is False

    report = await guard.cleanup_owned()
    assert report == {
        "tracked_count": 0,
        "terminated_count": 0,
        "status": "ownership_unavailable",
    }


def test_register_requires_pid_and_create_time():
    guard = BrowserProcessGuard()
    assert guard.register_owned_process(123, 456.5) is True
    assert guard.owned[0].pid == 123
    assert guard.owned[0].create_time == 456.5


def test_guard_has_no_process_name_or_force_kill_path():
    """Phase 1 守卫只接受身份登记，不得枚举名称或主动终止进程。"""
    import inspect

    source = inspect.getsource(BrowserProcessGuard)
    assert "process_iter" not in source
    assert ".terminate(" not in source
    assert ".kill(" not in source
