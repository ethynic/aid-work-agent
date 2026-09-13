"""端侧会话任务系统表族 DDL（C1，设计 §4/§9/§11/§13）。

三处同步（系统表规范）：本模块 / deploy/init-postgres.sql / deploy/db_update.yaml。
bs_weixin_conversation_bindings 是业务表，由 weixin_conversation 模块自建（见
src/weixin_conversation/init_tables.py），不进 db_update.yaml。

约束要点（设计 §4/§9/§11/§13）：
- session_tasks 部分唯一索引冻结"占用会话唯一"（未终结已发布状态，draft 不占用）；
- events 连续前缀 ACK：UNIQUE(tenant,assignment,local_seq) + UNIQUE(tenant,event_id)；
- decisions 五元唯一键 + opening 跨 spec_revision 部分唯一（§13.2）；
- texts/confirmations 按 §13.3/§13.5；cost_reservations 是预算预留接口的幂等账
  （§13.4：已结算+未决预留共同占任务额度，C3 接真实账务结算）。
无外键/触发器（数据库规范），引用完整性在 Python 层校验。
"""
from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger("session_tasks.init")

DDL_STATEMENTS: Tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS session_tasks (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        scenario_key TEXT NOT NULL,
        device_id UUID NOT NULL,
        account_binding_id UUID NOT NULL,
        conversation_binding_id UUID NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        version INTEGER NOT NULL DEFAULT 1,
        draft_spec_text_id UUID,
        draft_digest TEXT,
        spec_revision INTEGER,
        current_spec_id UUID,
        control_epoch INTEGER NOT NULL DEFAULT 0,
        server_control_seq INTEGER NOT NULL DEFAULT 0,
        completion_reason TEXT,
        blocked_reason TEXT,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_session_tasks_occupancy
        ON session_tasks (tenant_id, device_id, account_binding_id, conversation_binding_id)
        WHERE status IN ('active', 'paused', 'human_required', 'blocked')
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_tasks_owner
        ON session_tasks (tenant_id, user_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS session_task_specs (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        task_id UUID NOT NULL,
        revision INTEGER NOT NULL,
        spec_text_id UUID,
        limits_json TEXT NOT NULL,
        work_window_json TEXT,
        published_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, task_id, revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_task_assignments (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        task_id UUID NOT NULL,
        device_id UUID NOT NULL,
        runtime_instance_id TEXT NOT NULL,
        fence INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        is_current BOOLEAN DEFAULT TRUE NOT NULL,
        lease_expires_at TIMESTAMPTZ NOT NULL,
        claimed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        control_epoch_at_claim INTEGER NOT NULL DEFAULT 0,
        acked_local_seq INTEGER NOT NULL DEFAULT 0,
        server_control_seq INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_session_task_assignments_current
        ON session_task_assignments (tenant_id, task_id)
        WHERE is_current = TRUE
    """,
    """
    CREATE TABLE IF NOT EXISTS session_task_events (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        task_id UUID NOT NULL,
        assignment_id UUID NOT NULL,
        local_seq INTEGER NOT NULL,
        event_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        payload_text_id UUID,
        received_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, assignment_id, local_seq),
        UNIQUE (tenant_id, event_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_task_messages (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        task_id UUID NOT NULL,
        conversation_binding_id TEXT NOT NULL,
        binding_version INTEGER NOT NULL DEFAULT 0,
        input_version INTEGER NOT NULL,
        message_id TEXT NOT NULL,
        sender TEXT NOT NULL,
        text_id UUID,
        evidence_ref TEXT,
        batch_id TEXT,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, task_id, message_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_task_batches (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        task_id UUID NOT NULL,
        batch_id TEXT NOT NULL,
        input_version INTEGER NOT NULL,
        message_ids_json TEXT,
        observation_id TEXT,
        synthetic BOOLEAN DEFAULT FALSE NOT NULL,
        status TEXT NOT NULL DEFAULT 'accepted',
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, task_id, batch_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_task_decisions (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        task_id UUID NOT NULL,
        spec_revision INTEGER NOT NULL,
        batch_id TEXT NOT NULL,
        decision_kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        input_version INTEGER NOT NULL DEFAULT 0,
        reply_text_id UUID,
        reply_text_hash TEXT,
        completion_evidence TEXT,
        model_call_ref TEXT,
        lease_owner TEXT,
        lease_expires_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, id),
        UNIQUE (tenant_id, task_id, spec_revision, batch_id, decision_kind)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_session_task_decisions_opening
        ON session_task_decisions (tenant_id, task_id)
        WHERE decision_kind = 'opening'
    """,
    """
    CREATE TABLE IF NOT EXISTS session_task_execution_links (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        task_id UUID NOT NULL,
        decision_id UUID NOT NULL,
        occurrence_id UUID,
        run_id UUID,
        delivery_id UUID,
        invocation_id UUID,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, decision_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_task_texts (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        task_id UUID NOT NULL,
        purpose TEXT NOT NULL,
        encrypted_payload TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, task_id, id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_task_confirmations (
        confirmation_id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        task_id UUID NOT NULL,
        spec_revision INTEGER NOT NULL,
        spec_digest TEXT NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        consumed_at TIMESTAMPTZ,
        published_revision INTEGER,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (confirmation_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_task_confirmations_task
        ON session_task_confirmations (tenant_id, task_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS session_task_cost_reservations (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        task_id UUID NOT NULL,
        purpose TEXT NOT NULL,
        ref_key TEXT NOT NULL,
        amount NUMERIC(14,4) NOT NULL,
        state TEXT NOT NULL DEFAULT 'reserved',
        settled_amount NUMERIC(14,4),
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, task_id, purpose, ref_key)
    )
    """,
    # 模块级接口幂等（沿用 weixin_marketing_idempotency_keys 范式；业务配套表，不进 db_update.yaml）
    """
    CREATE TABLE IF NOT EXISTS session_tasks_idempotency_keys (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        route TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        response_json TEXT,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        UNIQUE (tenant_id, user_id, route, idempotency_key)
    )
    """,
)


def init_session_task_tables(conn) -> None:  # noqa: ANN001 - psycopg/RealDictConnection
    for ddl in DDL_STATEMENTS:
        cursor = conn.cursor()
        cursor.execute(ddl)
    conn.commit()


def ensure_tables() -> None:
    """自开连接兜底建表（对齐 weixin_marketing.ensure_tables 范式）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        init_session_task_tables(conn)


def session_tasks_tables_ready() -> bool:
    """探测系统表是否已建（测试 conftest 用；未建则 skip 提示先跑 init_database）。"""
    try:
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM session_tasks LIMIT 1")
        return True
    except Exception:  # noqa: BLE001 表未建/无 DB 均视为未就绪
        return False
