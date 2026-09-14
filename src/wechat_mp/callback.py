"""
微信公众号服务器配置回调接收端（WP0-E 诊断端点）。

用途：验证"群发完成事件 MASSSENDJOBFINISH 是否携带文章 URL"，
支撑设计文档 docs/system/wechat-mp/wechat-mp-knowledge-ingestion-design.md D11 的路径决策。

- GET  /api/wechat-mp/callback：公众平台保存服务器配置时的 URL 有效性验证（回显 echostr）
- POST /api/wechat-mp/callback：事件/消息推送接收，验签后完整记录字段，恒返回 success

Token 从环境变量 WECHAT_MP_CALLBACK_TOKEN 读取，未配置时端点返回 503。
诊断阶段请在公众平台后台选择「明文模式」；加密模式（encrypt_type=aes）本端点只记录不解密。
本端点只读诊断：不写业务库、不回复用户消息、不触发任何发送动作。
"""

import hashlib
import os
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from loguru import logger

router = APIRouter(prefix="/api/wechat-mp", tags=["微信公众号回调"])

_MAX_BODY_BYTES = 1024 * 1024  # 1MB，事件推送正常只有几 KB


def _check_signature(token: str, signature: str, timestamp: str, nonce: str) -> bool:
    raw = "".join(sorted([token, timestamp, nonce]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() == signature


def _get_token() -> str:
    return os.environ.get("WECHAT_MP_CALLBACK_TOKEN", "").strip()


@router.get("/callback", response_class=PlainTextResponse)
async def callback_verify(signature: str = "", timestamp: str = "", nonce: str = "", echostr: str = ""):
    """公众平台服务器配置 URL 验证：验签通过则原样回显 echostr。"""
    token = _get_token()
    if not token:
        logger.error("wechat_mp callback: WECHAT_MP_CALLBACK_TOKEN 未配置")
        return PlainTextResponse("callback token not configured", status_code=503)
    if _check_signature(token, signature, timestamp, nonce):
        logger.info("wechat_mp callback: URL 验证通过")
        return PlainTextResponse(echostr)
    logger.warning("wechat_mp callback: URL 验证签名不匹配")
    return PlainTextResponse("signature mismatch", status_code=403)


@router.post("/callback", response_class=PlainTextResponse)
async def callback_receive(request: Request):
    """接收事件/消息推送：验签 → 解析 XML → 全字段记录日志 → 返回 success。"""
    token = _get_token()
    if not token:
        logger.error("wechat_mp callback: WECHAT_MP_CALLBACK_TOKEN 未配置")
        return PlainTextResponse("callback token not configured", status_code=503)

    q = request.query_params
    signature = q.get("signature", "")
    timestamp = q.get("timestamp", "")
    nonce = q.get("nonce", "")
    encrypt_type = q.get("encrypt_type", "raw")
    msg_signature = q.get("msg_signature", "")

    if not _check_signature(token, signature, timestamp, nonce):
        logger.warning("wechat_mp callback: POST 签名不匹配")
        return PlainTextResponse("signature mismatch", status_code=403)

    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        logger.warning("wechat_mp callback: 请求体超限 {} bytes", len(body))
        return PlainTextResponse("success")

    raw = body.decode("utf-8", errors="replace")

    if encrypt_type == "aes":
        # 诊断端点不实现 AES 解密；提示改用明文模式重测
        logger.warning(
            "wechat_mp callback: 收到加密模式推送（encrypt_type=aes, msg_signature={}…），"
            "诊断阶段请在公众平台后台改用明文模式。body {} bytes",
            msg_signature[:8], len(body),
        )
        return PlainTextResponse("success")

    try:
        root = ET.fromstring(raw)
        fields = {child.tag: (child.text or "").strip() for child in root if len(child) == 0}
        # 嵌套节点（如 CopyrightCheckResult/ArticleUrl）序列化记录
        nested = {}
        for child in root:
            if len(child) > 0:
                nested[child.tag] = ET.tostring(child, encoding="unicode")
        logger.bind(module="wechat_mp_callback").info(
            "公众号回调事件 | MsgType={} Event={} fields={} nested={}",
            fields.get("MsgType"), fields.get("Event"), fields, nested,
        )
    except ET.ParseError:
        logger.bind(module="wechat_mp_callback").warning(
            "公众号回调 XML 解析失败，原文前 500 字符: {}", raw[:500]
        )

    return PlainTextResponse("success")
