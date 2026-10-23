"""
阿里云短信发送器

实现阿里云 Dysmsapi（短信服务）SendSms 接口，RPC 签名方式（HMAC-SHA1），
不引入阿里云 SDK 依赖，复用 requests。
文档参考：https://help.aliyun.com/document_detail/101414.html
"""

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import quote

import requests
from loguru import logger

from src.config.settings import settings
from .base import SmsSender


def _percent_encode(value: str) -> str:
    """阿里云 RPC 签名专用 URL 编码（RFC3986，+/ 空格替换）"""
    return quote(str(value), safe="-_.~")


class AliyunSmsSender(SmsSender):
    """阿里云短信发送器"""

    API_URL = "https://dysmsapi.aliyuncs.com/"
    API_VERSION = "2017-05-25"
    # 响应归一化：阿里云 Code=OK 映射为 code=200，供调用方统一判断
    SUCCESS_CODE = 200
    FAIL_CODE = 500

    @property
    def channel_name(self) -> str:
        return "Aliyun"

    def __init__(self):
        """初始化，从配置读取参数"""
        self.access_key_id = settings.sms.aliyun_access_key_id
        self.access_key_secret = settings.sms.aliyun_access_key_secret
        self.sign_name = settings.sms.aliyun_sign_name
        self.default_template_yzm = settings.sms.aliyun_template_yzm

    def is_available(self) -> bool:
        """检查是否配置完整"""
        return all([
            self.access_key_id,
            self.access_key_secret,
            self.sign_name,
            self.default_template_yzm,
        ])

    def _sign(self, params: Dict[str, str]) -> str:
        """计算 RPC 签名（SignatureMethod=HMAC-SHA1）"""
        sorted_params = sorted(params.items())
        canonical_query = "&".join(
            f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted_params
        )
        string_to_sign = f"GET&{_percent_encode('/')}&{_percent_encode(canonical_query)}"
        digest = hmac.new(
            (self.access_key_secret + "&").encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _normalize(self, result: Dict) -> Dict:
        """将阿里云响应归一化为项目统一格式（code=200 表示成功）"""
        biz_code = result.get("Code")
        if biz_code == "OK":
            return {
                "code": self.SUCCESS_CODE,
                "msg": result.get("Message", "OK"),
                "biz_id": result.get("BizId"),
                "request_id": result.get("RequestId"),
            }
        return {
            "code": self.FAIL_CODE,
            "msg": result.get("Message", f"阿里云短信返回异常: {biz_code}"),
            "biz_code": biz_code,
            "request_id": result.get("RequestId"),
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
            template_id: 模板CODE（如 SMS_123456789），不传使用默认验证码模板
            template_params: 模板参数，例如 {"code": "123456"}

        Returns:
            归一化响应字典，code=200 表示成功
        """
        if not self.is_available():
            logger.error("阿里云短信配置不完整")
            return {"code": self.FAIL_CODE, "msg": "短信配置不完整"}

        if not self.validate_mobile(mobile):
            logger.error(f"手机号格式错误: {mobile}")
            return {"code": 400, "msg": "手机号格式错误，必须是11位数字"}

        if not template_id:
            template_id = self.default_template_yzm

        if not template_params:
            template_params = {}

        params = {
            "AccessKeyId": self.access_key_id,
            "Action": "SendSms",
            "Format": "JSON",
            "PhoneNumbers": mobile,
            "RegionId": "cn-hangzhou",
            "SignName": self.sign_name,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": str(uuid.uuid4()),
            "SignatureVersion": "1.0",
            "TemplateCode": template_id,
            "TemplateParam": json.dumps(template_params, ensure_ascii=False),
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Version": self.API_VERSION,
        }
        params["Signature"] = self._sign(params)

        try:
            response = requests.get(self.API_URL, params=params, timeout=30)
            response.raise_for_status()
            result = response.json()
            normalized = self._normalize(result)
            logger.info(f"阿里云短信发送结果: mobile={mobile}, result={normalized}")
            return normalized
        except requests.RequestException as e:
            logger.error(f"阿里云短信请求失败: {e}")
            return {"code": self.FAIL_CODE, "msg": f"请求失败: {str(e)}"}
        except Exception as e:
            logger.error(f"阿里云短信发送异常: {e}")
            return {"code": self.FAIL_CODE, "msg": f"异常: {str(e)}"}
