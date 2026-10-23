"""
腾讯云短信发送器

实现腾讯云短信服务 v3 SendSms 接口，TC3-HMAC-SHA256 签名方式，
不引入腾讯云 SDK 依赖，复用 requests。
文档参考：https://cloud.tencent.com/document/api/382/55981
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import requests
from loguru import logger

from src.config.settings import settings
from .base import SmsSender


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TencentSmsSender(SmsSender):
    """腾讯云短信发送器"""

    API_URL = "https://sms.tencentcloudapi.com/"
    API_HOST = "sms.tencentcloudapi.com"
    API_SERVICE = "sms"
    API_VERSION = "2021-01-11"
    API_REGION = "ap-guangzhou"
    # 响应归一化：腾讯云 SendStatusSet[].Code="Ok" 映射为 code=200，供调用方统一判断
    SUCCESS_CODE = 200
    FAIL_CODE = 500

    @property
    def channel_name(self) -> str:
        return "Tencent"

    def __init__(self):
        """初始化，从配置读取参数"""
        self.secret_id = settings.sms.tencent_secret_id
        self.secret_key = settings.sms.tencent_secret_key
        self.sms_sdk_app_id = settings.sms.tencent_sms_sdk_app_id
        self.sign_name = settings.sms.tencent_sign_name
        self.default_template_yzm = settings.sms.tencent_template_yzm

    def is_available(self) -> bool:
        """检查是否配置完整"""
        return all([
            self.secret_id,
            self.secret_key,
            self.sms_sdk_app_id,
            self.sign_name,
            self.default_template_yzm,
        ])

    def _sign(
        self,
        payload: str,
        timestamp: int,
        content_type: str = "application/json; charset=utf-8",
    ) -> str:
        """计算 TC3-HMAC-SHA256 签名，返回 Authorization 头"""
        date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

        canonical_request = "\n".join([
            "POST",
            "/",
            "",
            f"content-type:{content_type}\nhost:{self.API_HOST}\n",
            "content-type;host",
            _sha256_hex(payload),
        ])
        string_to_sign = "\n".join([
            "TC3-HMAC-SHA256",
            str(timestamp),
            f"{date}/{self.API_SERVICE}/tc3_request",
            _sha256_hex(canonical_request),
        ])

        k_date = _hmac_sha256(("TC3" + self.secret_key).encode("utf-8"), date)
        k_service = _hmac_sha256(k_date, self.API_SERVICE)
        k_signing = _hmac_sha256(k_service, "tc3_request")
        signature = hmac.new(
            k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        credential = f"{self.secret_id}/{date}/{self.API_SERVICE}/tc3_request"
        return (
            f"TC3-HMAC-SHA256 Credential={credential}, "
            f"SignedHeaders=content-type;host, Signature={signature}"
        )

    def _normalize(self, result: Dict) -> Dict:
        """将腾讯云响应归一化为项目统一格式（code=200 表示成功）"""
        response = result.get("Response", {})
        error = response.get("Error")
        if error:
            return {
                "code": self.FAIL_CODE,
                "msg": error.get("Message", "腾讯云短信返回异常"),
                "biz_code": error.get("Code"),
                "request_id": response.get("RequestId"),
            }
        status_set = response.get("SendStatusSet") or []
        if not status_set:
            return {
                "code": self.FAIL_CODE,
                "msg": "腾讯云短信返回异常: SendStatusSet 为空",
                "request_id": response.get("RequestId"),
            }
        status = status_set[0]
        if status.get("Code") == "Ok":
            return {
                "code": self.SUCCESS_CODE,
                "msg": status.get("Message", "OK"),
                "biz_id": status.get("SerialNo"),
                "request_id": response.get("RequestId"),
            }
        return {
            "code": self.FAIL_CODE,
            "msg": status.get("Message", f"腾讯云短信返回异常: {status.get('Code')}"),
            "biz_code": status.get("Code"),
            "request_id": response.get("RequestId"),
        }

    def send(
        self,
        mobile: str,
        template_id: Optional[str] = None,
        template_params: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """
        发送短信

        Args:
            mobile: 11位手机号
            template_id: 模板ID（纯数字，如 1234567），不传使用默认验证码模板
            template_params: 模板参数，例如 {"code": "123456"}；
                腾讯云为位置参数，按 dict 值顺序转为 TemplateParamSet 数组

        Returns:
            归一化响应字典，code=200 表示成功
        """
        if not self.is_available():
            logger.error("腾讯云短信配置不完整")
            return {"code": self.FAIL_CODE, "msg": "短信配置不完整"}

        if not self.validate_mobile(mobile):
            logger.error(f"手机号格式错误: {mobile}")
            return {"code": 400, "msg": "手机号格式错误，必须是11位数字"}

        if not template_id:
            template_id = self.default_template_yzm

        if not template_params:
            template_params = {}
        # 腾讯云 TemplateParamSet 为位置参数数组，按值顺序传入
        template_param_set = [str(v) for v in template_params.values()]

        payload = json.dumps(
            {
                "PhoneNumberSet": [f"+86{mobile}"],
                "SmsSdkAppId": self.sms_sdk_app_id,
                "SignName": self.sign_name,
                "TemplateId": template_id,
                "TemplateParamSet": template_param_set,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        timestamp = int(time.time())
        content_type = "application/json; charset=utf-8"

        try:
            headers = {
                "Content-Type": content_type,
                "X-TC-Action": "SendSms",
                "X-TC-Version": self.API_VERSION,
                "X-TC-Region": self.API_REGION,
                "X-TC-Timestamp": str(timestamp),
                "X-TC-Language": "zh-CN",
                "Authorization": self._sign(payload, timestamp, content_type),
            }
            response = requests.post(self.API_URL, data=payload.encode("utf-8"), headers=headers, timeout=30)
            response.raise_for_status()
            result = response.json()
            normalized = self._normalize(result)
            logger.info(f"腾讯云短信发送结果: mobile={mobile}, result={normalized}")
            return normalized
        except requests.RequestException as e:
            # 腾讯云 v3 对鉴权/签名类错误返回 HTTP 4xx + Response.Error，
            # raise_for_status 抛错后仍解析响应体保留具体错误码，便于排障
            normalized = None
            if getattr(e, "response", None) is not None:
                try:
                    normalized = self._normalize(e.response.json())
                except Exception:
                    normalized = None
            if normalized is None:
                normalized = {"code": self.FAIL_CODE, "msg": f"请求失败: {str(e)}"}
            logger.error(f"腾讯云短信请求失败: {e}, result={normalized}")
            return normalized
        except Exception as e:
            logger.error(f"腾讯云短信发送异常: {e}")
            return {"code": self.FAIL_CODE, "msg": f"异常: {str(e)}"}
