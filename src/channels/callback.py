"""
渠道回调路由

处理来自各第三方平台的回调消息，包括:
- 企业微信: 加解密 + 异步后台处理
- 钉钉: 消息回调
- 飞书: 消息回调
"""

import asyncio
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from loguru import logger

from src.channels.idempotency import MessageDeduplicator
from src.channels.manager import channel_manager
from src.channels.session import channel_session_manager
from src.core.agent import master_agent
from src.core.agent_router import agent_router
from src.models.message import UnifiedResponse
from src.config.settings import settings

router = APIRouter(tags=["渠道回调"])

# 消息去重器（WeCom 回调重试保护）
_wecom_dedup = MessageDeduplicator(ttl_seconds=300)


async def process_channel_message(
    channel_type: str,
    message,
    bound_agent: Optional[str] = None,
) -> str:
    """
    处理渠道消息的统一逻辑

    Args:
        channel_type: 渠道类型
        message: 解析后的统一消息
        bound_agent: 绑定的子智能体名称（None 表示使用主智能体）

    Returns:
        响应文本
    """
    try:
        logger.info(
            f"Received {channel_type} message: {message.user_id}, "
            f"content: {message.text[:100] if message.text else 'N/A'}..."
        )

        # 获取或创建会话
        adapter = channel_manager.get_adapter(channel_type)
        user_info = None

        if adapter:
            try:
                user_info = await adapter.get_user_info(message.user_id)
            except Exception as e:
                logger.warning(f"Failed to get user info: {e}")

        session = channel_session_manager.get_or_create_session(
            channel_type=channel_type,
            channel_user_id=message.user_id,
            user_info=user_info,
            channel_chat_id=message.raw_message.get("chat_id") or message.raw_message.get("ToUserName"),
            metadata={
                "message_type": message.message_type,
            },
        )

        session_id = session["session_id"]

        # 添加用户消息到会话
        if message.text:
            channel_session_manager.add_message(
                session_id=session_id,
                role="user",
                content=message.text,
                message_type=message.message_type,
                metadata=message.raw_message,
            )

        # 获取对话上下文
        history = channel_session_manager.get_conversation_context(session_id, max_messages=20)

        # 通过 AgentRouter 获取对应的 Agent 实例
        agent = agent_router.get_agent(bound_agent, session_id)

        # 处理消息
        response_text = await agent.process_message_sync(
            user_input=message.text,
            session_id=session_id,
            history=history,
        )

        # 添加助手回复到会话
        channel_session_manager.add_message(
            session_id=session_id,
            role="assistant",
            content=response_text,
            message_type="text",
        )

        # 发送响应
        if adapter:
            response = UnifiedResponse.from_text(
                text=response_text,
                reply_to=message.user_id,
                message_id=f"resp_{message.message_id}",
            )
            await adapter.send_message(response)

        return "success"

    except Exception as e:
        logger.error(f"Failed to process {channel_type} message: {e}")
        raise


# ==================== 飞书回调 ====================

@router.get("/feishu/callback")
async def feishu_callback_get(
    challenge: str = Query(None, description="验证挑战"),
):
    """飞书回调验证 endpoint"""
    if challenge:
        return {"challenge": challenge}
    return {"status": "ok"}


@router.post("/feishu/callback")
async def feishu_callback_post(request: Request):
    """飞书消息回调 endpoint"""
    try:
        body = await request.json()
        logger.debug(f"Feishu callback body: {body}")

        adapter = channel_manager.get_adapter("feishu")
        if not adapter:
            return JSONResponse({"code": 1, "msg": "Feishu adapter not configured"}, status_code=500)

        message = await adapter.parse_message(body)

        if message.message_type == "event":
            return JSONResponse({"code": 0, "msg": "ok"})

        await process_channel_message("feishu", message)

        return JSONResponse({"code": 0, "msg": "ok"})

    except Exception as e:
        logger.error(f"Failed to process Feishu callback: {e}")
        return JSONResponse({"code": 1, "msg": str(e)}, status_code=500)


# ==================== 钉钉回调 ====================

@router.get("/dingtalk/callback")
async def dingtalk_callback_get(
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """钉钉回调验证 endpoint"""
    adapter = channel_manager.get_adapter("dingtalk")
    if not adapter:
        return PlainTextResponse("Dingtalk adapter not configured", status_code=500)

    if not await adapter.verify_signature(signature, timestamp, nonce, echostr):
        logger.warning("Dingtalk signature verification failed")
        return PlainTextResponse("Invalid signature", status_code=403)

    return PlainTextResponse(echostr)


@router.post("/dingtalk/callback")
async def dingtalk_callback_post(request: Request):
    """钉钉消息回调 endpoint"""
    try:
        body = await request.body()
        body_str = body.decode()

        adapter = channel_manager.get_adapter("dingtalk")
        if not adapter:
            return PlainTextResponse("Dingtalk adapter not configured", status_code=500)

        import xml.etree.ElementTree as ET
        root = ET.fromstring(body_str)

        msg_type = root.findtext("MsgType", "text")
        from_user = root.findtext("FromUserName", "")
        content = root.findtext("Content", "")

        if msg_type == "event":
            return PlainTextResponse("success")

        from src.models.message import UnifiedMessage, MessageType, ChannelType
        from datetime import datetime

        message = UnifiedMessage(
            message_id=root.findtext("MsgId", f"dingtalk_{datetime.now().timestamp()}"),
            channel_type=ChannelType.DINGTALK,
            user_id=from_user,
            message_type=MessageType.TEXT if msg_type == "text" else MessageType.FILE,
            content={"text": content} if msg_type == "text" else {},
            raw_message={"body": body_str},
        )

        await process_channel_message("dingtalk", message)

        return PlainTextResponse("success")

    except Exception as e:
        logger.error(f"Failed to process Dingtalk callback: {e}")
        return PlainTextResponse("error", status_code=500)


# ==================== 企业微信回调 ====================

@router.get("/wecom/callback")
async def wecom_callback_get(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """
    企业微信回调验证 endpoint

    WeCom 配置回调 URL 时发送 GET 请求验证。
    当开启了消息加密时，需要:
    1. 验证签名: SHA1(sort([token, timestamp, nonce, echostr]))
    2. 解密 echostr
    3. 返回解密后的明文
    """
    adapter = channel_manager.get_adapter("wecom")
    if not adapter:
        return PlainTextResponse("WeCom adapter not configured", status_code=500)

    if not adapter.crypto:
        # 无加密模式: 简单验证
        if not await adapter.verify_signature(msg_signature, timestamp, nonce, echostr):
            logger.warning("WeCom signature verification failed")
            return PlainTextResponse("Invalid signature", status_code=403)
        return PlainTextResponse(echostr)

    # 加密模式: 验签 + 解密
    if not adapter.crypto.verify_signature(msg_signature, timestamp, nonce, echostr):
        logger.warning("WeCom signature verification failed")
        return PlainTextResponse("Invalid signature", status_code=403)

    try:
        plaintext = adapter.crypto.decrypt(echostr)
        logger.info("WeCom 回调 URL 验证成功")
        return PlainTextResponse(plaintext)
    except Exception as e:
        logger.error(f"WeCom echostr 解密失败: {e}")
        return PlainTextResponse("Decryption failed", status_code=400)


@router.post("/wecom/callback")
async def wecom_callback_post(request: Request):
    """
    企业微信消息回调 endpoint

    处理流程:
    1. 解析 XML 获取加密消息体
    2. 验证签名 + 解密
    3. 去重检查
    4. 立即返回 "success"（5 秒内）
    5. 后台异步处理消息 + 发送回复
    """
    try:
        body = await request.body()
        body_str = body.decode()

        adapter = channel_manager.get_adapter("wecom")
        if not adapter:
            return PlainTextResponse("WeCom adapter not configured", status_code=500)

        # 解析 XML 获取加密内容
        root = ET.fromstring(body_str)
        encrypt = root.findtext("Encrypt", "")
        msg_signature = root.findtext("MsgSignature", "")
        timestamp = root.findtext("TimeStamp", str(int(time.time())))
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
            # 验签
            if not adapter.crypto.verify_signature(
                msg_signature, timestamp, nonce, encrypt
            ):
                logger.warning("WeCom POST 签名验证失败")
                return PlainTextResponse("Invalid signature", status_code=403)

            # 解密
            try:
                decrypted_xml = adapter.crypto.decrypt(encrypt)
            except Exception as e:
                logger.error(f"WeCom 消息解密失败: {e}")
                return PlainTextResponse("Decryption failed", status_code=400)
        else:
            # 无加密模式: 使用原始 body
            decrypted_xml = body_str

        # 校验消息接收方
        msg_root = ET.fromstring(decrypted_xml)
        to_user_name = msg_root.findtext("ToUserName", "")
        if to_user_name and to_user_name != adapter.corp_id:
            logger.warning(
                f"ToUserName mismatch: expected {adapter.corp_id}, got {to_user_name}"
            )
            return PlainTextResponse("Invalid receiver", status_code=403)

        # 解析解密后的消息
        message = await adapter.parse_message({"body": decrypted_xml})

        # 事件消息处理
        if message.message_type == "event":
            event_type = message.content.get("event", "")
            logger.info(f"WeCom 事件: {event_type}, 用户: {message.user_id}")
            if event_type == "subscribe":
                welcome = (
                    settings.channels.wecom.welcome_message
                    or "你好！我是智能助手，可以帮你处理日常任务。\n"
                       "直接发送消息即可开始对话。\n"
                       "输入「帮助」查看支持的功能。"
                )
                asyncio.create_task(adapter.send_text(welcome, message.user_id))
            return PlainTextResponse("success")

        # 速率限制检查
        if not adapter._check_rate_limit(message.user_id):
            logger.warning(f"WeCom 消息被速率限制拦截: user={message.user_id}")
            return PlainTextResponse("success")

        # 去重检查
        if await _wecom_dedup.is_duplicate(message.message_id):
            logger.debug(f"WeCom 重复消息，跳过: {message.message_id}")
            return PlainTextResponse("success")

        # 时间戳验证（防重放）
        msg_time = message.raw_message.get("timestamp")
        if msg_time and isinstance(msg_time, (int, float)):
            if abs(time.time() - msg_time) > 300:
                logger.warning(f"WeCom 消息时间戳过期: {msg_time}")
                return PlainTextResponse("success")

        # 立即返回 "success"，后台异步处理
        asyncio.create_task(
            _process_wecom_message_background(message)
        )

        return PlainTextResponse("success")

    except Exception as e:
        logger.error(f"Failed to process WeCom callback: {e}")
        return PlainTextResponse("error", status_code=500)


async def _process_wecom_message_background(message) -> None:
    """
    WeCom 消息后台异步处理

    在 asyncio.create_task 中执行，不阻塞回调响应。
    """
    try:
        adapter = channel_manager.get_adapter("wecom")
        if not adapter:
            logger.error("WeCom adapter 不可用，无法处理后台消息")
            return

        # 获取用户信息
        user_info = None
        try:
            user_info = await adapter.get_user_info(message.user_id)
        except Exception as e:
            logger.warning(f"获取用户信息失败: {e}")

        # 创建/获取会话
        session = channel_session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id=message.user_id,
            user_info=user_info,
            metadata={"message_type": message.message_type},
        )
        session_id = session["session_id"]

        # 记录用户消息
        if message.text:
            channel_session_manager.add_message(
                session_id=session_id,
                role="user",
                content=message.text,
                message_type=message.message_type,
                metadata=message.raw_message,
            )

        # 获取对话上下文
        history = channel_session_manager.get_conversation_context(
            session_id, max_messages=20
        )

        # Agent 处理
        agent = agent_router.get_agent(None, session_id)
        response_text = await agent.process_message_sync(
            user_input=message.text,
            session_id=session_id,
            history=history,
        )

        # 记录助手回复
        channel_session_manager.add_message(
            session_id=session_id,
            role="assistant",
            content=response_text,
            message_type="text",
        )

        # 发送回复（自动拆分长消息）
        await adapter.send_long_message(response_text, message.user_id)

    except Exception as e:
        logger.error(f"WeCom 后台消息处理失败: {e}")
        # 尝试发送错误提示
        try:
            adapter = channel_manager.get_adapter("wecom")
            if adapter and message.user_id:
                await adapter.send_text(
                    "抱歉，处理您的消息时遇到了问题，请稍后重试。",
                    message.user_id,
                )
        except Exception:
            pass


# ==================== 渠道会话管理 API ====================

@router.get("/api/channels/sessions")
async def list_channel_sessions(
    channel_type: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 50,
):
    """列出会话"""
    sessions = channel_session_manager.list_sessions(
        channel_type=channel_type,
        user_id=user_id,
        limit=limit,
    )
    return JSONResponse({"sessions": sessions, "count": len(sessions)})


@router.get("/api/channels/sessions/{channel_type}/{channel_user_id}")
async def get_channel_session(channel_type: str, channel_user_id: str):
    """获取会话详情"""
    session = channel_session_manager.get_session(channel_type, channel_user_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    messages = channel_session_manager.get_messages(session["session_id"])

    return JSONResponse({
        "session": session,
        "messages": messages,
    })


@router.delete("/api/channels/sessions/{session_id}")
async def delete_channel_session(session_id: str):
    """删除会话"""
    success = channel_session_manager.delete_session(session_id)
    return JSONResponse({"success": success})
