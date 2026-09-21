"""外部内容同步通用表：幂等 DDL（注册于 src/db/database.py）。

三处同步：本文件 / deploy/init-postgres.sql / deploy/db_update.yaml。
所有语句幂等（IF NOT EXISTS / ON CONFLICT DO NOTHING），可重复执行。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("content_sync.db")

DDL_STATEMENTS = (
    # ---- 源配置：谁在定时跑由配置行决定（module 区分数据源，租户×源一行）----
    """
    CREATE TABLE IF NOT EXISTS bs_content_sync_sources (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        module TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        sync_interval_hours INT NOT NULL DEFAULT 24,
        selection_mode VARCHAR(8) NOT NULL DEFAULT 'all',
        selected_ids JSONB,
        last_sync_at TIMESTAMP,
        last_error TEXT,
        created_at TIMESTAMP DEFAULT now(),
        updated_at TIMESTAMP DEFAULT now(),
        UNIQUE(tenant_id, module),
        CHECK (selection_mode IN ('all', 'ids'))
    )
    """,
    # ---- 运行账本 + 执行队列（queued→running→终态；蓝本 bs_wechat_mp_sync_runs）----
    """
    CREATE TABLE IF NOT EXISTS bs_content_sync_runs (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        module TEXT NOT NULL,
        user_id TEXT,
        trigger_type VARCHAR(16) NOT NULL,
        status VARCHAR(32) NOT NULL,
        owner_token TEXT,
        heartbeat_at TIMESTAMP,
        new_count INT DEFAULT 0,
        updated_count INT DEFAULT 0,
        skipped_count INT DEFAULT 0,
        deleted_count INT DEFAULT 0,
        restored_count INT DEFAULT 0,
        failed_count INT DEFAULT 0,
        vl_parsed_count INT DEFAULT 0,
        vl_billed_count INT DEFAULT 0,
        embedding_tokens INT DEFAULT 0,
        credits_charged NUMERIC(12,2) DEFAULT 0,
        fetch_complete BOOLEAN NOT NULL DEFAULT FALSE,
        total_reported INT,
        error_message TEXT,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT now()
    )
    """,
    # 同租户同源至多一条 running（queued 不限）；异源可并行
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_content_sync_runs_active
        ON bs_content_sync_runs(tenant_id, module) WHERE status = 'running'
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_content_sync_runs_tenant_created
        ON bs_content_sync_runs(tenant_id, module, created_at DESC)
    """,
    # ---- 批次内逐条任务（蓝本 bs_wechat_mp_sync_items）----
    """
    CREATE TABLE IF NOT EXISTS bs_content_sync_items (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        run_id BIGINT NOT NULL,
        module TEXT NOT NULL,
        native_id TEXT NOT NULL,
        action VARCHAR(16),
        status VARCHAR(16),
        error_code TEXT,
        error_message TEXT,
        vl_images INT DEFAULT 0,
        vl_billed INT DEFAULT 0,
        embedding_tokens INT DEFAULT 0,
        billing_status TEXT DEFAULT 'pending',
        billing_reference TEXT,
        credits_charged NUMERIC(12,2) DEFAULT 0,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT now(),
        UNIQUE(run_id, native_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_content_sync_items_run
        ON bs_content_sync_items(tenant_id, run_id)
    """,
    # ---- 记录当前态账本（蓝本 bs_wechat_mp_articles；payload 存各源目录展示字段）----
    """
    CREATE TABLE IF NOT EXISTS bs_content_sync_records (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        module TEXT NOT NULL,
        native_id TEXT NOT NULL,
        external_id TEXT NOT NULL,
        content_hash VARCHAR(64),
        pipeline_version TEXT,
        doc_id INTEGER,
        user_deleted BOOLEAN NOT NULL DEFAULT FALSE,
        miss_streak INT NOT NULL DEFAULT 0,
        fail_count INT NOT NULL DEFAULT 0,
        next_retry_at TIMESTAMP,
        processing_status VARCHAR(16) NOT NULL DEFAULT 'pending',
        error_message TEXT,
        payload JSONB,
        last_seen_at TIMESTAMP,
        last_synced_at TIMESTAMP,
        last_checked_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT now(),
        UNIQUE(tenant_id, module, native_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_content_sync_records_retry
        ON bs_content_sync_records(tenant_id, module, processing_status, next_retry_at)
    """,
    # ---- 图级 VL 缓存（tenant+URL 键：任何源的图片共用；URL 不变即图不变）----
    """
    CREATE TABLE IF NOT EXISTS bs_image_vision_cache (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        image_url TEXT NOT NULL,
        description TEXT,
        model TEXT,
        status VARCHAR(16) NOT NULL,
        is_billed BOOLEAN NOT NULL DEFAULT FALSE,
        parsed_at TIMESTAMP DEFAULT now(),
        created_at TIMESTAMP DEFAULT now(),
        UNIQUE(tenant_id, image_url),
        CHECK (status IN ('ok', 'unrecognized', 'failed'))
    )
    """,
)
# 计费价格种子行不入平台 DDL：各源模块（含租户定制）自带自举种植，
# 平台通用模块不得包含任何租户/源专名（2026-09-21 用户决议）


def init_content_sync_tables(conn) -> None:  # noqa: ANN001
    """幂等初始化五张通用表（可重复执行；价格种子行由各源模块自举）。"""
    for ddl in DDL_STATEMENTS:
        cursor = conn.cursor()
        cursor.execute(ddl)
    conn.commit()
