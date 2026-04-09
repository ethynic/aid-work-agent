"""
IM 用户自动注册服务

当 IM 渠道用户首次发送消息时，自动创建 users + tenant_users 记录。
"""

from typing import Optional

from loguru import logger

from src.db.database import get_db_connection
from src.db.models import UserDB
from src.saas.db.tenant_user_db import TenantUserDB


async def ensure_user_registered(
    channel_type: str,
    channel_user_id: str,
    tenant_id: Optional[str] = None,
) -> Optional[str]:
    """
    确保 IM 用户已注册。

    如果用户不存在，自动创建 users + tenant_users 记录。

    Args:
        channel_type: 渠道类型（wecom/dingtalk/feishu）
        channel_user_id: 渠道用户 ID
        tenant_id: 租户 ID（有值时注册为企业用户）

    Returns:
        user_id 或 None
    """
    # 1. 尝试通过渠道用户 ID 查找已有用户
    # 渠道用户的 session 格式为 {channel_type}_{channel_user_id}
    # 我们需要查找 users 表中是否有对应记录

    # 先通过 channel_user_id 模式匹配查找
    existing_user_id = _find_user_by_channel_id(channel_type, channel_user_id)
    if existing_user_id:
        return existing_user_id

    # 2. 用户不存在，尝试获取手机号（如果渠道 API 支持）
    phone = None
    # TODO: 调用渠道 API 获取用户手机号
    # 需要渠道适配器和实例，Phase 5 骨架中暂不实现

    # 3. 创建用户
    username = f"{channel_type}用户{channel_user_id[-4:]}"
    user = UserDB.create(username=username)
    if not user:
        logger.error(f"Failed to auto-create user for {channel_type}:{channel_user_id}")
        return None

    user_id = user["user_id"]

    # 4. 如果是租户用户，创建 tenant_users 映射
    if tenant_id:
        mapping = TenantUserDB.create(
            tenant_id=tenant_id,
            user_id=user_id,
            role="member",
            source="im_auto",
        )
        if mapping:
            logger.info(
                f"IM user auto-registered: {channel_type}:{channel_user_id} "
                f"→ user={user_id}, tenant={tenant_id}"
            )
        else:
            logger.warning(f"Created user {user_id} but failed to create tenant mapping")

    # 5. 记录渠道关联信息
    _save_channel_user_mapping(channel_type, channel_user_id, user_id)

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
