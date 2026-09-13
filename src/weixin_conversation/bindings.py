"""微信会话绑定服务（C1 骨架，设计 §13.3）。

C1 交付：创建 pending 绑定（真机验证前的登记）、属主查询、失效/过期标记。
verified 写入路径本阶段不开放：需要受信 Provider 的真机验证证据 schema（C0
真机 BLOCKED，证据 schema 待真机阶段冻结），伪造证据填 verified 属于契约违规。
"""
from __future__ import annotations

from typing import Any, Dict, List
from uuid import UUID

from src.session_tasks.constants import SessionTaskError

from .constants import BINDING_PENDING, BINDING_STATUSES, CONVERSATION_TYPES


def create_binding(tenant_id: str, user_id: str, device_id: str, account_binding_id: str,
                   conversation_type: str, label: str = "") -> Dict[str, Any]:
    """创建 pending 绑定（identity_version=0、验证字段 NULL，不冒充 verified）。"""
    if conversation_type not in CONVERSATION_TYPES:
        raise SessionTaskError(f"conversation_type 必须是 {'/'.join(CONVERSATION_TYPES)}", "VALIDATION_FAILED")
    from .config import scenario_enabled

    if not scenario_enabled(tenant_id):
        raise SessionTaskError("微信会话场景未启用", "FEATURE_DISABLED", 403)
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT tenant_id, user_id, status FROM local_tool_devices WHERE id=%s",
            (device_id,),
        )
        device = cursor.fetchone()
        if device is None or str(device["tenant_id"]) != tenant_id or str(device["user_id"]) != user_id:
            raise SessionTaskError("设备不存在或不属于当前用户", "NOT_FOUND", 404)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_weixin_conversation_bindings
                (tenant_id, user_id, device_id, account_binding_id, conversation_type, conversation_label,
                 verification_status)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending')
            RETURNING id, identity_version, verification_status, created_at
            """,
            (tenant_id, user_id, device_id, account_binding_id, conversation_type, label or None),
        )
        row = cursor.fetchone()
        conn.commit()
    return {
        "conversation_binding_id": str(row["id"]),
        "identity_version": row["identity_version"],
        "verification_status": row["verification_status"],
        "created_at": row["created_at"],
    }


def list_bindings(tenant_id: str, user_id: str, device_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """属主查询绑定（不含解密证据；列表只暴露状态与版本）。"""
    from src.db.database import get_db_connection

    where = "tenant_id=%s AND user_id=%s"
    params: List[Any] = [tenant_id, user_id]
    if device_id:
        where += " AND device_id=%s"
        params.append(device_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id, device_id, account_binding_id, conversation_type, conversation_label,
                   identity_version, verification_status, verified_at, expires_at, status, created_at
            FROM bs_weixin_conversation_bindings WHERE {where}
            ORDER BY created_at DESC LIMIT %s
            """,
            params + [limit],
        )
        rows = cursor.fetchall()
    return [dict(r) for r in rows]


def mark_binding(tenant_id: str, user_id: str, binding_id: UUID, status: str) -> Dict[str, Any]:
    """人工标记 invalid/expired（不允许手工写 verified）。"""
    if status not in BINDING_STATUSES or status == "verified":
        raise SessionTaskError("只允许标记 invalid/expired；verified 必须来自真机证据", "VALIDATION_FAILED")
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bs_weixin_conversation_bindings
            SET verification_status=%s, status=%s, updated_at=CURRENT_TIMESTAMP
            WHERE tenant_id=%s AND id=%s AND user_id=%s
            RETURNING id
            """,
            (status, "archived" if status == BINDING_PENDING else "active", tenant_id, binding_id, user_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise SessionTaskError("绑定不存在或无权访问", "NOT_FOUND", 404)
        conn.commit()
    return {"conversation_binding_id": str(binding_id), "verification_status": status}
