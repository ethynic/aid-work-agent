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
import random
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
from src.services.session_record import SessionRecordManager

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
    2. 解析消息 → 通过 agent_router 获取 agent 处理
    """
    # 1. 创建渠道适配器
    adapter, config_id, subagent_type = ChannelFactory.create_from_tenant_config(tenant_id, channel_type)
    if not adapter:
        logger.error(f"No channel config for tenant {tenant_id}/{channel_type}")
        return "error: no channel config"

    # 2. 解析消息
    try:
        message = await adapter.parse_message({"body": raw_body_str})
    except Exception as e:
        logger.error(f"Failed to parse {channel_type} message for tenant {tenant_id}: {e}")
        return "error"

    # 忽略事件消息
    if hasattr(message, 'message_type') and message.message_type == "event":
        return "success"

    # 3. 自动注册用户
    try:
        from src.saas.services.auto_register import ensure_user_registered
        user_id = await ensure_user_registered(channel_type, message.user_id, tenant_id)
    except Exception as e:
        logger.warning(f"Auto-register failed for {channel_type}:{message.user_id}: {e}")
        user_id = None

    # 4. 获取或创建会话（带租户隔离）
    session = channel_session_manager.get_or_create_session(
        channel_type=channel_type,
        channel_user_id=message.user_id,
        tenant_id=tenant_id,
        subagent_id=subagent_type or "",
    )
    session_id = session["session_id"]

    # 5. 保存用户消息到 channel_messages
    if message.text:
        channel_session_manager.add_message(
            session_id=session_id,
            role="user",
            content=message.text,
            message_type=message.message_type if hasattr(message, 'message_type') else "text",
            metadata=getattr(message, 'raw_message', None),
            tenant_id=tenant_id,
        )

    # 6. 获取 agent（根据渠道配置的 subagent_type 路由）
    from src.core.agent_router import agent_router
    agent = agent_router.get_agent(subagent_type, session_id)

    # 7. 开始记录 token 消耗
    record_service = SessionRecordManager.start_record(
        session_id=session_id,
        user_id=user_id or message.user_id,
        user_message=message.text,
        tenant_id=tenant_id,
        source_type=channel_type,  # "wecom" / "dingtalk" / "feishu"
    )
    record_service.set_model(agent.llm.get_model_name())
    record_service.set_provider(agent.llm.get_provider_name())

    # 8. 处理消息
    try:
        response_text = await agent.process_message_sync(
            user_input=message.text,
            session_id=session_id,
            record_service=record_service,
        )
        record_service.complete(response_text)
    except Exception as e:
        logger.error(f"Agent error for tenant {tenant_id}: {e}")
        record_service.mark_error(str(e))
        SessionRecordManager.end_record()
        return "error"

    SessionRecordManager.end_record()

    # 9. 保存助手回复到 channel_messages
    channel_session_manager.add_message(
        session_id=session_id,
        role="assistant",
        content=response_text,
        message_type="text",
        tenant_id=tenant_id,
    )

    # 10. 发送响应
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
        adapter, _, _ = ChannelFactory.create_from_tenant_config(tenant_id, "wecom", config_id=config_id)
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

        # 创建/获取会话（带租户隔离）
        session = channel_session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id=message.user_id,
            tenant_id=tenant_id,
            subagent_id=subagent_type or "",
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
                tenant_id=tenant_id,
            )

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

        # 延迟等待提示：Agent 处理超过阈值时发送等待消息
        from src.config.settings import settings
        indicator_config = settings.wecom.waiting_indicator

        agent_task = asyncio.create_task(
            agent.process_message_sync(
                user_input=message.text,
                session_id=session_id,
                record_service=record_service,
            )
        )

        if indicator_config.enabled and indicator_config.messages:
            await asyncio.sleep(indicator_config.delay_seconds)
            if not agent_task.done():
                indicator_msg = random.choice(indicator_config.messages)
                try:
                    await adapter.send_waiting_indicator(message.user_id, indicator_msg)
                    logger.debug(
                        f"[Tenant WeCom] 等待提示已发送: user={message.user_id}, "
                        f"delay={indicator_config.delay_seconds}s"
                    )
                except Exception as e:
                    logger.warning(f"[Tenant WeCom] 等待提示发送失败: {e}")

        response_text = await agent_task
        record_service.complete(response_text)
        SessionRecordManager.end_record()

        # 记录助手回复
        channel_session_manager.add_message(
            session_id=session_id,
            role="assistant",
            content=response_text,
            message_type="text",
            tenant_id=tenant_id,
        )

        # 发送回复（自动拆分长消息）
        logger.info(
            f"[Tenant WeCom] 开始发送回复: user={message.user_id}, "
            f"content_len={len(response_text) if response_text else 0}, "
            f"session_id={session_id}"
        )
        send_result = await adapter.send_long_message(response_text, message.user_id)
        logger.info(
            f"[Tenant WeCom] 回复发送{'成功' if send_result else '失败'}: "
            f"user={message.user_id}, session_id={session_id}"
        )

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
    adapter, _, _ = ChannelFactory.create_from_tenant_config(tenant_id, "wecom", config_id=config_id)
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

        adapter, _, subagent_type = ChannelFactory.create_from_tenant_config(
            tenant_id, "wecom", config_id=config_id
        )
        if not adapter:
            logger.warning(f"[Tenant WeCom] 配置不存在: tenant={tenant_id}, config={config_id}")
            return PlainTextResponse("Config not found", status_code=404)

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
    adapter, _, _ = ChannelFactory.create_from_tenant_config(tenant_id, "dingtalk")
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
    adapter, _, _ = ChannelFactory.create_from_tenant_config(tenant_id, "wecom_kf", config_id=config_id)
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

        adapter, _, _ = ChannelFactory.create_from_tenant_config(tenant_id, "wecom_kf", config_id=config_id)
        if not adapter:
            logger.warning(f"[Tenant WeCom KF] 配置不存在: tenant={tenant_id}, config={config_id}")
            return PlainTextResponse("Config not found", status_code=404)

        # 解析 XML 获取加密内容
        root = ET.fromstring(body_str)
        encrypt = root.findtext("Encrypt", "")
        msg_signature_param = root.findtext("MsgSignature", "")
        timestamp_param = root.findtext("TimeStamp", str(int(time.time())))
        nonce_param = root.findtext("Nonce", "")

        # 获取查询参数中的签名
        query_params = dict(request.query_params)
        if not msg_signature_param:
            msg_signature_param = query_params.get("msg_signature", "")
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

        # 消息事件 → 后台异步拉取并处理
        if event == "kf_msg_or_event":
            logger.info(f"[Tenant WeCom KF] 创建后台任务拉取消息: open_kfid={open_kfid}")
            asyncio.create_task(
                _process_tenant_wecom_kf_messages(tenant_id, config_id, open_kfid, adapter)
            )
        # 会话状态变更 → 更新本地状态
        elif event == "change_type" and change_type == "session_status_change":
            logger.info(f"[Tenant WeCom KF] 会话状态变更事件: open_kfid={open_kfid}")
            await _handle_kf_session_status_change(callback_root, tenant_id)
        # 进入会话 → 发送欢迎语
        elif event == "enter_session":
            logger.info(f"[Tenant WeCom KF] 进入会话事件: open_kfid={open_kfid}")
            await _handle_kf_enter_session(callback_root, adapter)
        else:
            logger.warning(
                f"[Tenant WeCom KF] 未知事件类型: event={event}, change_type={change_type}, "
                f"decrypted_xml前200字符={decrypted_xml[:200]}"
            )

        return PlainTextResponse("success")

    except Exception as e:
        logger.error(f"[Tenant WeCom KF] POST 处理异常: tenant={tenant_id}, config={config_id}, error={e}")
        return PlainTextResponse("error", status_code=500)


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

        logger.info(
            f"[WeCom KF] 后台处理开始: tenant={tenant_id}, config={config_id}, open_kfid={open_kfid}"
        )

        # 查找客服账号配置
        kf_config = adapter.get_kf_config(open_kfid)
        if not kf_config:
            logger.warning(f"[WeCom KF] 未知的 open_kfid: {open_kfid}")
            return

        subagent_type = kf_config.get("subagent_type", "")
        logger.info(f"[WeCom KF] 客服配置: subagent_type={subagent_type}, open_kfid={open_kfid}")

        # 使用 cursor 分页拉取消息
        cursor = adapter.cursor_manager.get_cursor(open_kfid)
        has_more = True
        total_messages = 0
        processed_messages = 0

        while has_more:
            logger.info(f"[WeCom KF] sync_msg调用: cursor={cursor[:20]}..., open_kfid={open_kfid}")
            result = await adapter.api_client.sync_msg(cursor=cursor, limit=100)
            errcode = result.get("errcode", 0)
            errmsg = result.get("errmsg", "")
            has_more = result.get("has_more", 0) == 1
            cursor = result.get("next_cursor", "")
            msg_count = len(result.get("msg_list", []))
            total_messages += msg_count
            logger.info(
                f"[WeCom KF] sync_msg返回: errcode={errcode}, errmsg={errmsg}, "
                f"msg_count={msg_count}, has_more={has_more}"
            )
            if result.get("errcode", 0) != 0:
                logger.error(f"[WeCom KF] sync_msg 失败: errcode={result.get('errcode')}")
                return

            for msg in result.get("msg_list", []):
                msg_id = msg.get("msgid", "")
                msg_origin = msg.get("origin", "")
                msg_type = msg.get("msgtype", "")
                logger.debug(
                    f"[WeCom KF] 消息: msgid={msg_id}, origin={msg_origin}, "
                    f"msgtype={msg_type}, open_kfid={open_kfid}"
                )

                # 跳过非客户消息（origin=3 是客户，origin=4 是接待人员）
                if msg.get("origin") != 3:
                    logger.debug(f"[WeCom KF] 跳过非客户消息: msgid={msg_id}, origin={msg_origin}")
                    continue

                # 消息去重
                dedup = _get_tenant_dedup(tenant_id)
                if await dedup.is_duplicate(msg_id):
                    logger.info(f"[WeCom KF] 重复消息已跳过: msgid={msg_id}")
                    continue

                # 设置当前客服上下文（供发送消息使用）
                adapter.current_open_kfid = open_kfid

                # 解析消息
                unified_msg = await adapter.parse_message(msg)
                logger.info(
                    f"[WeCom KF] 解析消息: msgid={msg_id}, user={unified_msg.user_id}, "
                    f"text_len={len(unified_msg.text or '')}, msgtype={msg.get('msgtype')}"
                )

                # 获取或创建会话
                session = channel_session_manager.get_or_create_session(
                    channel_type="wecom_kf",
                    channel_user_id=unified_msg.user_id,
                    tenant_id=tenant_id,
                    subagent_id=subagent_type or "",
                    channel_chat_id=open_kfid,
                )
                session_id = session["session_id"]

                # 自动注册用户
                user_id = None
                try:
                    user_id = await ensure_user_registered("wecom_kf", unified_msg.user_id, tenant_id)
                except Exception as e:
                    logger.warning(f"[WeCom KF] 自动注册失败: {e}")

                # 设置工具可访问的上下文
                set_kf_context({
                    "adapter": adapter,
                    "open_kfid": open_kfid,
                    "external_userid": unified_msg.user_id,
                    "kf_config": kf_config,
                    "session_id": session_id,
                })

                # 保存用户消息
                if unified_msg.text:
                    channel_session_manager.add_message(
                        session_id=session_id,
                        role="user",
                        content=unified_msg.text,
                        message_type=unified_msg.message_type,
                        metadata={"msgid": msg_id, "msgtype": msg.get("msgtype"), "open_kfid": open_kfid},
                        tenant_id=tenant_id,
                    )

                # 检查人工转接关键词
                if adapter.should_transfer_to_human(unified_msg.text, kf_config):
                    await _transfer_kf_to_human(adapter, session, kf_config, open_kfid, unified_msg.user_id, session_id)
                    continue

                # 路由到智能体
                agent = agent_router.get_agent(subagent_type, session_id, tenant_id=tenant_id)

                # 开始记录 token 消耗
                record_service = SessionRecordManager.start_record(
                    session_id=session_id,
                    user_id=user_id or unified_msg.user_id,
                    user_message=unified_msg.text,
                    tenant_id=tenant_id,
                    source_type="wecom_kf",
                )
                record_service.set_model(agent.llm.get_model_name())
                record_service.set_provider(agent.llm.get_provider_name())

                # 处理消息
                try:
                    response_text = await agent.process_message_sync(
                        user_input=unified_msg.text or "[非文本消息]",
                        session_id=session_id,
                        record_service=record_service,
                    )
                    record_service.complete(response_text)
                except Exception as e:
                    logger.error(f"[WeCom KF] Agent 处理异常: {e}")
                    record_service.mark_error(str(e))
                    SessionRecordManager.end_record()
                    continue

                SessionRecordManager.end_record()

                # 保存助手回复
                channel_session_manager.add_message(
                    session_id=session_id,
                    role="assistant",
                    content=response_text,
                    message_type="text",
                    tenant_id=tenant_id,
                )

                # 发送回复
                response = UnifiedResponse.from_text(
                    text=response_text,
                    reply_to=unified_msg.user_id,
                    message_id=f"resp_{msg_id}",
                )
                send_result = await adapter.send_message(response)
                logger.info(
                    f"[WeCom KF] 回复发送{'成功' if send_result else '失败'}: "
                    f"msgid={msg_id}, user={unified_msg.user_id}, "
                    f"text_len={len(response_text) if response_text else 0}"
                )
                processed_messages += 1

            # 更新 cursor
            adapter.cursor_manager.set_cursor(open_kfid, cursor)

        logger.info(
            f"[WeCom KF] 后台处理完成: tenant={tenant_id}, open_kfid={open_kfid}, "
            f"total_messages={total_messages}, processed_messages={processed_messages}"
        )

    except Exception as e:
        logger.error(f"[WeCom KF] 后台处理失败: tenant={tenant_id}, error={e}")


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
            f"[WeCom KF] 欢迎语已发送: open_kfid={open_kfid}, user={external_userid}"
        )
    except Exception as e:
        logger.error(f"[WeCom KF] enter_session 处理异常: {e}")


async def _handle_kf_session_status_change(callback_root, tenant_id: str) -> None:
    """会话状态变更 → 更新本地元信息"""
    try:
        open_kfid = callback_root.findtext("OpenKfId", "")
        external_userid = callback_root.findtext("ExternalUserId", "")
        service_state = int(callback_root.findtext("ServiceState", "0"))
        servicer_userid = callback_root.findtext("ServicerUserId", "")

        session_id = f"{tenant_id}_wecom_kf_{external_userid}_"
        try:
            channel_session_manager.update_session(
                session_id=session_id,
                metadata={
                    "service_state": service_state,
                    "open_kfid": open_kfid,
                    "servicer_userid": servicer_userid,
                },
            )
        except Exception:
            pass  # session 可能尚未创建

        logger.info(
            f"[WeCom KF] 会话状态变更: open_kfid={open_kfid}, "
            f"user={external_userid}, state={service_state}"
        )
    except Exception as e:
        logger.error(f"[WeCom KF] session_status_change 处理异常: {e}")


async def _transfer_kf_to_human(
    adapter, session, kf_config: dict, open_kfid: str, external_userid: str, session_id: str
) -> None:
    """将会话转接给人工客服"""
    try:
        servicer_list = kf_config.get("servicer_userid_list", [])
        if not servicer_list:
            logger.warning(f"[WeCom KF] 未配置人工客服: open_kfid={open_kfid}")
            return

        servicer_userid = servicer_list[0]
        result = await adapter.transfer_to_human(open_kfid, external_userid, servicer_userid)

        if result:
            channel_session_manager.update_session(
                session_id=session_id,
                metadata={"service_state": 3, "transferred_to": servicer_userid},
            )
            logger.info(f"[WeCom KF] 已转接人工: servicer={servicer_userid}")

            # 发送转接确认消息
            adapter.current_open_kfid = open_kfid
            await adapter.send_text("正在为您转接人工客服，请稍候...", external_userid)
        else:
            logger.error(f"[WeCom KF] 转接失败: open_kfid={open_kfid}")
    except Exception as e:
        logger.error(f"[WeCom KF] 转人工异常: {e}")
