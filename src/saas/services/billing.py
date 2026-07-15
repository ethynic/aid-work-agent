"""
计费服务

订阅生命周期管理、配额检查、到期处理。
"""

from datetime import datetime
from typing import Optional

from loguru import logger

from src.saas.db.subscription_db import SubscriptionDB


def check_expired_subscriptions() -> int:
    """
    批量检查并标记过期订阅

    可由定时任务调用。

    Returns:
        标记为过期的订阅数量
    """
    from src.db.database import get_db_connection

    count = 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            SELECT subscription_id FROM subscriptions
            WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at < %s
        """, (now,))
        expired = cursor.fetchall()

        for row in expired:
            SubscriptionDB.update_status(row["subscription_id"], "expired")
            count += 1
            logger.info(f"Subscription expired: {row['subscription_id']}")

    if count > 0:
        logger.info(f"Marked {count} subscriptions as expired")

    return count


def get_subscription_usage(subscription_id: str) -> Optional[dict]:
    """获取订阅用量详情"""
    sub = SubscriptionDB.get_by_id(subscription_id)
    if not sub:
        return None

    token_quota = sub["token_quota"]
    tokens_used = sub["tokens_used"]

    if token_quota == -1:
        tokens_remaining = -1
        usage_percent = 0.0
    else:
        tokens_remaining = max(0, token_quota - tokens_used)
        usage_percent = round(tokens_used / token_quota * 100, 2) if token_quota > 0 else 0

    return {
        "subscription_id": sub["subscription_id"],
        "token_quota": token_quota,
        "tokens_used": tokens_used,
        "tokens_remaining": tokens_remaining,
        "usage_percent": usage_percent,
        "status": sub["status"],
    }
