-- 可观测性追踪库初始化脚本
-- 在独立数据库（aid_work_logs2）中执行
-- 设计文档：docs/infrastructure/observability-design.md §二

-- TimescaleDB 扩展（需要超级用户权限，不存在时跳过）
DO $$ BEGIN
    CREATE EXTENSION IF NOT EXISTS timescaledb;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'TimescaleDB not available, using regular PG tables';
END $$;

-- ==================== obs_traces — 请求追踪 ====================

CREATE TABLE IF NOT EXISTS obs_traces (
    trace_id TEXT PRIMARY KEY,
    session_id TEXT,
    tenant_id TEXT,
    user_id TEXT,
    subagent_id TEXT,
    input TEXT,
    output TEXT,
    metadata JSONB,
    tags TEXT[],
    total_tokens INTEGER DEFAULT 0,
    total_cost NUMERIC(10,6) DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    agent_iterations INTEGER DEFAULT 0,
    tool_calls_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    error_message TEXT,
    source_type TEXT DEFAULT 'chat',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_obs_traces_tenant_time ON obs_traces(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_traces_session ON obs_traces(session_id);
CREATE INDEX IF NOT EXISTS idx_obs_traces_status ON obs_traces(status);
CREATE INDEX IF NOT EXISTS idx_obs_traces_tags ON obs_traces USING GIN(tags);

-- TimescaleDB Hypertable（开发环境可能不支持 TimescaleDB，用 DO 块容错）
DO $$
BEGIN
    IF (SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb') > 0 THEN
        PERFORM create_hypertable('obs_traces', 'created_at', chunk_time_interval => INTERVAL '1 day', migrate_data => true);
        ALTER TABLE obs_traces SET (timescaledb.compress, timescaledb.compress_segmentby = 'tenant_id');
        PERFORM add_compression_policy('obs_traces', INTERVAL '7 days');
        PERFORM add_retention_policy('obs_traces', INTERVAL '90 days');
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'TimescaleDB setup skipped for obs_traces: %', SQLERRM;
END $$;

-- ==================== obs_spans — 追踪步骤 ====================

CREATE TABLE IF NOT EXISTS obs_spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    span_type TEXT NOT NULL,
    name TEXT NOT NULL,
    input TEXT,
    output TEXT,
    metadata JSONB,
    model TEXT,
    model_params JSONB,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    cost NUMERIC(10,6) DEFAULT 0,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    duration_ms INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_obs_spans_trace ON obs_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_obs_spans_parent ON obs_spans(parent_span_id);
CREATE INDEX IF NOT EXISTS idx_obs_spans_type_name ON obs_spans(span_type, name);
CREATE INDEX IF NOT EXISTS idx_obs_spans_time ON obs_spans(start_time DESC);

DO $$
BEGIN
    IF (SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb') > 0 THEN
        PERFORM create_hypertable('obs_spans', 'created_at', chunk_time_interval => INTERVAL '1 day', migrate_data => true);
        ALTER TABLE obs_spans SET (timescaledb.compress, timescaledb.compress_segmentby = 'trace_id');
        PERFORM add_compression_policy('obs_spans', INTERVAL '7 days');
        PERFORM add_retention_policy('obs_spans', INTERVAL '90 days');
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'TimescaleDB setup skipped for obs_spans: %', SQLERRM;
END $$;

-- ==================== obs_scores — 评估分数 ====================

CREATE TABLE IF NOT EXISTS obs_scores (
    id SERIAL PRIMARY KEY,
    trace_id TEXT,
    span_id TEXT,
    score_name TEXT NOT NULL,
    score_value NUMERIC,
    score_source TEXT NOT NULL,
    reasoning TEXT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_obs_scores_trace ON obs_scores(trace_id);
CREATE INDEX IF NOT EXISTS idx_obs_scores_name ON obs_scores(score_name);
CREATE INDEX IF NOT EXISTS idx_obs_scores_source ON obs_scores(score_source);

DO $$
BEGIN
    IF (SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb') > 0 THEN
        PERFORM create_hypertable('obs_scores', 'created_at', chunk_time_interval => INTERVAL '7 days', migrate_data => true);
        ALTER TABLE obs_scores SET (timescaledb.compress, timescaledb.compress_segmentby = 'score_name');
        PERFORM add_compression_policy('obs_scores', INTERVAL '30 days');
        PERFORM add_retention_policy('obs_scores', INTERVAL '180 days');
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'TimescaleDB setup skipped for obs_scores: %', SQLERRM;
END $$;
