"""阿里云短信发送器单元测试（全 mock，无外部调用）"""

from unittest.mock import patch

import pytest

from src.sms.aliyun import AliyunSmsSender

pytestmark = pytest.mark.unit


ALIYUN_ENV = dict(
    aliyun_access_key_id="test_ak",
    aliyun_access_key_secret="test_sk",
    aliyun_sign_name="测试签名",
    aliyun_template_yzm="SMS_123456789",
)


@pytest.fixture
def sender():
    from src.config.settings import settings

    # aliyun.py 顶部已 `from src.config.settings import settings` 固定绑定，
    # 必须直接 patch 真实 settings.sms 对象的属性
    with patch.object(settings.sms, "channel", "Aliyun"), patch.object(
        settings.sms, "aliyun_access_key_id", ALIYUN_ENV["aliyun_access_key_id"]
    ), patch.object(
        settings.sms, "aliyun_access_key_secret", ALIYUN_ENV["aliyun_access_key_secret"]
    ), patch.object(
        settings.sms, "aliyun_sign_name", ALIYUN_ENV["aliyun_sign_name"]
    ), patch.object(
        settings.sms, "aliyun_template_yzm", ALIYUN_ENV["aliyun_template_yzm"]
    ):
        yield AliyunSmsSender()


class TestAliyunSmsSender:
    def test_channel_name(self, sender):
        assert sender.channel_name == "Aliyun"

    def test_is_available_configured(self, sender):
        assert sender.is_available() is True

    def test_is_available_missing_secret(self, sender):
        sender.access_key_secret = ""
        assert sender.is_available() is False

    def test_send_invalid_mobile(self, sender):
        result = sender.send("12345", template_params={"code": "123456"})
        assert result["code"] == 400

    def test_send_success_normalizes_code(self, sender):
        with patch("src.sms.aliyun.requests.get") as mock_get:
            mock_get.return_value.json.return_value = {
                "Code": "OK", "Message": "OK", "BizId": "9006197", "RequestId": "F655"
            }
            result = sender.send("13800138000", template_params={"code": "123456"})
        assert result["code"] == 200
        assert result["biz_id"] == "9006197"

    def test_send_biz_error_normalizes_code(self, sender):
        with patch("src.sms.aliyun.requests.get") as mock_get:
            mock_get.return_value.json.return_value = {
                "Code": "isv.BUSINESS_LIMIT_CONTROL", "Message": "触发分钟级流控", "RequestId": "X"
            }
            result = sender.send("13800138000")
        assert result["code"] == 500
        assert result["biz_code"] == "isv.BUSINESS_LIMIT_CONTROL"

    def test_send_request_exception(self, sender):
        import requests as requests_lib
        with patch("src.sms.aliyun.requests.get", side_effect=requests_lib.Timeout("timeout")):
            result = sender.send("13800138000")
        assert result["code"] == 500

    def test_send_default_template_used(self, sender):
        with patch("src.sms.aliyun.requests.get") as mock_get:
            mock_get.return_value.json.return_value = {"Code": "OK"}
            sender.send("13800138000")
        params = mock_get.call_args.kwargs["params"]
        assert params["TemplateCode"] == "SMS_123456789"
        assert params["SignName"] == "测试签名"
        assert params["PhoneNumbers"] == "13800138000"
        assert params["Action"] == "SendSms"
        assert params["Version"] == "2017-05-25"
        assert "Signature" in params

    def test_signature_deterministic(self, sender):
        params = {
            "AccessKeyId": "test_ak",
            "Action": "SendSms",
            "SignatureNonce": "fixed-nonce",
            "Timestamp": "2026-10-21T00:00:00Z",
        }
        assert sender._sign(params) == sender._sign(params)

    def test_percent_encode(self):
        from src.sms.aliyun import _percent_encode
        # JSON 中的花括号、引号必须被编码（RPC 签名要求）
        assert _percent_encode('{"code":"123456"}') == "%7B%22code%22%3A%22123456%22%7D"
        assert _percent_encode("a+b c") == "a%2Bb%20c"
        assert _percent_encode("a~b-c_d.e") == "a~b-c_d.e"
