"""bs_wecom_* 三张业务表 DDL（设计 §6 最小边界）。

四处同步：本模块 / deploy/init-postgres.sql / deploy/db_update.yaml（增量块）/
tests/unit/wecom_conversation。业务表规范列 tenant_id/user_id/created_at；
无外键/触发器，引用完整性在 Python 层校验（database_dev 规范）。

唯一键（设计 §3.2/§5.2）：
- 会话 UNIQUE(tenant_id, user_id, source_account_id, source_conversation_id)
- 消息 UNIQUE(tenant_id, conversation_id, source_generation, source_message_id)
- 日报 UNIQUE(tenant_id, conversation_id, business_date)

旧列兼容保留、停止业务读写：is_followed/followed_at（人工开关已删）、
collection_runner_id/last_read_invocation_id（采集生命周期与业务恢复游标已删）、
generation_* / refreshed_at / refresh_invocation_id / input_watermark（生成状态
机与刷新证明已删）、handling（工单状态已删）。不为字段整洁 DROP/重建。
"""
from __future__ import annotations

CONVERSATIONS_DDL = """
CREATE TABLE IF NOT EXISTS bs_wecom_conversations (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source_account_id TEXT NOT NULL,
    source_conversation_id TEXT NOT NULL,
    session_locator TEXT NOT NULL,
    name TEXT,
    kind TEXT,
    peer_platform TEXT,
    device_id UUID,
    is_followed BOOLEAN NOT NULL DEFAULT TRUE,
    followed_at TIMESTAMPTZ,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    collection_runner_id TEXT,
    last_read_invocation_id UUID,
    last_checked_at TIMESTAMPTZ,
    last_archived_at TIMESTAMPTZ,
    enroll_reasons JSONB,
    daily_plan_task_id TEXT,
    coverage JSONB,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE (tenant_id, user_id, source_account_id, source_conversation_id)
)
"""

CONVERSATIONS_OWNER_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_bs_wecom_conversations_owner
    ON bs_wecom_conversations (tenant_id, user_id, updated_at DESC)
"""

CONVERSATIONS_PLAN_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_bs_wecom_conversations_plan
    ON bs_wecom_conversations (tenant_id, user_id, daily_plan_task_id)
"""

MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS bs_wecom_messages (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    conversation_id BIGINT NOT NULL,
    source_generation TEXT NOT NULL,
    source_message_id BIGINT NOT NULL,
    server_id TEXT,
    source_invocation_id UUID,
    sender_id TEXT,
    sender_name TEXT,
    name_source TEXT,
    sender_platform TEXT NOT NULL DEFAULT 'unknown',
    sender_side TEXT NOT NULL,
    kind TEXT,
    text TEXT,
    text_format TEXT,
    system BOOLEAN NOT NULL DEFAULT FALSE,
    parse_trusted BOOLEAN NOT NULL DEFAULT FALSE,
    baseline BOOLEAN NOT NULL DEFAULT FALSE,
    sent_at TIMESTAMPTZ,
    source_time_text TEXT,
    source_fields JSONB,
    attachment_ref JSONB,
    send_origin TEXT,
    origin_evidence JSONB,
    send_invocation_id UUID,
    handling TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE (tenant_id, conversation_id, source_generation, source_message_id)
)
"""

MESSAGES_TIMELINE_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_bs_wecom_messages_timeline
    ON bs_wecom_messages (tenant_id, conversation_id, sent_at, id)
"""

DAILY_SUMMARIES_DDL = """
CREATE TABLE IF NOT EXISTS bs_wecom_daily_summaries (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    conversation_id BIGINT NOT NULL,
    business_date DATE NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    generation_runner_id TEXT,
    generation_started_at TIMESTAMPTZ,
    generation_status TEXT NOT NULL DEFAULT 'refreshing',
    last_error TEXT,
    refreshed_at TIMESTAMPTZ,
    refresh_invocation_id UUID,
    input_watermark BIGINT,
    snapshot_at TIMESTAMPTZ,
    generated_at TIMESTAMPTZ,
    source_runner_id TEXT,
    coverage JSONB,
    summary JSONB,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE (tenant_id, conversation_id, business_date)
)
"""

DAILY_SUMMARIES_RANGE_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_bs_wecom_daily_summaries_range
    ON bs_wecom_daily_summaries (tenant_id, user_id, business_date DESC)
"""

# 已按旧未交付 DDL 建表的环境补齐新列（幂等；与 CREATE TABLE 同步维护）
_ALTER_STATEMENTS = (
    "ALTER TABLE bs_wecom_conversations ADD COLUMN IF NOT EXISTS enroll_reasons JSONB",
    "ALTER TABLE bs_wecom_conversations ADD COLUMN IF NOT EXISTS daily_plan_task_id TEXT",
    "ALTER TABLE bs_wecom_daily_summaries ADD COLUMN IF NOT EXISTS snapshot_at TIMESTAMPTZ",
    "ALTER TABLE bs_wecom_daily_summaries ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ",
    "ALTER TABLE bs_wecom_daily_summaries ADD COLUMN IF NOT EXISTS source_runner_id TEXT",
)

_DDL_ITEMS = (
    CONVERSATIONS_DDL,
    CONVERSATIONS_OWNER_INDEX_DDL,
    CONVERSATIONS_PLAN_INDEX_DDL,
    MESSAGES_DDL,
    MESSAGES_TIMELINE_INDEX_DDL,
    DAILY_SUMMARIES_DDL,
    DAILY_SUMMARIES_RANGE_INDEX_DDL,
)


def init_tables(conn) -> None:  # noqa: ANN001 - psycopg RealDictConnection
    """幂等建表（可重复执行；兼容全新初始化与既有库增量两条路径）。"""
    cursor = conn.cursor()
    for ddl in _DDL_ITEMS:
        cursor.execute(ddl)
    for statement in _ALTER_STATEMENTS:
        cursor.execute(statement)
    conn.commit()


def ensure_tables() -> None:
    """自开连接兜底建表（Skill 加载 / 启动初始化 / 测试用）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        init_tables(conn)
