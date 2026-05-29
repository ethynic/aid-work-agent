"""
企业微信适配器

实现企业微信消息的接收和发送，支持:
- AES-256-CBC 消息加解密
- 多种消息类型（text, markdown, textcard, image, file）
- 长消息自动拆分
- 媒体文件上传下载
- HTTP 连接池
- Token 刷新锁和重试逻辑
"""

import asyncio
import hashlib
import re
import time
from collections import deque
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from src.channels.base import ChannelAdapter, build_public_url, format_file_size
from src.channels.wecom.crypto import WeComCrypto
from src.channels.wecom.media import WeComMedia
from src.channels.wecom.message_builder import WeComMessageBuilder
from src.models.message import MessageType, UnifiedMessage, UnifiedResponse


class WeComAdapter(ChannelAdapter):
    """
    企业微信适配器

    实现：
    - 消息接收和解析（支持加解密）
    - 消息发送（支持 text/markdown/image/file，自动拆分长消息）
    - 用户信息获取
    - 签名验证（4 元素 SHA1）
    - 媒体文件处理
    - HTTP 连接池和重试逻辑
    """

    def __init__(
        self,
        corp_id: str,
        agent_id: str,
        secret: str,
        token: Optional[str] = None,
        encoding_aes_key: Optional[str] = None,
        *,
        welcome_message: str = "",
        max_bytes: int = 2048,
        split_on_paragraph: bool = True,
        default_type: str = "markdown",
        max_attempts: int = 3,
        backoff_base: float = 1.0,
        rate_limit_enabled: bool = True,
        rate_limit_max: int = 10,
        media_upload_dir: str = "./storage/uploads/wecom",
    ):
        self.corp_id = corp_id
        self.agent_id = agent_id
        self.secret = secret
        self.token = token or ""
        self.encoding_aes_key = encoding_aes_key or ""
        self.welcome_message = welcome_message

        # 消息配置
        self._msg_config = {
            "max_bytes": max_bytes,
            "split_on_paragraph": split_on_paragraph,
            "default_type": default_type,
        }
        self._retry_config = {
            "max_attempts": max_attempts,
            "backoff_base": backoff_base,
        }

        # 加解密模块
        self.crypto: Optional[WeComCrypto] = None
        if self.token and self.encoding_aes_key and self.corp_id:
            self.crypto = WeComCrypto(self.token, self.encoding_aes_key, self.corp_id)
            logger.info("企业微信加解密模块已初始化")

        # 媒体处理模块
        self.media = WeComMedia(
            access_token_getter=self.get_access_token,
            upload_dir=media_upload_dir,
        )

        # Token 管理
        self._access_token: Optional[str] = None
        self._token_expires: int = 0
        self._token_lock = asyncio.Lock()

        # HTTP 连接池（延迟初始化）
        self._http_client: Optional[httpx.AsyncClient] = None

        # 速率限制器
        self._rate_limiter: Dict[str, deque] = {}
        self._rate_limit_enabled = rate_limit_enabled
        self._rate_limit_max = rate_limit_max

    @property
    def channel_type(self) -> str:
        return "wecom"

    async def _get_client(self) -> httpx.AsyncClient:
        """获取持久化的 HTTP 客户端（连接池）"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(
                    max_connections=100, max_keepalive_connections=20
                ),
            )
        return self._http_client

    async def close(self):
        """关闭 HTTP 连接池，供优雅关闭调用"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            logger.info("企业微信 HTTP 连接池已关闭")

    def _check_rate_limit(self, user_id: str) -> bool:
        """滑动窗口速率限制检查

        Args:
            user_id: 目标用户 ID

        Returns:
            True 表示允许通过，False 表示已超限
        """
        if not self._rate_limit_enabled:
            return True

        now = time.time()
        window = self._rate_limiter.setdefault(user_id, deque())
        # 移除 60 秒前的记录
        while window and window[0] < now - 60:
            window.popleft()
        if len(window) >= self._rate_limit_max:
            logger.warning(f"Rate limit exceeded for user {user_id}")
            return False
        window.append(now)
        return True

    # ==================== Token 管理 ====================

    async def get_access_token(self) -> str:
        """
        获取企业微信 access_token（带缓存和并发锁）

        Returns:
            access_token
        """
        # 快速路径：未过期的缓存
        if self._access_token and time.time() < self._token_expires:
            return self._access_token

        async with self._token_lock:
            # 双重检查
            if self._access_token and time.time() < self._token_expires:
                return self._access_token

            return await self._refresh_access_token()

    async def _refresh_access_token(self) -> str:
        """刷新 access_token（带重试）"""
        max_retries = self._retry_config["max_attempts"]

        for attempt in range(max_retries):
            try:
                client = await self._get_client()
                response = await client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                    params={
                        "corpid": self.corp_id,
                        "corpsecret": self.secret,
                    },
                )

                data = response.json()
                if data.get("errcode", 0) != 0:
                    raise RuntimeError(
                        f"获取 access_token 失败: errcode={data.get('errcode')}, "
                        f"errmsg={data.get('errmsg')}"
                    )

                self._access_token = data["access_token"]
                self._token_expires = time.time() + data["expires_in"] - 300

                logger.info("获取企业微信 access_token 成功")
                return self._access_token

            except Exception as e:
                if attempt < max_retries - 1:
                    wait = self._retry_config["backoff_base"] * (2**attempt)
                    logger.warning(
                        f"获取 access_token 重试 {attempt + 1}/{max_retries}，"
                        f"{wait}s 后重试: {e}"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"获取 access_token 最终失败: {e}")
                    raise

    def _invalidate_token(self):
        """强制使缓存的 token 失效（在收到 errcode=40014 时调用）"""
        self._access_token = None
        self._token_expires = 0

    # ==================== 消息解析 ====================

    async def parse_message(self, raw_message: Dict[str, Any]) -> UnifiedMessage:
        """
        解析企业微信消息

        输入格式: {"body": "<xml>...</xml>"} 或已解密的 XML 字符串

        Returns:
            UnifiedMessage 统一消息格式
        """
        xml_content = raw_message.get("body", "") or raw_message.get("xml", "")

        if isinstance(xml_content, str):
            root = ET.fromstring(xml_content)
        else:
            root = xml_content

        msg_type = root.findtext("MsgType", "text")
        from_user = root.findtext("FromUserName", "")
        create_time = int(root.findtext("CreateTime", "0"))
        msg_id = root.findtext("MsgId", f"msg_{create_time}")

        content: Dict[str, Any] = {}
        message_type = MessageType.TEXT

        if msg_type == "text":
            raw_text = root.findtext("Content", "")
            # 清洗群聊 @前缀: "@应用名称 实际消息" -> "实际消息"
            cleaned_text = re.sub(r"^@\S+\s+", "", raw_text)
            if cleaned_text != raw_text:
                logger.debug(f"清洗群聊 @前缀: '{raw_text}' -> '{cleaned_text}'")
            content["text"] = cleaned_text
        elif msg_type == "image":
            message_type = MessageType.IMAGE
            content["pic_url"] = root.findtext("PicUrl", "")
            content["media_id"] = root.findtext("MediaId", "")
        elif msg_type == "file":
            message_type = MessageType.FILE
            content["media_id"] = root.findtext("MediaId", "")
            content["file_name"] = root.findtext("FileName", "")
        elif msg_type == "voice":
            content["text"] = root.findtext("Recognition", "") or "[语音消息]"
            content["media_id"] = root.findtext("MediaId", "")
            content["voice_format"] = root.findtext("Format", "")
        elif msg_type == "video" or msg_type == "shortvideo":
            message_type = MessageType.FILE
            content["media_id"] = root.findtext("MediaId", "")
            content["thumb_media_id"] = root.findtext("ThumbMediaId", "")
        elif msg_type == "location":
            content["text"] = (
                f"[位置] {root.findtext('Label', '')} "
                f"({root.findtext('Location_X', '')}, "
                f"{root.findtext('Location_Y', '')})"
            )
        elif msg_type == "event":
            message_type = MessageType.EVENT
            content["event"] = root.findtext("Event", "")
            content["event_key"] = root.findtext("EventKey", "")
        elif msg_type == "link":
            content["text"] = (
                f"[链接] {root.findtext('Title', '')} "
                f"{root.findtext('Url', '')}"
            )
            content["title"] = root.findtext("Title", "")
            content["url"] = root.findtext("Url", "")

        return UnifiedMessage(
            message_id=msg_id,
            channel_type=self.channel_type,
            user_id=from_user,
            message_type=message_type,
            content=content,
            timestamp=datetime.fromtimestamp(create_time),
            raw_message={"xml": xml_content} if isinstance(xml_content, str) else raw_message,
        )

    # ==================== 消息发送 ====================

    async def send_message(self, message: UnifiedResponse) -> bool:
        """
        发送企业微信消息（自动选择消息类型和拆分）

        文本消息走 send_long_message，随后逐个发送 downloadable_files 为 textcard。
        """
        all_success = True

        # 1. 发送文本
        text = message.text
        if text:
            all_success = await self.send_long_message(text, message.reply_to)

        # 2. 发送可下载文件卡片
        for file_info in message.downloadable_files:
            url = build_public_url(file_info.download_url)
            success = await self.send_textcard(
                title=f"{file_info.file_name}",
                description=f"点击下载 ({format_file_size(file_info.file_size)})",
                url=url,
                user_id=message.reply_to,
                btntxt="下载",
            )
            if not success:
                all_success = False

        return all_success

    async def send_long_message(self, text: str, user_id: str) -> bool:
        """
        发送可能超长的消息（自动拆分 + 类型检测）

        Args:
            text: 消息文本
            user_id: 目标用户 ID

        Returns:
            是否全部发送成功
        """
        if not self._check_rate_limit(user_id):
            logger.warning(f"send_long_message 被速率限制拦截: user={user_id}")
            return False

        max_bytes = self._msg_config["max_bytes"]
        parts = WeComMessageBuilder.split_long_message(
            text, max_bytes, self._msg_config["split_on_paragraph"]
        )

        all_success = True
        for part in parts:
            msg_type = WeComMessageBuilder.detect_message_type(
                part, self._msg_config["default_type"]
            )
            msg_data = WeComMessageBuilder.build_msg_data(part, self.agent_id, msg_type)
            msg_data["touser"] = user_id

            success = await self._send_with_retry(msg_data)
            if not success:
                all_success = False

        return all_success

    async def send_text(self, text: str, user_id: str) -> bool:
        """发送纯文本消息"""
        msg_data = WeComMessageBuilder.build_text(text, self.agent_id)
        msg_data["touser"] = user_id
        return await self._send_with_retry(msg_data)

    async def send_markdown(self, content: str, user_id: str) -> bool:
        """发送 Markdown 消息"""
        msg_data = WeComMessageBuilder.build_markdown(content, self.agent_id)
        msg_data["touser"] = user_id
        return await self._send_with_retry(msg_data)

    async def send_image(self, media_id: str, user_id: str) -> bool:
        """发送图片消息"""
        msg_data = WeComMessageBuilder.build_image(media_id, self.agent_id)
        msg_data["touser"] = user_id
        return await self._send_with_retry(msg_data)

    async def send_file(self, media_id: str, user_id: str) -> bool:
        """发送文件消息"""
        msg_data = WeComMessageBuilder.build_file(media_id, self.agent_id)
        msg_data["touser"] = user_id
        return await self._send_with_retry(msg_data)

    async def send_textcard(
        self,
        title: str,
        description: str,
        url: str,
        user_id: str,
        btntxt: str = "详情",
    ) -> bool:
        """发送文本卡片消息"""
        msg_data = WeComMessageBuilder.build_textcard(
            title, description, url, self.agent_id, btntxt
        )
        msg_data["touser"] = user_id
        return await self._send_with_retry(msg_data)

    async def send_waiting_indicator(self, user_id: str, message: str) -> bool:
        """
        发送等待提示消息（纯文本，绕过应用层速率限制）。

        此方法故意不调用 _check_rate_limit()，因为：
        1. 等待提示是系统消息，不应计入用户消息配额
        2. 即使用户已触发速率限制，等待提示也应发出（改善 UX）
        """
        return await self.send_text(message, user_id)

    async def _send_with_retry(self, msg_data: Dict[str, Any]) -> bool:
        """
        发送消息（带重试和 token 刷新）

        Args:
            msg_data: WeCom API 消息体（不含 touser 前缀）

        Returns:
            是否成功
        """
        max_retries = self._retry_config["max_attempts"]

        for attempt in range(max_retries):
            try:
                access_token = await self.get_access_token()
                client = await self._get_client()

                url = (
                    f"https://qyapi.weixin.qq.com/cgi-bin/message/send"
                    f"?access_token={access_token}"
                )
                response = await client.post(url, json=msg_data)
                data = response.json()

                errcode = data.get("errcode", 0)

                # token 过期，刷新后重试
                if errcode in (40014, 42001):
                    logger.warning("access_token 已过期，正在刷新")
                    self._invalidate_token()
                    continue

                if errcode != 0:
                    logger.error(
                        f"发送消息失败: errcode={errcode}, "
                        f"errmsg={data.get('errmsg')}"
                    )
                    return False

                logger.info(f"企业微信消息发送成功: user={msg_data.get('touser')}, msgtype={msg_data.get('msgtype')}, content_len={len(msg_data.get('text', {}).get('content', msg_data.get('markdown', {}).get('content', '')))}")
                return True

            except Exception as e:
                if attempt < max_retries - 1:
                    wait = self._retry_config["backoff_base"] * (2**attempt)
                    logger.warning(
                        f"发送消息重试 {attempt + 1}/{max_retries}，"
                        f"{wait}s 后重试: {e}"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"发送消息最终失败: {e}")
                    return False

        return False

    # ==================== 用户信息 ====================

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """
        获取企业微信用户信息

        API: GET https://qyapi.weixin.qq.com/cgi-bin/user/get
        """
        try:
            access_token = await self.get_access_token()
            client = await self._get_client()

            response = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/user/get",
                params={
                    "access_token": access_token,
                    "userid": user_id,
                },
            )

            data = response.json()
            if data.get("errcode", 0) != 0:
                logger.error(
                    f"获取用户信息失败: errcode={data.get('errcode')}, "
                    f"errmsg={data.get('errmsg')}"
                )
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
            logger.error(f"获取用户信息异常: {e}")
            return {}

    # ==================== 签名验证 ====================

    async def verify_signature(
        self,
        signature: str,
        timestamp: str,
        nonce: str,
        body: str,
    ) -> bool:
        """
        验证企业微信消息签名

        使用 WeComCrypto 进行 4 元素签名验证（如果已配置加解密）。
        降级为 3 元素验证（无加密模式兼容）。

        Args:
            signature: 签名（msg_signature 或 signature）
            timestamp: 时间戳
            nonce: 随机数
            body: 加密消息体（echostr 或 Encrypt 值）

        Returns:
            签名是否有效
        """
        if not self.token:
            return True

        if self.crypto:
            return self.crypto.verify_signature(signature, timestamp, nonce, body)

        # 降级: 无加密模式的 3 元素验证
        items = [self.token, timestamp, nonce]
        items.sort()
        combined = "".join(items)
        calculated = hashlib.sha1(combined.encode()).hexdigest()
        return calculated == signature
