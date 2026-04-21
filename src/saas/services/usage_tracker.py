"""
用量追踪服务

每次对话完成后，从 chat_records 累加 tokens_used 到对应 subscription。
"""

from loguru import logger

from src.db.database import get_db_connection
from src.saas.db.subscription_db import SubscriptionDB


def track_token_usage(session_id: str, token_count: int, instance_id: str = None):
    """
    追踪一次对话的 token 用量

    在 chat_stream/chat 完成后调用。

    Args:
        session_id: 会话 ID
        token_count: 本次对话消耗的 token 数
        instance_id: 智能体实例 ID（有值时才追踪到订阅）
    """
    if not instance_id or token_count <= 0:
        return

    try:
        subscription = SubscriptionDB.get_active_by_instance(instance_id)
        if subscription:
            SubscriptionDB.update_tokens_used(subscription["subscription_id"], token_count)
            logger.debug(
                f"Token usage tracked: +{token_count} to subscription "
                f"{subscription['subscription_id']} (instance={instance_id})"
            )
    except Exception as e:
        logger.error(f"Failed to track token usage: {e}")


def track_from_chat_record(record_id: str):
    """
    从 chat_record 记录追踪用量

    根据 chat_record 的 session_id 查找关联的 agent_instance，
    然后累加到对应 subscription。

    Args:
        record_id: chat_records 表的 record_id
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT cr.session_id, cr.total_token_count
            FROM chat_records cr
            WHERE cr.record_id = %s
        """, (record_id,))
        row = cursor.fetchone()
        if not row or not row["total_token_count"]:
            return

        token_count = row["total_token_count"]
        session_id = row["session_id"]

        # 尝试从 session 路径找到关联的 instance_id
        # 格式: {channel_type}_{channel_user_id} 或 web_{user_id}_{hex}
        # TODO: Phase 4 实现后，可通过 instance_manager 反查 session 关联的 instance
        logger.debug(f"Chat record {record_id}: session={session_id}, tokens={token_count}")


# TODO: 公共用户付费
# def track_public_user_usage(user_id: str, token_count: int):
#     """追踪公共用户的 token 用量（未来付费时使用）"""
#     pass
