"""企微会话存档回调路由处理函数

封装 server 模式下企微回调的两个场景：
- GET echostr URL 验证（企微首次配置回调时触发）
- POST 事件接收（企微推送"有新消息"事件，立即 200 + 异步触发 fetcher）

路由层（wecom_personal_rpa_routes.py）通过 body 格式区分两种来源：
- JSON body → 客户端 HMAC 上报（client 模式，已有路径）
- XML body  → 企微回调（server 模式，本模块处理）

设计文档：§5.2 / §6.7

不直接 bind 路由（保持 main.py 路由注册模式一致），由路由函数转发调用。
"""

import asyncio
import re
from typing import Any, Dict, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse
from loguru import logger

from src.channels.wecom_personal_rpa.archive import callback_crypto, fetcher
from src.channels.wecom_personal_rpa.archive.callback_crypto import SignatureError
from src.channels.wecom_personal_rpa.archive.credential_codec import FORCED_LISTEN_MODE
from src.saas.db.channel_config_db import ChannelConfigDB

# 企微回调 XML 中 <Encrypt> 字段的简单正则提取（避免引入 lxml 依赖）
_ENCRYPT_PATTERN = re.compile(rb"<Encrypt><!\[CDATA\[(.*?)\]\]></Encrypt>", re.DOTALL)


def is_archive_callback_request(request: Request, body: bytes) -> bool:
    """判断请求是否是企微会话存档回调（而非客户端 HMAC 上报）。

    企微回调 body 是 XML 格式，含 <Encrypt> 字段；
    客户端 HMAC 上报 body 是 JSON 格式。

    Args:
        request: FastAPI Request 对象（含 headers）。
        body: 原始请求 body 字节。

    Returns:
        True = 企微回调（server 模式路径），False = 客户端 HMAC（client 模式路径）。
    """
    content_type = request.headers.get("content-type", "").lower()
    # 企微回调 content-type 通常是 application/xml 或 text/xml；客户端 HMAC 是 application/json
    if "xml" in content_type:
        return True
    if "json" in content_type:
        return False
    # content-type 缺失时看 body 前 64 字节是否 XML
    head = body[:64].lstrip()
    if head.startswith(b"<"):
        return True
    if head.startswith(b"{") or head.startswith(b"["):
        return False
    return False


def _extract_creds(cfg: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """从已解密的 config dict 提取企微回调需要的 3 个凭证。

    返回 dict 含 corp_id / token / encoding_aes_key，或 None（凭证缺失）。
    """
    config_data = cfg.get("config") or {}
    corp_id = config_data.get("corp_id")
    token = config_data.get("token")
    encoding_aes_key = config_data.get("encoding_aes_key")
    if not all([corp_id, token, encoding_aes_key]):
        return None
    return {
        "corp_id": corp_id,
        "token": token,
        "encoding_aes_key": encoding_aes_key,
    }


def _ensure_server_mode(cfg: Dict[str, Any]) -> bool:
    """防御性检查：配置必须是 wecom_personal_rpa + listen_mode=server。

    codec 已强制 listen_mode='server'，理论上永远 True。这里防御性兜底。
    """
    if cfg.get("channel_type") != "wecom_personal_rpa":
        return False
    config_data = cfg.get("config") or {}
    return config_data.get("listen_mode", FORCED_LISTEN_MODE) == "server"


async def handle_archive_echostr(
    tenant_id: str, config_id: str, request: Request
) -> PlainTextResponse:
    """处理企微 URL 验证（GET echostr）。

    企微后台首次保存回调 URL 时发 GET 请求，query 含：
      msg_signature / timestamp / nonce / echostr

    流程：按 config_id 反查凭证 → 验签 + AES 解密 echostr → 返回明文。
    """
    msg_signature = request.query_params.get("msg_signature", "")
    timestamp = request.query_params.get("timestamp", "")
    nonce = request.query_params.get("nonce", "")
    echostr = request.query_params.get("echostr", "")

    cfg = ChannelConfigDB.get_by_tenant_and_id(tenant_id, config_id)
    if cfg is None or not _ensure_server_mode(cfg):
        logger.warning(
            f"[ArchiveCallback] GET echostr 配置无效 tenant={tenant_id} config={config_id}"
        )
        return PlainTextResponse("config not found", status_code=404)

    creds = _extract_creds(cfg)
    if creds is None:
        logger.warning(
            f"[ArchiveCallback] GET echostr 凭证缺失 tenant={tenant_id} config={config_id}"
        )
        return PlainTextResponse("credentials incomplete", status_code=400)

    try:
        plain_echostr = callback_crypto.verify_and_decode_echostr(
            token=creds["token"],
            encoding_aes_key=creds["encoding_aes_key"],
            corp_id=creds["corp_id"],
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echostr=echostr,
        )
        logger.info(
            f"[ArchiveCallback] GET echostr 验证成功 tenant={tenant_id} config={config_id}"
        )
        return PlainTextResponse(plain_echostr)
    except SignatureError as e:
        logger.warning(
            f"[ArchiveCallback] GET echostr 验签失败 tenant={tenant_id} config={config_id}: {e}"
        )
        return PlainTextResponse("verify failed", status_code=401)


async def handle_archive_event(
    tenant_id: str, config_id: str, request: Request, body: bytes
) -> JSONResponse:
    """处理企微事件回调（POST）。

    企微推送"有新消息"事件时 POST XML body，含 <Encrypt> 字段。
    流程：验签 + AES 解密 → 立即 200 OK → 异步触发 fetcher 拉取（5s 超时硬约束）。

    fetcher 内部已有 Redis 锁防并发，重复触发安全。
    """
    msg_signature = request.query_params.get("msg_signature", "")
    timestamp = request.query_params.get("timestamp", "")
    nonce = request.query_params.get("nonce", "")

    cfg = ChannelConfigDB.get_by_tenant_and_id(tenant_id, config_id)
    if cfg is None or not _ensure_server_mode(cfg):
        logger.warning(
            f"[ArchiveCallback] POST 配置无效 tenant={tenant_id} config={config_id}"
        )
        return JSONResponse({"code": -1, "msg": "config not found"}, status_code=404)

    creds = _extract_creds(cfg)
    if creds is None:
        logger.warning(
            f"[ArchiveCallback] POST 凭证缺失 tenant={tenant_id} config={config_id}"
        )
        return JSONResponse({"code": -1, "msg": "credentials incomplete"}, status_code=400)

    # 提取 <Encrypt> 字段
    match = _ENCRYPT_PATTERN.search(body)
    if match is None:
        logger.warning(
            f"[ArchiveCallback] POST body 无 <Encrypt> 字段 tenant={tenant_id}"
        )
        return JSONResponse({"code": -1, "msg": "invalid body"}, status_code=400)
    encrypt_field = match.group(1).decode("utf-8")

    # 验签 + 解密
    try:
        plain = callback_crypto.verify_and_decrypt_event(
            token=creds["token"],
            encoding_aes_key=creds["encoding_aes_key"],
            corp_id=creds["corp_id"],
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypt_field=encrypt_field,
        )
    except SignatureError as e:
        logger.warning(
            f"[ArchiveCallback] POST 验签失败 tenant={tenant_id} config={config_id}: {e}"
        )
        # 防御性：若 listen_mode='server' 验签失败，路由层不应再回退到客户端 HMAC
        # （理论上 server 模式租户的回调不会带 HMAC，回退会误判）
        return JSONResponse({"code": -1, "msg": "verify failed"}, status_code=401)

    # 立即响应 200，异步触发 fetcher（5s 超时硬约束由企微约定）
    asyncio.create_task(fetcher.fetcher.fetch_once(tenant_id, config_id))

    logger.info(
        f"[ArchiveCallback] POST 事件接收成功 tenant={tenant_id} config={config_id} "
        f"plain_preview={plain[:80]!r}"
    )
    return JSONResponse({"code": 0, "msg": "ok"})
