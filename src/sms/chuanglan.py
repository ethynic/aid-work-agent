"""
创蓝253短信发送器

实现创蓝253 短信发送 V2 接口（统一应用 API Key 模式），
不引入 SDK 依赖，复用 requests。
文档参考：
- 短信发送：https://doc.chuanglan.com/docs/4KGLSB1T76BPH1NW
- API 调用说明（鉴权/CheckSum）：https://doc.chuanglan.com/docs/I58861JUE7A3QRU4
"""

import hashlib
import json
import time
import uuid
from typing import Dict, Optional

import requests
from loguru import logger

from src.config.settings import settings
from .base import SmsSender


def _sha1_hex(value: str) -> str:
    """SHA1 十六进制小写摘要（创蓝 CheckSum 算法）"""
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


class ChuangLan253Sender(SmsSender):
    """创蓝253短信发送器"""

    API_URL = "https://smssh.253.com/sms/v2/batch-send"
    PRODUCT_TYPE_VERIFY = "verify"
    # 响应归一化：创蓝 code="000000" 映射为 code=200，供调用方统一判断
    SUCCESS_BIZ_CODE = "000000"
    SUCCESS_CODE = 200
    FAIL_CODE = 500
    # 模板变量名前缀：模板变量按参数传入顺序映射为 param1、param2 ...
    PARAM_NAME_PREFIX = "param"

    @property
    def channel_name(self) -> str:
        return "ChuangLan253"

    def __init__(self):
        """初始化，从配置读取参数（AppID 与 AppSecret 成对下发，模板为验证码模板）"""
        self.app_id = settings.sms.chuanglan_app_id
        self.app_secret = settings.sms.chuanglan_app_secret
        self.default_template_yzm = settings.sms.chuanglan_template_yzm

    def is_available(self) -> bool:
        """检查是否配置完整（创蓝无独立签名，签名在模板中）"""
        return all([
            self.app_id,
            self.app_secret,
            self.default_template_yzm,
        ])

    def _build_check_sum(self, nonce: str, cur_time: str) -> str:
        """CheckSum = SHA1(AppSecret + Nonce + CurTime)，顺序不可调换"""
        return _sha1_hex(f"{self.app_secret}{nonce}{cur_time}")

    def _build_headers(self, nonce: str, cur_time: str) -> Dict[str, str]:
        """构造鉴权请求头"""
        return {
            "Content-Type": "application/json",
            "AppID": self.app_id,
            "Nonce": nonce,
            "CurTime": cur_time,
            "CheckSum": self._build_check_sum(nonce, cur_time),
        }

    @classmethod
    def _build_template_param(cls, template_params: Optional[Dict[str, str]]) -> Optional[str]:
        """
        把模板参数转成创蓝要求的「字符串化 JSON 数组」。

        创蓝模板变量固定命名为 param1、param2 ...，按手机号顺序每组一个对象；
        首期单号码发送，数组只有一个元素（多号码时每个号码一个元素）；
        同一元素内的 param1/param2 ... 由 dict 值顺序决定。
        无变量模板（如纯通知模板）返回 None，不传 templateParam。
        """
        if not template_params:
            return None
        param_group = {
            f"{cls.PARAM_NAME_PREFIX}{index}": str(value)
            for index, value in enumerate(template_params.values(), start=1)
        }
        return json.dumps([param_group], ensure_ascii=False, separators=(",", ":"))

    def _normalize(self, result: Dict) -> Dict:
        """将创蓝响应归一化为项目统一格式（code=200 表示成功）"""
        biz_code = str(result.get("code", ""))
        if biz_code == self.SUCCESS_BIZ_CODE:
            data = result.get("data") or {}
            return {
                "code": self.SUCCESS_CODE,
                "msg": result.get("msg", "success"),
                "biz_code": biz_code,
                "biz_id": data.get("msgId"),
                "request_id": result.get("requestId"),
                "success_num": data.get("successNum"),
                "fail_num": data.get("failNum"),
            }
        return {
            "code": self.FAIL_CODE,
            "msg": result.get("msg") or f"创蓝253短信返回异常: {biz_code}",
            "biz_code": biz_code,
            "request_id": result.get("requestId"),
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
            template_id: 模板 ID（如 1022923760），不传使用默认验证码模板
            template_params: 模板参数，例如 {"code": "123456"}；
                按 dict 值顺序映射为 param1、param2 ...

        Returns:
            归一化响应字典，code=200 表示成功
        """
        if not self.is_available():
            logger.error("创蓝253短信配置不完整")
            return {"code": self.FAIL_CODE, "msg": "短信配置不完整"}

        if not self.validate_mobile(mobile):
            logger.error(f"手机号格式错误: {mobile}")
            return {"code": 400, "msg": "手机号格式错误，必须是11位数字"}

        if not template_id:
            template_id = self.default_template_yzm

        body = {
            "productType": self.PRODUCT_TYPE_VERIFY,
            "phoneNumbers": mobile,
            "templateCode": template_id,
        }
        template_param = self._build_template_param(template_params)
        if template_param:
            body["templateParam"] = template_param

        nonce = uuid.uuid4().hex
        cur_time = str(int(time.time()))
        headers = self._build_headers(nonce, cur_time)
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))

        try:
            response = requests.post(
                self.API_URL,
                headers=headers,
                data=payload.encode("utf-8"),
                timeout=10,
            )
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError as e:
                # 响应体非 JSON（如网关返回 HTML 错误页）
                logger.error(f"创蓝253短信响应解析失败: {e}")
                return {"code": self.FAIL_CODE, "msg": f"响应解析失败: {str(e)}"}
            normalized = self._normalize(result)
            if normalized.get("biz_code") == "190403":
                # 常见部署问题：创蓝控制台启用了 IP 白名单，服务器出口 IP 未登记
                logger.warning(
                    "创蓝253 IP 白名单校验失败：需在创蓝控制台把服务器出口 IP 加入白名单"
                )
            logger.info(f"创蓝253短信发送结果: mobile={mobile}, result={normalized}")
            return normalized
        except requests.RequestException as e:
            # 创蓝业务错误同样返回 HTTP 200 + JSON；仅在网关/网络异常时走此分支。
            # HTTP 错误仍尝试解析响应体保留具体业务码，便于排障。
            normalized = None
            if getattr(e, "response", None) is not None:
                try:
                    normalized = self._normalize(e.response.json())
                except Exception:
                    normalized = None
            if normalized is None:
                normalized = {"code": self.FAIL_CODE, "msg": f"请求失败: {str(e)}"}
            logger.error(f"创蓝253短信请求失败: {e}, result={normalized}")
            return normalized
        except Exception as e:
            logger.error(f"创蓝253短信发送异常: {e}")
            return {"code": self.FAIL_CODE, "msg": f"异常: {str(e)}"}
