"""desktop_automation events（事件源与事件接纳，【计划 §3.2】）

- 事件源：desktop_automation_event_sources（source_ref 受信标识、payload schema、
  允许的事件类型）；场景定义 payload schema，底座只存 payload_ref/hash。
- 接纳：UNIQUE(tenant_id, source_id, external_event_id) 去重；重复有效事件返回原接纳结果。
- 匹配：从 kind=event 的已编译订阅配置中按一致性快照确定 eligible 集合，持久化到
  events.eligible_revision_refs；匹配 worker 只遍历该持久集合（R1：以库函数交付，
  APScheduler tick 接线留给 P2）。
"""

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json

from src.db.database import get_db_connection
from src.desktop_automation.constants import EVENT_SOURCE_STATUS_ACTIVE, EVENT_STATE_PROCESSED

_SOURCE_COLUMNS = """
    id, tenant_id, scenario_key, source_ref, source_type, key_ref, key_version,
    payload_schema, allowed_event_types, status, created_at, updated_at
"""

_EVENT_COLUMNS = """
    id, tenant_id, source_id, external_event_id, event_type, payload_ref, payload_hash,
    state, match_cursor, eligible_revision_refs, occurred_at, received_at, created_at
"""


def register_event_source(
    *,
    tenant_id: str,
    scenario_key: str,
    source_ref: str,
    source_type: str,
    key_ref: Optional[str] = None,
    key_version: Optional[str] = None,
    payload_schema: Optional[Dict[str, Any]] = None,
    allowed_event_types: Optional[List[str]] = None,
) -> str:
    """注册/更新事件源（幂等 upsert；UNIQUE(tenant_id, source_ref)）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO desktop_automation_event_sources
                (tenant_id, scenario_key, source_ref, source_type, key_ref, key_version,
                 payload_schema, allowed_event_types, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active')
            ON CONFLICT (tenant_id, source_ref) DO UPDATE
            SET source_type = EXCLUDED.source_type,
                key_ref = EXCLUDED.key_ref,
                key_version = EXCLUDED.key_version,
                payload_schema = EXCLUDED.payload_schema,
                allowed_event_types = EXCLUDED.allowed_event_types,
                updated_at = NOW()
            RETURNING id
            """,
            (
                tenant_id, scenario_key, source_ref, source_type, key_ref, key_version,
                Json(payload_schema) if payload_schema else None,
                Json(allowed_event_types) if allowed_event_types else None,
            ),
        )
        source_id = str(cursor.fetchone()["id"])
        conn.commit()
        return source_id


def get_event_source_by_ref(tenant_id: str, source_ref: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        return get_event_source_by_ref_on(cursor, tenant_id, source_ref)


def get_event_source_by_ref_on(cursor, tenant_id: str, source_ref: str) -> Optional[Dict[str, Any]]:
    """按 source_ref 查事件源（P1-6：接受既有事务游标，供持锁事务内读取）"""
    cursor.execute(
        f"SELECT {_SOURCE_COLUMNS} FROM desktop_automation_event_sources "
        "WHERE tenant_id = %s AND source_ref = %s",
        (tenant_id, source_ref),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def payload_hash_of(payload: bytes) -> str:
    """事件/载荷摘要（payload_ref/hash 契约；正文不进通用表）"""
    return hashlib.sha256(payload).hexdigest()


def accept_event(
    *,
    tenant_id: str,
    source_ref: str,
    external_event_id: str,
    event_type: Optional[str],
    payload_ref: str,
    payload_hash: str,
    occurred_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """持久接纳事件（幂等：UNIQUE(tenant_id, source_id, external_event_id)，
    重复有效事件返回原接纳结果，不重新选择版本或执行）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        result = accept_event_on(
            cursor,
            tenant_id=tenant_id, source_ref=source_ref,
            external_event_id=external_event_id, event_type=event_type,
            payload_ref=payload_ref, payload_hash=payload_hash,
            occurred_at=occurred_at,
        )
        conn.commit()
        return result


def accept_event_on(
    cursor,
    *,
    tenant_id: str,
    source_ref: str,
    external_event_id: str,
    event_type: Optional[str],
    payload_ref: str,
    payload_hash: str,
    occurred_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """accept_event 的同事务版本（P4-B：供 webhook/源侧投递器把 nonce 消耗、
    payload 持久化与事件接纳并入同一业务事务；调用方负责 commit/rollback）。"""
    source = get_event_source_by_ref_on(cursor, tenant_id, source_ref)
    if source is None or source["status"] != EVENT_SOURCE_STATUS_ACTIVE:
        return {"accepted": False, "reason": "source_not_found"}
    allowed = source.get("allowed_event_types")
    if allowed and event_type not in allowed:
        return {"accepted": False, "reason": "event_type_not_allowed"}

    # 一致性快照确定 eligible 集合并持久化（“接收时有效”= 本事务读取快照）
    eligible = find_eligible_subscriptions(cursor, tenant_id, source, event_type)

    cursor.execute(
        """
        INSERT INTO desktop_automation_events
            (tenant_id, source_id, external_event_id, event_type, payload_ref,
             payload_hash, state, eligible_revision_refs, occurred_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'received', %s, %s)
        ON CONFLICT (tenant_id, source_id, external_event_id) DO NOTHING
        RETURNING id
        """,
        (
            tenant_id, str(source["id"]), external_event_id, event_type, payload_ref,
            payload_hash, Json(eligible), occurred_at,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        # 幂等：重复有效事件返回原接纳结果（不重新选择版本或执行）
        cursor.execute(
            """
            SELECT id, state FROM desktop_automation_events
            WHERE tenant_id = %s AND source_id = %s AND external_event_id = %s
            """,
            (tenant_id, str(source["id"]), external_event_id),
        )
        existing = cursor.fetchone()
        return {
            "accepted": True,
            "event_id": str(existing["id"]) if existing else None,
            "duplicate": True,
            "eligible": [],
            "state": existing["state"] if existing else None,
        }
    return {
        "accepted": True, "event_id": str(row["id"]), "duplicate": False,
        "eligible": eligible,
    }


def find_eligible_subscriptions(
    cursor, tenant_id: str, source: Dict[str, Any], event_type: Optional[str]
) -> List[Dict[str, Any]]:
    """匹配候选：kind=event、source_ref/event_type 匹配的已编译订阅，且当时 task subject
    启用、active_revision_ref 匹配、revision subject 有效（快照内一次读取）。"""
    cursor.execute(
        """
        SELECT s.task_ref, s.revision_ref, s.scenario_key, s.condition_ref, s.delay_seconds, s.id
        FROM desktop_automation_schedules s
        JOIN desktop_automation_subjects t
          ON t.tenant_id = s.tenant_id AND t.scenario_key = s.scenario_key
         AND t.kind = 'task' AND t.ref = s.task_ref
        JOIN desktop_automation_subjects r
          ON r.tenant_id = s.tenant_id AND r.scenario_key = s.scenario_key
         AND r.kind = 'revision' AND r.ref = s.revision_ref AND r.task_ref = s.task_ref
        WHERE s.tenant_id = %s AND s.kind = 'event' AND s.status = 'active'
          AND s.source_ref = %s AND (s.event_type = %s OR s.event_type IS NULL OR s.event_type = '*')
          AND t.status = 'active' AND t.active_revision_ref = s.revision_ref
          AND r.status = 'published'
        ORDER BY s.scenario_key, s.task_ref, s.revision_ref, s.id
        """,
        (tenant_id, source["source_ref"], event_type),
    )
    # DB drivers may return the UUID primary key as uuid.UUID. The frozen
    # snapshot is JSON, so normalize this identifier at its schema boundary.
    return [{**dict(r), "id": str(r["id"])} for r in cursor.fetchall()]


def get_event(event_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_EVENT_COLUMNS} FROM desktop_automation_events "
            "WHERE id = %s AND tenant_id = %s",
            (event_id, tenant_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def advance_match_cursor(
    cursor, event_id: str, tenant_id: str, *, processed: bool = False,
    expected_cursor: Optional[int] = None,
) -> bool:
    """推进匹配水位（锁顺序置于 task 锁之后；全部处理完成才置 processed）。

    P4-B：expected_cursor 提供时为 CAS 推进（match_cursor 未变才 +1）——多个匹配
    worker 对同一游标竞争，失败方（返回 False）应回滚整个候选事务。缺省保持
    无条件推进（既有调用行为不变）。
    """
    state = EVENT_STATE_PROCESSED if processed else "processing"
    if expected_cursor is not None:
        cursor.execute(
            """
            UPDATE desktop_automation_events
            SET match_cursor = match_cursor + 1, state = %s, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s AND match_cursor = %s
            """,
            (state, event_id, tenant_id, expected_cursor),
        )
    else:
        cursor.execute(
            """
            UPDATE desktop_automation_events
            SET match_cursor = match_cursor + 1, state = %s
            WHERE id = %s AND tenant_id = %s
            """,
            (state, event_id, tenant_id),
        )
    return cursor.rowcount > 0


def list_unprocessed_events(
    scenario_key: str,
    *,
    limit: int = 100,
    tenant_allowlist: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """匹配 worker 扫描查询：received/processing 事件按 (created_at, id) 稳定排序分页。

    processing 仍在列表内——worker 崩溃后 state 停在 processing，重启恢复依赖
    match_cursor 断点续跑（不漏已接纳事件）。eligible_revision_refs 为空且非
    processed 的事件由调用方直接收敛 processed。
    """
    sql = f"""
        SELECT e.id, e.tenant_id, e.source_id, e.external_event_id, e.event_type,
               e.payload_ref, e.payload_hash, e.state, e.match_cursor,
               e.eligible_revision_refs, e.occurred_at, e.received_at, e.created_at,
               s.scenario_key AS source_scenario_key, s.source_ref
        FROM desktop_automation_events e
        JOIN desktop_automation_event_sources s
          ON s.id = e.source_id AND s.tenant_id = e.tenant_id
        WHERE e.state <> %s AND s.scenario_key = %s
    """
    params: List[Any] = [EVENT_STATE_PROCESSED, scenario_key]
    if tenant_allowlist is not None:
        sql += " AND e.tenant_id = ANY(%s)"
        params.append(list(tenant_allowlist))
    sql += " ORDER BY e.created_at, e.id LIMIT %s"
    params.append(limit)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, tuple(params))
        return [dict(r) for r in cursor.fetchall()]


def mark_event_processed(event_id: str, tenant_id: str) -> bool:
    """快照集合全部处理完成后置 processed（独立短事务；未处理完绝不提前置位）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE desktop_automation_events
            SET state = 'processed', updated_at = NOW()
            WHERE id = %s AND tenant_id = %s AND state <> 'processed'
            """,
            (event_id, tenant_id),
        )
        ok = cursor.rowcount > 0
        conn.commit()
        return ok
