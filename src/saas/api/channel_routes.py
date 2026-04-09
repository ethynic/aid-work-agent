"""
租户级渠道回调路由

路由：/t/{tenant_id}/wecom/callback 等
从 URL path 取 tenant_id → 查渠道配置构造 adapter → 查实例 → 处理消息。
"""

from fastapi import APIRouter, Request, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from loguru import logger

from src.saas.db.tenant_db import TenantDB
from src.saas.db.agent_instance_db import AgentInstanceDB
from src.saas.db.channel_config_db import ChannelConfigDB
from src.saas.services.channel_factory import ChannelFactory
from src.channels.session import channel_session_manager

router = APIRouter(tags=["租户渠道回调"])


async def _process_tenant_channel_message(
    tenant_id: str,
    channel_type: str,
    raw_body: bytes,
    raw_body_str: str,
):
    """
    处理租户级渠道消息

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
    instances = AgentInstanceDB.list_running_by_tenant(tenant_id)
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


# ==================== WeCom 租户回调 ====================

@router.get("/t/{tenant_id}/wecom/callback")
async def tenant_wecom_callback_get(
    tenant_id: str,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """企业微信租户回调验证"""
    adapter, _ = ChannelFactory.create_from_tenant_config(tenant_id, "wecom")
    if not adapter:
        return PlainTextResponse("No config", status_code=500)

    if not await adapter.verify_signature(msg_signature, timestamp, nonce, echostr):
        return PlainTextResponse("Invalid signature", status_code=403)
    return PlainTextResponse(echostr)


@router.post("/t/{tenant_id}/wecom/callback")
async def tenant_wecom_callback_post(tenant_id: str, request: Request):
    """企业微信租户消息回调"""
    body = await request.body()
    result = await _process_tenant_channel_message(tenant_id, "wecom", body, body.decode())
    return PlainTextResponse(result)


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
