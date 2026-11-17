"""就业/创业数据源同步队列唤醒信号（照 hongtao_shop/notify.py 模式，两模块共用）。

受理侧（base.trigger_sync）建 queued run 后 best-effort rpush 唤醒对应模块调度器，
降低领取延迟；信号丢失不丢任务——DB 队列是唯一事实来源，调度器 60s 兜底扫描
保证最终被领取。任何异常吞掉返回 False（由兜底接管）。
"""

from __future__ import annotations

from loguru import logger


def _wakeup_key(module: str) -> str:
    return f"{module}_queue_wakeup"


def notify_queued_work(module: str) -> bool:
    """跨进程唤醒对应模块调度器（best-effort，失败由 60s 兜底接管）。"""
    try:
        from src.core.redis_client import redis_client

        redis_client.rpush(redis_client.make_key(_wakeup_key(module)), "1")
        return True
    except Exception as e:  # noqa: BLE001
        logger.bind(module=module).debug(
            "{} 唤醒信号发送失败（由兜底扫描接管）: {}", module, type(e).__name__
        )
        return False
