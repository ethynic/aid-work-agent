"""
计费服务

订阅生命周期管理、配额检查、到期处理。
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple

from loguru import logger

from src.saas.db.subscription_db import SubscriptionDB
from src.saas.models.subscription import AVAILABLE_PLANS


def check_token_quota(instance_id: str = None, subscription_id: str = None) -> Tuple[bool, str]:
    """
    检查 token 配额是否充足

    在 agent 处理消息前调用。

    Args:
        instance_id: 智能体实例 ID（优先使用）
        subscription_id: 订阅 ID

    Returns:
        (ok, reason) — ok=True 表示配额充足
    """
    if not instance_id and not subscription_id:
        # 公共用户无 instance_id，始终放行
        return True, ""

    # 获取关联订阅
    subscription = None
    if instance_id:
        subscription = SubscriptionDB.get_active_by_instance(instance_id)
    elif subscription_id:
        subscription = SubscriptionDB.get_by_id(subscription_id)

    if not subscription:
        # 无订阅信息，放行（可能是公共用户或未绑定订阅的实例）
        return True, ""

    # 检查订阅状态
    if subscription["status"] != "active":
        return False, f"订阅状态为 {subscription['status']}，请联系管理员续费"

    # 检查到期
    if subscription.get("expires_at"):
        try:
            expires = datetime.strptime(subscription["expires_at"], "%Y-%m-%d %H:%M:%S")
            if datetime.now() > expires:
                SubscriptionDB.update_status(subscription["subscription_id"], "expired")
                return False, "订阅已过期，请联系管理员续费"
        except ValueError:
            pass

    # 检查配额
    token_quota = subscription["token_quota"]
    tokens_used = subscription["tokens_used"]

    if token_quota == -1:
        # 不限量
        return True, ""

    if tokens_used >= token_quota:
        return False, f"Token 配额已用尽（{tokens_used}/{token_quota}），请联系管理员续费或购买额外配额"

    # 接近配额阈值（90%）时告警
    if tokens_used >= token_quota * 0.9:
        usage_pct = tokens_used / token_quota * 100
        logger.warning(
            f"Token quota warning: subscription {subscription['subscription_id']} "
            f"at {usage_pct:.1f}% ({tokens_used}/{token_quota})"
        )

    return True, ""


def create_subscription_for_tenant(
    tenant_id: str,
    plan_name: str = "basic",
    billing_cycle: str = "monthly",
    subagent_type: Optional[str] = None,
) -> Optional[dict]:
    """
    为租户创建订阅

    根据套餐信息自动填充 unit_price, token_quota, expires_at。
    """
    plan = AVAILABLE_PLANS.get(plan_name)
    if not plan:
        logger.error(f"Unknown plan: {plan_name}")
        return None

    # 计算到期时间
    if billing_cycle == "yearly":
        expires_at = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
        unit_price = plan.price * 12  # 年费
    else:
        expires_at = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        unit_price = plan.price

    subscription = SubscriptionDB.create(
        tenant_id=tenant_id,
        subagent_type=subagent_type,
        billing_cycle=billing_cycle,
        unit_price=unit_price,
        token_quota=plan.token_quota,
        expires_at=expires_at,
    )

    if subscription:
        logger.info(
            f"Subscription created: {subscription['subscription_id']} "
            f"for tenant {tenant_id}, plan={plan_name}, quota={plan.token_quota}"
        )

    return subscription


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
            WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at < ?
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
