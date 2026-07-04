"""企微会话存档 HTTP API 客户端

与 C# ``clients/wecom-personal-rpa/.../ArchiveHttpClient.cs`` 行为对齐：
- GET /cgi-bin/gettoken：获取 access_token（Redis 缓存，提前 5 分钟刷新）
- POST /cgi-bin/msg/get_chat_data：按 seq 拉取一批密文
- errcode=45009（频率限制）→ 抛 ``WeComRateLimitException``，由 fetcher 决定暂停多久
- errcode!=0（其他错误）→ 抛 ``WeComApiException``

文档：https://developer.work.weixin.qq.com/document/path/91360

媒体下载（get_media_data）本期不实现，由客户端 C# ``ArchiveMediaDownloader`` 完成。
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import List, Optional

import httpx
from loguru import logger

from src.core.redis_client import redis_client


# 企微会话存档 API 端点
_GET_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
_GET_CHAT_DATA_URL = "https://qyapi.weixin.qq.com/cgi-bin/msg/get_chat_data"

# access_token Redis 缓存键前缀（TTL 由调用方按 expires_in - 300 设置）
_TOKEN_CACHE_KEY_PREFIX = "wecom_rpa:archive:token"

# 网络重试配置（与 C# 一致：1s / 3s / 9s）
_RETRY_DELAYS = (1, 3, 9)
_RETRY_MAX_ATTEMPTS = 3


class WeComRateLimitException(Exception):
    """企微 errcode=45009（接口频率限制）。

    Attributes:
        retry_after_seconds: 建议暂停的秒数（默认 60s，与 C# 一致）。
    """

    def __init__(self, retry_after_seconds: int = 60):
        super().__init__(f"企微返回 45009（频率限制），暂停 {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class WeComApiException(Exception):
    """企微 errcode != 0 异常。"""

    def __init__(self, api: str, errcode: int, errmsg: str):
        super().__init__(f"企微 {api} 调用失败：errcode={errcode} errmsg={errmsg}")
        self.api = api
        self.errcode = errcode
        self.errmsg = errmsg


@dataclass
class ChatDataItem:
    """单条会话存档密文条目（与 C# ChatDataItem 字段一致）。

    msgtype=text/image/file/voice/video/revoke/agree/disagree/...
    action=upload/recall 需要解密 encrypt_chat_msg；action=download/switch 跳过。
    """

    seq: int
    msg_id: str
    action: str  # upload / recall / download / switch
    from_: str
    tolist: List[str] = field(default_factory=list)
    roomid: Optional[str] = None
    msg_time: int = 0  # 秒级 Unix 时间戳
    msg_type: str = ""
    encrypt_random_key: str = ""  # base64，需 RSA 解密
    encrypt_chat_msg: str = ""  # base64，需 AES 解密


@dataclass
class ChatDataBatch:
    """会话存档密文批次。"""

    items: List[ChatDataItem] = field(default_factory=list)


def _token_cache_key(tenant_id: str, corpid: str) -> str:
    return f"{_TOKEN_CACHE_KEY_PREFIX}:{tenant_id}:{corpid}"


async def get_access_token(tenant_id: str, corpid: str, secret: str) -> str:
    """获取 access_token（Redis 缓存 + 提前 5 分钟刷新）。

    Args:
        tenant_id: 租户 ID（用于缓存隔离）。
        corpid: 企业 ID。
        secret: 会话存档 secret（注意：与会话存档 RSA 私钥配套，不是自建应用 secret）。

    Returns:
        access_token 字符串。

    Raises:
        ValueError: 参数为空。
        WeComApiException: 企微 errcode != 0。
        RuntimeError: 重试 3 次后仍失败。
    """
    if not tenant_id:
        raise ValueError("tenant_id 不能为空")
    if not corpid:
        raise ValueError("corpid 不能为空")
    if not secret:
        raise ValueError("secret 不能为空")

    cache_key = _token_cache_key(tenant_id, corpid)
    cached = redis_client.get(cache_key)
    if cached:
        return cached if isinstance(cached, str) else str(cached)

    url = f"{_GET_TOKEN_URL}?corpid={corpid}&corpsecret={secret}"
    json_resp = await _get_json_with_retry(url)
    _ensure_success(json_resp, "gettoken")

    expires_in = json_resp.get("expires_in", 7200)
    token = json_resp.get("access_token", "")
    if not token:
        raise WeComApiException("gettoken", -1, "响应中 access_token 为空")

    # 提前 5 分钟视为过期（避免边界），与 C# 一致
    redis_client.set(cache_key, token, ex=max(60, int(expires_in) - 300))
    logger.info(f"[ArchiveHttpClient] access_token 刷新成功 tenant={tenant_id} corp={corpid} expires={expires_in}s")
    return token


async def get_chat_data(access_token: str, seq: int, limit: int) -> ChatDataBatch:
    """按 seq 拉取一批会话存档密文。

    Args:
        access_token: 由 get_access_token 获取。
        seq: 起始 seq（拉取 seq > 此值的消息）。
        limit: 单批次上限（企微限制 ≤1000）。

    Returns:
        ChatDataBatch。

    Raises:
        ValueError: 参数非法。
        WeComRateLimitException: errcode=45009。
        WeComApiException: 其他 errcode != 0。
        RuntimeError: 重试 3 次后仍失败。
    """
    if not access_token:
        raise ValueError("access_token 不能为空")
    if limit < 1 or limit > 1000:
        raise ValueError(f"limit 应在 1~1000 之间，实际 {limit}")

    url = f"{_GET_CHAT_DATA_URL}?access_token={access_token}"
    body = json.dumps({"seq": seq, "limit": limit, "proxy": "", "last_snap_shot": 0})
    body_bytes = body.encode("utf-8")

    json_resp = await _post_json_with_retry(url, body_bytes)
    _ensure_success(json_resp, "get_chat_data")

    chatdata = json_resp.get("chatdata", []) or []
    items: List[ChatDataItem] = []
    for raw in chatdata:
        items.append(
            ChatDataItem(
                seq=int(raw.get("seq", 0)),
                msg_id=raw.get("msgid", "") or "",
                action=raw.get("action", "") or "",
                from_=raw.get("from", "") or "",
                tolist=[s for s in raw.get("tolist", []) or [] if s],
                roomid=raw.get("roomid"),
                msg_time=int(raw.get("msgtime", 0) or 0),
                msg_type=raw.get("msgtype", "") or "",
                encrypt_random_key=raw.get("encrypt_random_key", "") or "",
                encrypt_chat_msg=raw.get("encrypt_chat_msg", "") or "",
            )
        )

    return ChatDataBatch(items=items)


# ----------------- 内部工具 -----------------


async def _get_json_with_retry(url: str) -> dict:
    """GET JSON，重试 3 次（1s/3s/9s 退避）。"""
    last_exc: Optional[Exception] = None
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as ex:
            last_exc = ex
            logger.warning(f"[ArchiveHttpClient] GET 失败 第 {attempt + 1} 次: {ex}")
            if attempt < len(_RETRY_DELAYS):
                await asyncio.sleep(_RETRY_DELAYS[attempt])

    raise RuntimeError(f"GET {url} 失败（重试 {_RETRY_MAX_ATTEMPTS} 次）") from last_exc


async def _post_json_with_retry(url: str, body: bytes) -> dict:
    """POST JSON，重试 3 次；45009 立即抛 WeComRateLimitException（不重试）。"""
    last_exc: Optional[Exception] = None
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    content=body,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                )
                resp.raise_for_status()
                json_resp = resp.json()

                # 45009 频率限制：立即抛，由调用方决定暂停多久
                if int(json_resp.get("errcode", 0)) == 45009:
                    logger.warning(f"[ArchiveHttpClient] 企微返回 45009（频率限制）")
                    raise WeComRateLimitException(60)

                return json_resp
        except WeComRateLimitException:
            raise  # 不重试，直接向上传播
        except Exception as ex:
            last_exc = ex
            logger.warning(f"[ArchiveHttpClient] POST 失败 第 {attempt + 1} 次: {ex}")
            if attempt < len(_RETRY_DELAYS):
                await asyncio.sleep(_RETRY_DELAYS[attempt])

    raise RuntimeError(f"POST {url} 失败（重试 {_RETRY_MAX_ATTEMPTS} 次）") from last_exc


def _ensure_success(json_resp: dict, api: str) -> None:
    """检查企微 errcode != 0 抛 WeComApiException。"""
    errcode = int(json_resp.get("errcode", 0) or 0)
    if errcode != 0:
        errmsg = json_resp.get("errmsg", "") or ""
        raise WeComApiException(api, errcode, errmsg)
