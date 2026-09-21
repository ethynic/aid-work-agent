"""hongtao_shop 队列唤醒信号（照 wechat_mp/notify.py 模式）。

受理侧（trigger_sync）建 queued run 后 best-effort rpush 唤醒调度器，降低领取
延迟；信号丢失不丢任务——DB 队列是唯一事实来源，调度器 60s 兜底扫描保证最终
被领取。任何异常吞掉返回 False（由兜底接管）。
"""

from __future__ import annotations

from loguru import logger

WAKEUP_KEY = "hongtao_shop_queue_wakeup"


def notify_queued_work() -> bool:
    """跨进程唤醒调度器（best-effort，失败由 60s 兜底接管）。"""
    try:
        from src.core.redis_client import redis_client

        redis_client.rpush(redis_client.make_key(WAKEUP_KEY), "1")
        return True
    except Exception as e:  # noqa: BLE001
        logger.bind(module="hongtao_shop").debug(
            "hongtao_shop 唤醒信号发送失败（由兜底扫描接管）: {}", type(e).__name__
        )
        return False
