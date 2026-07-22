"""浏览器工具单元测试隔离 fixture（Phase 3R）。

消除跨事件循环残留：human_control 的模块级 owner runtime 注册表、completion
task 和运行时锁，以及 view_hub 单例的订阅状态，在每例后清理。pytest-asyncio
function-scoped loop 下，模块级 asyncio.Lock 会绑定首个循环导致后续用例跨循环
失败；本 fixture 配合 human_control 的惰性锁与 _reset_runtime_state 修复。
"""

from __future__ import annotations

import pytest

from src.tools.browser import human_control
from src.tools.browser import view_hub


@pytest.fixture(autouse=True)
async def _reset_browser_runtime_state():
    """每例前确保干净状态，每例后清理全局注册表与后台 task。"""
    # 每例开始前重置（防止上一例异常残留污染本例）
    human_control._reset_runtime_state()
    yield
    # 每例后停止本循环的 completion task 并清空注册表
    try:
        await human_control.stop_all_completion_monitors()
    except Exception:
        pass
    human_control._reset_runtime_state()
    # 清理 view_hub 单例的帧与订阅，避免跨例残留
    hub = view_hub.browser_view_hub
    try:
        for key in list(hub._frames.keys()):
            await hub.clear(key[0], key[1])
    except Exception:
        pass
