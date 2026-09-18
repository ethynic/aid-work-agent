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
    # C3 决策 worker 扩列：action=冻结的模型动作（reply/wait/handoff/done）；
    # model_attempts=实际模型调用次数（超时重试/修复各计一次，受 max_decisions 与
    # 费用预算约束）；failure_code=failed 决策的稳定失败原因。
    "ALTER TABLE session_task_decisions ADD COLUMN IF NOT EXISTS action TEXT",
    "ALTER TABLE session_task_decisions ADD COLUMN IF NOT EXISTS failure_code TEXT",
    "ALTER TABLE session_task_decisions ADD COLUMN IF NOT EXISTS model_attempts INTEGER NOT NULL DEFAULT 0",
    # §13.5 槽位语义：模型调用"已发起未确认结束"独立于决策状态——supersede/租约
    # 过期不清除；仅在调用返回（成功/异常）或滞留回收确认停止时清零，未确认结束
    # 前持续占用租户/任务在飞槽位，阻止替代调用重叠
    "ALTER TABLE session_task_decisions ADD COLUMN IF NOT EXISTS model_call_pending BOOLEAN NOT NULL DEFAULT FALSE",
    # attempt 级费用生命周期（C3 门禁 #1）：每次模型调用尝试的预留/启动/返回/
    # 计费状态持久化——补偿只释放"确实未启动"（state='reserved'）的 attempt，
    # 未知（started/returned/billing_pending）一律保留；与 cost_reservations 按
    # ref_key 一一对应（预留同事务写入）
    """
    CREATE TABLE IF NOT EXISTS session_task_decision_attempts (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        task_id UUID NOT NULL,
        decision_id UUID NOT NULL,
        attempt_ref TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'reserved',
        usage_json TEXT,
        credit_cost NUMERIC(14,4),
        billing_key TEXT,
        model TEXT,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, attempt_ref)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_task_attempts_decision
        ON session_task_decision_attempts (tenant_id, task_id, decision_id)
    """,
    # D1：返回时持久化用量/金额/账务键（既有表 ALTER 增列）
    "ALTER TABLE session_task_decision_attempts ADD COLUMN IF NOT EXISTS usage_json TEXT",
    "ALTER TABLE session_task_decision_attempts ADD COLUMN IF NOT EXISTS credit_cost NUMERIC(14,4)",
    "ALTER TABLE session_task_decision_attempts ADD COLUMN IF NOT EXISTS billing_key TEXT",
    "ALTER TABLE session_task_decision_attempts ADD COLUMN IF NOT EXISTS model TEXT",
    "ALTER TABLE session_task_decision_attempts ADD COLUMN IF NOT EXISTS user_id TEXT",
    "ALTER TABLE session_task_decision_attempts ADD COLUMN IF NOT EXISTS result_text_id UUID",
    "ALTER TABLE session_task_decision_attempts ADD COLUMN IF NOT EXISTS billing_retry_at TIMESTAMPTZ",
    "ALTER TABLE session_task_decision_attempts ADD COLUMN IF NOT EXISTS billing_retry_count INTEGER DEFAULT 0",
    # 账务幂等锚（五轮 #1）：稳定 attempt 计费键贯穿首次计费与补偿——部分唯一索引
    # 使 ChatRecordDB 写入判重与扣费在同一事务内恰好一次（并发补偿收敛单行）
    "ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS billing_ref TEXT",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_records_billing_ref
        ON chat_records (tenant_id, billing_ref)
        WHERE billing_ref IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_task_decisions_worker
        ON session_task_decisions (status, created_at)
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
    # 通用控制请求表（B1.2，设计 §5.5.5 冻结 schema）：human_required 异步迁移；
    # 只负责异步迁移，不是同步发送门禁；UNIQUE 前缀 (tenant_id, task_id) 同时服务
    # has_pending_control_request 的廉价检查
    """
    CREATE TABLE IF NOT EXISTS session_task_control_requests (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        task_id UUID NOT NULL,
        expected_control_epoch INTEGER NOT NULL,
        expected_block_epoch INTEGER NOT NULL,
        reason TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        processing_owner TEXT,
        processing_lease_expires_at TIMESTAMPTZ,
        retry_count INTEGER NOT NULL DEFAULT 0,
        next_retry_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        CHECK (status IN ('pending', 'processing', 'applied', 'stale', 'failed')),
        CHECK (retry_count >= 0)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_task_control_requests_scan
        ON session_task_control_requests (status, created_at)
    """,
    # CR 三审 P1-1：幂等键纳入 expected_block_epoch（唯一索引形态，可重复执行，
    # ON CONFLICT 列推断兼容）。同代同原因、不同 block epoch 的请求各自成行，
    # 旧请求按 epoch 复核自然 stale，新请求可 applied。
    """
    ALTER TABLE session_task_control_requests
        DROP CONSTRAINT IF EXISTS session_task_control_requests_tenant_id_task_id_expected_co_key
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_session_task_control_requests_idem
        ON session_task_control_requests (tenant_id, task_id, expected_control_epoch, expected_block_epoch, reason)
    """,
)


def init_session_task_tables(conn) -> None:  # noqa: ANN001 - psycopg/RealDictConnection
    for ddl in DDL_STATEMENTS:
        cursor = conn.cursor()
        cursor.execute(ddl)
    from .notifications import DDL as notices_ddl
    conn.cursor().execute(notices_ddl)
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
