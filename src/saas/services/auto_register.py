"""
IM 用户自动注册服务

当 IM 渠道用户首次发送消息时，自动创建 users 记录并设置 tenant_id。
"""

from typing import Any, Dict, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.db.models import UserDB

# channel_type → 中文名称映射
CHANNEL_TYPE_NAME = {
    "wecom_kf": "企业微信客服",
    "wecom": "企业微信",
    "dingtalk": "钉钉",
    "feishu": "飞书",
}


async def ensure_user_registered(
    channel_type: str,
    channel_user_id: str,
    tenant_id: Optional[str] = None,
    user_info: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
) -> Optional[str]:
    """
    确保 IM 用户已注册。

    如果用户不存在，自动创建 users 记录。
    如果提供了 tenant_id，则设置用户的 tenant_id。
    如果提供了 user_info，则更新用户的昵称和头像。

    Args:
        channel_type: 渠道类型（wecom/dingtalk/feishu）
        channel_user_id: 渠道用户 ID
        tenant_id: 租户 ID（有值时注册为企业用户）
        user_info: 用户信息 {"name": "昵称", "avatar": "头像URL"}

    Returns:
        user_id 或 None
    """
    # 1. 尝试通过渠道用户 ID 查找已有用户
    existing_user_id = _find_user_by_channel_id(channel_type, channel_user_id, tenant_id)
    if existing_user_id:
        # 如果有 tenant_id 但用户没有，更新
        if tenant_id:
            user = UserDB.get_by_id(existing_user_id)
            if user and not user.get("tenant_id"):
                UserDB.update(existing_user_id, tenant_id=tenant_id)
                logger.info(f"Updated user {existing_user_id} tenant_id to {tenant_id}")

        # 更新昵称和头像（如果提供了 user_info）
        if user_info:
            _update_user_info_from_channel(existing_user_id, user_info)

        # 补写 source（如果当前为空且有传入）
        if source:
            user = UserDB.get_by_id(existing_user_id)
            if user and not user.get("source"):
                UserDB.update_info(existing_user_id, source=source)
                logger.info(f"补写用户 {existing_user_id} source={source}")

        return existing_user_id

    # 2. 用户不存在，创建用户（设置 tenant_id）
    username = _build_username(channel_type, channel_user_id)
    nickname = _extract_nickname(user_info)
    wx_openid = _extract_wx_openid(user_info)
    wx_unionid = _extract_wx_unionid(user_info)
    user = UserDB.create(
        username=username,
        nickname=nickname,
        wx_openid=wx_openid,
        wx_unionid=wx_unionid,
        tenant_id=tenant_id,
        source=source,
    )
    if not user:
        logger.error(f"Failed to auto-create user for {channel_type}:{channel_user_id}")
        return None

    user_id = user["user_id"]

    # 3. 记录渠道关联信息
    _save_channel_user_mapping(channel_type, channel_user_id, user_id)

    # 4. 保存头像和微信信息（user_info 有值时）
    if user_info:
        extra_updates = {}
        if user_info.get("avatar"):
            extra_updates["avatar_url"] = user_info["avatar"]
        if user_info.get("wx_unionid") and not wx_unionid:
            extra_updates["wx_unionid"] = user_info["wx_unionid"]
        if extra_updates:
            UserDB.update_info(user_id, **extra_updates)

    if tenant_id:
        logger.info(
            f"IM user auto-registered: {channel_type}:{channel_user_id} "
            f"→ user={user_id}, tenant={tenant_id}, source={source}, nickname={nickname}"
        )
    else:
        logger.info(
            f"IM user auto-registered: {channel_type}:{channel_user_id} "
            f"→ user={user_id}, source={source}, nickname={nickname}"
        )

    return user_id


def _find_user_by_channel_id(
    channel_type: str,
    channel_user_id: str,
    tenant_id: Optional[str] = None,
) -> Optional[str]:
    """通过渠道用户 ID 查找系统用户

    Args:
        channel_type: 渠道类型
        channel_user_id: 渠道用户 ID
        tenant_id: 租户 ID。SaaS 模式下必填，用于跨租户隔离；
                   为 None 时（非 SaaS 模式）不加租户过滤，保持向后兼容。
    """
    channel_name = CHANNEL_TYPE_NAME.get(channel_type, channel_type)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if tenant_id:
            cursor.execute("""
                SELECT user_id FROM users
                WHERE username LIKE %s AND tenant_id = %s
                LIMIT 1
            """, (f"{channel_name}用户{channel_user_id[-4:]}", tenant_id))
        else:
            cursor.execute("""
                SELECT user_id FROM users
                WHERE username LIKE %s
                LIMIT 1
            """, (f"{channel_name}用户{channel_user_id[-4:]}",))
        row = cursor.fetchone()
        if row:
            return row["user_id"]
    return None


def _save_channel_user_mapping(channel_type: str, channel_user_id: str, user_id: str):
    """保存渠道用户映射到 users 表的元信息"""
    channel_name = CHANNEL_TYPE_NAME.get(channel_type, channel_type)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET username = %s
            WHERE user_id = %s
        """, (f"{channel_name}用户{channel_user_id[-4:]}", user_id))
        conn.commit()


def _build_username(channel_type: str, channel_user_id: str) -> str:
    """构建用户名，使用中文渠道名称"""
    channel_name = CHANNEL_TYPE_NAME.get(channel_type, channel_type)
    return f"{channel_name}用户{channel_user_id[-4:]}"


def _extract_nickname(user_info: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """从 user_info 中提取昵称"""
    if user_info and user_info.get("name"):
        return user_info["name"]
    return None


def _extract_wx_openid(user_info: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """从 user_info 中提取微信 openid"""
    if user_info and user_info.get("wx_openid"):
        return user_info["wx_openid"]
    return None


def _extract_wx_unionid(user_info: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """从 user_info 中提取微信 unionid"""
    if user_info and user_info.get("wx_unionid"):
        return user_info["wx_unionid"]
    return None


def _update_user_info_from_channel(existing_user_id: str,
                                   user_info: Dict[str, Any]):
    """用渠道用户信息更新已有用户的昵称、头像、微信信息。

    渠道（企微/钉钉/飞书）是用户昵称与头像的权威来源，只要渠道返回非空且与
    现值不同即覆盖更新，避免首次获取的异常值（如企微 customer/batchget 偶发
    返回客服账号名，2026-08-27 小腾老师事件）被永久固化。
    wx_openid/wx_unionid 是不可变身份标识，仅在为空时补写。
    """
    user = UserDB.get_by_id(existing_user_id)
    if not user:
        return

    updates = {}
    new_name = (user_info.get("name") or "").strip()
    new_avatar = (user_info.get("avatar") or "").strip()
    if new_name and new_name != user.get("nickname"):
        updates["nickname"] = new_name
    if new_avatar and new_avatar != user.get("avatar_url"):
        updates["avatar_url"] = new_avatar
    if user_info.get("wx_openid") and not user.get("wx_openid"):
        updates["wx_openid"] = user_info["wx_openid"]
    if user_info.get("wx_unionid") and not user.get("wx_unionid"):
        updates["wx_unionid"] = user_info["wx_unionid"]

    if updates:
        UserDB.update_info(existing_user_id, **updates)
        logger.info(
            f"Updated user {existing_user_id} info from {user_info.get('name', 'unknown')}: "
            f"{list(updates.keys())}"
        )
