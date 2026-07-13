"""archive 模块统一审计事件写入

封装 server 模式相关 audit 事件，避免在 callback_handler / fetcher / poller
等多处重复 try/except + json.dumps。所有写入失败仅记日志不抛异常
（审计失败不应阻断业务流程）。

事件类别（设计文档 §9）：
- archive_callback_received：收到企微回调（验签通过）
- archive_callback_verify_failed：验签失败（疑似伪造或凭证错误）
- archive_fetch_started：拉取开始（由 callback 或 poller 触发）
- archive_fetch_success：单次拉取成功（含 seq、batch_size、来源）
- archive_fetch_rate_limited：45009 触发暂停
- archive_fetch_error：拉取/解密异常
- archive_listen_mode_changed：模式切换（第一期不可达）
- archive_credential_updated：凭证更新（在 channel_config CRUD 中触发）
- archive_callback_url_regenerated：重新生成 config_id（本期未实现）

全部事件复用现有 wecom_rpa_audit_logs 表（通过 db.write_audit），
自动出现在管理后台审计列表 + /metrics 接口的 audit_counts 聚合中。
"""

import hashlib
import hmac
import json
import os
import unicodedata
from typing import Any, Dict, Optional

from loguru import logger

from src.channels.wecom_personal_rpa import db as rpa_db

# 审计事件类别常量（与 wecom_rpa 现有命名风格一致：snake_case）
CATEGORY_CALLBACK_RECEIVED = "archive_callback_received"
CATEGORY_CALLBACK_VERIFY_FAILED = "archive_callback_verify_failed"
CATEGORY_FETCH_STARTED = "archive_fetch_started"
CATEGORY_FETCH_SUCCESS = "archive_fetch_success"
CATEGORY_FETCH_RATE_LIMITED = "archive_fetch_rate_limited"
CATEGORY_FETCH_ERROR = "archive_fetch_error"
CATEGORY_DIRECTION_FILTERED = "archive_direction_filtered"
CATEGORY_DIRECTION_GUARD_FILTERED = "direction_guard_filtered"
CATEGORY_SELF_ECHO_ESCAPED = "self_echo_escaped"


def _hmac_key() -> bytes:
    key = os.getenv("WECOM_RPA_AUDIT_HMAC_KEY") or os.getenv("RPA_SECRET_KEY")
    if not key:
        raise RuntimeError("未配置 WECOM_RPA_AUDIT_HMAC_KEY 或 RPA_SECRET_KEY")
    return key.encode("utf-8")


def hash_identifier(value: Optional[str]) -> Optional[str]:
    """生成审计用不可逆短摘要，禁止记录企微 userid 明文。"""
    if not value:
        return None
    try:
        return hmac.new(_hmac_key(), value.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    except Exception as exc:
        # 摘要缺失时宁可不写标识，也不能泄露明文或让 inbound 被当作坏消息推进 seq。
        logger.warning(f"[ArchiveAudit] 标识摘要降级为空: {type(exc).__name__}")
        return None


def digest_reply_text(value: str) -> str:
    """生成出站正文关联摘要，正文自身不落审计。"""
    return hmac.new(_hmac_key(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def digest_reply_component(action_type: str, value: str) -> str:
    """生成单个出站动作与存档回流的稳定摘要。

    文本只规范化 Unicode 与换行；附件只使用类型和文件名。摘要中加入动作类型，
    避免同名图片/文件或正文与文件名互相碰撞。数据库只保存 HMAC，不保存正文副本。
    """
    normalized = unicodedata.normalize("NFC", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    canonical = f"wecom-rpa-echo-v1\0{action_type}\0{normalized}"
    return hmac.new(_hmac_key(), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def log_direction_filtered(
    tenant_id: str, config_id: str, account_id: str, direction: str,
    reason: str, sender: Optional[str], peer_id: Optional[str], event_id: str,
) -> None:
    # 摘要密钥或审计设施异常不能阻止安全过滤消息推进 seq。
    try:
        sender_hash = hash_identifier(sender)
        peer_hash = hash_identifier(peer_id)
    except Exception as exc:
        logger.warning(
            f"[ArchiveAudit] 方向摘要生成失败，降级为空摘要: {type(exc).__name__}"
        )
        sender_hash = None
        peer_hash = None
    _safe_write(
        tenant_id, CATEGORY_DIRECTION_FILTERED,
        {
            "config_id": config_id,
            "event_id": event_id,
            "direction": direction,
            "reason": reason,
            "sender_hash": sender_hash,
            "peer_hash": peer_hash,
        },
        account_id=account_id,
    )


def log_self_echo_detected(
    tenant_id: str, config_id: str, account_id: str, event_id: str
) -> None:
    _safe_write(
        tenant_id, "self_echo_detected",
        {"event_id": event_id, "matched": True},
        config_id=config_id, account_id=account_id,
    )


def _safe_write(
    tenant_id: str,
    category: str,
    payload: Dict[str, Any],
    config_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> None:
    """安全写入审计：序列化 payload + 脱敏异常吞掉。

    payload 中不应包含 secret / private_key / token / encoding_aes_key 等敏感字段。
    调用方负责只传业务字段（seq / batch_size / errcode / errmsg / source 等）。
    """
    try:
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        rpa_db.write_audit(
            tenant_id=tenant_id,
            client_id=None,  # server 模式无具体客户端
            account_id=account_id,
            category=category,
            payload_json=payload_json,
            # config_id 放在 payload 里更通用，action_id 留空
        )
    except Exception as e:
        logger.warning(
            f"[ArchiveAudit] 写入失败 category={category} tenant={tenant_id}: {e}"
        )


def log_callback_received(
    tenant_id: str, config_id: str, verify_ok: bool, detail: str = ""
) -> None:
    """收到企微回调（含验签通过/失败）。"""
    _safe_write(
        tenant_id=tenant_id,
        category=(
            CATEGORY_CALLBACK_RECEIVED if verify_ok else CATEGORY_CALLBACK_VERIFY_FAILED
        ),
        payload={
            "config_id": config_id,
            "verify_ok": verify_ok,
            "detail": detail[:200],  # 截断防止过长
        },
    )


def log_fetch_started(
    tenant_id: str, config_id: str, source: str, account_id: Optional[str] = None
) -> None:
    """拉取开始（source=callback / poller_initial / poller_periodic）。"""
    _safe_write(
        tenant_id=tenant_id,
        category=CATEGORY_FETCH_STARTED,
        payload={
            "config_id": config_id,
            "source": source,
        },
        account_id=account_id,
    )


def log_fetch_success(
    tenant_id: str,
    config_id: str,
    source: str,
    batch_size: int,
    processed: int,
    last_seq: int,
    account_id: Optional[str] = None,
) -> None:
    """单次拉取成功。"""
    _safe_write(
        tenant_id=tenant_id,
        category=CATEGORY_FETCH_SUCCESS,
        payload={
            "config_id": config_id,
            "source": source,
            "batch_size": batch_size,
            "processed": processed,
            "last_seq": last_seq,
        },
        account_id=account_id,
    )


def log_fetch_rate_limited(
    tenant_id: str,
    config_id: str,
    retry_after_seconds: int,
    account_id: Optional[str] = None,
) -> None:
    """45009 频率限制。"""
    _safe_write(
        tenant_id=tenant_id,
        category=CATEGORY_FETCH_RATE_LIMITED,
        payload={
            "config_id": config_id,
            "retry_after_seconds": retry_after_seconds,
        },
        account_id=account_id,
    )


def log_fetch_error(
    tenant_id: str,
    config_id: str,
    error_type: str,
    error_msg: str,
    stage: str = "",
    account_id: Optional[str] = None,
) -> None:
    """拉取/解密/处理异常。"""
    _safe_write(
        tenant_id=tenant_id,
        category=CATEGORY_FETCH_ERROR,
        payload={
            "config_id": config_id,
            "stage": stage,
            "error_type": error_type,
            "error_msg": error_msg[:300],  # 截断防止过长
        },
        account_id=account_id,
    )
