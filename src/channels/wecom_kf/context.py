"""微信客服会话上下文

用于工具访问当前回调的 adapter/open_kfid/kf_config/session_id 等信息。
使用 contextvars 实现 asyncio 安全的上下文传递。
"""
from typing import Optional
import contextvars

_kf_context: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "wecom_kf_context", default=None
)


def set_kf_context(ctx: dict):
    _kf_context.set(ctx)


def get_kf_context() -> Optional[dict]:
    return _kf_context.get()


def clear_kf_context():
    _kf_context.set(None)
