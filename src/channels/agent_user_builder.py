"""
渠道用户转 agent User 对象的构造工具

为飞书/企业微信/钉钉渠道构造含手机号的 User 对象，
透传给 agent.process_message_sync 后会自动注入 `## 当前用户` 段。
"""

from typing import Any, Optional

from loguru import logger

from src.core.temp_logger import tlog
from src.db.models import UserDB
from src.models.user import User


async def build_agent_user_for_channel(
    channel_type: str,
    channel_user_id: str,
    tenant_id: str,
    adapter: Any,
    user_id: Optional[str],
) -> Optional[User]:
    """
    构造传给 agent 的 User 对象。

    优先用 DB 缓存的 phone；缺失时调渠道 adapter.get_user_info 获取并写回 DB。
    任何失败都返回 User(phone=None) 或 None，不抛异常，不阻断主流程。

    Args:
        channel_type: 渠道类型 "feishu" / "wecom" / "dingtalk"
        channel_user_id: 渠道侧用户 ID（open_id / userid / staffId）
        tenant_id: 租户 ID
        adapter: 渠道适配器，需有 async get_user_info(user_id) -> dict
        user_id: 系统用户 ID（来自 ensure_user_registered）

    Returns:
        User 对象（含 phone 或不含），或 None（user_id 为 None 或用户记录不存在）
    """
    if not user_id:
        tlog(
            "飞书手机号注入",
            "build_agent_user_for_channel 入口 user_id 为空，返回 None: "
            "channel={ct}, channel_user_id={cuid}, tenant_id={tid}",
            ct=channel_type,
            cuid=channel_user_id,
            tid=tenant_id,
        )
        return None

    # 1. 从 DB 读用户记录
    try:
        user_record = UserDB.get_by_id(user_id)
    except Exception as e:
        logger.warning(
            f"读取用户记录失败: channel={channel_type}, user_id={user_id}, err={e}"
        )
        return None

    if not user_record:
        tlog(
            "飞书手机号注入",
            "DB 无 user_record: channel={ct}, user_id={uid}",
            ct=channel_type,
            uid=user_id,
        )
        return None

    name = (
        user_record.get("nickname")
        or user_record.get("username")
        or "unknown"
    )
    phone = user_record.get("phone")
    tlog(
        "飞书手机号注入",
        "DB 读到 user_record: channel={ct}, user_id={uid}, "
        "name={name}, db_phone={phone}",
        ct=channel_type,
        uid=user_id,
        name=name,
        phone=phone or "(空)",
    )

    # 2. DB 已有 phone，直接返回（DB 即缓存，手机号变更罕见）
    if phone:
        tlog(
            "飞书手机号注入",
            "DB 命中 phone，短路返回（不再查渠道 API）: "
            "channel={ct}, user_id={uid}, phone={phone}",
            ct=channel_type,
            uid=user_id,
            phone=phone,
        )
        return _build_user(user_id, name, phone, channel_type, channel_user_id)

    # 3. DB 无 phone，调渠道 API 获取
    tlog(
        "飞书手机号注入",
        "DB 无 phone，调渠道 API 获取: channel={ct}, user_id={uid}, "
        "channel_user_id={cuid}",
        ct=channel_type,
        uid=user_id,
        cuid=channel_user_id,
    )
    mobile = await _fetch_mobile_from_channel(
        adapter, channel_user_id, channel_type, user_id
    )
    tlog(
        "飞书手机号注入",
        "_fetch_mobile_from_channel 返回: channel={ct}, user_id={uid}, "
        "mobile={mobile}",
        ct=channel_type,
        uid=user_id,
        mobile=mobile or "(空)",
    )

    # 4. 拿到 mobile，写回 DB（索引已改非唯一，不会冲突；仍兜底其他 DB 错误）
    if mobile:
        try:
            UserDB.update_info(user_id, phone=mobile)
        except Exception as e:
            logger.warning(
                f"写回手机号失败: channel={channel_type}, user_id={user_id}, err={e}"
            )
        phone = mobile

    final_phone = phone or None
    tlog(
        "飞书手机号注入",
        "build_agent_user_for_channel 返回 User: channel={ct}, user_id={uid}, "
        "final_phone={phone}",
        ct=channel_type,
        uid=user_id,
        phone=final_phone or "(空)",
    )
    return _build_user(user_id, name, final_phone, channel_type, channel_user_id)


async def _fetch_mobile_from_channel(
    adapter: Any,
    channel_user_id: str,
    channel_type: str,
    user_id: str,
) -> str:
    """从渠道 API 获取用户手机号，失败返回空字符串"""
    try:
        info = await adapter.get_user_info(channel_user_id)
    except Exception as e:
        logger.warning(
            f"获取渠道用户信息异常: channel={channel_type}, "
            f"user_id={user_id}, channel_user_id={channel_user_id}, err={e}"
        )
        tlog(
            "飞书手机号注入",
            "adapter.get_user_info 异常: channel={ct}, user_id={uid}, "
            "channel_user_id={cuid}, err={err}",
            ct=channel_type,
            uid=user_id,
            cuid=channel_user_id,
            err=str(e),
            level="ERROR",
        )
        return ""

    if not info:
        logger.warning(
            f"渠道返回空用户信息: channel={channel_type}, user_id={user_id}"
        )
        tlog(
            "飞书手机号注入",
            "adapter.get_user_info 返回空 dict: channel={ct}, user_id={uid}",
            ct=channel_type,
            uid=user_id,
        )
        return ""

    mobile = info.get("mobile", "") or ""
    tlog(
        "飞书手机号注入",
        "adapter.get_user_info 返回 info: channel={ct}, user_id={uid}, "
        "info_keys={keys}, info_mobile={mobile}, info_name={name}",
        ct=channel_type,
        uid=user_id,
        keys=list(info.keys()),
        mobile=mobile or "(空)",
        name=info.get("name", "") or "(空)",
    )
    if not mobile:
        logger.warning(
            f"渠道用户信息无 mobile（可能权限未授予或用户未绑定手机号）: "
            f"channel={channel_type}, user_id={user_id}"
        )
        tlog(
            "飞书手机号注入",
            "info.mobile 为空（疑似飞书应用未授 contact:user.phone 权限或用户未绑定手机号）: "
            "channel={ct}, user_id={uid}",
            ct=channel_type,
            uid=user_id,
            level="WARNING",
        )
    return mobile


def _build_user(
    user_id: str,
    name: str,
    phone: Optional[str],
    channel_type: str,
    channel_user_id: str,
) -> User:
    """构造 User 对象"""
    return User(
        user_id=user_id,
        name=name,
        phone=phone,
        channel_type=channel_type,
        channel_user_id=channel_user_id,
    )
