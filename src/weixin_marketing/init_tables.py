"""bs_weixin_marketing_* 7 张业务表幂等 DDL（R40）

双轨落地：deploy/init-postgres.sql（全量）与本模块（幂等，init_database 挂接，
跟随 bs_recruiting 既有模式）；新业务表不进 db_update.yaml。
规范：UUID 主键 / TIMESTAMPTZ / tenant_id NOT NULL / 无外键无触发器 /
租户复合唯一键；content_blocks 的 kind 与字段互斥 CHECK 按【微信P §2】显式落地
（Python 侧 Pydantic 校验仍为第一道防线）。
"""

from loguru import logger

DDL_STATEMENTS = (
    # 1) automations：任务主档；version 乐观锁（If-Match CAS 409）
    """
    CREATE TABLE IF NOT EXISTS bs_weixin_marketing_automations (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        name TEXT NOT NULL,
        status TEXT DEFAULT 'draft' NOT NULL,
        active_revision_id UUID,
        draft_revision_id UUID,
        owner_scope TEXT DEFAULT 'owner' NOT NULL,
        version INTEGER DEFAULT 1 NOT NULL,
        pause_reason TEXT,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wxm_automations_tenant_status
        ON bs_weixin_marketing_automations (tenant_id, status, updated_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wxm_automations_tenant_user
        ON bs_weixin_marketing_automations (tenant_id, user_id, created_at DESC)
    """,
    # 2) revisions：发布后不可变（draft 可编辑；UNIQUE(tenant,automation,revision_no)）
    """
    CREATE TABLE IF NOT EXISTS bs_weixin_marketing_revisions (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        automation_id UUID NOT NULL,
        user_id TEXT NOT NULL,
        revision_no INTEGER NOT NULL,
        executor_type TEXT DEFAULT 'weixin.fixed_content.v1' NOT NULL,
        status TEXT DEFAULT 'draft' NOT NULL,
        trigger_json JSONB,
        policy_json JSONB,
        group_binding_id UUID,
        content_hash TEXT,
        authorized_by TEXT,
        authorization_source TEXT,
        source_message_id TEXT,
        published_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, automation_id, revision_no)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wxm_revisions_automation
        ON bs_weixin_marketing_revisions (tenant_id, automation_id, revision_no DESC)
    """,
    # 3) content_blocks：发布后不可变；kind 与字段互斥 CHECK（【微信P §2】）
    """
    CREATE TABLE IF NOT EXISTS bs_weixin_marketing_content_blocks (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        revision_id UUID NOT NULL,
        user_id TEXT NOT NULL,
        position INTEGER NOT NULL,
        kind TEXT NOT NULL,
        text_content TEXT,
        url TEXT,
        asset_id UUID,
        payload_hash TEXT,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, revision_id, position),
        CONSTRAINT ck_bs_wxm_blocks_kind CHECK (kind IN ('text', 'link', 'image')),
        CONSTRAINT ck_bs_wxm_blocks_fields_mutual_exclusive CHECK (
            (kind = 'text' AND text_content IS NOT NULL AND url IS NULL AND asset_id IS NULL)
            OR (kind = 'link' AND url IS NOT NULL AND text_content IS NULL AND asset_id IS NULL)
            OR (kind = 'image' AND asset_id IS NOT NULL AND text_content IS NULL AND url IS NULL)
        )
    )
    """,
    # 4) group_bindings：群绑定；label 非唯一身份（身份以 identity_evidence_ref/version 为准）
    """
    CREATE TABLE IF NOT EXISTS bs_weixin_marketing_group_bindings (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        device_id UUID,
        account_binding_id UUID,
        label TEXT,
        identity_evidence_ref TEXT,
        identity_version TEXT,
        state TEXT DEFAULT 'pending' NOT NULL,
        verified_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wxm_group_bindings_tenant_user
        ON bs_weixin_marketing_group_bindings (tenant_id, user_id, state)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wxm_group_bindings_account
        ON bs_weixin_marketing_group_bindings (tenant_id, account_binding_id)
    """,
    # 5) account_bindings：账号绑定（probe 验证依据；云端仅存受控摘要/引用）
    """
    CREATE TABLE IF NOT EXISTS bs_weixin_marketing_account_bindings (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        device_id UUID,
        account_anchor_ref TEXT,
        session_epoch INTEGER DEFAULT 0 NOT NULL,
        status TEXT DEFAULT 'active' NOT NULL,
        verified_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wxm_account_bindings_tenant_user
        ON bs_weixin_marketing_account_bindings (tenant_id, user_id, status)
    """,
    # 6) audit_events：微信配置/目标/内容变更审计（追加写；operation 审计在底座）
    """
    CREATE TABLE IF NOT EXISTS bs_weixin_marketing_audit_events (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT,
        automation_id UUID,
        run_id UUID,
        action TEXT NOT NULL,
        actor_type TEXT DEFAULT 'user' NOT NULL,
        actor_id TEXT,
        from_version INTEGER,
        to_version INTEGER,
        details_redacted JSONB DEFAULT '{}'::jsonb NOT NULL,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wxm_audit_tenant_automation
        ON bs_weixin_marketing_audit_events (tenant_id, automation_id, id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wxm_audit_run
        ON bs_weixin_marketing_audit_events (tenant_id, run_id, id)
    """,
    # 7) assets：图片素材（P2 仅建表/模型；上传与引用治理 P4）
    """
    CREATE TABLE IF NOT EXISTS bs_weixin_marketing_assets (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        storage_ref TEXT,
        sha256 TEXT,
        mime TEXT,
        size BIGINT,
        width INTEGER,
        height INTEGER,
        status TEXT DEFAULT 'active' NOT NULL,
        retention_until TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (tenant_id, id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wxm_assets_tenant_hash
        ON bs_weixin_marketing_assets (tenant_id, sha256)
    """,
)


def init_weixin_marketing_tables(conn) -> None:
    """幂等建 7 张表（由 src/db/database.py _init_postgresql 与集成测试调用）"""
    cursor = conn.cursor()
    for ddl in DDL_STATEMENTS:
        cursor.execute(ddl)
    conn.commit()
    logger.info("weixin_marketing 表初始化完成（bs_weixin_marketing_* 7 张业务表）")


def ensure_tables() -> None:
    """幂等建表（自开连接版，服务函数入口兜底调用）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        init_weixin_marketing_tables(conn)
