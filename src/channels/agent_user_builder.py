"""
渠道用户转 agent User 对象的构造工具

为飞书/企业微信/钉钉渠道构造含手机号的 User 对象，
透传给 agent.process_message_sync 后会自动注入 `## 当前用户` 段。
"""

import re
from typing import Any, Dict, Optional

from loguru import logger

from src.core.temp_logger import tlog
from src.db.models import UserDB
from src.models.user import User
from src.saas.services.auto_register import CHANNEL_TYPE_NAME

# 占位符 name 模式：{渠道中文名}用户{open_id 后4位}，如 飞书用户7f5b
_PLACEHOLDER_NAME_PATTERN = re.compile(
    r"^(" + "|".join(re.escape(v) for v in CHANNEL_TYPE_NAME.values()) + r")用户.{1,}$"
)


def _normalize_phone(raw: str) -> str:
    """清洗手机号为 11 位纯数字（去掉 +86/86 前缀和非数字字符）

    项目内短信发送、手机号比对均按 11 位纯数字格式（见 sms/base.py）。
    飞书 contact API 返回的 mobile 带国家码前缀（如 +8613817140566），需清洗。
    """
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) > 11 and digits.startswith("86"):
        digits = digits[2:]
    return digits


def _is_placeholder_name(name: str, channel_type: str) -> bool:
    """判断 name 是否为 auto_register 生成的占位符（如 飞书用户7f5b）"""
    if not name or name == "unknown":
        return True
    return bool(_PLACEHOLDER_NAME_PATTERN.match(name))


async def build_agent_user_for_channel(
    channel_type: str,
    channel_user_id: str,
    tenant_id: str,
    adapter: Any,
    user_id: Optional[str],
) -> Optional[User]:
    """
    构造传给 agent 的 User 对象。

    优先用 DB 缓存的 phone 和 nickname；phone 缺失或 name 是占位符时调渠道
    adapter.get_user_info 获取并写回 DB。手机号统一清洗为 11 位纯数字。
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
        return None

    name = (
        user_record.get("nickname")
        or user_record.get("username")
        or "unknown"
    )
    phone = user_record.get("phone")
    if channel_type == "dingtalk":
        tlog(
            "钉钉用户信息",
            "DB 读到 user_record: user_id={uid}, name={name}, db_phone={phone}",
            uid=user_id,
            name=name,
            phone=phone or "(空)",
        )

    # 2. 清洗 DB phone（老数据可能带 +86 前缀）
    if phone:
        normalized_phone = _normalize_phone(phone)
        if normalized_phone != phone:
            if channel_type == "dingtalk":
                tlog(
                    "钉钉用户信息",
                    "DB phone 未清洗，写回清洗后的值: user_id={uid}, "
                    "raw={raw}, normalized={norm}",
                    uid=user_id,
                    raw=phone,
                    norm=normalized_phone,
                )
            try:
                UserDB.update_info(user_id, phone=normalized_phone)
            except Exception as e:
                logger.warning(
                    f"写回清洗后 phone 失败: user_id={user_id}, err={e}"
                )
            phone = normalized_phone

    # 3. 短路判断：DB 有 phone 且 name 非占位符，直接返回
    name_is_placeholder = _is_placeholder_name(name, channel_type)
    if phone and not name_is_placeholder:
        if channel_type == "dingtalk":
            tlog(
                "钉钉用户信息",
                "DB 命中 phone 且 name 非占位符，短路返回: "
                "user_id={uid}, name={name}, phone={phone}",
                uid=user_id,
                name=name,
                phone=phone,
            )
        return _build_user(user_id, name, phone, channel_type, channel_user_id)

    # 4. 调渠道 API 获取（phone 缺失或 name 是占位符）
    if channel_type == "dingtalk":
        tlog(
            "钉钉用户信息",
            "调渠道 API 获取: user_id={uid}, db_phone={phone}, "
            "name_is_placeholder={is_ph}, name={name}",
            uid=user_id,
            phone=phone or "(空)",
            is_ph=name_is_placeholder,
            name=name,
        )
    info = await _fetch_user_info_from_channel(
        adapter, channel_user_id, channel_type, user_id
    )

    # 5. 拿到 info，清洗 mobile + 写回 phone + nickname
    if info:
        mobile_raw = info.get("mobile", "") or ""
        mobile = _normalize_phone(mobile_raw)
        real_name = info.get("name", "") or ""
        if channel_type == "dingtalk":
            tlog(
                "钉钉用户信息",
                "info 解析: user_id={uid}, info_name={name}, "
                "mobile_raw={mraw}, mobile_normalized={mnorm}",
                uid=user_id,
                name=real_name or "(空)",
                mraw=mobile_raw or "(空)",
                mnorm=mobile or "(空)",
            )

        update_fields: Dict[str, Any] = {}
        if mobile:
            update_fields["phone"] = mobile
            phone = mobile
        if real_name and real_name != name:
            update_fields["nickname"] = real_name
            name = real_name

        if update_fields:
            try:
                UserDB.update_info(user_id, **update_fields)
                if channel_type == "dingtalk":
                    tlog(
                        "钉钉用户信息",
                        "写回 DB: user_id={uid}, fields={fields}, "
                        "final_name={name}, final_phone={phone}",
                        uid=user_id,
                        fields=list(update_fields.keys()),
                        name=name,
                        phone=phone or "(空)",
                    )
            except Exception as e:
                logger.warning(
                    f"写回用户信息失败: channel={channel_type}, "
                    f"user_id={user_id}, err={e}"
                )

    if channel_type == "dingtalk":
        tlog(
            "钉钉用户信息",
            "build_agent_user_for_channel 返回 User: user_id={uid}, "
            "final_name={name}, final_phone={phone}",
            uid=user_id,
            name=name,
            phone=phone or "(空)",
        )
    return _build_user(user_id, name, phone or None, channel_type, channel_user_id)


async def _fetch_user_info_from_channel(
    adapter: Any,
    channel_user_id: str,
    channel_type: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """从渠道 API 获取用户信息，失败返回 None"""
    try:
        info = await adapter.get_user_info(channel_user_id)
    except Exception as e:
        logger.warning(
            f"获取渠道用户信息异常: channel={channel_type}, "
            f"user_id={user_id}, channel_user_id={channel_user_id}, err={e}"
        )
        return None

    if not info:
        logger.warning(
            f"渠道返回空用户信息: channel={channel_type}, user_id={user_id}"
        )
        return None

    return info


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
