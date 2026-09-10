"""weixin_marketing 事件源管理 + 签名 webhook 接纳（P4-B，R57）

- 事件源：desktop_automation_event_sources（底座表）之上管理 internal/webhook 两类
  源；webhook 源的签名密钥经仓库既有 secret_crypto（Fernet）加密后存模块级密钥表，
  支持 key_id + 版本轮换（旧新并行短窗）。
- 签名验证：hmac 标准库 HMAC-SHA256 + compare_digest 恒定时间比较；
  签名串 = f"{timestamp}.{nonce}.{raw_body}"；时间戳 ±5 分钟窗口。
- 防重放：nonce 表 UNIQUE(source_id, nonce)，消耗与事件接纳同一事务——
  「nonce 已消耗而事件未接纳」不可达；TTL 清理随匹配 tick。
- 限流：按源滑动窗口（Redis ZSET 优先，Redis 故障降级进程内内存，沿
  src/api/rate_limit.py 惯例）；未接纳请求明确 429。
- tenant 身份只取自受信 event source 行，拒绝 payload 自报 tenant_id/user_id。
- payload 持久化到模块级 payloads 表（受控字节 hash 去重），events 行只存
  payload_ref/hash（底座契约：正文不进通用表）。
"""

import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from psycopg2.extras import Json

from src.db.database import get_db_connection
from src.desktop_automation import events as da_events
from src.desktop_automation.constants import EVENT_SOURCE_STATUS_ACTIVE

# ==================== 常量 ====================

SOURCE_TYPE_INTERNAL = "internal"
SOURCE_TYPE_WEBHOOK = "webhook"
SOURCE_TYPES = (SOURCE_TYPE_INTERNAL, SOURCE_TYPE_WEBHOOK)

KEY_STATUS_ACTIVE = "active"
KEY_STATUS_RETIRING = "retiring"
KEY_STATUS_RETIRED = "retired"

# 签名时间戳窗口（±5 分钟，微信计划 §3.2/底座计划 §3.2）
SIGNATURE_TIMESTAMP_WINDOW_SECONDS = 300
# nonce 记录保留时长（须大于时间戳窗口，清理挂 event_match_tick）
NONCE_TTL_SECONDS = 900
# 默认轮换并行窗（旧 key 继续可验签的时长）
DEFAULT_ROTATE_WINDOW_SECONDS = 900

# 限流默认（可经 config webhook_rate_limit_per_minute 覆盖）
DEFAULT_RATE_LIMIT_PER_MINUTE = 120

# payload 上限（与 config.webhook_max_body_bytes 一致的默认值）
DEFAULT_MAX_BODY_BYTES = 256 * 1024

# 事件 envelope 字段约束
EXTERNAL_EVENT_ID_MAX = 256
EVENT_TYPE_MAX = 128
NONCE_MAX = 128
KEY_ID_MAX = 128

# payload_ref 规范：wxm-event:<sha256(payload 规范化字节)>
PAYLOAD_REF_PREFIX = "wxm-event:"

_TABLES_DDL = (
    """
    CREATE TABLE IF NOT EXISTS weixin_marketing_event_source_keys (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        source_id UUID NOT NULL,
        key_id TEXT NOT NULL,
        key_version INTEGER NOT NULL,
        encrypted_secret TEXT NOT NULL,
        status TEXT DEFAULT 'active' NOT NULL,
        retire_at TIMESTAMPTZ,
        created_by TEXT,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (source_id, key_id),
        UNIQUE (source_id, key_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS weixin_marketing_webhook_nonces (
        id BIGSERIAL PRIMARY KEY,
        source_id UUID NOT NULL,
        nonce TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        UNIQUE (source_id, nonce)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS weixin_marketing_event_payloads (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        source_id UUID,
        payload_hash TEXT NOT NULL,
        payload_json JSONB NOT NULL,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, payload_hash)
    )
    """,
)

_tables_lock = threading.Lock()
_tables_ready = False


def ensure_event_source_tables() -> None:
    """幂等建事件源配套表（密钥/nonce/payload；沿 api.py 幂等键表内建模式）"""
    global _tables_ready
    if _tables_ready:
        return
    with _tables_lock:
        if _tables_ready:
            return
        with get_db_connection() as conn:
            cur = conn.cursor()
            for ddl in _TABLES_DDL:
                cur.execute(ddl)
            conn.commit()
        _tables_ready = True


# ==================== 事件源 CRUD ====================


class EventSourceRefConflictError(Exception):
    """source_ref 在租户内已存在（创建不承担修改语义：类型/属主变更不提供路径，
    凭据变更唯一入口为 rotate-key 的管理鉴权）"""


def create_event_source(
    *,
    tenant_id: str,
    user_id: str,
    source_ref: str,
    source_type: str,
    allowed_event_types: Optional[List[str]] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """创建事件源（纯 INSERT，不复用底座 register_event_source 的 upsert——
    后者会静默改写既有源行，外部输入面下即「B 覆盖 A 的受信源」）：
    UNIQUE(tenant_id, source_ref) 冲突 → EventSourceRefConflictError（API 409
    SOURCE_REF_CONFLICT），原源行/密钥零变化。

    internal 不产密钥；webhook 生成 secret（Fernet 加密落库），明文仅在本次
    返回值中出现（之后任何 API 不回明文/旧密钥）。"""
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"未知事件源类型: {source_type}")
    ensure_event_source_tables()
    from src.channels.wecom_personal_rpa.secret_crypto import encrypt_secret

    payload: Dict[str, Any] = {
        "description": description,
    } if description else {}
    secret_plain: Optional[str] = None
    key_id: Optional[str] = None
    if source_type == SOURCE_TYPE_WEBHOOK:
        secret_plain = secrets.token_urlsafe(48)
        key_id = f"wk-{secrets.token_hex(8)}"

    with get_db_connection() as conn:
        cur = conn.cursor()
        # 纯 INSERT：冲突检测经 ON CONFLICT DO NOTHING RETURNING（返回 None=已存在），
        # 不抛裸 UniqueViolation、不触碰既有行
        cur.execute(
            """
            INSERT INTO desktop_automation_event_sources
                (tenant_id, scenario_key, source_ref, source_type, key_ref,
                 key_version, payload_schema, allowed_event_types, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active')
            ON CONFLICT (tenant_id, source_ref) DO NOTHING
            RETURNING id
            """,
            (
                tenant_id, _scenario_key(), source_ref, source_type, key_id,
                "1" if key_id else None,
                Json(payload) if payload else None,
                Json(allowed_event_types) if allowed_event_types else None,
            ),
        )
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            raise EventSourceRefConflictError(source_ref)
        source_id = str(row["id"])
        if secret_plain and key_id:
            cur.execute(
                """
                INSERT INTO weixin_marketing_event_source_keys
                    (tenant_id, source_id, key_id, key_version, encrypted_secret,
                     status, created_by)
                VALUES (%s, %s, %s, 1, %s, 'active', %s)
                """,
                (tenant_id, source_id, key_id, encrypt_secret(secret_plain), user_id),
            )
        conn.commit()
    return {
        "source": _get_source_view_on_source_id(source_id, tenant_id),
        "secret": secret_plain,
        "key_id": key_id,
    }


def _scenario_key() -> str:
    from src.weixin_marketing.constants import SCENARIO_KEY

    return SCENARIO_KEY


def get_event_source_view(source_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    ensure_event_source_tables()
    return _get_source_view_on_source_id(source_id, tenant_id)


def _get_source_view_on_source_id(source_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """源视图（凭据掩码：只回 key_id/版本/指纹，绝不回明文或密文）"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.id, s.tenant_id, s.source_ref, s.source_type, s.key_ref,
                   s.key_version, s.allowed_event_types, s.payload_schema, s.status,
                   s.created_at, s.updated_at,
                   (SELECT json_agg(json_build_object(
                       'key_id', k.key_id, 'key_version', k.key_version,
                       'status', k.status, 'retire_at', k.retire_at))
                FROM (SELECT key_id, key_version, status, retire_at
                      FROM weixin_marketing_event_source_keys
                      WHERE source_id = s.id AND tenant_id = s.tenant_id) k) AS keys
            FROM desktop_automation_event_sources s
            WHERE s.id = %s AND s.tenant_id = %s
            """,
            (source_id, tenant_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        view = dict(row)
        if view.get("source_type") == SOURCE_TYPE_WEBHOOK:
            view["webhook_url"] = f"/api/weixin-marketing/webhooks/{view['id']}"
        view["keys"] = view.get("keys") or []
        return view


def list_event_sources(
    tenant_id: str, *, source_type: Optional[str] = None, limit: int = 100, offset: int = 0
) -> Tuple[List[Dict[str, Any]], int]:
    """列出本租户事件源（分页；凭据掩码同上）"""
    ensure_event_source_tables()
    with get_db_connection() as conn:
        cur = conn.cursor()
        where = "tenant_id = %s"
        params: List[Any] = [tenant_id]
        if source_type:
            where += " AND source_type = %s"
            params.append(source_type)
        cur.execute(
            f"SELECT COUNT(*) AS c FROM desktop_automation_event_sources WHERE {where}",
            tuple(params),
        )
        total = int(cur.fetchone()["c"])
        cur.execute(
            f"""
            SELECT id FROM desktop_automation_event_sources
            WHERE {where} ORDER BY created_at, id LIMIT %s OFFSET %s
            """,
            tuple(params + [limit, offset]),
        )
        ids = [str(r["id"]) for r in cur.fetchall()]
    items = [v for i in ids if (v := _get_source_view_on_source_id(i, tenant_id))]
    return items, total


def rotate_key(
    *,
    tenant_id: str,
    source_id: str,
    user_id: str,
    rotate_window_seconds: int = DEFAULT_ROTATE_WINDOW_SECONDS,
) -> Dict[str, Any]:
    """轮换 webhook 签名密钥：新 key active；旧 active → retiring（retire_at 前
    旧新并行可验签，过窗自动失效）。明文新 secret 仅本次返回。

    管理权限：keys 表 created_by（源创建者）或 platform_admin（调用方判定后传入）。"""
    ensure_event_source_tables()
    from src.channels.wecom_personal_rpa.secret_crypto import encrypt_secret

    source = _get_source_source_row(tenant_id, source_id)
    if source is None:
        raise KeyError("event source not found")
    if source["source_type"] != SOURCE_TYPE_WEBHOOK:
        raise ValueError("仅 webhook 源支持密钥轮换")
    new_secret = secrets.token_urlsafe(48)
    new_key_id = f"wk-{secrets.token_hex(8)}"
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(MAX(key_version), 0) + 1 AS v
            FROM weixin_marketing_event_source_keys
            WHERE source_id = %s AND tenant_id = %s
            """,
            (source_id, tenant_id),
        )
        new_version = int(cur.fetchone()["v"])
        cur.execute(
            """
            UPDATE weixin_marketing_event_source_keys
            SET status = 'retiring', retire_at = NOW() + (%s * INTERVAL '1 second'),
                updated_at = NOW()
            WHERE source_id = %s AND tenant_id = %s AND status = 'active'
            """,
            (rotate_window_seconds, source_id, tenant_id),
        )
        cur.execute(
            """
            INSERT INTO weixin_marketing_event_source_keys
                (tenant_id, source_id, key_id, key_version, encrypted_secret,
                 status, created_by)
            VALUES (%s, %s, %s, %s, %s, 'active', %s)
            """,
            (tenant_id, source_id, new_key_id, new_version,
             encrypt_secret(new_secret), user_id),
        )
        cur.execute(
            """
            UPDATE desktop_automation_event_sources
            SET key_ref = %s, key_version = %s, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s
            """,
            (new_key_id, str(new_version), source_id, tenant_id),
        )
        conn.commit()
    logger.info(
        f"后端日志：weixin_marketing 轮换 webhook 密钥 tenant={tenant_id} "
        f"source={source_id} new_key_id={new_key_id} version={new_version} "
        f"window={rotate_window_seconds}s"
    )
    return {
        "source_id": source_id,
        "key_id": new_key_id,
        "key_version": new_version,
        "secret": new_secret,
        "rotate_window_seconds": rotate_window_seconds,
    }


def _get_source_source_row(tenant_id: str, source_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tenant_id, source_ref, source_type, status
            FROM desktop_automation_event_sources
            WHERE id = %s AND tenant_id = %s
            """,
            (source_id, tenant_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_source_row_by_id(source_id: str) -> Optional[Dict[str, Any]]:
    """按 source_id 全局查源行（webhook 公开面：source_id 是不重复 UUID，租户身份
    取自行内 tenant_id，不读请求参数/payload）"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tenant_id, source_ref, source_type, status
            FROM desktop_automation_event_sources
            WHERE id = %s
            """,
            (source_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def source_created_by(tenant_id: str, source_id: str) -> Optional[str]:
    """源创建者（首个 active key 的 created_by；internal 源无 key 行返回 None）"""
    ensure_event_source_tables()
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT created_by FROM weixin_marketing_event_source_keys
            WHERE source_id = %s AND tenant_id = %s
            ORDER BY key_version LIMIT 1
            """,
            (source_id, tenant_id),
        )
        row = cur.fetchone()
        return row["created_by"] if row else None


# ==================== 签名验证（恒定时间）====================


def compute_webhook_signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    """HMAC-SHA256(secret, "{timestamp}.{nonce}.{body}") hex——契约同时供测试构造签名"""
    message = f"{timestamp}.{nonce}.".encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def load_verifying_keys(tenant_id: str, source_id: str, now: datetime) -> List[Dict[str, Any]]:
    """取可验签密钥（active + 窗口内 retiring）；含 key_id 与解密后 secret 明文
    （仅进程内使用，不入日志/响应）。"""
    ensure_event_source_tables()
    from src.channels.wecom_personal_rpa.secret_crypto import decrypt_secret

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT key_id, key_version, encrypted_secret, status, retire_at
            FROM weixin_marketing_event_source_keys
            WHERE source_id = %s AND tenant_id = %s
              AND (status = 'active'
                   OR (status = 'retiring' AND retire_at IS NOT NULL AND retire_at > %s))
            ORDER BY key_version DESC
            """,
            (source_id, tenant_id, now),
        )
        rows = [dict(r) for r in cur.fetchall()]
    keys: List[Dict[str, Any]] = []
    for row in rows:
        try:
            secret = decrypt_secret(row["encrypted_secret"]).decode("utf-8")
        except Exception as e:  # noqa: BLE001 密文损坏不阻断其他 key
            logger.warning(
                f"后端日志：weixin_marketing webhook 密钥解密失败 key_id={row['key_id']}: "
                f"{type(e).__name__}"
            )
            continue
        keys.append({"key_id": row["key_id"], "key_version": row["key_version"],
                     "secret": secret, "status": row["status"]})
    return keys


def verify_webhook_request(
    *,
    tenant_id: str,
    source_id: str,
    timestamp: str,
    nonce: str,
    signature: str,
    key_id: Optional[str],
    body: bytes,
    now: datetime,
) -> Tuple[bool, str]:
    """验签链：时间戳窗口 → key 定位 → HMAC 恒定时间比较。

    返回 (ok, reason)；reason 为稳定拒绝原因（signature_invalid / timestamp_out_of_window /
    unknown_key / no_keys）。时间戳窗口先于签名比较（过期请求无谓耗 HMAC）。
    """
    try:
        ts_value = int(timestamp)
    except (TypeError, ValueError):
        return False, "signature_invalid"
    now_ts = int(now.timestamp())
    if abs(now_ts - ts_value) > SIGNATURE_TIMESTAMP_WINDOW_SECONDS:
        return False, "timestamp_out_of_window"
    keys = load_verifying_keys(tenant_id, source_id, now)
    if not keys:
        return False, "no_keys"
    if key_id:
        keys = [k for k in keys if k["key_id"] == key_id]
        if not keys:
            return False, "unknown_key"
    for key in keys:
        expected = compute_webhook_signature(key["secret"], timestamp, nonce, body)
        if hmac.compare_digest(expected, signature or ""):
            return True, ""
    return False, "signature_invalid"


# ==================== webhook 限流（沿 rate_limit.py 惯例）====================


class WebhookRateLimiter:
    """按源滑动窗口限流：Redis ZSET 优先（跨 worker 一致），Redis 故障降级进程内。
    429 语义：未接纳的请求明确拒绝（洪峰保护，不静默排队）。"""

    def __init__(self, max_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE):
        self.max = max_per_minute
        self.window = 60
        self._local: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def _redis_key(self, source_id: str) -> str:
        from src.core.redis_client import redis_client

        return redis_client.make_key("rate_limit:wxm_webhook", source_id)

    def is_allowed(self, source_id: str) -> bool:
        now = time.time()
        cutoff = now - self.window
        try:
            from src.core.redis_client import redis_client

            key = self._redis_key(source_id)
            redis_client.zremrangebyscore(key, 0, cutoff)
            if redis_client.zcard(key) >= self.max:
                return False
            redis_client.zadd(key, {f"{now}:{secrets.token_hex(4)}": now})
            redis_client.expire(key, self.window)
            return True
        except Exception as e:  # noqa: BLE001 Redis 故障降级内存（单 worker 仍有效）
            logger.warning(f"后端日志：weixin_marketing webhook 限流 Redis 不可用，降级内存: {type(e).__name__}")
        with self._lock:
            bucket = [t for t in self._local.get(source_id, []) if t > cutoff]
            if len(bucket) >= self.max:
                self._local[source_id] = bucket
                return False
            bucket.append(now)
            self._local[source_id] = bucket
            return True


_rate_limiter = WebhookRateLimiter()


def rate_limiter_for(config: Any) -> WebhookRateLimiter:
    """按 config 每分钟上限取限流器（配置变化时重建；默认全局单例）"""
    global _rate_limiter
    limit = int(getattr(config, "webhook_rate_limit_per_minute", DEFAULT_RATE_LIMIT_PER_MINUTE))
    if _rate_limiter.max != limit:
        _rate_limiter = WebhookRateLimiter(limit)
    return _rate_limiter


# ==================== payload 持久化与加载 ====================


def payload_ref_of(payload_hash: str) -> str:
    return f"{PAYLOAD_REF_PREFIX}{payload_hash}"


def canonical_payload_bytes(envelope: Dict[str, Any]) -> bytes:
    """规范化 payload 字节（sort_keys；hash 稳定不受键序影响）"""
    return json.dumps(envelope, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")


def store_event_payload_on(
    cursor, tenant_id: str, source_id: str, payload_hash: str, envelope: Dict[str, Any]
) -> str:
    """受控 payload 持久化（同事务；同 hash 复用既有行），返回 payload_ref"""
    cursor.execute(
        """
        INSERT INTO weixin_marketing_event_payloads
            (tenant_id, source_id, payload_hash, payload_json)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (tenant_id, payload_hash) DO NOTHING
        """,
        (tenant_id, source_id, payload_hash, Json(envelope)),
    )
    return payload_ref_of(payload_hash)


def load_event_payload(tenant_id: str, payload_ref: str) -> Optional[Dict[str, Any]]:
    """按 payload_ref 加载 payload（匹配 worker condition 判定用）"""
    ensure_event_source_tables()
    if not payload_ref or not payload_ref.startswith(PAYLOAD_REF_PREFIX):
        return None
    payload_hash = payload_ref[len(PAYLOAD_REF_PREFIX):]
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT payload_json FROM weixin_marketing_event_payloads
            WHERE tenant_id = %s AND payload_hash = %s
            """,
            (tenant_id, payload_hash),
        )
        row = cur.fetchone()
        return dict(row["payload_json"]) if row else None


# ==================== webhook 事件接纳（单事务）====================


class WebhookRejectError(Exception):
    """webhook 拒绝（HTTP 语义由 API 层翻译；reason 为稳定码）"""

    def __init__(self, status_code: int, reason: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.message = message


def accept_webhook_event(
    *,
    tenant_id: str,
    source_id: str,
    timestamp: str,
    nonce: str,
    signature: str,
    key_id: Optional[str],
    body: bytes,
    now: Optional[datetime] = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    rate_limiter: Optional[WebhookRateLimiter] = None,
) -> Dict[str, Any]:
    """签名 webhook 接纳全链（限流 → 验签 → envelope 校验 → 单事务接纳）。

    返回接纳结果（202 语义）；拒绝抛 WebhookRejectError（401/403/404/413/422/429）。
    tenant 只取自受信 source 行；nonce 消耗与事件接纳同事务（重放窗口内拒绝、
    崩溃不留「已耗 nonce 未接纳事件」）。
    """
    ensure_event_source_tables()
    now = now or datetime.now(timezone.utc)
    if rate_limiter is None:
        rate_limiter = _rate_limiter

    source = _get_source_source_row(tenant_id, source_id)
    if source is None or source["source_type"] != SOURCE_TYPE_WEBHOOK:
        raise WebhookRejectError(404, "not_found", "事件源不存在")
    if source["status"] != EVENT_SOURCE_STATUS_ACTIVE:
        raise WebhookRejectError(404, "not_found", "事件源不存在")

    if len(body) > max_body_bytes:
        raise WebhookRejectError(413, "payload_too_large", "请求体超过大小上限")
    if not rate_limiter.is_allowed(source_id):
        raise WebhookRejectError(429, "rate_limited", "请求频率超过限制")

    if not timestamp or not nonce or not signature:
        raise WebhookRejectError(401, "signature_invalid", "缺少签名头")
    if len(nonce) > NONCE_MAX or len(key_id or "") > KEY_ID_MAX:
        raise WebhookRejectError(401, "signature_invalid", "签名头非法")

    ok, reason = verify_webhook_request(
        tenant_id=tenant_id, source_id=source_id, timestamp=timestamp, nonce=nonce,
        signature=signature, key_id=key_id, body=body, now=now,
    )
    if not ok:
        if reason == "timestamp_out_of_window":
            raise WebhookRejectError(403, "timestamp_out_of_window", "时间戳超出允许窗口")
        raise WebhookRejectError(401, "signature_invalid", "签名验证失败")

    envelope = _parse_envelope(body)
    external_event_id = envelope.pop("event_id")
    event_type = envelope.pop("event_type", None)
    occurred_raw = envelope.pop("occurred_at", None)
    occurred_at = _parse_occurred_at(occurred_raw)

    payload_bytes = canonical_payload_bytes(envelope)
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    source_ref = source["source_ref"]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO weixin_marketing_webhook_nonces (source_id, nonce)
            VALUES (%s, %s)
            ON CONFLICT (source_id, nonce) DO NOTHING
            RETURNING id
            """,
            (source_id, nonce),
        )
        if cursor.fetchone() is None:
            conn.rollback()
            raise WebhookRejectError(403, "nonce_replayed", "重复的 nonce（疑似重放）")
        payload_ref = store_event_payload_on(
            cursor, tenant_id, source_id, payload_hash, envelope
        )
        result = da_events.accept_event_on(
            cursor,
            tenant_id=tenant_id, source_ref=source_ref,
            external_event_id=external_event_id, event_type=event_type,
            payload_ref=payload_ref, payload_hash=payload_hash,
            occurred_at=occurred_at,
        )
        if not result.get("accepted"):
            conn.rollback()
            if result.get("reason") == "event_type_not_allowed":
                raise WebhookRejectError(422, "event_type_not_allowed", "事件类型不在白名单")
            raise WebhookRejectError(422, "source_not_active", "事件源不可用")
        conn.commit()
    return result


_FORBIDDEN_TOP_LEVEL_FIELDS = ("tenant_id", "user_id")


def _parse_envelope(body: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise WebhookRejectError(422, "invalid_json", "请求体不是合法 JSON") from None
    if not isinstance(data, dict):
        raise WebhookRejectError(422, "invalid_json", "请求体必须是 JSON 对象")
    for field in _FORBIDDEN_TOP_LEVEL_FIELDS:
        if field in data:
            raise WebhookRejectError(
                422, "identity_field_forbidden",
                f"payload 不允许自报 {field}（身份取自事件源）",
            )
    event_id = data.get("event_id")
    if not isinstance(event_id, str) or not (1 <= len(event_id) <= EXTERNAL_EVENT_ID_MAX):
        raise WebhookRejectError(422, "invalid_event_id", "event_id 必须为 1-256 字符")
    event_type = data.get("event_type")
    if event_type is not None and (
        not isinstance(event_type, str) or not (1 <= len(event_type) <= EVENT_TYPE_MAX)
    ):
        raise WebhookRejectError(422, "invalid_event_type", "event_type 必须为 1-128 字符")
    return dict(data)


def _parse_occurred_at(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WebhookRejectError(422, "invalid_occurred_at", "occurred_at 必须为 ISO8601 字符串")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise WebhookRejectError(422, "invalid_occurred_at", "occurred_at 必须为 ISO8601 字符串") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def cleanup_expired_nonces(now: Optional[datetime] = None) -> int:
    """nonce TTL 清理（挂 event_match_tick；窗口远大于时间戳窗口）"""
    ensure_event_source_tables()
    now = now or datetime.now(timezone.utc)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM weixin_marketing_webhook_nonces WHERE created_at < %s",
            (now - timedelta(seconds=NONCE_TTL_SECONDS),),
        )
        deleted = cur.rowcount
        conn.commit()
    return deleted


# ==================== condition 白名单 DSL（简单判定）====================

# 支持的判定：[{field, op, value?}]（AND 组合）；op ∈ eq|ne|exists|gt|lt（数值比较）。
# 复杂 DSL（嵌套/or/正则）不在本期范围，登记为遗留。


def evaluate_condition(condition_ref: Optional[str], payload: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """判定冻结 condition（JSON 文本）对 payload 是否命中。

    返回 (hit, reason)：condition 为空 → 命中；JSON 损坏/结构非法 → 不命中
    （reason=condition_invalid，保守不触发）。"""
    if not condition_ref:
        return True, ""
    try:
        conditions = json.loads(condition_ref)
    except (json.JSONDecodeError, TypeError):
        return False, "condition_invalid"
    if not isinstance(conditions, list):
        return False, "condition_invalid"
    for cond in conditions:
        if not isinstance(cond, dict):
            return False, "condition_invalid"
        field = cond.get("field")
        op = cond.get("op")
        if not isinstance(field, str) or not field or op not in ("eq", "ne", "exists", "gt", "lt"):
            return False, "condition_invalid"
        actual = (payload or {}).get(field)
        if op == "exists":
            if actual is None:
                return False, "condition_not_matched"
            continue
        expected = cond.get("value")
        if op in ("eq", "ne"):
            matched = actual == expected
            if (op == "eq") != matched:
                return False, "condition_not_matched"
            continue
        # gt/lt 仅数值
        try:
            cmp_ok = (op == "gt" and float(actual) > float(expected)) or (
                op == "lt" and float(actual) < float(expected)
            )
        except (TypeError, ValueError):
            return False, "condition_not_matched"
        if not cmp_ok:
            return False, "condition_not_matched"
    return True, ""


def freeze_condition(condition: Optional[Any]) -> Optional[str]:
    """发布配置中的 condition → condition_ref 冻结文本（None/空列表 → None）"""
    if condition is None:
        return None
    if isinstance(condition, list) and not condition:
        return None
    return json.dumps(condition, sort_keys=True, ensure_ascii=False, default=str)
