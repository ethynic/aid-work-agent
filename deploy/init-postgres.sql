-- PostgreSQL 数据库初始化脚本
-- 用于 AID Work Agent 数据库初始化
-- 包含核心业务表和 SaaS 多租户表
-- 单实例方案：同时创建生产库和测试库，通过数据库和用户隔离

-- 启用 pgvector 扩展（用于向量搜索）
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============== 创建测试环境数据库和用户 ==============
-- 测试用户（只能访问测试库，防止误操作生产库）
CREATE USER aid_user2 WITH PASSWORD 'Aid_2026';

-- 创建测试数据库
CREATE DATABASE aid_work_agent2 OWNER aid_user2;

-- 在测试库中启用扩展
\c aid_work_agent2;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
\c aid_work_agent;

-- 为生产用户设置 statement_timeout 防护（防慢查询拖垮生产）
ALTER USER aid_user2 SET statement_timeout = '30000';  -- 测试用户查询超时30秒

-- ============== 核心业务表 ==============

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    user_id TEXT UNIQUE NOT NULL,
    username TEXT,
    phone TEXT,
    password_hash TEXT,
    wx_openid TEXT ,
    wx_unionid TEXT,
    avatar_url TEXT,
    nickname TEXT,
    tenant_id TEXT,
    role TEXT DEFAULT 'user',
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source TEXT
);

-- 租户内手机号索引（非唯一，支持跨渠道用户绑定同一手机号）
CREATE INDEX IF NOT EXISTS idx_users_tenant_phone ON users (tenant_id, phone) WHERE phone IS NOT NULL AND tenant_id IS NOT NULL;

-- 会话表
CREATE TABLE IF NOT EXISTS chat_sessions (
    id SERIAL PRIMARY KEY,
    session_id TEXT UNIQUE NOT NULL,
    user_id TEXT,
    tenant_id TEXT,
    subagent_id TEXT,
    instance_id TEXT,                        -- 关联的数字员工实例ID
    title TEXT,
    context_data TEXT,
    metadata JSONB,                          -- 会话级元数据（如 video_gen_params：创作模式/时长/比例/分辨率/生成条数）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_instance ON chat_sessions(instance_id, created_at DESC);

-- 消息表 - 存储用户和AI之间的每一条消息，用于前端展示聊天历史和构建对话上下文
-- 与 chat_records 表的区别：
-- 1. 存储粒度：单条消息（最小单元） vs 完整对话交互（用户输入+助手回复）
-- 2. 使用场景：消息展示和上下文构建 vs 用量统计、计费、审计、性能监控
-- 3. 数据结构：简单的 role/content/metadata vs 包含token统计、执行详情、状态等完整信息
-- 两个表存在内容冗余但设计合理，服务于不同的业务目的
CREATE TABLE IF NOT EXISTS chat_messages (
    id SERIAL PRIMARY KEY,
    message_id TEXT UNIQUE NOT NULL,
    session_id TEXT,
    role TEXT,
    content TEXT,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 会话记录表（每次和AI的对话）- 存储每次完整对话的处理记录，用于用量统计、计费、审计和性能监控
-- 与 chat_messages 表的区别：
-- 1. 存储粒度：完整对话交互（用户输入+助手回复） vs 单条消息（最小单元）
-- 2. 使用场景：用量统计、计费、审计、性能监控 vs 消息展示和上下文构建
-- 3. 数据结构：包含token统计、执行详情、状态等完整信息 vs 简单的 role/content/metadata
-- 两个表存在内容冗余但设计合理，服务于不同的业务目的
CREATE TABLE IF NOT EXISTS chat_records (
    id SERIAL PRIMARY KEY,
    record_id TEXT UNIQUE NOT NULL,
    session_id TEXT,
    tenant_id TEXT,
    user_id TEXT,
    user_message TEXT,
    assistant_message TEXT,
    total_token_count INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cached_input_tokens INTEGER DEFAULT 0,
    model TEXT,
    provider TEXT,
    execution_details TEXT,
    agent_iterations INTEGER DEFAULT 0,
    subagent_calls TEXT,
    status TEXT DEFAULT 'completed',
    error_message TEXT,
    duration_ms INTEGER DEFAULT 0,
    source_type TEXT DEFAULT 'chat',
    credit_cost NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_chat_records_session ON chat_records(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_records_source_type ON chat_records(source_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_records_user ON chat_records(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_records_tenant_time ON chat_records(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_records_tenant_model ON chat_records(tenant_id, model, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_tenant_user ON chat_sessions(tenant_id, user_id, updated_at DESC);

-- 渠道会话表（企业微信/钉钉/飞书等第三方渠道的会话和消息）
CREATE TABLE IF NOT EXISTS channel_sessions (
    id SERIAL PRIMARY KEY,
    session_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    channel_type TEXT NOT NULL,
    channel_user_id TEXT NOT NULL,
    subagent_id TEXT,
    channel_chat_id TEXT,
    user_id TEXT,
    username TEXT,
    title TEXT,
    context_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_message_at TIMESTAMP,
    metadata JSONB
);

CREATE TABLE IF NOT EXISTS channel_messages (
    id SERIAL PRIMARY KEY,
    message_id TEXT UNIQUE NOT NULL,
    session_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    message_type TEXT DEFAULT 'text',
    attachments TEXT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_recalled BOOLEAN NOT NULL DEFAULT FALSE,
    recalled_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_channel_sessions_tenant_channel ON channel_sessions(tenant_id, channel_type, channel_user_id);
CREATE INDEX IF NOT EXISTS idx_channel_messages_session ON channel_messages(session_id, created_at);
-- v3.2.1 P2-1：chat_messages 表同样需要 (session_id, created_at) 索引，
-- 供 MessageDB.count_messages_by_session（压缩阈值快路径）走索引扫描
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created ON chat_messages(session_id, created_at DESC);

-- 会话内上下文压缩摘要表（mid-term memory）
-- 每次压缩产生一行，旧的 summary 置为 superseded，永不删除
CREATE TABLE IF NOT EXISTS chat_context_summaries (
    id SERIAL PRIMARY KEY,
    summary_id TEXT UNIQUE NOT NULL,          -- csum_xxxxxxxx 格式
    session_id TEXT NOT NULL,                 -- chat_sessions.session_id 或 channel_session.session_id
    source_type TEXT NOT NULL,                -- 'chat' / 'wecom_kf' / 'dingtalk' / 'feishu' / 'wecom_personal_rpa'
    tenant_id TEXT,                           -- 租户隔离
    user_id TEXT,
    subagent_id TEXT,                         -- 关联的子智能体（NULL 表示主智能体）

    -- 摘要内容
    summary_text TEXT NOT NULL,               -- 完整结构化摘要文本
    summary_version INTEGER NOT NULL DEFAULT 1, -- 该 session 第几次压缩（递增）

    -- 压缩元数据
    compressed_message_ids BIGINT[] NOT NULL, -- 被压缩的 chat_messages.id 列表（可追溯）
    compressed_message_count INTEGER NOT NULL,
    original_token_count INTEGER NOT NULL,
    compressed_token_count INTEGER NOT NULL,  -- 摘要 + TAIL 的 token 数
    compression_ratio REAL NOT NULL,          -- compressed / original

    -- 模型与调用信息
    llm_provider TEXT,                        -- 'qwen' / 'zhipu' / 'deepseek'
    llm_model TEXT,
    llm_tokens_used INTEGER,                  -- 摘要 LLM 调用消耗

    -- 降级标记
    fallback_used BOOLEAN DEFAULT FALSE,      -- 是否走了同步降级路径

    -- 状态
    status TEXT NOT NULL DEFAULT 'active',    -- 'active' / 'superseded' / 'rolled_back'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    superseded_at TIMESTAMP
);

-- 同一 session 同一 source_type 同时只能有一条 active summary（部分唯一索引）
-- UNIQUE 保证并发场景下也不会出现两条 active；使用 COALESCE(NULL) 模式无意义，
-- 这里直接对 (session_id, source_type) 加唯一约束 + WHERE status='active' 过滤即可。
CREATE UNIQUE INDEX IF NOT EXISTS idx_ccs_session_active
    ON chat_context_summaries (session_id, source_type)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_ccs_session_list
    ON chat_context_summaries (session_id, source_type, created_at DESC);
-- v3.2.1 P1-1：管理后台列表按 tenant_id + created_at DESC 排序查询，需要此索引
CREATE INDEX IF NOT EXISTS idx_ccs_tenant_time
    ON chat_context_summaries (tenant_id, created_at DESC);

-- chat_messages / channel_messages 加 compacted 标记（CREATE TABLE IF NOT EXISTS 不会更新已存在的表，
-- 用 ALTER 兜底确保新部署也带上字段）
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS compacted BOOLEAN DEFAULT FALSE;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS compacted_by TEXT;
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS compacted BOOLEAN DEFAULT FALSE;
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS compacted_by TEXT;

-- v3.1: session 级上下文 token 缓存（Agent 主循环每次 LLM 调用后写入最后一次 prompt+completion tokens）
-- 压缩服务 _should_compress 优先读此字段，避免每次全量 count_tokens
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS context_token_count INTEGER DEFAULT 0;
ALTER TABLE channel_sessions ADD COLUMN IF NOT EXISTS context_token_count INTEGER DEFAULT 0;

-- 错误日志表（平台级，记录系统错误，仅平台管理员可见）
CREATE TABLE IF NOT EXISTS log_error (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    module TEXT,           -- 错误来源模块（如 agent.py:process_message:1234）
    error_type TEXT,       -- 异常类型（如 ValueError、HTTPException）
    message TEXT NOT NULL, -- 错误消息
    traceback TEXT,        -- 完整堆栈
    status TEXT DEFAULT 'unprocessed',  -- unprocessed / processed / ignored
    processed_by TEXT,    -- 处理人
    processed_at TIMESTAMP -- 处理时间
);

CREATE INDEX IF NOT EXISTS idx_log_error_timestamp ON log_error(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_log_error_status ON log_error(status);

-- Token成本价表
CREATE TABLE IF NOT EXISTS token_cost_prices (
    id SERIAL PRIMARY KEY,
    model_name TEXT UNIQUE NOT NULL,
    input_price_per_m NUMERIC(10,4),
    cached_input_price_per_m NUMERIC(10,4), -- 命中缓存输入单价
    output_price_per_m NUMERIC(10,4),
    price_per_second NUMERIC(10,4), -- 视频模型按秒计费单价（元/秒），fallback；优先看 price_per_second_by_resolution
    price_per_second_by_resolution JSONB, -- 按分辨率区分的视频单价 {"720P": 0.6, "1080P": 1.0}，命中 resolution key 优先用
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 文本模型单价
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('qwen-plus', 0.8, 2.0, 0.16)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('deepseek-v4-flash', 1.0, 2.0, 0.02)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('deepseek-v4-pro', 3.0, 6.0, 0.025)
ON CONFLICT (model_name) DO NOTHING;

-- 视觉模型单价
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('qwen3.7-plus', 2.0, 8.0, 0.4)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('qwen-vl-max', 1.6, 4.0, 0.32)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('qwen-vl-plus', 0.8, 2.0, 0.16)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('qwen3-vl-flash',0.6, 6, 0.012)    -- 按顶格 128K<Token≤256K 计算
ON CONFLICT (model_name) DO NOTHING;

-- 视频模型按秒计费单价
-- 万相 r2v 按 resolution 区分：720P=0.6, 1080P=1.0；price_per_second 留 720P 作 fallback
INSERT INTO token_cost_prices (model_name, price_per_second, price_per_second_by_resolution)
VALUES ('wan2.7-r2v', 0.6, '{"720P": 0.6, "1080P": 1.0}'::jsonb)
ON CONFLICT (model_name) DO NOTHING;
-- MiniMax-H3 主生成按 resolution 区分：768P=0.5, 2K=0.8；price_per_second 留 768P 作 fallback
INSERT INTO token_cost_prices (model_name, price_per_second, price_per_second_by_resolution)
VALUES ('MiniMax-H3', 0.5, '{"768P": 0.5, "2K": 0.8}'::jsonb)
ON CONFLICT (model_name) DO NOTHING;


-- 验证码表
CREATE TABLE IF NOT EXISTS sms_codes (
    id SERIAL PRIMARY KEY,
    phone TEXT,
    code TEXT,
    used INTEGER DEFAULT 0,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 远程连接凭据表 (SMB/FTP)
CREATE TABLE IF NOT EXISTS remote_credentials (
    id SERIAL PRIMARY KEY,
    credential_id TEXT UNIQUE NOT NULL,
    user_id TEXT,
    connection_type TEXT,
    server_host TEXT,
    server_port INTEGER,
    username TEXT,
    password TEXT,
    remote_path TEXT,
    domain TEXT,
    name TEXT,
    description TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 远程凭据索引
CREATE INDEX IF NOT EXISTS idx_remote_credentials_user ON remote_credentials(user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_remote_credentials_path ON remote_credentials(user_id, remote_path, status);

-- Token 表（用于多进程共享 session）
CREATE TABLE IF NOT EXISTS tokens (
    id SERIAL PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    user_id TEXT,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Token 索引
CREATE INDEX IF NOT EXISTS idx_tokens_token ON tokens(token);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id, expires_at);

-- 定时任务表
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id SERIAL PRIMARY KEY,
    task_id TEXT UNIQUE NOT NULL,
    user_id TEXT,
    name TEXT,
    description TEXT,
    task_prompt TEXT,
    schedule_type TEXT,
    cron_expression TEXT,
    interval_seconds INTEGER,
    session_id TEXT,
    status TEXT DEFAULT 'active',
    max_retries INTEGER DEFAULT 3,
    retry_count INTEGER DEFAULT 0,
    last_run_at TIMESTAMP,
    next_run_at TIMESTAMP,
    total_runs INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    -- 手动触发标记：API/工具写入 NOW()，background reconcile 扫到后立即执行一次并清空
    manual_trigger_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_user ON scheduled_tasks(user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run ON scheduled_tasks(next_run_at, status);

-- 定时任务执行日志表
CREATE TABLE IF NOT EXISTS scheduled_task_logs (
    id SERIAL PRIMARY KEY,
    log_id TEXT UNIQUE NOT NULL,
    task_id TEXT,
    user_id TEXT,
    session_id TEXT,
    status TEXT,
    trigger_type TEXT,
    result_summary TEXT,
    result_detail TEXT,
    error_message TEXT,
    error_trace TEXT,
    duration_ms INTEGER DEFAULT 0,
    token_usage INTEGER DEFAULT 0,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scheduled_task_logs_task ON scheduled_task_logs(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scheduled_task_logs_user ON scheduled_task_logs(user_id, created_at DESC);

-- ============== 知识库表 ==============

-- 知识库分类表
CREATE TABLE IF NOT EXISTS knowledge_categories (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    display_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uuid TEXT UNIQUE,
    UNIQUE(tenant_id, source_type)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_categories_tenant ON knowledge_categories(tenant_id);

-- 文档表
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    user_id TEXT,
    tenant_id TEXT,
    title TEXT,
    source_type TEXT,
    file_type TEXT,
    file_path TEXT,
    file_size INTEGER,
    total_chunks INTEGER,
    embedding_model TEXT,
    thumbnail_path TEXT,
    duration INTEGER,
    width INTEGER,
    height INTEGER,
    mime_type TEXT,
    raw_text TEXT,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    summary TEXT,
    uuid TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents(tenant_id);

-- 文本块表
CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    doc_id INTEGER,
    chunk_index INTEGER,
    text TEXT,
    tokens INTEGER,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uuid TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

-- 向量表（使用 pgvector）
CREATE TABLE IF NOT EXISTS chunks_vec (
    chunk_id INTEGER PRIMARY KEY,
    embedding vector(1024)
);

-- 创建向量索引（使用 HNSW 算法，支持余弦相似度搜索）
CREATE INDEX IF NOT EXISTS idx_chunks_vec_cosine ON chunks_vec USING hnsw (embedding vector_cosine_ops);

-- FTS5 全文搜索表（PostgreSQL 使用 tsvector）
CREATE TABLE IF NOT EXISTS chunks_fts (
    chunk_id INTEGER PRIMARY KEY,
    text TEXT,
    fts_vector tsvector
);

-- 创建 GIN 索引用于全文搜索
CREATE INDEX IF NOT EXISTS idx_chunks_fts_fts ON chunks_fts USING gin (fts_vector);

-- 用户邮箱配置表
CREATE TABLE IF NOT EXISTS user_email_settings (
    id SERIAL PRIMARY KEY,
    user_id TEXT UNIQUE NOT NULL,
    email_address TEXT,
    smtp_server TEXT,
    smtp_port INTEGER,
    smtp_user TEXT,
    smtp_password TEXT,
    smtp_encryption TEXT DEFAULT 'ssl',
    imap_server TEXT,
    imap_port INTEGER,
    imap_encryption TEXT DEFAULT 'ssl',
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_email_settings_user ON user_email_settings(user_id);

-- 数据连接器表（数据分析智能体）
CREATE TABLE IF NOT EXISTS data_connectors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT,
    name TEXT NOT NULL,
    db_type TEXT NOT NULL,
    host TEXT,
    port INTEGER,
    database_name TEXT NOT NULL,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,
    options JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    imported_tables JSONB DEFAULT '[]',
    last_sync_at TIMESTAMP,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_data_connectors_tenant ON data_connectors(tenant_id);

-- ============== SaaS 多租户表 ==============

-- 租户表
CREATE TABLE IF NOT EXISTS tenants (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT UNIQUE NOT NULL,
    company_name TEXT,
    contact_name TEXT,
    contact_phone TEXT,
    initial_admin_name TEXT,
    initial_admin_phone TEXT,
    status TEXT DEFAULT 'active',
    plan TEXT DEFAULT 'basic',
    max_instances INTEGER DEFAULT 5,
    max_users INTEGER DEFAULT 50,
    settings TEXT,
    expire_at TIMESTAMP,
    tenant_code TEXT,
    credit_balance NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    logo_file_id TEXT,  -- 租户 Logo 文件 ID（对应 uploaded_file:{file_id}）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON COLUMN tenants.expire_at IS '到期日期（时分秒为 23:59:59，当天仍可登录，空表示永久有效）';
COMMENT ON COLUMN tenants.credit_balance IS '积分余额（2 位小数），允许透支为负，对话中扣完不中断、下一轮入口拦截';

CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);
CREATE INDEX IF NOT EXISTS idx_tenants_expire_at ON tenants(expire_at);
CREATE INDEX IF NOT EXISTS idx_tenants_tenant_code ON tenants(tenant_code);

-- 订阅表
CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    subscription_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT,
    subagent_type TEXT,
    instance_quota INTEGER DEFAULT 1,      -- 实例并发配额
    billing_cycle TEXT DEFAULT 'monthly',
    unit_price REAL DEFAULT 0,
    token_quota INTEGER DEFAULT -1,
    tokens_used INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    payment_status TEXT DEFAULT 'pending',
    starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 生效时间
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant ON subscriptions(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_time_range ON subscriptions(tenant_id, subagent_type, starts_at, expires_at, status);

-- 回复风格表
CREATE TABLE IF NOT EXISTS reply_styles (
    id SERIAL PRIMARY KEY,
    style_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(style_id, tenant_id, version)
);

CREATE INDEX IF NOT EXISTS idx_reply_styles_tenant_active ON reply_styles(tenant_id, is_active);

-- 租户渠道配置表
CREATE TABLE IF NOT EXISTS tenant_channel_configs (
    id SERIAL PRIMARY KEY,
    config_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    channel_type TEXT,
    name TEXT,
    config TEXT,
    verified INTEGER DEFAULT 0,
    subagent_type TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tenant_channel_configs_tenant ON tenant_channel_configs(tenant_id, channel_type);

-- wecom_personal_rpa 渠道单例约束：同 tenant 只能有一份该类型配置
-- （切换 server/client 模式 = 编辑现有配置，不允许创建第二条）
CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_channel_configs_wecom_personal_rpa
    ON tenant_channel_configs(tenant_id, channel_type)
    WHERE channel_type = 'wecom_personal_rpa';

-- 支付订单表
CREATE TABLE IF NOT EXISTS payment_orders (
    id SERIAL PRIMARY KEY,
    order_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    subscription_id TEXT,
    amount REAL,
    payment_method TEXT,
    payment_status TEXT DEFAULT 'pending',
    paid_at TIMESTAMP,
    transaction_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_payment_orders_tenant ON payment_orders(tenant_id, payment_status);

-- 用户级数字员工授权表
CREATE TABLE IF NOT EXISTS user_agent_permissions (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_agent_permissions_user ON user_agent_permissions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_agent_permissions_tenant_user ON user_agent_permissions(tenant_id, user_id);

-- 子智能体环境变量表（通用，按租户+子智能体隔离）
CREATE TABLE IF NOT EXISTS subagent_env_vars (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    subagent_name TEXT NOT NULL,
    var_name TEXT NOT NULL,
    var_value TEXT,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_subagent_env_unique ON subagent_env_vars(tenant_id, subagent_name, var_name);
CREATE INDEX IF NOT EXISTS idx_subagent_env_tenant ON subagent_env_vars(tenant_id, subagent_name);

-- 租户级子智能体知识库关联表（Phase 3.2）
CREATE TABLE IF NOT EXISTS subagent_knowledge_sources (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    subagent_name TEXT NOT NULL,
    sources JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, subagent_name)
);

-- ============== Prompt 版本管理表 ==============

-- prompt_registry — Prompt 注册表
CREATE TABLE IF NOT EXISTS prompt_registry (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     TEXT,
    scope         TEXT NOT NULL,
    scope_id      TEXT NOT NULL,
    prompt_type   TEXT DEFAULT 'normal',
    display_name  TEXT,
    description   TEXT,
    latest_version INTEGER DEFAULT 0,
    created_by    TEXT,
    updated_by    TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_registry_tenant_scope
    ON prompt_registry (COALESCE(tenant_id, ''), scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_prompt_registry_scope
    ON prompt_registry (scope, scope_id);

-- prompt_versions — Prompt 版本（不可变）
CREATE TABLE IF NOT EXISTS prompt_versions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id      UUID NOT NULL,
    version        INTEGER NOT NULL,
    content        TEXT NOT NULL,
    variables      JSONB,
    model_config   JSONB,
    commit_message TEXT,
    content_hash   TEXT,
    parent_version INTEGER,
    created_by     TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_versions_prompt_ver
    ON prompt_versions (prompt_id, version);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_prompt
    ON prompt_versions (prompt_id, created_at DESC);

-- prompt_labels — 标签（命名指针）
CREATE TABLE IF NOT EXISTS prompt_labels (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id     UUID NOT NULL,
    version_id    UUID NOT NULL,
    label         TEXT NOT NULL,
    created_by    TEXT,
    updated_by    TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_labels_prompt_label
    ON prompt_labels (prompt_id, label);

-- prompt_drafts — 草稿（每 Prompt 最多一条）
CREATE TABLE IF NOT EXISTS prompt_drafts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id     UUID NOT NULL UNIQUE,
    content       TEXT NOT NULL,
    variables     JSONB,
    base_version  INTEGER,
    updated_by    TEXT,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- subagent_prompt_sections — System Prompt 分段管理（Phase 3.7）
CREATE TABLE IF NOT EXISTS subagent_prompt_sections (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    TEXT NOT NULL,
    section_key TEXT NOT NULL,
    content     TEXT DEFAULT '',
    updated_by  TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(agent_id, section_key)
);
CREATE INDEX IF NOT EXISTS idx_prompt_sections_agent ON subagent_prompt_sections(agent_id);


-- 输出初始化完成信息
DO $$
BEGIN
    RAISE NOTICE 'PostgreSQL database initialized successfully with pgvector extension';
    RAISE NOTICE 'Production database: aid_work_agent (user: aid_user)';
    RAISE NOTICE 'Test database: aid_work_agent2 (user: aid_user2)';
END $$;

-- ============== 在测试库中创建相同的表结构 ==============
\c aid_work_agent2;

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    user_id TEXT UNIQUE NOT NULL,
    username TEXT,
    phone TEXT,
    password_hash TEXT,
    wx_openid TEXT,
    wx_unionid TEXT,
    avatar_url TEXT,
    nickname TEXT,
    tenant_id TEXT,
    role TEXT DEFAULT 'user',
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source TEXT
);

-- 租户内手机号索引（非唯一，支持跨渠道用户绑定同一手机号）
CREATE INDEX IF NOT EXISTS idx_users_tenant_phone ON users (tenant_id, phone) WHERE phone IS NOT NULL AND tenant_id IS NOT NULL;

-- 会话表
CREATE TABLE IF NOT EXISTS chat_sessions (
    id SERIAL PRIMARY KEY,
    session_id TEXT UNIQUE NOT NULL,
    user_id TEXT,
    tenant_id TEXT,
    subagent_id TEXT,
    instance_id TEXT,                        -- 关联的数字员工实例ID
    title TEXT,
    context_data TEXT,
    metadata JSONB,                          -- 会话级元数据（如 video_gen_params：创作模式/时长/比例/分辨率/生成条数）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_instance ON chat_sessions(instance_id, created_at DESC);

-- 消息表 - 存储用户和AI之间的每一条消息，用于前端展示聊天历史和构建对话上下文
-- 与 chat_records 表的区别：
-- 1. 存储粒度：单条消息（最小单元） vs 完整对话交互（用户输入+助手回复）
-- 2. 使用场景：消息展示和上下文构建 vs 用量统计、计费、审计、性能监控
-- 3. 数据结构：简单的 role/content/metadata vs 包含token统计、执行详情、状态等完整信息
-- 两个表存在内容冗余但设计合理，服务于不同的业务目的
CREATE TABLE IF NOT EXISTS chat_messages (
    id SERIAL PRIMARY KEY,
    message_id TEXT UNIQUE NOT NULL,
    session_id TEXT,
    role TEXT,
    content TEXT,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 会话记录表 - 存储每次完整对话的处理记录，用于用量统计、计费、审计和性能监控
-- 与 chat_messages 表的区别：
-- 1. 存储粒度：完整对话交互（用户输入+助手回复） vs 单条消息（最小单元）
-- 2. 使用场景：用量统计、计费、审计、性能监控 vs 消息展示和上下文构建
-- 3. 数据结构：包含token统计、执行详情、状态等完整信息 vs 简单的 role/content/metadata
-- 两个表存在内容冗余但设计合理，服务于不同的业务目的
CREATE TABLE IF NOT EXISTS chat_records (
    id SERIAL PRIMARY KEY,
    record_id TEXT UNIQUE NOT NULL,
    session_id TEXT,
    tenant_id TEXT,
    user_id TEXT,
    user_message TEXT,
    assistant_message TEXT,
    total_token_count INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cached_input_tokens INTEGER DEFAULT 0,
    model TEXT,
    provider TEXT,
    execution_details TEXT,
    agent_iterations INTEGER DEFAULT 0,
    subagent_calls TEXT,
    status TEXT DEFAULT 'completed',
    error_message TEXT,
    duration_ms INTEGER DEFAULT 0,
    credit_cost NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_records_session ON chat_records(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_records_user ON chat_records(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_records_tenant_time ON chat_records(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_records_tenant_model ON chat_records(tenant_id, model, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_tenant_user ON chat_sessions(tenant_id, user_id, updated_at DESC);

-- 验证码表
CREATE TABLE IF NOT EXISTS sms_codes (
    id SERIAL PRIMARY KEY,
    phone TEXT,
    code TEXT,
    used INTEGER DEFAULT 0,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 远程连接凭据表
CREATE TABLE IF NOT EXISTS remote_credentials (
    id SERIAL PRIMARY KEY,
    credential_id TEXT UNIQUE NOT NULL,
    user_id TEXT,
    connection_type TEXT,
    server_host TEXT,
    server_port INTEGER,
    username TEXT,
    password TEXT,
    remote_path TEXT,
    domain TEXT,
    name TEXT,
    description TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_remote_credentials_user ON remote_credentials(user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_remote_credentials_path ON remote_credentials(user_id, remote_path, status);

-- Token 表
CREATE TABLE IF NOT EXISTS tokens (
    id SERIAL PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    user_id TEXT,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tokens_token ON tokens(token);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id, expires_at);

-- 定时任务表
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id SERIAL PRIMARY KEY,
    task_id TEXT UNIQUE NOT NULL,
    user_id TEXT,
    name TEXT,
    description TEXT,
    task_prompt TEXT,
    schedule_type TEXT,
    cron_expression TEXT,
    interval_seconds INTEGER,
    session_id TEXT,
    status TEXT DEFAULT 'active',
    max_retries INTEGER DEFAULT 3,
    retry_count INTEGER DEFAULT 0,
    last_run_at TIMESTAMP,
    next_run_at TIMESTAMP,
    total_runs INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    -- 手动触发标记：API/工具写入 NOW()，background reconcile 扫到后立即执行一次并清空
    manual_trigger_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_user ON scheduled_tasks(user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run ON scheduled_tasks(next_run_at, status);

-- 定时任务执行日志表
CREATE TABLE IF NOT EXISTS scheduled_task_logs (
    id SERIAL PRIMARY KEY,
    log_id TEXT UNIQUE NOT NULL,
    task_id TEXT,
    user_id TEXT,
    session_id TEXT,
    status TEXT,
    trigger_type TEXT,
    result_summary TEXT,
    result_detail TEXT,
    error_message TEXT,
    error_trace TEXT,
    duration_ms INTEGER DEFAULT 0,
    token_usage INTEGER DEFAULT 0,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scheduled_task_logs_task ON scheduled_task_logs(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scheduled_task_logs_user ON scheduled_task_logs(user_id, created_at DESC);

-- 错误日志表（测试库）
CREATE TABLE IF NOT EXISTS log_error (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    module TEXT,
    error_type TEXT,
    message TEXT NOT NULL,
    traceback TEXT,
    status TEXT DEFAULT 'unprocessed',
    processed_by TEXT,
    processed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_log_error_timestamp ON log_error(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_log_error_status ON log_error(status);

-- 向量表
CREATE TABLE IF NOT EXISTS chunks_vec (
    chunk_id INTEGER PRIMARY KEY,
    embedding vector(1024)
);

CREATE INDEX IF NOT EXISTS idx_chunks_vec_cosine ON chunks_vec USING hnsw (embedding vector_cosine_ops);

-- 全文搜索表
CREATE TABLE IF NOT EXISTS chunks_fts (
    chunk_id INTEGER PRIMARY KEY,
    text TEXT,
    fts_vector tsvector
);

CREATE INDEX IF NOT EXISTS idx_chunks_fts_fts ON chunks_fts USING gin (fts_vector);

-- 用户邮箱配置表
CREATE TABLE IF NOT EXISTS user_email_settings (
    id SERIAL PRIMARY KEY,
    user_id TEXT UNIQUE NOT NULL,
    email_address TEXT,
    smtp_server TEXT,
    smtp_port INTEGER,
    smtp_user TEXT,
    smtp_password TEXT,
    smtp_encryption TEXT DEFAULT 'ssl',
    imap_server TEXT,
    imap_port INTEGER,
    imap_encryption TEXT DEFAULT 'ssl',
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_email_settings_user ON user_email_settings(user_id);

-- 数据连接器表（数据分析智能体）
CREATE TABLE IF NOT EXISTS data_connectors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT,
    name TEXT NOT NULL,
    db_type TEXT NOT NULL,
    host TEXT,
    port INTEGER,
    database_name TEXT NOT NULL,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,
    options JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    imported_tables JSONB DEFAULT '[]',
    last_sync_at TIMESTAMP,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_data_connectors_tenant ON data_connectors(tenant_id);

-- 租户表
CREATE TABLE IF NOT EXISTS tenants (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT UNIQUE NOT NULL,
    company_name TEXT,
    contact_name TEXT,
    contact_phone TEXT,
    initial_admin_name TEXT,
    initial_admin_phone TEXT,
    status TEXT DEFAULT 'active',
    plan TEXT DEFAULT 'basic',
    max_instances INTEGER DEFAULT 5,
    max_users INTEGER DEFAULT 50,
    settings TEXT,
    expire_at TIMESTAMP,
    tenant_code TEXT,
    credit_balance NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    logo_file_id TEXT,  -- 租户 Logo 文件 ID（对应 uploaded_file:{file_id}）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON COLUMN tenants.expire_at IS '到期日期（时分秒为 23:59:59，当天仍可登录，空表示永久有效）';
COMMENT ON COLUMN tenants.credit_balance IS '积分余额（2 位小数），允许透支为负，对话中扣完不中断、下一轮入口拦截';

CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);
CREATE INDEX IF NOT EXISTS idx_tenants_expire_at ON tenants(expire_at);
CREATE INDEX IF NOT EXISTS idx_tenants_tenant_code ON tenants(tenant_code);

-- 订阅表
CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    subscription_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT,
    subagent_type TEXT,
    instance_quota INTEGER DEFAULT 1,      -- 实例并发配额
    billing_cycle TEXT DEFAULT 'monthly',
    unit_price REAL DEFAULT 0,
    token_quota INTEGER DEFAULT -1,
    tokens_used INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    payment_status TEXT DEFAULT 'pending',
    starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 生效时间
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant ON subscriptions(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_time_range ON subscriptions(tenant_id, subagent_type, starts_at, expires_at, status);

-- 回复风格表
CREATE TABLE IF NOT EXISTS reply_styles (
    id SERIAL PRIMARY KEY,
    style_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(style_id, tenant_id, version)
);

CREATE INDEX IF NOT EXISTS idx_reply_styles_tenant_active ON reply_styles(tenant_id, is_active);

-- 租户渠道配置表
CREATE TABLE IF NOT EXISTS tenant_channel_configs (
    id SERIAL PRIMARY KEY,
    config_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    channel_type TEXT,
    name TEXT,
    config TEXT,
    verified INTEGER DEFAULT 0,
    subagent_type TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tenant_channel_configs_tenant ON tenant_channel_configs(tenant_id, channel_type);

-- wecom_personal_rpa 渠道单例约束：同 tenant 只能有一份该类型配置
-- （切换 server/client 模式 = 编辑现有配置，不允许创建第二条）
CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_channel_configs_wecom_personal_rpa
    ON tenant_channel_configs(tenant_id, channel_type)
    WHERE channel_type = 'wecom_personal_rpa';

-- 支付订单表
CREATE TABLE IF NOT EXISTS payment_orders (
    id SERIAL PRIMARY KEY,
    order_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    subscription_id TEXT,
    amount REAL,
    payment_method TEXT,
    payment_status TEXT DEFAULT 'pending',
    paid_at TIMESTAMP,
    transaction_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_payment_orders_tenant ON payment_orders(tenant_id, payment_status);

-- 用户级数字员工授权表
CREATE TABLE IF NOT EXISTS user_agent_permissions (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_agent_permissions_user ON user_agent_permissions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_agent_permissions_tenant_user ON user_agent_permissions(tenant_id, user_id);

-- 子智能体环境变量表（通用，按租户+子智能体隔离）
CREATE TABLE IF NOT EXISTS subagent_env_vars (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    subagent_name TEXT NOT NULL,
    var_name TEXT NOT NULL,
    var_value TEXT,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_subagent_env_unique ON subagent_env_vars(tenant_id, subagent_name, var_name);
CREATE INDEX IF NOT EXISTS idx_subagent_env_tenant ON subagent_env_vars(tenant_id, subagent_name);


-- ============================================================
-- 旅游报价定价数据表（9张）
-- ============================================================

-- 车型与包车价格
CREATE TABLE IF NOT EXISTS bs_travel_quote_vehicles (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    region_name TEXT,
    vehicle_type TEXT NOT NULL,
    vehicle_type_label TEXT,
    seats_max INT NOT NULL,
    daily_rate DECIMAL(10,2),
    pricing_mode TEXT DEFAULT 'per_km',
    per_km_rate DECIMAL(10,2),
    driver_meal_allowance DECIMAL(10,2),
    driver_accommodation DECIMAL(10,2),
    effective_from DATE,
    effective_to DATE,
    is_active BOOLEAN DEFAULT TRUE,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uuid TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_travel_vehicles_tenant ON bs_travel_quote_vehicles(tenant_id, is_active);

-- 景点门票已迁移至向量知识库（source_type='attraction_resource'），旧表已删除

-- 酒店住宿已迁移至向量知识库（source_type='hotel_resource'），旧表已删除

-- 餐标价格
CREATE TABLE IF NOT EXISTS bs_travel_quote_meals (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    region_name TEXT,
    meal_tier TEXT NOT NULL,
    meal_tier_label TEXT NOT NULL,
    meal_type TEXT NOT NULL,
    meal_type_label TEXT NOT NULL,
    price_per_person DECIMAL(10,2) NOT NULL,
    pax_per_table INT DEFAULT 10,
    dishes_standard TEXT,
    season_type TEXT DEFAULT 'default',
    effective_from DATE,
    effective_to DATE,
    is_active BOOLEAN DEFAULT TRUE,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uuid TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_travel_meals_tenant ON bs_travel_quote_meals(tenant_id, is_active);

-- 导游费用
CREATE TABLE IF NOT EXISTS bs_travel_quote_guides (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    region_name TEXT,
    guide_type TEXT NOT NULL,
    guide_type_label TEXT NOT NULL,
    guide_level TEXT DEFAULT 'standard',
    guide_level_label TEXT,
    billing_method TEXT DEFAULT 'daily',
    daily_rate DECIMAL(10,2),
    trip_rate DECIMAL(10,2),
    language_premium DECIMAL(10,2) DEFAULT 0,
    peak_season_multiplier DECIMAL(3,2) DEFAULT 1.00,
    season_type TEXT DEFAULT 'default',
    is_active BOOLEAN DEFAULT TRUE,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uuid TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_travel_guides_tenant ON bs_travel_quote_guides(tenant_id, is_active);

-- 其他固定费用（保险、综合服务费等）
CREATE TABLE IF NOT EXISTS bs_travel_quote_fees (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    fee_name TEXT NOT NULL,
    fee_category TEXT NOT NULL,
    billing_method TEXT NOT NULL,
    unit_price DECIMAL(10,2) NOT NULL,
    is_mandatory BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INT DEFAULT 0,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uuid TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_travel_fees_tenant ON bs_travel_quote_fees(tenant_id, is_active);

-- 淡旺季配置
CREATE TABLE IF NOT EXISTS bs_travel_quote_seasons (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    season_type TEXT NOT NULL,
    season_type_label TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    price_multiplier DECIMAL(3,2) DEFAULT 1.00,
    is_active BOOLEAN DEFAULT TRUE,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uuid TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_travel_seasons_tenant ON bs_travel_quote_seasons(tenant_id, is_active);

-- ============== Prompt 版本管理表（测试库） ==============

CREATE TABLE IF NOT EXISTS prompt_registry (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     TEXT,
    scope         TEXT NOT NULL,
    scope_id      TEXT NOT NULL,
    prompt_type   TEXT DEFAULT 'normal',
    display_name  TEXT,
    description   TEXT,
    latest_version INTEGER DEFAULT 0,
    created_by    TEXT,
    updated_by    TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_registry_tenant_scope
    ON prompt_registry (COALESCE(tenant_id, ''), scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_prompt_registry_scope
    ON prompt_registry (scope, scope_id);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id      UUID NOT NULL,
    version        INTEGER NOT NULL,
    content        TEXT NOT NULL,
    variables      JSONB,
    model_config   JSONB,
    commit_message TEXT,
    content_hash   TEXT,
    parent_version INTEGER,
    created_by     TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_versions_prompt_ver
    ON prompt_versions (prompt_id, version);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_prompt
    ON prompt_versions (prompt_id, created_at DESC);

CREATE TABLE IF NOT EXISTS prompt_labels (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id     UUID NOT NULL,
    version_id    UUID NOT NULL,
    label         TEXT NOT NULL,
    created_by    TEXT,
    updated_by    TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_labels_prompt_label
    ON prompt_labels (prompt_id, label);

CREATE TABLE IF NOT EXISTS prompt_drafts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id     UUID NOT NULL UNIQUE,
    content       TEXT NOT NULL,
    variables     JSONB,
    base_version  INTEGER,
    updated_by    TEXT,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- subagent_prompt_sections — System Prompt 分段管理（Phase 3.7）
CREATE TABLE IF NOT EXISTS subagent_prompt_sections (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    TEXT NOT NULL,
    section_key TEXT NOT NULL,
    content     TEXT DEFAULT '',
    updated_by  TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(agent_id, section_key)
);
CREATE INDEX IF NOT EXISTS idx_prompt_sections_agent ON subagent_prompt_sections(agent_id);

-- subagent_definitions — 子智能体元数据定义（Phase 2）
CREATE TABLE IF NOT EXISTS subagent_definitions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        TEXT NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    version         TEXT DEFAULT '1.0.0',
    author          TEXT,
    triggers        JSONB DEFAULT '{}',
    tools           JSONB DEFAULT '{}',
    skills          JSONB DEFAULT '{}',
    context         JSONB DEFAULT '{}',
    delegatable_to  JSONB DEFAULT '[]',
    allow_delegation BOOLEAN DEFAULT TRUE,
    llm_provider    JSONB,
    reply_style     TEXT,
    business_pages  JSONB,
    knowledge_sources JSONB DEFAULT '[]',
    status          TEXT DEFAULT 'active',
    created_by      TEXT,
    updated_by      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_subagent_def_agent_id
    ON subagent_definitions (agent_id);
CREATE INDEX IF NOT EXISTS idx_subagent_def_status
    ON subagent_definitions (status);


-- ============================================================
-- 2026-6-10，为 8 张表加 BEFORE INSERT 触发器，新数据自动生成 UUID
-- 统一的 set_table_uuid() 函数，根据 TG_TABLE_NAME 决定前缀
-- 注意：BEFORE INSERT 触发器中 NEW.id 通常尚未生成（serial 列还未 nextval），
--       因此用 clock_timestamp() 而非 id::text 作为哈希盐，保证同一事务内也不重复
-- ============================================================
CREATE OR REPLACE FUNCTION set_table_uuid()
RETURNS TRIGGER AS $$
DECLARE
    v_prefix TEXT;
BEGIN
    IF NEW.uuid IS NULL OR NEW.uuid = '' THEN
        v_prefix := CASE TG_TABLE_NAME
            WHEN 'knowledge_categories'    THEN 'kc_'
            WHEN 'documents'                THEN 'doc_'
            WHEN 'chunks'                   THEN 'chunk_'
            WHEN 'bs_travel_quote_vehicles' THEN 'tqv_'
            WHEN 'bs_travel_quote_meals'    THEN 'tqm_'
            WHEN 'bs_travel_quote_guides'   THEN 'tqg_'
            WHEN 'bs_travel_quote_fees'     THEN 'tqf_'
            WHEN 'bs_travel_quote_seasons'  THEN 'tqs_'
            ELSE ''
        END;
        NEW.uuid := v_prefix
                  || substring(
                       md5(random()::text || clock_timestamp()::text || TG_TABLE_NAME),
                       1, 12
                     );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_set_uuid_knowledge_categories ON knowledge_categories;
CREATE TRIGGER trg_set_uuid_knowledge_categories
BEFORE INSERT ON knowledge_categories
FOR EACH ROW EXECUTE FUNCTION set_table_uuid();

DROP TRIGGER IF EXISTS trg_set_uuid_documents ON documents;
CREATE TRIGGER trg_set_uuid_documents
BEFORE INSERT ON documents
FOR EACH ROW EXECUTE FUNCTION set_table_uuid();

DROP TRIGGER IF EXISTS trg_set_uuid_chunks ON chunks;
CREATE TRIGGER trg_set_uuid_chunks
BEFORE INSERT ON chunks
FOR EACH ROW EXECUTE FUNCTION set_table_uuid();

DROP TRIGGER IF EXISTS trg_set_uuid_travel_vehicles ON bs_travel_quote_vehicles;
CREATE TRIGGER trg_set_uuid_travel_vehicles
BEFORE INSERT ON bs_travel_quote_vehicles
FOR EACH ROW EXECUTE FUNCTION set_table_uuid();

DROP TRIGGER IF EXISTS trg_set_uuid_travel_meals ON bs_travel_quote_meals;
CREATE TRIGGER trg_set_uuid_travel_meals
BEFORE INSERT ON bs_travel_quote_meals
FOR EACH ROW EXECUTE FUNCTION set_table_uuid();

DROP TRIGGER IF EXISTS trg_set_uuid_travel_guides ON bs_travel_quote_guides;
CREATE TRIGGER trg_set_uuid_travel_guides
BEFORE INSERT ON bs_travel_quote_guides
FOR EACH ROW EXECUTE FUNCTION set_table_uuid();

DROP TRIGGER IF EXISTS trg_set_uuid_travel_fees ON bs_travel_quote_fees;
CREATE TRIGGER trg_set_uuid_travel_fees
BEFORE INSERT ON bs_travel_quote_fees
FOR EACH ROW EXECUTE FUNCTION set_table_uuid();

DROP TRIGGER IF EXISTS trg_set_uuid_travel_seasons ON bs_travel_quote_seasons;
CREATE TRIGGER trg_set_uuid_travel_seasons
BEFORE INSERT ON bs_travel_quote_seasons
FOR EACH ROW EXECUTE FUNCTION set_table_uuid();


-- ============== 企业微信个人账号 RPA 渠道表（2026-06-22） ==============
-- 与 db_update.sql 同步；TEXT 存枚举/状态、无外键、无触发器、幂等。
-- 表结构必须与 src/channels/wecom_personal_rpa/db.py 保持一致。

-- 1. 客户端注册表
CREATE TABLE IF NOT EXISTS wecom_rpa_clients (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    name TEXT,
    encrypted_secret TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    min_version TEXT,
    agent_base_url TEXT,
    -- listen_mode: NULL 或 'client'。服务端拉取模式下永远为 NULL/'server'，客户端不启动本地 ChatArchiveListener
    listen_mode TEXT,
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_clients_tenant
    ON wecom_rpa_clients(tenant_id);

-- 2. 个人企微账号表
CREATE TABLE IF NOT EXISTS wecom_rpa_accounts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    client_id TEXT,
    display_name TEXT,
    wecom_user_id TEXT,
    wecom_user_aliases TEXT[] DEFAULT '{}',
    identity_verified_at TIMESTAMP,
    identity_verified_by TEXT,
    status TEXT NOT NULL DEFAULT 'offline',
    paused_reason TEXT,
    last_login_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_accounts_tenant_client
    ON wecom_rpa_accounts(tenant_id, client_id);

-- 3. 会话绑定表（同账号 + 搜索键唯一，用于重名识别）
CREATE TABLE IF NOT EXISTS wecom_rpa_conversation_bindings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    account_id TEXT NOT NULL,
    conversation_type TEXT,
    display_name TEXT,
    search_key TEXT NOT NULL,
    stable_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    last_verified_at TIMESTAMP,
    -- 监控白名单（绑定级）：任一非空时按白名单过滤，发送方不在白名单内的消息不上报
    -- monitor_user_names：监控的发送人显示名数组（可能重名）；空数组 = 不按名字过滤
    -- monitor_user_ids：监控的发送人稳定 ID 数组（external_userid/userid/room_id）；空数组 = 不按 ID 过滤
    -- 两个字段任一非空即按白名单过滤；都为空 = 监控所有（首版默认）
    monitor_user_names TEXT[] DEFAULT '{}',
    monitor_user_ids TEXT[] DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, search_key)
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_bindings_tenant_account
    ON wecom_rpa_conversation_bindings(tenant_id, account_id);

-- 4. 出站动作队列（离线客户端拉取执行）
CREATE TABLE IF NOT EXISTS wecom_rpa_archive_inbox (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    envelope JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    claim_token TEXT,
    error_message TEXT,
    next_retry_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    UNIQUE (tenant_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_archive_inbox_claim
    ON wecom_rpa_archive_inbox(tenant_id, status, next_retry_at, created_at);

CREATE TABLE IF NOT EXISTS wecom_rpa_action_outbox (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    account_id TEXT NOT NULL,
    conversation_id TEXT,
    request_id TEXT NOT NULL,
    session_id TEXT,
    actions TEXT,
    target_peer_id TEXT,
    reply_digest TEXT,
    reply_digests JSONB NOT NULL DEFAULT '[]'::jsonb,
    reply_context JSONB,
    action_results JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMP,
    completed_at TIMESTAMP,
    send_started_at TIMESTAMP,
    error_message TEXT,
    dedup_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_outbox_status_retry
    ON wecom_rpa_action_outbox(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_outbox_tenant_account
    ON wecom_rpa_action_outbox(tenant_id, account_id);
ALTER TABLE wecom_rpa_accounts ADD COLUMN IF NOT EXISTS wecom_user_id TEXT;
ALTER TABLE wecom_rpa_accounts ADD COLUMN IF NOT EXISTS wecom_user_aliases TEXT[] DEFAULT '{}';
ALTER TABLE wecom_rpa_accounts ADD COLUMN IF NOT EXISTS identity_verified_at TIMESTAMP;
ALTER TABLE wecom_rpa_accounts ADD COLUMN IF NOT EXISTS identity_verified_by TEXT;
ALTER TABLE wecom_rpa_action_outbox
    ADD COLUMN IF NOT EXISTS target_peer_id TEXT;
ALTER TABLE wecom_rpa_action_outbox
    ADD COLUMN IF NOT EXISTS reply_digest TEXT;
ALTER TABLE wecom_rpa_action_outbox
    ADD COLUMN IF NOT EXISTS reply_context JSONB;
ALTER TABLE wecom_rpa_action_outbox
    ADD COLUMN IF NOT EXISTS action_results JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE wecom_rpa_action_outbox
    ADD COLUMN IF NOT EXISTS reply_digests JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE wecom_rpa_action_outbox
    ADD COLUMN IF NOT EXISTS send_started_at TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_outbox_recent_echo
    ON wecom_rpa_action_outbox(tenant_id, account_id, target_peer_id, completed_at DESC)
    WHERE status = 'succeeded';
CREATE UNIQUE INDEX IF NOT EXISTS idx_wecom_rpa_accounts_tenant_wecom_user
    ON wecom_rpa_accounts(tenant_id, wecom_user_id) WHERE wecom_user_id IS NOT NULL;

-- 5. 审计日志表
CREATE TABLE IF NOT EXISTS wecom_rpa_audit_logs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    client_id TEXT,
    account_id TEXT,
    action_id TEXT,
    category TEXT,
    payload TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_audit_tenant_account
    ON wecom_rpa_audit_logs(tenant_id, account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_audit_tenant_category
    ON wecom_rpa_audit_logs(tenant_id, category, created_at DESC);

-- 社媒内容运营智能体与聚合平台核心表
CREATE TABLE IF NOT EXISTS social_accounts (
    account_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    platform TEXT,
    display_name TEXT,
    external_account_id TEXT,
    auth_type TEXT,
    credentials_encrypted TEXT,
    credential_key_version TEXT,
    status TEXT DEFAULT 'active',
    capabilities_json JSONB,
    capability_expires_at TIMESTAMP,
    last_validated_at TIMESTAMP,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_accounts_unique
    ON social_accounts(tenant_id, platform, external_account_id);

CREATE TABLE IF NOT EXISTS social_content_plans (
    plan_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    name TEXT,
    period_start DATE,
    period_end DATE,
    goal TEXT,
    target_audience TEXT,
    status TEXT DEFAULT 'draft',
    owner_user_id TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS social_content_items (
    item_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    plan_id TEXT,
    topic TEXT,
    objective TEXT,
    planned_at TIMESTAMP,
    timezone TEXT,
    owner_user_id TEXT,
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS social_content_masters (
    master_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    item_id TEXT,
    title TEXT,
    brief TEXT,
    facts_json JSONB,
    source_refs_json JSONB,
    brand_constraints_json JSONB,
    revision INTEGER DEFAULT 1,
    content_hash TEXT,
    status TEXT DEFAULT 'draft',
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS social_media_assets (
    asset_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    storage_file_id TEXT,
    asset_type TEXT,
    mime_type TEXT,
    file_size INTEGER,
    checksum TEXT,
    source_type TEXT,
    source_uri TEXT,
    license_type TEXT,
    license_owner TEXT,
    license_expires_at TIMESTAMP,
    status TEXT DEFAULT 'active',
    metadata_json JSONB,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS social_content_asset_links (
    link_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    master_id TEXT,
    asset_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS social_content_variants (
    variant_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    master_id TEXT,
    account_id TEXT,
    platform TEXT,
    content_type TEXT,
    revision INTEGER DEFAULT 1,
    content_json JSONB,
    content_hash TEXT,
    spec_version TEXT,
    prompt_version TEXT,
    status TEXT DEFAULT 'draft',
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_variants_revision
    ON social_content_variants(tenant_id, variant_id, revision);

CREATE TABLE IF NOT EXISTS social_review_records (
    review_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    variant_id TEXT,
    variant_revision INTEGER,
    content_hash TEXT,
    decision TEXT,
    comment TEXT,
    reviewer_user_id TEXT,
    reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS social_publish_jobs (
    job_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    account_id TEXT,
    variant_id TEXT,
    variant_revision INTEGER,
    content_hash TEXT,
    publish_mode TEXT,
    scheduled_at TIMESTAMP,
    timezone TEXT,
    status TEXT DEFAULT 'draft',
    idempotency_key TEXT UNIQUE,
    publish_snapshot_json JSONB,
    external_task_id TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    next_retry_at TIMESTAMP,
    lease_owner TEXT,
    lease_expires_at TIMESTAMP,
    last_error_code TEXT,
    last_error_message TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_due ON social_publish_jobs(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_tenant_account ON social_publish_jobs(tenant_id, account_id, created_at);
CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_external_task ON social_publish_jobs(external_task_id);
CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_lease ON social_publish_jobs(lease_expires_at);

CREATE TABLE IF NOT EXISTS social_publish_attempts (
    attempt_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    job_id TEXT,
    attempt_no INTEGER,
    trigger_type TEXT,
    request_summary_json JSONB,
    response_summary_json JSONB,
    status TEXT,
    platform_error_code TEXT,
    error_category TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS social_published_contents (
    published_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    job_id TEXT,
    account_id TEXT,
    variant_id TEXT,
    platform TEXT,
    external_content_id TEXT,
    external_url TEXT,
    confirmation_source TEXT,
    published_at TIMESTAMP,
    confirmed_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS social_metric_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    account_id TEXT,
    published_id TEXT,
    metric_date DATE,
    metric_definition TEXT,
    normalized_metrics_json JSONB,
    raw_metrics_json JSONB,
    source_type TEXT,
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    import_batch_id TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_metric_snapshots_unique
    ON social_metric_snapshots(tenant_id, account_id, published_id, metric_date, metric_definition);

CREATE TABLE IF NOT EXISTS social_data_import_batches (
    batch_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    account_id TEXT,
    platform TEXT,
    template_version TEXT,
    file_digest TEXT,
    status TEXT DEFAULT 'uploaded',
    error_summary TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);



-- ============================================================================
-- 2026-07-17 Browser Run/Executor Phase 2：仅存脱敏执行与恢复审计事实
-- ============================================================================
CREATE TABLE IF NOT EXISTS bs_browser_runs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE,
    parent_run_id TEXT,
    session_id TEXT NOT NULL,
    execution_target TEXT NOT NULL CHECK (execution_target IN ('server','client')),
    executor_client_id TEXT,
    state TEXT NOT NULL,
    routing_reason TEXT,
    failure_class TEXT,
    evidence_level TEXT,
    escalation_count INTEGER NOT NULL DEFAULT 0 CHECK (escalation_count BETWEEN 0 AND 1),
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    close_reason TEXT,
    error_code TEXT,
    steps_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_bs_browser_runs_tenant_session
    ON bs_browser_runs(tenant_id, session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bs_browser_runs_tenant_state
    ON bs_browser_runs(tenant_id, state, updated_at);

CREATE TABLE IF NOT EXISTS bs_browser_assistance_requests (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    assistance_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    agent_execution_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    state TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    instruction_code TEXT NOT NULL,
    completion_mode TEXT NOT NULL CHECK (completion_mode IN ('auto_or_confirm','confirm_only')),
    predicate_type TEXT,
    expires_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    resumed_at TIMESTAMP,
    error_code TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id, run_id) REFERENCES bs_browser_runs(tenant_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_bs_browser_assistance_tenant_run
    ON bs_browser_assistance_requests(tenant_id, run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bs_browser_assistance_tenant_state
    ON bs_browser_assistance_requests(tenant_id, state, expires_at);

-- ============================================================================
-- 2026-07-22 Browser Phase 3R：PostgreSQL 持久 lease 队列替代 Redis Stream 恢复
-- 设计文档 browser_visualization_design.md v2.8 §8.1。遵循 database_dev.md
-- 不加外键约束（外键完整性在应用层校验）。assistance_id 唯一保证幂等入队。
-- ============================================================================
CREATE TABLE IF NOT EXISTS bs_browser_resume_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,                       -- 不可猜 UUIDv4
    tenant_id TEXT NOT NULL,
    assistance_id TEXT NOT NULL UNIQUE,                -- 防重复入队
    run_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending','processing','completed','failed')),
    lease_owner TEXT,                                   -- worker 领取身份
    lease_until TIMESTAMP,                              -- 短租约，崩溃后可回收
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- 退避调度
    last_error_code TEXT,                               -- 白名单错误码，不存异常正文
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
-- 领取查询主索引：pending 到期或 processing 租约过期，按创建顺序
CREATE INDEX IF NOT EXISTS idx_bs_browser_resume_jobs_claim
    ON bs_browser_resume_jobs(state, available_at);
CREATE INDEX IF NOT EXISTS idx_bs_browser_resume_jobs_tenant
    ON bs_browser_resume_jobs(tenant_id, state, created_at);

-- 测试库初始化完成
DO $$
BEGIN
    RAISE NOTICE 'Test database (aid_work_agent2) initialized successfully';
END $$;

-- ============== 租户积分充值流水 ==============
-- 平台级计费表，记录每一次租户充值（手动/在线支付）
-- 与 subscriptions/payment_orders 并行，构成预付费积分计费体系
CREATE TABLE IF NOT EXISTS tenant_recharges (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    amount_yuan NUMERIC(10,2) NOT NULL,          -- 充值金额（元）
    credits INTEGER NOT NULL,                    -- 转化积分
    rate INTEGER NOT NULL,                       -- 兑换系数（credits / amount_yuan，默认 10）
    source TEXT NOT NULL DEFAULT 'manual',       -- manual / online_payment
    payment_order_id TEXT,                       -- 关联 payment_orders.order_id，manual 时为 NULL
    operator_id TEXT,                            -- 平台管理员 user_id（manual 必填）
    operator_name TEXT,                          -- 平台管理员姓名（冗余，便于审计）
    remark TEXT,                                 -- 备注
    balance_after NUMERIC(12,2),                  -- 充值后积分余额快照（创建时由事务内计算写入；历史数据为 NULL）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tenant_recharges_tenant_id ON tenant_recharges(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_recharges_created_at ON tenant_recharges(created_at DESC);

-- ============== 巡检商机模块（B1 商机池 3 表） ==============
-- 设计文档：docs/system/digital-employee/social-media-marketing-agent-design.md §7.6
-- 规范：database_dev.md（bs_ 前缀、tenant_id/user_id/created_at 必备、TEXT 存枚举、无外键/触发器）
-- 注意：bs_outbound_account_sessions（登录态子系统）由并行模块负责，不在本段

-- 商机主表：原文加密、意向分、状态、去重指纹
CREATE TABLE IF NOT EXISTS bs_outbound_leads (
    lead_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    platform TEXT,
    source_type TEXT,                            -- radar / content_interaction / outreach_reply
    external_content_id TEXT,
    external_url TEXT,
    raw_text_encrypted TEXT,                     -- PII 原文加密（encryption_manager）
    intent_score INTEGER,                        -- 0-100 意向分
    status TEXT DEFAULT 'new',                   -- new/contacted/qualified/invalid/converted
    assigned_user_id TEXT,
    dedup_fingerprint TEXT,                      -- 同 tenant 内 UNIQUE（部分索引）
    contact_points TEXT,
    risk_flags TEXT,
    user_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- 同 tenant 内 dedup_fingerprint 唯一（仅当指纹非空时强制）
CREATE UNIQUE INDEX IF NOT EXISTS idx_bs_outbound_leads_dedup
    ON bs_outbound_leads(tenant_id, dedup_fingerprint)
    WHERE dedup_fingerprint IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bs_outbound_leads_tenant_status
    ON bs_outbound_leads(tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bs_outbound_leads_assigned
    ON bs_outbound_leads(tenant_id, assigned_user_id, intent_score DESC);

-- 商机互动/跟进记录
CREATE TABLE IF NOT EXISTS bs_outbound_lead_interactions (
    interaction_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    lead_id TEXT,
    interaction_type TEXT,                       -- note/call/email/dm/comment/visit/wechat/other
    content TEXT,
    actor_user_id TEXT,
    user_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bs_outbound_lead_interactions_lead
    ON bs_outbound_lead_interactions(tenant_id, lead_id, created_at DESC);

-- 我方接触动作审计（comment/dm/post，默认 draft 待审）
CREATE TABLE IF NOT EXISTS bs_outbound_outreach_actions (
    action_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    lead_id TEXT,
    action_type TEXT,                            -- comment/dm/post
    channel TEXT,                                -- zhihu/xiaohongshu/...
    content_snapshot TEXT,
    execution_status TEXT,                       -- draft/pending_review/executed/failed/cancelled
    reviewer_user_id TEXT,
    user_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bs_outbound_outreach_actions_lead
    ON bs_outbound_outreach_actions(tenant_id, lead_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bs_outbound_outreach_actions_tenant_status
    ON bs_outbound_outreach_actions(tenant_id, execution_status, created_at DESC);

-- ============== 巡检商机：托管登录态（B0.5）==============
-- 社媒营销智能体 outbound 模块的登录态托管表，存 Playwright storage_state 加密 blob。
-- 用于知乎/小红书等 web 操作型连接器跨 run 维持登录态。设计 §7.3 / §10。
CREATE TABLE IF NOT EXISTS bs_outbound_account_sessions (
    account_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    platform TEXT,
    storage_state_encrypted TEXT NOT NULL,
    storage_state_key_version TEXT DEFAULT 'v1',
    status TEXT DEFAULT 'active',
    last_used_at TIMESTAMP,
    expired_reason TEXT,
    cookie_count INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_outbound_account_sessions_tenant_status
    ON bs_outbound_account_sessions(tenant_id, status, updated_at DESC);

-- ============== 工作日报功能（2026-07-22）==============
-- 个人 + 团队两个视角共用，按 scope 区分；scope=personal 时 target_user_id 必填
-- report_type 取值：daily / weekly / monthly
-- 详情见 docs/research/ai-agent-experience-daily-report-research.md §4.5.2
CREATE TABLE IF NOT EXISTS work_daily_reports (
    id SERIAL PRIMARY KEY,
    report_id TEXT UNIQUE NOT NULL,                  -- wdr_xxxxxxxx 格式
    tenant_id TEXT NOT NULL,
    scope TEXT NOT NULL,                             -- 'personal' / 'team'
    report_type TEXT NOT NULL DEFAULT 'daily',       -- 'daily' / 'weekly' / 'monthly'
    target_user_id TEXT,                             -- scope=personal 时必填，team 时为 NULL
    report_date DATE NOT NULL,                       -- 日报日期（按本地时区）

    -- 统计指标（JSON）：对话数/积分/节省时间/活跃员工数等
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- LLM 生成内容
    summary_text TEXT,                               -- 工作内容摘要
    highlights JSONB,                                -- 高光时刻数组
    suggestions JSONB,                               -- 建议/提醒数组

    -- 元数据
    model TEXT,                                      -- 生成所用模型（deepseek-v4-flash 等）
    token_cost INTEGER DEFAULT 0,                    -- 生成消耗 token
    credit_cost NUMERIC(12,2) DEFAULT 0.00,                   -- 生成消耗积分（与 chat_records.credit_cost 一致）
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    regenerated_count INTEGER DEFAULT 0,             -- 重生次数

    UNIQUE(tenant_id, scope, report_type, target_user_id, report_date)
);

CREATE INDEX IF NOT EXISTS idx_work_daily_reports_tenant_date
    ON work_daily_reports(tenant_id, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_work_daily_reports_user_date
    ON work_daily_reports(target_user_id, report_date DESC) WHERE scope = 'personal';
CREATE INDEX IF NOT EXISTS idx_work_daily_reports_tenant_scope_type_date
    ON work_daily_reports(tenant_id, scope, report_type, report_date DESC);

-- 日报推送配置表（每用户一行，UPSERT）
-- personal_report_types / team_report_types 为 TEXT[]，支持 daily/weekly/monthly 复选
-- 详情见 docs/research/ai-agent-experience-daily-report-research.md §4.5.3
CREATE TABLE IF NOT EXISTS work_report_preferences (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,

    -- 个人视角：总开关 + 订阅的报告类型（复选）
    personal_report_enabled BOOLEAN DEFAULT TRUE,
    personal_report_types TEXT[] NOT NULL DEFAULT ARRAY['daily'],
    personal_push_channels TEXT[],                  -- ['in_app', 'wecom', 'email']
    personal_push_time TIME DEFAULT '18:00',

    -- 团队视角（仅管理员可见）
    team_report_enabled BOOLEAN DEFAULT FALSE,
    team_report_types TEXT[] NOT NULL DEFAULT ARRAY['daily'],
    team_push_channels TEXT[],
    team_push_time TIME DEFAULT '19:00',

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, user_id)
);

-- ============== 工作成果记录功能（2026-07-29）==============
-- 记录子智能体产生的重要工作成果（文件交付/业务操作/决策建议）
-- 详见 docs/system/work-outcome-record-design.md
-- source: cp_realtime（cp 工具实时登记）/ scheduled_review（半夜复盘提取）/ manual
CREATE TABLE IF NOT EXISTS work_outcomes (
    id SERIAL PRIMARY KEY,
    outcome_id TEXT UNIQUE NOT NULL,                  -- wo_xxxxxxxx 格式
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,                            -- 触发成果的用户
    subagent_id TEXT,                                 -- 子智能体ID（主智能体直接交付时为 NULL）
    session_id TEXT NOT NULL,                         -- 会话ID（chat_sessions.session_id 或 channel_sessions.session_id）
    channel TEXT,                                     -- web/wecom/dingtalk/feishu/wecom_kf

    -- 成果内容
    summary TEXT NOT NULL,                            -- 一句话摘要，含业务对象和动作
    outcome_type TEXT NOT NULL DEFAULT 'other',       -- file/action/decision/other
    importance TEXT NOT NULL DEFAULT 'normal',        -- normal/high（预留，便于后续过滤）

    -- 文件关联（outcome_type=file 时必填）
    file_id TEXT,                                     -- cp 工具注册的 file_id
    file_name TEXT,                                    -- 面向用户的业务文件名（display_name）
    file_path TEXT,                                    -- 文件存储路径（用于后续清理/迁移）

    -- 业务扩展信息（如客户名、订单号、金额、行程天数等）
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- 来源与溯源
    source TEXT NOT NULL DEFAULT 'cp_realtime',     -- cp_realtime/scheduled_review/manual
    chat_record_id BIGINT,                            -- 关联 chat_records.id，便于反查对话上下文

    -- 复盘任务溯源（source=scheduled_review 时填写）
    review_batch_id TEXT,                             -- 复盘批次ID，便于追溯本次复盘的所有产出
    review_confidence REAL,                           -- 小模型判断置信度 0.0~1.0，便于后续过滤

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_work_outcomes_tenant_created
    ON work_outcomes(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_user_created
    ON work_outcomes(tenant_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_subagent_created
    ON work_outcomes(tenant_id, subagent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_session
    ON work_outcomes(session_id);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_file_id
    ON work_outcomes(file_id) WHERE file_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_work_outcomes_review_batch
    ON work_outcomes(review_batch_id) WHERE review_batch_id IS NOT NULL;

-- ============================================================================
-- 视频生成 MVP：gen_sessions / gen_cards 表
-- 详情见 docs/system/content-production/mvp-design.md §3
-- 与 src/video_gen/db.py 的 init_video_gen_tables() 保持一致
-- ============================================================================
CREATE TABLE IF NOT EXISTS gen_sessions (
    session_id        TEXT PRIMARY KEY,          -- sess_<12hex>
    tenant_id         TEXT,                      -- 租户（可空，demo 模式）
    user_id           TEXT,                      -- 发起用户
    scene_id          TEXT NOT NULL,             -- 场景预设 id（硬编码，如 product_showcase）
    product_image_fid TEXT NOT NULL,             -- 产品图 file_id（r2v 作 reference_image，锁定产品外观防变形）
    model_image_fid   TEXT,                      -- 模特图 file_id（可选，作 first_frame 控制起始画面；空则用产品图）
    copywriting       TEXT NOT NULL,             -- 运营填写的文案
    expanded_prompt   TEXT,                      -- 提示词引擎扩展后的完整 prompt（可微调）
    card_count        INT NOT NULL DEFAULT 3,    -- 本次抽卡条数（2-4）
    enable_ai_label   BOOLEAN NOT NULL DEFAULT TRUE,  -- 是否烧录 AI 内容角标（合规默认开）
    duration_sec      INT NOT NULL DEFAULT 5,    -- 视频时长（5/10/15 秒，万相 r2v 上限 15s）
    resolution        TEXT NOT NULL DEFAULT '720P',  -- 分辨率（720P/1080P，万相 r2v 不支持 480P）
    ratio             TEXT NOT NULL DEFAULT '9:16',  -- 视频画面比例（9:16 竖版 / 16:9 横版 / 1:1 / 4:3 / 3:4 / 21:9）
    status            TEXT NOT NULL DEFAULT 'generating',  -- generating/done/failed
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gen_cards (
    card_id           TEXT PRIMARY KEY,          -- card_<12hex>
    tenant_id         TEXT,
    session_id        TEXT NOT NULL,             -- 逻辑外键 -> gen_sessions
    variant_idx       INT NOT NULL,              -- 第几条（0-based）
    seed              BIGINT,                    -- 万相随机种子（差异化来源）
    variant_prompt    TEXT,                      -- 本条差异化后的 prompt
    provider_task_id  TEXT,                      -- 万相返回的 task_id
    provider_status   TEXT,                      -- PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED/UNKNOWN
    output_fid        TEXT,                      -- 成片 file_id（成功后回填）
    output_url        TEXT,                      -- 万相返回的临时 video_url（24h 有效，下载后可清）
    output_duration   INT,                       -- 成片时长（秒）
    kept              BOOLEAN NOT NULL DEFAULT FALSE,  -- 是否被运营留用
    parent_card_id    TEXT,                      -- 精修溯源（从哪张 card 重新生成）
    error_msg         TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_gen_cards_session
    ON gen_cards(session_id);
CREATE INDEX IF NOT EXISTS idx_gen_cards_polling
    ON gen_cards(provider_status);

-- ============================================================================
-- 视频创作智能体（video-agent）Phase 1：素材库 + 提示词库
-- 详见 docs/plans/plan-video-agent-phase1.md §1.1 / §1.2
-- 视频库复用 work_outcomes 表（outcome_type='file' + subagent_id='video-agent'），不新建
-- ============================================================================
CREATE TABLE IF NOT EXISTS asset_library (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,                         -- 租户隔离
    user_id TEXT,                                   -- 上传者（系统自动入库时可空）
    file_id TEXT NOT NULL,                          -- 关联 uploaded_file:{file_id}，下载入口
    display_name TEXT NOT NULL,                     -- 显示名（如 "产品图_001.jpg"）
    mime_type TEXT NOT NULL,                        -- image/jpeg / video/mp4 等
    size_bytes BIGINT NOT NULL,
    source TEXT NOT NULL,                           -- video_chat / user_upload / other_agent_manual
    scene TEXT,                                     -- 业务场景标签（product / model / bgm 等，可选）
    width INT,                                      -- 图片/视频宽
    height INT,                                     -- 图片/视频高
    -- 肖像授权字段（仅 source=video_chat 且图为模特图时使用，第一阶段可空）
    portrait_authorized BOOLEAN DEFAULT FALSE,
    portrait_auth_expire_at TIMESTAMP,
    portrait_auth_scope TEXT,                       -- 授权范围（如 "电商展示"）
    metadata JSONB,                                 -- 附加信息（如 EXIF、来源会话 ID）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_asset_library_tenant ON asset_library(tenant_id);
CREATE INDEX IF NOT EXISTS idx_asset_library_source ON asset_library(source);
CREATE INDEX IF NOT EXISTS idx_asset_library_tenant_scene ON asset_library(tenant_id, scene);

CREATE TABLE IF NOT EXISTS prompt_library (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,                                   -- 留用者 / 黑名单提交者
    category TEXT NOT NULL,                         -- kept（留用）/ blacklist（黑名单）/ template（模版）
    business_prompt TEXT NOT NULL,                  -- 业务层提示词（中文，员工可读）
    craft_prompt TEXT NOT NULL,                     -- 工艺层提示词（可灵 8 层框架结构化）
    model_params JSONB,                             -- 模型层参数（seed / negative_prompt / duration / ratio / resolution）
    industry_tag TEXT,                              -- 行业品类（美妆 / 服饰 / 食品等，可选）
    scene_tag TEXT,                                 -- 场景标签（开箱 / 展示 / 氛围等，可选）
    -- 关联视频（留用时记录是哪个视频的提示词）
    source_video_file_id TEXT,                      -- 来源视频的 file_id（可空，黑名单必填，留用必填）
    source_chat_session_id TEXT,                    -- 来源会话 ID（溯源）
    -- 黑名单专用
    dislike_reason TEXT,                            -- 不喜欢原因（光线偏暗/动作不自然/构图有问题/其他）
    -- 模版专用（第一阶段不写入，但字段先建好）
    promoted_from_kept_id INT,                      -- 由哪条留用记录升级而来
    promoted_by_user_id TEXT,                       -- 升级操作者（租户管理员）
    promoted_at TIMESTAMP,
    -- 通用
    metadata JSONB,                                 -- 附加信息（如生成时用的模型、消耗积分）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prompt_library_tenant_category ON prompt_library(tenant_id, category);
CREATE INDEX IF NOT EXISTS idx_prompt_library_tenant_scene ON prompt_library(tenant_id, scene_tag);

-- ============================================================================
-- subagent_definitions 扩展：chat_toolbar JSONB + upload_accept TEXT
-- 用于声明式 UI 配置：聊天工具栏额外按钮 + 上传文件类型限定
-- ============================================================================
ALTER TABLE subagent_definitions ADD COLUMN IF NOT EXISTS chat_toolbar JSONB DEFAULT '[]'::jsonb;
ALTER TABLE subagent_definitions ADD COLUMN IF NOT EXISTS upload_accept TEXT;

-- ============================================================================
-- 本地工具基础设施（M0.3）：本地设备配对/调用/事件 系统表
-- 详见 docs/plans/recruiting/m03-implementation-spec.md §2
-- ============================================================================
CREATE TABLE IF NOT EXISTS local_tool_devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT,
    platform TEXT,
    runtime_version TEXT,
    token_hash TEXT UNIQUE NOT NULL,
    machine_fingerprint_hash TEXT,
    capabilities_json JSONB,
    manifest_digest TEXT,
    selected BOOLEAN DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'active',   -- active / revoked
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lt_devices_tenant_user ON local_tool_devices(tenant_id, user_id);

CREATE TABLE IF NOT EXISTS local_tool_pairing_tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    code_hash TEXT UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS local_tool_invocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    device_id UUID NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json JSONB NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    -- queued/claimed/running/succeeded/failed/cancel_requested/cancelled/unknown/expired
    effect TEXT,                              -- none/applied/partial/unknown，终态时填
    claim_token_hash TEXT,
    lease_expires_at TIMESTAMP,
    result_json JSONB,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    claimed_at TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lt_inv_device_state ON local_tool_invocations(device_id, state);
CREATE INDEX IF NOT EXISTS idx_lt_inv_tenant_user ON local_tool_invocations(tenant_id, user_id);

CREATE TABLE IF NOT EXISTS local_tool_events (
    id BIGSERIAL PRIMARY KEY,
    invocation_id UUID NOT NULL,
    tenant_id TEXT NOT NULL,
    seq INT NOT NULL,
    stage TEXT,
    current INT,
    total INT,
    message TEXT,                             -- 脱敏后进度文案
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (invocation_id, seq)
);
