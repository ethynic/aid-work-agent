"""
IM 用户自动注册服务

当 IM 渠道用户首次发送消息时，自动创建 users 记录并设置 tenant_id。
"""

from typing import Optional

from loguru import logger

from src.db.database import get_db_connection
from src.db.models import UserDB


async def ensure_user_registered(
    channel_type: str,
    channel_user_id: str,
    tenant_id: Optional[str] = None,
) -> Optional[str]:
    """
    确保 IM 用户已注册。

    如果用户不存在，自动创建 users 记录。
    如果提供了 tenant_id，则设置用户的 tenant_id。

    Args:
        channel_type: 渠道类型（wecom/dingtalk/feishu）
        channel_user_id: 渠道用户 ID
        tenant_id: 租户 ID（有值时注册为企业用户）

    Returns:
        user_id 或 None
    """
    # 1. 尝试通过渠道用户 ID 查找已有用户
    existing_user_id = _find_user_by_channel_id(channel_type, channel_user_id)
    if existing_user_id:
        # 如果有 tenant_id 但用户没有，更新
        if tenant_id:
            user = UserDB.get_by_id(existing_user_id)
            if user and not user.get("tenant_id"):
                UserDB.update(existing_user_id, tenant_id=tenant_id)
                logger.info(f"Updated user {existing_user_id} tenant_id to {tenant_id}")
        return existing_user_id

    # 2. 用户不存在，创建用户（设置 tenant_id）
    username = f"{channel_type}用户{channel_user_id[-4:]}"
    user = UserDB.create(
        username=username,
        tenant_id=tenant_id,
    )
    if not user:
        logger.error(f"Failed to auto-create user for {channel_type}:{channel_user_id}")
        return None

    user_id = user["user_id"]

    # 3. 记录渠道关联信息
    _save_channel_user_mapping(channel_type, channel_user_id, user_id)

    if tenant_id:
        logger.info(
            f"IM user auto-registered: {channel_type}:{channel_user_id} "
            f"→ user={user_id}, tenant={tenant_id}"
        )
    else:
        logger.info(
            f"IM user auto-registered: {channel_type}:{channel_user_id} "
            f"→ user={user_id}"
        )

    return user_id


def _find_user_by_channel_id(channel_type: str, channel_user_id: str) -> Optional[str]:
    """通过渠道用户 ID 查找系统用户"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 查找是否已有此渠道用户的映射
        cursor.execute("""
            SELECT user_id FROM users
            WHERE username LIKE ?
            LIMIT 1
        """, (f"{channel_type}用户{channel_user_id[-4:]}",))
        row = cursor.fetchone()
        if row:
            return row["user_id"]
    return None


def _save_channel_user_mapping(channel_type: str, channel_user_id: str, user_id: str):
    """保存渠道用户映射到 users 表的元信息"""
    # 当前简单实现：更新 username 包含渠道信息
    # 未来可扩展为独立的 channel_user_mappings 表
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET username = ?
            WHERE user_id = ?
        """, (f"{channel_type}用户{channel_user_id[-4:]}", user_id))
        conn.commit()