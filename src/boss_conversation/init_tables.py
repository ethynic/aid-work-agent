"""boss_conversation 场景表 DDL（B2，设计 §5.2/§5.3/§5.5.4/§5.6 冻结）。

四处同步：本模块 / deploy/init-postgres.sql / deploy/db_update.yaml（增量块）/
tests/unit/session_tasks/test_migrations.py。bs_boss_* 为业务表（tenant_id/user_id/
created_at 规范列；无外键/触发器，引用完整性在 Python 层校验——database_dev 规范）。

- bs_boss_conversation_bindings：候选人绑定（verified 才可自动发送；频控触发计数
  列 + 同步阻断列；部分唯一索引保证同租户同设备同 candidate_name+job_id 仅一条
  verified 有效绑定）；
- bs_boss_reply_script_versions：不可变话术版本（append-only，lineage+version_no）；
- bs_boss_conversation_rate_slots / bs_boss_rate_settlement_anomalies：频控账本与
  异常队列（§5.5.4 冻结 DDL 原样落地，含 CHECK 约束与部分窗口索引）；
- bs_boss_comm_log_projection_queue：沟通日志投影队列（§5.6）；
- bs_recruiting_operator_resume_comm_logs ALTER 补 source_delivery_id/
  source_message_id + UNIQUE(tenant_id, source_delivery_id)（表可能未建——按
  information_schema 探测存在才执行，幂等可重复）。
"""
from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger("boss_conversation.init")

BINDINGS_DDL = """
CREATE TABLE IF NOT EXISTS bs_boss_conversation_bindings (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    device_id UUID NOT NULL,
    account_scope_id UUID NOT NULL,
    candidate_name TEXT NOT NULL,
    job_id TEXT NOT NULL,
    resume_id BIGINT,
    identity_version INTEGER NOT NULL DEFAULT 0,
    verification_status TEXT NOT NULL DEFAULT 'pending',
    login_fingerprint_hash TEXT,
    encrypted_identity_evidence TEXT,
    verified_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    rate_trigger_date DATE,
    rate_trigger_count INTEGER NOT NULL DEFAULT 0,
    last_rate_decision_id UUID,
    automation_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    automation_block_reason TEXT,
    automation_block_epoch INTEGER NOT NULL DEFAULT 0,
    automation_blocked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, id),
    CHECK (verification_status IN ('pending', 'verified', 'invalid', 'expired')),
    CHECK (rate_trigger_count >= 0),
    CHECK (automation_block_epoch >= 0)
)
"""

BINDINGS_INDEX_DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_boss_conv_bindings_verified
    ON bs_boss_conversation_bindings (tenant_id, device_id, candidate_name, job_id)
    WHERE verification_status = 'verified'
"""

BINDINGS_OWNER_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_boss_conv_bindings_owner
    ON bs_boss_conversation_bindings (tenant_id, user_id, device_id, created_at DESC)
"""

SCRIPT_VERSIONS_DDL = """
CREATE TABLE IF NOT EXISTS bs_boss_reply_script_versions (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    lineage_id UUID NOT NULL,
    version_no INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    template TEXT NOT NULL,
    slot_schema JSONB NOT NULL,
    source_script_id UUID,
    source_job_id UUID,
    source_job_name TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, lineage_id, version_no),
    CHECK (version_no >= 1)
)
"""

RATE_SLOTS_DDL = """
CREATE TABLE IF NOT EXISTS bs_boss_conversation_rate_slots (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    binding_id UUID NOT NULL,
    decision_id UUID NOT NULL,
    delivery_id UUID NOT NULL,
    status TEXT NOT NULL,
    reserved_at TIMESTAMPTZ NOT NULL,
    settled_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    settlement_effect TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, delivery_id),
    UNIQUE (tenant_id, decision_id),
    CHECK (status IN ('reserved', 'settled', 'released')),
    CHECK (settlement_effect IS NULL OR settlement_effect IN ('submitted', 'verified', 'unknown', 'not_started')),
    CHECK ((status='reserved' AND settled_at IS NULL AND released_at IS NULL)
        OR (status='settled'  AND settled_at IS NOT NULL AND released_at IS NULL)
        OR (status='released' AND released_at IS NOT NULL AND settled_at IS NULL)),
    CHECK ((status='reserved' AND settlement_effect IS NULL)
        OR (status='settled' AND settlement_effect IN ('submitted', 'verified', 'unknown'))
        OR (status='released' AND settlement_effect='not_started'))
)
"""

RATE_SLOTS_WINDOW_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_boss_rate_windows
    ON bs_boss_conversation_rate_slots (tenant_id, binding_id, reserved_at)
    WHERE status IN ('reserved', 'settled')
"""

ANOMALIES_DDL = """
CREATE TABLE IF NOT EXISTS bs_boss_rate_settlement_anomalies (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    task_id UUID NOT NULL,
    binding_id UUID NOT NULL,
    delivery_id UUID NOT NULL,
    invocation_id UUID NOT NULL,
    error_code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, delivery_id),
    CHECK (status IN ('pending', 'processing', 'resolved', 'ignored')),
    CHECK (retry_count >= 0)
)
"""

PROJECTION_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS bs_boss_comm_log_projection_queue (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    delivery_id UUID NOT NULL,
    binding_id UUID NOT NULL,
    resume_id BIGINT,
    status TEXT NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    last_error_code TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, delivery_id),
    CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    CHECK (retry_count >= 0)
)
"""

PROJECTION_QUEUE_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_boss_comm_log_projection_scan
    ON bs_boss_comm_log_projection_queue (status, next_retry_at, created_at)
"""

# 投影目标表补列（§5.6）：目标表由 recruiting 模块自建，可能尚未存在——
# 探测存在才执行（幂等；db_update.yaml 增量块同语句）。
COMM_LOGS_TARGET_TABLE = "bs_recruiting_operator_resume_comm_logs"

COMM_LOGS_ALTER_STATEMENTS: Tuple[str, ...] = (
    f"ALTER TABLE {COMM_LOGS_TARGET_TABLE} ADD COLUMN IF NOT EXISTS source_delivery_id UUID",
    f"ALTER TABLE {COMM_LOGS_TARGET_TABLE} ADD COLUMN IF NOT EXISTS source_message_id TEXT",
    f"""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_boss_comm_logs_source_delivery
        ON {COMM_LOGS_TARGET_TABLE} (tenant_id, source_delivery_id)
    """,
)


def _table_exists(cursor, table_name: str) -> bool:  # noqa: ANN001
    cursor.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = %s",
        (table_name,),
    )
    return cursor.fetchone() is not None


def init_boss_conversation_tables(conn) -> None:  # noqa: ANN001 - psycopg/RealDictConnection
    """幂等建表（可重复执行；兼容全新初始化与既有库增量两条路径）。"""
    cursor = conn.cursor()
    for ddl in (
        BINDINGS_DDL,
        BINDINGS_INDEX_DDL,
        BINDINGS_OWNER_INDEX_DDL,
        SCRIPT_VERSIONS_DDL,
        RATE_SLOTS_DDL,
        RATE_SLOTS_WINDOW_INDEX_DDL,
        ANOMALIES_DDL,
        PROJECTION_QUEUE_DDL,
        PROJECTION_QUEUE_INDEX_DDL,
    ):
        cursor.execute(ddl)
    if _table_exists(cursor, COMM_LOGS_TARGET_TABLE):
        for ddl in COMM_LOGS_ALTER_STATEMENTS:
            cursor.execute(ddl)
    conn.commit()


def ensure_tables() -> None:
    """自开连接兜底建表（测试/脚本用）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        init_boss_conversation_tables(conn)


def boss_conversation_tables_ready() -> bool:
    try:
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM bs_boss_conversation_bindings LIMIT 1")
        return True
    except Exception:  # noqa: BLE001
        return False
