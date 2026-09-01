"""
飞书适配器

实现飞书消息的接收和发送，支持:
- AES-256-CBC 消息加解密（使用 FeishuCrypto）
- 多种消息类型（text/post/interactive/image/file）
- 长消息自动拆分（三级策略：段落 → 行 → 字节）
- 媒体文件上传下载（使用 FeishuMedia）
- HTTP 连接池
- Token 刷新锁和重试逻辑
- 速率限制（滑动窗口）
- 群聊 @机器人处理
- 欢迎消息
"""

import asyncio
import json
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from loguru import logger

from src.channels._image_text_renderer import render_text_with_image_placeholders
from src.channels.base import (
    ChannelAdapter,
    StatusDeliveryResult,
    build_public_url,
    format_file_size,
)
from src.channels.feishu.crypto import FeishuCrypto
from src.channels.feishu.media import FeishuMedia
from src.channels.feishu.message_builder import FeishuMessageBuilder
from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client
from src.models.message import ChannelType, MessageType, UnifiedMessage, UnifiedResponse

# 原子预留限流 Lua（Phase 3，设计 §9.3）：单次往返内完成
# 「清窗口 → 查余额 → 校验 reserve_for_final → 扣减」，杜绝先查后发的 TOCTOU。
# KEYS[1]=限流 ZSET key；ARGV: 1=window_start 2=now 3=reserve 4=max 5=ttl 6=member
_RESERVE_RATE_LIMIT_LUA = """
local key = KEYS[1]
local window_start = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local reserve = tonumber(ARGV[3])
local max_n = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
local count = redis.call('ZCARD', key)
if count + 1 + reserve > max_n then
    return 0
end
redis.call('ZADD', key, now, ARGV[6])
redis.call('EXPIRE', key, ttl)
return 1
"""


FEISHU_BASE_URL = "https://open.feishu.cn"


class FeishuAdapter(ChannelAdapter):
    """
    飞书适配器

    实现：
    - 消息接收和解析（支持 v2.0 事件结构）
    - 消息发送（支持 text/post/image/file，自动拆分长消息）
    - 用户信息获取
    - 签名验证（SHA256）
    - 媒体文件处理
    - HTTP 连接池和重试逻辑
    - 速率限制
    - 群聊 @机器人处理
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        verification_token: str = "",
        encrypt_key: str = "",
        *,
        welcome_message: str = "",
        max_bytes: int = 4000,
        split_on_paragraph: bool = True,
        rate_limit_window: int = 60,
        rate_limit_max: int = 10,
        media_upload_dir: str = "./storage/uploads/feishu",
    ):
        """
        初始化飞书适配器

        Args:
            app_id: 飞书应用 ID
            app_secret: 飞书应用 Secret
            verification_token: 回调验证 Token
            encrypt_key: 加密密钥
            welcome_message: 首次会话欢迎消息
            max_bytes: 单条消息最大字节数（默认 4000）
            split_on_paragraph: 是否按段落拆分长消息
            rate_limit_window: 速率限制窗口（秒）
            rate_limit_max: 窗口内最大消息数
            media_upload_dir: 媒体文件存储目录
        """
        self.app_id = app_id
        self.app_secret = app_secret
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key
        self.welcome_message = welcome_message
        self._tenant_id: str = ""

        # 消息配置
        self._msg_config = {
            "max_bytes": max_bytes,
            "split_on_paragraph": split_on_paragraph,
        }

        # 加解密模块（仅当 encrypt_key 非空时启用）
        self.crypto: Optional[FeishuCrypto] = None
        if self.encrypt_key:
            self.crypto = FeishuCrypto(self.verification_token, self.encrypt_key)
            logger.info("飞书加解密模块已初始化")

        # 消息构建器
        self.message_builder = FeishuMessageBuilder()

        # 媒体处理模块
        self.media = FeishuMedia(
            access_token_getter=self.get_access_token,
            upload_dir=media_upload_dir,
        )

        # Token 管理
        self._access_token: Optional[str] = None
        self._token_expires: int = 0
        self._token_lock = asyncio.Lock()

        # HTTP 连接池（延迟初始化）
        self._http_client: Optional[httpx.AsyncClient] = None

        # 机器人 open_id（懒加载，用于群聊 @判断）
        self._bot_open_id: Optional[str] = None
        self._bot_open_id_lock = asyncio.Lock()
        # bot open_id 获取失败时的下次重试时间，避免每条群聊消息都打飞书接口
        self._bot_open_id_retry_after: float = 0.0
        # bot open_id 获取失败后的退避秒数（指数退避，最多 10 分钟）
        self._bot_open_id_backoff = 30.0

        # 速率限制器（每 user 滑动窗口）
        self._rate_limiter: Dict[str, deque] = defaultdict(deque)
        self._rate_limit_window = rate_limit_window
        self._rate_limit_max = rate_limit_max

    @property
    def channel_type(self) -> str:
        """获取渠道类型"""
        return "feishu"

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
        """获取持久化的 HTTP 客户端（连接池）"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return self._http_client

    async def close(self):
        """关闭 HTTP 连接池，供优雅关闭调用"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            logger.info("飞书 HTTP 连接池已关闭")

    def _check_rate_limit(self, user_id: str) -> bool:
        """滑动窗口速率限制检查（基于 Redis ZSET，多 worker 共享）

        Redis 不可用时降级到进程内 deque（仅单 worker 内有效）。

        Args:
            user_id: 目标用户 ID

        Returns:
            True 表示允许通过，False 表示已超限
        """
        now = time.time()
        window_start = now - self._rate_limit_window

        # 优先使用 Redis（多 worker 共享）
        if redis_client._connected and redis_client._client:
            try:
                key = redis_client.make_key(
                    CacheKeys.CHANNEL_RATE_LIMIT, f"feishu:{user_id}"
                )
                client = redis_client._client
                pipe = client.pipeline()
                pipe.zremrangebyscore(key, 0, window_start)
                pipe.zadd(key, {str(now): now})
                pipe.zcard(key)
                pipe.expire(key, self._rate_limit_window + 10)
                _, _, count, _ = pipe.execute()
                if count > self._rate_limit_max:
                    logger.warning(f"Rate limit exceeded for user {user_id}")
                    return False
                return True
            except Exception as e:
                logger.warning(
                    f"[Feishu] Redis 速率限制降级到内存: user={user_id}, error={e}"
                )

        # 内存降级（单 worker 内有效）
        window = self._rate_limiter[user_id]
        while window and window[0] < window_start:
            window.popleft()
        if len(window) >= self._rate_limit_max:
            logger.warning(f"Rate limit exceeded for user {user_id}")
            return False
        window.append(now)
        return True

    def _reserve_rate_limit(self, user_id: str, reserve_for_final: int = 1) -> bool:
        """低优先级 status 消息的原子预留限流（Phase 3，设计 §9.3）。

        Redis 路径：Lua 脚本一次往返原子完成「校验预留 + 扣减」；Redis 不可用时
        降级进程内 deque（同步临界区，无 await）。额度不足返回 False（抑制
        verbose、不占额度），final 仍可经 _check_rate_limit 正常发送。
        """
        reserve = max(0, int(reserve_for_final))
        now = time.time()
        window_start = now - self._rate_limit_window

        # 优先使用 Redis（多 worker 共享，Lua 原子预留）
        if redis_client._connected and redis_client._client:
            try:
                key = redis_client.make_key(
                    CacheKeys.CHANNEL_RATE_LIMIT, f"feishu:{user_id}"
                )
                client = redis_client._client
                member = f"{now}:{uuid.uuid4().hex[:8]}"
                allowed = client.eval(
                    _RESERVE_RATE_LIMIT_LUA, 1, key,
                    window_start, now, reserve,
                    self._rate_limit_max, int(self._rate_limit_window) + 10,
                    member,
                )
                if int(allowed) == 1:
                    return True
                logger.warning(
                    f"[Feishu] status 消息预留限流抑制（Redis）: user={user_id}, "
                    f"reserve={reserve}"
                )
                return False
            except Exception as e:
                logger.warning(
                    f"[Feishu] Redis 预留限流降级到内存: user={user_id}, error={e}"
                )

        # 内存降级（单 worker 内有效，同步临界区）
        window = self._rate_limiter[user_id]
        while window and window[0] < window_start:
            window.popleft()
        remaining = self._rate_limit_max - len(window)
        if remaining < 1 + reserve:
            logger.warning(
                f"[Feishu] status 消息预留限流抑制: user={user_id}, "
                f"remaining={remaining}, need={1 + reserve}"
            )
            return False
        window.append(now)
        return True

    # ==================== Token 管理 ====================

    async def get_access_token(self) -> str:
        """
        获取飞书 tenant_access_token（带缓存和并发锁）

        Returns:
            tenant_access_token
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
        """刷新 tenant_access_token（带重试）"""
        max_retries = 3
        backoff_base = 1.0

        for attempt in range(max_retries):
            try:
                client = await self._get_client()
                response = await client.post(
                    f"{FEISHU_BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
                    json={
                        "app_id": self.app_id,
                        "app_secret": self.app_secret,
                    },
                )

                data = response.json()
                code = data.get("code", -1)
                if code != 0:
                    raise RuntimeError(
                        f"获取 tenant_access_token 失败: code={code}, msg={data.get('msg')}"
                    )

                self._access_token = data["tenant_access_token"]
                expire = data.get("expire", 7200)
                self._token_expires = time.time() + expire - 300

                logger.info("获取飞书 tenant_access_token 成功")
                return self._access_token

            except Exception as e:
                if attempt < max_retries - 1:
                    wait = backoff_base * (2 ** attempt)
                    logger.warning(
                        f"获取 tenant_access_token 重试 {attempt + 1}/{max_retries}，"
                        f"{wait}s 后重试: {e}"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"获取 tenant_access_token 最终失败: {e}")
                    raise

    def _invalidate_token(self):
        """强制使缓存的 token 失效（在收到 token 过期错误时调用）"""
        self._access_token = None
        self._token_expires = 0

    # ==================== 机器人信息 ====================

    async def _init_bot_open_id(self) -> None:
        """
        获取机器人自身 open_id（懒加载，用于群聊 @判断）

        调用 GET /open-apis/bot/v3/info，结果缓存到 self._bot_open_id。
        失败时通过指数退避避免每条群聊消息都打飞书接口。
        """
        if self._bot_open_id:
            return

        # 失败退避期内不重试
        now = time.time()
        if now < self._bot_open_id_retry_after:
            return

        async with self._bot_open_id_lock:
            # 双重检查
            if self._bot_open_id:
                return
            if time.time() < self._bot_open_id_retry_after:
                return

            try:
                access_token = await self.get_access_token()
                client = await self._get_client()
                response = await client.get(
                    f"{FEISHU_BASE_URL}/open-apis/bot/v3/info",
                    headers={"Authorization": f"Bearer {access_token}"},
                )

                data = response.json()
                if data.get("code", 0) != 0:
                    logger.error(f"获取机器人信息失败: {data.get('msg')}")
                    self._schedule_bot_open_id_retry()
                    return

                self._bot_open_id = data.get("bot", {}).get("open_id")
                # 成功后重置退避
                self._bot_open_id_backoff = 30.0
                logger.info(f"飞书机器人 open_id: {self._bot_open_id}")

            except Exception as e:
                logger.error(f"获取机器人信息异常: {e}")
                self._schedule_bot_open_id_retry()

    def _schedule_bot_open_id_retry(self) -> None:
        """安排下一次 bot open_id 重试时间（指数退避，上限 600s）"""
        backoff = min(self._bot_open_id_backoff, 600.0)
        self._bot_open_id_retry_after = time.time() + backoff
        self._bot_open_id_backoff = min(backoff * 2, 600.0)

    # ==================== 消息解析 ====================

    async def parse_message(self, raw_message: Dict[str, Any]) -> Optional[UnifiedMessage]:
        """
        解析飞书 v2.0 事件消息

        输入格式: {"header": {...}, "event": {...}}

        Returns:
            UnifiedMessage 统一消息格式，或 None（群聊未 @机器人时）
        """
        # 解密消息（如果启用了加密）
        if self.crypto and raw_message.get("encrypt"):
            try:
                raw_message = self.crypto.decrypt(raw_message["encrypt"])
            except Exception as e:
                logger.error(f"飞书消息解密失败: {e}")
                return None

        # v2.0 事件结构
        header = raw_message.get("header", {})
        event = raw_message.get("event", {})
        event_type = header.get("event_type", "")

        # 只处理 im.message.receive_v1 事件
        if event_type != "im.message.receive_v1":
            logger.debug(f"忽略非消息事件: {event_type}")
            return None

        # 提取 sender 和 message
        sender = event.get("sender", {})
        message = event.get("message", {})

        chat_type = message.get("chat_type", "p2p")
        message_type_str = message.get("message_type", "text")
        content_str = message.get("content", "{}")

        # content 是 JSON 字符串，需要解析
        try:
            content = json.loads(content_str)
        except json.JSONDecodeError as e:
            logger.error(f"飞书消息 content 解析失败: {e}")
            return None

        # 群聊消息需要检查 @机器人
        if chat_type == "group":
            mentions = message.get("mentions", [])
            if not await self._is_bot_mentioned(mentions):
                logger.debug("群聊消息未 @机器人，忽略")
                return None
            # 清理 @_user_X 占位符
            text = self._clean_mentions(content.get("text", ""), mentions)
        else:
            text = content.get("text", "")

        # 构建 UnifiedMessage
        message_id = message.get("message_id", f"feishu_{int(time.time() * 1000)}")
        user_id = sender.get("sender_id", {}).get("open_id", "")

        # create_time 容错：飞书返回字符串型毫秒时间戳，非数字时兜底用当前时间
        try:
            timestamp = datetime.fromtimestamp(int(message.get("create_time", "")) / 1000)
        except (TypeError, ValueError):
            timestamp = datetime.now()

        # 处理不同的消息类型
        msg_content: Dict[str, Any] = {}
        msg_type = MessageType.TEXT

        if message_type_str == "text":
            msg_content["text"] = text
        elif message_type_str == "image":
            msg_type = MessageType.IMAGE
            msg_content["image_key"] = content.get("image_key", "")
        elif message_type_str == "file":
            msg_type = MessageType.FILE
            msg_content["file_key"] = content.get("file_key", "")
            msg_content["file_name"] = content.get("file_name", "")
        else:
            # 其他类型降级为文本
            msg_content["text"] = f"[{message_type_str} 消息]"

        return UnifiedMessage(
            message_id=message_id,
            channel_type=ChannelType.FEISHU,
            user_id=user_id,
            user_name="",  # 飞书 v2.0 事件不含用户名，需额外 API 获取
            message_type=msg_type,
            content=msg_content,
            timestamp=timestamp,
            raw_message=raw_message,
        )

    async def _is_bot_mentioned(self, mentions: list) -> bool:
        """
        检查机器人是否被 @

        Args:
            mentions: mentions 数组

        Returns:
            是否 @了机器人
        """
        if not mentions:
            return False

        # 懒加载机器人 open_id（失败时会进入退避，不阻塞本次判断）
        await self._init_bot_open_id()

        # 优先用 bot open_id 精确匹配
        if self._bot_open_id:
            for mention in mentions:
                mention_open_id = mention.get("id", {}).get("open_id", "")
                if mention_open_id == self._bot_open_id:
                    return True
            # bot open_id 已加载但 mentions 中无匹配，确实没 @ 机器人
            return False

        # bot open_id 未获取到（如应用无 /bot/v3/info 权限）：
        # 飞书只会推送 @ 机器人的群聊消息，所以有 mentions 即认为 @ 了机器人。
        logger.debug("机器人 open_id 未获取，按 mentions 非空判定为 @机器人")
        return True

    def _clean_mentions(self, text: str, mentions: list) -> str:
        """
        清理文本中的 @_user_X 占位符

        Args:
            text: 原始文本（含 @_user_X）
            mentions: mentions 数组

        Returns:
            清理后的文本
        """
        result = text
        for mention in mentions:
            key = mention.get("key", "")
            name = mention.get("name", "")
            if key:
                # 将 @_user_X 替换为 @名字（或直接删除）
                result = result.replace(key, f"@{name}" if name else "")
        return result.strip()

    # ==================== 消息发送 ====================

    async def send_message(self, message: UnifiedResponse) -> bool:
        """
        发送飞书消息（自动选择消息类型和拆分）

        发送顺序：文本（含图片占位符）→ 图片 → 可下载文件。
        单图失败不阻断后续发送，记 warning。
        """
        all_success = True
        images = message.get_images()

        # 1. 发送文本（含 placement 占位符）
        text = message.text
        if text:
            text = render_text_with_image_placeholders(text, images)
            all_success = await self.send_long_message(text, message.reply_to)

        # 2. 发送图片（Phase 2 P2.9.1 新增）
        for ref in images:
            try:
                local_path = await self._resolve_image_local_path(ref)
                if not local_path:
                    all_success = False
                    continue
                image_key = await self.media.upload_image(str(local_path))
                if image_key:
                    success = await self.send_image(image_key, message.reply_to)
                    if not success:
                        all_success = False
                else:
                    all_success = False
            except Exception as e:
                logger.warning(f"[feishu] send image {ref.get('file_id')} failed: {e}")
                all_success = False

        # 3. 发送可下载文件
        for file_info in message.downloadable_files:
            # 上传图片或文件到飞书
            if file_info.file_name.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
                # 图片：上传后发送图片消息
                image_key = await self.media.upload_image(file_info.local_path)
                if image_key:
                    success = await self.send_image(image_key, message.reply_to)
                    if not success:
                        all_success = False
                else:
                    all_success = False
            else:
                # 文件：上传后发送文件消息
                file_key = await self.media.upload_file(file_info.local_path)
                if file_key:
                    success = await self.send_file(file_key, file_info.file_name, message.reply_to)
                    if not success:
                        all_success = False
                else:
                    all_success = False

        return all_success

    async def _resolve_image_local_path(self, ref: Dict[str, Any]) -> Optional[Any]:
        """从 ImageRef dict（含 file_id）解析出本地路径。

        通过 ImageRegistry 从 Redis 查 file_id 拿本地绝对路径。
        ref 只要有 file_id 字段即可（不需要 ImageRef 全部字段）。

        Args:
            ref: ImageRef 的 dict 形式（来自 UnifiedResponse.get_images()）

        Returns:
            本地路径 Path 对象，失败返回 None
        """
        from pathlib import Path

        from src.core.image_asset import get_image_registry

        file_id = ref.get("file_id") if isinstance(ref, dict) else None
        if not file_id:
            logger.warning(f"[feishu] resolve image path failed: ref 缺少 file_id, ref={ref}")
            return None
        try:
            registry = get_image_registry()
            ref_obj = await registry.get_ref_by_file_id(file_id)
            if ref_obj is None:
                logger.warning(f"[feishu] image not found in registry: file_id={file_id}")
                return None
            return await registry.resolve_local_path(ref_obj)
        except Exception as e:
            logger.warning(f"[feishu] resolve image path failed file_id={file_id}: {e}")
            return None

    async def send_long_message(self, text: str, user_id: str) -> bool:
        """
        发送可能超长的消息（自动拆分 + 类型检测）

        Args:
            text: 消息文本
            user_id: 目标用户 ID（open_id）

        Returns:
            是否全部发送成功
        """
        if not self._check_rate_limit(user_id):
            logger.warning(f"send_long_message 被速率限制拦截: user={user_id}")
            return False

        max_bytes = self._msg_config["max_bytes"]
        parts = self.message_builder.split_long_message(
            text, max_bytes, self._msg_config["split_on_paragraph"]
        )

        all_success = True
        for part in parts:
            # 智能选择消息类型
            msg_type = self.message_builder.detect_message_type(part)

            if msg_type == "text":
                msg_body = self.message_builder.build_text(part)
            elif msg_type == "post":
                paragraphs = self.message_builder.markdown_to_post_paragraphs(part)
                msg_body = self.message_builder.build_post("", paragraphs)
            else:
                msg_body = self.message_builder.build_text(part)

            success = await self._send_with_retry(msg_body, user_id)
            if not success:
                all_success = False

        return all_success

    async def send_text(self, text: str, user_id: str) -> bool:
        """发送纯文本消息"""
        msg_body = self.message_builder.build_text(text)
        return await self._send_with_retry(msg_body, user_id)

    async def send_image(self, image_key: str, user_id: str) -> bool:
        """发送图片消息"""
        msg_body = self.message_builder.build_image(image_key)
        return await self._send_with_retry(msg_body, user_id)

    async def send_file(self, file_key: str, file_name: str, user_id: str) -> bool:
        """发送文件消息"""
        msg_body = self.message_builder.build_file(file_key, file_name)
        return await self._send_with_retry(msg_body, user_id)

    async def send_waiting_indicator(self, user_id: str, message: str) -> bool:
        """
        发送等待提示消息（纯文本，绕过应用层速率限制）。

        此方法故意不调用 _check_rate_limit()，因为：
        1. 等待提示是系统消息，不应计入用户消息配额
        2. 即使用户已触发速率限制，等待提示也应发出（改善 UX）
        """
        return await self.send_text(message, user_id)

    async def send_status_message(
        self, message: UnifiedResponse, *, reserve_for_final: int = 1
    ) -> StatusDeliveryResult:
        """低优先级 status/verbose 发送（Phase 3，设计 §9.3）。

        先经 _reserve_rate_limit 原子预留 final 额度（Redis Lua / 内存临界区），
        再单条纯文本发送；额度不足返回 suppressed_rate_limit（不发送、不占额度）。
        """
        text = (message.text or "").strip()
        if not text:
            return StatusDeliveryResult.suppressed_unsupported("empty verbose text")
        if not self._reserve_rate_limit(message.reply_to, reserve_for_final):
            return StatusDeliveryResult.suppressed_rate_limit(
                "insufficient quota after final reservation"
            )
        try:
            ok = await self.send_text(text, message.reply_to)
        except Exception as e:  # noqa: BLE001 - best-effort：异常收敛为 failed
            return StatusDeliveryResult.failed(f"send_text exception: {e}")
        return StatusDeliveryResult.sent() if ok else StatusDeliveryResult.failed(
            "send_text returned False"
        )

    async def _send_with_retry(
        self, msg_body: Dict[str, Any], user_id: str, max_retries: int = 3
    ) -> bool:
        """
        发送消息（带重试和 token 刷新）

        Args:
            msg_body: 消息体（由 message_builder 构建，content 已是 JSON 字符串）
            user_id: 目标用户 ID
            max_retries: 最大重试次数

        Returns:
            是否成功
        """
        backoff_base = 1.0
        token_refreshed = False  # 标记是否已刷新过 token，避免无限重试

        for attempt in range(max_retries):
            try:
                access_token = await self.get_access_token()
                client = await self._get_client()

                # 使用 uuid 做幂等性去重
                request_id = str(uuid.uuid4())

                response = await client.post(
                    f"{FEISHU_BASE_URL}/open-apis/im/v1/messages",
                    params={"receive_id_type": "open_id"},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "receive_id": user_id,
                        "msg_type": msg_body["msg_type"],
                        "content": msg_body["content"],  # 已是 JSON 字符串
                        "uuid": request_id,  # 幂等性去重
                    },
                )

                data = response.json()
                code = data.get("code", -1)

                # token 过期，刷新后重试（不消耗 retry 配额）
                if code in (99991663, 99991664) and not token_refreshed:
                    logger.warning("tenant_access_token 已过期，正在刷新")
                    self._invalidate_token()
                    token_refreshed = True
                    continue

                if code != 0:
                    logger.error(
                        f"发送消息失败: code={code}, msg={data.get('msg')}"
                    )
                    return False

                logger.info(
                    f"飞书消息发送成功: user={user_id}, "
                    f"msg_type={msg_body['msg_type']}"
                )
                return True

            except Exception as e:
                if attempt < max_retries - 1:
                    wait = backoff_base * (2 ** attempt)
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
        获取飞书用户信息

        API: GET /open-apis/contact/v3/users/{user_id}

        Args:
            user_id: 用户 open_id

        Returns:
            用户信息字典
        """
        try:
            access_token = await self.get_access_token()
            client = await self._get_client()

            response = await client.get(
                f"{FEISHU_BASE_URL}/open-apis/contact/v3/users/{user_id}",
                params={"user_id_type": "open_id"},
                headers={"Authorization": f"Bearer {access_token}"},
            )

            data = response.json()
            if data.get("code", 0) != 0:
                logger.error(
                    f"获取用户信息失败: code={data.get('code')}, msg={data.get('msg')}"
                )
                return {}

            user = data.get("data", {}).get("user", {})

            return {
                "user_id": user.get("open_id", ""),
                "name": user.get("name", ""),
                "en_name": user.get("en_name", ""),
                "department": user.get("department_ids", []),
                "position": user.get("position", ""),
                "email": user.get("email", ""),
                "mobile": user.get("mobile", ""),
                "avatar": user.get("avatar", {}).get("avatar_72", ""),
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
        验证飞书消息签名（v2.0）

        使用 FeishuCrypto 进行签名验证：SHA256(timestamp + nonce + encrypt_key + body)

        Args:
            signature: 签名（X-Lark-Signature 请求头）
            timestamp: 时间戳（X-Lark-Request-Timestamp）
            nonce: 随机数（X-Lark-Request-Nonce）
            body: 原始请求体

        Returns:
            签名是否有效
        """
        if not self.crypto:
            # 未启用加密模式，跳过签名验证
            return True

        return self.crypto.verify_signature(timestamp, nonce, body, signature)
