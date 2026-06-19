"""
钉钉签名验证模块单元测试

测试 DingTalkCrypto 的签名算法、时间戳校验。
覆盖官方文档签名算法：sign = base64(HmacSHA256(timestamp + "\\n" + appSecret, appSecret))
"""

import base64
import hashlib
import hmac
import time

import pytest

from src.channels.dingtalk.crypto import DingTalkCrypto


TEST_APP_SECRET = "test_app_secret_32_chars_long_x"


@pytest.fixture
def crypto():
    return DingTalkCrypto(TEST_APP_SECRET)


def _compute_sign(timestamp: str, app_secret: str) -> str:
    """根据钉钉算法计算签名（测试用辅助函数）"""
    string_to_sign = f"{timestamp}\n{app_secret}"
    hmac_code = hmac.new(
        app_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


class TestDingTalkCryptoSignature:
    """签名验证算法测试"""

    def test_verify_signature_valid(self, crypto):
        """正确签名应通过验证"""
        timestamp = str(int(time.time() * 1000))
        sign = _compute_sign(timestamp, TEST_APP_SECRET)
        assert crypto.verify_signature(timestamp, sign) is True

    def test_verify_signature_wrong_secret(self, crypto):
        """使用不同 secret 计算的签名应验证失败"""
        timestamp = str(int(time.time() * 1000))
        sign = _compute_sign(timestamp, "wrong_secret_xxxxxxxxxxxxxxxx")
        assert crypto.verify_signature(timestamp, sign) is False

    def test_verify_signature_tampered_sign(self, crypto):
        """篡改签名应验证失败"""
        timestamp = str(int(time.time() * 1000))
        sign = _compute_sign(timestamp, TEST_APP_SECRET)
        # 篡改签名最后一个字符
        tampered = sign[:-1] + ("A" if sign[-1] != "A" else "B")
        assert crypto.verify_signature(timestamp, tampered) is False

    def test_verify_signature_tampered_timestamp(self, crypto):
        """篡改时间戳应验证失败"""
        timestamp = str(int(time.time() * 1000))
        sign = _compute_sign(timestamp, TEST_APP_SECRET)
        # 时间戳 +1，签名不匹配
        assert crypto.verify_signature(str(int(timestamp) + 1), sign) is False

    def test_verify_signature_empty_timestamp(self, crypto):
        """空时间戳应返回 False"""
        sign = _compute_sign("123", TEST_APP_SECRET)
        assert crypto.verify_signature("", sign) is False

    def test_verify_signature_empty_sign(self, crypto):
        """空签名应返回 False"""
        assert crypto.verify_signature(str(int(time.time() * 1000)), "") is False

    def test_verify_signature_both_empty(self, crypto):
        """两者皆空应返回 False"""
        assert crypto.verify_signature("", "") is False

    def test_verify_signature_chinese_content(self, crypto):
        """包含中文的时间戳也能正确验证（理论上 timestamp 是数字，但测试鲁棒性）"""
        timestamp = "1718000000000"
        sign = _compute_sign(timestamp, TEST_APP_SECRET)
        assert crypto.verify_signature(timestamp, sign) is True

    def test_verify_signature_different_secrets_produce_different_signs(self):
        """不同 secret 产生不同签名"""
        timestamp = str(int(time.time() * 1000))
        sign1 = _compute_sign(timestamp, "secret_one_xxxxxxxxxxxxxxxxxxx")
        sign2 = _compute_sign(timestamp, "secret_two_xxxxxxxxxxxxxxxxxxx")
        assert sign1 != sign2


class TestDingTalkCryptoTimestamp:
    """时间戳校验测试"""

    def test_check_timestamp_current(self, crypto):
        """当前时间戳应通过校验"""
        timestamp = str(int(time.time() * 1000))
        assert crypto.check_timestamp(timestamp) is True

    def test_check_timestamp_within_one_hour(self, crypto):
        """1 小时内的时间戳应通过校验（默认 max_diff=3600 秒）"""
        timestamp = str(int((time.time() - 3000) * 1000))  # 3000 秒前
        assert crypto.check_timestamp(timestamp) is True

    def test_check_timestamp_exactly_at_boundary(self, crypto):
        """刚好在边界（3600 秒）的时间戳应通过校验"""
        timestamp = str(int((time.time() - 3599) * 1000))
        assert crypto.check_timestamp(timestamp) is True

    def test_check_timestamp_expired(self, crypto):
        """超过 1 小时的时间戳应校验失败"""
        timestamp = str(int((time.time() - 7200) * 1000))  # 2 小时前
        assert crypto.check_timestamp(timestamp) is False

    def test_check_timestamp_future_too_far(self, crypto):
        """未来太远的时间戳应校验失败"""
        timestamp = str(int((time.time() + 7200) * 1000))  # 未来 2 小时
        assert crypto.check_timestamp(timestamp) is False

    def test_check_timestamp_empty(self, crypto):
        """空时间戳应返回 False"""
        assert crypto.check_timestamp("") is False

    def test_check_timestamp_invalid_format(self, crypto):
        """非数字时间戳应返回 False"""
        assert crypto.check_timestamp("not_a_number") is False
        assert crypto.check_timestamp("abc.def") is False

    def test_check_timestamp_none(self, crypto):
        """None 应返回 False"""
        assert crypto.check_timestamp(None) is False  # type: ignore

    def test_check_timestamp_custom_max_diff(self, crypto):
        """自定义 max_diff 参数"""
        timestamp = str(int((time.time() - 60) * 1000))  # 60 秒前
        # max_diff=30 秒 → 失败
        assert crypto.check_timestamp(timestamp, max_diff=30) is False
        # max_diff=120 秒 → 通过
        assert crypto.check_timestamp(timestamp, max_diff=120) is True

    def test_check_timestamp_millisecond_precision(self, crypto):
        """钉钉使用毫秒时间戳，确保算法正确处理毫秒→秒转换"""
        # 当前时间戳（毫秒）
        now_ms = int(time.time() * 1000)
        # 字符串形式传入
        assert crypto.check_timestamp(str(now_ms)) is True
