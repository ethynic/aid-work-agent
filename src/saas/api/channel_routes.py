"""
租户级渠道回调路由

路由：/t/{tenant_id}/wecom/callback/{config_id} 等
从 URL path 取 tenant_id + config_id → 查渠道配置构造 adapter → 处理消息。

多租户 WeCom 集成关键点:
- GET  /t/{tenant_id}/wecom/callback/{config_id}: 验签 + 解密 echostr
- POST /t/{tenant_id}/wecom/callback/{config_id}: 验签 + 解密消息 → 立即返回 "success" → 后台异步处理
- 每个租户可配置多个 wecom 渠道（多个自建应用），通过 config_id 区分
- 每个渠道配置可关联 subagent_type，消息路由到对应数字员工
- 消息去重防止 WeCom 回调重试
"""

import asyncio
import base64
import json
import os
import random
import time
import xml.etree.ElementTree as ET
from typing import Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from loguru import logger

from src.saas.db.tenant_db import TenantDB
from src.saas.db.channel_config_db import ChannelConfigDB
from src.saas.services.channel_factory import ChannelFactory
from src.channels.session import channel_session_manager
from src.channels.verbose_dispatcher import resolve_verbose_feedback_config
from src.channels.wecom_kf.budget import WeComKfReplyBudget
from src.channels.idempotency import MessageDeduplicator
from src.services.session_record import SessionRecordManager
from src.core.storage import ensure_tenant_storage_dir, get_tenant_storage_path
from src.core.temp_logger import tlog as _tlog
from src.db.models import CustomerReferralDB
from src.channels.wecom_kf.prompts import (
    MSG_EXPIRED,
    MSG_CREDIT_EXHAUSTED,
)
from src.saas.api.wecom_kf_account import resolve_scene


def _kf_tlog(message: str, **kwargs) -> None:
    """微信客服回调链路统一主题日志，便于在 log/temp/微信客服回调.log 中排查"""
    try:
        _tlog("微信客服回调", message, **kwargs)
    except Exception:
        pass


def _has_successful_transfer_to_human(tool_messages: list) -> bool:
    """检查本轮 tool 消息序列中是否包含成功的 transfer_to_human 调用。

    转人工成功后微信客服会话切到人工状态，任何后续机器人消息会被
    微信客服接口拒绝（errcode=95018）。此判定用于拦截 LLM 在
    transfer_to_human 之后追加的多余文字回复。
    """
    if not tool_messages:
        return False
    transfer_call_ids = set()
    for tm in tool_messages:
        if tm.get("role") == "assistant" and tm.get("tool_calls"):
            for tc in tm["tool_calls"]:
                fn = tc.get("function") or {}
                if fn.get("name") == "transfer_to_human":
                    call_id = tc.get("id")
                    if call_id:
                        transfer_call_ids.add(call_id)
    if not transfer_call_ids:
        return False
    for tm in tool_messages:
        if tm.get("role") != "tool":
            continue
        if tm.get("tool_call_id") not in transfer_call_ids:
            continue
        content = tm.get("content")
        if isinstance(content, str):
            try:
                data = json.loads(content)
            except Exception:
                continue
        elif isinstance(content, dict):
            data = content
        else:
            continue
        if data.get("success") is True:
            return True
    return False
from src.core.session_queue import session_queue

router = APIRouter(tags=["租户渠道回调"])

# 每个租户有独立的消息去重器
_tenant_dedup_cache: dict[str, MessageDeduplicator] = {}

# 旧消息过滤阈值：超过 30 分钟的消息没有回复价值（客户早已离开，回复大概率发不出），
# cursor 到期/回调缺失积压时 sync_msg 会重放 3 天窗口内全部消息，从源头丢弃避免 95013 刷屏。
OLD_MESSAGE_MAX_AGE = 1800  # 30 分钟（秒）


def _get_tenant_dedup(tenant_id: str) -> MessageDeduplicator:
    """获取或创建租户级别的消息去重器"""
    if tenant_id not in _tenant_dedup_cache:
        _tenant_dedup_cache[tenant_id] = MessageDeduplicator(ttl_seconds=3600)
    return _tenant_dedup_cache[tenant_id]


def _is_stale_kf_message(msg: dict) -> bool:
    """send_time 超过 OLD_MESSAGE_MAX_AGE 视为过期（无回复价值）。

    客户消息（origin=3）与员工消息（origin=5）共用同一过滤口径，
    防止 cursor 丢失全量重放时旧员工消息污染上下文。
    """
    send_time = msg.get("send_time")
    if not send_time:
        return False
    try:
        return (time.time() - int(send_time)) > OLD_MESSAGE_MAX_AGE
    except (TypeError, ValueError):
        return False


def _merge_consecutive_user_messages(msg_list: list) -> list:
    """
    合并同一批次中同一用户连续发送的文本消息。

    WeCom 客服规则：用户每发一条消息，企业可回复 5 条。
    如果用户快速连发多条消息，系统逐条回复会耗尽 5 条限额（errcode=95001）。
    将同一用户连续的文本消息合并为一条可避免此问题。

    非文本消息（图片、语音等）不合并，保持独立处理。
    """
    if not msg_list:
        return []

    merged = []
    current_group = [msg_list[0]]

    for msg in msg_list[1:]:
        prev = current_group[-1]
        same_user = msg.get("external_userid") == prev.get("external_userid")
        both_text = msg.get("msgtype") == "text" and prev.get("msgtype") == "text"

        if same_user and both_text:
            current_group.append(msg)
        else:
            merged.append(_build_merged_message(current_group))
            current_group = [msg]

    merged.append(_build_merged_message(current_group))
    return merged


def _build_merged_message(group: list) -> dict:
    """将一组消息合并为一条。单条消息直接返回。

    合并消息保留 merged_from_msgids 和 merged_segments，供撤回时按段重建。
    """
    if len(group) == 1:
        return group[0]

    lines = []
    for msg in group:
        content = msg.get("text", {}).get("content", "")
        if content:
            lines.append(content)

    merged_msg = group[-1].copy()
    merged_msg["text"] = {"content": "\n".join(lines)}
    # 保留所有被合并的 msgid 和分段，供撤回时按段重建 content
    merged_msg["merged_from_msgids"] = [m.get("msgid", "") for m in group]
    merged_msg["merged_segments"] = [
        {"msgid": m.get("msgid", ""), "text": m.get("text", {}).get("content", "")}
        for m in group
    ]
    return merged_msg


# 文件扩展名 → MIME 类型映射
_MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
    ".mp3": "audio/mpeg", ".amr": "audio/amr", ".wav": "audio/wav",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".zip": "application/zip",
}


def _guess_mime_type(filename: str) -> str:
    """根据文件扩展名推断 MIME 类型"""
    ext = os.path.splitext(filename)[1].lower()
    return _MIME_MAP.get(ext, "application/octet-stream")


def _get_attachment_type(msgtype: str) -> str:
    """将微信消息类型映射为附件类型"""
    type_map = {
        "image": "image",
        "voice": "voice",
        "video": "video",
        "file": "file",
    }
    return type_map.get(msgtype, "file")


def _get_file_extension(msgtype: str, filename: str = "") -> str:
    """根据消息类型和文件名推断文件扩展名（仅作参考，最终由 magic bytes 修正）"""
    ext_map = {
        "image": ".jpg",
        "voice": ".wav",  # WeCom 微信客服 sync_msg voice_format=1 返回 PCM 16kHz，包装为 WAV
        "video": ".mp4",
        "file": "",  # 由文件名决定
    }
    if msgtype == "file" and filename:
        return os.path.splitext(filename)[1].lower()
    return ext_map.get(msgtype, "")


async def _download_and_build_attachments(
    api_client, msg: dict, session_id: str, tenant_id: str
) -> list:
    """
    下载微信媒体文件并构建附件列表。

    按 `backend_dev.md` 租户附件存储规范保存到：
        `storage/tenants/{tenant_id}/conversation/{filename}`

    `tenant_id` 为必填，缺失说明上游路由有 bug，直接 raise 让问题暴露。

    Returns:
        [{
            "type": "image|voice|file",
            "media_id": "xxx",
            "file_name": "xxx.jpg",
            "mime_type": "image/jpeg",
            "file_size": 12345,
            "content": "<base64>",  # 用于传递给 agent
            "local_path": "storage/tenants/{tenant_id}/conversation/...",  # 用于持久化
            "saved_at": "2026-06-14 12:00:00",
        }]
    """
    from datetime import datetime

    msgtype = msg.get("msgtype", "")
    media_item = msg.get(msgtype, {})
    media_id = media_item.get("media_id", "")

    if not media_id:
        return []
    try:
        content, content_type = await api_client.download_media(media_id)
    except Exception as e:
        logger.warning(f"[_download_and_build_attachments] 下载失败: media_id={media_id}, error_type={type(e).__name__}, error={e}")
        return []

    # 推断文件信息
    att_type = _get_attachment_type(msgtype)

    # 语音消息：通过 magic bytes 识别真实格式，避免 WeCom 标记错误
    detected_format = None
    detected_sample_rate = None
    if msgtype == "voice":
        from src.utils.audio_format import (
            detect_audio_format,
            wrap_pcm_as_wav,
            decode_silk_to_wav,
        )
        detected_format, detected_sample_rate = detect_audio_format(content, content_type)
        # SILK 是微信手机端专有格式（阿里云 ASR 不支持），需解码为 WAV
        if detected_format in ("silk_v3", "silk_v2"):
            try:
                content = decode_silk_to_wav(content, sample_rate=16000)
                detected_format = "wav"
                detected_sample_rate = 16000
                logger.info("[wecom_kf] SILK 语音已解码为 WAV: media_id={}", media_id[:8])
            except Exception as e:
                logger.warning("[wecom_kf] SILK 解码失败，保留原格式: media_id={}, error={}", media_id[:8], e)
        # voice_format=1 请求返回原始 PCM（无文件头），magic bytes 检测会 fallback 为 mp3/pcm，
        # 此处通过排除法识别：既不是已知有头格式、也不是 Content-Type 匹配到的格式时，
        # 视为来自微信 voice_format=1 的原始 PCM，包装为 WAV 以便浏览器播放和 ASR 提交。
        elif detected_format not in {"amr", "amr-wb", "wav", "mp3", "opus", "aac", "silk_v3", "silk_v2", "flac"}:
            content = wrap_pcm_as_wav(content, sample_rate=detected_sample_rate or 16000)
            detected_format = "wav"
            detected_sample_rate = 16000
        # 实际扩展名以检测结果为准
        ext_for_format = {
            "amr": ".amr",
            "amr-wb": ".awb",
            "wav": ".wav",
            "mp3": ".mp3",
            "opus": ".opus",
            "pcm": ".pcm",
            "aac": ".aac",
            "silk_v3": ".silk",
            "silk_v2": ".silk",
        }
        file_ext = ext_for_format.get(detected_format, ".wav")
    else:
        file_ext = _get_file_extension(msgtype, media_item.get("filename", ""))

    # 文件名：图片/语音没有文件名，使用默认名
    if msgtype == "image":
        file_name = f"image_{media_id[:8]}.jpg"
    elif msgtype == "voice":
        # 使用检测到的扩展名（默认 .wav）
        file_name = f"voice_{media_id[:8]}{file_ext}"
    elif msgtype == "video":
        file_name = f"video_{media_id[:8]}.mp4"
    else:
        file_name = media_item.get("filename", f"file_{media_id[:8]}{file_ext}")
        if file_ext and not file_name.lower().endswith(file_ext):
            file_name += file_ext

    mime_type = _guess_mime_type(file_name)
    file_size = len(content)

    # 保存到磁盘：storage/tenants/{tenant_id}/conversation/{filename}
    # tenant_id 由路由 path 参数保证必填，缺失说明上游有 bug，直接报错暴露
    if not tenant_id:
        raise RuntimeError(
            f"[_download_and_build_attachments] tenant_id 缺失，session_id={session_id}；"
            f"租户渠道回调必须携带 tenant_id，请检查路由配置"
        )

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    safe_filename = f"{timestamp}_{media_id}{file_ext}"
    tenant_dir = ensure_tenant_storage_dir(tenant_id, "conversation")
    local_path = os.path.join(tenant_dir, safe_filename)

    try:
        with open(local_path, "wb") as f:
            f.write(content)
    except Exception as e:
        logger.error(f"[wecom_kf] 保存附件失败: {local_path}, error={e}")
        local_path = ""

    # base64 编码用于传递给 agent
    content_b64 = base64.b64encode(content).decode("ascii")

    result = [{
        "type": att_type,
        "media_id": media_id,
        "file_name": file_name,
        "mime_type": mime_type,
        "file_size": file_size,
        "content": content_b64,
        "local_path": local_path,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }]

    # 语音消息：附带检测到的格式与采样率，方便后续 ASR 使用
    if msgtype == "voice" and detected_format is not None:
        result[0]["audio_format"] = detected_format
        result[0]["sample_rate"] = detected_sample_rate

    return result


def _build_user_input_for_agent(msg: dict, attachments: list) -> str:
    """
    根据消息类型构建传递给 agent 的 user_input。

    - 文本：直接返回 content
    - 语音：优先使用 Recognition 文字识别结果
    - 图片/文件：使用描述文字
    """
    msgtype = msg.get("msgtype", "")

    if msgtype == "text":
        return msg.get("text", {}).get("content", "")
    elif msgtype == "voice":
        # 微信语音识别结果（如有）
        recognition = msg.get("voice", {}).get("recognition", "")
        return recognition or "[语音消息]"
    elif msgtype == "image":
        return "[图片消息]"
    elif msgtype == "file":
        filename = msg.get("file", {}).get("filename", "文件")
        return f"[文件: {filename}]"
    elif msgtype == "video":
        return "[视频消息]"
    else:
        return f"[{msgtype}消息]"


def _build_attachments_for_agent(attachments: list) -> list:
    """
    构建传递给 agent 的附件格式。

    Returns:
        [{"type": "image/file/voice", "name": "xxx.jpg", "content": "<base64>", "mime_type": "image/jpeg"}]
    """
    result = []
    for att in attachments:
        result.append({
            "type": att["type"],
            "name": att["file_name"],
            "content": att["content"],
            "mime_type": att["mime_type"],
        })
    return result


async def _transcribe_voice_with_asr(
    audio_content: str,
    audio_format: str = "wav",
    sample_rate: int = 16000,
) -> str:
    """
    使用语音转文字工具识别语音内容。
    当微信 Recognition 为空时调用此函数。

    Args:
        audio_content: base64 编码的音频内容
        audio_format: 音频格式，默认 wav（WeCom 微信客服 voice_format=1 返回 PCM，已包装为 WAV）
        sample_rate: 采样率，默认 16000（PCM 16kHz）

    Returns:
        识别出的文字，如果识别失败返回 "[语音消息]"
    """
    # 提前拦截 SILK 格式：阿里云 ASR 不支持，避免无谓的 400 调用
    from src.utils.audio_format import is_supported_by_aliyun, get_unsupported_reason
    if audio_format.startswith("silk"):
        reason = get_unsupported_reason(audio_format)
        logger.warning(
            "[wecom_kf] 语音格式 {} 不被阿里云 ASR 支持: {}",
            audio_format, reason,
        )
        return f"[语音消息 - {reason}]"

    try:
        from src.tools.asr.speech_to_text_tool import SpeechToTextTool
        tool = SpeechToTextTool()
        result = await tool.execute(
            audio_content=audio_content,
            format=audio_format,
            sample_rate=sample_rate,
        )
        if result.get("success"):
            return result.get("text", "")
        else:
            logger.warning("[wecom_kf] 语音转文字失败: {}", result.get("error"))
            return "[语音消息]"
    except Exception as e:
        logger.error("[wecom_kf] 调用 ASR 工具异常: {}", e)
        return "[语音消息]"


async def _process_tenant_wecom_background(
    tenant_id: str,
    message,
    config_id: Optional[str] = None,
    subagent_type: Optional[str] = None,
) -> None:
    """
    租户级 WeCom 消息后台异步处理

    在 asyncio.create_task 中执行，不阻塞回调响应。

    Args:
        tenant_id: 租户 ID
        message: 解析后的统一消息
        subagent_type: 关联的数字员工类型（如 travel-consultant），None 时使用 master_agent
    """
    adapter = None
    try:
        # 适配器已在调用方创建并通过 message 的 raw_message 间接使用
        # 这里需要独立的 adapter 用于发送回复
        adapter, _, _ = await ChannelFactory.create_from_tenant_config(tenant_id, "wecom", config_id=config_id)
        if not adapter:
            logger.error(f"[Tenant WeCom] adapter 不可用: tenant={tenant_id}")
            return

        # 自动注册用户
        user_id = None
        try:
            from src.saas.services.auto_register import ensure_user_registered
            user_id = await ensure_user_registered("wecom", message.user_id, tenant_id)
        except Exception as e:
            logger.warning(f"[Tenant WeCom] 自动注册失败: {e}")

        # 构建用户信息并创建/获取会话（带租户隔离）
        user_info = {"user_id": user_id, "name": getattr(message, 'username', None) or getattr(message, 'user_name', None)}
        session = channel_session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id=message.user_id,
            tenant_id=tenant_id,
            user_info=user_info,
            subagent_id=subagent_type or "",
        )
        session_id = session["session_id"]

        # 检查隐藏命令
        from src.core.hidden_commands import is_hidden_command, execute_hidden_command
        if message.text and is_hidden_command(message.text):
            reply_text = await execute_hidden_command(message.text, session_id, tenant_id)
            if reply_text:
                await adapter.send_text(reply_text, message.user_id)
            return

        # 记录用户消息 —— P0-2：推迟到 process_and_persist 内统一写入

        # 获取对话上下文
        history = channel_session_manager.get_conversation_context(
            session_id, max_messages=20
        )

        # 获取 agent：根据 subagent_type 路由到对应数字员工
        from src.core.agent_router import agent_router
        agent = agent_router.get_agent(subagent_type, session_id)

        # 开始记录 token 消耗
        record_service = SessionRecordManager.start_record(
            session_id=session_id,
            user_id=user_id,
            user_message=message.text,
            tenant_id=tenant_id,
            source_type="wecom",
        )
        record_service.set_model(agent.llm.get_model_name())
        record_service.set_provider(agent.llm.get_provider_name())

        # 处理消息 + 持久化（P0-1 / P0-2 统一在 process_and_persist 内完成）
        send_response = channel_session_manager.make_send_response(
            adapter=adapter,
            message_id=message.message_id,
            reply_to=message.user_id,
            log_tag="[Tenant WeCom]",
        )
        # verbose 中间消息（Phase 3，设计 §11）：配置优先级
        # 渠道 config.verbose_feedback > 全局 agent.verbose_feedback > 代码默认
        _verbose_cfg = resolve_verbose_feedback_config(
            channel_cfg=getattr(adapter, "verbose_feedback", None),
        )
        _send_verbose = channel_session_manager.make_send_verbose(
            adapter=adapter,
            event_id=message.message_id,
            reply_to=message.user_id,
            log_tag="[Tenant WeCom]",
        )

        from src.channels.agent_user_builder import build_agent_user_for_channel
        agent_user = await build_agent_user_for_channel(
            channel_type="wecom",
            channel_user_id=message.user_id,
            tenant_id=tenant_id,
            adapter=adapter,
            user_id=user_id,
        )

        result = await channel_session_manager.process_and_persist(
            session_id=session_id,
            tenant_id=tenant_id,
            user_content=message.text,
            user_metadata=message.raw_message,
            message_type=message.message_type,
            agent=agent,
            agent_user=agent_user,
            record_service=record_service,
            send_response=send_response,
            send_verbose=_send_verbose,
            verbose_feedback_config=_verbose_cfg,
        )

        SessionRecordManager.end_record()
        if result["status"] == "merged":
            return

    except Exception as e:
        logger.error(f"[Tenant WeCom] 后台处理失败: tenant={tenant_id}, error={e}")
        # 标记 record 为失败
        try:
            _record = SessionRecordManager.get_current_record()
            if _record:
                _record.mark_error(str(e))
                SessionRecordManager.end_record()
        except Exception:
            pass
        # 尝试发送错误提示
        try:
            if adapter and message.user_id:
                await adapter.send_text(
                    "抱歉，处理您的消息时遇到了问题，请稍后重试。",
                    message.user_id,
                )
        except Exception:
            pass


# ==================== WeCom 租户回调 ====================

@router.get("/t/{tenant_id}/wecom/callback/{config_id}")
async def tenant_wecom_callback_get(
    tenant_id: str,
    config_id: str,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """
    企业微信租户回调验证（按渠道配置）

    流程: 按 config_id 查配置 → 验签 → 解密 echostr → 返回明文
    """
    adapter, _, _ = await ChannelFactory.create_from_tenant_config(tenant_id, "wecom", config_id=config_id)
    if not adapter:
        logger.warning(f"[Tenant WeCom] 配置不存在: tenant={tenant_id}, config={config_id}")
        return PlainTextResponse("Config not found", status_code=404)

    # 加密模式: 验签 + 解密
    if adapter.crypto:
        if not adapter.crypto.verify_signature(msg_signature, timestamp, nonce, echostr):
            logger.warning(f"[Tenant WeCom] 签名验证失败: tenant={tenant_id}, config={config_id}")
            return PlainTextResponse("Invalid signature", status_code=403)

        try:
            plaintext = adapter.crypto.decrypt(echostr)
            logger.info(f"[Tenant WeCom] 回调验证成功: tenant={tenant_id}, config={config_id}")
            return PlainTextResponse(plaintext)
        except Exception as e:
            logger.error(f"[Tenant WeCom] echostr 解密失败: tenant={tenant_id}, config={config_id}, error={e}")
            return PlainTextResponse("Decryption failed", status_code=400)

    # 无加密模式: 简单验签
    if not await adapter.verify_signature(msg_signature, timestamp, nonce, echostr):
        return PlainTextResponse("Invalid signature", status_code=403)
    return PlainTextResponse(echostr)


@router.post("/t/{tenant_id}/wecom/callback/{config_id}")
async def tenant_wecom_callback_post(tenant_id: str, config_id: str, request: Request):
    """
    企业微信租户消息回调（按渠道配置）

    流程:
    1. 按 config_id 查配置 → 构造 adapter 验签 + 解密消息
    2. 去重检查
    3. 立即返回 "success"
    4. 后台异步处理（根据 subagent_type 路由到对应数字员工）
    """
    try:
        body = await request.body()
        body_str = body.decode()

        adapter, _, subagent_type = await ChannelFactory.create_from_tenant_config(
            tenant_id, "wecom", config_id=config_id
        )
        if not adapter:
            logger.warning(f"[Tenant WeCom] 配置不存在: tenant={tenant_id}, config={config_id}")
            return PlainTextResponse("Config not found", status_code=404)

        # 解析 XML 获取加密内容
        root = ET.fromstring(body_str)
        encrypt = root.findtext("Encrypt", "")
        msg_signature = root.findtext("MsgSignature", "")
        timestamp = root.findtext("TimeStamp", "")
        nonce = root.findtext("Nonce", "")

        # 获取查询参数中的签名（WeCom 可能在 query 或 XML 中传签名）
        query_params = dict(request.query_params)
        if not msg_signature:
            msg_signature = query_params.get("msg_signature", "")
        if not timestamp or timestamp == str(int(time.time())):
            timestamp = query_params.get("timestamp", str(int(time.time())))
        if not nonce:
            nonce = query_params.get("nonce", "")

        # 解密消息体
        if encrypt and adapter.crypto:
            if not adapter.crypto.verify_signature(
                msg_signature, timestamp, nonce, encrypt
            ):
                logger.warning(f"[Tenant WeCom] POST 签名验证失败: tenant={tenant_id}, config={config_id}")
                return PlainTextResponse("Invalid signature", status_code=403)

            try:
                decrypted_xml = adapter.crypto.decrypt(encrypt)
            except Exception as e:
                logger.error(f"[Tenant WeCom] 消息解密失败: tenant={tenant_id}, config={config_id}, error={e}")
                return PlainTextResponse("Decryption failed", status_code=400)
        else:
            decrypted_xml = body_str

        # 解析消息
        message = await adapter.parse_message({"body": decrypted_xml})

        # 事件消息不处理
        if message.message_type == "event":
            return PlainTextResponse("success")

        # 去重检查
        dedup = _get_tenant_dedup(tenant_id)
        if await dedup.is_duplicate(message.message_id):
            logger.debug(f"[Tenant WeCom] 重复消息: tenant={tenant_id}, msg={message.message_id}")
            return PlainTextResponse("success")

        # 立即返回 "success"，后台异步处理
        asyncio.create_task(
            _process_tenant_wecom_background(tenant_id, message, config_id=config_id, subagent_type=subagent_type)
        )

        return PlainTextResponse("success")

    except Exception as e:
        logger.error(f"[Tenant WeCom] POST 处理异常: tenant={tenant_id}, config={config_id}, error={e}")
        return PlainTextResponse("error", status_code=500)


# ==================== DingTalk 租户回调 ====================

# 钉钉事件去重器（按 tenant_id 隔离，TTL 5 分钟）
_dingtalk_event_dedup: dict[str, MessageDeduplicator] = {}

# 后台任务引用集合：保留强引用避免被 GC 回收，任务完成后自动移除
_dingtalk_background_tasks: set = set()


def _get_dingtalk_event_dedup(tenant_id: str) -> MessageDeduplicator:
    """获取或创建钉钉事件去重器"""
    if tenant_id not in _dingtalk_event_dedup:
        _dingtalk_event_dedup[tenant_id] = MessageDeduplicator(ttl_seconds=300)
    return _dingtalk_event_dedup[tenant_id]


@router.get("/t/{tenant_id}/dingtalk/callback/{config_id}")
async def tenant_dingtalk_callback_get(tenant_id: str, config_id: str):
    """
    钉钉回调 GET 兜底

    钉钉机器人回调 URL 验证不依赖 GET（与企微不同）。
    此路由仅作兼容保留，返回 200 即可。config_id 路径段仅用于路由匹配，不影响返回内容。
    """
    return JSONResponse({"status": "ok"})


@router.post("/t/{tenant_id}/dingtalk/callback/{config_id}")
async def tenant_dingtalk_callback_post(
    tenant_id: str, config_id: str, request: Request
):
    """
    钉钉机器人消息回调统一入口

    流程:
    1. 按 config_id 查配置 → 构造 adapter
    2. 读取 timestamp/sign 请求头 → HmacSHA256 签名验证
    3. 解析 JSON body
    4. 按 msgId 去重（5 分钟 TTL）
    5. 立即返回 {"success": True} → 后台 asyncio.create_task 异步处理
    """
    try:
        body = await request.body()
        body_str = body.decode()

        adapter, _, subagent_type = await ChannelFactory.create_from_tenant_config(
            tenant_id, "dingtalk", config_id=config_id
        )
        if not adapter:
            logger.warning(
                f"[Tenant DingTalk] 配置不存在: tenant={tenant_id}, config={config_id}"
            )
            return JSONResponse({"success": False, "msg": "config not found"}, status_code=404)

        # 1. 签名验证（timestamp + sign 请求头）
        # 钉钉强制要求签名校验：sign 或 timestamp 头缺失直接拒绝，禁止跳过验证
        signature = request.headers.get("sign", "")
        timestamp = request.headers.get("timestamp", "")
        if not signature or not timestamp:
            logger.warning(
                f"[Tenant DingTalk] 缺少签名头: tenant={tenant_id}, "
                f"has_sign={bool(signature)}, has_timestamp={bool(timestamp)}"
            )
            return JSONResponse(
                {"success": False, "msg": "missing signature headers"},
                status_code=403,
            )
        if not await adapter.verify_signature(signature, timestamp, "", ""):
            logger.warning(f"[Tenant DingTalk] 签名验证失败: tenant={tenant_id}")
            return JSONResponse({"success": False, "msg": "invalid signature"}, status_code=403)

        # 2. 解析 JSON body
        try:
            data = json.loads(body_str)
        except json.JSONDecodeError as e:
            logger.warning(f"[Tenant DingTalk] body JSON 解析失败: tenant={tenant_id}, error={e}")
            return JSONResponse({"success": False, "msg": "invalid json"}, status_code=400)

        # 3. 空消息/忽略事件
        msg_type = data.get("msgtype", "text")
        if msg_type == "empty":
            logger.debug(f"[Tenant DingTalk] 忽略空消息: tenant={tenant_id}, config={config_id}")
            return JSONResponse({"success": True})

        # 4. 消息去重
        # 共享表 channel_message_dedup，需加 channel + tenant 前缀避免跨渠道/跨租户碰撞
        msg_id = data.get("msgId", "")
        if msg_id:
            dedup = _get_dingtalk_event_dedup(tenant_id)
            dedup_key = f"dingtalk:{tenant_id}:{msg_id}"
            if await dedup.is_duplicate(dedup_key):
                logger.debug(f"[Tenant DingTalk] 重复消息: tenant={tenant_id}, config={config_id}, msgId={msg_id}")
                return JSONResponse({"success": True})

        # 5. 立即返回，后台异步处理
        # 持有 task 强引用避免被 GC 回收，完成后通过回调移除
        task = asyncio.create_task(
            _process_tenant_dingtalk_background(
                tenant_id, data, config_id=config_id, subagent_type=subagent_type
            )
        )
        _dingtalk_background_tasks.add(task)
        task.add_done_callback(_dingtalk_background_tasks.discard)
        return JSONResponse({"success": True})

    except Exception as e:
        logger.error(
            f"[Tenant DingTalk] POST 处理异常: tenant={tenant_id}, config={config_id}, error={e}"
        )
        return JSONResponse({"success": False, "msg": "internal error"}, status_code=500)


async def _process_tenant_dingtalk_background(
    tenant_id: str,
    event_data: dict,
    config_id: Optional[str] = None,
    subagent_type: Optional[str] = None,
) -> None:
    """
    钉钉消息后台异步处理

    在 asyncio.create_task 中执行，不阻塞回调响应。

    Args:
        tenant_id: 租户 ID
        event_data: 钉钉回调 JSON dict（含 msgtype/text/senderId/conversationType 等）
        config_id: 渠道配置 ID（用于按 config_id 命中缓存）
        subagent_type: 关联的数字员工类型
    """
    adapter = None
    try:
        adapter, _, _ = await ChannelFactory.create_from_tenant_config(
            tenant_id, "dingtalk", config_id=config_id
        )
        if not adapter:
            logger.error(f"[Tenant DingTalk] adapter 不可用: tenant={tenant_id}")
            return

        # 解析消息（DingTalk parse_message 直接接受 JSON dict）
        message = await adapter.parse_message(event_data)
        if message is None:
            logger.debug(f"[Tenant DingTalk] 消息被忽略: tenant={tenant_id}")
            return

        # 自动注册用户
        user_id = None
        try:
            from src.saas.services.auto_register import ensure_user_registered
            user_id = await ensure_user_registered("dingtalk", message.user_id, tenant_id)
        except Exception as e:
            logger.warning(f"[Tenant DingTalk] 自动注册失败: {e}")

        # 提取 conversation_type（"1" 单聊 / "2" 群聊）
        conversation_type = message.content.get("conversation_type", "1")
        conversation_id = message.content.get("conversation_id", "")

        # 构建用户信息并创建/获取会话
        user_info = {"user_id": user_id, "name": getattr(message, "user_name", None) or ""}
        session = channel_session_manager.get_or_create_session(
            channel_type="dingtalk",
            channel_user_id=message.user_id,
            tenant_id=tenant_id,
            user_info=user_info,
            subagent_id=subagent_type or "",
        )
        session_id = session["session_id"]

        # 检查隐藏命令
        from src.core.hidden_commands import is_hidden_command, execute_hidden_command
        if message.text and is_hidden_command(message.text):
            reply_text = await execute_hidden_command(message.text, session_id, tenant_id)
            if reply_text:
                # 单聊回复到 userId，群聊回复到 openConversationId
                reply_target = conversation_id if conversation_type == "2" else message.user_id
                await adapter.send_text(reply_text, reply_target, conversation_type)
            return

        # 记录用户消息 —— P0-2：推迟到 process_and_persist 内统一写入

        # 获取 agent
        from src.core.agent_router import agent_router
        agent = agent_router.get_agent(subagent_type, session_id)

        # 开始记录 token 消耗
        record_service = SessionRecordManager.start_record(
            session_id=session_id,
            user_id=user_id or message.user_id,
            user_message=message.text,
            tenant_id=tenant_id,
            source_type="dingtalk",
        )
        record_service.set_model(agent.llm.get_model_name())
        record_service.set_provider(agent.llm.get_provider_name())

        # 处理消息 + 持久化（P0-1 / P0-2 统一在 process_and_persist 内完成）
        # 发送回复：单聊用 userId，群聊用 openConversationId
        reply_target = conversation_id if conversation_type == "2" else message.user_id

        send_response = channel_session_manager.make_send_response(
            adapter=adapter,
            message_id=message.message_id,
            reply_to=reply_target,
            extra_content={"conversation_type": conversation_type},
            log_tag="[Tenant DingTalk]",
        )
        # verbose 中间消息（Phase 3）：conversation_type / reply_target 与 final 一致（群聊同规则）
        _verbose_cfg = resolve_verbose_feedback_config(
            channel_cfg=getattr(adapter, "verbose_feedback", None),
        )
        _send_verbose = channel_session_manager.make_send_verbose(
            adapter=adapter,
            event_id=message.message_id,
            reply_to=reply_target,
            extra_content={"conversation_type": conversation_type},
            log_tag="[Tenant DingTalk]",
        )

        from src.channels.agent_user_builder import build_agent_user_for_channel
        agent_user = await build_agent_user_for_channel(
            channel_type="dingtalk",
            channel_user_id=message.user_id,
            tenant_id=tenant_id,
            adapter=adapter,
            user_id=user_id,
        )

        result = await channel_session_manager.process_and_persist(
            session_id=session_id,
            tenant_id=tenant_id,
            user_content=message.text,
            user_metadata=message.raw_message,
            message_type=message.message_type,
            agent=agent,
            agent_user=agent_user,
            record_service=record_service,
            send_response=send_response,
            send_verbose=_send_verbose,
            verbose_feedback_config=_verbose_cfg,
        )

        SessionRecordManager.end_record()
        if result["status"] == "merged":
            return

    except Exception as e:
        logger.error(f"[Tenant DingTalk] 后台处理失败: tenant={tenant_id}, error={e}")
        try:
            _record = SessionRecordManager.get_current_record()
            if _record:
                _record.mark_error(str(e))
                SessionRecordManager.end_record()
        except Exception:
            pass
        try:
            if adapter:
                fallback_user_id = event_data.get("senderId", "")
                if fallback_user_id:
                    await adapter.send_text(
                        "抱歉，处理您的消息时遇到了问题，请稍后重试。",
                        fallback_user_id,
                        "1",
                    )
        except Exception:
            pass


# ==================== Feishu 租户回调 ====================

# 飞书事件去重器（按 tenant_id 隔离，TTL 5 分钟）
_feishu_event_dedup: dict[str, MessageDeduplicator] = {}

# 后台任务引用集合：保留强引用避免被 GC 回收，任务完成后自动移除
_feishu_background_tasks: set = set()


def _get_feishu_event_dedup(tenant_id: str) -> MessageDeduplicator:
    """获取或创建飞书事件去重器"""
    if tenant_id not in _feishu_event_dedup:
        _feishu_event_dedup[tenant_id] = MessageDeduplicator(ttl_seconds=300)
    return _feishu_event_dedup[tenant_id]


async def _process_tenant_feishu_background(
    tenant_id: str,
    event_data: dict,
    config_id: Optional[str] = None,
    subagent_type: Optional[str] = None,
) -> None:
    """
    飞书事件后台异步处理

    在 asyncio.create_task 中执行，不阻塞回调响应。
    注意：飞书 parse_message 期望 v2.0 事件 dict（含 header + event），
    而非 {"body": raw_body_str} 包装。

    Args:
        tenant_id: 租户 ID
        event_data: 已解密的 v2.0 事件 dict
        config_id: 渠道配置 ID（用于按 config_id 命中缓存）
        subagent_type: 关联的数字员工类型
    """
    adapter = None
    try:
        adapter, _, _ = await ChannelFactory.create_from_tenant_config(
            tenant_id, "feishu", config_id=config_id
        )
        if not adapter:
            logger.error(f"[Tenant Feishu] adapter 不可用: tenant={tenant_id}")
            return

        # 飞书 parse_message 直接接受 v2.0 事件 dict
        message = await adapter.parse_message(event_data)
        if message is None:
            logger.debug(f"[Tenant Feishu] 消息被忽略（群聊未@机器人等）: tenant={tenant_id}")
            return

        # 自动注册用户
        user_id = None
        try:
            from src.saas.services.auto_register import ensure_user_registered
            user_id = await ensure_user_registered("feishu", message.user_id, tenant_id)
        except Exception as e:
            logger.warning(f"[Tenant Feishu] 自动注册失败: {e}")

        # 构建用户信息并创建/获取会话（带租户隔离）
        user_info = {"user_id": user_id, "name": getattr(message, "user_name", None) or ""}
        session = channel_session_manager.get_or_create_session(
            channel_type="feishu",
            channel_user_id=message.user_id,
            tenant_id=tenant_id,
            user_info=user_info,
            subagent_id=subagent_type or "",
        )
        session_id = session["session_id"]

        # 检查隐藏命令
        from src.core.hidden_commands import is_hidden_command, execute_hidden_command
        if message.text and is_hidden_command(message.text):
            reply_text = await execute_hidden_command(message.text, session_id, tenant_id)
            if reply_text:
                await adapter.send_text(reply_text, message.user_id)
            return

        # 记录用户消息 —— P0-2：推迟到 process_and_persist 内统一写入

        # 获取 agent
        from src.core.agent_router import agent_router
        agent = agent_router.get_agent(subagent_type, session_id)

        # 开始记录 token 消耗
        record_service = SessionRecordManager.start_record(
            session_id=session_id,
            user_id=user_id or message.user_id,
            user_message=message.text,
            tenant_id=tenant_id,
            source_type="feishu",
        )
        record_service.set_model(agent.llm.get_model_name())
        record_service.set_provider(agent.llm.get_provider_name())

        # 处理消息 + 持久化（P0-1 / P0-2 统一在 process_and_persist 内完成）
        send_response = channel_session_manager.make_send_response(
            adapter=adapter,
            message_id=message.message_id,
            reply_to=message.user_id,
            log_tag="[Tenant Feishu]",
        )
        # verbose 中间消息（Phase 3，设计 §11）
        _verbose_cfg = resolve_verbose_feedback_config(
            channel_cfg=getattr(adapter, "verbose_feedback", None),
        )
        _send_verbose = channel_session_manager.make_send_verbose(
            adapter=adapter,
            event_id=message.message_id,
            reply_to=message.user_id,
            log_tag="[Tenant Feishu]",
        )

        from src.channels.agent_user_builder import build_agent_user_for_channel
        agent_user = await build_agent_user_for_channel(
            channel_type="feishu",
            channel_user_id=message.user_id,
            tenant_id=tenant_id,
            adapter=adapter,
            user_id=user_id,
        )

        result = await channel_session_manager.process_and_persist(
            session_id=session_id,
            tenant_id=tenant_id,
            user_content=message.text,
            user_metadata=message.raw_message,
            message_type=message.message_type,
            agent=agent,
            agent_user=agent_user,
            record_service=record_service,
            send_response=send_response,
            send_verbose=_send_verbose,
            verbose_feedback_config=_verbose_cfg,
        )

        SessionRecordManager.end_record()
        if result["status"] == "merged":
            return

    except Exception as e:
        logger.error(f"[Tenant Feishu] 后台处理失败: tenant={tenant_id}, error={e}")
        try:
            _record = SessionRecordManager.get_current_record()
            if _record:
                _record.mark_error(str(e))
                SessionRecordManager.end_record()
        except Exception:
            pass
        try:
            if adapter:
                # 从 event_data 提取 user_id 作为兜底
                fallback_user_id = (
                    event_data.get("event", {})
                    .get("sender", {})
                    .get("sender_id", {})
                    .get("open_id", "")
                )
                if fallback_user_id:
                    await adapter.send_text(
                        "抱歉，处理您的消息时遇到了问题，请稍后重试。",
                        fallback_user_id,
                    )
        except Exception:
            pass


@router.get("/t/{tenant_id}/feishu/callback/{config_id}")
async def tenant_feishu_callback_get(
    tenant_id: str,
    config_id: str,
):
    """
    飞书回调 GET 兜底

    飞书 URL 验证实际走 POST（url_verification），此 GET 路由仅作健康检查保留，
    不回显任何外部输入，避免被无鉴权滥用为回声端点。config_id 路径段仅用于路由匹配，
    不影响返回内容。
    """
    return {"status": "ok"}


@router.post("/t/{tenant_id}/feishu/callback/{config_id}")
async def tenant_feishu_callback_post(
    tenant_id: str, config_id: str, request: Request
):
    """
    飞书事件统一入口（url_verification 和事件回调都走这里）

    流程:
    1. 按 config_id 查配置 → 构造 adapter
    2. 加密事件先解密（url_verification 也可能被加密）
    3. v2.0 签名校验（X-Lark-Signature 请求头，在原始 body 上计算）
    4. url_verification: 校验 token，返回 challenge
    5. 事件去重（event_id，5 分钟 TTL）
    6. 只处理 im.message.receive_v1，其他事件返回 200
    7. 立即返回 2xx → 后台 asyncio.create_task 异步处理
    """
    try:
        body = await request.body()
        body_str = body.decode()

        adapter, _, subagent_type = await ChannelFactory.create_from_tenant_config(
            tenant_id, "feishu", config_id=config_id
        )
        if not adapter:
            logger.warning(
                f"[Tenant Feishu] 配置不存在: tenant={tenant_id}, config={config_id}"
            )
            return JSONResponse({"code": 404, "msg": "config not found"}, status_code=404)

        # 解析 JSON body
        try:
            data = json.loads(body_str)
        except json.JSONDecodeError as e:
            logger.warning(f"[Tenant Feishu] body JSON 解析失败: tenant={tenant_id}, config={config_id}, error={e}")
            return JSONResponse({"code": 400, "msg": "invalid json"}, status_code=400)

        # 1. v2.0 签名校验（在原始 body 上计算，解密前）
        if adapter.crypto:
            signature = request.headers.get("X-Lark-Signature", "")
            timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
            nonce = request.headers.get("X-Lark-Request-Nonce", "")
            if signature:
                if not adapter.crypto.verify_signature(timestamp, nonce, body_str, signature):
                    logger.warning(f"[Tenant Feishu] 签名验证失败: tenant={tenant_id}, config={config_id}")
                    return JSONResponse({"code": 403, "msg": "invalid signature"}, status_code=403)

        # 2. 加密事件先解密（url_verification 也可能被加密）
        if "encrypt" in data and adapter.crypto:
            try:
                data = adapter.crypto.decrypt(data["encrypt"])
            except Exception as e:
                logger.error(f"[Tenant Feishu] 解密失败: tenant={tenant_id}, config={config_id}, error={e}")
                return JSONResponse({"code": 400, "msg": "decrypt failed"}, status_code=400)

        # 3. url_verification 挑战
        if data.get("type") == "url_verification":
            challenge = adapter.crypto.verify_url_verification(data) if adapter.crypto else None
            if adapter.crypto and challenge is None:
                return JSONResponse({"code": 403, "msg": "token mismatch"}, status_code=403)
            # 无加密模式：直接取 challenge
            if not adapter.crypto:
                token = data.get("token", "")
                if token != adapter.verification_token:
                    return JSONResponse({"code": 403, "msg": "token mismatch"}, status_code=403)
                challenge = data.get("challenge")
            if challenge:
                return {"challenge": challenge}
            return JSONResponse({"code": 400, "msg": "missing challenge"}, status_code=400)

        # 4. 事件去重
        event_id = data.get("header", {}).get("event_id", "")
        if event_id:
            dedup = _get_feishu_event_dedup(tenant_id)
            if await dedup.is_duplicate(event_id):
                logger.debug(f"[Tenant Feishu] 重复事件: tenant={tenant_id}, config={config_id}, event_id={event_id}")
                return JSONResponse({"code": 0, "msg": "duplicate"})

        # 5. 只处理 im.message.receive_v1
        event_type = data.get("header", {}).get("event_type", "")
        if event_type != "im.message.receive_v1":
            logger.debug(f"[Tenant Feishu] 忽略非消息事件: tenant={tenant_id}, config={config_id}, event_type={event_type}")
            return JSONResponse({"code": 0, "msg": "event ignored"})

        # 6. 立即返回 200 → 后台异步处理
        # 持有 task 强引用避免被 GC 回收，完成后通过回调移除
        task = asyncio.create_task(
            _process_tenant_feishu_background(
                tenant_id, data, config_id=config_id, subagent_type=subagent_type
            )
        )
        _feishu_background_tasks.add(task)
        task.add_done_callback(_feishu_background_tasks.discard)
        return JSONResponse({"code": 0, "msg": "ok"})

    except Exception as e:
        logger.error(f"[Tenant Feishu] POST 处理异常: tenant={tenant_id}, config={config_id}, error={e}")
        return JSONResponse({"code": 500, "msg": "internal error"}, status_code=500)


# ==================== WeCom KF 租户回调 ====================

@router.get("/t/{tenant_id}/wecom_kf/callback/{config_id}")
async def tenant_wecom_kf_callback_get(
    tenant_id: str,
    config_id: str,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """微信客服回调 URL 验证"""
    adapter, _, _ = await ChannelFactory.create_from_tenant_config(tenant_id, "wecom_kf", config_id=config_id)
    if not adapter:
        logger.warning(f"[Tenant WeCom KF] 配置不存在: tenant={tenant_id}, config={config_id}")
        return PlainTextResponse("Config not found", status_code=404)

    if adapter.crypto:
        if not adapter.crypto.verify_signature(msg_signature, timestamp, nonce, echostr):
            logger.warning(f"[Tenant WeCom KF] 签名验证失败: tenant={tenant_id}")
            return PlainTextResponse("Invalid signature", status_code=403)
        try:
            plaintext = adapter.crypto.decrypt(echostr)
            logger.info(f"[Tenant WeCom KF] 回调验证成功: tenant={tenant_id}")
            return PlainTextResponse(plaintext)
        except Exception as e:
            logger.error(f"[Tenant WeCom KF] echostr 解密失败: {e}")
            return PlainTextResponse("Decryption failed", status_code=400)

    if not await adapter.verify_signature(msg_signature, timestamp, nonce, echostr):
        return PlainTextResponse("Invalid signature", status_code=403)
    return PlainTextResponse(echostr)


@router.post("/t/{tenant_id}/wecom_kf/callback/{config_id}")
async def tenant_wecom_kf_callback_post(tenant_id: str, config_id: str, request: Request):
    """微信客服消息回调"""
    try:
        body = await request.body()
        body_str = body.decode()
        logger.info(
            f"[Tenant WeCom KF] 收到POST回调: tenant={tenant_id}, config={config_id}, "
            f"body_len={len(body_str)}, query_params={dict(request.query_params)}"
        )
        _kf_tlog(
            "POST回调入口: tenant={tenant}, config={config}, body_len={body_len}, "
            "query={query}, body={body}",
            tenant=tenant_id,
            config=config_id,
            body_len=len(body_str),
            query=dict(request.query_params),
            body=body_str,
        )

        adapter, _, _ = await ChannelFactory.create_from_tenant_config(tenant_id, "wecom_kf", config_id=config_id)
        if not adapter:
            logger.warning(f"[Tenant WeCom KF] 配置不存在: tenant={tenant_id}, config={config_id}")
            _kf_tlog(
                "配置不存在，返回404: tenant={tenant}, config={config}",
                tenant=tenant_id,
                config=config_id,
                level="ERROR",
            )
            return PlainTextResponse("Config not found", status_code=404)

        # 解析 XML 获取加密内容
        root = ET.fromstring(body_str)
        encrypt = root.findtext("Encrypt", "")
        msg_signature_param = root.findtext("MsgSignature", "")
        timestamp_param = root.findtext("TimeStamp", "")
        nonce_param = root.findtext("Nonce", "")

        # 获取查询参数中的签名
        query_params = dict(request.query_params)
        if not msg_signature_param:
            msg_signature_param = query_params.get("msg_signature", "")
        if not timestamp_param:
            timestamp_param = query_params.get("timestamp", str(int(time.time())))
        if not nonce_param:
            nonce_param = query_params.get("nonce", "")

        # 解密消息体
        if encrypt and adapter.crypto:
            if not adapter.crypto.verify_signature(msg_signature_param, timestamp_param, nonce_param, encrypt):
                logger.warning(f"[Tenant WeCom KF] POST 签名验证失败: tenant={tenant_id}")
                return PlainTextResponse("Invalid signature", status_code=403)
            try:
                decrypted_xml = adapter.crypto.decrypt(encrypt)
            except Exception as e:
                logger.error(f"[Tenant WeCom KF] 消息解密失败: {e}")
                return PlainTextResponse("Decryption failed", status_code=400)
        else:
            decrypted_xml = body_str

        # 解析回调 XML
        callback_root = ET.fromstring(decrypted_xml)
        event = callback_root.findtext("Event", "")
        change_type = callback_root.findtext("ChangeType", "")
        open_kfid = callback_root.findtext("OpenKfId", "")
        logger.info(
            f"[Tenant WeCom KF] 回调解析: event={event}, change_type={change_type}, "
            f"open_kfid={open_kfid}, tenant={tenant_id}"
        )
        _kf_tlog(
            "回调解析: tenant={tenant}, event={event}, change_type={change_type}, "
            "open_kfid={open_kfid}, decrypted_xml={xml}",
            tenant=tenant_id,
            event=event,
            change_type=change_type,
            open_kfid=open_kfid,
            xml=decrypted_xml,
        )
        # 临时调试：记录完整回调 XML，用于排查"用户撤回"等事件是否被微信推送
        # 调试主题：微信事件
        # from src.core.temp_logger import tlog
        # tlog(
        #     "微信事件",
        #     "回调 XML: tenant={tenant}, event={event}, change_type={change_type}, "
        #     "open_kfid={open_kfid}, xml={xml}",
        #     tenant=tenant_id,
        #     event=event,
        #     change_type=change_type,
        #     open_kfid=open_kfid,
        #     xml=decrypted_xml,
        # )

        # 消息事件 → 后台异步拉取并处理
        if event == "kf_msg_or_event":
            logger.info(f"[Tenant WeCom KF] 创建后台任务拉取消息: open_kfid={open_kfid}")
            _kf_tlog(
                "事件分发→创建后台任务: tenant={tenant}, event={event}, open_kfid={open_kfid}",
                tenant=tenant_id,
                event=event,
                open_kfid=open_kfid,
            )
            asyncio.create_task(
                _process_tenant_wecom_kf_messages(tenant_id, config_id, open_kfid, adapter)
            )
        # 会话状态变更 → 更新本地状态
        elif event == "change_type" and change_type == "session_status_change":
            logger.info(f"[Tenant WeCom KF] 会话状态变更事件: open_kfid={open_kfid}")
            _kf_tlog(
                "事件分发→会话状态变更: tenant={tenant}, open_kfid={open_kfid}",
                tenant=tenant_id,
                open_kfid=open_kfid,
            )
            await _handle_kf_session_status_change(callback_root, tenant_id)
        # 进入会话 → 发送欢迎语
        elif event == "enter_session":
            logger.info(f"[Tenant WeCom KF] 进入会话事件: open_kfid={open_kfid}")
            _kf_tlog(
                "事件分发→进入会话: tenant={tenant}, open_kfid={open_kfid}",
                tenant=tenant_id,
                open_kfid=open_kfid,
            )
            await _handle_kf_enter_session(callback_root, adapter)
        else:
            logger.warning(
                f"[Tenant WeCom KF] 未知事件类型: event={event}, change_type={change_type}, "
                f"decrypted_xml前200字符={decrypted_xml[:200]}"
            )
            _kf_tlog(
                "事件分发→未知事件: tenant={tenant}, event={event}, change_type={change_type}, "
                "xml_prefix={xml_prefix}",
                tenant=tenant_id,
                event=event,
                change_type=change_type,
                xml_prefix=decrypted_xml[:200],
                level="WARNING",
            )

        return PlainTextResponse("success")

    except Exception as e:
        logger.error(f"[Tenant WeCom KF] POST 处理异常: tenant={tenant_id}, config={config_id}, error={e}")
        _kf_tlog(
            "POST回调异常: tenant={tenant}, config={config}, error={error}",
            tenant=tenant_id,
            config=config_id,
            error=str(e),
            level="ERROR",
        )
        return PlainTextResponse("error", status_code=500)


async def _auto_fill_open_kfid(tenant_id: str, open_kfid: str) -> tuple:
    """
    自动填入 open_kfid：查找当前租户下 open_kfid 未设置的 wecom_kf 配置，
    如果只有 1 条匹配，将 open_kfid 写入该配置的 kf_account 中第一条记录。

    Returns:
        (success, config_id)：是否成功填入，及被更新的 config_id（用于上游失效 adapter 缓存）
    """
    try:
        configs = ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf")
        if not configs:
            logger.warning(f"[wecom_kf] auto_fill_open_kfid: 租户 {tenant_id} 无 wecom_kf 配置")
            _kf_tlog(
                "auto_fill失败: 租户无wecom_kf配置, tenant={tenant}",
                tenant=tenant_id,
                level="WARNING",
            )
            return False, None

        # 筛选 open_kfid 未设置的配置（kf_account 中至少有一条 open_kfid 为空）
        candidates = []
        for cfg in configs:
            kf_accounts = cfg.get("config", {}).get("kf_account", [])
            if kf_accounts:
                for kf in kf_accounts:
                    if not kf.get("open_kfid"):
                        candidates.append((cfg["config_id"], cfg["config"], kf_accounts))
                        break

        if len(candidates) == 0:
            logger.info(
                f"[wecom_kf] auto_fill_open_kfid: 租户 {tenant_id} 所有配置的 open_kfid 均已设置"
            )
            _kf_tlog(
                "auto_fill失败: 所有配置的open_kfid均已设置, tenant={tenant}, open_kfid={open_kfid}（"
                "说明DB有值但adapter缓存陈旧，应触发invalidate）",
                tenant=tenant_id,
                open_kfid=open_kfid,
                level="WARNING",
            )
            return False, None

        if len(candidates) > 1:
            logger.warning(
                f"[wecom_kf] auto_fill_open_kfid: 租户 {tenant_id} 存在 {len(candidates)} 条 "
                f"open_kfid 未设置的 wecom_kf 配置，无法自动填入"
            )
            _kf_tlog(
                "auto_fill失败: 存在{count}条候选配置无法唯一确定, tenant={tenant}",
                tenant=tenant_id,
                count=len(candidates),
                level="WARNING",
            )
            return False, None

        config_id, config_dict, kf_accounts = candidates[0]
        # 填入第一条 open_kfid 为空的记录
        for kf in kf_accounts:
            if not kf.get("open_kfid"):
                kf["open_kfid"] = open_kfid
                break

        ChannelConfigDB.update(config_id, config_dict)
        # DB 已更新，必须使 adapter 进程内缓存失效，否则缓存的 adapter 实例
        # 仍持有旧 kf_accounts（无 open_kfid），下次回调依旧找不到 kf_config
        await ChannelFactory.invalidate_adapter(tenant_id, "wecom_kf", config_id, close=True)
        logger.info(
            f"[wecom_kf] auto_fill_open_kfid: 已将 open_kfid={open_kfid} "
            f"自动填入 tenant={tenant_id} config={config_id}，并失效 adapter 缓存"
        )
        _kf_tlog(
            "auto_fill成功: open_kfid={open_kfid} 已填入 config={config}, adapter缓存已失效",
            open_kfid=open_kfid,
            config=config_id,
        )
        return True, config_id
    except Exception as e:
        logger.opt(exception=True).error(f"[wecom_kf] auto_fill_open_kfid 异常: {e}")
        _kf_tlog(
            "auto_fill异常: {err}, tenant={tenant}",
            tenant=tenant_id,
            err=str(e),
            level="ERROR",
        )
        return False, None


async def _reply_kf_blocked_message(
    adapter,
    tenant_id: str,
    open_kfid: str,
    unified_msg,
    session_id: str,
    user_content: str,
    text: str,
    kind: str,
) -> None:
    """拦截命中时：先落库用户消息，再发送固定话术并落库 assistant 消息（不计费）。

    复用发送 + 落库通道，写 channel_messages 便于前端展示。
    """
    try:
        channel_session_manager.add_message(
            session_id=session_id,
            role="user",
            content=user_content,
            message_type="text",
            tenant_id=tenant_id,
            metadata={
                "kind": f"blocked_{kind}_user",
                "msgid": getattr(unified_msg, "message_id", "") or "",
            },
        )
    except Exception as e:
        logger.warning(f"[wecom_kf] 拦截落库用户消息失败: {e}")

    try:
        adapter.current_open_kfid = open_kfid
        await adapter.send_text(text, unified_msg.user_id)
    except Exception as e:
        logger.warning(f"[wecom_kf] 拦截话术发送失败: {e}")
        return

    try:
        channel_session_manager.add_message(
            session_id=session_id,
            role="assistant",
            content=text,
            message_type="text",
            tenant_id=tenant_id,
            metadata={"kind": f"blocked_{kind}_reply", "open_kfid": open_kfid},
        )
    except Exception as e:
        logger.warning(f"[wecom_kf] 拦截话术落库失败: {e}")


async def _is_kf_account_blocked(
    adapter,
    kf_config: dict,
    tenant_id: str,
    open_kfid: str,
    unified_msg,
    session_id: str,
    user_content: str,
) -> bool:
    """客服账号到期 / 积分用尽拦截，命中时回固定话术并落库，返回 True。

    两条拦截均不调智能体、不 start_record（不计费）。
    转人工会话不经此路径（企微直达员工侧，不会回调我方智能体）。
    """
    from datetime import datetime, timedelta

    # 拦截 1：到期日期（expire_at 可空，到期日当天仍有效，+23h59m59s）
    expire_at = kf_config.get("expire_at")
    if expire_at:
        try:
            expire_dt = datetime.strptime(expire_at, "%Y-%m-%d") + timedelta(
                hours=23, minutes=59, seconds=59
            )
            if datetime.now() > expire_dt:
                _kf_tlog(
                    "账号到期拦截: tenant={tenant}, open_kfid={open_kfid}, expire_at={expire_at}",
                    tenant=tenant_id, open_kfid=open_kfid, expire_at=expire_at,
                    level="WARNING",
                )
                await _reply_kf_blocked_message(
                    adapter, tenant_id, open_kfid, unified_msg,
                    session_id, user_content, MSG_EXPIRED, "expired",
                )
                return True
        except ValueError:
            logger.warning(f"[wecom_kf] expire_at 格式非法: {expire_at}")

    # 拦截 2：积分上限（credit_limit>0 才查；0=跟随租户积分，已有租户级拦截）
    credit_limit = kf_config.get("credit_limit", 0)
    if credit_limit and credit_limit > 0:
        try:
            used = CustomerReferralDB.sum_kf_account_credit(tenant_id, open_kfid)
            if used >= float(credit_limit):
                _kf_tlog(
                    "账号积分用尽拦截: tenant={tenant}, open_kfid={open_kfid}, "
                    "used={used}, limit={limit}",
                    tenant=tenant_id, open_kfid=open_kfid, used=used, limit=credit_limit,
                    level="WARNING",
                )
                await _reply_kf_blocked_message(
                    adapter, tenant_id, open_kfid, unified_msg,
                    session_id, user_content, MSG_CREDIT_EXHAUSTED, "credit_exhausted",
                )
                return True
        except Exception as e:
            logger.warning(f"[wecom_kf] 积分归集查询失败: {e}")

    return False


# Phase 3（设计 §11）：route 外层旧 watchdog _process_with_waiting_indicator 及其
# 配置读取助手 _get_waiting_indicator_cfg 已删除；旧 waiting_indicator 配置仅保留
# enabled 开关语义，经 resolve_verbose_feedback_config 映射进新 verbose 机制
#（开关开→启用、开关关→显式关闭），超时秒数（delay_seconds）随 watchdog 一并下线。
async def _process_tenant_wecom_kf_messages(
    tenant_id: str, config_id: str, open_kfid: str, adapter
) -> None:
    """
    后台异步处理：从微信客服 API 拉取消息 → 逐条处理 → 发送回复
    """
    try:
        from src.saas.services.auto_register import ensure_user_registered
        from src.core.agent_router import agent_router
        from src.channels.wecom_kf.context import set_kf_context
        from src.models.message import UnifiedResponse
        # from src.core.temp_logger import tlog

        logger.info(
            f"[wecom_kf] 后台处理开始: tenant={tenant_id}, config={config_id}, open_kfid={open_kfid}"
        )
        _kf_tlog(
            "后台任务开始: tenant={tenant}, config={config}, open_kfid={open_kfid}",
            tenant=tenant_id,
            config=config_id,
            open_kfid=open_kfid,
        )

        # 查找客服账号配置
        kf_config = adapter.get_kf_config(open_kfid)
        if not kf_config:
            logger.info(f"[wecom_kf] 新的 open_kfid: {open_kfid}，尝试自动填入配置")
            _kf_tlog(
                "kf_config未找到，尝试自动填入: tenant={tenant}, open_kfid={open_kfid}",
                tenant=tenant_id,
                open_kfid=open_kfid,
                level="WARNING",
            )
            filled, filled_config_id = await _auto_fill_open_kfid(tenant_id, open_kfid)
            if not filled:
                return
            # auto_fill 已失效 adapter 缓存，重新获取最新 adapter 实例
            new_adapter, _, _ = await ChannelFactory.create_from_tenant_config(
                tenant_id, "wecom_kf", config_id=filled_config_id
            )
            if not new_adapter:
                _kf_tlog(
                    "auto_fill后重建adapter失败: tenant={tenant}, config={config}",
                    tenant=tenant_id,
                    config=filled_config_id,
                    level="ERROR",
                )
                return
            adapter = new_adapter
            kf_config = adapter.get_kf_config(open_kfid)
            if not kf_config:
                _kf_tlog(
                    "auto_fill+重建adapter后仍未找到kf_config, tenant={tenant}, open_kfid={open_kfid}",
                    tenant=tenant_id,
                    open_kfid=open_kfid,
                    level="ERROR",
                )
                return
            _kf_tlog(
                "auto_fill后重试成功: tenant={tenant}, open_kfid={open_kfid}",
                tenant=tenant_id,
                open_kfid=open_kfid,
            )

        subagent_type = kf_config.get("subagent_type", "")
        logger.info(f"[wecom_kf] 客服配置: subagent_type={subagent_type}, open_kfid={open_kfid}")
        _kf_tlog(
            "kf_config命中: tenant={tenant}, open_kfid={open_kfid}, subagent_type={subagent_type}, "
            "kf_config_keys={keys}",
            tenant=tenant_id,
            open_kfid=open_kfid,
            subagent_type=subagent_type,
            keys=list(kf_config.keys()),
        )

        # 使用 cursor 分页拉取消息
        cursor = adapter.cursor_manager.get_cursor(open_kfid)
        has_more = True
        total_messages = 0
        processed_messages = 0

        while has_more:
            logger.info(f"[wecom_kf] sync_msg调用: cursor={cursor[:20]}..., open_kfid={open_kfid}")
            result = await adapter.api_client.sync_msg(open_kfid=open_kfid, cursor=cursor, limit=100, voice_format=1)
            errcode = result.get("errcode", 0)
            errmsg = result.get("errmsg", "")
            has_more = result.get("has_more", 0) == 1
            cursor = result.get("next_cursor", "")
            msg_count = len(result.get("msg_list", []))
            total_messages += msg_count
            logger.info(
                f"[wecom_kf] sync_msg返回: errcode={errcode}, errmsg={errmsg}, "
                f"msg_count={msg_count}, has_more={has_more}"
            )
            _kf_tlog(
                "sync_msg返回: tenant={tenant}, open_kfid={open_kfid}, errcode={errcode}, "
                "errmsg={errmsg}, msg_count={msg_count}, has_more={has_more}, "
                "next_cursor={next_cursor}",
                tenant=tenant_id,
                open_kfid=open_kfid,
                errcode=errcode,
                errmsg=errmsg,
                msg_count=msg_count,
                has_more=has_more,
                next_cursor=cursor[:30] if cursor else "",
            )
            if result.get("errcode", 0) != 0:
                logger.error(f"[wecom_kf] sync_msg 失败: errcode={result.get('errcode')}")
                _kf_tlog(
                    "sync_msg失败: tenant={tenant}, errcode={errcode}, errmsg={errmsg}",
                    tenant=tenant_id,
                    errcode=errcode,
                    errmsg=errmsg,
                    level="ERROR",
                )
                return

            # 预处理：过滤非客户消息 + 去重，得到有效消息列表
            valid_msgs = []
            servicer_msgs_to_persist = []  # 本页员工消息（origin=5），按 send_time 正序，供穿插入库
            recalled_msgids_in_batch = set()  # 本批次内被用户撤回的消息 msgid（供后续剔除用）
            for msg in result.get("msg_list", []):
                msg_id = msg.get("msgid", "")
                msg_origin = msg.get("origin", "")
                msg_type = msg.get("msgtype", "")
                logger.debug(
                    f"[wecom_kf] 消息: msgid={msg_id}, origin={msg_origin}, "
                    f"msgtype={msg_type}, open_kfid={open_kfid}"
                )

                # 实验期埋点：全量记录所有非客户消息（含事件、员工消息、未知 origin）
                # 用于验证企微 sync_msg 到底能返回什么内容
                if msg_origin != 3:
                    try:
                        import json as _json
                        _tlog(
                            "wecom_kf_servicer_msg",
                            "open_kfid={kfid} msgid={mid} origin={og} msgtype={mt} "
                            "ext_user={eu} servicer={sv} send_time={st} raw={raw}",
                            kfid=open_kfid,
                            mid=msg_id,
                            og=msg_origin,
                            mt=msg_type,
                            eu=msg.get("external_userid"),
                            sv=msg.get("servicer_userid"),
                            st=msg.get("send_time"),
                            raw=_json.dumps(msg, ensure_ascii=False)[:2000],
                        )
                    except Exception:
                        pass

                # ===== 撤回消息处理：在 origin 过滤之前识别 user_recall_msg 事件 =====
                if msg_type == "event" and msg.get("event", {}).get("event_type") == "user_recall_msg":
                    event_data = msg.get("event", {})
                    # 被撤回的原消息 ID 在 event.recall_msgid（事件自身 ID 是外层 msg.msgid）
                    recall_msgid = event_data.get("recall_msgid", "")
                    external_userid = event_data.get("external_userid", "")

                    # 记录原始事件结构，便于核对字段名
                    # try:
                    #     import json as _json
                        # tlog(
                        #     "微信事件",
                        #     "撤回事件原始结构: event_msgid={event_msgid}, raw={raw}",
                        #     event_msgid=msg_id,
                        #     raw=_json.dumps(msg, ensure_ascii=False),
                        # )
                    # except Exception:
                    #     pass

                    # 事件去重：按撤回事件自身的 msgid 去重，避免同一事件多次推送重复处理
                    dedup = _get_tenant_dedup(tenant_id)
                    if await dedup.is_duplicate(f"recall_event:{msg_id}"):
                        # tlog(
                        #     "撤回消息",
                        #     "撤回事件重复跳过: event_msgid={event_msgid}, recall_msgid={recall_msgid}, "
                        #     "open_kfid={open_kfid}, user={user}",
                        #     event_msgid=msg_id,
                        #     recall_msgid=recall_msgid,
                        #     open_kfid=open_kfid,
                        #     user=external_userid,
                        # )
                        continue

                    # 记录本批次的撤回，供后续剔除用
                    recalled_msgids_in_batch.add(recall_msgid)
                    # tlog(
                    #     "撤回消息",
                    #     "识别到撤回事件: event_msgid={event_msgid}, recall_msgid={recall_msgid}, "
                    #     "open_kfid={open_kfid}, user={user}, batch_size={batch_size}",
                    #     event_msgid=msg_id,
                    #     recall_msgid=recall_msgid,
                    #     open_kfid=open_kfid,
                    #     user=external_userid,
                    #     batch_size=len(recalled_msgids_in_batch),
                    # )

                    # 跨批次兜底：尝试标记已持久化的消息
                    try:
                        session = channel_session_manager.find_session(
                            channel_type="wecom_kf",
                            channel_user_id=external_userid,
                            channel_chat_id=open_kfid,
                            tenant_id=tenant_id,
                        )
                        if session:
                            session_id = session["session_id"]
                            marked_count = channel_session_manager.mark_recalled_message(
                                session_id=session_id,
                                recall_msgid=recall_msgid,
                                tenant_id=tenant_id,
                            )
                            # tlog(
                            #     "撤回消息",
                            #     "跨批次标记已持久化消息: session_id={session_id}, recall_msgid={recall_msgid}, "
                            #     "marked_count={marked_count}",
                            #     session_id=session_id,
                            #     recall_msgid=recall_msgid,
                            #     marked_count=marked_count,
                            # )
                        # else:
                        #     tlog(
                        #         "撤回消息",
                        #         "跨批次标记未找到会话: recall_msgid={recall_msgid}, user={user}",
                        #         recall_msgid=recall_msgid,
                        #         user=external_userid,
                        #     )
                    except Exception as _e:
                        logger.warning(
                            f"[wecom_kf] 跨批次标记已持久化消息异常: recall_msgid={recall_msgid}, error={_e}"
                        )

                    continue  # 撤回事件处理完毕，跳过后续 origin 过滤等逻辑
                # ===== 撤回消息处理结束 =====

                # ===== 客户扫码进入会话（enter_session）事件：引流归因 + 欢迎语 =====
                if msg_type == "event" and msg.get("event", {}).get("event_type") == "enter_session":
                    event_data = msg.get("event", {})
                    scene = event_data.get("scene", "")
                    ext_userid = event_data.get("external_userid", "")
                    welcome_code = event_data.get("welcome_code", "")

                    # 事件去重（enter_session 可能重复推送）
                    dedup = _get_tenant_dedup(tenant_id)
                    if await dedup.is_duplicate(f"enter_session:{msg_id}"):
                        continue

                    _kf_tlog(
                        "enter_session事件: tenant={tenant}, user={user}, scene={scene}, "
                        "open_kfid={open_kfid}, has_welcome_code={has_code}",
                        tenant=tenant_id, user=ext_userid, scene=scene,
                        open_kfid=open_kfid, has_code=bool(welcome_code),
                    )

                    # 1. 确保 C 端客户注册（幂等，扫码即建用户 → 计入引流数）
                    customer_user_id = None
                    if ext_userid:
                        try:
                            customer_user_id = await ensure_user_registered(
                                "wecom_kf", ext_userid, tenant_id, source="wecom_kf"
                            )
                        except Exception as e:
                            logger.warning(f"[wecom_kf] enter_session 注册客户失败: {e}")

                    # 2. 引流归因（first-touch）：scene 匹配到绑定引流员工的客服账号
                    if scene and customer_user_id:
                        try:
                            _cfg_id, kf_binding = resolve_scene(tenant_id, scene)
                            if kf_binding and kf_binding.get("tenant_user_id"):
                                CustomerReferralDB.record(
                                    tenant_id=tenant_id,
                                    referrer_user_id=kf_binding["tenant_user_id"],
                                    customer_user_id=customer_user_id,
                                    open_kfid=open_kfid,
                                    scene=scene,
                                )
                                _kf_tlog(
                                    "引流归因成功: tenant={tenant}, customer={customer}, "
                                    "referrer={referrer}, scene={scene}",
                                    tenant=tenant_id, customer=ext_userid,
                                    referrer=kf_binding["tenant_user_id"], scene=scene,
                                )
                        except Exception as e:
                            logger.warning(f"[wecom_kf] enter_session 引流归因失败: {e}")

                    # 3. 发送欢迎语（welcome_code 有效 + 配置了欢迎语）
                    if welcome_code and kf_config.get("welcome_message"):
                        try:
                            adapter.current_open_kfid = open_kfid
                            await adapter.send_welcome_message(
                                welcome_code, kf_config["welcome_message"]
                            )
                            _kf_tlog(
                                "enter_session欢迎语已发送: tenant={tenant}, user={user}",
                                tenant=tenant_id, user=ext_userid,
                            )
                        except Exception as e:
                            logger.warning(f"[wecom_kf] enter_session 欢迎语发送失败: {e}")

                    continue
                # ===== enter_session 处理结束 =====

                # 临时调试：记录 msg_list 中每条原始条目（含被过滤的事件型条目，如撤回事件）
                # 调试主题：微信事件
                # try:
                #     import json as _json
                    # tlog(
                    #     "微信事件",
                    #     "sync_msg 条目: open_kfid={open_kfid}, msgid={msgid}, "
                    #     "origin={origin}, msgtype={msgtype}, raw={raw}",
                    #     open_kfid=open_kfid,
                    #     msgid=msg_id,
                    #     origin=msg_origin,
                    #     msgtype=msg_type,
                    #     raw=_json.dumps(msg, ensure_ascii=False),
                    # )
                # except Exception:
                #     pass

                # 跳过非客户消息（origin=3 是客户，origin=4 是接待人员）
                if msg.get("origin") != 3:
                    # 员工消息（origin=5）：收集入库（只持久化不触发 AI），
                    # 同样过滤 30 分钟旧消息，避免 cursor 重放时旧员工消息污染上下文
                    if msg.get("origin") == 5 and not _is_stale_kf_message(msg):
                        dedup = _get_tenant_dedup(tenant_id)
                        if not await dedup.is_duplicate(f"servicer:{msg_id}"):
                            servicer_msgs_to_persist.append(msg)
                    continue

                # ===== 旧消息过滤：超过 30 分钟没有回复价值，直接丢弃 =====
                # 场景：cursor 到期 / 回调缺失积压时 sync_msg 会返回 3 天窗口内全部消息，
                # 旧消息多为已结束会话(remote=4)重放，回复发不出(trans(1) -> 95013)且客户早已离开。
                # send_time 为 Unix 秒级时间戳。过滤发生在合并之前，逐条判定更精确。
                if _is_stale_kf_message(msg):
                    logger.info(
                        f"[wecom_kf] 丢弃超过30分钟的旧消息: msgid={msg_id}, "
                        f"send_time={msg.get('send_time')}"
                    )
                    continue
                # ===== 旧消息过滤结束 =====

                # 仅处理文字 + 语音消息：图片/视频/文件等附件消息直接过滤，
                # 避免转发给智能体产生"看不了视频"等无效回复消耗积分
                from src.channels.wecom_kf.message import (
                    KF_FILTER_HINT_MESSAGE,
                    should_process_kf_message,
                )
                if not should_process_kf_message(msg_type):
                    logger.info(
                        f"[wecom_kf] 跳过非文字/语音消息: msgid={msg_id}, msgtype={msg_type}"
                    )
                    # 通知客户：无法识别该类型文件，请用文字或语音描述需求。
                    # 按客户维度节流（复用 dedup，5 分钟窗口），避免连续发文件刷屏
                    try:
                        ext_user = msg.get("external_userid", "")
                        if ext_user:
                            dedup = _get_tenant_dedup(tenant_id)
                            if not await dedup.is_duplicate(
                                f"kf_filter_hint:{tenant_id}:{ext_user}"
                            ):
                                adapter.current_open_kfid = open_kfid
                                await adapter.send_text(
                                    KF_FILTER_HINT_MESSAGE, ext_user
                                )
                    except Exception as e:
                        logger.warning(
                            f"[wecom_kf] 发送文件类型提示失败: msgid={msg_id}, error={e}"
                        )
                    continue

                # 消息去重
                dedup = _get_tenant_dedup(tenant_id)
                if await dedup.is_duplicate(msg_id):
                    logger.info(f"[wecom_kf] 重复消息已跳过: msgid={msg_id}")
                    continue

                valid_msgs.append(msg)

            # ===== 同批次剔除：从 valid_msgs 中剔除被撤回的消息 =====
            if recalled_msgids_in_batch:
                original_count = len(valid_msgs)
                valid_msgs = [m for m in valid_msgs if m.get("msgid") not in recalled_msgids_in_batch]
                filtered_count = original_count - len(valid_msgs)
                # if filtered_count > 0:
                #     tlog(
                #         "撤回消息",
                #         "同批次剔除: recall_count={recall_count}, filtered={filtered}, "
                #         "remaining={remaining}, recall_msgids={recall_msgids}",
                #         recall_count=len(recalled_msgids_in_batch),
                #         filtered=filtered_count,
                #         remaining=len(valid_msgs),
                #         recall_msgids=list(recalled_msgids_in_batch),
                #     )
            # ===== 同批次剔除结束 =====

            # 同一批次内合并同一用户的连续文本消息，避免逐条回复耗尽 WeCom 5条限额
            merged_msgs = _merge_consecutive_user_messages(valid_msgs)
            if len(merged_msgs) < len(valid_msgs):
                logger.info(
                    f"[wecom_kf] 消息合并: {len(valid_msgs)} -> {len(merged_msgs)}"
                )

            for msg in merged_msgs:
                msg_id = msg.get("msgid", "")

                # 先入库 send_time 早于本条客户消息的员工消息，保证时间正序穿插，
                # 避免把员工回复错配给后到的客户提问（C1→S1→C2→S2 必须保序）
                while servicer_msgs_to_persist and (
                    servicer_msgs_to_persist[0].get("send_time", 0)
                    < msg.get("send_time", 0)
                ):
                    _smsg = servicer_msgs_to_persist.pop(0)
                    await _persist_kf_servicer_message(
                        _smsg, open_kfid, tenant_id, subagent_type
                    )

                # 设置当前客服上下文（供发送消息使用）
                adapter.current_open_kfid = open_kfid

                # 解析消息
                unified_msg = await adapter.parse_message(msg)
                logger.info(
                    f"[wecom_kf] 解析消息: msgid={msg_id}, user={unified_msg.user_id}, "
                    f"text_len={len(unified_msg.text or '')}, msgtype={msg.get('msgtype')}"
                )
                _kf_tlog(
                    "消息处理开始: tenant={tenant}, msgid={msgid}, user={user}, "
                    "msgtype={msgtype}, text={text}, msg_keys={keys}",
                    tenant=tenant_id,
                    msgid=msg_id,
                    user=unified_msg.user_id,
                    msgtype=msg.get("msgtype", ""),
                    text=(unified_msg.text or "")[:200],
                    keys=list(msg.keys()),
                )

                # 获取客户信息（昵称、头像）
                user_info = await adapter.get_user_info(unified_msg.user_id)
                logger.info(
                    f"[wecom_kf] 客户信息: user={unified_msg.user_id}, "
                    f"name={user_info.get('name')}, has_avatar={bool(user_info.get('avatar'))}"
                )
                _kf_tlog(
                    "客户信息: tenant={tenant}, user={user}, name={name}, has_avatar={has_avatar}",
                    tenant=tenant_id,
                    user=unified_msg.user_id,
                    name=user_info.get("name"),
                    has_avatar=bool(user_info.get("avatar")),
                )

                # 自动注册用户并获取 user_id
                try:
                    from src.saas.services.auto_register import ensure_user_registered
                    user_id = await ensure_user_registered("wecom_kf", unified_msg.user_id, tenant_id, user_info, source="wecom_kf")
                    user_info["user_id"] = user_id
                    _kf_tlog(
                        "用户注册成功: tenant={tenant}, external_userid={ext_user}, user_id={user_id}",
                        tenant=tenant_id,
                        ext_user=unified_msg.user_id,
                        user_id=user_id,
                    )
                except Exception as e:
                    logger.warning(f"[wecom_kf] 自动注册失败: {e}")
                    _kf_tlog(
                        "用户注册失败: tenant={tenant}, external_userid={ext_user}, error={error}",
                        tenant=tenant_id,
                        ext_user=unified_msg.user_id,
                        error=str(e),
                        level="ERROR",
                    )

                # 构建会话元数据（存储头像等扩展信息）
                session_metadata = {}
                if user_info.get("avatar"):
                    session_metadata["avatar"] = user_info["avatar"]
                if user_info.get("gender"):
                    session_metadata["gender"] = user_info["gender"]

                # 获取或创建会话
                session = channel_session_manager.get_or_create_session(
                    channel_type="wecom_kf",
                    channel_user_id=unified_msg.user_id,
                    tenant_id=tenant_id,
                    user_info=user_info,
                    subagent_id=subagent_type or "",
                    channel_chat_id=open_kfid,
                    metadata=session_metadata if session_metadata else None,
                )
                session_id = session["session_id"]
                _kf_tlog(
                    "会话获取/创建: tenant={tenant}, session_id={session_id}, "
                    "subagent_id={subagent_id}, user={user}, open_kfid={open_kfid}",
                    tenant=tenant_id,
                    session_id=session_id,
                    subagent_id=subagent_type or "",
                    user=unified_msg.user_id,
                    open_kfid=open_kfid,
                )

                # 检查会话是否已在人工接待中，避免 AI 重复处理
                session_metadata = session.get("metadata") or {}
                logger.info(
                    f"[wecom_kf] 会话状态检查: session_id={session_id}, "
                    f"service_state={session_metadata.get('service_state')}, "
                    f"exit_human_timeout_failed_at={session_metadata.get('exit_human_timeout_failed_at', 'N/A')}, "
                    f"has_metadata_keys={list(session_metadata.keys()) if session_metadata else 'None'}"
                )
                if session_metadata.get("service_state") == 3:
                    # 如果之前超时退出人工失败，尝试查询微信侧实际状态来校准
                    if session_metadata.get("exit_human_timeout_failed_at"):
                        try:
                            state_result = await adapter.api_client.get_service_state(
                                open_kfid, unified_msg.user_id
                            )
                            actual_state = state_result.get("service_state")
                            if actual_state == 1:
                                # 微信侧已是智能助手接待，更新本地状态，继续 AI 处理
                                logger.info(
                                    f"[wecom_kf] 超时失败会话已恢复为智能助手: "
                                    f"session_id={session_id}"
                                )
                                channel_session_manager.update_session(
                                    session_id=session_id,
                                    metadata={"service_state": 1},
                                )
                            elif actual_state == 3:
                                # 微信侧仍是人工状态，跳过 AI 处理
                                logger.info(
                                    f"[wecom_kf] 超时失败会话仍在人工接待中，跳过AI处理: "
                                    f"session_id={session_id}"
                                )
                                await _persist_kf_context_customer_message(
                                    unified_msg, msg, session_id, open_kfid, tenant_id,
                                    "customer_human",
                                )
                                continue
                            elif actual_state == 4:
                                # 会话已结束，微信禁止变更状态（errcode=95013），不调 trans。
                                # 同步本地状态后跳过 AI 处理
                                channel_session_manager.update_session(
                                    session_id=session_id,
                                    metadata={"service_state": 4},
                                )
                                logger.info(
                                    f"[wecom_kf] 超时失败会话已结束(remote=4)，跳过AI处理: "
                                    f"session_id={session_id}"
                                )
                                await _persist_kf_context_customer_message(
                                    unified_msg, msg, session_id, open_kfid, tenant_id,
                                    "customer_ended",
                                )
                                continue
                            elif actual_state == 0:
                                # 微信侧状态为未处理(0)，尝试切回智能助手
                                logger.info(
                                    f"[wecom_kf] 超时失败会话微信侧状态=0，"
                                    f"尝试切回智能助手: session_id={session_id}"
                                )
                                recover_result = await adapter.api_client.trans_service_state(
                                    open_kfid=open_kfid,
                                    external_userid=unified_msg.user_id,
                                    service_state=1,
                                )
                                if recover_result.get("errcode", 0) == 0:
                                    channel_session_manager.update_session(
                                        session_id=session_id,
                                        metadata={"service_state": 1},
                                    )
                                    logger.info(
                                        f"[wecom_kf] 超时失败会话已恢复为智能助手: "
                                        f"session_id={session_id}"
                                    )
                                    # 继续正常 AI 处理（不 continue）
                                else:
                                    logger.warning(
                                        f"[wecom_kf] 超时失败会话切回智能助手失败: "
                                        f"session_id={session_id}, errcode={recover_result.get('errcode')}"
                                    )
                                    await _persist_kf_context_customer_message(
                                        unified_msg, msg, session_id, open_kfid, tenant_id,
                                        "customer_human",
                                    )
                                    continue
                            else:
                                # 微信侧是待接入池(2)，不允许机器人发消息
                                logger.warning(
                                    f"[wecom_kf] 超时失败会话微信侧状态={actual_state}，"
                                    f"不允许发送消息: session_id={session_id}"
                                )
                                await _persist_kf_context_customer_message(
                                    unified_msg, msg, session_id, open_kfid, tenant_id,
                                    "customer_human",
                                )
                                continue
                        except Exception as e:
                            logger.warning(
                                f"[wecom_kf] 查询微信侧会话状态失败，跳过AI处理: "
                                f"session_id={session_id}, error={e}"
                            )
                            continue
                    else:
                        # 无超时失败标记，先查询微信远程状态校准，防止员工已结束但本地状态未更新
                        try:
                            state_result = await adapter.api_client.get_service_state(
                                open_kfid, unified_msg.user_id
                            )
                            actual_state = state_result.get("service_state")
                            if actual_state == 1:
                                # 微信侧已是智能助手接待，更新本地状态，继续 AI 处理
                                channel_session_manager.update_session(
                                    session_id=session_id,
                                    metadata={"service_state": 1},
                                )
                                logger.info(
                                    f"[wecom_kf] 远程状态校准：微信侧已恢复智能助手，继续AI处理: "
                                    f"session_id={session_id}"
                                )
                                # 继续正常 AI 处理（不 continue）
                            elif actual_state == 4:
                                # 会话已结束，微信禁止变更状态（errcode=95013），不调 trans。
                                # 同步本地状态后跳过 AI 处理
                                channel_session_manager.update_session(
                                    session_id=session_id,
                                    metadata={"service_state": 4},
                                )
                                logger.info(
                                    f"[wecom_kf] 远程状态校准：会话已结束(remote=4)，跳过AI处理: "
                                    f"session_id={session_id}"
                                )
                                await _persist_kf_context_customer_message(
                                    unified_msg, msg, session_id, open_kfid, tenant_id,
                                    "customer_ended",
                                )
                                continue
                            elif actual_state == 0:
                                # 微信侧状态为未处理(0)，尝试切回智能助手
                                recover_result = await adapter.api_client.trans_service_state(
                                    open_kfid=open_kfid,
                                    external_userid=unified_msg.user_id,
                                    service_state=1,
                                )
                                if recover_result.get("errcode", 0) == 0:
                                    channel_session_manager.update_session(
                                        session_id=session_id,
                                        metadata={"service_state": 1},
                                    )
                                    logger.info(
                                        f"[wecom_kf] 远程状态=0，已切回智能助手: "
                                        f"session_id={session_id}"
                                    )
                                    # 继续正常 AI 处理（不 continue）
                                else:
                                    logger.warning(
                                        f"[wecom_kf] 远程状态=0，切回智能助手失败: "
                                        f"session_id={session_id}, errcode={recover_result.get('errcode')}"
                                    )
                                    await _persist_kf_context_customer_message(
                                        unified_msg, msg, session_id, open_kfid, tenant_id,
                                        "customer_human",
                                    )
                                    continue
                            elif actual_state == 3:
                                # 微信侧仍是人工接待，检查是否要退出人工服务
                                if adapter.should_exit_human(unified_msg.text or "", kf_config):
                                    await _exit_kf_human_service(adapter, open_kfid, unified_msg.user_id, session_id)
                                    await _persist_kf_context_customer_message(
                                        unified_msg, msg, session_id, open_kfid, tenant_id,
                                        "customer_human",
                                    )
                                    continue
                                logger.info(
                                    f"[wecom_kf] 会话仍在人工接待中，跳过AI处理: "
                                    f"session_id={session_id}, user={unified_msg.user_id}"
                                )
                                await _persist_kf_context_customer_message(
                                    unified_msg, msg, session_id, open_kfid, tenant_id,
                                    "customer_human",
                                )
                                continue
                            else:
                                # 微信侧是待接入池(2)或其他未知状态
                                logger.warning(
                                    f"[wecom_kf] 远程状态={actual_state}，不允许发送消息: "
                                    f"session_id={session_id}"
                                )
                                await _persist_kf_context_customer_message(
                                    unified_msg, msg, session_id, open_kfid, tenant_id,
                                    "customer_human",
                                )
                                continue
                        except Exception as e:
                            logger.warning(
                                f"[wecom_kf] 查询微信侧会话状态失败，跳过AI处理: "
                                f"session_id={session_id}, error={e}"
                            )
                            continue

                # 自动注册用户
                user_id = None
                try:
                    user_id = await ensure_user_registered("wecom_kf", unified_msg.user_id, tenant_id, user_info, source="wecom_kf")
                except Exception as e:
                    logger.warning(f"[wecom_kf] 自动注册失败: {e}")

                # 设置工具可访问的上下文
                # user_id：租户侧注册用户（ensure_user_registered 生成），供留资等工具记录线索归属
                # lead_capture：会话留资状态机快照（已留资则工具拒绝重复留资）
                set_kf_context({
                    "adapter": adapter,
                    "open_kfid": open_kfid,
                    "external_userid": unified_msg.user_id,
                    "kf_config": kf_config,
                    "session_id": session_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "lead_capture": session_metadata.get("lead_capture"),
                })

                # 发送前校验微信远程会话状态，防止本地状态与远程不一致导致 95018
                try:
                    remote_state = await adapter.api_client.get_service_state(
                        open_kfid, unified_msg.user_id
                    )
                    remote_service_state = remote_state.get("service_state")
                    logger.info(
                        f"[wecom_kf] 远程状态校验: session_id={session_id}, "
                        f"local_service_state={session_metadata.get('service_state')}, "
                        f"remote_service_state={remote_service_state}"
                    )
                    if remote_service_state != 1:
                        if remote_service_state == 4:
                            # 会话已结束，微信禁止变更状态（errcode=95013）。
                            # 不调 trans、不跑 AI（回复大概率发不出，白耗积分），同步本地状态。
                            channel_session_manager.update_session(
                                session_id=session_id,
                                metadata={"service_state": 4},
                            )
                            logger.info(
                                f"[wecom_kf] 会话已结束(remote=4)，跳过AI处理: "
                                f"session_id={session_id}"
                            )
                            await _persist_kf_context_customer_message(
                                unified_msg, msg, session_id, open_kfid, tenant_id,
                                "customer_ended",
                            )
                            continue
                        # 远程状态 0(未处理)：可切回智能助手，行为不变
                        if remote_service_state == 0:
                            logger.info(
                                f"[wecom_kf] 远程状态=0，尝试切回智能助手: "
                                f"session_id={session_id}"
                            )
                            recover_result = await adapter.api_client.trans_service_state(
                                open_kfid=open_kfid,
                                external_userid=unified_msg.user_id,
                                service_state=1,
                            )
                            if recover_result.get("errcode", 0) == 0:
                                channel_session_manager.update_session(
                                    session_id=session_id,
                                    metadata={"service_state": 1},
                                )
                                logger.info(
                                    f"[wecom_kf] 远程状态已恢复为智能助手: "
                                    f"session_id={session_id}"
                                )
                                # 继续正常 AI 处理（不 continue）
                            else:
                                logger.warning(
                                    f"[wecom_kf] 远程状态切回智能助手失败: "
                                    f"session_id={session_id}, errcode={recover_result.get('errcode')}"
                                )
                                await _persist_kf_context_customer_message(
                                    unified_msg, msg, session_id, open_kfid, tenant_id,
                                    "customer_human",
                                )
                                continue
                        else:
                            logger.warning(
                                f"[wecom_kf] 远程状态不允许机器人发送消息，跳过处理: "
                                f"session_id={session_id}, remote_state={remote_service_state}"
                            )
                            # 同步本地状态到远程实际状态
                            channel_session_manager.update_session(
                                session_id=session_id,
                                metadata={"service_state": remote_service_state},
                            )
                            await _persist_kf_context_customer_message(
                                unified_msg, msg, session_id, open_kfid, tenant_id,
                                "customer_human",
                            )
                            continue
                except Exception as e:
                    logger.warning(
                        f"[wecom_kf] 远程状态校验失败，继续处理: "
                        f"session_id={session_id}, error={e}"
                    )

                # 检查隐藏命令
                from src.core.hidden_commands import is_hidden_command, execute_hidden_command
                if is_hidden_command(unified_msg.text or ""):
                    reply_text = await execute_hidden_command(unified_msg.text or "", session_id, tenant_id)
                    if reply_text:
                        from src.models.message import UnifiedResponse
                        response = UnifiedResponse.from_text(
                            text=reply_text,
                            reply_to=unified_msg.user_id,
                            message_id=f"resp_{msg_id}",
                        )
                        await adapter.send_message(response)
                    continue

                # 保存用户消息
                msgtype = msg.get("msgtype", "")
                logger.info(f"[wecom_kf] 收到消息: msgtype={msgtype}, msg keys={list(msg.keys())}, msg={msg}")
                user_attachments = []
                if msgtype in ("image", "voice", "video", "file"):
                    logger.info(f"[wecom_kf] 检测到多媒体消息，开始下载附件: msgtype={msgtype}")
                    user_attachments = await _download_and_build_attachments(
                        adapter.api_client, msg, session_id, tenant_id
                    )
                    logger.info(f"[wecom_kf] 附件下载完成: count={len(user_attachments)}, attachments={[{'type': a.get('type'), 'file_name': a.get('file_name'), 'content_len': len(a.get('content',''))} for a in user_attachments]}")

                user_input = _build_user_input_for_agent(msg, user_attachments)

                # 语音消息：调用阿里云 ASR 转文字
                if msgtype == "voice" and user_input == "[语音消息]" and user_attachments:
                    logger.info(f"[微信语音] 匹配到语音分支: msgtype={msgtype}, user_input={user_input}, has_attachments={bool(user_attachments)}")
                    audio_content = user_attachments[0].get("content", "")
                    # 优先使用 magic bytes 检测结果（避免 WeCom 错误标记 .mp3）
                    audio_format = user_attachments[0].get("audio_format") \
                        or user_attachments[0].get("file_name", "").split(".")[-1] \
                        or "amr"
                    audio_sample_rate = user_attachments[0].get("sample_rate") or 16000
                    user_input = await _transcribe_voice_with_asr(
                        audio_content, audio_format, audio_sample_rate
                    )
                    logger.info(f"[微信语音] ASR 转文字完成: result_len={len(user_input)}, preview={user_input[:100]}")
                elif msgtype == "voice":
                    logger.warning(
                        "[微信语音] 未进入 ASR 分支: user_input={user_input!r}, has_attachments={has_attachments}, "
                        "条件检查: msgtype_voice={c1}, input_is_placeholder={c2}, has_attachments={c3}".format(
                            user_input=user_input,
                            has_attachments=bool(user_attachments),
                            c1=(msgtype == "voice"),
                            c2=(user_input == "[语音消息]"),
                            c3=bool(user_attachments),
                        )
                    )

                # 语音消息：先判断 ASR 是否识别成功（在加前缀之前判断）
                asr_success = msgtype == "voice" and not user_input.startswith("[语音消息")

                user_content = unified_msg.text or user_input

                # 语音消息：ASR 识别成功后用 "[ASR识别结果] 文本" 格式保存
                # （unified_msg.text 永远是 "[语音消息]"，ASR 结果在 user_input 中）
                if asr_success:
                    user_content = f"[ASR识别结果] {user_input}"

                # ASR 短句（<10 字）识别误差较高，加 [ASR识别结果] 前缀提示 LLM 来源，以便 LLM 进入宽容模式；长句识别率较高，无需宽容模式，不加前缀
                if asr_success and 0 < len(user_input) < 10:
                    user_input = f"[ASR识别结果] {user_input}"

                # 构建用户消息的附件元数据（保存到 channel_messages.attachments，不含 base64）
                user_attachments_meta = []
                for att in user_attachments:
                    user_attachments_meta.append({
                        "type": att["type"],
                        "media_id": att["media_id"],
                        "file_name": att["file_name"],
                        "mime_type": att["mime_type"],
                        "file_size": att["file_size"],
                        "local_path": att["local_path"],
                        "saved_at": att["saved_at"],
                    })

                # 保存用户消息 —— P0-2：推迟到 process_and_persist 内统一写入

                # 检查人工转接关键词
                if adapter.should_transfer_to_human(user_content, kf_config):
                    await _transfer_kf_to_human(adapter, session, kf_config, open_kfid, unified_msg.user_id, session_id)
                    continue

                # 客服账号到期 / 积分用尽拦截（不计费，回固定话术）
                if await _is_kf_account_blocked(
                    adapter, kf_config, tenant_id, open_kfid,
                    unified_msg, session_id, user_content,
                ):
                    continue

                # 路由到智能体
                agent = agent_router.get_agent(subagent_type, session_id, tenant_id=tenant_id)
                _kf_tlog(
                    "Agent路由: tenant={tenant}, session_id={session_id}, "
                    "subagent_type={subagent_type}, agent_class={agent_class}, "
                    "model={model}, user_input_len={user_input_len}, user_input={user_input}",
                    tenant=tenant_id,
                    session_id=session_id,
                    subagent_type=subagent_type,
                    agent_class=agent.__class__.__name__,
                    model=agent.llm.get_model_name() if agent.llm else "unknown",
                    user_input_len=len(user_input),
                    user_input=user_input[:300],
                )

                # 开始记录 token 消耗
                record_service = SessionRecordManager.start_record(
                    session_id=session_id,
                    user_id=user_id or unified_msg.user_id,
                    user_message=user_content,
                    tenant_id=tenant_id,
                    source_type="wecom_kf",
                )
                record_service.set_model(agent.llm.get_model_name())
                record_service.set_provider(agent.llm.get_provider_name())

                # ASR 计费（按次计费，识别成功才计费；必须在 start_record 之后，否则 get_current_record 返回 None）
                # 注意：SpeechToTextTool 内部也已补 add_asr_usage，但仅当调用时已有当前 record 才生效；
                # 渠道侧 ASR 在 start_record（2161）之前调用（2095），此时 get_current_record 恒为 None，
                # 故工具内部不会计费，需在此处外层补计。agent 主循环路径由工具内部计费，无此外层调用，不会双计。
                if asr_success:
                    try:
                        record_service.add_asr_usage(calls=1)
                    except Exception:
                        logger.opt(exception=True).debug("Failed to record ASR usage")

                # 处理消息（通过 progress_callback 捕获可下载文件 + 本轮 tool 消息序列）
                downloadable_files = []
                tool_messages_collected = []  # 本轮 tool 消息序列，供事务持久化到 channel_messages

                # 语音消息：ASR 识别成功后，不再将语音附件传递给 agent，避免 LLM 误以为需要处理语音识别
                agent_attachments_input = user_attachments if not asr_success else []
                if asr_success:
                    logger.info(f"[微信语音] ASR 识别成功，过滤语音附件: user_input={user_input!r}")

                agent_attachments = _build_attachments_for_agent(agent_attachments_input)
                logger.info(f"[agent_call] agent_attachments: count={len(agent_attachments)}, items={[{'type': a['type'], 'name': a['name'], 'content_len': len(a['content']), 'mime': a['mime_type']} for a in agent_attachments]}")
                logger.info(f"[微信消息] 进入会话队列: session_id={session_id}, user_input_len={len(user_input)}")

                # 处理消息 + 持久化（P0-1 / P0-2 统一在 process_and_persist 内完成）
                # verbose 配置解析（Phase 3，设计 §11）：渠道 config.verbose_feedback
                # > 旧 waiting_indicator 映射（enabled/delay_seconds/message 原样保留语义）
                # > 全局 agent.verbose_feedback > 代码默认
                _verbose_cfg = resolve_verbose_feedback_config(
                    channel_cfg=getattr(adapter, "verbose_feedback", None),
                    legacy_waiting_indicator=getattr(adapter, "waiting_indicator", None),
                )
                # owner 级 5 次回复预算（设计 §9.3）：verbose/final 共享；
                # verbose 关闭时不注入预算，final 行为与历史完全一致
                _kf_reply_budget = (
                    WeComKfReplyBudget(total=5) if _verbose_cfg.effective_enabled else None
                )
                _base_send_response = channel_session_manager.make_send_response(
                    adapter=adapter,
                    message_id=msg_id,
                    reply_to=unified_msg.user_id,
                    log_tag="[wecom_kf]",
                    reply_budget=_kf_reply_budget,
                )
                _send_verbose = channel_session_manager.make_send_verbose(
                    adapter=adapter,
                    event_id=msg_id,
                    reply_to=unified_msg.user_id,
                    log_tag="[wecom_kf]",
                    reply_budget=_kf_reply_budget,
                )

                async def send_response(response_text, downloadable_files, images=None):
                    # 若本轮 LLM 已成功调用 transfer_to_human，会话已切到人工状态，
                    # 微信客服接口会拒收后续消息（errcode=95018），跳过发送
                    if _has_successful_transfer_to_human(tool_messages_collected):
                        logger.info(
                            f"[wecom_kf] 检测到本轮已成功转人工，跳过 LLM 追加回复: "
                            f"session_id={session_id}, "
                            f"suppressed_text_len={len(response_text or '')}"
                        )
                        _kf_tlog(
                            "转人工后跳过LLM回复: session_id={sid}, suppressed_len={n}, "
                            "suppressed_preview={p}",
                            sid=session_id,
                            n=len(response_text or ""),
                            p=(response_text or "")[:200],
                        )
                        return True
                    return await _base_send_response(response_text, downloadable_files, images)

                assistant_metadata = None
                # 预先构造 assistant_metadata（downloadable_files 当前为空，由 process_and_persist 内部填充）
                # 实际值在 process_and_persist 内通过 closure 引用重新计算，这里占位为 None
                # 真正写入时由 process_and_persist 透传：downloadable_files 在 send_response 后已填充，
                # 但批量写入发生在 send_response 之前——为保持「assistant_metadata 反映 downloadable_files」语义，
                # 此处不传，改由下方在 process_and_persist 调用前预填：
                # （downloadable_files 此时为空，故仍为 None；保留这块逻辑用于未来扩展）

                # 构造 user_metadata：同批次合并消息写 merged_from_msgids + merged_segments，
                # 单条消息写 msgid。跨请求合并（session_queue）的 merged 字段由 process_and_persist 内部填充。
                if "merged_from_msgids" in msg:
                    user_metadata = {
                        "open_kfid": open_kfid,
                        "merged_from_msgids": msg["merged_from_msgids"],
                        "merged_segments": msg["merged_segments"],
                    }
                else:
                    user_metadata = {"msgid": msg_id, "msgtype": msgtype, "open_kfid": open_kfid}

                try:
                    # 处理消息（Phase 3：等待提示由 verbose 机制接管——旧 waiting_indicator
                    # 配置经 resolve_verbose_feedback_config 映射进新机制，只发一次，无双发）
                    result = await channel_session_manager.process_and_persist(
                        session_id=session_id,
                        tenant_id=tenant_id,
                        user_content=user_content,
                        user_metadata=user_metadata,
                        user_attachments_meta=user_attachments_meta if user_attachments_meta else None,
                        message_type=unified_msg.message_type,
                        agent=agent,
                        agent_user_input=user_input,
                        agent_attachments=agent_attachments if agent_attachments else None,
                        record_service=record_service,
                        tool_messages_collected=tool_messages_collected,
                        assistant_metadata=assistant_metadata,
                        send_response=send_response,
                        send_verbose=_send_verbose,
                        verbose_feedback_config=_verbose_cfg,
                    )
                    _kf_tlog(
                        "process_and_persist完成: tenant={tenant}, session_id={session_id}, "
                        "status={status}, response_text_len={resp_len}, response_text={response_text}",
                        tenant=tenant_id,
                        session_id=session_id,
                        status=result.get("status"),
                        resp_len=len(result.get("response_text") or ""),
                        response_text=(result.get("response_text") or "")[:500],
                    )
                except Exception as e:
                    logger.opt(exception=True).error(f"[wecom_kf] Agent 处理异常: {e}")
                    _kf_tlog(
                        "Agent处理异常: tenant={tenant}, session_id={session_id}, error={error}",
                        tenant=tenant_id,
                        session_id=session_id,
                        error=str(e),
                        level="ERROR",
                    )
                    record_service.mark_error(str(e))
                    SessionRecordManager.end_record()
                    continue

                SessionRecordManager.end_record()
                logger.info(
                    f"[微信消息] 队列处理返回: status={result.get('status')}, "
                    f"response_text_len={len(result.get('response_text') or '')}"
                )
                if result["status"] == "merged":
                    # 消息被合并/排队，本调用方无需发送回复
                    continue

                processed_messages += 1

            # flush 剩余员工消息（晚于本页最后一条客户消息；
            # 跨页时它们早于下页首条客户消息，顺序仍正确）
            while servicer_msgs_to_persist:
                _smsg = servicer_msgs_to_persist.pop(0)
                await _persist_kf_servicer_message(
                    _smsg, open_kfid, tenant_id, subagent_type
                )

            # 更新 cursor
            adapter.cursor_manager.set_cursor(open_kfid, cursor)

        logger.info(
            f"[wecom_kf] 后台处理完成: tenant={tenant_id}, open_kfid={open_kfid}, "
            f"total_messages={total_messages}, processed_messages={processed_messages}"
        )
        _kf_tlog(
            "后台任务完成: tenant={tenant}, open_kfid={open_kfid}, "
            "total_messages={total}, processed_messages={processed}",
            tenant=tenant_id,
            open_kfid=open_kfid,
            total=total_messages,
            processed=processed_messages,
        )

    except Exception as e:
        logger.error(f"[wecom_kf] 后台处理失败: tenant={tenant_id}, error={e}")
        _kf_tlog(
            "后台任务异常: tenant={tenant}, error={error}",
            tenant=tenant_id,
            error=str(e),
            level="ERROR",
        )


async def _persist_kf_servicer_message(
    msg: dict, open_kfid: str, tenant_id: str, subagent_type: str
) -> None:
    """员工消息（origin=5）落库到 channel_messages：只持久化，不触发 AI。

    content 前缀 `[人工客服] ` 让 LLM 明确区分「客户发言」与「人工客服发言」，
    避免把员工消息误当客户提问。员工姓名 MVP 用固定前缀，servicer_userid 存
    metadata 供后续按企微 API 反查。员工消息仅文本入库（语音/图片/文件跳过）。
    异常吞掉不影响主流程（去重 key 已在收集时标记，TTL 内不重试、过期后重拉可补）。
    """
    try:
        external_userid = msg.get("external_userid", "")
        if not external_userid:
            return
        # 员工消息仅文本入库（与 should_process_kf_message 口径一致）
        if msg.get("msgtype") != "text":
            return
        text = msg.get("text", {}).get("content", "")
        if not text:
            return

        session = channel_session_manager.get_or_create_session(
            channel_type="wecom_kf",
            channel_user_id=external_userid,
            tenant_id=tenant_id,
            subagent_id=subagent_type or "",
            channel_chat_id=open_kfid,
        )
        session_id = session["session_id"]

        metadata = {
            "source": "servicer",
            "servicer_userid": msg.get("servicer_userid", ""),
            "msgid": msg.get("msgid", ""),
            "open_kfid": open_kfid,
        }
        channel_session_manager.add_message(
            session_id=session_id,
            role="user",
            content=f"[人工客服] {text}",
            message_type="text",
            tenant_id=tenant_id,
            metadata=metadata,
        )
        _kf_tlog(
            "员工消息落库: tenant={tenant}, session_id={sid}, msgid={mid}, "
            "servicer={servicer}, text={text}",
            tenant=tenant_id,
            sid=session_id,
            mid=msg.get("msgid", ""),
            servicer=msg.get("servicer_userid", ""),
            text=text[:100],
        )
    except Exception as e:
        logger.warning(
            f"[wecom_kf] 员工消息落库失败（去重已标记，TTL 内不重试、过期后重拉可补）: "
            f"msgid={msg.get('msgid')}, error={e}"
        )


async def _persist_kf_context_customer_message(
    unified_msg, msg: dict, session_id: str, open_kfid: str, tenant_id: str, source: str
) -> None:
    """人工期/已结束期客户消息落库到 channel_messages：只持久化，不触发 AI。

    source 取值 customer_human（人工接待期）/ customer_ended（已结束会话积压），
    仅用于前端展示与事后排查，不影响 LLM 上下文重建（都是 role=user）。
    语音占位符（`[语音消息]`）在人工期不做 ASR，过滤不落库。
    """
    try:
        text = getattr(unified_msg, "text", None)
        if not text or text.startswith("[语音消息"):
            return  # 语音占位符不入库（人工期不做 ASR）

        metadata = {
            "source": source,
            "msgid": msg.get("msgid", ""),
            "open_kfid": open_kfid,
        }
        channel_session_manager.add_message(
            session_id=session_id,
            role="user",
            content=text,
            message_type="text",
            tenant_id=tenant_id,
            metadata=metadata,
        )
        _kf_tlog(
            "人工期/已结束客户消息落库: tenant={tenant}, session_id={sid}, msgid={mid}, "
            "source={source}, text={text}",
            tenant=tenant_id,
            sid=session_id,
            mid=msg.get("msgid", ""),
            source=source,
            text=text[:100],
        )
    except Exception as e:
        logger.warning(
            f"[wecom_kf] 客户消息落库失败: msgid={msg.get('msgid')}, source={source}, error={e}"
        )


async def _handle_kf_enter_session(callback_root, adapter) -> None:
    """客户进入会话 → 发送欢迎语"""
    try:
        open_kfid = callback_root.findtext("OpenKfId", "")
        code = callback_root.findtext("Code", "")
        external_userid = callback_root.findtext("ExternalUserId", "")

        kf_config = adapter.get_kf_config(open_kfid)
        if not kf_config or not code:
            return

        welcome_text = kf_config.get("welcome_message", "")
        if not welcome_text:
            return

        adapter.current_open_kfid = open_kfid
        await adapter.send_welcome_message(code, welcome_text)
        logger.info(
            f"[wecom_kf] 欢迎语已发送: open_kfid={open_kfid}, user={external_userid}"
        )
    except Exception as e:
        logger.error(f"[wecom_kf] enter_session 处理异常: {e}")


async def _handle_kf_session_status_change(callback_root, tenant_id: str) -> None:
    """会话状态变更 → 更新本地元信息，结束对话时通知客户并清理上下文"""
    try:
        open_kfid = callback_root.findtext("OpenKfId", "")
        external_userid = callback_root.findtext("ExternalUserId", "")
        service_state = int(callback_root.findtext("ServiceState", "0"))
        servicer_userid = callback_root.findtext("ServicerUserId", "")

        updated_count = channel_session_manager.update_session_by_channel_user(
            tenant_id=tenant_id,
            channel_type="wecom_kf",
            channel_user_id=external_userid,
            metadata={
                "service_state": service_state,
                "open_kfid": open_kfid,
                "servicer_userid": servicer_userid,
            },
        )

        logger.info(
            f"[wecom_kf] 会话状态变更: open_kfid={open_kfid}, "
            f"user={external_userid}, state={service_state}, updated_sessions={updated_count}"
        )

        # 员工结束对话（service_state=4）时，通知客户（历史对话保留，由 system 标记消息防止 Agent 误触发转人工）
        if service_state == 4 and updated_count > 0:
            # 实验期埋点：记录会话结束事件时刻，便于与 wecom_kf_servicer_msg.log 比对
            # 验证结束前员工的最后消息是否被 sync_msg 拉到
            try:
                _tlog(
                    "wecom_kf_servicer_msg",
                    "[SESSION_END] open_kfid={kfid} ext_user={eu} servicer={sv} state=4",
                    kfid=open_kfid,
                    eu=external_userid,
                    sv=servicer_userid,
                )
            except Exception:
                pass
            await _on_kf_session_ended(tenant_id, open_kfid, external_userid)

    except Exception as e:
        logger.error(f"[wecom_kf] session_status_change 处理异常: {e}")


async def _on_kf_session_ended(tenant_id: str, open_kfid: str, external_userid: str) -> None:
    """员工结束微信客服对话后：通知客户（不再清除历史对话，转人工防误触发由 system 标记消息承担）"""
    try:
        from src.saas.services.channel_factory import ChannelFactory

        adapter, _, _ = await ChannelFactory.create_from_tenant_config(tenant_id, "wecom_kf")
        if adapter is None:
            logger.warning(
                f"[wecom_kf] 结束对话通知：无法创建 adapter: tenant_id={tenant_id}"
            )
            return

        adapter.current_open_kfid = open_kfid
        await adapter.send_text(
            "人工服务已结束，如需继续咨询请重新发送消息。",
            external_userid,
        )
        logger.info(
            f"[wecom_kf] 已向客户发送结束对话通知: "
            f"open_kfid={open_kfid}, user={external_userid}"
        )
        await adapter.close()
    except Exception as e:
        logger.error(f"[wecom_kf] 结束对话通知客户失败: {e}")


async def _transfer_kf_to_human(
    adapter, session, kf_config: dict, open_kfid: str, external_userid: str, session_id: str
) -> None:
    """将会话转接给人工客服"""
    try:
        servicer_list = kf_config.get("servicer_userid_list", [])
        if not servicer_list:
            logger.warning(f"[wecom_kf] 未配置人工客服: open_kfid={open_kfid}")
            return

        servicer_userid = servicer_list[0]

        # 先发送转接确认消息，再执行转接（转接后机器人不能再发消息）
        adapter.current_open_kfid = open_kfid
        await adapter.send_text("正在为您转接人工客服，请稍候...", external_userid)

        result = await adapter.transfer_to_human(open_kfid, external_userid, servicer_userid)

        if result:
            from datetime import datetime as _dt
            transferred_at = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
            channel_session_manager.update_session(
                session_id=session_id,
                metadata={
                    "service_state": 3,
                    "transferred_to": servicer_userid,
                    "last_transferred_at": transferred_at,
                },
            )
            # 写入 system 标记消息，避免 Agent 后续基于历史中的"转人工"字样再次触发转人工
            try:
                tenant_id = (session or {}).get("tenant_id", "") if isinstance(session, dict) else ""
                channel_session_manager.add_message(
                    session_id=session_id,
                    role="system",
                    content=(
                        f"[已转人工] 用户此前已请求转人工并已转接给人工客服（{servicer_userid}），"
                        f"该次请求已处理完成。历史对话中的「转人工」「找客服」「人工」等字样"
                        f"属于已处理的旧请求，除非用户当前消息再次明确请求人工服务，"
                        f"否则不要再次调用 transfer_to_human 工具。"
                    ),
                    message_type="text",
                    tenant_id=tenant_id,
                    metadata={
                        "kind": "transfer_to_human_marker",
                        "servicer_userid": servicer_userid,
                        "transferred_at": transferred_at,
                        "transfer_source": "keyword",
                    },
                )
            except Exception as e:
                logger.warning(f"[wecom_kf] 写入转人工 system 标记失败: {e}")
            logger.info(f"[wecom_kf] 已转接人工: servicer={servicer_userid}")
        else:
            logger.error(f"[wecom_kf] 转接失败: open_kfid={open_kfid}")
    except Exception as e:
        logger.error(f"[wecom_kf] 转人工异常: {e}")


async def _exit_kf_human_service(
    adapter, open_kfid: str, external_userid: str, session_id: str
) -> None:
    """退出人工服务（结束会话），用户新发消息自动走智能助手接待流程"""
    try:
        result = await adapter.end_human_service(open_kfid, external_userid)

        if result:
            # 会话结束后用户新发消息会进入新会话，自动走智能助手接待流程
            channel_session_manager.update_session(
                session_id=session_id,
                metadata={"service_state": 4},
            )
            logger.info(f"[wecom_kf] 已退出人工服务（会话已结束）: open_kfid={open_kfid}, user={external_userid}")

            adapter.current_open_kfid = open_kfid
            await adapter.send_text("已退出人工服务，本次会话已结束。如有新问题，请重新发送消息。", external_userid)
        else:
            logger.error(f"[wecom_kf] 退出人工服务失败: open_kfid={open_kfid}")
    except Exception as e:
        logger.error(f"[wecom_kf] 退出人工服务异常: {e}")
