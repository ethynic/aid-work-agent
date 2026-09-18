"""desktop_automation attempts（一次操作尝试账本，§5.1 三层标识的第三层）

attempt/invocation：一次操作尝试；只有证明未提交或人工决定再次发送时才能新建 attempt
（predecessor_attempt_id 关联人工重试链）。invocation 唯一绑定 attempt。
"""

from typing import Any, Dict, List, Optional

from psycopg2.extras import Json

from src.db.database import get_db_connection

_ATTEMPT_COLUMNS = """
    id, tenant_id, delivery_id, run_id, user_id, attempt_no, invocation_id, permit_id,
    request_id, effect, phase, safe_to_retry, evidence_ref, result_ref, detail_json,
    predecessor_attempt_id, created_at, finished_at
"""


def create_attempt(
    cursor,
    tenant_id: str,
    delivery: Dict[str, Any],
    invocation_id: str,
    request_id: str,
    *,
    permit_id: Optional[str] = None,
    predecessor_attempt_id: Optional[str] = None,
) -> str:
    """单步驱动时创建 attempt（attempt_no = 已有 +1；invocation 唯一）"""
    cursor.execute(
        "SELECT COALESCE(MAX(attempt_no), 0) + 1 AS next_no "
        "FROM desktop_automation_attempts WHERE delivery_id = %s AND tenant_id = %s",
        (str(delivery["id"]), tenant_id),
    )
    attempt_no = cursor.fetchone()["next_no"]
    cursor.execute(
        """
        INSERT INTO desktop_automation_attempts
            (tenant_id, delivery_id, run_id, user_id, attempt_no, invocation_id,
             permit_id, request_id, predecessor_attempt_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            tenant_id, str(delivery["id"]), delivery.get("run_id"), delivery.get("user_id"),
            attempt_no, invocation_id, permit_id, request_id, predecessor_attempt_id,
        ),
    )
    return str(cursor.fetchone()["id"])


def get_attempt(attempt_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_ATTEMPT_COLUMNS} FROM desktop_automation_attempts "
            "WHERE id = %s AND tenant_id = %s",
            (attempt_id, tenant_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_attempt_by_invocation(invocation_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        return get_attempt_by_invocation_on(cursor, invocation_id, tenant_id)


def get_attempt_by_invocation_on(cursor, invocation_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """invocation 唯一映射 attempt（P1-6：接受既有事务游标，供持锁事务内读取）"""
    cursor.execute(
        f"SELECT {_ATTEMPT_COLUMNS} FROM desktop_automation_attempts "
        "WHERE invocation_id = %s AND tenant_id = %s",
        (invocation_id, tenant_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def lock_attempt_by_invocation_on(cursor, invocation_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """invocation 唯一映射 attempt 并 FOR UPDATE（CR 阻断 1：operation-result
    冻结锁序 invocation→attempt→delivery→(guard)binding→rate slot 的第二环；
    列集与 get_attempt_by_invocation_on 一致）。"""
    cursor.execute(
        f"SELECT {_ATTEMPT_COLUMNS} FROM desktop_automation_attempts "
        "WHERE invocation_id = %s AND tenant_id = %s FOR UPDATE",
        (invocation_id, tenant_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def find_attempt_by_request_id(request_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_ATTEMPT_COLUMNS} FROM desktop_automation_attempts "
            "WHERE request_id = %s AND tenant_id = %s ORDER BY created_at DESC",
            (request_id, tenant_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_delivery_attempts(delivery_id: str, tenant_id: str) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_ATTEMPT_COLUMNS} FROM desktop_automation_attempts "
            "WHERE delivery_id = %s AND tenant_id = %s ORDER BY attempt_no",
            (delivery_id, tenant_id),
        )
        return [dict(r) for r in cursor.fetchall()]


def finish_attempt(
    cursor,
    attempt_id: str,
    tenant_id: str,
    *,
    effect: Optional[str] = None,
    phase: Optional[str] = None,
    safe_to_retry: Optional[bool] = None,
    evidence_ref: Optional[str] = None,
    result_ref: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> bool:
    """写 attempt 结果（原始 effect 与人工业务判定分别存储，互不覆盖）"""
    sets = ["finished_at = NOW()"]
    params: List[Any] = []
    for col, val in (
        ("effect", effect), ("phase", phase), ("safe_to_retry", safe_to_retry),
        ("evidence_ref", evidence_ref), ("result_ref", result_ref),
    ):
        if val is not None:
            sets.append(f"{col} = %s")
            params.append(val)
    if detail is not None:
        sets.append("detail_json = %s")
        params.append(Json(detail))
    params.extend([attempt_id, tenant_id])
    cursor.execute(
        f"""
        UPDATE desktop_automation_attempts
        SET {", ".join(sets)}
        WHERE id = %s AND tenant_id = %s AND finished_at IS NULL
        """,
        tuple(params),
    )
    return cursor.rowcount > 0
