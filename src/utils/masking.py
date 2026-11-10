"""通用脱敏工具"""

from typing import Optional


def mask_phone(phone: Optional[str]) -> Optional[str]:
    """手机号脱敏：11 位保留前 3 后 4；非 11 位（海外号码等）统一掩码前 3 + **** + 后 2；空返回 None。

    注意：不能用「非 11 位原样返回」的登录场景脱敏语义，海外号码会明文泄露。
    """
    if not phone:
        return None
    phone = str(phone).strip()
    if not phone:
        return None
    if len(phone) == 11 and phone.isdigit():
        return f"{phone[:3]}****{phone[7:]}"
    if len(phone) > 5:
        return f"{phone[:3]}****{phone[-2:]}"
    return f"{phone[:1]}****"
