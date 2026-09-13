"""发布确认链（C1，设计 §13.5）。

一次性 confirmation_id：仅认证用户交互端点可签发（API 层保证，本模块不暴露给
设备/模型身份）；绑定 tenant/user/task/草稿版本与规范化发布摘要，10 分钟有效。
publish 在同事务校验并消费；重复请求幂等返回原发布结果（service 层处理重放）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from .constants import ERR_CONFIRMATION_INVALID, SessionTaskError

CONFIRMATION_TTL_SECONDS = 600


def issue_confirmation(conn, tenant_id: str, user_id: str, task_id: UUID, task_version: int, digest: str) -> Dict[str, Any]:  # noqa: ANN001
    """为当前草稿版本签发确认凭据；同版本重复签发返回新凭据（旧凭据自然过期）。"""
    confirmation_id = uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=CONFIRMATION_TTL_SECONDS)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO session_task_confirmations
            (confirmation_id, tenant_id, user_id, task_id, spec_revision, spec_digest, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (confirmation_id, tenant_id, user_id, task_id, task_version, digest, expires_at),
    )
    return {"confirmation_id": str(confirmation_id), "expires_at": expires_at.isoformat(), "task_version": task_version}


def load_confirmation(conn, tenant_id: str, task_id: UUID, confirmation_id: UUID) -> Optional[Dict[str, Any]]:  # noqa: ANN001
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT confirmation_id, user_id, spec_revision, spec_digest, expires_at, consumed_at
        FROM session_task_confirmations
        WHERE tenant_id=%s AND task_id=%s AND confirmation_id=%s
        """,
        (tenant_id, task_id, confirmation_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def validate_confirmation(row: Dict[str, Any], *, user_id: str, expected_version: int, digest: str) -> None:
    """消费前校验：未过期、未消费、用户匹配、版本与摘要一致（设计 §13.5）。"""
    if row is None:
        raise SessionTaskError("发布确认不存在", ERR_CONFIRMATION_INVALID, 409)
    if row["user_id"] != user_id:
        raise SessionTaskError("发布确认与当前用户不匹配", ERR_CONFIRMATION_INVALID, 409)
    if row["consumed_at"] is not None:
        raise SessionTaskError("发布确认已被消费", ERR_CONFIRMATION_INVALID, 409)
    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise SessionTaskError("发布确认已过期，请重新确认", ERR_CONFIRMATION_INVALID, 409)
    if row["spec_revision"] != expected_version:
        raise SessionTaskError("任务版本已变化，发布确认失效", ERR_CONFIRMATION_INVALID, 409)
    if row["spec_digest"] != digest:
        raise SessionTaskError("发布内容已修改，发布确认失效", ERR_CONFIRMATION_INVALID, 409)


def consume_confirmation(conn, confirmation_row: Dict[str, Any], published_revision: int) -> None:  # noqa: ANN001
    """同事务消费确认（CAS：仅未消费行可更新）。"""
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE session_task_confirmations
        SET consumed_at = CURRENT_TIMESTAMP, published_revision = %s
        WHERE confirmation_id = %s AND consumed_at IS NULL
        """,
        (published_revision, confirmation_row["confirmation_id"]),
    )
    if cursor.rowcount != 1:
        raise SessionTaskError("发布确认已被消费（并发重放被拒绝）", ERR_CONFIRMATION_INVALID, 409)
