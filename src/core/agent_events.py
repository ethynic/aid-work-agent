"""Agent 事件模块 — 统一的事件格式和辅助函数"""

import time
from typing import Any, Dict


def make_event(event_type: str, **kwargs) -> Dict[str, Any]:
    """创建标准 Agent 事件"""
    event = {"type": event_type, "timestamp": int(time.time() * 1000)}
    event.update(kwargs)
    return event
