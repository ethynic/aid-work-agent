"""创蓝253短信发送器单元测试（全 mock，无外部调用）"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.sms.chuanglan import ChuangLan253Sender, _sha1_hex

pytestmark = pytest.mark.unit


CHUANGLAN_ENV = dict(
    chuanglan_app_id="PROD_TEST_APP_ID",
    chuanglan_app_secret="test_secret_value",
    chuanglan_template_yzm="1022923760",
)


@pytest.fixture
def sender():
    from src.config.settings import settings

    # chuanglan.py 顶部已 `from src.config.settings import settings` 固定绑定，
    # 必须直接 patch 真实 settings.sms 对象的属性
    with patch.object(settings.sms, "channel", "ChuangLan253"), patch.object(
        settings.sms, "chuanglan_app_id", CHUANGLAN_ENV["chuanglan_app_id"]
    ), patch.object(
        settings.sms, "chuanglan_app_secret", CHUANGLAN_ENV["chuanglan_app_secret"]
    ), patch.object(
        settings.sms, "chuanglan_template_yzm", CHUANGLAN_ENV["chuanglan_template_yzm"]
    ):
        yield ChuangLan253Sender()


def _mock_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


SUCCESS_PAYLOAD = {
    "msg": "success",
    "code": "000000",
    "data": {
        "failNum": "0",
        "successNum": "1",
        "msgId": "26073016585700902202000045962837",
    },
    "requestId": "2CBBEDF76FB64E3EB924DA3958B7D6BB",
}


class TestChuangLan253Sender:
    def test_channel_name(self, sender):
        assert sender.channel_name == "ChuangLan253"

    def test_is_available_configured(self, sender):
        assert sender.is_available() is True

    def test_is_available_missing_secret(self, sender):
        sender.app_secret = ""
        assert sender.is_available() is False

    def test_is_available_missing_template(self, sender):
        sender.default_template_yzm = ""
        assert sender.is_available() is False

    def test_send_not_available(self, sender):
        sender.app_id = ""
        with patch("src.sms.chuanglan.requests.post") as mock_post:
            result = sender.send("13800138000")
        assert result["code"] == 500
        mock_post.assert_not_called()

    def test_send_invalid_mobile(self, sender):
        with patch("src.sms.chuanglan.requests.post") as mock_post:
            result = sender.send("12345", template_params={"code": "123456"})
        assert result["code"] == 400
        mock_post.assert_not_called()

    def test_check_sum_matches_official_example(self, sender):
        """官方文档 CheckSum 计算示例（AppSecret + Nonce + CurTime 顺序）"""
        sender.app_secret = "a3K7mP9xQA"
        nonce = "36SuukDa291gfN4dTSNQ07lxFzP68KPB"
        cur_time = "1779367802"
        assert sender._build_check_sum(nonce, cur_time) == (
            "09dedc0e189b21e774686df7055c6bb003b0a128"
        )

    def test_sha1_hex_known_value(self):
        assert _sha1_hex("abc") == "a9993e364706816aba3e25717850c26c9cd0d89d"

    def test_build_headers(self, sender):
        sender.app_secret = "a3K7mP9xQA"
        headers = sender._build_headers("nonce123", "1779367802")
        assert headers["Content-Type"] == "application/json"
        assert headers["AppID"] == "PROD_TEST_APP_ID"
        assert headers["Nonce"] == "nonce123"
        assert headers["CurTime"] == "1779367802"
        assert headers["CheckSum"] == _sha1_hex("a3K7mP9xQAnonce1231779367802")

    def test_template_param_single_value(self):
        result = ChuangLan253Sender._build_template_param({"code": "123456"})
        assert json.loads(result) == [{"param1": "123456"}]

    def test_template_param_positional_mapping(self):
        result = ChuangLan253Sender._build_template_param({"name": "张三", "days": "3"})
        assert json.loads(result) == [{"param1": "张三", "param2": "3"}]

    def test_template_param_empty(self):
        assert ChuangLan253Sender._build_template_param(None) is None
        assert ChuangLan253Sender._build_template_param({}) is None

    def test_send_success_normalizes_code(self, sender):
        with patch("src.sms.chuanglan.requests.post") as mock_post:
            mock_post.return_value = _mock_response(SUCCESS_PAYLOAD)
            result = sender.send("13800138000", template_params={"code": "123456"})
        assert result["code"] == 200
        assert result["biz_code"] == "000000"
        assert result["biz_id"] == "26073016585700902202000045962837"
        assert result["success_num"] == "1"

    def test_send_request_shape(self, sender):
        with patch("src.sms.chuanglan.requests.post") as mock_post:
            mock_post.return_value = _mock_response(SUCCESS_PAYLOAD)
            sender.send("13800138000", template_params={"code": "123456"})
        kwargs = mock_post.call_args.kwargs
        assert kwargs["timeout"] == 10
        assert kwargs["headers"]["AppID"] == "PROD_TEST_APP_ID"
        assert len(kwargs["headers"]["Nonce"]) == 32
        body = json.loads(kwargs["data"].decode("utf-8"))
        assert body == {
            "productType": "verify",
            "phoneNumbers": "13800138000",
            "templateCode": "1022923760",
            "templateParam": '[{"param1":"123456"}]',
        }

    def test_send_explicit_template_id(self, sender):
        with patch("src.sms.chuanglan.requests.post") as mock_post:
            mock_post.return_value = _mock_response(SUCCESS_PAYLOAD)
            sender.send("13800138000", template_id="9999999")
        body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        assert body["templateCode"] == "9999999"

    def test_send_biz_error_normalizes_code(self, sender):
        # 创蓝业务错误同样 HTTP 200，靠响应体 code 判定
        payload = {"msg": "验签失败", "code": "120001", "requestId": "X"}
        with patch("src.sms.chuanglan.requests.post") as mock_post:
            mock_post.return_value = _mock_response(payload)
            result = sender.send("13800138000", template_params={"code": "123456"})
        assert result["code"] == 500
        assert result["biz_code"] == "120001"
        assert result["msg"] == "验签失败"

    def test_send_ip_whitelist_error(self, sender):
        # 部署常见问题：服务器出口 IP 未加入创蓝控制台白名单
        payload = {"msg": "IP白名单校验失败", "code": "190403", "requestId": "Z"}
        with patch("src.sms.chuanglan.requests.post") as mock_post:
            mock_post.return_value = _mock_response(payload)
            result = sender.send("13800138000", template_params={"code": "123456"})
        assert result["code"] == 500
        assert result["biz_code"] == "190403"

    def test_send_timeout(self, sender):
        import requests as requests_lib
        with patch("src.sms.chuanglan.requests.post", side_effect=requests_lib.Timeout("timeout")):
            result = sender.send("13800138000", template_params={"code": "123456"})
        assert result["code"] == 500

    def test_send_http_error_with_json_body_keeps_biz_code(self, sender):
        import requests as requests_lib
        response = _mock_response({"msg": "签名过期", "code": "120504", "requestId": "Y"})
        error = requests_lib.HTTPError("400 Client Error", response=response)
        with patch("src.sms.chuanglan.requests.post", side_effect=error):
            result = sender.send("13800138000", template_params={"code": "123456"})
        assert result["code"] == 500
        assert result["biz_code"] == "120504"

    def test_send_non_json_response(self, sender):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.side_effect = ValueError("Expecting value")
        with patch("src.sms.chuanglan.requests.post", return_value=response):
            result = sender.send("13800138000", template_params={"code": "123456"})
        assert result["code"] == 500
        assert "响应解析失败" in result["msg"]


def test_manager_registers_chuanglan():
    """通道集中注册：SMS_CHANNEL=ChuangLan253 可选中"""
    from src.sms.manager import sms_manager
    assert "ChuangLan253" in sms_manager.list_channels()
