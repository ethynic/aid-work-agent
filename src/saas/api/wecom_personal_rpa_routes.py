"""企业微信个人账号 RPA 渠道路由（Wire 层）

本模块是 RPA 渠道对外的 HTTP / WebSocket 入口，完全复刻 wecom_kf 的处理范式，
但鉴权 / 解析 / 投递全部委托给 ``src.channels.wecom_personal_rpa`` 各模块：

- ``POST /t/{tenant_id}/wecom_personal_rpa/callback/{config_id}``
    客户端上报入站事件（message / status / action_result），HMAC 鉴权后
    按 event_type 分发；message 事件 task 化后台处理，所有分支立即返回
    ``{"result": "accepted"}``。
- ``GET /api/v1/channels/wecom-personal-rpa/config``
    下发客户端运行配置（protocol_version / rate_limits / paused）。
- ``GET /api/v1/channels/wecom-personal-rpa/files/{file_id}``
    短期签名下载（hmac 自签 token，TTL ≈600s），从租户存储目录回吐文件。
- ``WS /t/{tenant_id}/wecom_personal_rpa/ws/{config_id}``
    在线客户端长连接，握手从 query 取 client_id/timestamp/nonce/signature 验签；
    建立后把该 client 的 pending outbox 推送一遍。

鉴权：所有客户端→服务端请求经 ``auth.verify_request``，``get_secret`` 回调
在回调内 ``db.get_client`` 校验 tenant_id 一致后 ``secret_crypto.decrypt_secret``。

错误信封统一 ``RpaErrorResponse``，``debug`` 字段脱敏（剔除 secret/token/
signature/绝对路径）。

路由用全路径定义，include 时不加 prefix。

规范对齐：
- backend_dev.md：loguru（禁 logging）、错误 debug 字段脱敏、租户隔离、
  文件存储走 ``get_tenant_storage_abs_path``、callback 不阻塞（task 化）、绝对 import。
- docs/system/wecom-personal-rpa-protocol.md：B 节签名逐字对齐。
"""

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from src.channels.idempotency import MessageDeduplicator
from src.channels.session import channel_session_manager
from src.channels.wecom_personal_rpa import auth, db, secret_crypto
from src.channels.wecom_personal_rpa.adapter import WeComPersonalRpaAdapter
from src.channels.wecom_personal_rpa.connection import client_connection_registry
from src.channels.wecom_personal_rpa.message import (
    parse_action_result,
    parse_rpa_message,
    parse_status_event,
)
from src.channels.wecom_personal_rpa.router import (
    check_conversation_authorization,
    is_allowed_by_monitor_whitelist,
)
from src.channels.wecom_personal_rpa.schemas import (
    PROTOCOL_VERSION,
    MonitorUsersEntry,
    RpaCallbackEnvelope,
    RpaConfigResponse,
    RpaErrorResponse,
    RpaRateLimits,
)
from src.core.storage import get_tenant_storage_abs_path

router = APIRouter(tags=["企业微信个人RPA渠道"])

# ===========================================================================
# 常量
# ===========================================================================

_CHANNEL_TYPE = "wecom_personal_rpa"

# 幂等键前缀（与 protocol.md §A.9 对齐）
_DEDUP_PREFIX = "wecom_personal_rpa"
# 出站动作入队去重前缀（与 action_client.deliver_actions 内部一致）
_OUTBOX_DEDUP_PREFIX = "wecom_personal_rpa"


def _normalize_conversation_search_name(display_name: Optional[str]) -> Optional[str]:
    """生成企微搜索名：仅移除末尾精确 ``@微信``，不做模糊替换。"""
    value = (display_name or "").strip()
    if value.endswith("@微信"):
        value = value[:-3].strip()
    return value or None

# 文件下载短期签名 token TTL（秒），与 nonce 防重放窗口一致
_FILE_TOKEN_TTL_SECONDS = 600
# 媒体上传文件下载 token TTL（秒），24 小时
_MEDIA_TOKEN_TTL_SECONDS = 24 * 3600
# 媒体上传文件大小上限（字节），100 MB
_MEDIA_MAX_SIZE_BYTES = 100 * 1024 * 1024
# 媒体上传文件扩展名白名单（小写，不含点）。
# 不在白名单的扩展名返回 unsupported_file_type，避免 .exe/.bat/.ps1/.js 等危险文件
# 通过签名 URL 下载。
_MEDIA_ALLOWED_EXTENSIONS = frozenset(
    {
        "png", "jpg", "jpeg", "gif", "bmp", "webp",
        "pdf", "docx", "xlsx", "pptx",
        "zip", "txt", "csv",
    }
)
# 文件签名主密钥环境变量名（与 secret_crypto 主密钥解耦，单独可配置）
_FILE_TOKEN_SECRET_ENV = "RPA_FILE_TOKEN_SECRET"

# audit category 取值
_AUDIT_INBOUND = "inbound_message"
_AUDIT_STATUS = "status_event"
_AUDIT_ACTION_RESULT = "action_result"
_AUDIT_NEEDS_REVIEW = "needs_review"
_AUDIT_AGENT_REPLY = "agent_reply"
_AUDIT_MONITOR_FILTERED = "monitor_whitelist_filtered"


# ===========================================================================
# 脱敏工具
# ===========================================================================

import re

# 敏感信息脱敏正则（对齐 backend_dev.md sanitize_error_info 思路）
_SENSITIVE_PATTERNS = [
    # secret=xxx / api_key=xxx / token=xxx / signature=xxx（含中英文冒号、空格）
    re.compile(r'(secret|api[_-]?key|token|signature)["\s:=]+[^\s,"]+', re.IGNORECASE),
    # 绝对路径（Windows / POSIX）
    re.compile(r'([A-Za-z]:\\[^\s"\']+|/[^\s"\']+)'),
]


def _sanitize_debug(value: str) -> str:
    """脱敏 debug 文本：剔除 secret / token / signature / 绝对路径。

    用于 ``RpaErrorResponse.debug`` 序列化前的清洗，确保不泄漏密钥与路径。
    """
    if not value:
        return value
    sanitized = value
    for pat in _SENSITIVE_PATTERNS:
        sanitized = pat.sub("***", sanitized)
    return sanitized


def _error_response(
    error: str,
    message: str,
    debug: Optional[str] = None,
    status_code: int = 401,
) -> JSONResponse:
    """构造统一的 ``RpaErrorResponse`` JSONResponse，debug 字段先脱敏。"""
    body = RpaErrorResponse(
        error=error,  # type: ignore[arg-type]
        message=message,
        debug=_sanitize_debug(debug) if debug else None,
    )
    return JSONResponse(content=body.dict(), status_code=status_code)


# ===========================================================================
# 鉴权辅助：构造 get_secret 回调
# ===========================================================================


def _make_get_secret(tenant_id: str):
    """返回 ``get_secret(client_id) -> bytes | None`` 闭包。

    在回调内：
    1. ``db.get_client(cid)`` 读取客户端（跨租户可见，client_id 全局唯一）。
    2. 校验 ``client.tenant_id == tenant_id``（租户隔离，不一致返回 None → auth_failed）。
    3. 校验 ``status == 'active'``（禁用返回 None → auth_failed；auth 层不区分，
       协议要求 client_disabled，但 auth.verify_request 统一返回 auth_failed，
       这里保守返回 None 让其走 auth_failed 分支）。
    4. ``secret_crypto.decrypt_secret`` 解密 encrypted_secret 返回 bytes。
       解密异常记 error 并返回 None。
    """

    def _get_secret(client_id: str) -> Optional[bytes]:
        try:
            client = db.get_client(client_id)
        except Exception as e:
            logger.error(f"RPA get_secret: db.get_client 失败 cid={client_id}: {e}")
            return None
        if not client:
            return None
        if client.get("tenant_id") != tenant_id:
            # 跨租户访问：视为不存在，保守返回 None
            logger.info(
                f"RPA get_secret: 租户不匹配 cid={client_id} "
                f"path_tenant={tenant_id}"
            )
            return None
        if client.get("status") != "active":
            logger.info(f"RPA get_secret: 客户端已禁用 cid={client_id}")
            return None
        encrypted = client.get("encrypted_secret")
        if not encrypted:
            return None
        try:
            return secret_crypto.decrypt_secret(encrypted)
        except Exception as e:
            # 不记录密文/明文；仅记异常类型
            logger.error(
                f"RPA get_secret: decrypt_secret 失败 cid={client_id}: "
                f"{type(e).__name__}"
            )
            return None

    return _get_secret


def _make_get_secret_by_client_id():
    """``/config`` 专用 get_secret：仅按 client_id 查、不校验 tenant_id。

    背景：客户端首次拉 ``/config`` 时还不知道自己的 tenant_id（正是要从响应里拿），
    无法在请求头或路径里携带。``/config`` 的鉴权已由 HMAC 签名保证（知道 secret 即合法 client），
    client_id 全局唯一，故无需 tenant_id 二次校验。

    仍校验 ``status == 'active'``，禁用客户端继续被拒。
    """

    def _get_secret(client_id: str) -> Optional[bytes]:
        try:
            client = db.get_client(client_id)
        except Exception as e:
            logger.error(f"RPA get_secret_by_client_id: db.get_client 失败 cid={client_id}: {e}")
            return None
        if not client:
            return None
        if client.get("status") != "active":
            logger.info(f"RPA get_secret_by_client_id: 客户端已禁用 cid={client_id}")
            return None
        encrypted = client.get("encrypted_secret")
        if not encrypted:
            return None
        try:
            return secret_crypto.decrypt_secret(encrypted)
        except Exception as e:
            # 不记录密文/明文；仅记异常类型
            logger.error(
                f"RPA get_secret: decrypt_secret 失败 cid={client_id}: "
                f"{type(e).__name__}"
            )
            return None

    return _get_secret


# ===========================================================================
# 1. 入站回调
# ===========================================================================


@router.get("/t/{tenant_id}/wecom_personal_rpa/callback/{config_id}")
async def wecom_personal_rpa_callback_get(
    tenant_id: str, config_id: str, request: Request
):
    """企微会话存档回调 URL 验证（GET echostr）。

    仅 server 模式生效（企微后台首次配置回调时触发）。
    client 模式不需要此验证。
    """
    from src.channels.wecom_personal_rpa.archive import callback_handler

    return await callback_handler.handle_archive_echostr(tenant_id, config_id, request)


@router.post("/t/{tenant_id}/wecom_personal_rpa/callback/{config_id}")
async def wecom_personal_rpa_callback(
    tenant_id: str, config_id: str, request: Request
):
    """RPA 入站回调入口（双验签兼容）。

    Phase 6 起改为双模式兼容：
    - body 是 XML 格式 → 企微会话存档回调（server 模式，走 archive_handler）
    - body 是 JSON 格式 → 客户端 HMAC 上报（client 模式，原有路径不变）

    两种模式由 body 格式天然区分，无需试错。第一期仅 server 模式可用，
    client 模式代码保留为未来开放做准备。
    """
    # 1. 原始字节（签名依赖原始字节，禁止 re-serialize）
    raw_body = await request.body()

    # 2. body 格式分流：XML → 企微回调，JSON → 客户端 HMAC
    from src.channels.wecom_personal_rpa.archive import callback_handler

    if callback_handler.is_archive_callback_request(request, raw_body):
        # server 模式：企微会话存档回调
        return await callback_handler.handle_archive_event(
            tenant_id, config_id, request, raw_body
        )

    # client 模式：原有客户端 HMAC 上报路径（本期不开放，代码保留）
    headers = dict(request.headers)

    # 2. 鉴权
    get_secret = _make_get_secret(tenant_id)
    vr = auth.verify_request(headers, raw_body, get_secret)
    if not vr.ok:
        logger.info(
            f"RPA callback 鉴权失败 tenant={tenant_id} config={config_id} "
            f"error={vr.error}"
        )
        return _error_response(
            error=vr.error or "auth_failed",
            message="鉴权失败",
            debug=None,
            status_code=401,
        )

    # 3. 解析信封
    try:
        envelope_dict = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"RPA callback body 非合法 JSON: {type(e).__name__}")
        return _error_response(
            error="bad_request",
            message="请求体不是合法 JSON",
            debug=None,
            status_code=400,
        )

    try:
        env = RpaCallbackEnvelope.model_validate(envelope_dict)
    except Exception as e:  # pydantic.ValidationError
        logger.warning(f"RPA callback 信封校验失败: {e}")
        return _error_response(
            error="bad_request",
            message="回调信封字段校验失败",
            debug=str(e),
            status_code=400,
        )

    # 4. 去重：event_id 维度
    dedup = MessageDeduplicator()
    dedup_key = f"{_DEDUP_PREFIX}:{tenant_id}:{env.event_id}"
    if await dedup.is_duplicate(dedup_key):
        logger.info(f"RPA callback 重复事件已跳过: event_id={env.event_id}")
        return {"result": "accepted"}

    # 5. 记录最近心跳
    try:
        db.update_last_seen(tenant_id, env.client_id)
    except Exception as e:
        logger.warning(f"RPA callback update_last_seen 失败: {e}")

    # 6. 按 event_type 分发
    env_raw = envelope_dict  # 传给下游解析的原始 dict（含 payload）

    if env.event_type == "message":
        # task 化后台处理，不阻塞回调响应
        asyncio.create_task(_process_inbound_message(tenant_id, env, env_raw))
        return {"result": "accepted"}

    if env.event_type == "status":
        await _handle_status_event(tenant_id, env, env_raw)
        return {"result": "accepted"}

    if env.event_type == "action_result":
        await _handle_action_result(tenant_id, env, env_raw)
        return {"result": "accepted"}

    # 未知 event_type：记审计后接受（兼容未来新增类型，不阻断客户端）
    logger.warning(f"RPA callback 未知 event_type={env.event_type}")
    try:
        db.write_audit(
            tenant_id=tenant_id,
            client_id=env.client_id,
            account_id=env.account_id,
            category=_AUDIT_INBOUND,
            payload_json=json.dumps(
                {"event_id": env.event_id, "event_type": env.event_type, "unknown": True},
                ensure_ascii=False,
            ),
        )
    except Exception as e:
        logger.warning(f"RPA callback 未知事件审计写入失败: {e}")
    return {"result": "accepted"}


# ===========================================================================
# 1.1 message 事件处理（后台 task）
# ===========================================================================


async def _process_inbound_message(
    tenant_id: str,
    env: RpaCallbackEnvelope,
    env_raw: dict,
    source: str = "client_callback",
) -> None:
    """处理入站聊天消息事件。

    复刻 wecom_kf 流程，但解析 / 路由 / 投递全部走 RPA 渠道组件：
    - adapter.parse_message → UnifiedMessage
    - router.check_conversation_authorization → needs_review / paused 判定
    - ensure_user_registered → channel_session_manager.get_or_create_session
    - agent.process_message_sync（经 session_queue 串行调度）
    - adapter.set_reply_context + adapter.send_message（走 action_client 投递）

    source 参数仅用于日志/审计区分来源：
    - "client_callback"（默认）：C# 客户端上报的回调（已有路径）
    - "server_fetcher"：服务端 archive fetcher 拉取后调用（Phase 4 新增）
    两种来源行为完全等价，复用同一处理链路。
    """
    # 延迟 import：避免顶层 import src.core.* 触发 master_agent 单例创建链副作用
    from src.core.agent_router import agent_router
    from src.core.session_queue import session_queue
    from src.saas.services.auto_register import ensure_user_registered
    from src.services.session_record import SessionRecordManager

    try:
        # 1. 解析消息
        adapter = WeComPersonalRpaAdapter(
            client_id=env.client_id,
            tenant_id=tenant_id,
            account_id=env.account_id,
        )
        um = await adapter.parse_message(env_raw)

        # 2. 授权判定（binding 是否 active）
        payload = env.payload or {}
        conversation_id = payload.get("conversation_id", "")
        conversation_type = payload.get("conversation_type", "external_user")
        sender_display_name = payload.get("sender_display_name", "")
        sender_stable_id = payload.get("sender_stable_id")
        # search_key 归一化：稳定 ID 优先，否则 account_id + display_name
        search_key = sender_stable_id or f"{env.account_id}:{sender_display_name}"

        authz = await check_conversation_authorization(
            tenant_id=tenant_id,
            account_id=env.account_id,
            conversation_id=conversation_id,
            conversation_type=conversation_type,
            search_key=search_key,
            display_name=sender_display_name,
            stable_id=sender_stable_id,
        )

        if authz.needs_review or authz.paused:
            # 需人工确认 / 已暂停：不下发 agent，仅审计 + accepted
            try:
                db.write_audit(
                    tenant_id=tenant_id,
                    client_id=env.client_id,
                    account_id=env.account_id,
                    category=_AUDIT_NEEDS_REVIEW,
                    payload_json=json.dumps(
                        {
                            "event_id": env.event_id,
                            "session_id": authz.session_id,
                            "needs_review": authz.needs_review,
                            "paused": authz.paused,
                            "reason": authz.reason,
                        },
                        ensure_ascii=False,
                    ),
                )
            except Exception as e:
                logger.warning(f"RPA needs_review 审计写入失败: {e}")
            logger.info(
                f"RPA message 跳过（待确认/暂停）event_id={env.event_id} "
                f"reason={authz.reason}"
            )
            return

        session_id = authz.session_id

        # 2.5 绑定级监控白名单服务端二次校验
        # 客户端缓存白名单只是优化（减少 callback），真正的过滤必须服务端做。
        # 防止客户端绕过白名单上报非白名单内的消息。
        authoritative_binding = None
        try:
            authoritative_binding = db.find_binding_by_search_key(
                tenant_id=tenant_id,
                account_id=env.account_id,
                search_key=search_key,
            )
        except Exception as e:
            logger.warning(
                f"RPA monitor whitelist 查询 binding 失败 event_id={env.event_id}: {e}"
            )
        if not is_allowed_by_monitor_whitelist(
            binding=authoritative_binding,
            sender_display_name=sender_display_name,
            sender_stable_id=sender_stable_id,
        ):
            try:
                db.write_audit(
                    tenant_id=tenant_id,
                    client_id=env.client_id,
                    account_id=env.account_id,
                    category=_AUDIT_MONITOR_FILTERED,
                    payload_json=json.dumps(
                        {
                            "event_id": env.event_id,
                            "session_id": session_id,
                            "sender_display_name": sender_display_name,
                            "sender_stable_id": sender_stable_id,
                            "reason": "not_in_monitor_whitelist",
                        },
                        ensure_ascii=False,
                    ),
                )
            except Exception as e:
                logger.warning(f"RPA monitor_whitelist_filtered 审计写入失败: {e}")
            logger.info(
                f"RPA message 被监控白名单过滤 event_id={env.event_id} "
                f"sender={sender_display_name} sid={sender_stable_id}"
            )
            return

        authoritative_display_name = (
            (authoritative_binding or {}).get("display_name") or sender_display_name
        )
        conversation_search_name = _normalize_conversation_search_name(
            authoritative_display_name
        )

        # 3. 自动注册用户
        user_info = {
            "name": um.user_name or sender_display_name,
            "user_id": um.user_id,
        }
        try:
            registered_user_id = await ensure_user_registered(
                _CHANNEL_TYPE,
                um.user_id,
                tenant_id,
                user_info,
                source=_CHANNEL_TYPE,
            )
            if registered_user_id:
                user_info["user_id"] = registered_user_id
        except Exception as e:
            logger.warning(f"RPA ensure_user_registered 失败: {e}")

        # 4. 会话（channel_user_id 用 account_id:conversation_id 保证唯一）
        channel_user_id = f"{env.account_id}:{conversation_id}"
        # subagent_id 由 channel_config 的 subagent_type 决定，首版留空走 master
        subagent_id = ""
        session = channel_session_manager.get_or_create_session(
            channel_type=_CHANNEL_TYPE,
            channel_user_id=channel_user_id,
            tenant_id=tenant_id,
            user_info=user_info,
            subagent_id=subagent_id,
            channel_chat_id=conversation_id,
            metadata={
                "account_id": env.account_id,
                "conversation_id": conversation_id,
            },
        )
        channel_session_id = session["session_id"]

        # 5. 落库用户消息 —— P0-2：推迟到 process_and_persist 内统一写入
        user_text = um.text or ""

        # 6. agent 处理（经 session_queue 串行调度）
        agent = agent_router.get_agent(
            subagent_id or None, channel_session_id, tenant_id=tenant_id
        )

        record = SessionRecordManager.start_record(
            session_id=channel_session_id,
            user_id=user_info.get("user_id") or um.user_id,
            user_message=user_text,
            tenant_id=tenant_id,
            source_type=_CHANNEL_TYPE,
        )
        try:
            record.set_model(agent.llm.get_model_name())
            record.set_provider(agent.llm.get_provider_name())
        except Exception:
            pass

        # send_response 回调：通过工厂方法构造（含 RPA 的 set_reply_context 钩子）
        send_response = channel_session_manager.make_send_response(
            adapter=adapter,
            message_id=env.event_id,
            reply_to=um.user_id,
            log_tag="[RPA]",
            pre_send=lambda: adapter.set_reply_context(
                account_id=env.account_id,
                conversation_id=conversation_id,
                session_id=session_id,
                request_id=env.event_id,
                tenant_id=tenant_id,
                sender_display_name=authoritative_display_name,
                sender_stable_id=sender_stable_id,
                conversation_search_name=conversation_search_name,
                inbound_text=user_text,
            ),
        )

        try:
            result = await channel_session_manager.process_and_persist(
                session_id=channel_session_id,
                tenant_id=tenant_id,
                user_content=user_text,
                user_metadata={"event_id": env.event_id, "client_id": env.client_id},
                message_type="text",
                agent=agent,
                record_service=record,
                send_response=send_response,
            )
        except Exception as e:
            logger.error(
                f"RPA agent 处理异常 event_id={env.event_id}: {e}", exc_info=True
            )
            record.mark_error(str(e))
            SessionRecordManager.end_record()
            return

        SessionRecordManager.end_record()

        if result["status"] == "merged":
            # 消息被合并 / 排队，本调用方无需发送回复
            return

        response_text = result.get("response_text") or ""
        downloadable_files = result.get("downloadable_files") or []

        # 9. 审计
        try:
            db.write_audit(
                tenant_id=tenant_id,
                client_id=env.client_id,
                account_id=env.account_id,
                category=_AUDIT_AGENT_REPLY,
                payload_json=json.dumps(
                    {
                        "event_id": env.event_id,
                        "session_id": session_id,
                        "send_ok": bool(result.get("send_ok")),
                        "text_len": len(response_text),
                        "files": len(downloadable_files),
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception as e:
            logger.warning(f"RPA agent_reply 审计写入失败: {e}")

    except Exception as e:
        logger.error(
            f"RPA _process_inbound_message 异常 event_id={env.event_id}: {e}",
            exc_info=True,
        )
        # 不影响已返回的 accepted；尽量补一条审计
        try:
            db.write_audit(
                tenant_id=tenant_id,
                client_id=env.client_id,
                account_id=env.account_id,
                category=_AUDIT_INBOUND,
                payload_json=json.dumps(
                    {
                        "event_id": env.event_id,
                        "error": _sanitize_debug(str(e)),
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception:
            pass


# ===========================================================================
# 1.2 status 事件处理
# ===========================================================================


async def _handle_status_event(
    tenant_id: str, env: RpaCallbackEnvelope, env_raw: dict
) -> None:
    """处理账号 / 桌面状态上报：写账号状态 + 审计。"""
    try:
        status_payload = parse_status_event(env_raw)
    except Exception as e:
        logger.warning(f"RPA status 解析失败 event_id={env.event_id}: {e}")
        try:
            db.write_audit(
                tenant_id=tenant_id,
                client_id=env.client_id,
                account_id=env.account_id,
                category=_AUDIT_STATUS,
                payload_json=json.dumps(
                    {"event_id": env.event_id, "parse_error": _sanitize_debug(str(e))},
                    ensure_ascii=False,
                ),
            )
        except Exception:
            pass
        return

    try:
        # status='paused' 时携带 reason，便于人工排查
        paused_reason = None
        if status_payload.status == "paused":
            paused_reason = status_payload.detail or "paused"
        db.set_account_status(
            tenant_id=tenant_id,
            account_id=env.account_id,
            status=status_payload.status,
            paused_reason=paused_reason,
        )
    except Exception as e:
        logger.warning(f"RPA set_account_status 失败: {e}")

    try:
        db.write_audit(
            tenant_id=tenant_id,
            client_id=env.client_id,
            account_id=env.account_id,
            category=_AUDIT_STATUS,
            payload_json=json.dumps(
                {
                    "event_id": env.event_id,
                    "status": status_payload.status,
                    "detail": status_payload.detail,
                    # qr_image_ref 是短期凭证，禁止写入审计
                },
                ensure_ascii=False,
            ),
        )
    except Exception as e:
        logger.warning(f"RPA status 审计写入失败: {e}")


# ===========================================================================
# 1.3 action_result 事件处理
# ===========================================================================


async def _handle_action_result(
    tenant_id: str, env: RpaCallbackEnvelope, env_raw: dict
) -> None:
    """处理客户端 action 执行回执：幂等去重 → 更新 outbox 状态 → 审计。"""
    try:
        ar = parse_action_result(env_raw)
    except Exception as e:
        logger.warning(f"RPA action_result 解析失败 event_id={env.event_id}: {e}")
        return

    # action_result_id 维度去重
    dedup = MessageDeduplicator()
    dedup_key = f"{_DEDUP_PREFIX}:{tenant_id}:{ar.action_result_id}"
    if await dedup.is_duplicate(dedup_key):
        logger.info(
            f"RPA action_result 重复已跳过: ar_id={ar.action_result_id}"
        )
        return

    # 按 request_id 找到 outbox 行（db.mark_outbox_status 接收主键 id）
    outbox_status = "succeeded" if ar.success else "failed"
    error_message = ar.error_message if not ar.success else None
    marked = False
    try:
        # list_outbox 不支持按 request_id 直接过滤，拉取该租户近窗 pending/running 行匹配
        # 优先匹配 pending / running，命中后用主键 id 更新
        for candidate_status in ("running", "pending"):
            rows = db.list_outbox(
                tenant_id=tenant_id,
                account_id=env.account_id,
                status=candidate_status,
                limit=200,
            )
            target = next(
                (r for r in rows if r.get("request_id") == ar.request_id), None
            )
            if target:
                db.mark_outbox_status(
                    action_id=target["id"],
                    status=outbox_status,
                    error_message=error_message,
                )
                marked = True
                break
        if not marked:
            # 回执先于 outbox 入队或已终态：记 info，不阻断
            logger.info(
                f"RPA action_result 未匹配到 running/pending outbox 行 "
                f"request_id={ar.request_id}"
            )
    except Exception as e:
        logger.error(f"RPA mark_outbox_status 失败 request_id={ar.request_id}: {e}")

    try:
        db.write_audit(
            tenant_id=tenant_id,
            client_id=env.client_id,
            account_id=env.account_id,
            category=_AUDIT_ACTION_RESULT,
            payload_json=json.dumps(
                {
                    "event_id": env.event_id,
                    "request_id": ar.request_id,
                    "action_result_id": ar.action_result_id,
                    "action_index": ar.action_index,
                    "action_type": ar.action_type,
                    "success": ar.success,
                    "error_code": ar.error_code,
                    "marked": marked,
                },
                ensure_ascii=False,
            ),
        )
    except Exception as e:
        logger.warning(f"RPA action_result 审计写入失败: {e}")


# ===========================================================================
# 2. 配置下发
# ===========================================================================


@router.get("/api/v1/channels/wecom-personal-rpa/config")
async def wecom_personal_rpa_config(request: Request):
    """下发客户端运行配置。鉴权同 callback（需 X-Client-Id 等头）。"""
    raw_body = await request.body()  # GET 通常为空，但签名串仍按原始字节构造
    headers = dict(request.headers)

    # /config 鉴权：客户端首启时还不知道自己的 tenant_id（要从响应里拿），
    # 所以只用 client_id 反查 + HMAC 签名校验，不强制 tenant_id 匹配。
    get_secret = _make_get_secret_by_client_id()
    vr = auth.verify_request(headers, raw_body, get_secret)
    if not vr.ok:
        return _error_response(
            error=vr.error or "auth_failed",
            message="鉴权失败",
            status_code=401,
        )

    # vr.client_id 已校验通过；读取该 client 的归属租户与账号
    try:
        client = db.get_client(vr.client_id)
    except Exception as e:
        logger.error(f"RPA config db.get_client 失败: {e}")
        return _error_response(
            error="internal_error",
            message="服务端内部错误",
            status_code=500,
        )
    if not client:
        return _error_response(
            error="auth_failed", message="客户端不存在", status_code=401
        )
    tenant_id = client.get("tenant_id")
    if not tenant_id:
        # 客户端注册时必须有 tenant_id；缺失说明数据异常，应该报错而不是兜底
        logger.error(f"RPA config client {vr.client_id} 缺少 tenant_id 字段，数据异常")
        return _error_response(
            error="internal_error",
            message="客户端配置异常：缺少 tenant_id",
            status_code=500,
        )

    # 取该 client 下第一个账号（首版单账号托管）
    paused = False
    try:
        accounts = db.list_accounts(tenant_id, client_id=vr.client_id)
        if accounts:
            acct = accounts[0]
            paused = acct.get("status") in ("paused", "offline", "need_login")
    except Exception as e:
        logger.warning(f"RPA config list_accounts 失败: {e}")

    # 只下发明确归属于当前已鉴权客户端的渠道配置，禁止把同租户其他客户端的
    # config_id 泄漏给当前客户端。新协议统一下发稳定的业务 config_id（chan_*）。
    # listen_mode 第一期强制 'server'（codec 已写入），客户端据此跳过本地 ChatArchiveListener
    config_id = None
    listen_mode = "server"  # 默认 server（无 tenant_channel_configs 配置时也走 server）
    try:
        from src.saas.db.channel_config_db import ChannelConfigDB

        configs = ChannelConfigDB.list_by_tenant(tenant_id, channel_type=_CHANNEL_TYPE)
        owned_config = next(
            (
                item
                for item in configs
                if (item.get("config") or {}).get("client_id") == vr.client_id
            ),
            None,
        )
        if owned_config:
            config_id = owned_config.get("config_id")
            # 注意：list_by_tenant 返回的 config 字段是 mask 后的（敏感字段掩码），
            # 但 listen_mode 是明文字段，可直接读取
            config_data = owned_config.get("config") or {}
            lm = config_data.get("listen_mode")
            if lm in ("server", "client"):
                listen_mode = lm
    except Exception as e:
        logger.warning(
            f"RPA config 查询 tenant_channel_configs 失败 tenant={tenant_id}: {e}"
        )

    resp = RpaConfigResponse(
        protocol_version=PROTOCOL_VERSION,
        min_client_version=client.get("min_version") or "1.0.0",
        paused=paused,
        paused_scope="account" if paused else None,
        rate_limits=RpaRateLimits(),
        server_time=datetime.now(),
        client_id=vr.client_id,
        tenant_id=tenant_id,
        config_id=config_id,
        archive_enabled=_archive_enabled(),
        listen_mode=listen_mode,
        monitor_users=_build_monitor_users(tenant_id, vr.client_id),
    )
    # 直接返回 Pydantic 模型，由 FastAPI 的 jsonable_encoder 序列化 datetime
    return resp


def _archive_enabled() -> bool:
    """会话存档开关：环境变量 WECOM_RPA_ARCHIVE_ENABLED（默认 false）。"""
    return os.getenv("WECOM_RPA_ARCHIVE_ENABLED", "false").lower() == "true"


def _build_monitor_users(tenant_id: str, client_id: str) -> Dict[str, MonitorUsersEntry]:
    """组装绑定级监控白名单 dict（key = binding_id）。"""
    monitor_users: Dict[str, MonitorUsersEntry] = {}
    try:
        bindings = db.list_bindings_by_client(tenant_id, client_id)
    except Exception as e:
        logger.warning(
            f"RPA config list_bindings_by_client 失败 tenant={tenant_id} client={client_id}: {e}"
        )
        return monitor_users
    for b in bindings or []:
        names = b.get("monitor_user_names") or []
        ids = b.get("monitor_user_ids") or []
        # 仅在白名单非空时下发，节省客户端缓存
        if not names and not ids:
            continue
        binding_id = b.get("id")
        if not binding_id:
            continue
        monitor_users[binding_id] = MonitorUsersEntry(
            user_names=list(names),
            user_ids=list(ids),
        )
    return monitor_users


# ===========================================================================
# 2.5 媒体文件上传（客户端会话存档拿到的图片/文件）
# ===========================================================================


@router.post("/api/v1/channels/wecom-personal-rpa/media-upload")
async def upload_media(request: Request, file: UploadFile = File(...)):
    """客户端上传媒体文件（会话存档拿到的图片/文件），返回 24h 短期签名 URL。

    鉴权：复用 HMAC-SHA256（与 callback 同款头），body 用固定占位串 ``"media-upload"``。
    文件保存到 ``storage/tenants/{tenant_id}/conversation/`` 下，
    返回 24 小时有效的下载 URL（复用 wecom_personal_rpa_download_file 签名机制）。
    """
    # 1. HMAC 鉴权
    raw_body = b"media-upload"
    headers = dict(request.headers)
    get_secret = _make_get_secret_by_client_id()
    vr = auth.verify_request(headers, raw_body, get_secret)
    if not vr.ok:
        return _error_response(
            error=vr.error or "auth_failed",
            message="鉴权失败",
            status_code=401,
        )
    client_id = vr.client_id or ""

    # 2. 解析租户
    try:
        client = db.get_client(client_id)
    except Exception as e:
        logger.error(f"RPA media-upload db.get_client 失败: {e}")
        return _error_response(
            error="internal_error",
            message="服务端内部错误",
            status_code=500,
        )
    if not client:
        return _error_response(
            error="auth_failed", message="客户端不存在", status_code=401
        )
    tenant_id = client.get("tenant_id") or ""
    if not tenant_id:
        return _error_response(
            error="auth_failed", message="客户端无租户归属", status_code=401
        )

    # 3. 大小预检（content-length 头）
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MEDIA_MAX_SIZE_BYTES + 1024:
        return _error_response(
            error="bad_request",
            message=f"文件大小超过上限 {_MEDIA_MAX_SIZE_BYTES} 字节",
            status_code=413,
        )

    # 4. 流式读取并校验大小
    from src.core.storage import ensure_tenant_storage_dir

    original_name = file.filename or "media"
    # 文件扩展名白名单：拒绝 .exe/.bat/.ps1/.js 等危险类型
    ext = os.path.splitext(original_name)[1].lower()
    # ext 形如 ".png"；去点后查白名单。无扩展名（ext == ""）也拒绝。
    ext_clean = ext[1:] if ext.startswith(".") else ext
    if ext_clean not in _MEDIA_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(_MEDIA_ALLOWED_EXTENSIONS))
        return _error_response(
            error="unsupported_file_type",
            message=f"不支持的文件类型：{ext or '(无扩展名)'}，允许：{allowed}",
            status_code=400,
        )
    # 文件名：{uuid12}_{original}，防冲突 + 保留原文件名
    safe_orig = os.path.basename(original_name).replace(os.sep, "_")[:64]
    file_uuid = uuid.uuid4().hex[:12]
    stored_filename = f"{file_uuid}_{safe_orig}"
    try:
        storage_dir = ensure_tenant_storage_dir(tenant_id, "conversation")
    except Exception as e:
        logger.error(f"RPA media-upload ensure storage dir 失败: {e}")
        return _error_response(
            error="internal_error",
            message="存储初始化失败",
            status_code=500,
        )
    abs_path = os.path.join(storage_dir, stored_filename)

    total = 0
    try:
        with open(abs_path, "wb") as out:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MEDIA_MAX_SIZE_BYTES:
                    out.close()
                    try:
                        os.remove(abs_path)
                    except OSError:
                        pass
                    return _error_response(
                        error="bad_request",
                        message=f"文件大小超过上限 {_MEDIA_MAX_SIZE_BYTES} 字节",
                        status_code=413,
                    )
                out.write(chunk)
    except Exception as e:
        logger.error(f"RPA media-upload 写文件失败: {e}", exc_info=True)
        # 清理半成品
        try:
            if os.path.isfile(abs_path):
                os.remove(abs_path)
        except OSError:
            pass
        return _error_response(
            error="internal_error",
            message="文件保存失败",
            status_code=500,
        )

    # 5. 生成 24h 短期签名 URL
    expires = int(time.time()) + _MEDIA_TOKEN_TTL_SECONDS
    sig_token = _sign_file_token(stored_filename, tenant_id, expires)
    download_url = (
        f"/api/v1/channels/wecom-personal-rpa/files/{stored_filename}"
        f"?tenant_id={tenant_id}&sig={sig_token}"
    )
    expires_at = datetime.fromtimestamp(expires)

    logger.info(
        f"RPA media-upload ok client={client_id} tenant={tenant_id} "
        f"file={stored_filename} size={total}"
    )

    return {
        "file_id": stored_filename,
        "url": download_url,
        "expires_at": expires_at,
        "size": total,
    }


# ===========================================================================
# 3. 文件短期签名下载
# ===========================================================================


def _file_token_secret() -> bytes:
    """文件下载签名主密钥。

    优先环境变量 RPA_FILE_TOKEN_SECRET；缺失则回退到 RPA_SECRET_KEY（与
    secret_crypto 共用主密钥，避免新增必填环境变量阻断首版部署）。
    """
    secret = os.environ.get(_FILE_TOKEN_SECRET_ENV) or os.environ.get("RPA_SECRET_KEY")
    if not secret:
        raise RuntimeError(
            f"文件下载签名主密钥未配置：请设置 {_FILE_TOKEN_SECRET_ENV} 或 RPA_SECRET_KEY"
        )
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _sign_file_token(file_id: str, tenant_id: str, expires: int) -> str:
    """生成 file_id 的短期签名 token：``{expires}.{hmac_hex}``。"""
    secret = _file_token_secret()
    msg = f"{file_id}:{tenant_id}:{expires}".encode("utf-8")
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def _verify_file_token(token: str, file_id: str, tenant_id: str) -> bool:
    """校验签名 token 并检查 TTL。"""
    if not token or "." not in token:
        return False
    expires_str, sig = token.split(".", 1)
    try:
        expires = int(expires_str)
    except ValueError:
        return False
    if int(time.time()) > expires:
        return False
    expected = _sign_file_token(file_id, tenant_id, expires)
    return hmac.compare_digest(expected, token)


@router.get(
    "/api/v1/channels/wecom-personal-rpa/files/{file_id}",
)
async def wecom_personal_rpa_download_file(
    file_id: str, request: Request
):
    """短期签名下载文件。

    签名 token 通过 query 参数 ``sig`` 传入（hmac 自签，TTL ≈600s）。
    tenant_id 通过 query 参数 ``tenant_id`` 传入（文件路径定位）。
    文件位于 ``storage/tenants/{tenant_id}/`` 下各场景子目录。
    """
    query = dict(request.query_params)
    tenant_id = query.get("tenant_id", "")
    sig_token = query.get("sig", "")
    if not tenant_id or not sig_token:
        return _error_response(
            error="bad_request",
            message="缺少 tenant_id 或 sig 参数",
            status_code=400,
        )

    if not _verify_file_token(sig_token, file_id, tenant_id):
        return _error_response(
            error="auth_failed",
            message="文件签名无效或已过期",
            status_code=401,
        )

    # 在租户存储目录下递归查找 file_id（文件名包含 file_id）
    # 首版按已知场景子目录检索；命中即返回
    candidate_scenes = ("conversation", "export", "report", "knowledge")
    for scene in candidate_scenes:
        abs_path = get_tenant_storage_abs_path(tenant_id, scene, file_id)
        if os.path.isfile(abs_path):
            return FileResponse(abs_path, filename=file_id)

    return _error_response(
        error="bad_request",
        message="文件不存在",
        status_code=404,
    )


# ===========================================================================
# 4. WebSocket 长连接
# ===========================================================================


@router.websocket(
    "/t/{tenant_id}/wecom_personal_rpa/ws/{config_id}",
)
async def wecom_personal_rpa_ws(
    websocket: WebSocket, tenant_id: str, config_id: str
):
    """在线客户端 WebSocket 长连接。

    握手从 query 取 client_id/timestamp/nonce/signature，``auth.verify_request``
    校验（body 用空串）。通过后注册到 ``client_connection_registry``，
    循环接收心跳更新 last_seen，断开时 unregister。
    建立后把该 client 的 pending outbox 推送一遍。
    """
    query = dict(websocket.query_params)
    client_id = query.get("client_id", "")
    timestamp = query.get("timestamp", "")
    nonce = query.get("nonce", "")
    signature = query.get("signature", "")

    # 握手头构造（auth.verify_request 按固定头名取值）
    headers = {
        "X-Client-Id": client_id,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }
    get_secret = _make_get_secret(tenant_id)
    vr = auth.verify_request(headers, "", get_secret)
    if not vr.ok:
        logger.info(
            f"RPA ws 握手鉴权失败 tenant={tenant_id} client={client_id} "
            f"error={vr.error}"
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # config_id 与租户必须匹配。已有归属只能由后台管理员修改，不能允许同租户内
    # 任意持有合法密钥的客户端通过连接 URL 劫持另一个渠道配置。
    from src.saas.db.channel_config_db import ChannelConfigDB

    channel_config = ChannelConfigDB.resolve_by_tenant_reference(
        tenant_id, _CHANNEL_TYPE, config_id
    )
    if not channel_config:
        logger.info(
            f"RPA ws 配置不匹配 tenant={tenant_id} client={client_id} config={config_id}"
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    authenticated_client_id = vr.client_id or client_id
    configured_client_id = (channel_config.get("config") or {}).get("client_id")
    if configured_client_id and configured_client_id != authenticated_client_id:
        logger.warning(
            f"RPA ws 客户端与配置归属不匹配 tenant={tenant_id} "
            f"client={authenticated_client_id} config={config_id}"
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    resolved_config_id = channel_config.get("config_id")
    if not resolved_config_id:
        logger.error(
            f"RPA ws 配置缺少业务 ID tenant={tenant_id} config={config_id}"
        )
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return
    if not configured_client_id and not ChannelConfigDB.claim_client_if_unowned(
        tenant_id, _CHANNEL_TYPE, resolved_config_id, authenticated_client_id
    ):
        logger.warning(
            f"RPA ws 更新配置 client_id 失败 tenant={tenant_id} config={config_id}"
        )
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    # 建连时即建立稳定的 tenant-namespaced account → client 映射，避免必须等到
    # archive poller 首次拉取后才能投递。subagent_type 不参与账号 ID。
    from src.channels.wecom_personal_rpa.archive.fetcher import ServerArchiveFetcher

    account_config = dict(channel_config)
    account_config["config_id"] = resolved_config_id
    account_id = ServerArchiveFetcher._infer_account_id(tenant_id, account_config)
    config_data = channel_config.get("config") or {}
    if not account_id or not db.upsert_account(
        tenant_id=tenant_id,
        client_id=authenticated_client_id,
        account_id=account_id,
        display_name=(
            config_data.get("account_display_name")
            or config_data.get("account_id")
            or channel_config.get("name")
            or account_id
        ),
    ):
        logger.warning(
            f"RPA ws 建立账号映射失败 tenant={tenant_id} client={authenticated_client_id} "
            f"config={config_id}"
        )
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    await websocket.accept()
    await client_connection_registry.register(vr.client_id or client_id, websocket)
    logger.info(
        f"RPA ws 已连接 tenant={tenant_id} client={vr.client_id} config={config_id}"
    )

    # 推送 pending outbox（首版只推当前 worker 可见的在线连接）
    await _push_pending_outbox(tenant_id, vr.client_id or client_id, websocket)

    try:
        while True:
            # 心跳兼容文本和二进制帧；receive_text() 遇二进制帧会触发 KeyError。
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                logger.info(f"RPA ws 断开 client={vr.client_id}")
                break
            if message["type"] != "websocket.receive":
                continue
            try:
                db.update_last_seen(tenant_id, vr.client_id or client_id)
            except Exception as e:
                logger.debug(f"RPA ws update_last_seen 失败: {e}")
    except WebSocketDisconnect:
        logger.info(f"RPA ws 断开 client={vr.client_id}")
    except Exception:
        logger.exception(f"RPA ws 异常断开 client={vr.client_id}")
    finally:
        await client_connection_registry.unregister(vr.client_id or client_id)


async def _push_pending_outbox(
    tenant_id: str, client_id: str, websocket: WebSocket
) -> None:
    """连接建立后把该 client 名下 pending outbox 推送一遍。

    只推该 client 关联账号的 pending 行；投递后状态由 action_result 回执更新，
    此处不抢占标记（保持 pending，由客户端回执驱动状态流转）。
    """
    try:
        accounts = db.list_accounts(tenant_id, client_id=client_id)
    except Exception as e:
        logger.warning(f"RPA ws 推送 outbox list_accounts 失败: {e}")
        return

    for acct in accounts or []:
        account_id = acct.get("id")
        if not account_id:
            continue
        try:
            pending_rows = db.list_outbox(
                tenant_id=tenant_id,
                account_id=account_id,
                status="pending",
                limit=100,
            )
        except Exception as e:
            logger.warning(f"RPA ws list_outbox 失败 account={account_id}: {e}")
            continue
        for row in pending_rows or []:
            envelope = {
                "type": "actions",
                "request_id": row.get("request_id"),
                "session_id": row.get("session_id"),
                "account_id": row.get("account_id"),
                "conversation_id": row.get("conversation_id"),
                "actions": row.get("actions") or [],
            }
            if row.get("reply_context"):
                envelope["reply_context"] = row["reply_context"]
            try:
                payload = json.dumps(envelope, ensure_ascii=False)
                await websocket.send_text(payload)
            except Exception as e:
                logger.info(
                    f"RPA ws 推送 outbox 失败 request_id={row.get('request_id')}: "
                    f"{type(e).__name__}"
                )
                # 连接已失效，停止后续推送
                return
