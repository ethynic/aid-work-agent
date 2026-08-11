"""本地工具 DB 访问层（同步 psycopg2，全部 SQL 参数化）

调用方（FastAPI async endpoint）必须用 asyncio.to_thread 包裹，禁止在事件循环内直接调用。
所有查询/更新必带 tenant_id（claim 同时带 device_id）。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from psycopg2.extras import Json

from src.db.database import get_db_connection

# 终态：写 result 时遇到终态直接幂等返回
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "unknown", "expired")


# ==================== 配对码 ====================


def create_ticket(tenant_id: str, user_id: str, code_hash: str, expires_at: datetime) -> str:
    """创建配对码（库存 hash），返回 ticket id"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO local_tool_pairing_tickets (tenant_id, user_id, code_hash, expires_at)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (tenant_id, user_id, code_hash, expires_at),
        )
        ticket_id = str(cursor.fetchone()["id"])
        conn.commit()
        return ticket_id


def invalidate_unused_tickets(tenant_id: str, user_id: str) -> int:
    """作废同一用户所有未使用的旧配对码，防止多码并存"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE local_tool_pairing_tickets
            SET used_at = NOW()
            WHERE tenant_id = %s AND user_id = %s AND used_at IS NULL
            """,
            (tenant_id, user_id),
        )
        count = cursor.rowcount
        conn.commit()
        return count


def consume_ticket(code_hash: str) -> Optional[Dict[str, Any]]:
    """原子单次消费配对码：未使用且未过期才命中，返回 ticket 否则 None"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE local_tool_pairing_tickets
            SET used_at = NOW()
            WHERE code_hash = %s AND used_at IS NULL AND expires_at > NOW()
            RETURNING id, tenant_id, user_id, expires_at, used_at, created_at
            """,
            (code_hash,),
        )
        row = cursor.fetchone()
        conn.commit()
        return dict(row) if row else None


# ==================== 设备 ====================


def create_device(
    tenant_id: str,
    user_id: str,
    token_hash: str,
    name: Optional[str] = None,
    platform: Optional[str] = None,
    runtime_version: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
    machine_fingerprint_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """配对成功后创建设备（只存 token hash）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO local_tool_devices
                (tenant_id, user_id, name, platform, runtime_version, token_hash,
                 machine_fingerprint_hash, capabilities_json, last_seen_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            RETURNING id, tenant_id, user_id, name, platform, runtime_version,
                      capabilities_json, selected, status, last_seen_at, created_at
            """,
            (
                tenant_id,
                user_id,
                name,
                platform,
                runtime_version,
                token_hash,
                machine_fingerprint_hash,
                Json(capabilities) if capabilities is not None else None,
            ),
        )
        row = dict(cursor.fetchone())
        conn.commit()
        return row


def get_device_by_token_hash(token_hash: str) -> Optional[Dict[str, Any]]:
    """按 token hash 查 active 设备（Runtime API 鉴权用）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tenant_id, user_id, name, platform, runtime_version,
                   capabilities_json, manifest_digest, selected, status, last_seen_at
            FROM local_tool_devices
            WHERE token_hash = %s AND status = 'active'
            """,
            (token_hash,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_devices(tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
    """当前 tenant+user 的设备列表（最新在前）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tenant_id, user_id, name, platform, runtime_version,
                   capabilities_json, selected, status, last_seen_at, created_at
            FROM local_tool_devices
            WHERE tenant_id = %s AND user_id = %s
            ORDER BY created_at DESC
            """,
            (tenant_id, user_id),
        )
        return [dict(r) for r in cursor.fetchall()]


def revoke_device(tenant_id: str, user_id: str, device_id: str) -> bool:
    """撤销设备（status=revoked，selected 清除），返回是否命中"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE local_tool_devices
            SET status = 'revoked', selected = FALSE, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s AND user_id = %s AND status = 'active'
            """,
            (device_id, tenant_id, user_id),
        )
        ok = cursor.rowcount > 0
        conn.commit()
        return ok


def touch_device_seen(
    device_id: str,
    runtime_version: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
    manifest_digest: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """心跳：更新 last_seen / 版本 / 能力 / manifest 摘要，返回最新 selected/status"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE local_tool_devices
            SET last_seen_at = NOW(),
                runtime_version = COALESCE(%s, runtime_version),
                capabilities_json = COALESCE(%s, capabilities_json),
                manifest_digest = COALESCE(%s, manifest_digest),
                updated_at = NOW()
            WHERE id = %s
            RETURNING selected, status, last_seen_at
            """,
            (
                runtime_version,
                Json(capabilities) if capabilities is not None else None,
                manifest_digest,
                device_id,
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return dict(row) if row else None


def select_device(tenant_id: str, user_id: str, device_id: str) -> bool:
    """选定设备（单选）：事务内先清后设，目标设备必须归属该用户且 active"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE local_tool_devices SET selected = FALSE WHERE tenant_id = %s AND user_id = %s",
            (tenant_id, user_id),
        )
        cursor.execute(
            """
            UPDATE local_tool_devices
            SET selected = TRUE, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s AND user_id = %s AND status = 'active'
            """,
            (device_id, tenant_id, user_id),
        )
        ok = cursor.rowcount > 0
        conn.commit()
        return ok


# ==================== Invocation ====================


def create_invocation(
    tenant_id: str,
    user_id: str,
    device_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> str:
    """创建 invocation（state=queued），返回 id。M0.5 路由层使用"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO local_tool_invocations
                (tenant_id, user_id, device_id, tool_name, arguments_json)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (tenant_id, user_id, device_id, tool_name, Json(arguments)),
        )
        invocation_id = str(cursor.fetchone()["id"])
        conn.commit()
        return invocation_id


def get_invocation(invocation_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """按 id+tenant 查询 invocation。M0.5 proxy 轮询终态使用"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tenant_id, user_id, device_id, tool_name, arguments_json,
                   state, effect, result_json, error_code, error_message,
                   created_at, claimed_at, started_at, finished_at
            FROM local_tool_invocations
            WHERE id = %s AND tenant_id = %s
            """,
            (invocation_id, tenant_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_events(invocation_id: str, since_seq: int = 0) -> List[Dict[str, Any]]:
    """按 seq 游标拉取新事件（升序）。M0.5 proxy 进度转发使用"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT seq, stage, current, total, message, created_at
            FROM local_tool_events
            WHERE invocation_id = %s AND seq > %s
            ORDER BY seq
            """,
            (invocation_id, since_seq),
        )
        return [dict(r) for r in cursor.fetchall()]


def expire_stale_claims() -> int:
    """租约过期的 claimed/running → unknown/effect=unknown（claim 路径顺带调用）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE local_tool_invocations
            SET state = 'unknown', effect = 'unknown', finished_at = NOW()
            WHERE state IN ('claimed', 'running')
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < NOW()
            """
        )
        count = cursor.rowcount
        conn.commit()
        if count:
            logger.warning(f"后端日志：{count} 条本地工具 invocation 租约过期，置为 unknown")
        return count


def claim_next(
    device_id: str,
    tenant_id: str,
    claim_token_hash: str,
    lease_seconds: int,
) -> Optional[Dict[str, Any]]:
    """领取下一条 queued invocation（事务 + FOR UPDATE SKIP LOCKED 防重复领取）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id FROM local_tool_invocations
            WHERE state = 'queued' AND device_id = %s AND tenant_id = %s
            ORDER BY created_at
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """,
            (device_id, tenant_id),
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return None
        cursor.execute(
            """
            UPDATE local_tool_invocations
            SET state = 'claimed',
                claim_token_hash = %s,
                lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                claimed_at = NOW()
            WHERE id = %s
            RETURNING id, tenant_id, user_id, device_id, tool_name, arguments_json,
                      state, lease_expires_at, claimed_at, created_at
            """,
            (claim_token_hash, lease_seconds, row["id"]),
        )
        invocation = dict(cursor.fetchone())
        conn.commit()
        return invocation


def mark_started(invocation_id: str, tenant_id: str, claim_token_hash: str) -> Optional[Dict[str, Any]]:
    """claimed → running。hash 匹配时返回当前记录（无论是否迁移），不匹配/不存在返回 None"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE local_tool_invocations
            SET state = 'running', started_at = NOW()
            WHERE id = %s AND tenant_id = %s AND claim_token_hash = %s AND state = 'claimed'
            RETURNING *
            """,
            (invocation_id, tenant_id, claim_token_hash),
        )
        row = cursor.fetchone()
        conn.commit()
        if row:
            return dict(row)
        cursor.execute(
            """
            SELECT * FROM local_tool_invocations
            WHERE id = %s AND tenant_id = %s AND claim_token_hash = %s
            """,
            (invocation_id, tenant_id, claim_token_hash),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def append_event(
    invocation_id: str,
    tenant_id: str,
    claim_token_hash: str,
    stage: Optional[str],
    current: Optional[int],
    total: Optional[int],
    message: Optional[str],
    lease_seconds: int,
) -> Optional[Tuple[int, bool]]:
    """追加进度事件并续租，返回 (seq, cancel_flag)。

    校验 claim token + state in (claimed, running, cancel_requested)。
    cancel_requested 也放行，否则设备无法在 progress 响应里拿到 cancel=true。
    hash 不匹配或状态不允许返回 None。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT state FROM local_tool_invocations
            WHERE id = %s AND tenant_id = %s AND claim_token_hash = %s
            """,
            (invocation_id, tenant_id, claim_token_hash),
        )
        row = cursor.fetchone()
        if not row or row["state"] not in ("claimed", "running", "cancel_requested"):
            conn.commit()
            return None

        cancel_flag = row["state"] == "cancel_requested"

        cursor.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM local_tool_events WHERE invocation_id = %s",
            (invocation_id,),
        )
        seq = cursor.fetchone()["next_seq"]
        cursor.execute(
            """
            INSERT INTO local_tool_events (invocation_id, tenant_id, seq, stage, current, total, message)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (invocation_id, tenant_id, seq, stage, current, total, message),
        )
        # 进度回传顺带续租
        cursor.execute(
            """
            UPDATE local_tool_invocations
            SET lease_expires_at = NOW() + (%s * INTERVAL '1 second')
            WHERE id = %s
            """,
            (lease_seconds, invocation_id),
        )
        conn.commit()
        return seq, cancel_flag


def write_result(
    invocation_id: str,
    tenant_id: str,
    claim_token_hash: str,
    success: bool,
    code: Optional[str] = None,
    message: Optional[str] = None,
    effect: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    retryable: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """写入终态（幂等：已终态直接返回当前记录）。

    state 映射：success→succeeded；code='EXECUTION_UNKNOWN'→unknown；其余失败→failed。
    hash 不匹配或不存在返回 None。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM local_tool_invocations WHERE id = %s AND tenant_id = %s",
            (invocation_id, tenant_id),
        )
        row = cursor.fetchone()
        if not row or row["claim_token_hash"] != claim_token_hash:
            conn.commit()
            return None
        if row["state"] in TERMINAL_STATES:
            # 幂等：已终态重复写返回原状态，不报错
            conn.commit()
            return dict(row)

        if success:
            new_state = "succeeded"
        elif code == "EXECUTION_UNKNOWN":
            new_state = "unknown"
        else:
            new_state = "failed"

        result_json = {
            "success": success,
            "code": code,
            "message": message,
            "data": data,
            "retryable": retryable,
        }
        cursor.execute(
            """
            UPDATE local_tool_invocations
            SET state = %s,
                effect = %s,
                result_json = %s,
                error_code = %s,
                error_message = %s,
                finished_at = NOW(),
                lease_expires_at = NULL
            WHERE id = %s
            RETURNING *
            """,
            (
                new_state,
                effect,
                Json(result_json),
                None if success else code,
                None if success else message,
                invocation_id,
            ),
        )
        updated = dict(cursor.fetchone())
        conn.commit()
        return updated


def request_cancel(invocation_id: str, tenant_id: str) -> bool:
    """请求取消。M0.5 路由层使用。

    - queued → cancelled（终态，effect=none）：设备永远不会领取，必须直接落终态，
      否则行会永久卡在 cancel_requested（claim_next 只取 queued，租约清扫只管 claimed/running）
    - claimed/running → cancel_requested：等设备在 progress 响应里看到 cancel=true 后写终态
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE local_tool_invocations
            SET state = 'cancelled', effect = 'none', finished_at = NOW()
            WHERE id = %s AND tenant_id = %s AND state = 'queued'
            """,
            (invocation_id, tenant_id),
        )
        count = cursor.rowcount
        cursor.execute(
            """
            UPDATE local_tool_invocations
            SET state = 'cancel_requested'
            WHERE id = %s AND tenant_id = %s AND state IN ('claimed', 'running')
            """,
            (invocation_id, tenant_id),
        )
        count += cursor.rowcount
        conn.commit()
        return count > 0
