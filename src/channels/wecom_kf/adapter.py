"""企业微信客服适配器

企业微信客服（微信客服）适配器

通过微信客服 API 实现 AI Agent 与外部微信用户的对话。

核心流程：
1. 企业微信回调 → WeComKfCallback → 创建 adapter
2. adapter 通过 sync_msg 拉取消息 → 转为 UnifiedMessage
3. Agent 处理 → 生成回复 → 通过 send_msg API 推送
4. 支持人工转接、欢迎语、多客服账号
"""
import asyncio
import hashlib
import os
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from src.channels.base import ChannelAdapter, build_public_url, format_file_size
from src.channels.wecom.crypto import WeComCrypto
from src.channels.wecom.message_builder import WeComMessageBuilder
from src.channels.wecom_kf.api_client import WeComKfApiClient
from src.channels.wecom_kf.cursor import CursorManager
from src.channels.wecom_kf.message import (
    contains_table_or_image,
    markdown_to_plain_text,
    parse_kf_message,
    segment_markdown,
    table_to_plain_text,
)
from src.channels.wecom_kf.renderer import WeComKfRenderer
from src.core.redis_client import redis_client
from src.core.storage import ensure_tenant_storage_dir
from src.models.message import MessageType, UnifiedMessage, UnifiedResponse



class WeComKfAdapter(ChannelAdapter):
    """微信客服适配器"""

    def __init__(
        self,
        corp_id: str,
        secret: str,
        token: str = "",
        encoding_aes_key: str = "",
        kf_account: Optional[List[Dict[str, Any]]] = None,
        *,
        max_bytes: int = 2048,
        media_upload_dir: str = "./storage/uploads/wecom_kf",
        **kwargs,
    ):
        self.corp_id = corp_id
        self.secret = secret
        self.token = token
        self.encoding_aes_key = encoding_aes_key
        self._max_bytes = max_bytes

        # 客服账号配置列表
        self.kf_accounts: List[Dict[str, Any]] = kf_account or []

        # 加解密模块（复用现有 WeComCrypto）
        self.crypto: Optional[WeComCrypto] = None
        if token and encoding_aes_key and corp_id:
            self.crypto = WeComCrypto(token, encoding_aes_key, corp_id)
            logger.info("微信客服加解密模块已初始化")

        # API 客户端
        self.api_client = WeComKfApiClient(corp_id, secret)

        # 游标管理器
        self.cursor_manager = CursorManager(redis_client)

        # HTTP 连接池（延迟初始化，供 adapter 自身关闭使用）
        self._http_client: Optional[httpx.AsyncClient] = None

        # 当前回调上下文中的客服账号 ID（由回调 handler 设置）
        self.current_open_kfid: str = ""

        # 表格渲染器（懒加载）
        self._renderer: Optional[WeComKfRenderer] = None
        self._render_enabled: bool = kwargs.get("render_tables", True)
        self._media_upload_dir: str = media_upload_dir
        self._tenant_id: str = ""

    @property
    def channel_type(self) -> str:
        return "wecom_kf"

    async def set_tenant_id(self, tenant_id: str) -> None:
        """
        注入租户 ID（由 ChannelFactory 在创建 adapter 后调用）。

        设置后媒体文件将存到 `storage/tenants/{tenant_id}/conversation/`，
        遵循 `backend_dev.md` 租户附件存储规范。
        """
        self._tenant_id = tenant_id or ""
        # 渲染器懒加载；若已创建则同步 tenant_id，未创建时创建时传入
        if self._renderer is not None and hasattr(self._renderer, "set_tenant_id"):
            self._renderer.set_tenant_id(self._tenant_id)

    def _resolve_media_dir(self) -> str:
        """解析媒体文件存储目录：有租户走 tenants 规范，无租户回退旧路径。"""
        if self._tenant_id:
            return ensure_tenant_storage_dir(self._tenant_id, "conversation")
        os.makedirs(self._media_upload_dir, exist_ok=True)
        return self._media_upload_dir

    # ==================== 默认缩略图 ====================

    def _generate_default_thumb(self) -> bytes:
        """生成 64x64 PNG 缩略图（纯色带文字），纯代码生成无外部依赖。"""
        size = 64
        # 最小 PNG: IHDR+IDAT+IEND
        def chunk(chunk_type: bytes, data: bytes) -> bytes:
            c = chunk_type + data
            crc = self._crc32(c)
            return (len(data)).to_bytes(4, "big") + c + crc.to_bytes(4, "big")

        # IHDR
        ihdr = (
            size.to_bytes(4, "big") + size.to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00"  # 8-bit RGB
        )
        # IDAT: 64x64 蓝色像素 (zlib 压缩)
        raw = b""
        for y in range(size):
            raw += b"\x00"  # filter none
            for x in range(size):
                # 中心区域白色方块
                if 16 <= x < 48 and 16 <= y < 48:
                    raw += b"\xff\xff\xff"
                else:
                    raw += b"\x1a\x6d\xff"  # 蓝色
        import zlib
        compressed = zlib.compress(raw)

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", compressed)
            + chunk(b"IEND", b"")
        )

    @staticmethod
    def _crc32(data: bytes) -> int:
        """CRC32 计算（纯 Python，无 zlib.crc32 兼容性问题）。"""
        crc = 0xFFFFFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xEDB88320
                else:
                    crc >>= 1
        return crc ^ 0xFFFFFFFF

    async def _get_default_thumb_media_id(self) -> str:
        """获取默认缩略图的 media_id，带缓存（1小时内有效）。

        缓存键带企业维度（corp_id）：media_id 是企业级素材，若多企业微信客服
        共用一个键，会互相读到对方企业的 media_id，发送时报 40007 invalid media_id。
        """
        cache_key = redis_client.make_key("wecom_kf", f"default_thumb_media_id:{self.corp_id}")
        cached = redis_client.get(cache_key)
        if cached:
            return cached

        png_data = self._generate_default_thumb()
        thumb_path = os.path.join(self._resolve_media_dir(), "_default_thumb.png")
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        with open(thumb_path, "wb") as f:
            f.write(png_data)

        result = await self.api_client.upload_media(thumb_path, "image")
        media_id = result.get("media_id", "")
        if media_id:
            redis_client.set(cache_key, media_id, ex=3500)
            logger.info(f"默认缩略图已上传并缓存: media_id={media_id[:20]}...")
        else:
            logger.warning(f"默认缩略图上传失败: {result.get('errmsg')}")
        return media_id

    # ==================== 客服配置查询 ====================

    def get_kf_config(self, open_kfid: str) -> Optional[Dict[str, Any]]:
        """根据 open_kfid 查找对应的客服账号配置"""
        for kf in self.kf_accounts:
            if kf.get("open_kfid") == open_kfid:
                return kf
        return None

    def should_transfer_to_human(self, text: str, kf_config: Dict[str, Any]) -> bool:
        """【已废弃】判断消息是否匹配人工转接关键词。

        新设计：转人工完全由 Agent 通过 transfer_to_human 工具调用处理，
        不再在回调路径进行关键词拦截。此函数保留仅用于兼容旧配置。
        """
        return False

    def should_exit_human(self, text: str, kf_config: Dict[str, Any]) -> bool:
        """判断消息是否匹配退出人工关键词。
        未配置关键词时默认禁用退出人工；设为空数组 [] 时同样禁用；配置了关键词才启用。
        """
        if not text:
            return False
        keywords = kf_config.get("exit_human_keywords")
        if not keywords:
            return False
        return any(kw in text for kw in keywords)

    # ==================== 消息解析 ====================

    async def parse_message(self, raw_message: Dict[str, Any]) -> UnifiedMessage:
        """
        解析微信客服消息。

        输入为 sync_msg 返回的 msg_list 中的一条消息 dict。
        """
        return parse_kf_message(raw_message)

    @property
    def renderer(self) -> WeComKfRenderer:
        if self._renderer is None:
            self._renderer = WeComKfRenderer(
                upload_dir=self._media_upload_dir,
                tenant_id=self._tenant_id,
            )
        return self._renderer

    # ==================== 消息发送 ====================

    async def send_message(self, message: UnifiedResponse) -> bool:
        """
        发送统一响应消息。

        流程：markdown -> 优先整段长图 -> 逐块选择最优方式发送
          - 若 text 含 md 表格 或 图片引用：整段 md 渲染为单张长图，以 image 消息发送
            （规避 wecom_kf 单次咨询 5 次回复限制；失败降级为分段逻辑）
          - 否则：segment_markdown 分段
            - text 块 -> 增强纯文本 -> 拆分 -> text 消息
            - table 块 -> 渲染图片 -> 上传 -> image 消息（降级为纯文本）
            - link 块 -> link 消息（降级为纯文本 URL）
        随后逐个发送 downloadable_files 为 link 消息。
        """
        all_success = True
        text = message.text

        if text:
            # 如果渲染功能关闭，走原有的纯文本全流程
            if not self._render_enabled:
                all_success = await self._send_as_plain_text(text, message.reply_to)
            elif contains_table_or_image(text):
                # 含表格或图片：优先整段渲染为长图，一次性发送
                sent = await self._send_full_text_as_image(text, message.reply_to)
                if not sent:
                    # 长图渲染/发送失败，降级走分段逻辑
                    all_success = await self._send_segmented(text, message.reply_to)
                else:
                    all_success = True
            else:
                all_success = await self._send_segmented(text, message.reply_to)

        # 发送可下载文件链接
        thumb_media_id = ""
        if message.downloadable_files:
            thumb_media_id = await self._get_default_thumb_media_id()
        for file_info in message.downloadable_files:
            # 图片文件优先作为 image 消息直接发送（用户在微信侧直接看到图片）
            # 失败/不满足前置条件时降级为 link 卡片或纯文本链接
            mime_type = (file_info.mime_type or "").lower()
            if mime_type.startswith("image/"):
                handled, success = await self._send_image_file_as_image(file_info, message.reply_to)
                if handled:
                    if not success:
                        all_success = False
                    continue

            url = build_public_url(file_info.download_url)
            # 无缩略图时降级为纯文本链接
            if not thumb_media_id:
                fallback = f"{file_info.file_name}: {url}"
                success = await self._send_text_block(fallback, message.reply_to)
                if not success:
                    all_success = False
                continue
            content = {
                "title": file_info.file_name,
                "desc": f"点击下载 ({format_file_size(file_info.file_size)})",
                "url": url,
                "thumb_media_id": thumb_media_id,
            }
            result = await self.api_client.send_msg(
                touser=message.reply_to,
                open_kfid=self.current_open_kfid,
                msgtype="link",
                content=content,
            )
            if result.get("errcode", 0) != 0:
                all_success = False

        return all_success

    async def _send_segmented(self, text: str, user_id: str) -> bool:
        """按 segment_markdown 分段逐块发送（text/table/link 三种块类型）。"""
        all_success = True
        blocks = segment_markdown(text)
        for block in blocks:
            if block.type == "text":
                success = await self._send_text_block(block.content, user_id)
            elif block.type == "table":
                success = await self._send_table_as_image(block.content, user_id)
            elif block.type == "link":
                success = await self._send_link_message(block, user_id)
            else:
                success = True
            if not success:
                all_success = False
        return all_success

    async def _send_full_text_as_image(self, markdown_text: str, user_id: str) -> bool:
        """将整段 markdown 渲染为长图并以单个 image 消息发送。

        用于含表格或图片的回复，规避 wecom_kf 单次咨询 5 次回复限制。
        任何环节失败返回 False，由调用方降级走分段逻辑。

        Args:
            markdown_text: 原始 markdown 文本
            user_id: 接收用户 ID

        Returns:
            True 如果长图渲染并发送成功，False 否则
        """
        try:
            image_path = await self.renderer.render_markdown(markdown_text)
            if not image_path or not os.path.exists(image_path):
                logger.warning("整段 markdown 长图渲染失败，降级走分段逻辑")
                return False

            upload_result = await self.api_client.upload_media(image_path, "image")
            media_id = upload_result.get("media_id")
            if not media_id:
                logger.warning(
                    f"长图上传素材未返回 media_id，降级走分段逻辑: {upload_result.get('errmsg')}"
                )
                return False

            send_result = await self.api_client.send_msg(
                touser=user_id,
                open_kfid=self.current_open_kfid,
                msgtype="image",
                content={"media_id": media_id},
            )
            if send_result.get("errcode", 0) == 0:
                logger.info("整段 markdown 长图已发送")
                return True
            logger.warning(
                f"长图 image 消息发送失败 errcode={send_result.get('errcode')} "
                f"errmsg={send_result.get('errmsg')}，降级走分段逻辑"
            )
            return False
        except Exception as e:
            logger.opt(exception=True).warning(
                f"整段 markdown 长图渲染/发送异常，降级走分段逻辑: {e}",
            )
            return False

    async def _send_as_plain_text(self, text: str, user_id: str) -> bool:
        """纯文本全流程（禁用渲染时的降级路径）。"""
        plain_text = markdown_to_plain_text(text)
        parts = self._split_message(plain_text)
        all_success = True
        for part in parts:
            result = await self.api_client.send_msg(
                touser=user_id,
                open_kfid=self.current_open_kfid,
                msgtype="text",
                content={"content": part},
            )
            if result.get("errcode", 0) != 0:
                all_success = False
        return all_success

    async def _send_text_block(self, text_content: str, user_id: str) -> bool:
        """发送文本块：增强纯文本 → 拆分 → text 消息。"""
        plain = markdown_to_plain_text(text_content)
        if not plain.strip():
            return True
        parts = self._split_message(plain)
        all_success = True
        for part in parts:
            result = await self.api_client.send_msg(
                touser=user_id,
                open_kfid=self.current_open_kfid,
                msgtype="text",
                content={"content": part},
            )
            if result.get("errcode", 0) != 0:
                all_success = False
        return all_success

    async def _send_table_as_image(self, markdown_table: str, user_id: str) -> bool:
        """将表格渲染为图片并发送，失败时降级为纯文本。"""
        try:
            image_path = await self.renderer.render_table(markdown_table)
            if image_path and os.path.exists(image_path):
                upload_result = await self.api_client.upload_media(image_path, "image")
                media_id = upload_result.get("media_id")
                if media_id:
                    send_result = await self.api_client.send_msg(
                        touser=user_id,
                        open_kfid=self.current_open_kfid,
                        msgtype="image",
                        content={"media_id": media_id},
                    )
                    if send_result.get("errcode", 0) == 0:
                        return True
                    logger.warning(f"表格图片发送失败: {send_result.get('errmsg')}")
        except Exception as e:
            logger.warning(f"表格渲染/上传失败，降级为纯文本: {e}")

        # 降级：纯文本表格
        fallback_text = table_to_plain_text(markdown_table)
        return await self._send_text_block(fallback_text, user_id)

    async def _send_image_file_as_image(self, file_info, user_id: str) -> tuple[bool, bool]:
        """将图片文件作为企微 image 消息直接发送，失败时降级（返回 handled=False）。

        企微客服 image 消息只接受 media_id（先上传临时素材换取），不接受 URL/base64。
        本方法从 Redis 读取 file_id 对应的本地路径，上传后以 image 消息发送，让用户
        在微信侧直接看到图片，而不是点击下载链接。

        Returns:
            (handled, success)
            - (True, True): 已成功以 image 消息发送
            - (True, False): 已尝试但发送失败（已记录日志）
            - (False, False): 未处理（不满足前置条件），调用方应走原 link/纯文本逻辑
        """
        # 企微临时素材 image 限制 2MB
        if file_info.file_size > 2 * 1024 * 1024:
            return (False, False)

        file_id = file_info.file_id
        if not file_id:
            return (False, False)

        key = redis_client.make_key("uploaded_file", file_id)
        file_meta = redis_client.hgetall(key)
        if not file_meta:
            return (False, False)

        file_path = file_meta.get("path")
        if not file_path or not os.path.exists(file_path):
            return (False, False)

        try:
            upload_result = await self.api_client.upload_media(file_path, "image")
            media_id = upload_result.get("media_id")
            if not media_id:
                logger.warning(
                    f"图片文件上传素材未返回 media_id，降级为 link: file_id={file_id}"
                )
                return (False, False)

            send_result = await self.api_client.send_msg(
                touser=user_id,
                open_kfid=self.current_open_kfid,
                msgtype="image",
                content={"media_id": media_id},
            )
            if send_result.get("errcode", 0) == 0:
                return (True, True)
            logger.warning(
                f"图片文件 image 消息发送失败 errcode={send_result.get('errcode')}, "
                f"降级为 link: file_id={file_id}"
            )
            return (False, False)
        except Exception as e:
            logger.warning(
                f"图片文件 image 消息发送异常，降级为 link: file_id={file_id}, err={e}"
            )
            return (False, False)

    async def _send_link_message(self, block, user_id: str) -> bool:
        """发送 link 消息卡片，失败时降级为纯文本 URL。"""
        url = block.meta.get("url", block.content)
        title = block.meta.get("title", url)
        thumb_media_id = await self._get_default_thumb_media_id()
        # 无缩略图时直接降级为纯文本
        if not thumb_media_id:
            fallback_text = f"{title}: {url}"
            return await self._send_text_block(fallback_text, user_id)
        try:
            result = await self.api_client.send_msg(
                touser=user_id,
                open_kfid=self.current_open_kfid,
                msgtype="link",
                content={
                    "title": title,
                    "url": url,
                    "thumb_media_id": thumb_media_id,
                },
            )
            if result.get("errcode", 0) == 0:
                return True
            logger.warning(f"link 消息发送失败: {result.get('errmsg')}")
        except Exception as e:
            logger.warning(f"link 消息发送异常，降级为纯文本: {e}")

        # 降级：纯文本 URL
        fallback_text = f"{title}: {url}"
        return await self._send_text_block(fallback_text, user_id)

    async def send_welcome_message(self, code: str, welcome_text: str) -> bool:
        """
        发送欢迎语（enter_session 事件中使用）。

        Args:
            code: enter_session 回调中的 Code 字段，用于 send_msg_on_event
            welcome_text: 欢迎语文本
        """
        result = await self.api_client.send_welcome(
            code=code,
            msgtype="text",
            content={"content": welcome_text},
        )
        return result.get("errcode", 0) == 0

    async def send_text(self, text: str, user_id: str) -> bool:
        """发送纯文本消息（供回调流程直接调用）"""
        result = await self.api_client.send_msg(
            touser=user_id,
            open_kfid=self.current_open_kfid,
            msgtype="text",
            content={"content": text},
        )
        return result.get("errcode", 0) == 0

    async def send_waiting_indicator(self, user_id: str, message: str) -> bool:
        """发送等待提示消息"""
        return await self.send_text(message, user_id)

    async def send_long_message(self, text: str, user_id: str) -> bool:
        """发送长消息（自动拆分）"""
        plain_text = markdown_to_plain_text(text)
        parts = self._split_message(plain_text)
        all_success = True
        for part in parts:
            result = await self.api_client.send_msg(
                touser=user_id,
                open_kfid=self.current_open_kfid,
                msgtype="text",
                content={"content": part},
            )
            if result.get("errcode", 0) != 0:
                all_success = False
        return all_success

    # ==================== 人工转接 ====================

    async def transfer_to_human(
        self, open_kfid: str, external_userid: str, servicer_userid: str
    ) -> bool:
        """将会话转接给人工客服"""
        result = await self.api_client.trans_service_state(
            open_kfid=open_kfid,
            external_userid=external_userid,
            service_state=3,  # 人工接待
            servicer_userid=servicer_userid,
        )
        return result.get("errcode", 0) == 0

    async def end_human_service(self, open_kfid: str, external_userid: str) -> bool:
        """结束人工服务会话（state=3 → state=4）

        根据企业微信状态图，人工接待(3)只能通过API转到结束会话(4)，
        无法直接切回智能助手接待(1)。结束会话后用户新发消息会自动
        进入新会话并走智能助手接待流程。
        """
        result = await self.api_client.trans_service_state(
            open_kfid=open_kfid,
            external_userid=external_userid,
            service_state=4,  # 结束会话
        )
        if result.get("errcode", 0) == 0:
            return True

        # errcode=95016: not allow transition state
        if result.get("errcode") == 95016:
            state_result = await self.api_client.get_service_state(open_kfid, external_userid)
            actual_state = state_result.get("service_state")
            if actual_state == 4:
                logger.info(
                    f"微信客服会话已结束: "
                    f"open_kfid={open_kfid}, external_userid={external_userid}"
                )
                return True
            logger.warning(
                f"微信客服会话无法结束: "
                f"open_kfid={open_kfid}, external_userid={external_userid}, "
                f"actual_state={actual_state}"
            )

        return False

    # ==================== 用户信息 ====================

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """获取外部微信用户信息（需要 open_kfid 上下文）"""
        if not self.current_open_kfid:
            return {}
        result = await self.api_client.get_customer_info(
            external_userid=user_id,
        )
        if result.get("errcode", 0) != 0:
            return {}
        customers = result.get("customer_list") or []
        if not customers:
            return {}
        customer = customers[0]
        return {
            "user_id": customer.get("external_userid", ""),
            "name": customer.get("nickname", ""),
            "avatar": customer.get("avatar", ""),
            "gender": customer.get("gender", 0),
            "wx_unionid": customer.get("unionid", ""),
        }

    # ==================== 签名验证 ====================

    async def verify_signature(
        self, signature: str, timestamp: str, nonce: str, body: str
    ) -> bool:
        """验证回调签名"""
        if not self.token:
            return True
        if self.crypto:
            return self.crypto.verify_signature(signature, timestamp, nonce, body)
        # 降级为 3 元素验证
        items = [self.token, timestamp, nonce]
        items.sort()
        combined = "".join(items)
        calculated = hashlib.sha1(combined.encode()).hexdigest()
        return calculated == signature

    # ==================== 内部方法 ====================

    def _split_message(self, text: str) -> List[str]:
        """拆分长消息（复用 WeComMessageBuilder）"""
        return WeComMessageBuilder.split_long_message(text, self._max_bytes)

    async def close(self):
        """关闭 HTTP 连接池"""
        if self._renderer:
            await self._renderer.close()
        await self.api_client.close()
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
