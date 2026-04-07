"""
渠道回调路由

处理来自各第三方平台的回调消息
"""

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from loguru import logger

from src.channels.manager import channel_manager
from src.channels.session import channel_session_manager
from src.core.agent import master_agent
from src.core.agent_router import agent_router
from src.models.message import UnifiedResponse

router = APIRouter(tags=["渠道回调"])


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
        logger.info(f"Received {channel_type} message: {message.user_id}, content: {message.text[:100] if message.text else 'N/A'}...")

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
    """
    飞书回调验证endpoint

    飞书在配置回调URL时会发送GET请求进行验证
    """
    if challenge:
        # 飞书URL验证
        return {"challenge": challenge}
    return {"status": "ok"}


@router.post("/feishu/callback")
async def feishu_callback_post(request: Request):
    """
    飞书消息回调endpoint

    接收飞书推送的消息
    """
    try:
        body = await request.json()
        logger.debug(f"Feishu callback body: {body}")

        adapter = channel_manager.get_adapter("feishu")
        if not adapter:
            return JSONResponse({"code": 1, "msg": "Feishu adapter not configured"}, status_code=500)

        # 解析消息
        message = await adapter.parse_message(body)

        # 忽略非文本消息事件
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
    """
    钉钉回调验证endpoint

    钉钉在配置回调URL时会发送GET请求进行验证
    """
    adapter = channel_manager.get_adapter("dingtalk")
    if not adapter:
        return PlainTextResponse("Dingtalk adapter not configured", status_code=500)

    # 验证签名
    if not await adapter.verify_signature(signature, timestamp, nonce, echostr):
        logger.warning("Dingtalk signature verification failed")
        return PlainTextResponse("Invalid signature", status_code=403)

    # 返回解密后的echostr
    return PlainTextResponse(echostr)


@router.post("/dingtalk/callback")
async def dingtalk_callback_post(request: Request):
    """
    钉钉消息回调endpoint

    接收钉钉推送的消息
    """
    try:
        body = await request.body()
        body_str = body.decode()

        adapter = channel_manager.get_adapter("dingtalk")
        if not adapter:
            return PlainTextResponse("Dingtalk adapter not configured", status_code=500)

        # 解析消息（钉钉使用XML格式）
        import xml.etree.ElementTree as ET
        root = ET.fromstring(body_str)

        msg_type = root.findtext("MsgType", "text")
        from_user = root.findtext("FromUserName", "")
        content = root.findtext("Content", "")

        # 事件消息不回复
        if msg_type == "event":
            return PlainTextResponse("success")

        # 创建统一消息
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
    企业微信回调验证endpoint
    """
    adapter = channel_manager.get_adapter("wecom")
    if not adapter:
        return PlainTextResponse("WeCom adapter not configured", status_code=500)

    if not await adapter.verify_signature(msg_signature, timestamp, nonce, echostr):
        logger.warning("WeCom signature verification failed")
        return PlainTextResponse("Invalid signature", status_code=403)

    return PlainTextResponse(echostr)


@router.post("/wecom/callback")
async def wecom_callback_post(request: Request):
    """
    企业微信消息回调endpoint
    """
    try:
        body = await request.body()
        body_str = body.decode()

        adapter = channel_manager.get_adapter("wecom")
        if not adapter:
            return PlainTextResponse("WeCom adapter not configured", status_code=500)

        import xml.etree.ElementTree as ET
        root = ET.fromstring(body_str)

        msg_type = root.findtext("MsgType", "text")
        from_user = root.findtext("FromUserName", "")

        # 事件消息不回复
        if msg_type == "event":
            return PlainTextResponse("success")

        # 解析消息
        message = await adapter.parse_message({"body": body_str})

        await process_channel_message("wecom", message)

        return PlainTextResponse("success")

    except Exception as e:
        logger.error(f"Failed to process WeCom callback: {e}")
        return PlainTextResponse("error", status_code=500)


# ==================== 渠道会话管理 API ====================

@router.get("/api/channels/sessions")
async def list_channel_sessions(
    channel_type: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 50,
):
    """
    列出会话

    Args:
        channel_type: 渠道类型过滤
        user_id: 用户ID过滤
        limit: 限制条数
    """
    sessions = channel_session_manager.list_sessions(
        channel_type=channel_type,
        user_id=user_id,
        limit=limit,
    )
    return JSONResponse({"sessions": sessions, "count": len(sessions)})


@router.get("/api/channels/sessions/{channel_type}/{channel_user_id}")
async def get_channel_session(channel_type: str, channel_user_id: str):
    """
    获取会话详情

    Args:
        channel_type: 渠道类型
        channel_user_id: 渠道用户ID
    """
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
    """
    删除会话

    Args:
        session_id: 会话ID
    """
    success = channel_session_manager.delete_session(session_id)
    return JSONResponse({"success": success})
