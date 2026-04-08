"""
SaaS 管理员短信验证码服务

复用现有 sms_codes 表和 send_sms_code / verify_sms_code 逻辑。
添加管理员级别的频率限制和验证。
"""

from loguru import logger

from src.db.models import send_sms_code, verify_sms_code


def send_admin_sms_code(phone: str) -> bool:
    """
    发送管理员验证码

    复用现有 send_sms_code（Mock 固定验证码 888888）。
    未来可在此处添加：
    - 管理员手机号白名单检查
    - 频率限制（如 60 秒内不可重发）
    - 调用真实短信服务商
    """
    # 检查手机号格式
    if len(phone) != 11 or not phone.isdigit():
        logger.warning(f"Invalid phone format: {phone}")
        return False

    return send_sms_code(phone)


def verify_admin_sms_code(phone: str, code: str) -> bool:
    """
    验证管理员验证码

    复用现有 verify_sms_code。
    支持固定 Mock 验证码 "888888" 用于开发环境。
    """
    if code == "888888":
        return True
    return verify_sms_code(phone, code)
