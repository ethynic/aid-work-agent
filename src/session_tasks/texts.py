"""受控文本存储（C1，设计 §13.3）。

复用 src/channels/wecom_personal_rpa/secret_crypto 的 Fernet 实现（主密钥
settings.app.secret_key → RPA_SECRET_KEY，缺失抛错、不降级明文）。普通计数/
状态/limits 明文存列；goal/policy/completion 自然语言/opening/消息正文/决策
正文一律走本模块：text_ref = session_task_texts.id（UUID），不是 URL/路径。
日志与错误不携带解密值。模块关闭不阻断应用启动（惰性 import，调用才触发）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from .constants import ERR_CRYPTO_UNAVAILABLE, TEXT_PURPOSES, SessionTaskError

logger = logging.getLogger("session_tasks.texts")


def _crypto():
    from src.channels.wecom_personal_rpa.secret_crypto import decrypt_secret, encrypt_secret

    return encrypt_secret, decrypt_secret


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def store_text(conn, tenant_id: str, task_id, purpose: str, payload: Any) -> UUID:  # noqa: ANN001
    """加密写入一条受控文本并返回 text_id。调用方负责事务提交（与业务行同事务）。"""
    if purpose not in TEXT_PURPOSES:
        raise SessionTaskError(f"非法文本用途: {purpose}", "VALIDATION_FAILED")
    try:
        encrypt_secret, _ = _crypto()
        encrypted = encrypt_secret(_canonical_json(payload))
    except RuntimeError as exc:  # 主密钥缺失：拒绝本功能写入，不降级明文
        raise SessionTaskError("服务端加密密钥未配置，无法发布/存储会话任务文本", ERR_CRYPTO_UNAVAILABLE, 503) from exc
    text_id = uuid4()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO session_task_texts (id, tenant_id, task_id, purpose, encrypted_payload)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (text_id, tenant_id, task_id, purpose, encrypted),
    )
    return text_id


def load_text(conn, tenant_id: str, task_id, text_id: UUID, *, expected_purpose: Optional[str] = None) -> Any:  # noqa: ANN001
    """解密读取一条受控文本（租户+任务双重校验）；解密失败由调用方转 blocked。"""
    try:
        _, decrypt_secret = _crypto()
    except Exception as exc:  # noqa: BLE001
        raise SessionTaskError("服务端加密不可用", ERR_CRYPTO_UNAVAILABLE, 503) from exc
    cursor = conn.cursor()
    cursor.execute(
        "SELECT purpose, encrypted_payload FROM session_task_texts WHERE tenant_id=%s AND task_id=%s AND id=%s",
        (tenant_id, task_id, text_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise SessionTaskError("受控文本不存在或跨任务引用被拒绝", "NOT_FOUND", 404)
    purpose, encrypted = row["purpose"], row["encrypted_payload"]
    if expected_purpose is not None and purpose != expected_purpose:
        raise SessionTaskError("受控文本用途不匹配", "VALIDATION_FAILED")
    try:
        raw = decrypt_secret(encrypted).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 密文损坏/主密钥轮换：blocked，不返回明文猜测
        logger.warning("受控文本解密失败 tenant=%s task=%s text=%s", tenant_id, task_id, text_id)
        raise SessionTaskError("受控文本解密失败（密文损坏或密钥不匹配）", ERR_CRYPTO_UNAVAILABLE, 503) from exc
    return json.loads(raw)


def digest_payload(payload: Any) -> str:
    """规范化摘要（幂等/confirmations 共用；sha256 hex）。"""
    import hashlib

    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def spec_digest(spec: Dict[str, Any]) -> str:
    """发布确认摘要：只覆盖授权字段（设计 §13.5：修改任一授权字段即失效）。"""
    authorized = {k: spec[k] for k in ("goal", "completion_rule", "reply_policy", "limits", "opening_text", "work_window") if k in spec}
    return digest_payload(authorized)
