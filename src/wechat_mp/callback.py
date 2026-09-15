"""微信公众号服务器配置回调接收端（产品版，WP4）。

路由：``/api/wechat-mp/callback/{config_id}``（每配置独立 token，配置存 tenant_channel_configs，
channel_type=wechat_mp，敏感字段经 src.wechat_mp.config_codec Fernet 加密）。

- GET  ：公众平台保存服务器配置时的 URL 有效性验证（验签回显 echostr），
  通过后记录 config_verified_at（三态之一，设计 §4）。
- POST ：事件/消息推送接收。验签 → 明文/安全模式（WXBizMsgCrypt AES 解密 + msg_signature 校验）
  → ToUserName 与配置 original_id 绑定校验 → MASSSENDJOBFINISH 逐子篇解析 ArticleUrl
  → 同事务写 events(pending) + queued run + pending items 三件套 → 返回 success。

可靠接收语义（设计 §3 / 计划 WP4）：
- 事件幂等键 event_key = MsgID + Event，命中重复直接 success 不重复建行。
- DB 不可用返回 500 让微信重试，不确认未持久化的接收。
- 本端点只做「受理入库队列」，实际抓取由 WP5 worker 完成；其他事件类型记日志恒 success。
- 凭据/token 不进日志与响应。
"""

import asyncio
import hashlib
import hmac
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from loguru import logger
from psycopg2.extras import Json

from src.channels.wecom.crypto import WeComCrypto
from src.db.database import get_db_connection
from src.saas.db.channel_config_db import ChannelConfigDB
from src.wechat_mp.notify import notify_queued_work
from src.wechat_mp.service import normalize_batch_urls, upsert_article_rows

router = APIRouter(prefix="/api/wechat-mp", tags=["微信公众号回调"])

_MAX_BODY_BYTES = 1024 * 1024  # 1MB，事件推送正常只有几 KB
_WECHAT_MP_CHANNEL_TYPE = "wechat_mp"
_EVENT_MASSSENDFINISH = "MASSSENDJOBFINISH"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_signature(token: str, signature: str, timestamp: str, nonce: str) -> bool:
    """URL/消息验签：SHA1(sort([token, timestamp, nonce]))，compare_digest 防时序攻击。"""
    if not signature:
        return False
    raw = "".join(sorted([token, timestamp, nonce]))
    calculated = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return hmac.compare_digest(calculated, signature)


def _load_config(config_id: str) -> Optional[Dict[str, Any]]:
    """按 config_id 取 wechat_mp 配置（敏感字段解密明文，仅供服务端内部使用）。

    配置不存在或渠道类型不符返回 None（对外 404，不区分原因避免探测）。
    """
    cfg = ChannelConfigDB.get_by_id_decrypted(config_id)
    if not cfg or cfg.get("channel_type") != _WECHAT_MP_CHANNEL_TYPE:
        return None
    return cfg


def _is_enabled(cfg: Dict[str, Any]) -> bool:
    return bool((cfg.get("config") or {}).get("enabled", True))


def _touch_config_field(config_id: str, field: str, value: Any) -> None:
    """best-effort 更新 config JSON 三态字段；失败仅记日志，不影响回调主流程。"""
    try:
        ChannelConfigDB.update_config_field(config_id, field, value)
    except Exception as e:
        logger.warning(
            "wechat_mp callback: 更新配置字段失败 config_id={} field={}: {}",
            config_id, field, type(e).__name__,
        )


def _mark_verified(config_id: str) -> None:
    """GET URL 验证通过：记录 config_verified_at 并置 verified 标志（三态之一）。"""
    _touch_config_field(config_id, "config_verified_at", _utc_now_iso())
    try:
        ChannelConfigDB.set_verified(config_id, True)
    except Exception as e:
        logger.warning(
            "wechat_mp callback: set_verified 失败 config_id={}: {}", config_id, type(e).__name__
        )


def _record_error(config_id: str, message: str) -> None:
    """记录 last_error（脱敏后的固定文案，不含凭据/正文）。"""
    _touch_config_field(config_id, "last_error", f"{_utc_now_iso()} {message}")


# ------------------------------- 事件解析 -------------------------------


def _flatten_xml(root: ET.Element) -> Dict[str, str]:
    """提取顶层标量字段（嵌套节点另行处理）。"""
    return {child.tag: (child.text or "").strip() for child in root if len(child) == 0}


def _extract_article_urls(root: ET.Element) -> List[Tuple[str, str]]:
    """解析 MASSSENDJOBFINISH 的 ArticleUrlResult，返回 [(ArticleIdx, ArticleUrl), ...]（保序）。

    多图文时 ResultList 下多个 item 逐子篇给出 URL（WP0 实测结构，设计 §13.2）。
    item 可能缺失 URL 子节点，跳过并由调用方计数。
    """
    result: List[Tuple[str, str]] = []
    container = root.find("ArticleUrlResult")
    if container is None:
        return result
    for item in container.findall(".//item"):
        idx = (item.findtext("ArticleIdx") or "").strip()
        url = (item.findtext("ArticleUrl") or "").strip()
        if url:
            result.append((idx, url))
    return result


def _build_event_key(fields: Dict[str, str], raw: str) -> str:
    """事件幂等键：MsgID + Event；MsgID 缺失时退化为主体 hash（不丢幂等性）。"""
    msg_id = fields.get("MsgID") or ""
    event = fields.get("Event") or ""
    if msg_id:
        return f"{msg_id}:{event}"
    digest = hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()
    return f"noid:{digest}:{event}"


# ------------------------------- 受理入库 -------------------------------

# 返回值语义
_ACCEPT_ACCEPTED = "accepted"
_ACCEPT_DUPLICATE = "duplicate"
_ACCEPT_NO_VALID_URL = "no_valid_url"


def _accept_masssend_event(
    tenant_id: str,
    config_id: str,
    event_key: str,
    payload: Dict[str, Any],
    article_urls: List[Tuple[str, str]],
) -> str:
    """同事务写 events(pending) + queued run + pending items 三件套。

    - event_key 命中唯一约束 → duplicate（不重复建行，调用方直接 success）
    - 逐子篇 URL 经 normalize_batch_urls 校验 + 批内按 external_id 去重（WP5 CR P2：
      同一事件 ResultList 重复 URL 时不再撞 items UNIQUE(tenant_id,run_id,article_row_id)
      整体回滚 500），再经 upsert_article_rows upsert 文章当前态行（与手动导入共用口径）
    - 无任何合法 URL → no_valid_url（整体回滚，不产生半成品队列）
    - DB 异常向上抛出（调用方返回 500 让微信重试）
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_events (tenant_id, config_id, event_key, payload, status)
                VALUES (%s, %s, %s, %s, 'pending')
                ON CONFLICT (tenant_id, config_id, event_key) DO NOTHING
                RETURNING id
                """,
                (tenant_id, config_id, event_key, Json(payload)),
            )
            row = cursor.fetchone()
            if not row:
                conn.rollback()
                return _ACCEPT_DUPLICATE
            event_id = row["id"]

            # 逐子篇校验 + 批内去重 + upsert 文章当前态行（external_id = URL 规范身份，设计 §5.4）
            identities, rejected, duplicates = normalize_batch_urls([u for _, u in article_urls])
            for item in rejected:
                logger.warning(
                    "wechat_mp callback: 子篇 URL 拒收 config_id={} url={}: {}",
                    config_id, item["url"], item["reason"],
                )
            for item in duplicates:
                logger.warning(
                    "wechat_mp callback: 子篇 URL 批内重复去重 config_id={} external_id={}",
                    config_id, item["external_id"],
                )
            accepted = upsert_article_rows(
                cursor, tenant_id, identities, config_id=config_id, source_channel="callback"
            )
            accepted_row_ids = [a["article_row_id"] for a in accepted]

            if not accepted_row_ids:
                conn.rollback()
                return _ACCEPT_NO_VALID_URL

            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_runs
                    (tenant_id, config_id, trigger_type, status, total_count)
                VALUES (%s, %s, 'callback', 'queued', %s)
                RETURNING id
                """,
                (tenant_id, config_id, len(accepted_row_ids)),
            )
            run_id = cursor.fetchone()["id"]

            for article_row_id in accepted_row_ids:
                cursor.execute(
                    """
                    INSERT INTO bs_wechat_mp_sync_items
                        (tenant_id, run_id, article_row_id, action, status)
                    VALUES (%s, %s, %s, 'new', 'pending')
                    """,
                    (tenant_id, run_id, article_row_id),
                )

            cursor.execute(
                "UPDATE bs_wechat_mp_events SET run_id = %s WHERE id = %s",
                (run_id, event_id),
            )
            conn.commit()
            # WP7：受理成功后 best-effort 唤醒 worker 领取（失败不抛异常，
            # 由 scheduler 60s 兜底扫描接管）；notify 内部自捕获，双保险再包一层
            try:
                notify_queued_work()
            except Exception as e:  # noqa: BLE001 唤醒失败不影响受理结果
                logger.bind(module="wechat_mp").debug(
                    "wechat_mp callback 队列唤醒通知异常（忽略）: {}", e
                )
            return _ACCEPT_ACCEPTED
        except Exception:
            conn.rollback()
            raise


# ------------------------------- 路由 -------------------------------


@router.get("/callback/{config_id}", response_class=PlainTextResponse)
async def callback_verify(
    config_id: str, signature: str = "", timestamp: str = "", nonce: str = "", echostr: str = ""
):
    """公众平台服务器配置 URL 验证：验签通过则原样回显 echostr，并记录 config_verified_at。"""
    cfg = await asyncio.to_thread(_load_config, config_id)
    if cfg is None:
        return PlainTextResponse("config not found", status_code=404)
    if not _is_enabled(cfg):
        logger.warning("wechat_mp callback: GET 配置已禁用 config_id={}", config_id)
        return PlainTextResponse("config disabled", status_code=403)

    token = (cfg.get("config") or {}).get("callback_token") or ""
    if not token:
        logger.error("wechat_mp callback: 配置缺 callback_token config_id={}", config_id)
        return PlainTextResponse("callback token missing", status_code=500)

    if not _check_signature(token, signature, timestamp, nonce):
        logger.warning("wechat_mp callback: GET 验签失败 config_id={}", config_id)
        return PlainTextResponse("signature mismatch", status_code=403)

    await asyncio.to_thread(_mark_verified, config_id)
    logger.bind(module="wechat_mp").info(
        "wechat_mp callback: URL 验证通过 config_id={} tenant_id={}", config_id, cfg["tenant_id"]
    )
    return PlainTextResponse(echostr)


@router.post("/callback/{config_id}", response_class=PlainTextResponse)
async def callback_receive(config_id: str, request: Request):
    """事件/消息推送接收：验签 → 解密（安全模式）→ 身份校验 → 受理入库 → success。"""
    cfg = await asyncio.to_thread(_load_config, config_id)
    if cfg is None:
        return PlainTextResponse("config not found", status_code=404)
    if not _is_enabled(cfg):
        logger.warning("wechat_mp callback: POST 配置已禁用 config_id={}", config_id)
        return PlainTextResponse("config disabled", status_code=403)

    config_data: Dict[str, Any] = cfg.get("config") or {}
    tenant_id = cfg["tenant_id"]
    token = config_data.get("callback_token") or ""
    if not token:
        logger.error("wechat_mp callback: 配置缺 callback_token config_id={}", config_id)
        return PlainTextResponse("callback token missing", status_code=500)

    q = request.query_params
    signature = q.get("signature", "")
    timestamp = q.get("timestamp", "")
    nonce = q.get("nonce", "")
    encrypt_type = q.get("encrypt_type", "raw")
    msg_signature = q.get("msg_signature", "")

    if not _check_signature(token, signature, timestamp, nonce):
        logger.warning("wechat_mp callback: POST 验签失败 config_id={}", config_id)
        return PlainTextResponse("signature mismatch", status_code=403)

    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        logger.warning("wechat_mp callback: 请求体超限 {} bytes config_id={}", len(body), config_id)
        return PlainTextResponse("success")

    raw = body.decode("utf-8", errors="replace")

    # 安全模式：外层 XML 取 Encrypt，msg_signature 校验 + AES 解密（receiveid=appid）
    if encrypt_type == "aes":
        try:
            outer = ET.fromstring(raw)
        except ET.ParseError:
            logger.warning("wechat_mp callback: 加密推送外层 XML 解析失败 config_id={}", config_id)
            return PlainTextResponse("success")
        encrypt_field = (outer.findtext("Encrypt") or "").strip()
        if not encrypt_field:
            logger.warning("wechat_mp callback: 加密推送缺 Encrypt 字段 config_id={}", config_id)
            return PlainTextResponse("success")

        aes_key = config_data.get("encoding_aes_key") or ""
        appid = config_data.get("appid") or ""
        if not aes_key or not appid:
            # 明确报错指引配置；返回非 success 让公众平台后台可见失败原因
            logger.error(
                "wechat_mp callback: 收到安全模式推送但配置缺 EncodingAESKey/appid config_id={}",
                config_id,
            )
            await asyncio.to_thread(
                _record_error, config_id, "收到加密消息但未配置 EncodingAESKey，请在渠道配置中补齐"
            )
            return PlainTextResponse(
                "config error: encoding_aes_key not configured", status_code=500
            )

        crypto = WeComCrypto(token=token, encoding_aes_key=aes_key, corp_id=appid)
        if not crypto.verify_signature(msg_signature, timestamp, nonce, encrypt_field):
            logger.warning("wechat_mp callback: msg_signature 校验失败 config_id={}", config_id)
            return PlainTextResponse("msg signature mismatch", status_code=403)
        try:
            raw = crypto.decrypt(encrypt_field)
        except ValueError as e:
            # 解密失败（含 appid 接收方校验不符）：不泄漏细节，仅记类型
            logger.warning(
                "wechat_mp callback: AES 解密失败 config_id={}: {}", config_id, type(e).__name__
            )
            return PlainTextResponse("decrypt failed", status_code=403)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        logger.warning("wechat_mp callback: 事件 XML 解析失败 config_id={}", config_id)
        return PlainTextResponse("success")

    fields = _flatten_xml(root)

    # 事件身份校验：ToUserName 必须等于配置的公众号原始 ID（gh_xxx）才受理
    original_id = (config_data.get("original_id") or "").strip()
    to_user = fields.get("ToUserName", "")
    if original_id and to_user != original_id:
        logger.warning(
            "wechat_mp callback: ToUserName 身份不符拒收 config_id={} tenant_id={}",
            config_id, tenant_id,
        )
        await asyncio.to_thread(
            _record_error, config_id, "收到非本配置公众号的事件（ToUserName 不符），已拒收"
        )
        return PlainTextResponse("success")

    msg_type = fields.get("MsgType", "")
    event = fields.get("Event", "")

    if msg_type == "event" and event == _EVENT_MASSSENDFINISH:
        article_urls = _extract_article_urls(root)
        event_key = _build_event_key(fields, raw)
        payload: Dict[str, Any] = dict(fields)
        payload["article_urls"] = [
            {"idx": idx, "url": url} for idx, url in article_urls
        ]
        try:
            result = await asyncio.to_thread(
                _accept_masssend_event, tenant_id, config_id, event_key, payload, article_urls
            )
        except Exception:
            # DB 不可用：返回 500 让微信重试，不确认未持久化的接收
            logger.opt(exception=True).error(
                "wechat_mp callback: 事件受理入库失败 config_id={} tenant_id={}",
                config_id, tenant_id,
            )
            return PlainTextResponse("internal error", status_code=500)

        if result == _ACCEPT_DUPLICATE:
            logger.bind(module="wechat_mp").info(
                "wechat_mp callback: 重复事件命中幂等键 config_id={} tenant_id={}",
                config_id, tenant_id,
            )
        elif result == _ACCEPT_NO_VALID_URL:
            logger.warning(
                "wechat_mp callback: MASSSENDJOBFINISH 无合法文章 URL config_id={} tenant_id={}",
                config_id, tenant_id,
            )
            await asyncio.to_thread(
                _record_error, config_id, "群发完成事件未解析到合法文章 URL"
            )
        else:
            await asyncio.to_thread(
                _touch_config_field, config_id, "last_event_at", _utc_now_iso()
            )
            logger.bind(module="wechat_mp").info(
                "wechat_mp callback: 群发事件已受理 config_id={} tenant_id={} 子篇数={}",
                config_id, tenant_id, len(article_urls),
            )
        return PlainTextResponse("success")

    # 其他事件类型：记日志恒 success（本端点只受理群发完成事件）
    await asyncio.to_thread(_touch_config_field, config_id, "last_event_at", _utc_now_iso())
    logger.bind(module="wechat_mp").info(
        "wechat_mp callback: 收到未处理事件 config_id={} tenant_id={} MsgType={} Event={}",
        config_id, tenant_id, msg_type, event,
    )
    return PlainTextResponse("success")
