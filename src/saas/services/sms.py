"""
SaaS 管理员短信验证码服务

复用现有 sms_codes 表和 send_sms_code / verify_sms_code 逻辑。
添加管理员级别的频率限制和验证。
支持真实短信通道发送验证码。
"""

import random
from datetime import datetime, timedelta
from loguru import logger

from src.config.settings import settings
from src.db.database import get_db_connection
from src.db.models import send_sms_code, verify_sms_code
from src.sms.manager import sms_manager


def _generate_code() -> str:
    """生成6位随机验证码"""
    return f"{random.randint(0, 999999):06d}"


def send_admin_sms_code(phone: str) -> bool:
    """
    发送管理员验证码

    - 演示模式：固定验证码 888888，不实际发送
    - 非演示模式：调用配置的短信通道真实发送
    """
    # 检查手机号格式
    if len(phone) != 11 or not phone.isdigit():
        logger.warning(f"Invalid phone format: {phone}")
        return False

    # 演示模式：不实际发送，验证码固定为 888888
    if settings.demo.enabled:
        logger.info(f"演示模式，手机号 {phone} 使用固定验证码 888888")
        return send_sms_code(phone)

    # 非演示模式：检查短信配置
    sender = sms_manager.get_sender()
    if sender is None or not sender.is_available():
        logger.error("短信通道未配置，无法发送验证码")
        return False

    # 生成验证码
    code = _generate_code()
    logger.info(f"发送验证码到 {phone}，验证码: {code}")

    # 保存验证码到数据库（和现有逻辑保持一致
    placeholder = "%s"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 标记旧验证码已使用
        cursor.execute(f"UPDATE sms_codes SET used = 1 WHERE phone = {placeholder}", (phone,))
        # 插入新验证码
        expires_at = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(f"""
            INSERT INTO sms_codes (phone, code, expires_at)
            VALUES ({placeholder}, {placeholder}, {placeholder})
        """, (phone, code, expires_at))
        conn.commit()

    # 调用短信通道发送
    result = sms_manager.send(phone, template_params={"code": code})

    if result is None:
        logger.error(f"发送验证码失败: 无可用通道")
        return False

    code_result = result.get("code")
    if code_result == 200:
        logger.info(f"验证码发送成功: phone={phone}, result={result}")
        return True
    else:
        logger.error(f"验证码发送失败: phone={phone}, code={code_result}, msg={result.get('msg')}")
        return False


def verify_admin_sms_code(phone: str, code: str) -> bool:
    """
    验证管理员验证码

    复用现有 verify_sms_code。
    支持固定 Mock 验证码 "888888" 用于开发环境/演示模式。
    """
    # 演示模式允许固定验证码
    if settings.demo.enabled and code == "888888":
        return True

    return verify_sms_code(phone, code)
