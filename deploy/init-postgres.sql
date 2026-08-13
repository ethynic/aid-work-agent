-- PostgreSQL 数据库初始化脚本
-- 用于 AID Work Agent 数据库初始化
-- 包含核心业务表和 SaaS 多租户表
-- 单实例方案：通过修改下方 \c 指令切换目标数据库（生产库 / 测试库）
--
-- 切换数据库：修改下面这一行 \c 即可
--   生产库：\c aid_work_agent
--   测试库：\c aid_work_agent2
-- 注意：目标数据库及用户需提前手动创建，例如首次初始化测试库时执行：
--   CREATE USER aid_user2 WITH PASSWORD 'Aid_2026';
--   CREATE DATABASE aid_work_agent2 OWNER aid_user2;
--   ALTER USER aid_user2 SET statement_timeout = '30000';

-- ============== 指定目标数据库（切换数据库时修改这一行）==============
\c aid_work_agent

-- 启用 pgvector 扩展（用于向量搜索）
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

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
    -- LLM 计费接入改造（2026-08-12）：embedding/ASR/视频提示词 等扩展计费维度
    embedding_tokens INTEGER DEFAULT 0,   -- 累加该轮交互中所有 embedding 调用的 input token
    asr_calls INTEGER DEFAULT 0,          -- 累加该轮交互中所有 ASR 调用次数（阿里云 NLS 按次计费）
    usage_breakdown JSONB,                -- 详细分项明细 JSON（chat/embedding/asr/vision 各自 token 与 credit）
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
    embedding_price_per_m NUMERIC(10,4), -- 向量模型单价（元/百万 token），用于 text-embedding-v3 等
    asr_price_per_call NUMERIC(10,4), -- 语音识别单价（元/次），用于阿里云 NLS 一句话识别
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

-- Embedding 向量模型单价（元/百万 tokens）
-- text-embedding-v3 阿里云百炼官方定价 0.5 元/百万 tokens
INSERT INTO token_cost_prices (model_name, embedding_price_per_m)
VALUES ('text-embedding-v3', 0.5)
ON CONFLICT (model_name) DO NOTHING;

-- 阿里云 NLS 一句话识别单价：0.01 元/次（1次最多60s）
INSERT INTO token_cost_prices (model_name, asr_price_per_call)
VALUES ('aliyun-nls-asr', 0.01)
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

-- subagent_template_files — 租户级子智能体模板文件关联
-- 每个租户的每个子智能体可挂载多个模板文件（名称 + file_id + 元信息），
-- 运行时注入 system prompt 末尾（### 相关模板位置信息）。
CREATE TABLE IF NOT EXISTS subagent_template_files (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    subagent_name TEXT NOT NULL,
    files JSONB NOT NULL DEFAULT '[]',
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
END $$;

