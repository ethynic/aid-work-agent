"""desktop_automation 幂等 DDL（R3：三处同步的代码侧权威）

与 deploy/init-postgres.sql（全量）和 deploy/db_update.yaml（存量增量）保持一致；
由 src/db/database.py _init_postgresql 以 try/except 降级风格调用（与其他模块一致）。

规范：TIMESTAMPTZ / UUID 主键 / 无外键无触发器（引用完整性在 Python 校验）/
tenant_id 一律 NOT NULL / 任务族表 user_id NOT NULL（events/outbox/audit/quota 等系统
生成行允许 NULL，database_dev.md 例外）/ 租户复合唯一键。
"""

from loguru import logger

# 一个逻辑批次的全部 DDL（幂等，可重复执行）
DDL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS desktop_automation_subjects (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        scenario_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        ref TEXT NOT NULL,
        version TEXT,
        owner_id TEXT NOT NULL,
        status TEXT NOT NULL,
        active_revision_ref TEXT,
        authorization_epoch INTEGER DEFAULT 0 NOT NULL,
        task_ref TEXT,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, scenario_key, kind, ref)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_da_subjects_revision_task
        ON desktop_automation_subjects (tenant_id, scenario_key, task_ref)
        WHERE kind = 'revision'
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_automation_schedules (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        scenario_key TEXT NOT NULL,
        task_ref TEXT NOT NULL,
        revision_ref TEXT NOT NULL,
        user_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        trigger_key TEXT NOT NULL,
        timezone TEXT,
        anchor_at TIMESTAMPTZ,
        interval_seconds INTEGER,
        cron_expr TEXT,
        day_of_week TEXT,
        next_fire_at TIMESTAMPTZ,
        ends_at TIMESTAMPTZ,
        max_count INTEGER,
        run_count INTEGER DEFAULT 0 NOT NULL,
        grace_seconds INTEGER DEFAULT 0 NOT NULL,
        miss_policy TEXT DEFAULT 'skip_overlap' NOT NULL,
        one_shot BOOLEAN DEFAULT FALSE NOT NULL,
        consumed BOOLEAN DEFAULT FALSE NOT NULL,
        source_ref TEXT,
        event_type TEXT,
        condition_ref TEXT,
        delay_seconds INTEGER,
        status TEXT DEFAULT 'active' NOT NULL,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, scenario_key, revision_ref, trigger_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_da_schedules_due
        ON desktop_automation_schedules (tenant_id, next_fire_at)
        WHERE kind = 'time' AND status = 'active' AND consumed = FALSE
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_da_schedules_event
        ON desktop_automation_schedules (tenant_id, source_ref, event_type)
        WHERE kind = 'event'
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_automation_event_sources (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        scenario_key TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        source_type TEXT NOT NULL,
        key_ref TEXT,
        key_version TEXT,
        payload_schema JSONB,
        allowed_event_types JSONB,
        status TEXT DEFAULT 'active' NOT NULL,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, source_ref)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_automation_events (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        source_id UUID NOT NULL,
        external_event_id TEXT NOT NULL,
        event_type TEXT,
        payload_ref TEXT,
        payload_hash TEXT,
        state TEXT DEFAULT 'received' NOT NULL,
        match_cursor INTEGER DEFAULT 0 NOT NULL,
        eligible_revision_refs JSONB,
        occurred_at TIMESTAMPTZ,
        received_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, source_id, external_event_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_da_events_state
        ON desktop_automation_events (tenant_id, state, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_automation_occurrences (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        scenario_key TEXT NOT NULL,
        task_ref TEXT NOT NULL,
        revision_ref TEXT NOT NULL,
        user_id TEXT NOT NULL,
        trigger_kind TEXT NOT NULL,
        trigger_key TEXT NOT NULL,
        scheduled_for TIMESTAMPTZ,
        due_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ,
        status TEXT DEFAULT 'open' NOT NULL,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, scenario_key, task_ref, trigger_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_da_occurrences_task
        ON desktop_automation_occurrences (tenant_id, scenario_key, task_ref, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_automation_runs (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        occurrence_id UUID NOT NULL,
        scenario_key TEXT NOT NULL,
        task_ref TEXT NOT NULL,
        revision_ref TEXT NOT NULL,
        user_id TEXT NOT NULL,
        state TEXT DEFAULT 'pending' NOT NULL,
        device_id UUID,
        lease_expires_at TIMESTAMPTZ,
        fence_token INTEGER DEFAULT 0 NOT NULL,
        authorization_epoch INTEGER,
        due_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ,
        result_json JSONB,
        claimed_at TIMESTAMPTZ,
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, occurrence_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_da_runs_open
        ON desktop_automation_runs (tenant_id, scenario_key, task_ref)
        WHERE state NOT IN ('succeeded', 'failed', 'cancelled', 'partial', 'unknown', 'expired')
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_da_runs_due
        ON desktop_automation_runs (due_at)
        WHERE state = 'pending'
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_automation_deliveries (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        run_id UUID NOT NULL,
        scenario_key TEXT,
        task_ref TEXT,
        revision_ref TEXT,
        user_id TEXT NOT NULL,
        position INTEGER NOT NULL,
        operation TEXT NOT NULL,
        provider_key TEXT,
        target_ref TEXT,
        target_handle TEXT,
        target_version TEXT,
        payload_ref TEXT,
        payload_hash TEXT,
        state TEXT DEFAULT 'pending' NOT NULL,
        effect TEXT,
        phase TEXT,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        finished_at TIMESTAMPTZ,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, run_id, position)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_da_deliveries_run
        ON desktop_automation_deliveries (tenant_id, run_id, position)
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_automation_attempts (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        delivery_id UUID NOT NULL,
        run_id UUID,
        user_id TEXT NOT NULL,
        attempt_no INTEGER NOT NULL,
        invocation_id UUID,
        permit_id UUID,
        request_id TEXT NOT NULL,
        effect TEXT,
        phase TEXT,
        safe_to_retry BOOLEAN,
        evidence_ref TEXT,
        result_ref TEXT,
        detail_json JSONB,
        predecessor_attempt_id UUID,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        finished_at TIMESTAMPTZ,
        PRIMARY KEY (id),
        UNIQUE (invocation_id),
        UNIQUE (tenant_id, delivery_id, attempt_no)
    )
    """,
    # R27：写后验证证据登记——UNIQUE(tenant_id, evidence_ref) 事务级防复用，
    # INSERT ON CONFLICT 仲裁并发（败者绑定比对：同操作幂等放行/他操作拒绝）
    """
    CREATE TABLE IF NOT EXISTS desktop_automation_evidence (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT,
        evidence_ref TEXT NOT NULL,
        invocation_id UUID NOT NULL,
        attempt_id UUID NOT NULL,
        device_id UUID NOT NULL,
        request_id TEXT NOT NULL,
        target_ref TEXT,
        payload_hash TEXT,
        effect TEXT,
        phase TEXT,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, evidence_ref)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_da_evidence_attempt
        ON desktop_automation_evidence (tenant_id, attempt_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_automation_audit_events (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT,
        scenario_key TEXT,
        kind TEXT NOT NULL,
        aggregate_type TEXT NOT NULL,
        aggregate_ref TEXT NOT NULL,
        detail JSONB DEFAULT '{}' NOT NULL,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_da_audit_agg
        ON desktop_automation_audit_events (tenant_id, aggregate_type, aggregate_ref, id)
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_automation_outbox (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT,
        kind TEXT NOT NULL,
        aggregate_ref TEXT NOT NULL,
        dedupe_key TEXT NOT NULL,
        state TEXT DEFAULT 'pending' NOT NULL,
        available_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        lease_expires_at TIMESTAMPTZ,
        attempt_count INTEGER DEFAULT 0 NOT NULL,
        payload_ref TEXT,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, kind, dedupe_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_da_outbox_pending
        ON desktop_automation_outbox (state, available_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_automation_quota_buckets (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT,
        scope_type TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        bucket_start TIMESTAMPTZ NOT NULL,
        window_seconds INTEGER NOT NULL,
        limit_count INTEGER NOT NULL,
        reserved_count INTEGER DEFAULT 0 NOT NULL,
        used_count INTEGER DEFAULT 0 NOT NULL,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, scope_type, scope_id, bucket_start)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS local_tool_operation_permits (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        invocation_id UUID NOT NULL,
        device_id UUID NOT NULL,
        claim_token_hash TEXT NOT NULL,
        request_id TEXT NOT NULL,
        delivery_id UUID,
        operation TEXT,
        target_ref TEXT,
        target_version TEXT,
        payload_hash TEXT,
        authorization_revision TEXT,
        authorization_epoch INTEGER,
        resource_key TEXT,
        quota_reservation JSONB,
        state TEXT DEFAULT 'issued' NOT NULL,
        permit_token_hash TEXT NOT NULL,
        deadline TIMESTAMPTZ NOT NULL,
        issued_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        consumed_at TIMESTAMPTZ,
        expired_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_lt_permits_invocation_request
        ON local_tool_operation_permits (tenant_id, invocation_id, request_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_lt_permits_delivery
        ON local_tool_operation_permits (tenant_id, delivery_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_lt_permits_expire
        ON local_tool_operation_permits (state, deadline)
    """,
    # local_tool_invocations 扩列（旧行全 NULL = 聊天 proxy 链路；v2 扩展只加列不改既有列）
    "ALTER TABLE local_tool_invocations ADD COLUMN IF NOT EXISTS provider_key TEXT",
    "ALTER TABLE local_tool_invocations ADD COLUMN IF NOT EXISTS business_kind TEXT",
    "ALTER TABLE local_tool_invocations ADD COLUMN IF NOT EXISTS business_ref JSONB",
    "ALTER TABLE local_tool_invocations ADD COLUMN IF NOT EXISTS dedupe_key TEXT",
    "ALTER TABLE local_tool_invocations ADD COLUMN IF NOT EXISTS deadline_at TIMESTAMPTZ",
    "ALTER TABLE local_tool_invocations ADD COLUMN IF NOT EXISTS authorization_epoch INTEGER",
    "ALTER TABLE local_tool_invocations ADD COLUMN IF NOT EXISTS write_phase TEXT",
    # 会话任务执行道（设计 §10）：仅服务端设置 'session_task'，默认 'standard'。
    # 通用 claim 在 SQL 层排除 session_task；定向 claim 只接 session_task 且绑定
    # assignment。NOT NULL DEFAULT 使旧行迁移后即 standard，老客户端不可领新道。
    "ALTER TABLE local_tool_invocations ADD COLUMN IF NOT EXISTS execution_lane TEXT NOT NULL DEFAULT 'standard'",
    """
    CREATE INDEX IF NOT EXISTS idx_lt_inv_lane_claim
        ON local_tool_invocations (tenant_id, device_id, state, execution_lane)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_lt_invocations_dedupe
        ON local_tool_invocations (tenant_id, business_kind, dedupe_key)
        WHERE business_kind IS NOT NULL AND dedupe_key IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_lt_inv_provider
        ON local_tool_invocations (tenant_id, provider_key, state)
    """,
)


def init_desktop_automation_tables(conn) -> None:
    """幂等建表（在 _init_postgresql 连接上执行；失败由调用方降级处理）"""
    cursor = conn.cursor()
    for ddl in DDL_STATEMENTS:
        cursor.execute(ddl)
    conn.commit()
    logger.info(
        "desktop_automation 表初始化完成（12 系统表 + local_tool_operation_permits "
        "+ invocations 扩列）"
    )
