"""
钉钉签名验证模块

实现钉钉 HmacSHA256 签名验证算法：
sign = base64(HmacSHA256(timestamp + "\\n" + appSecret, appSecret))

官方文档：
https://open.dingtalk.com/document/orgapp/receive-message
"""

import base64
import hashlib
import hmac
import time

from loguru import logger


class DingTalkCrypto:
    """钉钉签名验证器"""

    def __init__(self, app_secret: str):
        """
        初始化签名验证器

        Args:
            app_secret: 钉钉应用的 AppSecret
        """
        self.app_secret = app_secret

    def verify_signature(self, timestamp: str, sign: str) -> bool:
        """
        验证钉钉请求签名

        签名算法：
        1. 构造待签名字符串：timestamp + "\\n" + appSecret
        2. 使用 HmacSHA256 算法，密钥为 appSecret
        3. Base64 编码得到签名

        Args:
            timestamp: 请求头中的 timestamp（毫秒时间戳）
            sign: 请求头中的 sign（Base64 编码的签名）

        Returns:
            True 表示签名有效，False 表示签名无效
        """
        if not timestamp or not sign:
            logger.warning("[DingTalk] 签名验证失败: 缺少 timestamp 或 sign")
            return False

        try:
            # 构造待签名字符串
            string_to_sign = f"{timestamp}\n{self.app_secret}"

            # 使用 HmacSHA256 算法
            hmac_code = hmac.new(
                self.app_secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha256,
            ).digest()

            # Base64 编码
            expected_sign = base64.b64encode(hmac_code).decode("utf-8")

            # 比对签名
            is_valid = hmac.compare_digest(sign, expected_sign)

            if not is_valid:
                logger.warning(
                    f"[DingTalk] 签名验证失败: timestamp={timestamp}, "
                    f"sign={sign[:20]}..., expected={expected_sign[:20]}..."
                )

            return is_valid

        except Exception as e:
            logger.error(f"[DingTalk] 签名验证异常: {e}")
            return False

    def check_timestamp(self, timestamp: str, max_diff: int = 3600) -> bool:
        """
        检查时间戳是否在合理范围内

        钉钉要求时间戳偏差在 ±1 小时内（3600 秒）

        Args:
            timestamp: 毫秒时间戳字符串
            max_diff: 最大允许的时间差（秒），默认 3600（1 小时）

        Returns:
            True 表示时间戳有效，False 表示时间戳过期
        """
        if not timestamp:
            logger.warning("[DingTalk] 时间戳检查失败: timestamp 为空")
            return False

        try:
            # 转换为秒级时间戳（钉钉传入的是毫秒）
            ts_seconds = int(timestamp) / 1000
            current_time = time.time()
            diff = abs(current_time - ts_seconds)

            is_valid = diff <= max_diff

            if not is_valid:
                logger.warning(
                    f"[DingTalk] 时间戳过期: timestamp={timestamp}, "
                    f"diff={diff:.2f}s, max_diff={max_diff}s"
                )

            return is_valid

        except (ValueError, TypeError) as e:
            logger.error(f"[DingTalk] 时间戳解析失败: timestamp={timestamp}, error={e}")
            return False
