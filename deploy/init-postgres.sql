-- PostgreSQL 数据库初始化脚本
-- 用于 AID Work Agent 数据库初始化
-- 包含核心业务表、SaaS 多租户表，及渠道(wecom_rpa)/社媒/视频/工作报告等全部系统表
-- 不含 bs_ 开头的业务表（由子智能体初始化时自动创建）
--
-- 目标数据库：请勿在本文件中写 \c 切库——历史上这里的 \c 曾把对测试库执行的
-- 初始化静默重定向到生产库。统一用 psql -d 显式指定目标库执行：
--   生产库：docker exec -i aid-postgres psql -U aid_user -d aid_work_agent -f 本文件
--   测试库：docker exec -i aid-postgres psql -U aid_user -d aid_work_agent2 -f 本文件
-- 注意：目标数据库及用户需提前手动创建，例如首次初始化测试库时执行：
--   CREATE USER aid_user2 WITH PASSWORD 'Aid_2026';
--   CREATE DATABASE aid_work_agent2 OWNER aid_user2;
--   ALTER USER aid_user2 SET statement_timeout = '30000';

-- ============== 目标数据库由 psql -d 参数指定（见上） ==============

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
    gender SMALLINT DEFAULT 0,
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
    recalled_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'active'   -- 消息状态：active（正常）/ invalid（软删除失效，隐藏命令"新会话"标记，历史记录保留但不再进入 LLM 上下文）
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
-- 隐藏命令"新会话"软删除：status='invalid' 的消息不进入 LLM 上下文，历史记录保留
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

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

-- 用户行为审计日志表（登录/登出/改密/渠道绑定等安全敏感事件，仅记写操作不记查询）
-- 设计文档：docs/system/user-behavior-audit-log-design.md §3
CREATE TABLE IF NOT EXISTS user_behavior_logs (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,                      -- 租户ID；platform_admin 全局操作为 NULL
    user_id TEXT,                        -- 操作人；登录失败无用户身份/渠道事件未注册用户时为 NULL，标识记 detail
    user_role TEXT,                      -- 操作时角色快照（platform_admin/tenant_admin/user）
    action TEXT NOT NULL,                -- 行为类型，见枚举 BehaviorAction
    resource_type TEXT,                  -- 资源类型，见枚举 BehaviorResourceType
    resource_id TEXT,                    -- 资源ID（如租户ID、文档doc_id）
    resource_name TEXT,                  -- 资源名称快照（便于人读，如租户名、文档标题）
    detail JSONB DEFAULT '{}',           -- 变更摘要（字段名列表/关键新值），过滤敏感字段
    client_ip VARCHAR(45),               -- IPv4/IPv6，取 X-Forwarded-For 首段
    user_agent VARCHAR(512),             -- 原始 UA 截断
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_msg TEXT,                      -- 失败原因（截断 500 字符）
    entry VARCHAR(16),                   -- 入口：web / api / channel（渠道回调），见枚举 BehaviorEntry
    login_method VARCHAR(32),            -- 登录方式：password / sms / sso；仅登录事件有值
    channel VARCHAR(16),                 -- 渠道：wecom / wecom_kf / wecom_personal_rpa / dingtalk / feishu；仅渠道事件有值
    channel_user_id TEXT,                -- 渠道侧用户标识（external_userid 等）；仅渠道事件有值
    token_id TEXT,                       -- 当前 token 的 SHA256 前 8 位（关联登录态，不存原文）
    request_id TEXT,                     -- obs 系统 trace_id，无则 NULL
    http_method VARCHAR(10),             -- CRUD 事件：请求方法（GET/POST/PUT/PATCH/DELETE）
    path VARCHAR(255),                   -- CRUD 事件：请求路径（如 /api/saas/tenants）
    device_type VARCHAR(16),             -- 粗分设备类型：pc / mobile / tablet / unknown，见枚举 BehaviorDeviceType
    device_info VARCHAR(32),             -- 细分设备快照：写入时解析冻结（如 android_wechat），受控词表见设计 §5.4
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ubl_tenant_time ON user_behavior_logs(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ubl_user_time ON user_behavior_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ubl_action_time ON user_behavior_logs(action, created_at DESC);

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
    price_per_call NUMERIC(10,4), -- 通用按次单价（元/次），公众号图片 VL 解析等按张/按次计费（与 asr_price_per_call 区分）
    tiered_pricing JSONB, -- 分段计价模型单价（按单次请求输入 token 数分档），[{"max_input": 32768, "input_per_m": 0.2, "cached_input_per_m": 0.04, "output_per_m": 0.8}, ...]
    is_multimodal BOOLEAN NOT NULL DEFAULT FALSE, -- 是否原生多模态（支持图片输入）；TRUE 的模型收到用户上传图片可直接进 content 数组原生理解，FALSE 维持先 OCR
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 文本模型单价
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('qwen-plus', 0.8, 2.0, 0.16)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('deepseek-flash', 2.0, 8.0, 0.04)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('deepseek-v4-pro', 9.0, 27.0, 0.3)
ON CONFLICT (model_name) DO NOTHING;
-- 阿里云百炼平台 deepseek-v4-flash-0731 是正式版，增加价格信息
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('deepseek-v4-flash-0731', 3.0, 9.0, 0.1)
ON CONFLICT (model_name) DO UPDATE SET
  input_price_per_m = EXCLUDED.input_price_per_m,
  output_price_per_m = EXCLUDED.output_price_per_m,
  cached_input_price_per_m = EXCLUDED.cached_input_price_per_m;

-- 视觉模型单价（qwen3.7-plus 为文本模型，见 src/api/video_gen.py 标注；3 个 vl 模型 is_multimodal=TRUE）
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('qwen3.7-plus', 2.0, 8.0, 0.4)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m, is_multimodal)
VALUES ('qwen-vl-max', 1.6, 4.0, 0.32, TRUE)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m, is_multimodal)
VALUES ('qwen-vl-plus', 0.8, 2.0, 0.16, TRUE)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m, is_multimodal)
VALUES ('qwen3-vl-flash',0.6, 6, 0.012, TRUE)    -- 按顶格 128K<Token≤256K 计算
ON CONFLICT (model_name) DO NOTHING;

-- GLM-5.3-Flash（zhipu 默认模型，2026-08-28 起；GLM-5 系列首个原生多模态模型，is_multimodal=TRUE）
-- 智谱官方定价：输入 0.8 元/M、输出 2.8 元/M；缓存命中价官方公布 0.23 元/M（2026-09-17 确认）
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m, is_multimodal)
VALUES ('GLM-5.3-Flash', 0.8, 2.8, 0.23, TRUE)
ON CONFLICT (model_name) DO NOTHING;

-- kimi-k3（Moonshot 视觉推理模型，weixin-cli 客户端代理端点白名单路由用，is_multimodal=TRUE）
-- 单价 2026-08-27 用户确认口径：输入 20 元/M、输出 100 元/M；缓存价未公布，按输入价计
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m, is_multimodal)
VALUES ('kimi-k3', 20.0, 100.0, 20.0, TRUE)
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

-- 公众号图片 VL 解析按张计费伪模型：0.01 元/张（×usage_factor 100 = 1 积分/张；运营改 DB 调价）
INSERT INTO token_cost_prices (model_name, price_per_call)
VALUES ('wechat_mp_image_parse', 0.01)
ON CONFLICT (model_name) DO NOTHING;

-- 分段计价模型单价：qwen3.7-flash 按单次请求输入 token 数分档
-- 档位（百炼官方）：0<T≤32K=输入0.2/输出0.8；32K<T≤256K=0.6/2.4；256K<T≤1M=1.2/4.8；显式缓存命中按输入价 10%（2026-08-16 修正，原 20% 高估成本一倍）
INSERT INTO token_cost_prices (model_name, tiered_pricing)
VALUES ('qwen3.7-flash', '[
  {"max_input": 32768,   "input_per_m": 0.2, "cached_input_per_m": 0.02, "output_per_m": 0.8},
  {"max_input": 262144,  "input_per_m": 0.6, "cached_input_per_m": 0.06, "output_per_m": 2.4},
  {"max_input": 1048576, "input_per_m": 1.2, "cached_input_per_m": 0.12, "output_per_m": 4.8}
]'::jsonb)
ON CONFLICT (model_name) DO NOTHING;

-- qwen3.8-flash（千问 Flash 新款，2026-09 用于本系统测试；无阶梯计价 0<T≤1M 单档）
-- 百炼官方华北2（北京）定价：输入 0.8 元/M、输出 2.7 元/M
-- 缓存走隐式缓存（自动前缀匹配，context_cache=false 不加 cache_control），命中按输入价 20% = 0.16 元/M
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('qwen3.8-flash', 0.8, 2.7, 0.16)
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
-- tenant_id：任务属主租户。''=公共用户（无租户上下文）或遗留未解析行；
-- 租户视图按 tenant_id 过滤时 '' 行 fail-closed 不可见。与 deploy/db_update.sql 对应增量节一致。
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id SERIAL PRIMARY KEY,
    task_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_tenant ON scheduled_tasks(tenant_id, status);

-- 定时任务执行日志表
-- tenant_id：随任务属主租户落库（''=公共用户或遗留未回填行），按任务链回填
CREATE TABLE IF NOT EXISTS scheduled_task_logs (
    id SERIAL PRIMARY KEY,
    log_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_scheduled_task_logs_tenant ON scheduled_task_logs(tenant_id, created_at DESC);

-- ============== 知识库表 ==============

-- 知识库分类表
CREATE TABLE IF NOT EXISTS knowledge_categories (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    display_name TEXT,
    parent_id INTEGER REFERENCES knowledge_categories(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uuid TEXT UNIQUE,
    UNIQUE(tenant_id, source_type)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_categories_tenant ON knowledge_categories(tenant_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_categories_parent ON knowledge_categories(tenant_id, parent_id);

-- 文档表
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    user_id TEXT,
    tenant_id TEXT,
    title TEXT,
    source_type TEXT,
    sub_category TEXT,
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
    uuid TEXT UNIQUE,
    -- 外部内容源（微信公众号等）扩展列（设计 §7.1；默认值即等价现状，存量数据无需回填）
    origin VARCHAR(32) NOT NULL DEFAULT 'manual_upload',
    external_id TEXT,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    expires_at TIMESTAMP
);

-- 旧表迁移兜底（CREATE TABLE IF NOT EXISTS 不会更新已存在的表）
ALTER TABLE documents ADD COLUMN IF NOT EXISTS origin VARCHAR(32) NOT NULL DEFAULT 'manual_upload';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS external_id TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_documents_sub_category ON documents(tenant_id, sub_category);
-- 外部内容源：来源身份去重（部分唯一）与检索过滤（active/未过期）
CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_origin_external
    ON documents(tenant_id, origin, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_documents_status ON documents(status, expires_at);

-- 文本块表
CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    doc_id INTEGER,
    chunk_index INTEGER,
    text TEXT,
    text_vec TSVECTOR,  -- 全文检索向量（由触发器自动维护，替代原 chunks_fts 表）
    tokens INTEGER,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uuid TEXT UNIQUE
);

-- 旧表迁移兜底（CREATE TABLE IF NOT EXISTS 不会更新已存在的表）
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS text_vec TSVECTOR;

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

-- 向量表（使用 pgvector）
CREATE TABLE IF NOT EXISTS chunks_vec (
    chunk_id INTEGER PRIMARY KEY,
    embedding vector(1024)
);

-- 创建向量索引（使用 HNSW 算法，支持余弦相似度搜索）
CREATE INDEX IF NOT EXISTS idx_chunks_vec_cosine ON chunks_vec USING hnsw (embedding vector_cosine_ops);

-- 全文检索：chunks.text_vec 由触发器自动维护（2026-08-14 从原 chunks_fts 表迁移而来）
CREATE INDEX IF NOT EXISTS idx_chunks_text_vec ON chunks USING GIN (text_vec);

-- 自动更新 text_vec 的触发器
CREATE OR REPLACE FUNCTION chunks_text_vec_update()
RETURNS TRIGGER AS $$
BEGIN
    NEW.text_vec := to_tsvector('simple', NEW.text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- PostgreSQL 不支持 CREATE TRIGGER IF NOT EXISTS，需先删除再创建
DROP TRIGGER IF EXISTS chunks_text_vec_trigger ON chunks;
CREATE TRIGGER chunks_text_vec_trigger
BEFORE INSERT OR UPDATE ON chunks
FOR EACH ROW EXECUTE FUNCTION chunks_text_vec_update();

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
    mode TEXT,  -- 渠道模式（如 wecom_personal_rpa 的 server/client），历史遗留列
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tenant_channel_configs_tenant ON tenant_channel_configs(tenant_id, channel_type);

-- wecom_personal_rpa 渠道单例约束：同 tenant 只能有一份该类型配置
-- （切换 server/client 模式 = 编辑现有配置，不允许创建第二条）
CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_channel_configs_wecom_personal_rpa
    ON tenant_channel_configs(tenant_id, channel_type)
    WHERE channel_type = 'wecom_personal_rpa';

-- wechat_mp 渠道（公众号内容入知识库 WP4）：同租户同 appid 唯一（设计 §4）
-- config 为 TEXT 列，jsonb 取值需显式 ::jsonb 转换
CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_channel_configs_wechat_mp_appid
    ON tenant_channel_configs(tenant_id, ((config::jsonb)->>'appid'))
    WHERE channel_type = 'wechat_mp';

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

-- 租户间知识库共享授权表（租户级授权：A -> B，整体授权，不涉及具体分类）
-- 具体共享哪些分类由第二步 subagent_knowledge_sources.sources 的 owner_tenant_id 决定
CREATE TABLE IF NOT EXISTS tenant_knowledge_shares (
    id SERIAL PRIMARY KEY,
    from_tenant_id TEXT NOT NULL,     -- 知识库提供租户（A）
    to_tenant_id TEXT NOT NULL,       -- 知识库接收租户（B）
    created_by TEXT,                  -- 创建人（平台管理员 user_id）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (from_tenant_id, to_tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_shares_to ON tenant_knowledge_shares(to_tenant_id);

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


-- =================== 系统级补充表（2026-08-14 全量审计补齐）===================
CREATE TABLE IF NOT EXISTS _db_update_applied (
    id TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS captchas (
    captcha_id TEXT NOT NULL,
    code TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (captcha_id)
);

CREATE TABLE IF NOT EXISTS channel_message_dedup (
    message_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (message_id)
);
CREATE INDEX IF NOT EXISTS idx_dedup_created_at ON channel_message_dedup USING btree (created_at);


-- =================== 渠道系统补充表（wecom_personal_rpa）===================
CREATE TABLE IF NOT EXISTS wecom_rpa_clients (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    name TEXT,
    encrypted_secret TEXT,
    status TEXT DEFAULT 'active' NOT NULL,
    min_version TEXT,
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    agent_base_url TEXT,
    listen_mode TEXT,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_clients_tenant ON wecom_rpa_clients USING btree (tenant_id);

CREATE TABLE IF NOT EXISTS wecom_rpa_accounts (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    client_id TEXT,
    display_name TEXT,
    status TEXT DEFAULT 'offline' NOT NULL,
    paused_reason TEXT,
    last_login_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    wecom_user_id TEXT,
    wecom_user_aliases TEXT[] DEFAULT '{}',
    identity_verified_at TIMESTAMP,
    identity_verified_by TEXT,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_accounts_tenant_client ON wecom_rpa_accounts USING btree (tenant_id, client_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wecom_rpa_accounts_tenant_wecom_user ON wecom_rpa_accounts USING btree (tenant_id, wecom_user_id) WHERE (wecom_user_id IS NOT NULL);

CREATE TABLE IF NOT EXISTS wecom_rpa_action_outbox (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    account_id TEXT NOT NULL,
    conversation_id TEXT,
    request_id TEXT NOT NULL,
    session_id TEXT,
    actions TEXT,
    status TEXT DEFAULT 'pending' NOT NULL,
    attempts INTEGER DEFAULT 0 NOT NULL,
    next_retry_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT,
    dedup_key TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reply_context JSONB,
    action_results JSONB DEFAULT '{}' NOT NULL,
    target_peer_id TEXT,
    reply_digest TEXT,
    reply_digests JSONB DEFAULT '[]' NOT NULL,
    send_started_at TIMESTAMP,
    UNIQUE (dedup_key),
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_outbox_recent_echo ON wecom_rpa_action_outbox USING btree (tenant_id, account_id, target_peer_id, completed_at DESC) WHERE (status = 'succeeded'::text);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_outbox_status_retry ON wecom_rpa_action_outbox USING btree (status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_outbox_tenant_account ON wecom_rpa_action_outbox USING btree (tenant_id, account_id);

CREATE TABLE IF NOT EXISTS wecom_rpa_archive_inbox (
    id SERIAL,
    tenant_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    envelope JSONB NOT NULL,
    status TEXT DEFAULT 'pending' NOT NULL,
    attempts INTEGER DEFAULT 0 NOT NULL,
    claim_token TEXT,
    error_message TEXT,
    next_retry_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_archive_inbox_claim ON wecom_rpa_archive_inbox USING btree (tenant_id, status, next_retry_at, created_at);

CREATE TABLE IF NOT EXISTS wecom_rpa_audit_logs (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    client_id TEXT,
    account_id TEXT,
    action_id TEXT,
    category TEXT,
    payload TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_audit_tenant_account ON wecom_rpa_audit_logs USING btree (tenant_id, account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_audit_tenant_category ON wecom_rpa_audit_logs USING btree (tenant_id, category, created_at DESC);

CREATE TABLE IF NOT EXISTS wecom_rpa_conversation_bindings (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    account_id TEXT NOT NULL,
    conversation_type TEXT,
    display_name TEXT,
    search_key TEXT NOT NULL,
    stable_id TEXT,
    status TEXT DEFAULT 'pending' NOT NULL,
    last_verified_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    monitor_user_names TEXT[] DEFAULT '{}',
    monitor_user_ids TEXT[] DEFAULT '{}',
    UNIQUE (account_id, search_key),
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_bindings_tenant_account ON wecom_rpa_conversation_bindings USING btree (tenant_id, account_id);


-- =================== SaaS 多租户补充表（子智能体定义 / 充值 / 桌面客户端）===================
CREATE TABLE IF NOT EXISTS subagent_definitions (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    version TEXT DEFAULT '1.0.0',
    author TEXT,
    triggers JSONB DEFAULT '{}',
    tools JSONB DEFAULT '{}',
    skills JSONB DEFAULT '{}',
    context JSONB DEFAULT '{}',
    delegatable_to JSONB DEFAULT '[]',
    allow_delegation BOOLEAN DEFAULT true,
    llm_provider JSONB,
    reply_style TEXT,
    business_pages JSONB,
    status TEXT DEFAULT 'active',
    created_by TEXT,
    updated_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    knowledge_sources JSONB DEFAULT '[]',
    chat_toolbar JSONB DEFAULT '[]',
    upload_accept TEXT,
    recap JSONB DEFAULT '{}',
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_subagent_def_agent_id ON subagent_definitions USING btree (agent_id);
CREATE INDEX IF NOT EXISTS idx_subagent_def_status ON subagent_definitions USING btree (status);

CREATE TABLE IF NOT EXISTS tenant_recharges (
    id SERIAL,
    tenant_id TEXT NOT NULL,
    amount_yuan NUMERIC(10,2) NOT NULL,
    credits INTEGER NOT NULL,
    rate INTEGER NOT NULL,
    source TEXT DEFAULT 'manual' NOT NULL,
    payment_order_id TEXT,
    operator_id TEXT,
    operator_name TEXT,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    balance_after NUMERIC(12,2),
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_tenant_recharges_created_at ON tenant_recharges USING btree (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_recharges_tenant_id ON tenant_recharges USING btree (tenant_id);

CREATE TABLE IF NOT EXISTS client_activation_codes (
    id SERIAL,
    code TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    client_name TEXT,
    status TEXT DEFAULT 'unused' NOT NULL,
    activated_at TIMESTAMP,
    activated_machine TEXT,
    expires_at TIMESTAMP,
    max_uses INTEGER DEFAULT 1,
    used_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (code),
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_client_activation_codes_tenant ON client_activation_codes USING btree (tenant_id);

CREATE TABLE IF NOT EXISTS client_bindings (
    id SERIAL,
    binding_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    activation_code_id INTEGER,
    client_name TEXT,
    machine_id TEXT,
    access_token TEXT NOT NULL,
    status TEXT DEFAULT 'active' NOT NULL,
    last_seen_at TIMESTAMP,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (access_token),
    UNIQUE (binding_id),
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_client_bindings_tenant ON client_bindings USING btree (tenant_id);
CREATE INDEX IF NOT EXISTS idx_client_bindings_token ON client_bindings USING btree (access_token);

CREATE TABLE IF NOT EXISTS client_usage_logs (
    id SERIAL,
    tenant_id TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    client_name TEXT,
    session_id TEXT,
    association_name TEXT,
    role TEXT,
    stage TEXT,
    status TEXT,
    model TEXT,
    provider TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    raw_credit_cost NUMERIC(12,2) DEFAULT 0,
    credit_cost NUMERIC(12,2) DEFAULT 0,
    error_code TEXT,
    detail TEXT,
    client_ref_id TEXT,                        -- C 模式标准上报幂等键（P4，客户端计费统一接入；tenant 内唯一）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_client_usage_logs_binding ON client_usage_logs USING btree (binding_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_client_usage_logs_tenant_ref
    ON client_usage_logs (tenant_id, client_ref_id) WHERE client_ref_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_client_usage_logs_status ON client_usage_logs USING btree (status, created_at);
CREATE INDEX IF NOT EXISTS idx_client_usage_logs_tenant ON client_usage_logs USING btree (tenant_id, created_at);


-- =================== 本地工具补充表（浏览器助手 / 本地设备）===================
CREATE TABLE IF NOT EXISTS local_tool_devices (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT,
    platform TEXT,
    runtime_version TEXT,
    token_hash TEXT NOT NULL,
    machine_fingerprint_hash TEXT,
    capabilities_json JSONB,
    manifest_digest TEXT,
    selected BOOLEAN DEFAULT false,
    status TEXT DEFAULT 'active' NOT NULL,
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE (token_hash)
);
CREATE INDEX IF NOT EXISTS idx_lt_devices_tenant_user ON local_tool_devices USING btree (tenant_id, user_id);

CREATE TABLE IF NOT EXISTS local_tool_pairing_tickets (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (code_hash),
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS local_tool_invocations (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    device_id UUID NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json JSONB NOT NULL,
    session_id TEXT,                            -- 触发本次调用的会话（客户端计费台账归属，2026-09-01 P2；无会话来源为 NULL）
    state TEXT DEFAULT 'queued' NOT NULL,
    effect TEXT,
    claim_token_hash TEXT,
    lease_expires_at TIMESTAMP,
    result_json JSONB,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    claimed_at TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    credit_cost NUMERIC(12,2),                  -- BOSS 本地工具按次计费实扣积分（成功时回写）
    provider_key TEXT,                          -- v2（desktop_automation）：受信 Provider 路由（NULL=旧聊天链路，任何设备可领）
    business_kind TEXT,                         -- v2 业务类型（'desktop_automation'；NULL=旧链路）
    business_ref JSONB,                         -- v2 业务引用（delivery_id/run_id/occurrence_id/scenario_key 等，不含场景正文）
    dedupe_key TEXT,                            -- v2 幂等键（tenant+business_kind 内唯一）
    deadline_at TIMESTAMPTZ,                    -- v2 操作截止
    authorization_epoch INTEGER,                -- v2 授权快照 epoch（许可事务复验）
    write_phase TEXT,                           -- 写动作阶段（许可发放后置 may_have_started）
    execution_lane TEXT DEFAULT 'standard' NOT NULL, -- 执行道（会话任务 'session_task' 仅定向 claim 可领；其余 'standard'，NULL 不存在）
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_lt_inv_device_state ON local_tool_invocations USING btree (device_id, state);
CREATE INDEX IF NOT EXISTS idx_lt_inv_tenant_user ON local_tool_invocations USING btree (tenant_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_lt_invocations_dedupe
    ON local_tool_invocations (tenant_id, business_kind, dedupe_key)
    WHERE business_kind IS NOT NULL AND dedupe_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lt_inv_provider ON local_tool_invocations USING btree (tenant_id, provider_key, state);
CREATE INDEX IF NOT EXISTS idx_lt_inv_lane_claim ON local_tool_invocations USING btree (tenant_id, device_id, state, execution_lane);

CREATE TABLE IF NOT EXISTS local_tool_events (
    id SERIAL,
    invocation_id UUID NOT NULL,
    tenant_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    stage TEXT,
    current INTEGER,
    total INTEGER,
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (invocation_id, seq),
    PRIMARY KEY (id)
);


-- =================== 桌面 CLI 无人值守自动任务底座（desktop_automation，P1-A 2026-09-08）===================
-- 规范：TIMESTAMPTZ/UUID；无外键无触发器（引用完整性 Python 校验）；tenant_id 一律 NOT NULL；
-- 任务族表 user_id NOT NULL；events/outbox/audit/quota 系统生成行 user_id 可 NULL。
-- 与 deploy/db_update.yaml（存量增量）和 src/desktop_automation/init_tables.py（代码侧幂等 DDL）三处同步。

CREATE TABLE IF NOT EXISTS desktop_automation_subjects (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    scenario_key TEXT NOT NULL,
    kind TEXT NOT NULL,                        -- task / revision
    ref TEXT NOT NULL,                         -- 业务引用值（task_ref/revision_ref 指向的值）
    version TEXT,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL,                      -- task: active/paused；revision: published/superseded
    active_revision_ref TEXT,                  -- task subject 专用
    authorization_epoch INTEGER DEFAULT 0 NOT NULL,
    task_ref TEXT,                             -- revision subject 专用（归属 task）
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, scenario_key, kind, ref)
);
CREATE INDEX IF NOT EXISTS idx_da_subjects_revision_task
    ON desktop_automation_subjects (tenant_id, scenario_key, task_ref)
    WHERE kind = 'revision';

CREATE TABLE IF NOT EXISTS desktop_automation_schedules (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    scenario_key TEXT NOT NULL,
    task_ref TEXT NOT NULL,
    revision_ref TEXT NOT NULL,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,                        -- time / event
    trigger_key TEXT NOT NULL,                 -- revision 内局部触发标识（time / event:{source}:{event_type}）
    timezone TEXT,
    anchor_at TIMESTAMPTZ,
    interval_seconds INTEGER,
    cron_expr TEXT,
    day_of_week TEXT,                          -- mon..sun
    next_fire_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    max_count INTEGER,
    run_count INTEGER DEFAULT 0 NOT NULL,
    grace_seconds INTEGER DEFAULT 0 NOT NULL,  -- 迟到宽限
    miss_policy TEXT DEFAULT 'skip_overlap' NOT NULL,
    one_shot BOOLEAN DEFAULT FALSE NOT NULL,
    consumed BOOLEAN DEFAULT FALSE NOT NULL,   -- 一次性 schedule 保存状态并保留行对账
    source_ref TEXT,                           -- kind=event 专用
    event_type TEXT,
    condition_ref TEXT,
    delay_seconds INTEGER,
    status TEXT DEFAULT 'active' NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, scenario_key, revision_ref, trigger_key)
);
CREATE INDEX IF NOT EXISTS idx_da_schedules_due
    ON desktop_automation_schedules (tenant_id, next_fire_at)
    WHERE kind = 'time' AND status = 'active' AND consumed = FALSE;
CREATE INDEX IF NOT EXISTS idx_da_schedules_event
    ON desktop_automation_schedules (tenant_id, source_ref, event_type)
    WHERE kind = 'event';

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
);

CREATE TABLE IF NOT EXISTS desktop_automation_events (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    source_id UUID NOT NULL,
    external_event_id TEXT NOT NULL,
    event_type TEXT,
    payload_ref TEXT,                          -- 受控租户存储引用（正文不进通用表）
    payload_hash TEXT,
    state TEXT DEFAULT 'received' NOT NULL,    -- received/processing/processed
    match_cursor INTEGER DEFAULT 0 NOT NULL,
    eligible_revision_refs JSONB,              -- 接纳事务快照固化的匹配候选集合
    occurred_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, source_id, external_event_id)
);
CREATE INDEX IF NOT EXISTS idx_da_events_state
    ON desktop_automation_events (tenant_id, state, created_at);

CREATE TABLE IF NOT EXISTS desktop_automation_occurrences (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    scenario_key TEXT NOT NULL,
    task_ref TEXT NOT NULL,
    revision_ref TEXT NOT NULL,
    user_id TEXT NOT NULL,
    trigger_kind TEXT NOT NULL,                -- time / event / manual
    trigger_key TEXT NOT NULL,                 -- R11 规范编码（time/event/manual，外部 ID 先哈希）
    scheduled_for TIMESTAMPTZ,
    due_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    status TEXT DEFAULT 'open' NOT NULL,       -- open / skipped
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, scenario_key, task_ref, trigger_key)
);
CREATE INDEX IF NOT EXISTS idx_da_occurrences_task
    ON desktop_automation_occurrences (tenant_id, scenario_key, task_ref, created_at);

CREATE TABLE IF NOT EXISTS desktop_automation_runs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    occurrence_id UUID NOT NULL,
    scenario_key TEXT NOT NULL,
    task_ref TEXT NOT NULL,
    revision_ref TEXT NOT NULL,
    user_id TEXT NOT NULL,
    state TEXT DEFAULT 'pending' NOT NULL,     -- pending/running/waiting_device + §5.4 终态
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
);
CREATE INDEX IF NOT EXISTS idx_da_runs_open
    ON desktop_automation_runs (tenant_id, scenario_key, task_ref)
    WHERE state NOT IN ('succeeded', 'failed', 'cancelled', 'partial', 'unknown', 'expired');
CREATE INDEX IF NOT EXISTS idx_da_runs_due
    ON desktop_automation_runs (due_at)
    WHERE state = 'pending';

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
    target_ref TEXT,                           -- 持久 subject 引用（与短期 target_handle 不可互换）
    target_handle TEXT,
    target_version TEXT,
    payload_ref TEXT,
    payload_hash TEXT,
    state TEXT DEFAULT 'pending' NOT NULL,     -- pending/dispatched/succeeded/failed/unknown/expired/skipped
    effect TEXT,                               -- none/applied/unknown（R10 delivery 聚合层）
    phase TEXT,                                -- prepared/may_have_started/verified/unknown（R10）
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    finished_at TIMESTAMPTZ,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, run_id, position)
);
CREATE INDEX IF NOT EXISTS idx_da_deliveries_run
    ON desktop_automation_deliveries (tenant_id, run_id, position);

CREATE TABLE IF NOT EXISTS desktop_automation_attempts (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    delivery_id UUID NOT NULL,
    run_id UUID,
    user_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    invocation_id UUID,                        -- invocation 唯一绑定
    permit_id UUID,
    request_id TEXT NOT NULL,
    effect TEXT,
    phase TEXT,
    safe_to_retry BOOLEAN,
    evidence_ref TEXT,
    result_ref TEXT,
    detail_json JSONB,
    predecessor_attempt_id UUID,               -- 人工重试链（证明未提交才允许新建 attempt）
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    finished_at TIMESTAMPTZ,
    PRIMARY KEY (id),
    UNIQUE (invocation_id),
    UNIQUE (tenant_id, delivery_id, attempt_no)
);

-- R27：写后验证证据登记——UNIQUE(tenant_id, evidence_ref) 事务级防复用；
-- INSERT ON CONFLICT 仲裁并发（败者绑定比对：同操作幂等放行/他操作拒绝）；
-- 迟到回执（R28）携带 applied/verified 证据时同样持久追加登记
CREATE TABLE IF NOT EXISTS desktop_automation_evidence (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    evidence_ref TEXT NOT NULL,                -- 场景证据命名空间内的受控引用
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
);
CREATE INDEX IF NOT EXISTS idx_da_evidence_attempt
    ON desktop_automation_evidence (tenant_id, attempt_id);

CREATE TABLE IF NOT EXISTS desktop_automation_audit_events (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    scenario_key TEXT,
    kind TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_ref TEXT NOT NULL,
    detail JSONB DEFAULT '{}' NOT NULL,        -- 只存受控引用/摘要，不存场景正文
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_da_audit_agg
    ON desktop_automation_audit_events (tenant_id, aggregate_type, aggregate_ref, id);

CREATE TABLE IF NOT EXISTS desktop_automation_outbox (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    kind TEXT NOT NULL,
    aggregate_ref TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,                  -- 确定性 dedupe（如 run:{occurrence_id}）
    state TEXT DEFAULT 'pending' NOT NULL,     -- pending/processing/done
    available_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    lease_expires_at TIMESTAMPTZ,
    attempt_count INTEGER DEFAULT 0 NOT NULL,
    payload_ref TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, kind, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_da_outbox_pending
    ON desktop_automation_outbox (state, available_at);

CREATE TABLE IF NOT EXISTS desktop_automation_quota_buckets (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    scope_type TEXT NOT NULL,                  -- tenant/task/target/account/resource（R9 固定顺序）
    scope_id TEXT NOT NULL,                    -- opaque
    bucket_start TIMESTAMPTZ NOT NULL,         -- floor(now/window)*window 对齐
    window_seconds INTEGER NOT NULL,
    limit_count INTEGER NOT NULL,
    reserved_count INTEGER DEFAULT 0 NOT NULL,
    used_count INTEGER DEFAULT 0 NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, scope_type, scope_id, bucket_start)
);

-- 写动作许可（短期一次性；token 只存 hash，明文仅签发响应返回一次）
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
    resource_key TEXT,                         -- R13 桌面资源标识（sha256(device|win_user|session)）
    quota_reservation JSONB,                   -- R9 预留凭据（落账/释放定位）
    state TEXT DEFAULT 'issued' NOT NULL,      -- issued/consumed/expired
    permit_token_hash TEXT NOT NULL,
    deadline TIMESTAMPTZ NOT NULL,
    issued_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    consumed_at TIMESTAMPTZ,
    expired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_lt_permits_invocation_request
    ON local_tool_operation_permits (tenant_id, invocation_id, request_id);
CREATE INDEX IF NOT EXISTS idx_lt_permits_delivery
    ON local_tool_operation_permits (tenant_id, delivery_id);
CREATE INDEX IF NOT EXISTS idx_lt_permits_expire
    ON local_tool_operation_permits (state, deadline);


-- =================== 视频生成补充表 ===================
CREATE TABLE IF NOT EXISTS gen_sessions (
    session_id TEXT NOT NULL,
    tenant_id TEXT,
    user_id TEXT,
    scene_id TEXT NOT NULL,
    product_image_fid TEXT NOT NULL,
    model_image_fid TEXT,
    copywriting TEXT NOT NULL,
    expanded_prompt TEXT,
    card_count INTEGER DEFAULT 3 NOT NULL,
    status TEXT DEFAULT 'generating' NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    enable_ai_label BOOLEAN DEFAULT true NOT NULL,
    duration_sec INTEGER DEFAULT 10 NOT NULL,
    resolution TEXT DEFAULT '720P' NOT NULL,
    ratio TEXT DEFAULT '9:16' NOT NULL,
    PRIMARY KEY (session_id)
);

CREATE TABLE IF NOT EXISTS gen_cards (
    card_id TEXT NOT NULL,
    tenant_id TEXT,
    session_id TEXT NOT NULL,
    variant_idx INTEGER NOT NULL,
    seed BIGINT,
    variant_prompt TEXT,
    provider_task_id TEXT,
    provider_status TEXT,
    output_fid TEXT,
    output_url TEXT,
    output_duration INTEGER,
    kept BOOLEAN DEFAULT false NOT NULL,
    parent_card_id TEXT,
    error_msg TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (card_id)
);
CREATE INDEX IF NOT EXISTS idx_gen_cards_polling ON gen_cards USING btree (provider_status);
CREATE INDEX IF NOT EXISTS idx_gen_cards_session ON gen_cards USING btree (session_id);

CREATE TABLE IF NOT EXISTS asset_library (
    id SERIAL,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    file_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    source TEXT NOT NULL,
    scene TEXT,
    width INTEGER,
    height INTEGER,
    portrait_authorized BOOLEAN DEFAULT false,
    portrait_auth_expire_at TIMESTAMP,
    portrait_auth_scope TEXT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_asset_library_source ON asset_library USING btree (source);
CREATE INDEX IF NOT EXISTS idx_asset_library_tenant ON asset_library USING btree (tenant_id);
CREATE INDEX IF NOT EXISTS idx_asset_library_tenant_scene ON asset_library USING btree (tenant_id, scene);

CREATE TABLE IF NOT EXISTS prompt_library (
    id SERIAL,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    category TEXT NOT NULL,
    business_prompt TEXT NOT NULL,
    craft_prompt TEXT NOT NULL,
    model_params JSONB,
    industry_tag TEXT,
    scene_tag TEXT,
    source_video_file_id TEXT,
    source_chat_session_id TEXT,
    dislike_reason TEXT,
    promoted_from_kept_id INTEGER,
    promoted_by_user_id TEXT,
    promoted_at TIMESTAMP,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_prompt_library_tenant_category ON prompt_library USING btree (tenant_id, category);
CREATE INDEX IF NOT EXISTS idx_prompt_library_tenant_scene ON prompt_library USING btree (tenant_id, scene_tag);


-- =================== 社媒运营补充表（social_media 智能体）===================
CREATE TABLE IF NOT EXISTS social_accounts (
    account_id TEXT NOT NULL,
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
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_accounts_unique ON social_accounts USING btree (tenant_id, platform, external_account_id);

CREATE TABLE IF NOT EXISTS social_media_assets (
    asset_id TEXT NOT NULL,
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (asset_id)
);

CREATE TABLE IF NOT EXISTS social_content_masters (
    master_id TEXT NOT NULL,
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
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (master_id)
);

CREATE TABLE IF NOT EXISTS social_content_items (
    item_id TEXT NOT NULL,
    tenant_id TEXT,
    plan_id TEXT,
    topic TEXT,
    objective TEXT,
    planned_at TIMESTAMP,
    timezone TEXT,
    owner_user_id TEXT,
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (item_id)
);

CREATE TABLE IF NOT EXISTS social_content_variants (
    variant_id TEXT NOT NULL,
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
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (variant_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_variants_revision ON social_content_variants USING btree (tenant_id, variant_id, revision);

CREATE TABLE IF NOT EXISTS social_content_plans (
    plan_id TEXT NOT NULL,
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
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (plan_id)
);

CREATE TABLE IF NOT EXISTS social_content_asset_links (
    link_id TEXT NOT NULL,
    tenant_id TEXT,
    master_id TEXT,
    asset_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (link_id)
);

CREATE TABLE IF NOT EXISTS social_publish_jobs (
    job_id TEXT NOT NULL,
    tenant_id TEXT,
    account_id TEXT,
    variant_id TEXT,
    variant_revision INTEGER,
    content_hash TEXT,
    publish_mode TEXT,
    scheduled_at TIMESTAMP,
    timezone TEXT,
    status TEXT DEFAULT 'draft',
    idempotency_key TEXT,
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
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (idempotency_key),
    PRIMARY KEY (job_id)
);
CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_due ON social_publish_jobs USING btree (status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_external_task ON social_publish_jobs USING btree (external_task_id);
CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_lease ON social_publish_jobs USING btree (lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_tenant_account ON social_publish_jobs USING btree (tenant_id, account_id, created_at);

CREATE TABLE IF NOT EXISTS social_publish_attempts (
    attempt_id TEXT NOT NULL,
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
    duration_ms INTEGER,
    PRIMARY KEY (attempt_id)
);

CREATE TABLE IF NOT EXISTS social_published_contents (
    published_id TEXT NOT NULL,
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (published_id)
);

CREATE TABLE IF NOT EXISTS social_metric_snapshots (
    snapshot_id TEXT NOT NULL,
    tenant_id TEXT,
    account_id TEXT,
    published_id TEXT,
    metric_date DATE,
    metric_definition TEXT,
    normalized_metrics_json JSONB,
    raw_metrics_json JSONB,
    source_type TEXT,
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    import_batch_id TEXT,
    PRIMARY KEY (snapshot_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_metric_snapshots_unique ON social_metric_snapshots USING btree (tenant_id, account_id, published_id, metric_date, metric_definition);

CREATE TABLE IF NOT EXISTS social_review_records (
    review_id TEXT NOT NULL,
    tenant_id TEXT,
    variant_id TEXT,
    variant_revision INTEGER,
    content_hash TEXT,
    decision TEXT,
    comment TEXT,
    reviewer_user_id TEXT,
    reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (review_id)
);

CREATE TABLE IF NOT EXISTS social_data_import_batches (
    batch_id TEXT NOT NULL,
    tenant_id TEXT,
    account_id TEXT,
    platform TEXT,
    template_version TEXT,
    file_digest TEXT,
    status TEXT DEFAULT 'uploaded',
    error_summary TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (batch_id)
);


-- =================== 工作报告补充表 ===================
CREATE TABLE IF NOT EXISTS work_daily_reports (
    id SERIAL,
    report_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    report_type TEXT DEFAULT 'daily' NOT NULL,
    target_user_id TEXT,
    report_date DATE NOT NULL,
    metrics JSONB DEFAULT '{}' NOT NULL,
    summary_text TEXT,
    highlights JSONB,
    suggestions JSONB,
    model TEXT,
    token_cost INTEGER DEFAULT 0,
    credit_cost NUMERIC(12,2) DEFAULT 0,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    regenerated_count INTEGER DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE (report_id),
    UNIQUE (tenant_id, scope, report_type, target_user_id, report_date)
);
CREATE INDEX IF NOT EXISTS idx_work_daily_reports_tenant_date ON work_daily_reports USING btree (tenant_id, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_work_daily_reports_tenant_scope_type_date ON work_daily_reports USING btree (tenant_id, scope, report_type, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_work_daily_reports_user_date ON work_daily_reports USING btree (target_user_id, report_date DESC) WHERE (scope = 'personal'::text);

CREATE TABLE IF NOT EXISTS work_outcomes (
    id SERIAL,
    outcome_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    subagent_id TEXT,
    session_id TEXT,
    channel TEXT,
    summary TEXT NOT NULL,
    outcome_type TEXT DEFAULT 'other' NOT NULL,
    importance TEXT DEFAULT 'normal' NOT NULL,
    file_id TEXT,
    file_name TEXT,
    file_path TEXT,
    metadata JSONB DEFAULT '{}' NOT NULL,
    source TEXT DEFAULT 'cp_realtime' NOT NULL,
    chat_record_id BIGINT,
    review_batch_id TEXT,
    review_confidence REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (outcome_id),
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_file_id ON work_outcomes USING btree (file_id) WHERE (file_id IS NOT NULL);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_review_batch ON work_outcomes USING btree (review_batch_id) WHERE (review_batch_id IS NOT NULL);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_session ON work_outcomes USING btree (session_id);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_subagent_created ON work_outcomes USING btree (tenant_id, subagent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_tenant_created ON work_outcomes USING btree (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_user_created ON work_outcomes USING btree (tenant_id, user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS work_report_preferences (
    id SERIAL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    personal_report_enabled BOOLEAN DEFAULT true,
    personal_report_types TEXT[] DEFAULT ARRAY['daily'] NOT NULL,
    personal_push_channels TEXT[],
    personal_push_time TIME DEFAULT '18:00:00',
    team_report_enabled BOOLEAN DEFAULT false,
    team_report_types TEXT[] DEFAULT ARRAY['daily'] NOT NULL,
    team_push_channels TEXT[],
    team_push_time TIME DEFAULT '19:00:00',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, user_id)
);

-- =================== 招聘操作智能体（recruiting-operator）===================
-- 简历库：保存从 BOSS 直聘 CLI 采集的候选人简历（截图图片 file_id 引用、OCR 全文、
-- 基本信息 JSONB、关联职位、获取日期）。与 deploy/db_update.sql 2026-08-16 条目保持一致。
CREATE TABLE IF NOT EXISTS bs_recruiting_operator_resumes (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    candidate_name TEXT,
    job_name TEXT,
    candidate_info JSONB,
    images JSONB NOT NULL DEFAULT '[]'::jsonb,
    ocr_text TEXT,
    source TEXT NOT NULL DEFAULT 'boss',
    status TEXT NOT NULL DEFAULT 'new',
    remark TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bs_ror_tenant ON bs_recruiting_operator_resumes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_bs_ror_tenant_job ON bs_recruiting_operator_resumes(tenant_id, job_name);
CREATE INDEX IF NOT EXISTS idx_bs_ror_tenant_fetched ON bs_recruiting_operator_resumes(tenant_id, fetched_at);

-- =================== 微信客服引流（wecom_kf 客服账号引流）===================
-- 2026-08-18：C端客户→引流员工 first-touch 归因表。enter_session 事件按 scene 反查
-- 绑定的引流员工后写入；UNIQUE(customer_user_id) + ON CONFLICT DO NOTHING 保证一个
-- C端客户只归属第一个扫码的引流员工。与 deploy/db_update.sql 2026-08-18 条目保持一致。
CREATE TABLE IF NOT EXISTS customer_referrals (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,                    -- 租户隔离
    referrer_user_id TEXT,             -- 引流租户员工 user_id
    customer_user_id TEXT,             -- C端客户 user_id
    open_kfid TEXT,                    -- 客服账号 ID（追溯来源）
    scene TEXT,                        -- 场景值（追溯来源）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_customer_referrals_customer UNIQUE (customer_user_id)
);
CREATE INDEX IF NOT EXISTS idx_customer_referrals_tenant ON customer_referrals(tenant_id);
CREATE INDEX IF NOT EXISTS idx_customer_referrals_referrer ON customer_referrals(tenant_id, referrer_user_id);

-- =================== 客户留资线索（pre-sales 售前咨询留资）===================
-- 2026-08-21：售前咨询客户留资线索表（lead_capture 能力级中性命名，跨智能体复用）。
-- 手机号加密落库（src/db/encryption.py），列表/详情接口解密返回（权限内），日志不打印明文。
-- 与 src/saas/db/tables.py init_saas_tables() / deploy/db_update.sql 2026-08-21 条目保持一致。
CREATE TABLE IF NOT EXISTS bs_lead_capture_leads (
    id SERIAL PRIMARY KEY,
    lead_id TEXT UNIQUE NOT NULL,          -- lead_lc_<uuid12>
    tenant_id TEXT NOT NULL,               -- 租户隔离
    user_id TEXT,                          -- 租户侧注册用户（ensure_user_registered 生成）
    customer_user_id TEXT,                 -- 微信侧 external_userid（老客户识别）
    channel_chat_id TEXT,                  -- open_kfid（来源客服账号）
    kf_account_name TEXT,                  -- 客服账号名快照
    contact_method TEXT,                   -- phone | qr
    phone TEXT,                            -- 手机号（加密存储）
    contact_name TEXT,                     -- 客户姓名（对话中抽取，可选）
    demand_summary TEXT,                   -- 需求摘要（对话中抽取，可选）
    source TEXT DEFAULT 'lead_capture',    -- 固定来源标识
    stage TEXT DEFAULT 'new',              -- new | contacting | converted | abandoned
    assigned_to TEXT,                      -- 归属员工 user_id（= kf_account.tenant_user_id 快照）
    assignee_name TEXT,                    -- 归属员工姓名快照
    transferred_to TEXT,                   -- 留资后若转人工，记录 servicer_userid（最近一次转人工，transfer_to_human 工具回写）
    servicer_name TEXT,                    -- 最近一次转人工的企微员工姓名（映射不到为空）
    last_human_transfer_at TIMESTAMP,      -- 最近一次转人工时间
    intent_level TEXT,                     -- 客户意向度 high | medium | low（lead_refresh LLM 判定）
    intent_reason TEXT,                    -- 意向度判定依据（一句话，供运营理解）
    demand_points JSONB,                   -- 客户需求分条（字符串数组，lead_refresh LLM 产出）
    last_analyzed_message_id TEXT,         -- 分析游标：最后参与分析的消息 id（Phase 2 增量裁剪预留）
    session_id TEXT,                       -- 产生线索的渠道会话
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lc_leads_tenant ON bs_lead_capture_leads(tenant_id);
CREATE INDEX IF NOT EXISTS idx_lc_leads_created ON bs_lead_capture_leads(created_at);
CREATE INDEX IF NOT EXISTS idx_lc_leads_assigned ON bs_lead_capture_leads(assigned_to);
CREATE INDEX IF NOT EXISTS idx_lc_leads_customer ON bs_lead_capture_leads(customer_user_id);

-- =================== 微信营销自动化（weixin-marketing，P2）===================
-- 7 张业务表（R40）：automations/revisions/content_blocks/group_bindings/
-- account_bindings/audit_events/assets。与 src/weixin_marketing/init_tables.py 幂等 DDL
-- 双轨同步（init_database 挂接）；发布后 revisions/content_blocks 不可变；
-- 无外键无触发器（引用完整性在 Python 校验）；新业务表不进 db_update.yaml。
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
);
CREATE INDEX IF NOT EXISTS idx_bs_wxm_automations_tenant_status ON bs_weixin_marketing_automations (tenant_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_bs_wxm_automations_tenant_user ON bs_weixin_marketing_automations (tenant_id, user_id, created_at DESC);

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
);
CREATE INDEX IF NOT EXISTS idx_bs_wxm_revisions_automation ON bs_weixin_marketing_revisions (tenant_id, automation_id, revision_no DESC);

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
);

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
);
CREATE INDEX IF NOT EXISTS idx_bs_wxm_group_bindings_tenant_user ON bs_weixin_marketing_group_bindings (tenant_id, user_id, state);
CREATE INDEX IF NOT EXISTS idx_bs_wxm_group_bindings_account ON bs_weixin_marketing_group_bindings (tenant_id, account_binding_id);

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
);
CREATE INDEX IF NOT EXISTS idx_bs_wxm_account_bindings_tenant_user ON bs_weixin_marketing_account_bindings (tenant_id, user_id, status);

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
);
CREATE INDEX IF NOT EXISTS idx_bs_wxm_audit_tenant_automation ON bs_weixin_marketing_audit_events (tenant_id, automation_id, id);
CREATE INDEX IF NOT EXISTS idx_bs_wxm_audit_run ON bs_weixin_marketing_audit_events (tenant_id, run_id, id);

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
);
CREATE INDEX IF NOT EXISTS idx_bs_wxm_assets_tenant_hash ON bs_weixin_marketing_assets (tenant_id, sha256);

-- =================== 微信营销自动化 API 幂等键（weixin-marketing，P2-A2）===================
-- R46 Idempotency-Key 存储：基础设施表（非 bs_ 业务表，同 desktop_agent_turn_requests
-- 先例），scope=(tenant_id, user_id, route, idempotency_key) 唯一；request_digest
-- 为「路径+请求体规范化 JSON」摘要（同 key 异 payload 检测）；仅存响应 JSON 与
-- 状态码，不存业务正文。与 src/weixin_marketing/api.py _IDEMPOTENCY_DDL 逐语句
-- 一致（api 模块幂等自建，此处为部署基线）；不进 db_update.yaml。
CREATE TABLE IF NOT EXISTS weixin_marketing_idempotency_keys (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    route TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    response_json JSONB,
    status_code INTEGER,
    status TEXT DEFAULT 'pending' NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE (tenant_id, user_id, route, idempotency_key)
);

-- =================== 微信营销自动化事件闭环（weixin-marketing，P4-B）===================
-- 事件源签名密钥/nonce/payload 与内部事件示例业务表（基础设施+示例表，非 bs_ 业务
-- 表，同幂等键表先例：模块幂等自建，此处为部署基线；不进 db_update.yaml）。
-- 与 src/weixin_marketing/event_sources.py _TABLES_DDL 及 internal_event_example.py
-- _EXAMPLE_DDL 逐语句一致：
-- - event_source_keys：webhook 源 HMAC 密钥版本行（Fernet 密文；rotate 时旧 active
--   → retiring + retire_at 并行窗，窗口内旧新均可验签）；UNIQUE(source_id,key_id)
--   与 UNIQUE(source_id,key_version) 仲裁并发轮换。
-- - webhook_nonces：nonce 防重放（UNIQUE(source_id,nonce)；消耗与事件接纳同事务，
--   TTL 900s 由 event_match_tick 清理）。
-- - event_payloads：受控事件 payload 持久化（tenant+hash 去重复用；events 行只存
--   payload_ref/hash，正文不进底座通用表）。
-- - example_orders：内部事件示例业务对象（P5 后真实业务事件源参照；源侧 outbox
--   复用 desktop_automation_outbox，不另建 outbox 表）。
CREATE TABLE IF NOT EXISTS weixin_marketing_event_source_keys (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    source_id UUID NOT NULL,
    key_id TEXT NOT NULL,
    key_version INTEGER NOT NULL,
    encrypted_secret TEXT NOT NULL,
    status TEXT DEFAULT 'active' NOT NULL,
    retire_at TIMESTAMPTZ,
    created_by TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (source_id, key_id),
    UNIQUE (source_id, key_version)
);
CREATE TABLE IF NOT EXISTS weixin_marketing_webhook_nonces (
    id BIGSERIAL PRIMARY KEY,
    source_id UUID NOT NULL,
    nonce TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE (source_id, nonce)
);
CREATE TABLE IF NOT EXISTS weixin_marketing_event_payloads (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    source_id UUID,
    payload_hash TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, payload_hash)
);
CREATE TABLE IF NOT EXISTS weixin_marketing_example_orders (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    label TEXT,
    status TEXT DEFAULT 'pending' NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);

-- 输出初始化完成信息
DO $$
BEGIN
    RAISE NOTICE 'PostgreSQL database initialized successfully with pgvector extension';
END $$;

-- ============================================================
-- 端侧会话任务 C1（session_tasks 系统表族；与 src/session_tasks/init_tables.py
-- 及 deploy/db_update.yaml 2026-09-12 22:30:00 块逐语句一致）
-- ============================================================

CREATE TABLE IF NOT EXISTS session_tasks (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    scenario_key TEXT NOT NULL,
    device_id UUID NOT NULL,
    account_binding_id UUID NOT NULL,
    conversation_binding_id UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    version INTEGER NOT NULL DEFAULT 1,
    draft_spec_text_id UUID,
    draft_digest TEXT,
    spec_revision INTEGER,
    current_spec_id UUID,
    control_epoch INTEGER NOT NULL DEFAULT 0,
    server_control_seq INTEGER NOT NULL DEFAULT 0,
    completion_reason TEXT,
    blocked_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_session_tasks_occupancy
    ON session_tasks (tenant_id, device_id, account_binding_id, conversation_binding_id)
    WHERE status IN ('active', 'paused', 'human_required', 'blocked');
CREATE INDEX IF NOT EXISTS idx_session_tasks_owner
    ON session_tasks (tenant_id, user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS session_task_specs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    revision INTEGER NOT NULL,
    spec_text_id UUID,
    limits_json TEXT NOT NULL,
    work_window_json TEXT,
    published_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, task_id, revision)
);
CREATE TABLE IF NOT EXISTS session_task_assignments (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    device_id UUID NOT NULL,
    runtime_instance_id TEXT NOT NULL,
    fence INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    is_current BOOLEAN DEFAULT TRUE NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    claimed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    control_epoch_at_claim INTEGER NOT NULL DEFAULT 0,
    acked_local_seq INTEGER NOT NULL DEFAULT 0,
    server_control_seq INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_session_task_assignments_current
    ON session_task_assignments (tenant_id, task_id)
    WHERE is_current = TRUE;
CREATE TABLE IF NOT EXISTS session_task_events (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    assignment_id UUID NOT NULL,
    local_seq INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    payload_text_id UUID,
    received_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, assignment_id, local_seq),
    UNIQUE (tenant_id, event_id)
);
CREATE TABLE IF NOT EXISTS session_task_messages (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    conversation_binding_id TEXT NOT NULL,
    binding_version INTEGER NOT NULL DEFAULT 0,
    input_version INTEGER NOT NULL,
    message_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    text_id UUID,
    evidence_ref TEXT,
    batch_id TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, task_id, message_id)
);
CREATE TABLE IF NOT EXISTS session_task_batches (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    batch_id TEXT NOT NULL,
    input_version INTEGER NOT NULL,
    message_ids_json TEXT,
    observation_id TEXT,
    synthetic BOOLEAN DEFAULT FALSE NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, task_id, batch_id)
);
CREATE TABLE IF NOT EXISTS session_task_decisions (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    spec_revision INTEGER NOT NULL,
    batch_id TEXT NOT NULL,
    decision_kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    input_version INTEGER NOT NULL DEFAULT 0,
    reply_text_id UUID,
    reply_text_hash TEXT,
    completion_evidence TEXT,
    model_call_ref TEXT,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    action TEXT,                                -- C3：冻结的模型动作（reply/wait/handoff/done）
    failure_code TEXT,                          -- C3：failed 决策稳定失败原因
    model_attempts INTEGER DEFAULT 0 NOT NULL,  -- C3：实际模型调用次数（超时重试/修复各计一次）
    model_call_pending BOOLEAN DEFAULT FALSE NOT NULL, -- C3：模型调用已发起未确认结束（占用槽位，supersede/过期不释放）
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, task_id, spec_revision, batch_id, decision_kind)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_session_task_decisions_opening
    ON session_task_decisions (tenant_id, task_id)
    WHERE decision_kind = 'opening';
CREATE INDEX IF NOT EXISTS idx_session_task_decisions_worker ON session_task_decisions USING btree (status, created_at);
CREATE TABLE IF NOT EXISTS session_task_decision_attempts (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    decision_id UUID NOT NULL,
    attempt_ref TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'reserved',          -- C3：reserved|started|returned|billing_pending|billed|released
    usage_json TEXT,                                  -- D1：返回时持久化的实际 token 用量（计费补偿输入）
    credit_cost NUMERIC(14,4),                        -- D1：计算的积分金额
    result_text_id UUID,
    billing_retry_at TIMESTAMPTZ,
    billing_retry_count INTEGER DEFAULT 0,
    billing_key TEXT,                                 -- D1：稳定账务幂等键（st-decision:<attempt_ref>）
    model TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, attempt_ref)
);
CREATE INDEX IF NOT EXISTS idx_session_task_attempts_decision ON session_task_decision_attempts USING btree (tenant_id, task_id, decision_id);
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS billing_ref TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_records_billing_ref ON chat_records (tenant_id, billing_ref) WHERE billing_ref IS NOT NULL;
ALTER TABLE session_task_decision_attempts ADD COLUMN IF NOT EXISTS user_id TEXT;
CREATE TABLE IF NOT EXISTS session_task_execution_links (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    decision_id UUID NOT NULL,
    occurrence_id UUID,
    run_id UUID,
    delivery_id UUID,
    invocation_id UUID,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, decision_id)
);
CREATE TABLE IF NOT EXISTS session_task_texts (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    purpose TEXT NOT NULL,
    encrypted_payload TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, task_id, id)
);
CREATE TABLE IF NOT EXISTS session_task_confirmations (
    confirmation_id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    spec_revision INTEGER NOT NULL,
    spec_digest TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    published_revision INTEGER,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (confirmation_id)
);
CREATE INDEX IF NOT EXISTS idx_session_task_confirmations_task
    ON session_task_confirmations (tenant_id, task_id, created_at DESC);
CREATE TABLE IF NOT EXISTS session_task_cost_reservations (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    purpose TEXT NOT NULL,
    ref_key TEXT NOT NULL,
    amount NUMERIC(14,4) NOT NULL,
    state TEXT NOT NULL DEFAULT 'reserved',
    settled_amount NUMERIC(14,4),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, task_id, purpose, ref_key)
);
-- 模块接口幂等（业务配套表；模块 init 自建，不进 db_update.yaml，全量基线保留）
CREATE TABLE IF NOT EXISTS session_tasks_idempotency_keys (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    route TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    response_json TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE (tenant_id, user_id, route, idempotency_key)
);
-- 微信会话绑定（bs_ 业务表；src/weixin_conversation/init_tables.py 同源）
CREATE TABLE IF NOT EXISTS bs_weixin_conversation_bindings (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    device_id UUID NOT NULL,
    account_binding_id UUID NOT NULL,
    conversation_type TEXT NOT NULL,
    conversation_label TEXT,
    identity_version INTEGER NOT NULL DEFAULT 0,
    verification_status TEXT NOT NULL DEFAULT 'pending',
    encrypted_identity_evidence TEXT,
    verifier_version TEXT,
    verified_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS idx_bs_wx_conv_bindings_owner
    ON bs_weixin_conversation_bindings (tenant_id, user_id, device_id, conversation_type);


-- C4 owner-only in-app notices
CREATE TABLE IF NOT EXISTS session_task_notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    user_id TEXT NOT NULL,
    control_epoch INTEGER NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, task_id, control_epoch),
    FOREIGN KEY (tenant_id, task_id) REFERENCES session_tasks (tenant_id, id) ON DELETE CASCADE
);

-- B1.2 通用控制请求表（设计 §5.5.5 冻结 schema；human_required 异步迁移，
-- 只负责异步迁移不是同步发送门禁；处理失败保持 binding 阻断，无自动恢复路径）
CREATE TABLE IF NOT EXISTS session_task_control_requests (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL,
    expected_control_epoch INTEGER NOT NULL,
    expected_block_epoch INTEGER NOT NULL,
    reason TEXT NOT NULL,
    source_type TEXT NOT NULL,       -- permit_denied | rate_settlement
    source_ref TEXT NOT NULL,        -- 受控 delivery/invocation 引用，不存敏感正文
    status TEXT NOT NULL DEFAULT 'pending', -- pending | processing | applied | stale | failed
    processing_owner TEXT,
    processing_lease_expires_at TIMESTAMPTZ,
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CHECK (status IN ('pending','processing','applied','stale','failed')),
    CHECK (retry_count >= 0)
);
CREATE INDEX IF NOT EXISTS idx_session_task_control_requests_scan
    ON session_task_control_requests (status, created_at);
ALTER TABLE session_task_control_requests
    DROP CONSTRAINT IF EXISTS session_task_control_requests_tenant_id_task_id_expected_co_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_session_task_control_requests_idem
    ON session_task_control_requests (tenant_id, task_id, expected_control_epoch, expected_block_epoch, reason);

-- ============================================================================
-- 微信公众号内容入知识库（bs_ 业务表；src/wechat_mp/db.py 同源，设计 §8 二审定稿）
-- 队列语义：sync_runs + sync_items 即执行队列；受理即建 queued run + pending items；
-- articles 只维护文章当前状态；events 是回调收件箱（先落库再返回 success）。
-- ============================================================================

-- 文章当前状态（唯一当前态记录）
CREATE TABLE IF NOT EXISTS bs_wechat_mp_articles (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    config_id TEXT,                          -- 可空：手动粘贴无配置
    external_id TEXT NOT NULL,               -- 规范身份（设计 §5.4）
    original_url TEXT,
    fetch_url TEXT,
    source_channel VARCHAR(16) NOT NULL,     -- callback/manual/freepublish
    title TEXT,
    publish_time TIMESTAMP,
    wx_update_time TIMESTAMP,
    content_hash VARCHAR(64),
    doc_id INTEGER,                          -- 关联 documents.id
    sub_category VARCHAR(64),
    tags JSONB,
    status VARCHAR(16) NOT NULL DEFAULT 'active',   -- active/missing/deleted/alias/unconfirmed
    master_article_row_id BIGINT,            -- alias 行指向主记录
    processing_status VARCHAR(16) DEFAULT 'pending',-- pending/success/sync_failed/deferred
    pipeline_version TEXT,
    next_retry_at TIMESTAMP,                 -- 失败退避
    error_message TEXT,                      -- 最近一次失败原因（脱敏后）
    image_count INT DEFAULT 0,
    image_parsed_count INT DEFAULT 0,
    last_synced_at TIMESTAMP,
    last_checked_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT now(),
    UNIQUE(tenant_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_bs_wechat_mp_articles_retry
    ON bs_wechat_mp_articles(tenant_id, processing_status, next_retry_at);

-- 回调事件收件箱（可靠接收：先落库再返回 success）
CREATE TABLE IF NOT EXISTS bs_wechat_mp_events (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    event_key TEXT NOT NULL,                 -- MsgID+Event 等幂等键
    run_id BIGINT,                           -- 关联受理批次
    payload JSONB,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending/done/failed
    received_at TIMESTAMP DEFAULT now(),
    processed_at TIMESTAMP,
    error_message TEXT,
    UNIQUE(tenant_id, config_id, event_key)  -- 组合键，跨配置不碰撞
);
CREATE INDEX IF NOT EXISTS idx_bs_wechat_mp_events_status
    ON bs_wechat_mp_events(status, received_at);

-- 同步运行账本 + 执行队列（queued→running→终态；受理即建 queued run 与 pending items）
CREATE TABLE IF NOT EXISTS bs_wechat_mp_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    config_id TEXT,
    user_id TEXT,                            -- 操作者；后台触发可空
    created_at TIMESTAMP DEFAULT now(),
    agent_id TEXT,
    session_id TEXT,                         -- agent 触发时记录可信运行时身份；不保存对话正文
    owner_token TEXT,
    heartbeat_at TIMESTAMP,
    trigger_type VARCHAR(16) NOT NULL,       -- callback/scheduled/manual/agent/retry/recheck
    status VARCHAR(32) NOT NULL,             -- queued/running/success/partial_failed/skipped_no_credit/failed/interrupted
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
);
-- 租户级串行：每租户至多一条 running（与 Redis 锁同粒度）；queued 不限条数，受理即排队
CREATE UNIQUE INDEX IF NOT EXISTS uq_wechat_mp_runs_active
    ON bs_wechat_mp_sync_runs(tenant_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_bs_wechat_mp_sync_runs_tenant_created
    ON bs_wechat_mp_sync_runs(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS bs_wechat_mp_sync_items (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    created_at TIMESTAMP DEFAULT now(),
    run_id BIGINT NOT NULL,
    article_row_id BIGINT NOT NULL,          -- 归属文章当前态记录
    action TEXT,                             -- new/update/delete/restore/check
    status TEXT,                             -- pending/running/success/failed/skipped/deferred/interrupted
    error_code TEXT,                         -- 固定原因码，不混存记录 ID
    duplicate_of_item_id BIGINT,             -- 同批次别名重复项关联主 item（status='skipped' 时）
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    billing_status TEXT DEFAULT 'pending',   -- pending/charged/failed/unknown/not_required
    billing_reference TEXT,
    credits_charged NUMERIC(12,2) DEFAULT 0,
    UNIQUE(tenant_id, run_id, article_row_id)
);
CREATE INDEX IF NOT EXISTS idx_bs_wechat_mp_sync_items_run
    ON bs_wechat_mp_sync_items(tenant_id, run_id);
