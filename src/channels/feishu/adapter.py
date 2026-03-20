"""
飞书适配器

实现飞书消息的接收和发送
"""

import base64
import hashlib
import hmac
import time
import json
from typing import Any, Dict, Optional
from datetime import datetime

import httpx
from loguru import logger

from src.channels.base import ChannelAdapter
from src.models.message import UnifiedMessage, UnifiedResponse, MessageType, ChannelType
from src.config.settings import settings


class FeishuAdapter(ChannelAdapter):
    """
    飞书适配器

    实现：
    - 消息接收和解析
    - 消息发送
    - 用户信息获取
    - 签名验证
    """

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        verification_token: Optional[str] = None,
        encrypt_key: Optional[str] = None,
    ):
        """
        初始化飞书适配器

        Args:
            app_id: 飞书应用ID
            app_secret: 飞书应用Secret
            verification_token: 回调验证Token
            encrypt_key: 加密密钥
        """
        config = settings.channels.feishu
        self.app_id = app_id or config.app_id
        self.app_secret = app_secret or config.app_secret
        self.verification_token = verification_token or config.verification_token
        self.encrypt_key = encrypt_key or config.encrypt_key

        self._access_token: Optional[str] = None
        self._token_expires: int = 0

    @property
    def channel_type(self) -> str:
        """获取渠道类型"""
        return "feishu"

    async def get_access_token(self) -> str:
        """
        获取飞书access_token

        Returns:
            access_token
        """
        if self._access_token and time.time() < self._token_expires:
            return self._access_token

        try:
            async with httpx.AsyncClient() as client:
                url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
                response = await client.post(
                    url,
                    json={
                        "app_id": self.app_id,
                        "app_secret": self.app_secret,
                    }
                )

                data = response.json()

                if data.get("code", 0) != 0:
                    raise RuntimeError(f"获取access_token失败: {data.get('msg')}")

                self._access_token = data["tenant_access_token"]
                self._token_expires = time.time() + data.get("expire", 7200) - 300

                logger.info("获取飞书access_token成功")
                return self._access_token

        except Exception as e:
            logger.error(f"获取access_token失败: {e}")
            raise

    def _decrypt_message(self, encrypted_str: str) -> Dict[str, Any]:
        """
        解密飞书消息

        Args:
            encrypted_str: 加密的密文（Base64编码）

        Returns:
            解密后的消息字典
        """
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
        import os

        # Base64解码
        encrypted_data = base64.b64decode(encrypted_str)

        # 取前16字节作为IV
        iv = encrypted_data[:16]
        cipher_text = encrypted_data[16:]

        # AES-256-CBC解密
        key = self.encrypt_key.encode('utf-8')[:32].ljust(32, b'\0')
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(cipher_text) + decryptor.finalize()

        # 去除PKCS7填充
        padding_len = decrypted[-1]
        decrypted = decrypted[:-padding_len]

        return json.loads(decrypted.decode('utf-8'))

    async def parse_message(self, raw_message: Dict[str, Any]) -> UnifiedMessage:
        """
        解析飞书消息

        Args:
            raw_message: 原始消息数据

        Returns:
            统一消息格式
        """
        # 解密消息（如果启用了加密）
        if self.encrypt_key and raw_message.get("encrypt"):
            raw_message = self._decrypt_message(raw_message["encrypt"])

        # 获取事件类型
        event = raw_message.get("event", {})
        event_type = raw_message.get("event_type", "")

        # 处理不同类型的事件
        if "im.message.receive_v1" in event_type or event.get("message"):
            return await self._parse_app_message(raw_message)
        elif "im.message" in event_type:
            return await self._parse_p2p_chat_message(raw_message)
        else:
            return await self._parse_app_message(raw_message)

    async def _parse_app_message(self, raw_message: Dict[str, Any]) -> UnifiedMessage:
        """解析应用消息"""
        event = raw_message.get("event", {})
        message = event.get("message", {})

        msg_type = message.get("msg_type", "text")
        content = json.loads(message.get("content", "{}"))
        sender = event.get("sender", {})

        message_type = MessageType.TEXT
        msg_content = {}

        if msg_type == "text":
            message_type = MessageType.TEXT
            msg_content["text"] = content.get("text", "")
        elif msg_type == "image":
            message_type = MessageType.IMAGE
            msg_content["image_key"] = content.get("image_key", "")
        elif msg_type == "file":
            message_type = MessageType.FILE
            msg_content["file_key"] = content.get("file_key", "")
            msg_content["file_name"] = content.get("file_name", "")

        return UnifiedMessage(
            message_id=message.get("message_id", f"feishu_{datetime.now().timestamp()}"),
            channel_type=ChannelType.FEISHU,
            user_id=sender.get("sender_id", {}).get("open_id", ""),
            user_name=sender.get("sender_id", {}).get("name", ""),
            department_id=sender.get("sender_id", {}).get("department_id", ""),
            message_type=message_type,
            content=msg_content,
            timestamp=datetime.fromtimestamp(message.get("create_time", datetime.now().timestamp()) / 1000),
            raw_message=raw_message,
        )

    async def _parse_p2p_chat_message(self, raw_message: Dict[str, Any]) -> UnifiedMessage:
        """解析P2P聊天消息"""
        message = raw_message.get("message", {})

        msg_type = message.get("msg_type", "text")
        content = json.loads(message.get("content", "{}"))
        chat_id = message.get("chat_id", "")
        sender = raw_message.get("sender", {})

        message_type = MessageType.TEXT
        msg_content = {}

        if msg_type == "text":
            message_type = MessageType.TEXT
            msg_content["text"] = content.get("text", "")
        elif msg_type == "image":
            message_type = MessageType.IMAGE
            msg_content["image_key"] = content.get("image_key", "")

        return UnifiedMessage(
            message_id=message.get("message_id", f"feishu_{datetime.now().timestamp()}"),
            channel_type=ChannelType.FEISHU,
            user_id=sender.get("open_id", "") or sender.get("user_id", ""),
            user_name="",
            message_type=message_type,
            content=msg_content,
            timestamp=datetime.fromtimestamp(message.get("create_time", datetime.now().timestamp()) / 1000),
            raw_message=raw_message,
        )

    async def send_message(self, message: UnifiedResponse) -> bool:
        """
        发送飞书消息

        Args:
            message: 统一响应格式

        Returns:
            是否发送成功
        """
        try:
            access_token = await self.get_access_token()

            async with httpx.AsyncClient() as client:
                url = "https://open.feishu.cn/open-apis/im/v1/messages"
                response = await client.post(
                    url,
                    params={"receive_id_type": "open_id"},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "receive_id": message.reply_to,
                        "msg_type": "text",
                        "content": json.dumps({"text": message.text}),
                    }
                )

                data = response.json()

                if data.get("code", 0) != 0:
                    logger.error(f"发送消息失败: {data.get('msg')}")
                    return False

                logger.info(f"消息发送成功: {message.reply_to}")
                return True

        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            return False

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """
        获取飞书用户信息

        Args:
            user_id: 用户ID (open_id)

        Returns:
            用户信息字典
        """
        try:
            access_token = await self.get_access_token()

            async with httpx.AsyncClient() as client:
                url = f"https://open.feishu.cn/open-apis/contact/v3/users/{user_id}"
                response = await client.get(
                    url,
                    params={"user_id_type": "open_id"},
                    headers={"Authorization": f"Bearer {access_token}"},
                )

                data = response.json()

                if data.get("code", 0) != 0:
                    logger.error(f"获取用户信息失败: {data.get('msg')}")
                    return {}

                user = data.get("data", {}).get("user", {})

                return {
                    "user_id": user.get("open_id", ""),
                    "name": user.get("name", ""),
                    "en_name": user.get("en_name", ""),
                    "department": user.get("department_ids", []),
                    "position": user.get("position", ""),
                    "email": user.get("email", ""),
                    "avatar": user.get("avatar", {}).get("avatar_72", ""),
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
        验证飞书消息签名

        Args:
            signature: 签名
            timestamp: 时间戳
            nonce: 随机数
            body: 消息体

        Returns:
            签名是否有效
        """
        if not self.encrypt_key:
            return True

        # 构造签名字符串
        string_to_sign = f"{timestamp}{nonce}{body}"
        sign_str = base64.b64decode(self.encrypt_key).decode('utf-8')

        # 计算签名
        calculated = hmac.new(
            sign_str.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return calculated == signature

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """
        获取群信息

        Args:
            chat_id: 群ID

        Returns:
            群信息字典
        """
        try:
            access_token = await self.get_access_token()

            async with httpx.AsyncClient() as client:
                url = f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}"
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {access_token}"},
                )

                data = response.json()

                if data.get("code", 0) != 0:
                    return {}

                return data.get("data", {})

        except Exception as e:
            logger.error(f"获取群信息失败: {e}")
            return {}
