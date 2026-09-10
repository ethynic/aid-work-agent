"""内部事件适配示例（P4-B，R57）：示例业务状态变更 → 源侧 outbox 同事务 → 投递器
→ 底座 events 表（accept_event_on，eligible 快照同事务）→ 匹配 worker → occurrence/run。

全链为假数据测试示例，供 P5 后真实业务事件源参照：
- 示例业务对象 example_order（pending → completed）；
- complete_example_order 在**同一业务事务**写业务行 + 源侧 outbox
  （desktop_automation_outbox kind='wxm_example_event'，dedupe_key 确定性）；
- deliver_example_event_outbox 定向领取（kind 过滤，不与 dispatch tick 的
  run_due 条目互抢）→ 同事务投递（payload 持久化 + accept_event_on）→ done；
- 崩溃恢复：投递事务失败整体回滚（outbox 条目回 pending 重投），事件接纳幂等
  （UNIQUE(tenant_id, source_id, external_event_id)）。
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from psycopg2.extras import Json

from src.db.database import get_db_connection
from src.desktop_automation import events as da_events
from src.desktop_automation import outbox as da_outbox

# 示例源标识（受信 source_ref；注册后方可投递）
EXAMPLE_SOURCE_REF = "wxm-example-internal"
EXAMPLE_EVENT_TYPE = "example.order_completed"

# 源侧 outbox kind（与底座 run_due/run_finished 区分；投递器按此定向领取）
OUTBOX_KIND_EXAMPLE_EVENT = "wxm_example_event"
# 投递重试上限（毒丸收敛 failed，行保留对账）
OUTBOX_MAX_ATTEMPTS = 10

_EXAMPLE_DDL = """
CREATE TABLE IF NOT EXISTS weixin_marketing_example_orders (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    label TEXT,
    status TEXT DEFAULT 'pending' NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
)
"""

_tables_ready = False


def ensure_example_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(_EXAMPLE_DDL)
        conn.commit()
    _tables_ready = True


def example_dedupe_key(order_id: str) -> str:
    """order → 事件投递的确定性 dedupe_key（每订单恰一条 completed 事件）"""
    return f"example_event:{order_id}"


def create_example_order(
    *, tenant_id: str, user_id: str, label: str = "示例订单"
) -> Dict[str, Any]:
    """创建示例业务对象（pending；无事件副作用）"""
    ensure_example_tables()
    order_id = str(uuid.uuid4())
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO weixin_marketing_example_orders
                (id, tenant_id, user_id, label, status)
            VALUES (%s, %s, %s, %s, 'pending')
            """,
            (order_id, tenant_id, user_id, label),
        )
        conn.commit()
    return {"order_id": order_id, "status": "pending", "label": label}


def complete_example_order(
    *, tenant_id: str, user_id: str, order_id: str, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """示例业务状态变更（pending → completed）：业务行更新与源侧 outbox 同一事务。

    幂等：已完成订单重复调用不重复入队（dedupe_key 唯一 + 状态机拒绝）。
    """
    ensure_example_tables()
    now = now or datetime.now(timezone.utc)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, status FROM weixin_marketing_example_orders
            WHERE id = %s AND tenant_id = %s FOR UPDATE
            """,
            (order_id, tenant_id),
        )
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            raise KeyError("example order not found")
        if row["status"] != "pending":
            conn.rollback()
            return {"order_id": order_id, "status": row["status"], "enqueued": False}
        cur.execute(
            """
            UPDATE weixin_marketing_example_orders
            SET status = 'completed', completed_at = %s, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s
            """,
            (now, order_id, tenant_id),
        )
        da_outbox.enqueue_outbox(
            cur, tenant_id, OUTBOX_KIND_EXAMPLE_EVENT, order_id,
            example_dedupe_key(order_id), user_id=user_id,
        )
        conn.commit()
    logger.info(
        f"后端日志：weixin_marketing 示例订单完成并入队 tenant={tenant_id} order={order_id}"
    )
    return {"order_id": order_id, "status": "completed", "enqueued": True}


def _claim_example_outbox(limit: int = 50, lease_seconds: int = 60) -> List[Dict[str, Any]]:
    """定向领取示例事件条目（kind 过滤 + FOR UPDATE SKIP LOCKED；不动 run_due 条目）"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id FROM desktop_automation_outbox
            WHERE state = 'pending' AND kind = %s AND available_at <= NOW()
            ORDER BY created_at
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (OUTBOX_KIND_EXAMPLE_EVENT, limit),
        )
        ids = [str(r["id"]) for r in cur.fetchall()]
        if not ids:
            conn.commit()
            return []
        cur.execute(
            """
            UPDATE desktop_automation_outbox
            SET state = 'processing',
                lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                attempt_count = attempt_count + 1, updated_at = NOW()
            WHERE id::text = ANY(%s)
            RETURNING id, tenant_id, aggregate_ref, dedupe_key, attempt_count
            """,
            (lease_seconds, ids),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows


def _example_envelope(order: Dict[str, Any]) -> Dict[str, Any]:
    """订单行 → 受控事件 envelope（正文只进 payloads 表，events 行只存 ref/hash）"""
    return {
        "kind": "example_order",
        "order_id": str(order["id"]),
        "label": order.get("label"),
        "status": order["status"],
        "completed_at": order.get("completed_at").isoformat()
        if order.get("completed_at") else None,
    }


def _mark_example_failed(outbox_id: str, tenant_id: str, attempts: int, reason: str) -> None:
    """毒丸收敛：超上限条目置 failed（行保留对账；对应业务事件不再投递）"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE desktop_automation_outbox
            SET state = 'failed', lease_expires_at = NULL, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s
            """,
            (outbox_id, tenant_id),
        )
        conn.commit()
    logger.error(
        f"后端日志：weixin_marketing 示例事件投递毒丸收敛 id={outbox_id} "
        f"attempts={attempts} reason={reason}"
    )


def _mark_outbox_done_on(cursor, outbox_id: str, tenant_id: str) -> None:
    """游标内标 done（与事件接纳同事务——避免独立连接先标 done 而主事务回滚丢事件）"""
    cursor.execute(
        """
        UPDATE desktop_automation_outbox
        SET state = 'done', lease_expires_at = NULL, updated_at = NOW()
        WHERE id = %s AND tenant_id = %s
        """,
        (outbox_id, tenant_id),
    )


def deliver_example_event_outbox(
    *, limit: int = 50, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """投递器：源侧 outbox → 底座 events 表（同事务：payload 持久化 + accept_event_on
    + 条目 done——三者同 commit，崩溃恢复不留半个状态）。

    单条失败整体回滚留 pending（attempt_count 递增），崩溃由 lease 过期回 pending 重投；
    事件接纳幂等（external_event_id=order_id 去重），重投不产生重复事件行。
    """
    import hashlib

    from src.weixin_marketing import event_sources as wxm_sources

    ensure_example_tables()
    wxm_sources.ensure_event_source_tables()
    now = now or datetime.now(timezone.utc)
    entries = _claim_example_outbox(limit=limit)
    delivered, duplicates, failed = 0, 0, 0
    for entry in entries:
        tenant_id = entry["tenant_id"]
        order_id = str(entry["aggregate_ref"])
        try:
            result = None
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, tenant_id, label, status, completed_at
                    FROM weixin_marketing_example_orders
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (order_id, tenant_id),
                )
                order = cur.fetchone()
                if order is None:
                    raise RuntimeError("example order row missing")
                envelope = _example_envelope(dict(order))
                payload_bytes = wxm_sources.canonical_payload_bytes(envelope)
                payload_hash = hashlib.sha256(payload_bytes).hexdigest()
                payload_ref = wxm_sources.store_event_payload_on(
                    cur, tenant_id, None, payload_hash, envelope
                )
                result = da_events.accept_event_on(
                    cur,
                    tenant_id=tenant_id, source_ref=EXAMPLE_SOURCE_REF,
                    external_event_id=f"example:{order_id}",
                    event_type=EXAMPLE_EVENT_TYPE,
                    payload_ref=payload_ref, payload_hash=payload_hash,
                    occurred_at=now,
                )
                if not result.get("accepted"):
                    raise RuntimeError(f"event not accepted: {result.get('reason')}")
                _mark_outbox_done_on(cur, str(entry["id"]), tenant_id)
                conn.commit()
            if result.get("duplicate"):
                duplicates += 1
            else:
                delivered += 1
        except Exception as e:  # noqa: BLE001 单条隔离：回滚留 pending 重投
            failed += 1
            logger.opt(exception=True).warning(
                f"后端日志：weixin_marketing 示例事件投递失败 order={order_id}: {e}"
            )
            if int(entry.get("attempt_count") or 0) + 1 >= OUTBOX_MAX_ATTEMPTS:
                _mark_example_failed(
                    str(entry["id"]), tenant_id, int(entry.get("attempt_count") or 0) + 1,
                    repr(e)[:200],
                )
    return {"claimed": len(entries), "delivered": delivered,
            "duplicates": duplicates, "failed": failed}


def register_example_source(tenant_id: str) -> Optional[str]:
    """注册示例内部事件源（幂等；受信注册点——生产不自动调用）"""
    return da_events.register_event_source(
        tenant_id=tenant_id,
        scenario_key=_scenario_key(),
        source_ref=EXAMPLE_SOURCE_REF,
        source_type="internal",
        allowed_event_types=[EXAMPLE_EVENT_TYPE],
    )


def _scenario_key() -> str:
    from src.weixin_marketing.constants import SCENARIO_KEY

    return SCENARIO_KEY


def get_example_order(tenant_id: str, order_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tenant_id, user_id, label, status, completed_at, created_at
            FROM weixin_marketing_example_orders
            WHERE id = %s AND tenant_id = %s
            """,
            (order_id, tenant_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
