"""
钉钉适配器

实现钉钉消息的接收和发送
"""

import base64
import hashlib
import hmac
import time
import json
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional
from datetime import datetime

import httpx
from loguru import logger

from src.channels.base import ChannelAdapter
from src.models.message import UnifiedMessage, UnifiedResponse, MessageType, ChannelType
from src.config.settings import settings


class DingtalkAdapter(ChannelAdapter):
    """
    钉钉适配器

    实现：
    - 消息接收和解析
    - 消息发送
    - 用户信息获取
    - 签名验证
    """

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        token: Optional[str] = None,
        encoding_aes_key: Optional[str] = None,
    ):
        """
        初始化钉钉适配器

        Args:
            app_key: 钉钉应用Key
            app_secret: 钉钉应用Secret
            token: 回调Token
            encoding_aes_key: 加密密钥
        """
        config = settings.channels.dingtalk
        self.app_key = app_key or config.app_key
        self.app_secret = app_secret or config.app_secret
        self.token = token or config.token
        self.encoding_aes_key = encoding_aes_key or config.encoding_aes_key

        self._access_token: Optional[str] = None
        self._token_expires: int = 0

    @property
    def channel_type(self) -> str:
        """获取渠道类型"""
        return "dingtalk"

    async def get_access_token(self) -> str:
        """
        获取钉钉access_token

        Returns:
            access_token
        """
        if self._access_token and time.time() < self._token_expires:
            return self._access_token

        try:
            async with httpx.AsyncClient() as client:
                url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
                response = await client.post(
                    url,
                    json={
                        "appKey": self.app_key,
                        "appSecret": self.app_secret,
                    }
                )

                data = response.json()

                if data.get("errcode", 0) != 0 and data.get("code") != 0:
                    raise RuntimeError(f"获取access_token失败: {data.get('errmsg') or data.get('message')}")

                self._access_token = data.get("accessToken") or data.get("access_token")
                self._token_expires = time.time() + (data.get("expireIn") or 7200) - 300

                logger.info("获取钉钉access_token成功")
                return self._access_token

        except Exception as e:
            logger.error(f"获取access_token失败: {e}")
            raise

    def _decrypt_message(self, encrypt_str: str) -> Dict[str, Any]:
        """
        解密钉钉消息

        Args:
            encrypt_str: 加密的密文（Base64编码）

        Returns:
            解密后的消息字典
        """
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend

        # Base64解码
        encrypted_data = base64.b64decode(encrypt_str)

        # 取前16字节作为IV
        iv = encrypted_data[:16]
        cipher_text = encrypted_data[16:]

        # AES-256-CBC解密
        key = self.encoding_aes_key.encode('utf-8')[:32].ljust(32, b'\0')
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(cipher_text) + decryptor.finalize()

        # 去除PKCS7填充
        padding_len = decrypted[-1]
        decrypted = decrypted[:-padding_len]

        # 解析JSON（包含msg_signature, time, nonce等）
        return json.loads(decrypted.decode('utf-8'))

    async def parse_message(self, raw_message: Dict[str, Any]) -> UnifiedMessage:
        """
        解析钉钉消息

        Args:
            raw_message: 原始消息数据

        Returns:
            统一消息格式
        """
        # 如果是加密消息，先解密
        if raw_message.get("encrypt"):
            raw_message = self._decrypt_message(raw_message["encrypt"])

        # 解析XML格式的回调消息
        xml_content = raw_message.get("xml") or raw_message.get("body", "")
        if isinstance(xml_content, str):
            root = ET.fromstring(xml_content)
        else:
            root = xml_content

        msg_type = root.findtext("MsgType", "text")
        from_user = root.findtext("FromUserName", "")
        to_user = root.findtext("ToUserName", "")
        create_time = int(root.findtext("CreateTime", "0"))
        msg_id = root.findtext("MsgId", f"dingtalk_{create_time}")

        message_type = MessageType.TEXT
        content = {}

        if msg_type == "text":
            content["text"] = root.findtext("Content", "")
        elif msg_type == "image":
            message_type = MessageType.IMAGE
            content["pic_url"] = root.findtext("PicUrl", "")
            content["media_id"] = root.findtext("MediaId", "")
        elif msg_type == "voice":
            message_type = MessageType.FILE
            content["media_id"] = root.findtext("MediaId", "")
            content["voice_length"] = root.findtext("VoiceDuration", "")
        elif msg_type == "file":
            message_type = MessageType.FILE
            content["media_id"] = root.findtext("MediaId", "")
            content["file_name"] = root.findtext("FileName", "")
        elif msg_type == "event":
            message_type = MessageType.EVENT
            content["event"] = root.findtext("Event", "")
            content["event_key"] = root.findtext("EventKey", "")

        return UnifiedMessage(
            message_id=msg_id,
            channel_type=ChannelType.DINGTALK,
            user_id=from_user,
            user_name="",
            message_type=message_type,
            content=content,
            timestamp=datetime.fromtimestamp(create_time) if create_time > 0 else datetime.now(),
            raw_message={"xml": xml_content} if isinstance(xml_content, str) else raw_message,
        )

    async def send_message(self, message: UnifiedResponse) -> bool:
        """
        发送钉钉消息

        Args:
            message: 统一响应格式

        Returns:
            是否发送成功
        """
        try:
            access_token = await self.get_access_token()

            async with httpx.AsyncClient() as client:
                url = "https://api.dingtalk.com/v1.0/im/messages"
                response = await client.post(
                    url,
                    headers={
                        "x-acs-dingtalk-access-token": access_token,
                        "Content-Type": "application/json",
                    },
                    json={
                        "receiver": message.reply_to,
                        "msgtype": "text",
                        "text": {
                            "content": message.text,
                        },
                    }
                )

                data = response.json()

                if data.get("errcode", 0) != 0 and data.get("code") != 0:
                    logger.error(f"发送消息失败: {data.get('errmsg') or data.get('message')}")
                    return False

                logger.info(f"消息发送成功: {message.reply_to}")
                return True

        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            return False

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """
        获取钉钉用户信息

        Args:
            user_id: 用户ID

        Returns:
            用户信息字典
        """
        try:
            access_token = await self.get_access_token()

            async with httpx.AsyncClient() as client:
                url = f"https://oapi.dingtalk.com/topapi/v2/user/get"
                response = await client.post(
                    url,
                    headers={"x-acs-dingtalk-access-token": access_token},
                    json={
                        "userid": user_id,
                        "language": "zh_CN"
                    }
                )

                data = response.json()

                if data.get("errcode", 0) != 0:
                    logger.error(f"获取用户信息失败: {data.get('errmsg')}")
                    return {}

                user = data.get("result", {})

                return {
                    "user_id": user.get("userid", ""),
                    "name": user.get("name", ""),
                    "department": user.get("dept_id_list", []),
                    "position": user.get("title", ""),
                    "mobile": user.get("mobile", ""),
                    "email": user.get("email", ""),
                    "avatar": user.get("avatar", ""),
                }

        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            return {}

    async def verify_signature(
        self,
        signature: str,
        timestamp: str,
        nonce: str,
        body: str,
    ) -> bool:
        """
        验证钉钉消息签名

        Args:
            signature: 签名
            timestamp: 时间戳
            nonce: 随机数
            body: 消息体

        Returns:
            签名是否有效
        """
        if not self.token:
            return True

        # 排序并拼接
        items = [self.token, timestamp, nonce]
        items.sort()
        combined = "".join(items)

        # 计算SHA1
        calculated = hashlib.sha1(combined.encode()).hexdigest()

        return calculated == signature
