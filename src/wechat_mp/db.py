"""微信公众号内容入知识库：数据库 schema 幂等初始化（WP1）。

DDL 以设计 docs/system/wechat-mp/wechat-mp-knowledge-ingestion-design.md §7.1/§8
（2026-09-14 二审定稿）为唯一权威，三处同步：
- 本文件（启动时幂等初始化，注册于 src/db/database.py init_database）
- deploy/init-postgres.sql（全新环境）
- deploy/db_update.yaml（存量环境增量批次）

所有语句幂等（IF NOT EXISTS / 部分唯一索引），可重复执行。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("wechat_mp.db")

DDL_STATEMENTS = (
    # ---- documents 系统表加 4 列（设计 §7.1；默认值即等价现状，存量数据无需回填）----
    # external_id 用 TEXT：设计 §7.1 草稿为 VARCHAR(128) 并注明"超限改 TEXT"；
    # 接口通道身份 appid:article_id:item_key 实测可超 128（article_id 64 字符 + appid 18 + 前缀），故取 TEXT
    """
    ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS origin VARCHAR(32) NOT NULL DEFAULT 'manual_upload',
        ADD COLUMN IF NOT EXISTS external_id TEXT,
        ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active',
        ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_origin_external
        ON documents(tenant_id, origin, external_id) WHERE external_id IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_documents_status
        ON documents(status, expires_at)
    """,
    # ---- 文章当前状态（设计 §8；唯一当前态记录，批次任务归属见 sync_items）----
    """
    CREATE TABLE IF NOT EXISTS bs_wechat_mp_articles (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        config_id TEXT,
        external_id TEXT NOT NULL,
        original_url TEXT,
        fetch_url TEXT,
        source_channel VARCHAR(16) NOT NULL,
        title TEXT,
        publish_time TIMESTAMP,
        wx_update_time TIMESTAMP,
        content_hash VARCHAR(64),
        doc_id INTEGER,
        sub_category VARCHAR(64),
        tags JSONB,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        master_article_row_id BIGINT,
        processing_status VARCHAR(16) DEFAULT 'pending',
        pipeline_version TEXT,
        next_retry_at TIMESTAMP,
        error_message TEXT,
        image_count INT DEFAULT 0,
        image_parsed_count INT DEFAULT 0,
        last_synced_at TIMESTAMP,
        last_checked_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT now(),
        UNIQUE(tenant_id, external_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wechat_mp_articles_retry
        ON bs_wechat_mp_articles(tenant_id, processing_status, next_retry_at)
    """,
    # ---- 回调事件收件箱（可靠接收：先落库再返回 success）----
    """
    CREATE TABLE IF NOT EXISTS bs_wechat_mp_events (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        config_id TEXT NOT NULL,
        event_key TEXT NOT NULL,
        run_id BIGINT,
        payload JSONB,
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        received_at TIMESTAMP DEFAULT now(),
        processed_at TIMESTAMP,
        error_message TEXT,
        UNIQUE(tenant_id, config_id, event_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wechat_mp_events_status
        ON bs_wechat_mp_events(status, received_at)
    """,
    # ---- 同步运行账本 + 执行队列（queued→running→终态）----
    """
    CREATE TABLE IF NOT EXISTS bs_wechat_mp_sync_runs (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        config_id TEXT,
        user_id TEXT,
        created_at TIMESTAMP DEFAULT now(),
        agent_id TEXT,
        session_id TEXT,
        owner_token TEXT,
        heartbeat_at TIMESTAMP,
        trigger_type VARCHAR(16) NOT NULL,
        status VARCHAR(32) NOT NULL,
        total_count INT,
        new_count INT,
        updated_count INT,
        deleted_count INT,
        skipped_count INT,
        failed_count INT,
        credits_charged NUMERIC(12,2) DEFAULT 0,
        error_message TEXT,
        started_at TIMESTAMP,
        completed_at TIMESTAMP
    )
    """,
    # 租户级串行：每租户至多一条 running（与 Redis 锁同粒度）；queued 不限条数
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_wechat_mp_runs_active
        ON bs_wechat_mp_sync_runs(tenant_id) WHERE status = 'running'
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wechat_mp_sync_runs_tenant_created
        ON bs_wechat_mp_sync_runs(tenant_id, created_at DESC)
    """,
    # ---- 批次内逐篇任务 ----
    """
    CREATE TABLE IF NOT EXISTS bs_wechat_mp_sync_items (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT,
        created_at TIMESTAMP DEFAULT now(),
        run_id BIGINT NOT NULL,
        article_row_id BIGINT NOT NULL,
        action TEXT,
        status TEXT,
        error_code TEXT,
        duplicate_of_item_id BIGINT,
        error_message TEXT,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        billing_status TEXT DEFAULT 'pending',
        billing_reference TEXT,
        credits_charged NUMERIC(12,2) DEFAULT 0,
        UNIQUE(tenant_id, run_id, article_row_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wechat_mp_sync_items_run
        ON bs_wechat_mp_sync_items(tenant_id, run_id)
    """,
    # ---- wechat_mp 渠道配置：同租户同 appid 部分唯一索引（设计 §4；系统表索引，三处同步）----
    # config 为 TEXT 列，jsonb 取值需显式 ::jsonb 转换
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_channel_configs_wechat_mp_appid
        ON tenant_channel_configs(tenant_id, ((config::jsonb)->>'appid'))
        WHERE channel_type = 'wechat_mp'
    """,
)


def init_wechat_mp_tables(conn) -> None:  # noqa: ANN001
    """幂等初始化 documents 加列与四张 bs_wechat_mp_* 表（可重复执行）。"""
    for ddl in DDL_STATEMENTS:
        cursor = conn.cursor()
        cursor.execute(ddl)
    conn.commit()


def ensure_tables() -> None:
    """自获取连接执行初始化（供技能加载/脚本调用）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        init_wechat_mp_tables(conn)
