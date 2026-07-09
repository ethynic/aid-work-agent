"""服务端拉取会话存档的核心执行器

被两处调用：
- callback_handler 收到企微回调后异步触发（设计文档 §3.2 主路径）
- poller 60s 兜底轮询（设计文档 §5.4 保险路径）

主流程：
  1. Redis 分布式锁 wecom_rpa:archive:lock:{tenant_id}（TTL 60s，防 Gunicorn 多 worker 并发）
  2. 从 tenant_channel_configs 读配置 + 解密凭证
  3. 检查 listen_mode='server'（防御性，第一期永远为 True）
  4. 调 C SDK GetChatData 拉一批密文（通过 http_client.get_chat_data 包装）
  5. 逐条：chat_crypto.decrypt_random_key 解 random_key →
     wecom_finance_sdk.decrypt_data_raw 调 SDK DecryptData 拿明文 →
     构造 RpaCallbackEnvelope → 调 _process_inbound_message(source='server_fetcher') →
     推进 last_seq
  6. 单条解密失败也推进 seq + continue（避免坏消息卡死整个租户死循环重拉）

不抛异常给上游（除非致命错误）：拉取/解密/处理异常都记录到 config.last_error_*。
"""

import asyncio
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from src.channels.wecom_personal_rpa.archive import callback_crypto, chat_crypto, http_client
from src.channels.wecom_personal_rpa.archive import wecom_finance_sdk
from src.channels.wecom_personal_rpa.archive import audit as archive_audit
from src.channels.wecom_personal_rpa.archive.credential_codec import (
    FORCED_LISTEN_MODE,
    decrypt_sensitive_fields,
)
from src.channels.wecom_personal_rpa.archive.http_client import (
    WeComApiException,
    WeComRateLimitException,
)
from src.core.redis_client import redis_client
from src.saas.db.channel_config_db import ChannelConfigDB

# Redis 分布式锁键前缀 + TTL
_LOCK_KEY_PREFIX = "wecom_rpa:archive:lock"
_LOCK_TTL_SECONDS = 60

# 单次拉取超时（防死锁，超过就放弃让下一周期重试）
_FETCH_TIMEOUT_SECONDS = 30

# SDK DecryptData 子进程级超时。必须小于外层单条超时，确保先由 SDK 代理层
# terminate/rebuild 卡死的子进程池，再把异常返回给 fetcher 推进 seq。
_SDK_DECRYPT_TIMEOUT_SECONDS = 8

# 单条消息解密总超时（RSA + SDK DecryptData）。外层只做兜底；真正能杀掉
# C SDK 卡死调用的是 wecom_finance_sdk.decrypt_data_raw 的进程级超时。
_SINGLE_ITEM_TIMEOUT_SECONDS = _SDK_DECRYPT_TIMEOUT_SECONDS + 2

# 客户端 ID 占位：server 模式拉取时没有具体客户端，出站靠 account_id 路由
_SERVER_CLIENT_ID_PLACEHOLDER = "_server_"


class ServerArchiveFetcher:
    """服务端会话存档拉取执行器（每个租户一份配置独立调用 fetch_once）。"""

    async def fetch_once(self, tenant_id: str, config_id: str) -> None:
        """拉取一次该租户的所有新消息。

        被两处调用，行为一致：
        - callback_handler：收到企微回调后 asyncio.create_task 异步触发
        - poller：60s 兜底轮询扫描所有 verified 配置

        并发安全：用 Redis 分布式锁保证同 tenant 同时只有一个 fetcher 运行。
        """
        if not tenant_id or not config_id:
            logger.warning(f"[ServerArchiveFetcher] 参数为空 tenant={tenant_id} config_id={config_id}")
            return

        lock_key = f"{_LOCK_KEY_PREFIX}:{tenant_id}"
        lock_value = secrets.token_hex(8)

        acquired = redis_client.acquire_lock(lock_key, lock_value, ex=_LOCK_TTL_SECONDS)
        if not acquired:
            logger.debug(f"[ServerArchiveFetcher] 未获锁 tenant={tenant_id}（另一 fetcher 正在运行），跳过")
            return

        logger.info(
            f"[ServerArchiveFetcher] 获锁开始拉取 tenant={tenant_id} config={config_id}"
        )

        try:
            await asyncio.wait_for(
                self._fetch_once_internal(tenant_id, config_id),
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[ServerArchiveFetcher] fetch_once 超时 {_FETCH_TIMEOUT_SECONDS}s tenant={tenant_id}"
            )
            await self._mark_error(tenant_id, config_id, f"fetch_once 超时 {_FETCH_TIMEOUT_SECONDS}s")
            archive_audit.log_fetch_error(
                tenant_id, config_id, "TimeoutError",
                f"fetch_once 超时 {_FETCH_TIMEOUT_SECONDS}s", stage="fetch_once"
            )
        except WeComRateLimitException as e:
            # 45009：标记错误，下次 poller 周期会跳过该 tenant（暂时由 poller 间隔 + Redis 锁兜底；
            # 后续可加 status='paused' 字段做更精细的暂停）
            logger.warning(f"[ServerArchiveFetcher] 45009 tenant={tenant_id} pause={e.retry_after_seconds}s")
            await self._mark_error(tenant_id, config_id, f"企微 45009 频率限制，暂停 {e.retry_after_seconds}s")
            archive_audit.log_fetch_rate_limited(
                tenant_id, config_id, e.retry_after_seconds
            )
        except Exception as e:
            logger.warning(f"[ServerArchiveFetcher] fetch_once 异常 tenant={tenant_id}: {type(e).__name__}: {e}")
            await self._mark_error(tenant_id, config_id, f"{type(e).__name__}: {e}")
            archive_audit.log_fetch_error(
                tenant_id, config_id, type(e).__name__, str(e), stage="fetch_once"
            )
        finally:
            redis_client.release_lock(lock_key, lock_value)

    # ----------------- 内部实现 -----------------

    async def _fetch_once_internal(self, tenant_id: str, config_id: str) -> None:
        """实际拉取逻辑（已持锁）。"""
        logger.info(
            f"[ServerArchiveFetcher] 进入拉取流程 tenant={tenant_id} config={config_id}"
        )
        cfg = ChannelConfigDB.get_by_tenant_and_id(tenant_id, config_id)
        if cfg is None:
            logger.warning(f"[ServerArchiveFetcher] 配置不存在 tenant={tenant_id} config_id={config_id}")
            return

        if cfg.get("channel_type") != "wecom_personal_rpa":
            logger.warning(f"[ServerArchiveFetcher] 非 wecom_personal_rpa 类型，跳过")
            return

        config_data: Dict[str, Any] = cfg.get("config") or {}

        # 防御性检查（codec 已强制 server，理论上永远为 True）
        listen_mode = config_data.get("listen_mode", FORCED_LISTEN_MODE)
        if listen_mode != "server":
            logger.warning(
                f"[ServerArchiveFetcher] listen_mode={listen_mode} 非 server，跳过 tenant={tenant_id}"
            )
            return

        # 从 config_data 解密敏感字段（get_by_tenant_and_id 已解密，但保险起见再过一遍）
        # 注意：get_by_tenant_and_id 已调用 decrypt_sensitive_fields，config_data 已含明文
        creds = _extract_credentials(config_data)
        if not creds:
            await self._mark_error(tenant_id, config_id, "凭证不完整（缺 corp_id / archive_secret / private_key / token / encoding_aes_key）")
            return

        last_seq = int(config_data.get("last_seq", 0) or 0)
        batch_limit = int(config_data.get("batch_limit", 1000) or 1000)

        # 拉取密文批次（C SDK GetChatData，不需要 access_token）
        # 注意：SDK 用 corpid+secret 直连，与 get_access_token 用同一套凭证；
        # 凭证错误时 SDK 会返回非 0 errcode（如 48002 / 60011），由上层异常处理。
        logger.info(
            f"[ServerArchiveFetcher] 调用 GetChatData tenant={tenant_id} "
            f"config={config_id} seq>{last_seq} limit={batch_limit}"
        )
        batch = await http_client.get_chat_data(
            corpid=creds["corp_id"], secret=creds["archive_secret"],
            seq=last_seq, limit=batch_limit,
        )
        logger.info(
            f"[ServerArchiveFetcher] GetChatData 返回 tenant={tenant_id} "
            f"config={config_id} batch={len(batch.items)}"
        )

        if not batch.items:
            logger.debug(f"[ServerArchiveFetcher] 无新消息 tenant={tenant_id} seq>{last_seq}")
            await self._clear_error(tenant_id, config_id)
            return

        # 推断 account_id（用于 envelope + 后续 outbound 路由）
        # wecom_personal_rpa 渠道约定：account_id 来自 subagent_type 字段（绑定关系）或配置中显式 account_id
        account_id = self._infer_account_id(cfg)

        # 逐条解密 + 处理 + 推进 seq
        # 解密路径（企微官方规范）：
        #   1. RSA-PKCS1v15 解密 encrypt_random_key → random_key bytes
        #   2. random_key 转 UTF-8 字符串（企微 random_key 是可打印字符串，非任意字节）
        #   3. 调 SDK DecryptData(encrypt_key, encrypt_chat_msg) → 明文 JSON
        # 注意：encrypt_chat_msg 的 base64 解码 + AES 解密由 SDK 内部完成，
        #       不要用 Python 自己 AES 解密（曾经尝试过，遇到 SDK 返回非 4 倍数
        #       长度的密文时无解，SDK 内部对此有容错）
        processed_count = 0
        failed_count = 0
        total_items = len(batch.items)
        logger.info(
            f"[ServerArchiveFetcher] 开始处理密文 tenant={tenant_id} "
            f"config={config_id} total={total_items} account_id={account_id}"
        )
        for index, item in enumerate(batch.items, start=1):
            try:
                self._log_item_progress(tenant_id, config_id, index, total_items, item)
                # 单条超时：SDK DecryptData 卡死时（曾遇到 4n+1 密文子进程挂起）跳过该条，
                # 不让坏消息卡死整个租户。SDK 代理层会先在子进程级超时并重建池，
                # 外层 wait_for 只是防止 RSA 或未知 Python 逻辑异常阻塞过久。
                random_key_bytes, plain_json = await asyncio.wait_for(
                    self._decrypt_one(
                        creds["private_key"], item.encrypt_random_key, item.encrypt_chat_msg,
                    ),
                    timeout=_SINGLE_ITEM_TIMEOUT_SECONDS,
                )
                env, env_raw = self._build_envelope(account_id, item, plain_json)

                # 延迟 import 避免顶层循环
                from src.saas.api.wecom_personal_rpa_routes import _process_inbound_message

                await _process_inbound_message(
                    tenant_id=tenant_id,
                    env=env,
                    env_raw=env_raw,
                    source="server_fetcher",
                )

                # 成功一条立即推进 seq（避免重拉重复触发，与 C# 实现一致）
                if item.seq > last_seq:
                    ChannelConfigDB.update_config_field(config_id, "last_seq", item.seq)
                    last_seq = item.seq
                processed_count += 1

            except WeComRateLimitException:
                raise  # 45009 由上层处理
            except Exception as ex:
                # 单条解密/处理失败：推进 seq 跳过该条（避免坏消息卡死整个租户死循环重拉），
                # 记录错误到 audit，继续处理后续条目。
                # 历史行为是 break 不推进 seq，结果遇到一条坏消息（如 Base64 异常）就永远卡住。
                failed_count += 1
                logger.warning(
                    f"[ServerArchiveFetcher] 解密/处理失败 msgid={item.msg_id} seq={item.seq} "
                    f"tenant={tenant_id}: {type(ex).__name__}: {ex}（已跳过，继续下一条）"
                )
                archive_audit.log_fetch_error(
                    tenant_id, config_id,
                    type(ex).__name__,
                    f"msgid={item.msg_id} seq={item.seq}: {ex}",
                    stage="decrypt_message",
                )
                if item.seq > last_seq:
                    ChannelConfigDB.update_config_field(config_id, "last_seq", item.seq)
                    last_seq = item.seq

        # 更新最近拉取时间 + 清错误
        ChannelConfigDB.update_config_field(
            config_id, "last_fetch_at", datetime.now(timezone.utc).isoformat()
        )
        await self._clear_error(tenant_id, config_id)
        logger.info(
            f"[ServerArchiveFetcher] 拉取完成 tenant={tenant_id} batch={len(batch.items)} "
            f"processed={processed_count} failed={failed_count} last_seq={last_seq}"
        )
        # audit：拉取成功（source 由调用栈推断：callback_handler 调用 vs poller 调用）
        # 简化做法：根据调用上下文不区分，统一记 fetch_success，统计意义已足够
        archive_audit.log_fetch_success(
            tenant_id=tenant_id,
            config_id=config_id,
            source="fetcher",
            batch_size=len(batch.items),
            processed=processed_count,
            last_seq=last_seq,
            account_id=account_id or None,
        )

    # ----------------- 解密 -----------------

    async def _decrypt_one(
        self, private_key_pem: str, encrypt_random_key: str, encrypt_chat_msg: str
    ) -> Tuple[bytes, str]:
        """解密单条消息：RSA 解 random_key + SDK DecryptData 拿明文。

        两步都放到线程池，避免阻塞事件循环。SDK 调用本身是同步阻塞的 ctypes 调用
        （内部还会跨子进程），可能因密文异常而长时间挂起。

        Returns:
            (random_key_bytes, plain_json)，random_key 主要用于调试，业务上只用 plain_json。
        """
        random_key_bytes = await asyncio.to_thread(
            chat_crypto.decrypt_random_key, private_key_pem, encrypt_random_key
        )
        plain_json = await asyncio.to_thread(
            wecom_finance_sdk.decrypt_data_raw,
            random_key_bytes, encrypt_chat_msg, _SDK_DECRYPT_TIMEOUT_SECONDS,
        )
        return random_key_bytes, plain_json

    @staticmethod
    def _log_item_progress(
        tenant_id: str,
        config_id: str,
        index: int,
        total: int,
        item: "http_client.ChatDataItem",
    ) -> None:
        """按采样记录单条处理进度，避免大批量时日志爆炸。"""
        if index <= 5 or index == total or index % 100 == 0:
            logger.info(
                f"[ServerArchiveFetcher] 处理消息 tenant={tenant_id} config={config_id} "
                f"idx={index}/{total} seq={item.seq} msgid={item.msg_id} "
                f"action={item.action} type={item.msg_type}"
            )
        else:
            logger.debug(
                f"[ServerArchiveFetcher] 处理消息 tenant={tenant_id} config={config_id} "
                f"idx={index}/{total} seq={item.seq} msgid={item.msg_id}"
            )

    # ----------------- envelope 构造 -----------------

    def _build_envelope(
        self, account_id: str, item: "http_client.ChatDataItem", plain_json: str
    ) -> Tuple[Any, Dict[str, Any]]:
        """构造 RpaCallbackEnvelope + env_raw（与 C# InboundEventBuilder.BuildAsync 字段对齐）。

        Args:
            account_id: 推断的企微账号 ID。
            item: 企微密文条目（含 seq / msgid / action / from / msgtype / msgtime / roomid / tolist）。
            plain_json: 解密后的明文 JSON（含 text.content / image.sdkfileid 等）。

        Returns:
            (env: RpaCallbackEnvelope, env_raw: dict)
            env 用于 _process_inbound_message 内部 model_validate 后再处理；
            env_raw 用于 parse_message（adapter 解析需要原始 dict 结构）。
        """
        # 解析明文 JSON 拿到 text / media 字段
        try:
            plain = json.loads(plain_json)
        except Exception:
            plain = {}

        # 提取文本内容（text 类型的 content 字段；其他类型可能空）
        text_content = ""
        msg_type = item.msg_type
        if msg_type == "text":
            text_content = (plain.get("text") or {}).get("content") or plain.get("text") or ""
        else:
            text_content = plain.get("text") or ""

        # 提取媒体字段（image/file/voice/video）
        media_sdk_file_id = ""
        media_file_name: Optional[str] = None
        if msg_type in ("image", "file", "voice", "video"):
            payload_section = plain.get(msg_type) or {}
            media_sdk_file_id = payload_section.get("sdkfileid") or ""
            media_file_name = payload_section.get("filename")

        # 构造 conversation_id / conversation_type（与 C# InferConversation 一致）
        if item.roomid:
            conversation_id = item.roomid
            conversation_type = "external_group"
        else:
            peer = item.tolist[0] if item.tolist else item.from_ or "unknown"
            conversation_id = f"{item.from_}_{peer}"
            conversation_type = "external_user"

        # event_id 复用 archive msgid（与 C# BuildEventId 一致，前缀 msg_）
        raw_msgid = item.msg_id or secrets.token_hex(16)
        event_id = f"msg_{raw_msgid}"

        # message_type 映射（与 C# MapMessageType 一致：未知归一为 link）
        message_type = self._map_message_type(msg_type)

        # occurred_at：用 msg_time（秒级 Unix 时间戳）
        try:
            occurred_at = datetime.fromtimestamp(item.msg_time or 0, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            occurred_at = datetime.now(timezone.utc)

        # 构造 attachments（媒体消息才有，且首版只透传 sdkfileid，URL 由客户端下载）
        attachments = []
        if media_sdk_file_id:
            attachments.append(
                {
                    "type": msg_type,
                    "url": "",  # 服务端不下载媒体，URL 留空，客户端 C# ArchiveMediaDownloader 处理
                    "name": media_file_name,
                    "sdk_file_id": media_sdk_file_id,  # 透传给客户端
                }
            )

        env_raw = {
            "event_id": event_id,
            "client_id": _SERVER_CLIENT_ID_PLACEHOLDER,
            "account_id": account_id,
            "event_type": "message",
            "occurred_at": occurred_at.isoformat(),
            "payload": {
                "conversation_id": conversation_id,
                "conversation_type": conversation_type,
                "sender_display_name": item.from_,  # 首版 display_name = stable_id
                "sender_stable_id": item.from_,
                "message_type": message_type,
                "text": text_content,
                "attachments": attachments,
            },
        }

        # 延迟 import 避免顶层循环依赖
        from src.channels.wecom_personal_rpa.schemas import RpaCallbackEnvelope

        env = RpaCallbackEnvelope.model_validate(env_raw)
        return env, env_raw

    @staticmethod
    def _map_message_type(archive_msg_type: str) -> str:
        """企微 msgtype → RpaMessagePayload.message_type（与 C# MapMessageType 一致）。"""
        mapping = {
            "text": "text",
            "image": "image",
            "file": "file",
            "voice": "voice",
            "video": "video",
            "link": "link",
        }
        return mapping.get(archive_msg_type, "link")

    @staticmethod
    def _infer_account_id(cfg: Dict[str, Any]) -> str:
        """从配置中推断 account_id。

        优先级：
        1. config.account_id（用户显式填写）
        2. subagent_type（绑定关系，本期 server 模式默认）
        3. 空字符串（占位，下游按需处理）
        """
        config_data = cfg.get("config") or {}
        return (
            config_data.get("account_id")
            or cfg.get("subagent_type")
            or ""
        )

    # ----------------- 错误状态写入 -----------------

    async def _mark_error(self, tenant_id: str, config_id: str, msg: str) -> None:
        try:
            ChannelConfigDB.update_config_field(config_id, "last_error_at",
                                                datetime.now(timezone.utc).isoformat())
            ChannelConfigDB.update_config_field(config_id, "last_error_msg", msg[:500])
        except Exception as e:
            logger.warning(f"[ServerArchiveFetcher] 写错误状态失败 tenant={tenant_id}: {e}")

    async def _clear_error(self, tenant_id: str, config_id: str) -> None:
        try:
            ChannelConfigDB.update_config_field(config_id, "last_error_at", None)
            ChannelConfigDB.update_config_field(config_id, "last_error_msg", None)
        except Exception as e:
            logger.warning(f"[ServerArchiveFetcher] 清错误状态失败 tenant={tenant_id}: {e}")


# ----------------- 工具函数 -----------------


def _extract_credentials(config_data: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """从 config JSON 提取服务端拉取所需的明文凭证。

    config_data 应来自 ChannelConfigDB.get_by_tenant_and_id / get_by_id_decrypted，
    敏感字段已解密为明文。

    返回 dict 或 None（凭证不完整时）。
    """
    corp_id = config_data.get("corp_id")
    archive_secret = config_data.get("archive_secret")
    private_key = config_data.get("private_key")
    token = config_data.get("token")
    encoding_aes_key = config_data.get("encoding_aes_key")

    if not all([corp_id, archive_secret, private_key, token, encoding_aes_key]):
        return None

    return {
        "corp_id": corp_id,
        "archive_secret": archive_secret,
        "private_key": private_key,
        "token": token,
        "encoding_aes_key": encoding_aes_key,
    }


# 全局单例（与 poller / callback_handler 共享）
fetcher = ServerArchiveFetcher()
