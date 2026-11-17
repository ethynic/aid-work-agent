"""水城创业网同步调度器（复用 lyjy_employment 共用基类，仅 interval 语义）。

sccy_policy 每个 run 本就是全量 hash 比对（每日全量即对账，源行默认间隔 24h），
不启用独立 reconcile 类型；tick 到期判定/锁/并发/余额预检与 lyjy 同构。
"""

from __future__ import annotations

from src.tenant_custom.lyjy_employment.scheduler import KbSyncSchedulerBase


class SccyPolicyScheduler(KbSyncSchedulerBase):
    """水城创业网同步调度器（interval 驱动，每个 run 即全量比对）。"""

    MODULE = "sccy_policy"
    INTERVAL_DEFAULT_HOURS = 24  # 回落值与 bootstrap 默认一致（每日全量）
    ENABLE_RECONCILE = False

    def _create_service(self):
        from src.tenant_custom.sccy_policy.service import sccy_policy_service

        return sccy_policy_service


def create_scheduler():
    """optional_modules 工厂约定：无参构造调度器实例（background_runner 通用加载）。"""
    return SccyPolicyScheduler()
