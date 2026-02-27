"""
企业微信适配器

实现企业微信消息的接收和发送
"""

import base64
import hashlib
import hmac
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional
from datetime import datetime

import httpx
from loguru import logger

from src.channels.base import ChannelAdapter
from src.models.message import UnifiedMessage, UnifiedResponse, MessageType
from src.config.settings import settings


class WeComAdapter(ChannelAdapter):
    """
    企业微信适配器
    
    实现：
    - 消息接收和解析
    - 消息发送
    - 用户信息获取
    - 签名验证
    """
    
    def __init__(
        self,
        corp_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        secret: Optional[str] = None,
        token: Optional[str] = None,
        encoding_aes_key: Optional[str] = None,
    ):
        """
        初始化企业微信适配器
        
        Args:
            corp_id: 企业ID
            agent_id: 应用ID
            secret: 应用Secret
            token: 回调Token
            encoding_aes_key: 加密密钥
        """
        config = settings.channels.wecom
        self.corp_id = corp_id or config.corp_id
        self.agent_id = agent_id or config.agent_id
        self.secret = secret or config.secret
        self.token = token or config.token
        self.encoding_aes_key = encoding_aes_key or config.encoding_aes_key
        
        self._access_token: Optional[str] = None
        self._token_expires: int = 0
    
    @property
    def channel_type(self) -> str:
        """获取渠道类型"""
        return "wecom"
    
    async def get_access_token(self) -> str:
        """
        获取企业微信access_token
        
        Returns:
            access_token
        """
        # 检查缓存
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        
        try:
            async with httpx.AsyncClient() as client:
                url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken"
                response = await client.get(
                    url,
                    params={
                        "corpid": self.corp_id,
                        "corpsecret": self.secret,
                    }
                )
                
                data = response.json()
                
                if data.get("errcode", 0) != 0:
                    raise RuntimeError(f"获取access_token失败: {data.get('errmsg')}")
                
                self._access_token = data["access_token"]
                self._token_expires = time.time() + data["expires_in"] - 300
                
                logger.info("获取企业微信access_token成功")
                return self._access_token
        
        except Exception as e:
            logger.error(f"获取access_token失败: {e}")
            raise
    
    async def parse_message(self, raw_message: Dict[str, Any]) -> UnifiedMessage:
        """
        解析企业微信消息
        
        Args:
            raw_message: 原始消息数据
        
        Returns:
            统一消息格式
        """
        # 解析XML消息
        xml_content = raw_message.get("body", "") or raw_message.get("xml", "")
        
        if isinstance(xml_content, str):
            root = ET.fromstring(xml_content)
        else:
            root = xml_content
        
        # 提取消息信息
        msg_type = root.findtext("MsgType", "text")
        from_user = root.findtext("FromUserName", "")
        to_user = root.findtext("ToUserName", "")
        create_time = int(root.findtext("CreateTime", "0"))
        msg_id = root.findtext("MsgId", f"msg_{create_time}")
        
        # 解析消息内容
        content = {}
        message_type = MessageType.TEXT
        
        if msg_type == "text":
            content["text"] = root.findtext("Content", "")
        elif msg_type == "image":
            message_type = MessageType.IMAGE
            content["pic_url"] = root.findtext("PicUrl", "")
            content["media_id"] = root.findtext("MediaId", "")
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
            channel_type=self.channel_type,
            user_id=from_user,
            message_type=message_type,
            content=content,
            timestamp=datetime.fromtimestamp(create_time),
            raw_message={"xml": xml_content} if isinstance(xml_content, str) else raw_message,
        )
    
    async def send_message(self, message: UnifiedResponse) -> bool:
        """
        发送企业微信消息
        
        Args:
            message: 统一响应格式
        
        Returns:
            是否发送成功
        """
        try:
            access_token = await self.get_access_token()
            
            # 构建消息体
            msg_data = {
                "touser": message.reply_to,
                "msgtype": "text",
                "agentid": self.agent_id,
                "text": {
                    "content": message.text,
                },
            }
            
            async with httpx.AsyncClient() as client:
                url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
                response = await client.post(url, json=msg_data)
                
                data = response.json()
                
                if data.get("errcode", 0) != 0:
                    logger.error(f"发送消息失败: {data.get('errmsg')}")
                    return False
                
                logger.info(f"消息发送成功: {message.reply_to}")
                return True
        
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            return False
    
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """
        获取企业微信用户信息
        
        Args:
            user_id: 用户ID
        
        Returns:
            用户信息字典
        """
        try:
            access_token = await self.get_access_token()
            
            async with httpx.AsyncClient() as client:
                url = f"https://qyapi.weixin.qq.com/cgi-bin/user/get"
                response = await client.get(
                    url,
                    params={
                        "access_token": access_token,
                        "userid": user_id,
                    }
                )
                
                data = response.json()
                
                if data.get("errcode", 0) != 0:
                    logger.error(f"获取用户信息失败: {data.get('errmsg')}")
                    return {}
                
                return {
                    "user_id": data.get("userid", ""),
                    "name": data.get("name", ""),
                    "department": data.get("department", []),
                    "position": data.get("position", ""),
                    "mobile": data.get("mobile", ""),
                    "email": data.get("email", ""),
                    "avatar": data.get("avatar", ""),
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
        验证企业微信消息签名
        
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
