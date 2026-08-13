"""
钉钉适配器

实现钉钉消息的接收和发送，支持:
- HmacSHA256 签名验证（使用 DingTalkCrypto）
- 多种消息类型（text/markdown/image/file）
- 长消息自动拆分（三级策略：段落 → 行 → 字节）
- 媒体文件上传下载（使用 DingTalkMedia）
- HTTP 连接池和 Token 刷新锁
- 速率限制（滑动窗口）
- 单聊/群聊分流发送
- 欢迎消息

官方文档:
https://open.dingtalk.com/document/orgapp/receive-message
"""

import asyncio
import json
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from loguru import logger

from src.channels._image_text_renderer import render_text_with_image_placeholders
from src.channels.base import ChannelAdapter, build_public_url
from src.channels.dingtalk.crypto import DingTalkCrypto
from src.channels.dingtalk.media import DingTalkMedia
from src.channels.dingtalk.message_builder import DingTalkMessageBuilder
from src.models.message import ChannelType, MessageType, UnifiedMessage, UnifiedResponse


DINGTALK_API_BASE_URL = "https://api.dingtalk.com"


class DingTalkAdapter(ChannelAdapter):
    """
    钉钉适配器

    实现：
    - 消息接收和解析（JSON 格式，非 XML）
    - 消息发送（支持 text/markdown/image/file，自动拆分长消息）
    - 单聊/群聊分流发送（不同 API 端点）
    - 用户信息获取
    - 签名验证（HmacSHA256，无消息体加密）
    - 媒体文件处理
    - HTTP 连接池 + 重试 + token 刷新
    - 速率限制（每用户滑动窗口）
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        robot_code: Optional[str] = None,
        token: str = "",
        encoding_aes_key: str = "",
        *,
        welcome_message: str = "",
        max_bytes: int = 4000,
        split_on_paragraph: bool = True,
        rate_limit_window: int = 60,
        rate_limit_max: int = 10,
        media_upload_dir: str = "./storage/uploads/dingtalk",
        **_extra: Any,
    ):
        """
        初始化钉钉适配器

        Args:
            app_key: 钉钉应用 AppKey
            app_secret: 钉钉应用 AppSecret
            robot_code: 机器人编码（默认使用 app_key）
            token: 回调 Token（钉钉机器人回调签名不使用，仅为兼容 ChannelConfig 字段保留）
            encoding_aes_key: 回调 EncodingAESKey（钉钉无消息体加密，仅为兼容 ChannelConfig 字段保留）
            welcome_message: 首次会话欢迎消息
            max_bytes: 单条消息最大字节数（钉钉上限约 4000）
            split_on_paragraph: 是否按段落拆分长消息
            rate_limit_window: 速率限制窗口（秒）
            rate_limit_max: 窗口内最大消息数
            media_upload_dir: 媒体文件存储目录
            **_extra: 兼容 ChannelConfig 中可能存在的其他字段（忽略）
        """
        self.app_key = app_key
        self.app_secret = app_secret
        self.robot_code = robot_code or app_key
        self.token = token
        self.encoding_aes_key = encoding_aes_key
        self.welcome_message = welcome_message
        self._tenant_id: str = ""

        if _extra:
            logger.debug(f"[DingTalk] 忽略未使用的 ChannelConfig 字段: {list(_extra.keys())}")

        self._msg_config = {
            "max_bytes": max_bytes,
            "split_on_paragraph": split_on_paragraph,
        }

        self.crypto = DingTalkCrypto(self.app_secret)
        self.message_builder = DingTalkMessageBuilder()
        self.media = DingTalkMedia(
            access_token_getter=self.get_access_token,
            upload_dir=media_upload_dir,
        )

        self._access_token: Optional[str] = None
        self._token_expires: float = 0
        self._token_lock = asyncio.Lock()
        self._client_lock = asyncio.Lock()

        self._http_client: Optional[httpx.AsyncClient] = None

        self._rate_limiter: Dict[str, deque] = defaultdict(deque)
        self._rate_limit_window = rate_limit_window
        self._rate_limit_max = rate_limit_max

    @property
    def channel_type(self) -> str:
        return "dingtalk"

    async def set_tenant_id(self, tenant_id: str) -> None:
        """
        注入租户 ID（由 ChannelFactory 在创建 adapter 后调用）。

        设置后媒体文件将存到 `storage/tenants/{tenant_id}/conversation/`，
        遵循 `backend_dev.md` 租户附件存储规范。
        """
        self._tenant_id = tenant_id or ""
        if hasattr(self.media, "set_tenant_id"):
            self.media.set_tenant_id(self._tenant_id)

    async def _get_client(self) -> httpx.AsyncClient:
        # 快速路径：client 已存在且未关闭
        if self._http_client is not None and not self._http_client.is_closed:
            return self._http_client
        # 加锁串行化创建，避免并发请求各自创建一个 client 造成旧实例泄漏
        async with self._client_lock:
            if self._http_client is None or self._http_client.is_closed:
                self._http_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(30.0, connect=10.0),
                    limits=httpx.Limits(
                        max_connections=100, max_keepalive_connections=20
                    ),
                )
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            logger.info("[DingTalk] HTTP 连接池已关闭")

    def _check_rate_limit(self, user_id: str) -> bool:
        """滑动窗口速率限制，True 表示允许通过"""
        now = time.time()
        window = self._rate_limiter[user_id]
        while window and window[0] < now - self._rate_limit_window:
            window.popleft()
        if len(window) >= self._rate_limit_max:
            logger.warning(f"[DingTalk] 速率限制已触发: user={user_id}")
            return False
        window.append(now)
        return True

    # ==================== Token 管理 ====================

    async def get_access_token(self) -> str:
        """
        获取钉钉 access_token（带缓存和并发锁）

        API: POST /v1.0/oauth2/accessToken
        鉴权: 无（appKey + appSecret 在 body 中）

        Returns:
            access_token
        """
        if self._access_token and time.time() < self._token_expires:
            return self._access_token

        async with self._token_lock:
            if self._access_token and time.time() < self._token_expires:
                return self._access_token
            return await self._refresh_access_token()

    async def _refresh_access_token(self) -> str:
        max_retries = 3
        backoff_base = 1.0

        for attempt in range(max_retries):
            try:
                client = await self._get_client()
                response = await client.post(
                    f"{DINGTALK_API_BASE_URL}/v1.0/oauth2/accessToken",
                    json={"appKey": self.app_key, "appSecret": self.app_secret},
                )

                data = response.json()

                if response.status_code != 200 or "accessToken" not in data:
                    err_msg = (
                        data.get("message")
                        or data.get("errmsg")
                        or f"HTTP {response.status_code}"
                    )
                    raise RuntimeError(f"获取 access_token 失败: {err_msg}")

                self._access_token = data["accessToken"]
                expire_in = data.get("expireIn", 7200)
                self._token_expires = time.time() + expire_in - 300

                logger.info("[DingTalk] access_token 获取成功")
                return self._access_token

            except Exception as e:
                if attempt < max_retries - 1:
                    wait = backoff_base * (2 ** attempt)
                    logger.warning(
                        f"[DingTalk] access_token 重试 {attempt + 1}/{max_retries}，"
                        f"{wait}s 后重试: {e}"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"[DingTalk] access_token 最终失败: {e}")
                    raise

    def _invalidate_token(self):
        """强制使缓存的 token 失效（在收到 token 过期错误时调用）"""
        self._access_token = None
        self._token_expires = 0

    # ==================== 消息解析 ====================

    async def parse_message(self, raw_message: Dict[str, Any]) -> Optional[UnifiedMessage]:
        """
        解析钉钉消息（JSON 格式）

        钉钉回调消息结构:
        {
            "msgtype": "text",
            "text": {"content": "xxx"},
            "msgId": "xxx",
            "createAt": 1234567890000,
            "conversationType": "1" | "2",
            "conversationId": "xxx",
            "conversationTitle": "群名",  // 仅群聊
            "senderId": "$:LWCP_v1:$...",  // chatbot 会话用户 ID，不能直接用于发消息
            "senderStaffId": "manager81",  // 企业内员工 ID，oToMessages 接口要求
            "senderNick": "xxx",
            "senderCorpId": "xxx",
            "sessionWebhook": "xxx",
            ...
        }

        user_id 优先取 senderStaffId，缺失时回退 senderId（向后兼容）。

        Returns:
            UnifiedMessage 或 None（忽略的事件/空消息）
        """
        msg_type = raw_message.get("msgtype", "text")
        msg_id = raw_message.get("msgId", f"dingtalk_{int(time.time() * 1000)}")
        create_at = raw_message.get("createAt", int(time.time() * 1000))

        # 钉钉回调中存在两种 sender 标识：
        # - senderStaffId: 企业内员工 ID（staffId），oToMessages/batchSend 接口要求的就是它
        # - senderId: chatbot 会话用户 ID（LWCP 格式），不能直接用于发消息
        # 因此 user_id 优先取 senderStaffId，缺失时回退 senderId（保持向后兼容）
        sender_id = raw_message.get("senderStaffId") or raw_message.get("senderId", "")
        sender_nick = raw_message.get("senderNick", "")

        conversation_type = raw_message.get("conversationType", "1")
        conversation_id = raw_message.get("conversationId", "")

        msg_content: Dict[str, Any] = {
            "conversation_type": conversation_type,
            "conversation_id": conversation_id,
        }
        unified_msg_type = MessageType.TEXT

        if msg_type == "text":
            text_content = raw_message.get("text", {})
            msg_content["text"] = text_content.get("content", "").strip()

        elif msg_type == "picture":
            unified_msg_type = MessageType.IMAGE
            picture = raw_message.get("picture", {})
            msg_content["download_code"] = picture.get("downloadCode", "")

        elif msg_type == "richText":
            rich_text = raw_message.get("richText", [])
            text_parts = [item.get("text", "") for item in rich_text if "text" in item]
            msg_content["text"] = "".join(text_parts).strip()

        elif msg_type == "file":
            unified_msg_type = MessageType.FILE
            file_info = raw_message.get("file", {})
            msg_content["download_code"] = file_info.get("downloadCode", "")
            msg_content["file_name"] = file_info.get("fileName", "")

        elif msg_type in ("video", "audio"):
            unified_msg_type = MessageType.FILE
            media_info = raw_message.get(msg_type, {})
            msg_content["download_code"] = media_info.get("downloadCode", "")
            msg_content["file_name"] = media_info.get("fileName", "")

        elif msg_type == "empty":
            logger.debug("[DingTalk] 忽略空消息（可能仅 @机器人无内容）")
            return None

        else:
            msg_content["text"] = f"[{msg_type} 消息暂不支持]"

        try:
            ts_seconds = int(create_at) / 1000
            timestamp = datetime.fromtimestamp(ts_seconds)
        except (ValueError, TypeError):
            timestamp = datetime.now()

        return UnifiedMessage(
            message_id=msg_id,
            channel_type=ChannelType.DINGTALK,
            user_id=sender_id,
            user_name=sender_nick,
            message_type=unified_msg_type,
            content=msg_content,
            timestamp=timestamp,
            raw_message=raw_message,
        )

    # ==================== 消息发送 ====================

    async def send_message(self, message: UnifiedResponse) -> bool:
        """
        发送钉钉消息（自动选择消息类型和拆分）

        通过 message.content 中的 conversation_type 判断单聊/群聊：
        - conversation_type="1"（单聊）: reply_to 应为 userId
        - conversation_type="2"（群聊）: reply_to 应为 openConversationId

        发送顺序：文本（含图片占位符）→ 图片 → 可下载文件。
        单图失败不阻断后续发送，记 warning。
        """
        content = message.content or {}
        conversation_type = str(content.get("conversation_type", "1"))
        reply_to = message.reply_to

        all_success = True
        images = message.get_images()

        # 1. 发送文本（含 placement 占位符）
        text = message.text
        if text:
            text = render_text_with_image_placeholders(text, images)
            all_success = await self.send_long_message(text, reply_to, conversation_type)

        # 2. 发送图片（Phase 2 P2.9.2 新增）
        # 钉钉 sampleImageMsg 需要 photoURL（公网 URL），不能直接发本地路径。
        # 通过 build_public_url 把 ImageRef.download_url 转成公网 URL，
        # 公网不可达时 send_image 内部会失败 → 该图跳过 warning 不阻断。
        for ref in images:
            try:
                file_id = ref.get("file_id") if isinstance(ref, dict) else None
                if not file_id:
                    continue
                download_url = ref.get("download_url") or f"/api/files/{file_id}/download"
                public_url = build_public_url(download_url)
                success = await self.send_image(public_url, reply_to, conversation_type)
                if not success:
                    all_success = False
            except Exception as e:
                logger.warning(
                    f"[dingtalk] send image {ref.get('file_id') if isinstance(ref, dict) else '?'} failed: {e}"
                )
                all_success = False

        for file_info in message.downloadable_files:
            # 速率限制（与 send_long_message 一致）
            if not self._check_rate_limit(reply_to):
                logger.warning(
                    f"[DingTalk] send_message 文件分支被速率限制拦截: target={reply_to}"
                )
                all_success = False
                break

            # DownloadableFileInfo 只携带 download_url（无 local_path），
            # 先从 URL 下载到本地再上传到钉钉。
            file_name = file_info.file_name or "未命名文件"
            ext = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
            media_type = "image" if ext in ("png", "jpg", "jpeg", "gif") else "file"

            media_id = await self.media.upload_from_url(
                file_info.download_url, file_name, self.robot_code, media_type
            )
            if not media_id:
                all_success = False
                continue

            # 钉钉 sampleImageMsg 需要 photoURL（公网 URL），
            # 上传后只能拿到 mediaId，需走 sampleFile 通道。
            ok = await self.send_file(
                media_id, file_name, reply_to, conversation_type
            )
            if not ok:
                all_success = False

        return all_success

    async def send_long_message(
        self,
        text: str,
        user_id: str,
        conversation_type: str = "1",
    ) -> bool:
        """发送可能超长的消息（自动拆分 + 类型检测）"""
        if not self._check_rate_limit(user_id):
            logger.warning(f"[DingTalk] send_long_message 被速率限制拦截: user={user_id}")
            return False

        max_bytes = self._msg_config["max_bytes"]
        parts = self.message_builder.split_long_message(
            text, max_bytes, self._msg_config["split_on_paragraph"]
        )

        all_success = True
        for part in parts:
            msg_type = self.message_builder.detect_message_type(part)
            if msg_type == "markdown":
                msg_body = self.message_builder.build_markdown("消息", part)
            else:
                msg_body = self.message_builder.build_text(part)

            ok = await self._send_with_retry(msg_body, user_id, conversation_type)
            if not ok:
                all_success = False

        return all_success

    async def send_text(
        self, text: str, user_id: str, conversation_type: str = "1"
    ) -> bool:
        """发送纯文本消息"""
        msg_body = self.message_builder.build_text(text)
        return await self._send_with_retry(msg_body, user_id, conversation_type)

    async def send_image(
        self, photo_url: str, user_id: str, conversation_type: str = "1"
    ) -> bool:
        """发送图片消息（通过 photoURL）"""
        msg_body = self.message_builder.build_image(photo_url)
        return await self._send_with_retry(msg_body, user_id, conversation_type)

    async def send_file(
        self,
        media_id: str,
        file_name: str,
        user_id: str,
        conversation_type: str = "1",
    ) -> bool:
        """发送文件消息"""
        file_type = file_name.rsplit(".", 1)[-1] if "." in file_name else "file"
        msg_body = self.message_builder.build_file(media_id, file_name, file_type)
        return await self._send_with_retry(msg_body, user_id, conversation_type)

    async def send_waiting_indicator(self, user_id: str, message: str) -> bool:
        """发送等待提示（绕过应用层速率限制）"""
        return await self.send_text(message, user_id, "1")

    async def _send_with_retry(
        self,
        msg_body: Dict[str, Any],
        user_id: str,
        conversation_type: str = "1",
        max_retries: int = 3,
    ) -> bool:
        """
        发送消息（带重试和 token 刷新）

        根据 conversation_type 自动选择:
        - 单聊 ("1"): POST /v1.0/robot/oToMessages/batchSend
        - 群聊 ("2"): POST /v1.0/robot/groupMessages/send

        Args:
            msg_body: {"msgKey": "...", "msgParam": "<JSON string>"}
            user_id: 单聊时为 userId，群聊时为 openConversationId
            conversation_type: "1" 或 "2"
            max_retries: 最大重试次数
        """
        backoff_base = 1.0

        for attempt in range(max_retries):
            try:
                access_token = await self.get_access_token()
                client = await self._get_client()

                if conversation_type == "2":
                    url = f"{DINGTALK_API_BASE_URL}/v1.0/robot/groupMessages/send"
                    payload = {
                        "robotCode": self.robot_code,
                        "openConversationId": user_id,
                        "msgKey": msg_body["msgKey"],
                        "msgParam": msg_body["msgParam"],
                    }
                else:
                    url = f"{DINGTALK_API_BASE_URL}/v1.0/robot/oToMessages/batchSend"
                    payload = {
                        "robotCode": self.robot_code,
                        "userIds": [user_id],
                        "msgKey": msg_body["msgKey"],
                        "msgParam": msg_body["msgParam"],
                    }

                response = await client.post(
                    url,
                    headers={
                        "x-acs-dingtalk-access-token": access_token,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

                data = response.json()

                if response.status_code != 200 or data.get("errcode"):
                    errcode = data.get("errcode") or data.get("code")
                    errmsg = (
                        data.get("errmsg")
                        or data.get("message")
                        or data.get("msg")
                        or f"HTTP {response.status_code}"
                    )

                    # 常见 token 过期错误码: 40014, 40001, 40002
                    errmsg_lower = str(errmsg).lower()
                    if errcode in (40014, 40001, 40002) or "token" in errmsg_lower:
                        logger.warning("[DingTalk] access_token 已过期，正在刷新")
                        self._invalidate_token()
                        continue

                    logger.error(
                        f"[DingTalk] 发送消息失败: errcode={errcode}, errmsg={errmsg}, "
                        f"conversation_type={conversation_type}, target={user_id}"
                    )
                    return False

                logger.info(
                    f"[DingTalk] 消息发送成功: conversation_type={conversation_type}, "
                    f"target={user_id}, msg_key={msg_body['msgKey']}"
                )
                return True

            except Exception as e:
                if attempt < max_retries - 1:
                    wait = backoff_base * (2 ** attempt)
                    logger.warning(
                        f"[DingTalk] 发送消息重试 {attempt + 1}/{max_retries}，"
                        f"{wait}s 后重试: {e}"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"[DingTalk] 发送消息最终失败: {e}")
                    return False

        return False

    # ==================== 用户信息 ====================

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """
        获取钉钉用户信息

        API: POST /topapi/v2/user/get（旧版 oapi 接口）

        注意：user_id 应为 senderStaffId（员工 ID），与该接口的 userid 参数一致。
        若误传 senderId（LWCP 格式会话用户 ID），接口会返回 user.notExist。
        """
        try:
            access_token = await self.get_access_token()
            client = await self._get_client()

            response = await client.post(
                "https://oapi.dingtalk.com/topapi/v2/user/get",
                params={"access_token": access_token},
                json={"userid": user_id, "language": "zh_CN"},
            )

            data = response.json()
            errcode = data.get("errcode", 0)
            if errcode != 0:
                logger.error(
                    f"[DingTalk] 获取用户信息失败: errcode={errcode}, "
                    f"errmsg={data.get('errmsg')}"
                )
                return {}

            result = data.get("result", {})

            return {
                "user_id": result.get("userid", ""),
                "name": result.get("name", ""),
                "department": result.get("dept_id_list", []),
                "position": result.get("title", ""),
                "mobile": result.get("mobile", ""),
                "email": result.get("email", ""),
                "avatar": result.get("avatar", ""),
            }

        except Exception as e:
            logger.error(f"[DingTalk] 获取用户信息异常: {e}")
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
        验证钉钉消息签名

        签名算法：sign = base64(HmacSHA256(timestamp + "\\n" + appSecret, appSecret))

        注意：钉钉签名验证不需要 nonce 和 body 参数，
        这两个参数是为了保持与 ChannelAdapter 接口兼容而保留。

        Args:
            signature: 请求头中的 sign（Base64 编码的签名）
            timestamp: 请求头中的 timestamp（毫秒时间戳）
            nonce: 未使用（接口兼容）
            body: 未使用（接口兼容）

        Returns:
            签名和时间戳是否都有效
        """
        if not self.crypto.verify_signature(timestamp, signature):
            return False

        if not self.crypto.check_timestamp(timestamp):
            return False

        return True
