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

-- 租户内手机号唯一约束（不同租户允许相同手机号）
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tenant_phone ON users (tenant_id, phone) WHERE phone IS NOT NULL AND tenant_id IS NOT NULL;

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
    metadata TEXT
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
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_channel_sessions_tenant_channel ON channel_sessions(tenant_id, channel_type, channel_user_id);
CREATE INDEX IF NOT EXISTS idx_channel_messages_session ON channel_messages(session_id, created_at);

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
    output_price_per_m NUMERIC(10,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 初始数据：qwen-plus 模型单价
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m)
VALUES ('qwen-plus', 0.8, 2.0)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m)
VALUES ('deepseek-v4-flash', 1.0, 2.0)
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON COLUMN tenants.expire_at IS '到期日期（时分秒为 23:59:59，当天仍可登录，空表示永久有效）';

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

-- 智能体实例表
CREATE TABLE IF NOT EXISTS agent_instances (
    id SERIAL PRIMARY KEY,
    instance_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    subscription_id TEXT,
    subagent_type TEXT,
    display_name TEXT,                          -- 子智能体类型显示名
    instance_name TEXT,                         -- 实例名称："外贸小明"
    avatar TEXT DEFAULT '🤖',                   -- 头像 emoji 或 URL
    description TEXT,                            -- 实例描述
    personality_traits TEXT,                     -- 性格特征（JSON数组）
    status TEXT DEFAULT 'idle' CHECK (status IN ('idle', 'busy')), -- 只允许 idle/busy
    current_session_id TEXT,                     -- 当前活跃会话ID
    current_user_id TEXT,                        -- 当前使用者
    locked_at TIMESTAMP,                         -- 锁定开始时间
    lock_expires_at TIMESTAMP,                   -- 锁过期时间
    config TEXT,
    bound_channel_type TEXT,
    allowed_skills TEXT,
    reply_style_id TEXT,                          -- 回复风格ID
    total_chats INTEGER DEFAULT 0,               -- 累计对话次数
    total_messages INTEGER DEFAULT 0,            -- 累计消息数
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_instances_tenant ON agent_instances(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_instances_tenant_type ON agent_instances(tenant_id, subagent_type, status);
CREATE INDEX IF NOT EXISTS idx_agent_instances_lock_expires
ON agent_instances(lock_expires_at)
WHERE current_session_id IS NOT NULL;

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

-- 实例等待队列表
CREATE TABLE IF NOT EXISTS agent_instance_queue (
    id SERIAL PRIMARY KEY,
    queue_id TEXT UNIQUE NOT NULL,
    instance_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT DEFAULT 'waiting',        -- waiting / ready / expired / cancelled
    enqueued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    wait_timeout_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_instance_queue_instance ON agent_instance_queue(instance_id, position);
CREATE INDEX IF NOT EXISTS idx_instance_queue_session ON agent_instance_queue(session_id);
CREATE INDEX IF NOT EXISTS idx_instance_queue_timeout ON agent_instance_queue(wait_timeout_at);

-- 租户渠道配置表
CREATE TABLE IF NOT EXISTS tenant_channel_configs (
    id SERIAL PRIMARY KEY,
    config_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    channel_type TEXT,
    config TEXT,
    verified INTEGER DEFAULT 0,
    subagent_type TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tenant_channel_configs_tenant ON tenant_channel_configs(tenant_id, channel_type);

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

-- 租户内手机号唯一约束（不同租户允许相同手机号）
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tenant_phone ON users (tenant_id, phone) WHERE phone IS NOT NULL AND tenant_id IS NOT NULL;

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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON COLUMN tenants.expire_at IS '到期日期（时分秒为 23:59:59，当天仍可登录，空表示永久有效）';

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

-- 智能体实例表
CREATE TABLE IF NOT EXISTS agent_instances (
    id SERIAL PRIMARY KEY,
    instance_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    subscription_id TEXT,
    subagent_type TEXT,
    display_name TEXT,                          -- 子智能体类型显示名
    instance_name TEXT,                         -- 实例名称："外贸小明"
    avatar TEXT DEFAULT '🤖',                   -- 头像 emoji 或 URL
    description TEXT,                            -- 实例描述
    personality_traits TEXT,                     -- 性格特征（JSON数组）
    status TEXT DEFAULT 'idle' CHECK (status IN ('idle', 'busy')), -- 只允许 idle/busy
    current_session_id TEXT,                     -- 当前活跃会话ID
    current_user_id TEXT,                        -- 当前使用者
    locked_at TIMESTAMP,                         -- 锁定开始时间
    lock_expires_at TIMESTAMP,                   -- 锁过期时间
    config TEXT,
    bound_channel_type TEXT,
    allowed_skills TEXT,
    reply_style_id TEXT,                          -- 回复风格ID
    total_chats INTEGER DEFAULT 0,               -- 累计对话次数
    total_messages INTEGER DEFAULT 0,            -- 累计消息数
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_instances_tenant ON agent_instances(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_instances_tenant_type ON agent_instances(tenant_id, subagent_type, status);
CREATE INDEX IF NOT EXISTS idx_agent_instances_lock_expires
ON agent_instances(lock_expires_at)
WHERE current_session_id IS NOT NULL;

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

-- 实例等待队列表
CREATE TABLE IF NOT EXISTS agent_instance_queue (
    id SERIAL PRIMARY KEY,
    queue_id TEXT UNIQUE NOT NULL,
    instance_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT DEFAULT 'waiting',        -- waiting / ready / expired / cancelled
    enqueued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    wait_timeout_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_instance_queue_instance ON agent_instance_queue(instance_id, position);
CREATE INDEX IF NOT EXISTS idx_instance_queue_session ON agent_instance_queue(session_id);
CREATE INDEX IF NOT EXISTS idx_instance_queue_timeout ON agent_instance_queue(wait_timeout_at);

-- 租户渠道配置表
CREATE TABLE IF NOT EXISTS tenant_channel_configs (
    id SERIAL PRIMARY KEY,
    config_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    channel_type TEXT,
    config TEXT,
    verified INTEGER DEFAULT 0,
    subagent_type TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tenant_channel_configs_tenant ON tenant_channel_configs(tenant_id, channel_type);

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
    llm_provider    TEXT,
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


-- 测试库初始化完成
DO $$
BEGIN
    RAISE NOTICE 'Test database (aid_work_agent2) initialized successfully';
END $$;