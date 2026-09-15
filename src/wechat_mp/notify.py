"""微信公众号队列唤醒通知（WP7，极简独立模块）。

职责：受理侧（service.import_urls / retry_article / recheck_article、callback 受理）
在事务提交成功后 best-effort 唤醒后台 worker 立即领取 queued run。

为什么独立成模块：scheduler 与受理侧都要引用同一唤醒 key，而 service 不得 import
scheduler（设计 §3 分层规则 + 避免循环依赖），故把 key 常量与 notify 放在本模块，
两侧共同依赖。

可靠性口径（计划 WP7 / 设计 §5.1）：通知尽力而为、丢失不丢任务——DB 队列是唯一
事实来源，scheduler 驱动协程每 60s 兜底扫描保证最终被领取。
"""

from __future__ import annotations

from loguru import logger

# 唤醒信号 key（rpush/lpop 跨进程队列；scheduler 侧非阻塞 lpop 清空）
WAKEUP_KEY = "wechat_mp_queue_wakeup"


def _get_redis():
    """取全局 redis 客户端（函数内懒加载，便于测试替身注入）。"""
    from src.core.redis_client import redis_client

    return redis_client


def notify_queued_work() -> bool:
    """受理成功后唤醒 worker 领取队列（best-effort，任何失败都不抛异常）。

    Redis 不可用 / rpush 失败返回 False 并记 debug：不降级内存队列（跨进程无意义，
    见 redis_client.rpush 注释），由 60s 兜底扫描接管。
    """
    try:
        redis = _get_redis()
        return bool(redis.rpush(redis.make_key(WAKEUP_KEY), "1"))
    except Exception as e:  # noqa: BLE001 唤醒失败不影响受理结果
        logger.bind(module="wechat_mp").debug(
            "wechat_mp 队列唤醒通知失败（忽略，兜底扫描将接管）: {}", e
        )
        return False
