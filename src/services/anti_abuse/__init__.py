"""防滥用策略引擎（Phase 1 观察闭环）。

模块顶层零副作用（backend_dev.md 包初始化规范）：
服务实例经 get_anti_abuse_service() 惰性构造，import 本包不触发任何
DB 连接或 Agent 构建。设计见 docs/system/saas/anti-abuse-policy-design.md。
"""
from typing import Any

from .service import AntiAbuseService

_anti_abuse_service_instance: Any = None


def get_anti_abuse_service() -> "AntiAbuseService":
    """返回 AntiAbuseService 单例，第一次调用时构造"""
    global _anti_abuse_service_instance
    if _anti_abuse_service_instance is None:
        _anti_abuse_service_instance = AntiAbuseService()
    return _anti_abuse_service_instance


__all__ = ["AntiAbuseService", "get_anti_abuse_service"]
