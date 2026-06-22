"""企业微信个人账号 RPA 渠道请求鉴权

实现 protocol.md §A.1 的鉴权契约：
- 取 X-Client-Id / X-Timestamp / X-Nonce / X-Signature 头
- 时间戳窗口校验（TIMESTAMP_TOLERANCE_SECONDS = 300）
- nonce 防重放（Redis SETNX + TTL，降级进程内 dict）
- HMAC-SHA256 常量时间比较

签名契约（见 schemas.py 顶部注释与 protocol.md §A.1）::

    sig = hmac_sha256(
        key = client_secret_bytes,           # 由 get_secret(client_id) 返回
        msg = client_id + timestamp + nonce + raw_body,  # 原始字节拼接，无分隔符
    ).hexdigest()                            # 小写十六进制

安全约束：
- 常量时间比较（hmac.compare_digest），避免时序侧信道。
- 任何日志/错误信息中不得出现明文 secret 或签名串。
- nonce 防重放：Redis 不可用时降级进程内 dict，并记录 warning。

签名逐字对齐 protocol.md §B.1。
"""

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Callable, Optional

from loguru import logger

from src.channels.wecom_personal_rpa.schemas import (
    HEADER_CLIENT_ID,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    NONCE_TTL_SECONDS,
    TIMESTAMP_TOLERANCE_SECONDS,
)
from src.core.redis_client import redis_client


@dataclass
class VerifyResult:
    """verify_request 的返回值。

    成功时 ok=True、client_id 为请求方 client_id、error=None。
    失败时 ok=False、client_id=None、error 为 ErrorCode 字面量（见 schemas.ErrorCode）。
    """

    ok: bool
    client_id: Optional[str]
    error: Optional[str]


def _to_bytes(value: str | bytes) -> bytes:
    """将 str 或 bytes 统一为 bytes（str 按 UTF-8 编码）。"""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8")


def compute_signature(
    client_id: str,
    timestamp: str,
    nonce: str,
    body: str | bytes,
    secret: bytes,
) -> str:
    """返回小写十六进制 HMAC-SHA256。

    msg = client_id + timestamp + nonce + raw_body（原始字节直接拼接，无分隔符）。
    body 接受 str 或 bytes；str 时按 UTF-8 编码。

    Args:
        client_id: 客户端 ID。
        timestamp: Unix 秒级时间戳（字符串形式，与请求头原样）。
        nonce: 一次性随机串。
        body: 原始请求体（str 或 bytes），不得 re-serialize。
        secret: 客户端 secret 字节（由 get_secret 解密后传入）。

    Returns:
        小写十六进制签名串。
    """
    msg = (
        _to_bytes(client_id)
        + _to_bytes(timestamp)
        + _to_bytes(nonce)
        + _to_bytes(body)
    )
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


_DEGRADED_WARNED = False  # 进程级标志：Redis 降级 warning 仅记录一次，避免刷屏


def _claim_nonce(client_id: str, nonce: str) -> bool:
    """nonce 防重放：尝试占位，成功（首次）返回 True，已存在返回 False。

    使用 ``redis_client.acquire_lock``（即 ``SET key value NX EX ttl``）实现 SETNX+TTL。
    Redis 不可用时 ``redis_client`` 内部自动降级到其进程内 ``_InMemoryFallback``，
    本函数额外检测降级状态并记录一次 ``logger.warning``（满足契约要求的降级告警）。
    """
    global _DEGRADED_WARNED
    nonce_key = f"wecom_personal_rpa:nonce:{client_id}:{nonce}"

    # acquire_lock 即 SET key value NX EX ttl，成功（首次占位）返回 True，已存在返回 False。
    # redis_client 内部对 Redis 异常已 try/except 并降级到 _InMemoryFallback。
    acquired = redis_client.acquire_lock(nonce_key, "1", ex=NONCE_TTL_SECONDS)

    # 检测是否处于 Redis 降级状态（_connected 为 False 表示降级到内存）
    if not getattr(redis_client, "_connected", True):
        if not _DEGRADED_WARNED:
            logger.warning(
                "RPA nonce 防重放降级到进程内存储（Redis 不可用）；"
                "多 worker 部署下存在跨进程重放风险"
            )
            _DEGRADED_WARNED = True

    return bool(acquired)


def verify_request(
    headers: dict,
    raw_body: str | bytes,
    get_secret: Callable[[str], bytes | None],
) -> VerifyResult:
    """完整校验：client_id 存在性 + 时间戳窗口 + nonce 防重放 + 签名常量时间比较。

    Args:
        headers: 请求头字典（大小写不敏感键由调用方保证，本函数按精确头名取值）。
        raw_body: 原始请求体（str 或 bytes），不得 re-serialize。
        get_secret: 回调 ``get_secret(client_id) -> bytes | None``。
                    返回解密后的 secret bytes；客户端不存在/禁用时返回 None。

    Returns:
        VerifyResult。任一步骤失败返回 ``VerifyResult(ok=False, error=<ErrorCode>)``。
    """
    def _get_header(name: str) -> Optional[str]:
        # 支持精确头名与大小写不敏感回退
        val = headers.get(name)
        if val is not None:
            return val
        # 大小写不敏感回退（HTTP 头大小写不敏感）
        lower = name.lower()
        for k, v in headers.items():
            if k.lower() == lower:
                return v
        return None

    # 1. 取头：缺失任一即 auth_failed
    client_id = _get_header(HEADER_CLIENT_ID)
    timestamp = _get_header(HEADER_TIMESTAMP)
    nonce = _get_header(HEADER_NONCE)
    signature = _get_header(HEADER_SIGNATURE)

    if not (client_id and timestamp and nonce and signature):
        return VerifyResult(ok=False, client_id=None, error="auth_failed")

    # 2. 时间戳窗口校验
    try:
        ts_int = int(timestamp)
    except (ValueError, TypeError):
        return VerifyResult(ok=False, client_id=None, error="auth_failed")

    now_int = int(time.time())
    if abs(now_int - ts_int) > TIMESTAMP_TOLERANCE_SECONDS:
        # 不在日志中暴露具体时间戳偏移细节，仅记录失败类别
        logger.info(f"RPA 鉴权失败: 时间戳超窗 (client_id={client_id})")
        return VerifyResult(ok=False, client_id=None, error="auth_failed")

    # 3. get_secret：客户端不存在/禁用返回 None → client_disabled 或 auth_failed
    secret = get_secret(client_id)
    if secret is None:
        # 调用方负责区分"不存在"与"禁用"，此处统一返回 auth_failed
        # （protocol.md 校验规则：client_id 不存在返回 auth_failed，禁用返回 client_disabled；
        #  调用方应在 get_secret 返回 None 时按 db.status 决定，本函数保守返回 auth_failed）
        return VerifyResult(ok=False, client_id=None, error="auth_failed")

    # 4. nonce 防重放（在签名校验前占位，防止攻击者用合法签名重放）
    if not _claim_nonce(client_id, nonce):
        logger.info(f"RPA 鉴权失败: nonce 重复 (client_id={client_id})")
        return VerifyResult(ok=False, client_id=None, error="auth_failed")

    # 5. 签名常量时间比较
    expected = compute_signature(client_id, timestamp, nonce, raw_body, secret)
    if not hmac.compare_digest(expected, signature):
        # 绝不记录签名串
        logger.info(f"RPA 鉴权失败: 签名不匹配 (client_id={client_id})")
        return VerifyResult(ok=False, client_id=None, error="auth_failed")

    return VerifyResult(ok=True, client_id=client_id, error=None)
