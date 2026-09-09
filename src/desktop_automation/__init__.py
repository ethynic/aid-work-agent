"""桌面 CLI 无人值守自动任务底座（desktop_automation）

分层（docs/design/desktop-automation/desktop-cli-automation-design.md §1）：
- 场景（微信/BOSS）经 TrustedAdapterRegistry 注册受信适配器；
- 本包管事件接纳与时间扫描骨架、occurrence/run/delivery/attempt、额度、审计/outbox；
- 本地操作通道（src/local_tools）管 invocation/permit/设备鉴权。

本包 __init__ 保持惰性：不 import 子模块、无副作用（backend_dev.md 包初始化规范）。
"""

__all__ = [
    "adapters",
    "attempts",
    "audit",
    "constants",
    "deliveries",
    "events",
    "executor",
    "init_tables",
    "occurrences",
    "outbox",
    "quota",
    "runs",
    "schedules",
    "subjects",
]
