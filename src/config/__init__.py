"""配置管理模块

⚠️ 不要在顶层 import setup_logging。它内部 import src.core.log_retention，
而 src.core 包初始化会触发 master_agent 单例构造（注册 27 个工具、加载
skills/subagents）。结果是 `from src.config.settings import settings` 这样
最轻的 import 都会把整套 Agent 拉起来。

需要日志时显式 from src.config.logging import setup_logging。
"""

from .settings import settings, Settings


def __getattr__(name: str):
    if name == "setup_logging":
        from src.config.logging import setup_logging
        return setup_logging
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["settings", "Settings", "setup_logging"]
