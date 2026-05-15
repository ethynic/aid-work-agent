"""
租户级渠道回调路由

路由：/t/{tenant_id}/wecom/callback 等
从 URL path 取 tenant_id → 查渠道配置构造 adapter → 查实例 → 处理消息。

多租户 WeCom 集成关键点:
- GET  /t/{tenant_id}/wecom/callback: 验签 + 解密 echostr
- POST /t/{tenant_id}/wecom/callback: 验签 + 解密消息 → 立即返回 "success" → 后台异步处理
- 每个租户有独立的渠道凭证和加解密配置
- 消息去重防止 WeCom 回调重试
"""

import asyncio
import time
import xml.etree.ElementTree as ET
from typing import Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from loguru import logger

from src.saas.db.tenant_db import TenantDB
from src.saas.db.agent_instance_db import AgentInstanceDB
from src.saas.db.channel_config_db import ChannelConfigDB
from src.saas.services.channel_factory import ChannelFactory
from src.channels.session import channel_session_manager
from src.channels.idempotency import MessageDeduplicator

router = APIRouter(tags=["租户渠道回调"])

# 每个租户有独立的消息去重器
_tenant_dedup_cache: dict[str, MessageDeduplicator] = {}


def _get_tenant_dedup(tenant_id: str) -> MessageDeduplicator:
    """获取或创建租户级别的消息去重器"""
    if tenant_id not in _tenant_dedup_cache:
        _tenant_dedup_cache[tenant_id] = MessageDeduplicator(ttl_seconds=300)
    return _tenant_dedup_cache[tenant_id]


async def _process_tenant_channel_message(
    tenant_id: str,
    channel_type: str,
    raw_body: bytes,
    raw_body_str: str,
):
    """
    处理租户级渠道消息（同步版本，用于钉钉/飞书）

    流程：
    1. 查租户渠道配置 → 构造 adapter
    2. 查绑定了此渠道的 agent_instance → 获取 agent
    3. 解析消息 → 通过 instance 的 agent 处理
    """
    # 1. 创建渠道适配器
    adapter, config_id = ChannelFactory.create_from_tenant_config(tenant_id, channel_type)
    if not adapter:
        logger.error(f"No channel config for tenant {tenant_id}/{channel_type}")
        return "error: no channel config"

    # 2. 查找绑定了此渠道的实例
    instances = AgentInstanceDB.list_by_tenant(tenant_id)
    target_instance = None
    for inst in instances:
        if inst.get("bound_channel_type") == channel_type:
            target_instance = inst
            break

    if not target_instance:
        logger.warning(f"No running instance bound to {channel_type} for tenant {tenant_id}")
        return "success"

    # 3. 解析消息
    try:
        message = await adapter.parse_message({"body": raw_body_str})
    except Exception as e:
        logger.error(f"Failed to parse {channel_type} message for tenant {tenant_id}: {e}")
        return "error"

    # 忽略事件消息
    if hasattr(message, 'message_type') and message.message_type == "event":
        return "success"

    # 4. 自动注册用户
    try:
        from src.saas.services.auto_register import ensure_user_registered
        user_id = await ensure_user_registered(channel_type, message.user_id, tenant_id)
    except Exception as e:
        logger.warning(f"Auto-register failed for {channel_type}:{message.user_id}: {e}")
        user_id = None

    # 5. 获取或创建会话
    session = channel_session_manager.get_or_create_session(
        channel_type=channel_type,
        channel_user_id=message.user_id,
    )
    session_id = session["session_id"]

    # 6. 通过 instance_manager 获取 agent
    from src.saas.services.instance_manager import instance_manager
    agent = instance_manager.get_agent(target_instance["instance_id"], None, session_id)

    if not agent:
        from src.core.agent_router import agent_router
        agent = agent_router.get_agent(None, session_id)

    # 7. 处理消息
    try:
        response_text = await agent.process_message_sync(
            user_input=message.text,
            session_id=session_id,
        )
    except Exception as e:
        logger.error(f"Agent error for tenant {tenant_id}: {e}")
        return "error"

    # 8. 发送响应
    try:
        from src.models.message import UnifiedResponse
        response = UnifiedResponse.from_text(
            text=response_text,
            reply_to=message.user_id,
            message_id=f"resp_{message.message_id}",
        )
        await adapter.send_message(response)
    except Exception as e:
        logger.error(f"Failed to send response for tenant {tenant_id}: {e}")

    return "success"


async def _process_tenant_wecom_background(
    tenant_id: str,
    message,
    target_instance: dict,
) -> None:
    """
    租户级 WeCom 消息后台异步处理

    在 asyncio.create_task 中执行，不阻塞回调响应。
    """
    adapter = None
    try:
        adapter, _ = ChannelFactory.create_from_tenant_config(tenant_id, "wecom")
        if not adapter:
            logger.error(f"[Tenant WeCom] adapter 不可用: tenant={tenant_id}")
            return

        # 自动注册用户
        try:
            from src.saas.services.auto_register import ensure_user_registered
            await ensure_user_registered("wecom", message.user_id, tenant_id)
        except Exception as e:
            logger.warning(f"[Tenant WeCom] 自动注册失败: {e}")

        # 创建/获取会话
        session = channel_session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id=message.user_id,
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

        # 获取 agent
        from src.saas.services.instance_manager import instance_manager
        agent = instance_manager.get_agent(
            target_instance["instance_id"], None, session_id
        )
        if not agent:
            from src.core.agent_router import agent_router
            agent = agent_router.get_agent(None, session_id)

        # Agent 处理
        response_text = await agent.process_message_sync(
            user_input=message.text,
            session_id=session_id,
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
        logger.error(f"[Tenant WeCom] 后台处理失败: tenant={tenant_id}, error={e}")
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

@router.get("/t/{tenant_id}/wecom/callback")
async def tenant_wecom_callback_get(
    tenant_id: str,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """
    企业微信租户回调验证

    流程: 验签 → 解密 echostr → 返回明文
    """
    adapter, _ = ChannelFactory.create_from_tenant_config(tenant_id, "wecom")
    if not adapter:
        return PlainTextResponse("No config", status_code=500)

    # 加密模式: 验签 + 解密
    if adapter.crypto:
        if not adapter.crypto.verify_signature(msg_signature, timestamp, nonce, echostr):
            logger.warning(f"[Tenant WeCom] 签名验证失败: tenant={tenant_id}")
            return PlainTextResponse("Invalid signature", status_code=403)

        try:
            plaintext = adapter.crypto.decrypt(echostr)
            logger.info(f"[Tenant WeCom] 回调验证成功: tenant={tenant_id}")
            return PlainTextResponse(plaintext)
        except Exception as e:
            logger.error(f"[Tenant WeCom] echostr 解密失败: tenant={tenant_id}, error={e}")
            return PlainTextResponse("Decryption failed", status_code=400)

    # 无加密模式: 简单验签
    if not await adapter.verify_signature(msg_signature, timestamp, nonce, echostr):
        return PlainTextResponse("Invalid signature", status_code=403)
    return PlainTextResponse(echostr)


@router.post("/t/{tenant_id}/wecom/callback")
async def tenant_wecom_callback_post(tenant_id: str, request: Request):
    """
    企业微信租户消息回调

    流程:
    1. 验签 + 解密消息
    2. 去重检查
    3. 立即返回 "success"
    4. 后台异步处理
    """
    try:
        body = await request.body()
        body_str = body.decode()

        adapter, _ = ChannelFactory.create_from_tenant_config(tenant_id, "wecom")
        if not adapter:
            return PlainTextResponse("No config", status_code=500)

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
            if not adapter.crypto.verify_signature(
                msg_signature, timestamp, nonce, encrypt
            ):
                logger.warning(f"[Tenant WeCom] POST 签名验证失败: tenant={tenant_id}")
                return PlainTextResponse("Invalid signature", status_code=403)

            try:
                decrypted_xml = adapter.crypto.decrypt(encrypt)
            except Exception as e:
                logger.error(f"[Tenant WeCom] 消息解密失败: tenant={tenant_id}, error={e}")
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

        # 查找绑定了此渠道的实例
        instances = AgentInstanceDB.list_by_tenant(tenant_id)
        target_instance = None
        for inst in instances:
            if inst.get("bound_channel_type") == "wecom":
                target_instance = inst
                break

        if not target_instance:
            logger.warning(f"[Tenant WeCom] 无运行实例: tenant={tenant_id}")
            return PlainTextResponse("success")

        # 立即返回 "success"，后台异步处理
        asyncio.create_task(
            _process_tenant_wecom_background(tenant_id, message, target_instance)
        )

        return PlainTextResponse("success")

    except Exception as e:
        logger.error(f"[Tenant WeCom] POST 处理异常: tenant={tenant_id}, error={e}")
        return PlainTextResponse("error", status_code=500)


# ==================== Dingtalk 租户回调 ====================

@router.get("/t/{tenant_id}/dingtalk/callback")
async def tenant_dingtalk_callback_get(
    tenant_id: str,
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """钉钉租户回调验证"""
    adapter, _ = ChannelFactory.create_from_tenant_config(tenant_id, "dingtalk")
    if not adapter:
        return PlainTextResponse("No config", status_code=500)

    if not await adapter.verify_signature(signature, timestamp, nonce, echostr):
        return PlainTextResponse("Invalid signature", status_code=403)
    return PlainTextResponse(echostr)


@router.post("/t/{tenant_id}/dingtalk/callback")
async def tenant_dingtalk_callback_post(tenant_id: str, request: Request):
    """钉钉租户消息回调"""
    body = await request.body()
    result = await _process_tenant_channel_message(tenant_id, "dingtalk", body, body.decode())
    return PlainTextResponse(result)


# ==================== Feishu 租户回调 ====================

@router.get("/t/{tenant_id}/feishu/callback")
async def tenant_feishu_callback_get(
    tenant_id: str,
    challenge: str = Query(None),
):
    """飞书租户回调验证"""
    if challenge:
        return {"challenge": challenge}
    return {"status": "ok"}


@router.post("/t/{tenant_id}/feishu/callback")
async def tenant_feishu_callback_post(tenant_id: str, request: Request):
    """飞书租户消息回调"""
    body = await request.body()
    result = await _process_tenant_channel_message(tenant_id, "feishu", body, body.decode())
    return JSONResponse({"code": 0, "msg": result})
