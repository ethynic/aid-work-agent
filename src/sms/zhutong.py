"""
助通短信发送器

实现助通短信 API V2 版本
文档参考：https://zhutongdocs.apifox.cn/
"""

import hashlib
import json
import time
from typing import Dict, Optional

import requests
from loguru import logger

from src.config.settings import settings
from .base import SmsSender


class ZhuTongSmsSender(SmsSender):
    """助通短信发送器"""

    API_URL = "https://api-shss.zthysms.com/v2/sendSmsTp"

    @property
    def channel_name(self) -> str:
        return "ZhuTong"

    def __init__(self):
        """初始化，从配置读取参数"""
        self.username = settings.sms.username
        self.password = settings.sms.password
        self.signature = settings.sms.signature
        self.default_template_yzm = settings.sms.template_yzm

    def is_available(self) -> bool:
        """检查是否配置完整"""
        return all([
            self.username,
            self.password,
            self.signature,
            self.default_template_yzm,
        ])

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
            template_id: 模板ID，不传使用默认验证码模板
            template_params: 模板参数，例如 {"code": "123456"}

        Returns:
            API 响应字典
        """
        # 验证参数
        if not self.is_available():
            logger.error("助通短信配置不完整")
            return {"code": 500, "msg": "短信配置不完整"}

        if not self.validate_mobile(mobile):
            logger.error(f"手机号格式错误: {mobile}")
            return {"code": 400, "msg": "手机号格式错误，必须是11位数字"}

        # 使用默认模板
        if not template_id:
            template_id = self.default_template_yzm

        if not template_params:
            template_params = {}

        # 计算签名密码
        t_key = str(int(time.time()))[:10]
        password_md5 = hashlib.md5(self.password.encode()).hexdigest()
        hasher = hashlib.md5((password_md5 + t_key).encode()).hexdigest()

        # 构造请求数据
        request_data = {
            "username": self.username,
            "password": hasher,
            "tKey": t_key,
            "signature": f"【{self.signature}】",
            "tpId": template_id,
            "records": [
                {"mobile": mobile, "tpContent": template_params}
            ]
        }

        headers = {"Content-Type": "application/json"}
        logger.debug(f"助通短信请求: {request_data}")

        try:
            response = requests.post(
                self.API_URL,
                headers=headers,
                data=json.dumps(request_data, ensure_ascii=True),
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            logger.info(f"助通短信发送结果: mobile={mobile}, result={result}")
            return result
        except requests.RequestException as e:
            logger.error(f"助通短信请求失败: {e}")
            return {"code": 500, "msg": f"请求失败: {str(e)}"}
        except Exception as e:
            logger.error(f"助通短信发送异常: {e}")
            return {"code": 500, "msg": f"异常: {str(e)}"}
