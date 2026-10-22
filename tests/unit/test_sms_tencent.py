"""腾讯云短信发送器单元测试（全 mock，无外部调用）"""

import json
from unittest.mock import patch

import pytest

from src.sms.tencent import TencentSmsSender

pytestmark = pytest.mark.unit


TENCENT_ENV = dict(
    tencent_secret_id="test_id",
    tencent_secret_key="test_key",
    tencent_sms_sdk_app_id="1400000000",
    tencent_sign_name="测试签名",
    tencent_template_yzm="1234567",
)


@pytest.fixture
def sender():
    from src.config.settings import settings

    # tencent.py 顶部已 `from src.config.settings import settings` 固定绑定，
    # 必须直接 patch 真实 settings.sms 对象的属性
    with patch.object(settings.sms, "channel", "Tencent"), patch.object(
        settings.sms, "tencent_secret_id", TENCENT_ENV["tencent_secret_id"]
    ), patch.object(
        settings.sms, "tencent_secret_key", TENCENT_ENV["tencent_secret_key"]
    ), patch.object(
        settings.sms, "tencent_sms_sdk_app_id", TENCENT_ENV["tencent_sms_sdk_app_id"]
    ), patch.object(
        settings.sms, "tencent_sign_name", TENCENT_ENV["tencent_sign_name"]
    ), patch.object(
        settings.sms, "tencent_template_yzm", TENCENT_ENV["tencent_template_yzm"]
    ):
        yield TencentSmsSender()


class TestTencentSmsSender:
    def test_channel_name(self, sender):
        assert sender.channel_name == "Tencent"

    def test_is_available_configured(self, sender):
        assert sender.is_available() is True

    def test_is_available_missing_secret_key(self, sender):
        sender.secret_key = ""
        assert sender.is_available() is False

    def test_is_available_missing_sdk_app_id(self, sender):
        sender.sms_sdk_app_id = ""
        assert sender.is_available() is False

    def test_send_invalid_mobile(self, sender):
        result = sender.send("12345", template_params={"code": "123456"})
        assert result["code"] == 400

    def test_send_success_normalizes_code(self, sender):
        with patch("src.sms.tencent.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "Response": {
                    "SendStatusSet": [{
                        "SerialNo": "20261022:123456",
                        "PhoneNumber": "+8613800138000",
                        "Fee": 1,
                        "Message": "send success",
                        "Code": "Ok",
                    }],
                    "RequestId": "F655",
                }
            }
            result = sender.send("13800138000", template_params={"code": "123456"})
        assert result["code"] == 200
        assert result["biz_id"] == "20261022:123456"
        assert result["request_id"] == "F655"

    def test_send_status_error_normalizes_code(self, sender):
        with patch("src.sms.tencent.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "Response": {
                    "SendStatusSet": [{
                        "SerialNo": "",
                        "PhoneNumber": "+8613800138000",
                        "Fee": 0,
                        "Message": "触发分钟级流控",
                        "Code": "LimitExceeded.PhoneNumberMinuteLimit",
                    }],
                    "RequestId": "X",
                }
            }
            result = sender.send("13800138000")
        assert result["code"] == 500
        assert result["biz_code"] == "LimitExceeded.PhoneNumberMinuteLimit"

    def test_send_api_error_normalizes_code(self, sender):
        with patch("src.sms.tencent.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "Response": {
                    "Error": {
                        "Code": "AuthFailure.SignatureFailure",
                        "Message": "签名计算错误",
                    },
                    "RequestId": "Y",
                }
            }
            result = sender.send("13800138000")
        assert result["code"] == 500
        assert result["biz_code"] == "AuthFailure.SignatureFailure"
        assert result["request_id"] == "Y"

    def test_send_empty_status_set(self, sender):
        with patch("src.sms.tencent.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {"Response": {"RequestId": "Z"}}
            result = sender.send("13800138000")
        assert result["code"] == 500

    def test_send_request_exception(self, sender):
        import requests as requests_lib
        with patch("src.sms.tencent.requests.post", side_effect=requests_lib.Timeout("timeout")):
            result = sender.send("13800138000")
        assert result["code"] == 500

    def test_send_http_error_parses_error_body(self, sender):
        # 腾讯云 v3 签名/鉴权错误返回 HTTP 4xx + Response.Error，异常分支应保留错误码
        import requests as requests_lib
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "Response": {
                "Error": {"Code": "AuthFailure.SignatureFailure", "Message": "签名计算错误"},
                "RequestId": "Y",
            }
        }
        mock_resp.raise_for_status.side_effect = requests_lib.HTTPError(
            "401 Client Error", response=mock_resp
        )
        with patch("src.sms.tencent.requests.post", return_value=mock_resp):
            result = sender.send("13800138000")
        assert result["code"] == 500
        assert result["biz_code"] == "AuthFailure.SignatureFailure"
        assert result["request_id"] == "Y"

    def test_send_default_template_used(self, sender):
        with patch("src.sms.tencent.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "Response": {
                    "SendStatusSet": [{"SerialNo": "S1", "Code": "Ok", "Message": "send success"}],
                    "RequestId": "R1",
                }
            }
            sender.send("13800138000")
        body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        assert body["TemplateId"] == "1234567"
        assert body["SignName"] == "测试签名"
        assert body["PhoneNumberSet"] == ["+8613800138000"]
        assert body["SmsSdkAppId"] == "1400000000"
        assert body["TemplateParamSet"] == []

    def test_send_template_params_positional_set(self, sender):
        with patch("src.sms.tencent.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "Response": {
                    "SendStatusSet": [{"SerialNo": "S1", "Code": "Ok", "Message": "send success"}],
                    "RequestId": "R1",
                }
            }
            sender.send("13800138000", template_params={"code": "123456"})
        body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        # 腾讯云为位置参数，按 dict 值顺序转数组
        assert body["TemplateParamSet"] == ["123456"]

    def test_request_headers_and_signature_present(self, sender):
        with patch("src.sms.tencent.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "Response": {
                    "SendStatusSet": [{"SerialNo": "S1", "Code": "Ok", "Message": "send success"}],
                    "RequestId": "R1",
                }
            }
            sender.send("13800138000")
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["X-TC-Action"] == "SendSms"
        assert headers["X-TC-Version"] == "2021-01-11"
        assert headers["X-TC-Region"] == "ap-guangzhou"
        assert headers["X-TC-Timestamp"].isdigit()
        assert headers["Authorization"].startswith("TC3-HMAC-SHA256 Credential=test_id/")

    def test_signature_deterministic(self, sender):
        assert sender._sign('{"a":1}', 1761000000) == sender._sign('{"a":1}', 1761000000)

    def test_signature_golden(self, sender):
        # 固定输入 -> 期望输出，防止签名构造被改坏（golden 值经独立实现验算）
        expected = (
            "TC3-HMAC-SHA256 Credential=test_id/2025-10-20/sms/tc3_request, "
            "SignedHeaders=content-type;host, "
            "Signature=5a79d367e32fdb9abfecf54daa0425ff61874bb51c035c9884cb0bba1ee49743"
        )
        assert sender._sign('{"a":1}', 1761000000) == expected

    def test_signature_changes_with_payload(self, sender):
        assert sender._sign('{"a":1}', 1761000000) != sender._sign('{"a":2}', 1761000000)
