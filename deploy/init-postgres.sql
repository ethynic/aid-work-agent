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
    tenant_type TEXT NOT NULL DEFAULT 'test',  -- 租户类型：real=真实租户（真实金额充值）/ test=测试/演示租户（虚拟充值），仅影响平台统计页汇总口径，不影响审计数据
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON COLUMN tenants.tenant_type IS '租户类型：real=真实租户（真实金额充值）/ test=测试/演示租户（虚拟充值）。仅影响 /portal/recharge 与 /portal/token-usage 统计汇总口径，不影响审计数据';

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

-- 回复风格审计表（安全审计用，非业务逻辑：记录 reply_styles 的全部写操作来源，
-- 用于追查直连 SQL 清空风格表的事故，见 docs/ops/log-warning-audit-20260922.md）
CREATE TABLE IF NOT EXISTS reply_styles_audit (
    id SERIAL PRIMARY KEY,
    op TEXT NOT NULL,
    old_row JSONB,
    new_row JSONB,
    db_user TEXT,
    client_addr TEXT,
    application_name TEXT,
    happened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 审计触发器（记录 current_user / inet_client_addr / application_name，可归因直连 SQL）
CREATE OR REPLACE FUNCTION fn_reply_styles_audit() RETURNS trigger AS $$
BEGIN
    INSERT INTO reply_styles_audit (op, old_row, new_row, db_user, client_addr, application_name)
    VALUES (
        TG_OP,
        CASE WHEN TG_OP IN ('DELETE', 'UPDATE') THEN to_jsonb(OLD) END,
        CASE WHEN TG_OP IN ('INSERT', 'UPDATE') THEN to_jsonb(NEW) END,
        current_user,
        inet_client_addr()::text,
        current_setting('application_name', true)
    );
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE COALESCE(NEW, OLD) END;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reply_styles_audit ON reply_styles;
CREATE TRIGGER trg_reply_styles_audit
AFTER INSERT OR UPDATE OR DELETE ON reply_styles
FOR EACH ROW EXECUTE FUNCTION fn_reply_styles_audit();

DROP TRIGGER IF EXISTS trg_reply_styles_audit_truncate ON reply_styles;
CREATE TRIGGER trg_reply_styles_audit_truncate
AFTER TRUNCATE ON reply_styles
FOR EACH STATEMENT EXECUTE FUNCTION fn_reply_styles_audit();

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
    is_gift BOOLEAN NOT NULL DEFAULT FALSE,  -- 赠送金额标记：true=赠送充值（积分照常入余额，但不计入平台总充值金额汇总）
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
    token_type TEXT DEFAULT 'activated',             -- activated=激活产出 / static=管理端签发的长期直连 token（M10c，鉴权跳过过期检查）
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

-- ============================================================================
-- BOSS 端侧会话任务（boss.chat_reply.v1）场景表（B2；src/boss_conversation/
-- init_tables.py 同源，设计 §5.2/§5.3/§5.5.4/§5.6 冻结 DDL；bs_ 业务表遵守
-- tenant_id/user_id/created_at 规范列，无外键/触发器，引用完整性在 Python 层）
-- ============================================================================

-- 候选人绑定（verified 才可自动发送；频控触发计数列 + 同步阻断列；
-- 部分唯一索引保证同租户同设备同 candidate_name+job_id 仅一条 verified 有效绑定）
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
    verification_status TEXT NOT NULL DEFAULT 'pending', -- pending | verified | invalid | expired
    login_fingerprint_hash TEXT,               -- HMAC-SHA256 摘要，锚点不落库不落日志
    encrypted_identity_evidence TEXT,          -- 受控加密身份证据（verified 必填）
    verified_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    rate_trigger_date DATE,                    -- 频控触发计数（Asia/Shanghai 当日）
    rate_trigger_count INTEGER NOT NULL DEFAULT 0,
    last_rate_decision_id UUID,                -- 触发计数去重（跨日原子重置时一并清空）
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
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_boss_conv_bindings_verified
    ON bs_boss_conversation_bindings (tenant_id, device_id, candidate_name, job_id)
    WHERE verification_status = 'verified';
CREATE INDEX IF NOT EXISTS idx_boss_conv_bindings_owner
    ON bs_boss_conversation_bindings (tenant_id, user_id, device_id, created_at DESC);

-- 不可变话术版本（append-only：仅场景 API 写入；无指向 jobs/scripts 的 FK）
CREATE TABLE IF NOT EXISTS bs_boss_reply_script_versions (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    lineage_id UUID NOT NULL,
    version_no INTEGER NOT NULL,
    content_hash TEXT NOT NULL,                -- 模板规范化字节 sha256
    template TEXT NOT NULL,
    slot_schema JSONB NOT NULL,
    source_script_id UUID,                     -- 历史来源引用，无 FK
    source_job_id UUID,
    source_job_name TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, lineage_id, version_no),
    CHECK (version_no >= 1)
);

-- 频控账本（设计 §5.5.4 冻结 DDL：正常路径仅在预留成功时创建 reserved 行；
-- 唯一例外是 operation-result 发现 slot 缺失时补建 settled 异常行）
CREATE TABLE IF NOT EXISTS bs_boss_conversation_rate_slots (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    binding_id UUID NOT NULL,
    decision_id UUID NOT NULL,
    delivery_id UUID NOT NULL,
    status TEXT NOT NULL,          -- reserved | settled | released
    reserved_at TIMESTAMPTZ NOT NULL,
    settled_at TIMESTAMPTZ,        -- status=settled 时非空
    released_at TIMESTAMPTZ,       -- status=released 时非空
    settlement_effect TEXT,        -- submitted | verified | unknown | not_started（结算依据）
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, delivery_id),
    UNIQUE (tenant_id, decision_id),
    CHECK (status IN ('reserved','settled','released')),
    CHECK (settlement_effect IS NULL OR settlement_effect IN ('submitted','verified','unknown','not_started')),
    CHECK ((status='reserved' AND settled_at IS NULL AND released_at IS NULL)
        OR (status='settled'  AND settled_at IS NOT NULL AND released_at IS NULL)
        OR (status='released' AND released_at IS NOT NULL AND settled_at IS NULL)),
    CHECK ((status='reserved' AND settlement_effect IS NULL)
        OR (status='settled' AND settlement_effect IN ('submitted','verified','unknown'))
        OR (status='released' AND settlement_effect='not_started'))
);
CREATE INDEX IF NOT EXISTS idx_boss_rate_windows ON bs_boss_conversation_rate_slots (tenant_id, binding_id, reserved_at)
    WHERE status IN ('reserved','settled');

-- 结算异常队列（敏感异常详情不落明文，只存错误码与受控引用）
CREATE TABLE IF NOT EXISTS bs_boss_rate_settlement_anomalies (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    task_id UUID NOT NULL,
    binding_id UUID NOT NULL,
    delivery_id UUID NOT NULL,
    invocation_id UUID NOT NULL,
    error_code TEXT NOT NULL,       -- rate_slot_missing | rate_slot_state_conflict | settlement_failed 等
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | processing | resolved | ignored
    retry_count INT NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, delivery_id),
    CHECK (status IN ('pending','processing','resolved','ignored')),
    CHECK (retry_count >= 0)
);

-- 沟通日志投影队列（§5.6；verified（或 unknown 人工判定后）入队，后台 job 幂等 upsert）
CREATE TABLE IF NOT EXISTS bs_boss_comm_log_projection_queue (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    delivery_id UUID NOT NULL,
    binding_id UUID NOT NULL,
    resume_id BIGINT,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | processing | done | failed
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    last_error_code TEXT,                   -- 仅错误码，不落敏感详情
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, delivery_id),
    CHECK (status IN ('pending','processing','done','failed')),
    CHECK (retry_count >= 0)
);
CREATE INDEX IF NOT EXISTS idx_boss_comm_log_projection_scan
    ON bs_boss_comm_log_projection_queue (status, next_retry_at, created_at);

-- 投影目标表补列（表由 recruiting 模块自建，bootstrap 未必已建——存在才执行，
-- 应用启动 init_recruiting_timeline_tables 建表后由模块/增量块幂等补列）
DO $boss_comm_logs$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = current_schema()
                 AND table_name = 'bs_recruiting_operator_resume_comm_logs') THEN
        ALTER TABLE bs_recruiting_operator_resume_comm_logs ADD COLUMN IF NOT EXISTS source_delivery_id UUID;
        ALTER TABLE bs_recruiting_operator_resume_comm_logs ADD COLUMN IF NOT EXISTS source_message_id TEXT;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_boss_comm_logs_source_delivery
            ON bs_recruiting_operator_resume_comm_logs (tenant_id, source_delivery_id);
    END IF;
END
$boss_comm_logs$;

-- ============================================================================
-- 外部内容同步通用表（content_sync 平台基础设施；src/services/content_sync/db.py 同源，
-- 任何数据源模块（含租户定制）复用，不建私有表）
-- ============================================================================

-- 源配置（谁在定时跑由配置行决定；module 区分数据源）
CREATE TABLE IF NOT EXISTS bs_content_sync_sources (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    module TEXT NOT NULL,                       -- 数据源模块标识（各模块自定义 slug）
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    sync_interval_hours INT NOT NULL DEFAULT 24,
    selection_mode VARCHAR(8) NOT NULL DEFAULT 'all',  -- all | ids（挑选白名单）
    selected_ids JSONB,
    last_sync_at TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now(),
    UNIQUE(tenant_id, module),
    CHECK (selection_mode IN ('all', 'ids'))
);

-- 运行账本 + 执行队列（queued→running→终态；蓝本 bs_wechat_mp_sync_runs）
CREATE TABLE IF NOT EXISTS bs_content_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    module TEXT NOT NULL,
    user_id TEXT,
    trigger_type VARCHAR(16) NOT NULL,          -- manual | scheduled
    status VARCHAR(32) NOT NULL,                -- queued/running/success/partial_failed/failed/skipped_no_credit/interrupted
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
    fetch_complete BOOLEAN NOT NULL DEFAULT FALSE,  -- 对账门禁佐证
    total_reported INT,
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_content_sync_runs_active
    ON bs_content_sync_runs(tenant_id, module) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_content_sync_runs_tenant_created
    ON bs_content_sync_runs(tenant_id, module, created_at DESC);

-- 批次内逐条任务（蓝本 bs_wechat_mp_sync_items）
CREATE TABLE IF NOT EXISTS bs_content_sync_items (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    run_id BIGINT NOT NULL,
    module TEXT NOT NULL,
    native_id TEXT NOT NULL,
    action VARCHAR(16),                         -- new | update | skip | delete | restore（失败项 action 为空）
    status VARCHAR(16),                         -- pending/running/success/skipped/failed/interrupted
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
);
CREATE INDEX IF NOT EXISTS idx_content_sync_items_run
    ON bs_content_sync_items(tenant_id, run_id);

-- 记录当前态账本（蓝本 bs_wechat_mp_articles；payload JSONB 存各源目录展示字段）
CREATE TABLE IF NOT EXISTS bs_content_sync_records (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    module TEXT NOT NULL,
    native_id TEXT NOT NULL,
    external_id TEXT NOT NULL,                  -- documents.origin/external_id 命名空间
    content_hash VARCHAR(64),
    pipeline_version TEXT,
    doc_id INTEGER,                             -- → documents.id
    user_deleted BOOLEAN NOT NULL DEFAULT FALSE,-- 同步侧自愈：用户知识库删除抑制
    miss_streak INT NOT NULL DEFAULT 0,         -- 消失两击计数
    fail_count INT NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMP,                    -- 300s×2^fail_count 上限 24h
    processing_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    error_message TEXT,
    payload JSONB,                              -- 各源自定义目录展示字段（name/model/status...）
    last_seen_at TIMESTAMP,                     -- 每轮 fetch 刷新（消失对账）
    last_synced_at TIMESTAMP,
    last_checked_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT now(),
    UNIQUE(tenant_id, module, native_id)
);
CREATE INDEX IF NOT EXISTS idx_content_sync_records_retry
    ON bs_content_sync_records(tenant_id, module, processing_status, next_retry_at);

-- 通用图级 VL 缓存（tenant+URL 键：任何源的图片共用；URL 不变即图不变）
CREATE TABLE IF NOT EXISTS bs_image_vision_cache (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    image_url TEXT NOT NULL,
    description TEXT,
    model TEXT,
    status VARCHAR(16) NOT NULL,                -- ok | unrecognized | failed
    is_billed BOOLEAN NOT NULL DEFAULT FALSE,   -- unrecognized 不计费
    parsed_at TIMESTAMP DEFAULT now(),
    created_at TIMESTAMP DEFAULT now(),
    UNIQUE(tenant_id, image_url),
    CHECK (status IN ('ok', 'unrecognized', 'failed'))
);

-- AgentRunner: durable acceptance, logical session ownership, authoritative usage facts.
CREATE TABLE IF NOT EXISTS agent_runners (
    runner_id TEXT PRIMARY KEY,
    queue_order BIGSERIAL UNIQUE NOT NULL,
    tenant_id TEXT,
    scope_key TEXT NOT NULL,
    session_kind TEXT NOT NULL CHECK (session_kind IN ('web','channel')),
    session_id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('user','channel')),
    actor_id TEXT NOT NULL,
    user_id TEXT,
    service_id TEXT NOT NULL,
    source TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    input JSONB NOT NULL,
    profile_id TEXT NOT NULL,
    profile_fingerprint TEXT NOT NULL,
    checkpoint JSONB,
    checkpoint_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN
        ('queued','running','waiting','paused','interrupted','finalizing','completed','failed','cancelled')),
    settlement_status TEXT NOT NULL DEFAULT 'pending' CHECK (settlement_status IN ('pending','settled')),
    attempt INTEGER NOT NULL DEFAULT 0,
    worker_id TEXT,
    lease_until TIMESTAMPTZ,
    revision BIGINT NOT NULL DEFAULT 0,
    view_revision BIGINT NOT NULL DEFAULT 0,
    control_revision BIGINT NOT NULL DEFAULT 0,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    public_snapshot JSONB NOT NULL DEFAULT '{}',
    result JSONB,
    record_id TEXT UNIQUE NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    CHECK ((tenant_id IS NULL AND scope_key='global') OR
           (tenant_id IS NOT NULL AND scope_key='tenant:' || tenant_id)),
    CHECK (session_kind <> 'channel' OR tenant_id IS NOT NULL),
    UNIQUE(scope_key,actor_kind,actor_id,source,client_request_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_runners_queue ON agent_runners(status,queue_order);
CREATE INDEX IF NOT EXISTS idx_agent_runners_session ON agent_runners(scope_key,session_kind,session_id,queue_order);
CREATE INDEX IF NOT EXISTS idx_agent_runners_lease ON agent_runners(lease_until) WHERE status IN ('running','finalizing');

CREATE TABLE IF NOT EXISTS agent_runner_session_claims (
    scope_key TEXT NOT NULL,
    tenant_id TEXT,
    session_kind TEXT NOT NULL CHECK (session_kind IN ('web','channel')),
    session_id TEXT NOT NULL,
    owner_runner_id TEXT UNIQUE NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0,
    gate TEXT NOT NULL DEFAULT 'execution' CHECK (gate IN ('execution','delivery')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(scope_key,session_kind,session_id),
    CHECK ((tenant_id IS NULL AND scope_key='global') OR
           (tenant_id IS NOT NULL AND scope_key='tenant:' || tenant_id)),
    CHECK (session_kind <> 'channel' OR tenant_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS agent_runner_usage_receipts (
    receipt_id TEXT PRIMARY KEY,
    runner_id TEXT NOT NULL,
    tenant_id TEXT,
    scope_key TEXT NOT NULL,
    call_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    tool_call_id TEXT,
    authorized_attempt INTEGER NOT NULL,
    owner TEXT NOT NULL CHECK (owner IN ('llm','embedding','asr','local_reference')),
    purpose TEXT NOT NULL,
    billing_boundary TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    provider_request_id TEXT,
    phase TEXT NOT NULL DEFAULT 'started' CHECK (phase IN ('started','observed','unknown','no_usage')),
    usage JSONB,
    price_snapshot JSONB NOT NULL DEFAULT '{}',
    fact_digest TEXT,
    applied BOOLEAN NOT NULL DEFAULT FALSE,
    external_owner_id TEXT,
    record_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    observed_at TIMESTAMPTZ,
    applied_at TIMESTAMPTZ,
    UNIQUE(runner_id,call_id),
    CHECK ((tenant_id IS NULL AND scope_key='global') OR
           (tenant_id IS NOT NULL AND scope_key='tenant:' || tenant_id)),
    CHECK (phase <> 'observed' OR usage IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_agent_runner_usage_pending ON agent_runner_usage_receipts(runner_id,applied,phase);

-- M4 private pause/resume/reply command ledger; public events remain separate.
ALTER TABLE agent_runners ADD COLUMN IF NOT EXISTS pause_requested BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE agent_runners ADD COLUMN IF NOT EXISTS resume_control_id TEXT;
CREATE TABLE IF NOT EXISTS agent_runner_controls (
    control_id TEXT PRIMARY KEY,
    runner_id TEXT NOT NULL,
    tenant_id TEXT,
    scope_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('pause','resume','reply','browser_complete')),
    client_request_id TEXT NOT NULL,
    intent_digest TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted' CHECK (status IN ('accepted','claimed','consumed','rejected')),
    error_code TEXT,
    consumed_attempt INTEGER,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    consumed_at TIMESTAMPTZ,
    UNIQUE(runner_id,client_request_id),
    CHECK ((tenant_id IS NULL AND scope_key='global') OR
           (tenant_id IS NOT NULL AND scope_key='tenant:' || tenant_id))
);
CREATE INDEX IF NOT EXISTS idx_agent_runner_controls_pending ON agent_runner_controls(runner_id,status);

-- Browser baseline audit compatibility and mandatory Runner owner linkage.
-- One DO is one savepoint in the existing incremental migration driver: any
-- incompatible catalog rolls back this whole Browser block, not partial DDL.
DO $$
DECLARE
    spec RECORD;
    attribute RECORD;
BEGIN
CREATE TABLE IF NOT EXISTS bs_browser_runs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    run_id TEXT NOT NULL UNIQUE,
    parent_run_id TEXT,
    session_id TEXT,
    execution_target TEXT DEFAULT 'server',
    executor_client_id TEXT,
    state TEXT DEFAULT 'CREATED',
    routing_reason TEXT,
    failure_class TEXT,
    evidence_level TEXT,
    escalation_count INTEGER DEFAULT 0,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    close_reason TEXT,
    error_code TEXT,
    steps_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    runner_id TEXT,
    runner_execution_id TEXT,
    runner_tool_call_id TEXT,
    owner_worker_id TEXT,
    owner_boot_id TEXT,
    browser_epoch TEXT,
    owner_endpoint TEXT,
    owner_lease_until TIMESTAMPTZ,
    runtime_state TEXT,
    closed_at TIMESTAMPTZ,
    -- 复合唯一：bs_browser_assistance_requests 复合外键的引用目标（租户内 run 唯一）
    UNIQUE (tenant_id, run_id)
);

IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='bs_browser_runs'::regclass AND attname='id' AND NOT attisdropped) THEN
        ALTER TABLE bs_browser_runs ADD COLUMN id BIGSERIAL;
    END IF;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS tenant_id TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS user_id TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS run_id TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS parent_run_id TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS session_id TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS execution_target TEXT DEFAULT 'server';

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS executor_client_id TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS state TEXT DEFAULT 'CREATED';

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS routing_reason TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS failure_class TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS evidence_level TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS escalation_count INTEGER DEFAULT 0;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMP;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS finished_at TIMESTAMP;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS close_reason TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS error_code TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS steps_count INTEGER DEFAULT 0;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS runner_id TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS runner_execution_id TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS runner_tool_call_id TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS owner_worker_id TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS owner_boot_id TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS browser_epoch TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS owner_endpoint TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS owner_lease_until TIMESTAMPTZ;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS runtime_state TEXT;

ALTER TABLE bs_browser_runs ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS bs_browser_assistance_requests (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    assistance_id TEXT NOT NULL UNIQUE,
    run_id TEXT,
    agent_execution_id TEXT,
    tool_call_id TEXT,
    state TEXT DEFAULT 'pending',
    reason_code TEXT,
    instruction_code TEXT,
    completion_mode TEXT,
    predicate_type TEXT,
    expires_at TIMESTAMP,
    completed_at TIMESTAMP,
    resumed_at TIMESTAMP,
    error_code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    runner_id TEXT,
    runner_wait_id TEXT,
    owner_boot_id TEXT,
    browser_epoch TEXT,
    completion_ref TEXT,
    completion_fact JSONB,
    extended_at TIMESTAMPTZ,
    continuation_id TEXT,
    -- 租户内关联到 run（引用 bs_browser_runs 的 UNIQUE (tenant_id, run_id)）
    FOREIGN KEY (tenant_id, run_id) REFERENCES bs_browser_runs(tenant_id, run_id)
);

IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='bs_browser_assistance_requests'::regclass AND attname='id' AND NOT attisdropped) THEN
        ALTER TABLE bs_browser_assistance_requests ADD COLUMN id BIGSERIAL;
    END IF;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS tenant_id TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS user_id TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS assistance_id TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS run_id TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS agent_execution_id TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS tool_call_id TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS state TEXT DEFAULT 'pending';

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS reason_code TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS instruction_code TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS completion_mode TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS predicate_type TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS resumed_at TIMESTAMP;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS error_code TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS runner_id TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS runner_wait_id TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS owner_boot_id TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS browser_epoch TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS completion_ref TEXT;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS completion_fact JSONB;

ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS continuation_id TEXT;

-- 原随机 bac 与 Runner execution/call/wait 的持久关联（独立于 completion 相位）。
-- 存量 legacy 行 continuation_id 为 NULL，部分唯一索引允许多行 NULL 共存。
CREATE UNIQUE INDEX IF NOT EXISTS uq_browser_assistance_continuation
    ON bs_browser_assistance_requests (continuation_id)
    WHERE continuation_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS bs_browser_resume_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT,
    tenant_id TEXT,
    assistance_id TEXT,
    run_id TEXT,
    state TEXT DEFAULT 'pending',
    lease_owner TEXT,
    lease_until TIMESTAMPTZ,
    attempts INTEGER DEFAULT 0,
    available_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    last_error_code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='bs_browser_resume_jobs'::regclass AND attname='id' AND NOT attisdropped) THEN
        ALTER TABLE bs_browser_resume_jobs ADD COLUMN id BIGSERIAL;
    END IF;

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS job_id TEXT;

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS tenant_id TEXT;

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS assistance_id TEXT;

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS run_id TEXT;

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS state TEXT DEFAULT 'pending';

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS lease_owner TEXT;

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS attempts INTEGER DEFAULT 0;

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS last_error_code TEXT;

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE bs_browser_resume_jobs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;

CREATE UNIQUE INDEX IF NOT EXISTS idx_browser_runs_run_id ON bs_browser_runs(run_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_browser_assistance_id ON bs_browser_assistance_requests(assistance_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_browser_resume_job_id ON bs_browser_resume_jobs(job_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_browser_resume_assistance ON bs_browser_resume_jobs(assistance_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_browser_runner_original_call ON bs_browser_runs(runner_id,runner_execution_id,runner_tool_call_id) WHERE runner_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_browser_completion_ref ON bs_browser_assistance_requests(completion_ref) WHERE completion_ref IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_browser_runner_owner ON bs_browser_runs(owner_worker_id,owner_boot_id,owner_lease_until) WHERE runner_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_browser_assistance_runner_wait ON bs_browser_assistance_requests(runner_id,runner_wait_id) WHERE runner_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_browser_resume_pending ON bs_browser_resume_jobs(state,available_at);

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='bs_browser_runs'::regclass AND conname='ck_browser_runner_binding') THEN
        ALTER TABLE bs_browser_runs ADD CONSTRAINT ck_browser_runner_binding CHECK ((runner_id IS NULL AND runner_execution_id IS NULL AND runner_tool_call_id IS NULL
    AND owner_worker_id IS NULL AND owner_boot_id IS NULL AND browser_epoch IS NULL
    AND owner_endpoint IS NULL AND owner_lease_until IS NULL AND runtime_state IS NULL AND closed_at IS NULL)
 OR (runner_id IS NOT NULL AND tenant_id IS NOT NULL AND user_id IS NOT NULL AND session_id IS NOT NULL
    AND run_id IS NOT NULL AND runner_execution_id IS NOT NULL AND runner_tool_call_id IS NOT NULL
    AND owner_worker_id IS NOT NULL AND owner_boot_id IS NOT NULL AND browser_epoch IS NOT NULL AND owner_endpoint IS NOT NULL
    AND runtime_state IS NOT NULL AND ((runtime_state IN ('starting','live') AND owner_lease_until IS NOT NULL AND closed_at IS NULL)
      OR (runtime_state IN ('closed','lost') AND owner_lease_until IS NULL AND closed_at IS NOT NULL))));
    END IF;


    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='bs_browser_assistance_requests'::regclass AND conname='ck_browser_assistance_runner_binding') THEN
        ALTER TABLE bs_browser_assistance_requests ADD CONSTRAINT ck_browser_assistance_runner_binding CHECK ((runner_id IS NULL AND runner_wait_id IS NULL AND owner_boot_id IS NULL AND browser_epoch IS NULL
    AND completion_ref IS NULL AND completion_fact IS NULL)
 OR (runner_id IS NOT NULL AND tenant_id IS NOT NULL AND user_id IS NOT NULL AND assistance_id IS NOT NULL
    AND run_id IS NOT NULL AND agent_execution_id IS NOT NULL AND tool_call_id IS NOT NULL
    AND runner_wait_id IS NOT NULL AND owner_boot_id IS NOT NULL AND browser_epoch IS NOT NULL
    AND ((completion_ref IS NULL AND completion_fact IS NULL) OR (completion_ref IS NOT NULL AND completion_fact IS NOT NULL AND jsonb_typeof(completion_fact)='object'))));
    END IF;

    -- Only these three tables are checked. Legacy audit wall-clock timestamps
    -- and int4/int8 auto-increment primary IDs are accepted without conversion.
    FOR spec IN SELECT * FROM (VALUES
        ('bs_browser_runs','id','BIGSERIAL'),
        ('bs_browser_runs','tenant_id','TEXT'),
        ('bs_browser_runs','user_id','TEXT'),
        ('bs_browser_runs','run_id','TEXT'),
        ('bs_browser_runs','parent_run_id','TEXT'),
        ('bs_browser_runs','session_id','TEXT'),
        ('bs_browser_runs','execution_target','TEXT'),
        ('bs_browser_runs','executor_client_id','TEXT'),
        ('bs_browser_runs','state','TEXT'),
        ('bs_browser_runs','routing_reason','TEXT'),
        ('bs_browser_runs','failure_class','TEXT'),
        ('bs_browser_runs','evidence_level','TEXT'),
        ('bs_browser_runs','escalation_count','INTEGER'),
        ('bs_browser_runs','started_at','TIMESTAMP'),
        ('bs_browser_runs','finished_at','TIMESTAMP'),
        ('bs_browser_runs','close_reason','TEXT'),
        ('bs_browser_runs','error_code','TEXT'),
        ('bs_browser_runs','steps_count','INTEGER'),
        ('bs_browser_runs','created_at','TIMESTAMP'),
        ('bs_browser_runs','updated_at','TIMESTAMP'),
        ('bs_browser_runs','runner_id','TEXT'),
        ('bs_browser_runs','runner_execution_id','TEXT'),
        ('bs_browser_runs','runner_tool_call_id','TEXT'),
        ('bs_browser_runs','owner_worker_id','TEXT'),
        ('bs_browser_runs','owner_boot_id','TEXT'),
        ('bs_browser_runs','browser_epoch','TEXT'),
        ('bs_browser_runs','owner_endpoint','TEXT'),
        ('bs_browser_runs','owner_lease_until','TIMESTAMPTZ'),
        ('bs_browser_runs','runtime_state','TEXT'),
        ('bs_browser_runs','closed_at','TIMESTAMPTZ'),
        ('bs_browser_assistance_requests','id','BIGSERIAL'),
        ('bs_browser_assistance_requests','tenant_id','TEXT'),
        ('bs_browser_assistance_requests','user_id','TEXT'),
        ('bs_browser_assistance_requests','assistance_id','TEXT'),
        ('bs_browser_assistance_requests','run_id','TEXT'),
        ('bs_browser_assistance_requests','agent_execution_id','TEXT'),
        ('bs_browser_assistance_requests','tool_call_id','TEXT'),
        ('bs_browser_assistance_requests','state','TEXT'),
        ('bs_browser_assistance_requests','reason_code','TEXT'),
        ('bs_browser_assistance_requests','instruction_code','TEXT'),
        ('bs_browser_assistance_requests','completion_mode','TEXT'),
        ('bs_browser_assistance_requests','predicate_type','TEXT'),
        ('bs_browser_assistance_requests','expires_at','TIMESTAMP'),
        ('bs_browser_assistance_requests','completed_at','TIMESTAMP'),
        ('bs_browser_assistance_requests','resumed_at','TIMESTAMP'),
        ('bs_browser_assistance_requests','error_code','TEXT'),
        ('bs_browser_assistance_requests','created_at','TIMESTAMP'),
        ('bs_browser_assistance_requests','updated_at','TIMESTAMP'),
        ('bs_browser_assistance_requests','runner_id','TEXT'),
        ('bs_browser_assistance_requests','runner_wait_id','TEXT'),
        ('bs_browser_assistance_requests','owner_boot_id','TEXT'),
        ('bs_browser_assistance_requests','browser_epoch','TEXT'),
        ('bs_browser_assistance_requests','completion_ref','TEXT'),
        ('bs_browser_assistance_requests','completion_fact','JSONB'),
        ('bs_browser_resume_jobs','id','BIGSERIAL'),
        ('bs_browser_resume_jobs','job_id','TEXT'),
        ('bs_browser_resume_jobs','tenant_id','TEXT'),
        ('bs_browser_resume_jobs','assistance_id','TEXT'),
        ('bs_browser_resume_jobs','run_id','TEXT'),
        ('bs_browser_resume_jobs','state','TEXT'),
        ('bs_browser_resume_jobs','lease_owner','TEXT'),
        ('bs_browser_resume_jobs','lease_until','TIMESTAMPTZ'),
        ('bs_browser_resume_jobs','attempts','INTEGER'),
        ('bs_browser_resume_jobs','available_at','TIMESTAMPTZ'),
        ('bs_browser_resume_jobs','last_error_code','TEXT'),
        ('bs_browser_resume_jobs','created_at','TIMESTAMP'),
        ('bs_browser_resume_jobs','updated_at','TIMESTAMP'),
        ('bs_browser_resume_jobs','completed_at','TIMESTAMP')
    ) AS required(table_name,column_name,column_kind) LOOP
        SELECT a.atttypid,a.atttypmod,a.attnotnull,a.attidentity,
               pg_get_expr(d.adbin,d.adrelid) AS default_expression,a.attnum
          INTO attribute
          FROM pg_attribute a LEFT JOIN pg_attrdef d
            ON d.adrelid=a.attrelid AND d.adnum=a.attnum
         WHERE a.attrelid=to_regclass(spec.table_name) AND a.attname=spec.column_name
           AND a.attnum>0 AND NOT a.attisdropped;
        IF NOT FOUND OR NOT (CASE spec.column_kind
            WHEN 'TEXT' THEN attribute.atttypid IN ('text'::regtype,'varchar'::regtype)
                              AND attribute.atttypmod=-1
            WHEN 'BIGSERIAL' THEN attribute.atttypid IN ('int4'::regtype,'int8'::regtype)
            WHEN 'INTEGER' THEN attribute.atttypid='int4'::regtype
            WHEN 'TIMESTAMP' THEN attribute.atttypid IN ('timestamp'::regtype,'timestamptz'::regtype)
            WHEN 'TIMESTAMPTZ' THEN attribute.atttypid='timestamptz'::regtype
            WHEN 'JSONB' THEN attribute.atttypid='jsonb'::regtype
            ELSE FALSE END) THEN
            RAISE EXCEPTION 'BROWSER_SCHEMA_COLUMN_INCOMPATIBLE: %.%',spec.table_name,spec.column_name;
        END IF;
        IF spec.column_kind='BIGSERIAL' AND (
            NOT attribute.attnotnull OR NOT (
                attribute.attidentity IN ('a','d') OR
                COALESCE(attribute.default_expression LIKE 'nextval(%',FALSE)) OR
            NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid=to_regclass(spec.table_name)
                AND i.indisprimary AND i.indisvalid AND i.indisready
                AND i.indnatts=1 AND i.indkey[0]=attribute.attnum)) THEN
            RAISE EXCEPTION 'BROWSER_SCHEMA_PRIMARY_ID_INCOMPATIBLE: %',spec.table_name;
        END IF;
    END LOOP;

    -- A same-name index must prove the original immutable uniqueness contract.
    FOR spec IN SELECT * FROM (VALUES
        ('idx_browser_runs_run_id','bs_browser_runs',ARRAY['run_id']::text[],NULL::text),
        ('idx_browser_assistance_id','bs_browser_assistance_requests',ARRAY['assistance_id']::text[],NULL::text),
        ('idx_browser_resume_job_id','bs_browser_resume_jobs',ARRAY['job_id']::text[],NULL::text),
        ('idx_browser_resume_assistance','bs_browser_resume_jobs',ARRAY['assistance_id']::text[],NULL::text),
        ('idx_browser_runner_original_call','bs_browser_runs',ARRAY['runner_id','runner_execution_id','runner_tool_call_id']::text[],'(runner_id IS NOT NULL)'),
        ('idx_browser_completion_ref','bs_browser_assistance_requests',ARRAY['completion_ref']::text[],'(completion_ref IS NOT NULL)')
    ) AS required(index_name,table_name,column_names,predicate) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
              JOIN pg_am am ON am.oid=c.relam
             WHERE i.indexrelid=to_regclass(spec.index_name)
               AND i.indrelid=to_regclass(spec.table_name) AND am.amname='btree'
               AND i.indisunique AND i.indisvalid AND i.indisready
               AND i.indnatts=cardinality(spec.column_names)
               AND i.indnkeyatts=cardinality(spec.column_names) AND i.indexprs IS NULL
               AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum,ordinal)
                         JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
                         ORDER BY k.ordinal)=spec.column_names
               AND pg_get_expr(i.indpred,i.indrelid) IS NOT DISTINCT FROM spec.predicate
        ) THEN
            RAISE EXCEPTION 'BROWSER_SCHEMA_INDEX_INCOMPATIBLE: %',spec.index_name;
        END IF;
    END LOOP;

    -- Parse the expected CHECK using the actual column types. Deparse against
    -- each relation before comparing: legacy dropped-column attnum gaps differ.
    IF to_regclass('pg_temp.runner_browser_check_0_reference') IS NOT NULL THEN
        RAISE EXCEPTION 'BROWSER_SCHEMA_CHECK_REFERENCE_COLLISION';
    END IF;
    CREATE TEMP TABLE runner_browser_check_0_reference (LIKE bs_browser_runs) ON COMMIT DROP;
    ALTER TABLE runner_browser_check_0_reference ADD CONSTRAINT expected_browser_binding CHECK ((runner_id IS NULL AND runner_execution_id IS NULL AND runner_tool_call_id IS NULL
    AND owner_worker_id IS NULL AND owner_boot_id IS NULL AND browser_epoch IS NULL
    AND owner_endpoint IS NULL AND owner_lease_until IS NULL AND runtime_state IS NULL AND closed_at IS NULL)
 OR (runner_id IS NOT NULL AND tenant_id IS NOT NULL AND user_id IS NOT NULL AND session_id IS NOT NULL
    AND run_id IS NOT NULL AND runner_execution_id IS NOT NULL AND runner_tool_call_id IS NOT NULL
    AND owner_worker_id IS NOT NULL AND owner_boot_id IS NOT NULL AND browser_epoch IS NOT NULL AND owner_endpoint IS NOT NULL
    AND runtime_state IS NOT NULL AND ((runtime_state IN ('starting','live') AND owner_lease_until IS NOT NULL AND closed_at IS NULL)
      OR (runtime_state IN ('closed','lost') AND owner_lease_until IS NULL AND closed_at IS NOT NULL))));
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint actual JOIN pg_constraint expected
          ON expected.conrelid='pg_temp.runner_browser_check_0_reference'::regclass
         AND expected.conname='expected_browser_binding'
         WHERE actual.conrelid='bs_browser_runs'::regclass AND actual.conname='ck_browser_runner_binding'
           AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
           AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(expected.conbin,expected.conrelid)
    ) THEN
        RAISE EXCEPTION 'BROWSER_SCHEMA_CHECK_INCOMPATIBLE: ck_browser_runner_binding';
    END IF;
    DROP TABLE runner_browser_check_0_reference;

    IF to_regclass('pg_temp.runner_browser_check_1_reference') IS NOT NULL THEN
        RAISE EXCEPTION 'BROWSER_SCHEMA_CHECK_REFERENCE_COLLISION';
    END IF;
    CREATE TEMP TABLE runner_browser_check_1_reference (LIKE bs_browser_assistance_requests) ON COMMIT DROP;
    ALTER TABLE runner_browser_check_1_reference ADD CONSTRAINT expected_browser_binding CHECK ((runner_id IS NULL AND runner_wait_id IS NULL AND owner_boot_id IS NULL AND browser_epoch IS NULL
    AND completion_ref IS NULL AND completion_fact IS NULL)
 OR (runner_id IS NOT NULL AND tenant_id IS NOT NULL AND user_id IS NOT NULL AND assistance_id IS NOT NULL
    AND run_id IS NOT NULL AND agent_execution_id IS NOT NULL AND tool_call_id IS NOT NULL
    AND runner_wait_id IS NOT NULL AND owner_boot_id IS NOT NULL AND browser_epoch IS NOT NULL
    AND ((completion_ref IS NULL AND completion_fact IS NULL) OR (completion_ref IS NOT NULL AND completion_fact IS NOT NULL AND jsonb_typeof(completion_fact)='object'))));
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint actual JOIN pg_constraint expected
          ON expected.conrelid='pg_temp.runner_browser_check_1_reference'::regclass
         AND expected.conname='expected_browser_binding'
         WHERE actual.conrelid='bs_browser_assistance_requests'::regclass AND actual.conname='ck_browser_assistance_runner_binding'
           AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
           AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(expected.conbin,expected.conrelid)
    ) THEN
        RAISE EXCEPTION 'BROWSER_SCHEMA_CHECK_INCOMPATIBLE: ck_browser_assistance_runner_binding';
    END IF;
    DROP TABLE runner_browser_check_1_reference;

END $$;

-- Original native assistance has one durable extension. Legacy NULL remains unused.
DO $$
BEGIN
    ALTER TABLE bs_browser_assistance_requests ADD COLUMN IF NOT EXISTS extended_at TIMESTAMPTZ;
    IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='bs_browser_assistance_requests'::regclass
        AND attname='extended_at' AND atttypid='timestamptz'::regtype AND NOT attisdropped AND NOT attnotnull) THEN
        RAISE EXCEPTION 'BROWSER_SCHEMA_COLUMN_INCOMPATIBLE: bs_browser_assistance_requests.extended_at';
    END IF;
END $$;

-- AgentRunner M5 public notification ledger; historical roots start at head/floor zero.
DO $$
DECLARE
    spec RECORD;
BEGIN
    ALTER TABLE agent_runners ADD COLUMN IF NOT EXISTS event_seq BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE agent_runners ADD COLUMN IF NOT EXISTS event_floor_seq BIGINT NOT NULL DEFAULT 0;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='agent_runners'::regclass AND conname='ck_runner_event_watermarks') THEN
        ALTER TABLE agent_runners ADD CONSTRAINT ck_runner_event_watermarks CHECK (event_seq>=event_floor_seq AND event_floor_seq>=0);
    END IF;
    CREATE TABLE IF NOT EXISTS agent_runner_events (
        runner_id TEXT NOT NULL,
        seq BIGINT NOT NULL,
        tenant_id TEXT,
        scope_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (runner_id,seq),
        CONSTRAINT ck_runner_event_shape CHECK (seq>0 AND jsonb_typeof(payload)='object'
            AND kind IN ('created','revision_changed','terminal','settlement_changed')),
        CONSTRAINT ck_runner_event_scope CHECK ((tenant_id IS NULL AND scope_key='global')
            OR (tenant_id IS NOT NULL AND scope_key='tenant:' || tenant_id))
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_runner_event_created ON agent_runner_events(runner_id) WHERE kind='created';
    CREATE UNIQUE INDEX IF NOT EXISTS idx_runner_event_terminal ON agent_runner_events(runner_id) WHERE kind='terminal';
    FOR spec IN SELECT * FROM (VALUES
        ('agent_runners','event_seq','int8',TRUE),
        ('agent_runners','event_floor_seq','int8',TRUE),
        ('agent_runner_events','runner_id','text',TRUE),
        ('agent_runner_events','seq','int8',TRUE),
        ('agent_runner_events','tenant_id','text',FALSE),
        ('agent_runner_events','scope_key','text',TRUE),
        ('agent_runner_events','kind','text',TRUE),
        ('agent_runner_events','payload','jsonb',TRUE),
        ('agent_runner_events','created_at','timestamptz',TRUE)
    ) AS wanted(table_name,column_name,type_name,required) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=to_regclass(spec.table_name)
            AND a.attname=spec.column_name AND a.atttypid=to_regtype(spec.type_name)
            AND a.attnotnull=spec.required AND NOT a.attisdropped) THEN
            RAISE EXCEPTION 'RUNNER_EVENT_SCHEMA_COLUMN_INCOMPATIBLE: %.%',spec.table_name,spec.column_name;
        END IF;
    END LOOP;
    FOR spec IN SELECT * FROM (VALUES
        ('agent_runner_events_pkey',ARRAY['runner_id','seq']::text[],NULL::text),
        ('idx_runner_event_created',ARRAY['runner_id']::text[], '(kind = ''created''::text)'),
        ('idx_runner_event_terminal',ARRAY['runner_id']::text[], '(kind = ''terminal''::text)')
    ) AS wanted(index_name,column_names,predicate) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
            JOIN pg_am am ON am.oid=c.relam WHERE c.oid=to_regclass(spec.index_name)
            AND i.indrelid='agent_runner_events'::regclass AND i.indisunique AND i.indisvalid
            AND i.indisready AND (spec.index_name<>'agent_runner_events_pkey' OR i.indisprimary) AND am.amname='btree' AND i.indexprs IS NULL
            AND i.indnkeyatts=array_length(spec.column_names,1) AND i.indnatts=i.indnkeyatts
            AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum,ordinal)
                JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum ORDER BY k.ordinal)=spec.column_names
            AND pg_get_expr(i.indpred,i.indrelid) IS NOT DISTINCT FROM spec.predicate) THEN
            RAISE EXCEPTION 'RUNNER_EVENT_SCHEMA_INDEX_INCOMPATIBLE: %',spec.index_name;
        END IF;
    END LOOP;
    IF to_regclass('pg_temp.runner_event_root_check_reference') IS NOT NULL
        OR to_regclass('pg_temp.runner_event_check_reference') IS NOT NULL THEN
        RAISE EXCEPTION 'RUNNER_EVENT_SCHEMA_REFERENCE_COLLISION';
    END IF;
    CREATE TEMP TABLE runner_event_root_check_reference (LIKE agent_runners) ON COMMIT DROP;
    ALTER TABLE runner_event_root_check_reference ADD CONSTRAINT expected_watermarks CHECK (event_seq>=event_floor_seq AND event_floor_seq>=0);
    ALTER TABLE runner_event_root_check_reference ALTER COLUMN event_seq SET DEFAULT 0;
    ALTER TABLE runner_event_root_check_reference ALTER COLUMN event_floor_seq SET DEFAULT 0;
    FOR spec IN SELECT * FROM (VALUES ('event_seq'),('event_floor_seq')) AS wanted(column_name) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            JOIN pg_attribute expected_a ON expected_a.attrelid='pg_temp.runner_event_root_check_reference'::regclass
                AND expected_a.attname=spec.column_name
            JOIN pg_attrdef expected_d ON expected_d.adrelid=expected_a.attrelid AND expected_d.adnum=expected_a.attnum
            WHERE a.attrelid='agent_runners'::regclass AND a.attname=spec.column_name
                AND pg_get_expr(d.adbin,d.adrelid)=pg_get_expr(expected_d.adbin,expected_d.adrelid)) THEN
            RAISE EXCEPTION 'RUNNER_EVENT_SCHEMA_DEFAULT_INCOMPATIBLE: %',spec.column_name;
        END IF;
    END LOOP;
    CREATE TEMP TABLE runner_event_check_reference (LIKE agent_runner_events) ON COMMIT DROP;
    ALTER TABLE runner_event_check_reference ADD CONSTRAINT expected_shape CHECK (seq>0 AND jsonb_typeof(payload)='object'
        AND kind IN ('created','revision_changed','terminal','settlement_changed'));
    ALTER TABLE runner_event_check_reference ADD CONSTRAINT expected_scope CHECK ((tenant_id IS NULL AND scope_key='global')
        OR (tenant_id IS NOT NULL AND scope_key='tenant:' || tenant_id));
    FOR spec IN SELECT * FROM (VALUES
        ('agent_runners','ck_runner_event_watermarks','runner_event_root_check_reference','expected_watermarks'),
        ('agent_runner_events','ck_runner_event_shape','runner_event_check_reference','expected_shape'),
        ('agent_runner_events','ck_runner_event_scope','runner_event_check_reference','expected_scope')
    ) AS wanted(table_name,check_name,reference_table,reference_check) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_constraint expected
            ON expected.conrelid=to_regclass('pg_temp.' || spec.reference_table) AND expected.conname=spec.reference_check
            WHERE actual.conrelid=to_regclass(spec.table_name) AND actual.conname=spec.check_name
            AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
            AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(expected.conbin,expected.conrelid)) THEN
            RAISE EXCEPTION 'RUNNER_EVENT_SCHEMA_CHECK_INCOMPATIBLE: %',spec.check_name;
        END IF;
    END LOOP;
    DROP TABLE runner_event_root_check_reference;
    DROP TABLE runner_event_check_reference;
END $$;

-- M6a KF received-only ingress: no Runner, fee or delivery owner.
DO $$
DECLARE spec RECORD; reference_name TEXT; BEGIN
CREATE TABLE IF NOT EXISTS wecom_kf_account_sync (
    account_id TEXT NOT NULL,
    account_order BIGSERIAL NOT NULL,
    tenant_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    corp_id TEXT NOT NULL,
    open_kfid TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    raw_profile TEXT NOT NULL,
    config_version TIMESTAMP NOT NULL,
    requested_generation BIGINT NOT NULL DEFAULT 0,
    completed_generation BIGINT NOT NULL DEFAULT 0,
    cursor TEXT NOT NULL DEFAULT '',
    worker_id TEXT,
    claim_epoch BIGINT NOT NULL DEFAULT 0,
    lease_until TIMESTAMPTZ,
    verification_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (account_id),
    CONSTRAINT ck_kf_account_watermarks CHECK (requested_generation>=completed_generation AND completed_generation>=0 AND claim_epoch>=0),
    CONSTRAINT ck_kf_account_lease CHECK ((worker_id IS NULL AND lease_until IS NULL) OR (worker_id IS NOT NULL AND lease_until IS NOT NULL)),
    CONSTRAINT ck_kf_account_profile CHECK (profile_id=CASE WHEN raw_profile='' THEN 'main' ELSE raw_profile END)
);
CREATE TABLE IF NOT EXISTS channel_session_routes (
    route_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    source TEXT NOT NULL,
    config_id TEXT NOT NULL,
    corp_id TEXT NOT NULL,
    open_kfid TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    chat_kind TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    raw_profile TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_id TEXT,
    legacy_shared BOOLEAN NOT NULL DEFAULT FALSE,
    config_version TIMESTAMP NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (route_id),
    CONSTRAINT ck_kf_route_shape CHECK (source='wecom_kf' AND chat_kind='kf_direct' AND chat_id=open_kfid AND profile_id=CASE WHEN raw_profile='' THEN 'main' ELSE raw_profile END)
);
CREATE TABLE IF NOT EXISTS wecom_kf_inbox (
    account_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    corp_id TEXT NOT NULL,
    open_kfid TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT '',
    route_id TEXT,
    origin BIGINT NOT NULL,
    message_type TEXT NOT NULL,
    send_time BIGINT NOT NULL,
    payload JSONB NOT NULL,
    payload_digest TEXT NOT NULL,
    capability_ciphertext TEXT,
    config_version TIMESTAMP NOT NULL,
    state TEXT NOT NULL DEFAULT 'received',
    received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (account_id,namespace,message_id),
    CONSTRAINT ck_kf_inbox_shape CHECK (namespace IN ('sync','callback') AND state='received' AND origin>=0 AND origin<=10 AND send_time>=0 AND jsonb_typeof(payload)='object' AND ((actor_id='' AND route_id IS NULL) OR (actor_id<>'' AND route_id IS NOT NULL)))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_kf_account_order ON wecom_kf_account_sync(account_order);
CREATE UNIQUE INDEX IF NOT EXISTS uq_kf_account_scope ON wecom_kf_account_sync(tenant_id,config_id,corp_id,open_kfid);
CREATE UNIQUE INDEX IF NOT EXISTS uq_kf_route_scope ON channel_session_routes(tenant_id,source,config_id,corp_id,open_kfid,actor_id,chat_kind,chat_id,profile_id);
CREATE INDEX IF NOT EXISTS idx_kf_inbox_scope ON wecom_kf_inbox(tenant_id,config_id,account_id,received_at);
FOR spec IN SELECT * FROM (VALUES ('wecom_kf_account_sync','account_id','text',TRUE),
('wecom_kf_account_sync','account_order','int8',TRUE),
('wecom_kf_account_sync','tenant_id','text',TRUE),
('wecom_kf_account_sync','config_id','text',TRUE),
('wecom_kf_account_sync','corp_id','text',TRUE),
('wecom_kf_account_sync','open_kfid','text',TRUE),
('wecom_kf_account_sync','profile_id','text',TRUE),
('wecom_kf_account_sync','raw_profile','text',TRUE),
('wecom_kf_account_sync','config_version','timestamp',TRUE),
('wecom_kf_account_sync','requested_generation','int8',TRUE),
('wecom_kf_account_sync','completed_generation','int8',TRUE),
('wecom_kf_account_sync','cursor','text',TRUE),
('wecom_kf_account_sync','worker_id','text',FALSE),
('wecom_kf_account_sync','claim_epoch','int8',TRUE),
('wecom_kf_account_sync','lease_until','timestamptz',FALSE),
('wecom_kf_account_sync','verification_code','text',FALSE),
('wecom_kf_account_sync','created_at','timestamptz',TRUE),
('wecom_kf_account_sync','updated_at','timestamptz',TRUE),
('channel_session_routes','route_id','text',TRUE),
('channel_session_routes','tenant_id','text',TRUE),
('channel_session_routes','source','text',TRUE),
('channel_session_routes','config_id','text',TRUE),
('channel_session_routes','corp_id','text',TRUE),
('channel_session_routes','open_kfid','text',TRUE),
('channel_session_routes','actor_id','text',TRUE),
('channel_session_routes','chat_kind','text',TRUE),
('channel_session_routes','chat_id','text',TRUE),
('channel_session_routes','profile_id','text',TRUE),
('channel_session_routes','raw_profile','text',TRUE),
('channel_session_routes','session_id','text',TRUE),
('channel_session_routes','user_id','text',FALSE),
('channel_session_routes','legacy_shared','bool',TRUE),
('channel_session_routes','config_version','timestamp',TRUE),
('channel_session_routes','created_at','timestamptz',TRUE),
('wecom_kf_inbox','account_id','text',TRUE),
('wecom_kf_inbox','namespace','text',TRUE),
('wecom_kf_inbox','message_id','text',TRUE),
('wecom_kf_inbox','tenant_id','text',TRUE),
('wecom_kf_inbox','config_id','text',TRUE),
('wecom_kf_inbox','corp_id','text',TRUE),
('wecom_kf_inbox','open_kfid','text',TRUE),
('wecom_kf_inbox','actor_id','text',TRUE),
('wecom_kf_inbox','route_id','text',FALSE),
('wecom_kf_inbox','origin','int8',TRUE),
('wecom_kf_inbox','message_type','text',TRUE),
('wecom_kf_inbox','send_time','int8',TRUE),
('wecom_kf_inbox','payload','jsonb',TRUE),
('wecom_kf_inbox','payload_digest','text',TRUE),
('wecom_kf_inbox','capability_ciphertext','text',FALSE),
('wecom_kf_inbox','config_version','timestamp',TRUE),
('wecom_kf_inbox','state','text',TRUE),
('wecom_kf_inbox','received_at','timestamptz',TRUE)) AS wanted(table_name,column_name,type_name,required) LOOP
IF NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=to_regclass(spec.table_name)
 AND a.attname=spec.column_name AND a.atttypid=to_regtype(spec.type_name)
 AND a.attnotnull=spec.required AND NOT a.attisdropped) THEN
 RAISE EXCEPTION 'KF_INGRESS_SCHEMA_COLUMN_INCOMPATIBLE: %.%',spec.table_name,spec.column_name;
END IF; END LOOP;
FOR spec IN SELECT * FROM (VALUES ('wecom_kf_account_sync','wecom_kf_account_sync_pkey',ARRAY['account_id']::text[],TRUE,TRUE),
('wecom_kf_account_sync','uq_kf_account_order',ARRAY['account_order']::text[],TRUE,FALSE),
('wecom_kf_account_sync','uq_kf_account_scope',ARRAY['tenant_id','config_id','corp_id','open_kfid']::text[],TRUE,FALSE),
('channel_session_routes','channel_session_routes_pkey',ARRAY['route_id']::text[],TRUE,TRUE),
('channel_session_routes','uq_kf_route_scope',ARRAY['tenant_id','source','config_id','corp_id','open_kfid','actor_id','chat_kind','chat_id','profile_id']::text[],TRUE,FALSE),
('wecom_kf_inbox','wecom_kf_inbox_pkey',ARRAY['account_id','namespace','message_id']::text[],TRUE,TRUE),
('wecom_kf_inbox','idx_kf_inbox_scope',ARRAY['tenant_id','config_id','account_id','received_at']::text[],FALSE,FALSE)) AS wanted(table_name,index_name,column_names,is_unique,is_primary) LOOP
IF NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am am ON am.oid=c.relam
 WHERE c.oid=to_regclass(spec.index_name) AND i.indrelid=to_regclass(spec.table_name)
 AND i.indisunique=spec.is_unique AND i.indisprimary=spec.is_primary AND i.indisvalid AND i.indisready
 AND am.amname='btree' AND i.indexprs IS NULL AND i.indpred IS NULL
 AND i.indnkeyatts=array_length(spec.column_names,1) AND i.indnatts=i.indnkeyatts
 AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum,ordinal)
 JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum ORDER BY k.ordinal)=spec.column_names) THEN
 RAISE EXCEPTION 'KF_INGRESS_SCHEMA_INDEX_INCOMPATIBLE: %',spec.index_name;
END IF; END LOOP;
IF to_regclass('pg_temp.kf_wecom_kf_account_sync_reference') IS NOT NULL THEN RAISE EXCEPTION 'KF_INGRESS_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE kf_wecom_kf_account_sync_reference (LIKE wecom_kf_account_sync) ON COMMIT DROP;
ALTER TABLE kf_wecom_kf_account_sync_reference ALTER COLUMN requested_generation SET DEFAULT 0;
ALTER TABLE kf_wecom_kf_account_sync_reference ALTER COLUMN completed_generation SET DEFAULT 0;
ALTER TABLE kf_wecom_kf_account_sync_reference ALTER COLUMN cursor SET DEFAULT '';
ALTER TABLE kf_wecom_kf_account_sync_reference ALTER COLUMN claim_epoch SET DEFAULT 0;
ALTER TABLE kf_wecom_kf_account_sync_reference ALTER COLUMN created_at SET DEFAULT clock_timestamp();
ALTER TABLE kf_wecom_kf_account_sync_reference ALTER COLUMN updated_at SET DEFAULT clock_timestamp();
ALTER TABLE kf_wecom_kf_account_sync_reference ADD CONSTRAINT ck_kf_account_watermarks CHECK (requested_generation>=completed_generation AND completed_generation>=0 AND claim_epoch>=0);
ALTER TABLE kf_wecom_kf_account_sync_reference ADD CONSTRAINT ck_kf_account_lease CHECK ((worker_id IS NULL AND lease_until IS NULL) OR (worker_id IS NOT NULL AND lease_until IS NOT NULL));
ALTER TABLE kf_wecom_kf_account_sync_reference ADD CONSTRAINT ck_kf_account_profile CHECK (profile_id=CASE WHEN raw_profile='' THEN 'main' ELSE raw_profile END);
FOR spec IN SELECT conname FROM pg_constraint WHERE conrelid='pg_temp.kf_wecom_kf_account_sync_reference'::regclass LOOP
IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_constraint expected
 ON expected.conrelid='pg_temp.kf_wecom_kf_account_sync_reference'::regclass AND expected.conname=spec.conname
 WHERE actual.conrelid='wecom_kf_account_sync'::regclass AND actual.conname=spec.conname
 AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
 AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(expected.conbin,expected.conrelid)) THEN
 RAISE EXCEPTION 'KF_INGRESS_SCHEMA_CHECK_INCOMPATIBLE: %',spec.conname; END IF; END LOOP;
FOR spec IN SELECT a.attname FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE a.attrelid='pg_temp.kf_wecom_kf_account_sync_reference'::regclass LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_attrdef actual ON actual.adrelid=a.attrelid AND actual.adnum=a.attnum
 JOIN pg_attribute expected_a ON expected_a.attrelid='pg_temp.kf_wecom_kf_account_sync_reference'::regclass AND expected_a.attname=spec.attname
 JOIN pg_attrdef expected ON expected.adrelid=expected_a.attrelid AND expected.adnum=expected_a.attnum
 WHERE a.attrelid='wecom_kf_account_sync'::regclass AND a.attname=spec.attname
 AND pg_get_expr(actual.adbin,actual.adrelid)=pg_get_expr(expected.adbin,expected.adrelid)) THEN
 RAISE EXCEPTION 'KF_INGRESS_SCHEMA_DEFAULT_INCOMPATIBLE: %.%','wecom_kf_account_sync',spec.attname; END IF; END LOOP;
DROP TABLE kf_wecom_kf_account_sync_reference;
IF to_regclass('pg_temp.kf_channel_session_routes_reference') IS NOT NULL THEN RAISE EXCEPTION 'KF_INGRESS_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE kf_channel_session_routes_reference (LIKE channel_session_routes) ON COMMIT DROP;
ALTER TABLE kf_channel_session_routes_reference ALTER COLUMN legacy_shared SET DEFAULT FALSE;
ALTER TABLE kf_channel_session_routes_reference ALTER COLUMN created_at SET DEFAULT clock_timestamp();
ALTER TABLE kf_channel_session_routes_reference ADD CONSTRAINT ck_kf_route_shape CHECK (source='wecom_kf' AND chat_kind='kf_direct' AND chat_id=open_kfid AND profile_id=CASE WHEN raw_profile='' THEN 'main' ELSE raw_profile END);
FOR spec IN SELECT conname FROM pg_constraint WHERE conrelid='pg_temp.kf_channel_session_routes_reference'::regclass LOOP
IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_constraint expected
 ON expected.conrelid='pg_temp.kf_channel_session_routes_reference'::regclass AND expected.conname=spec.conname
 WHERE actual.conrelid='channel_session_routes'::regclass AND actual.conname=spec.conname
 AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
 AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(expected.conbin,expected.conrelid)) THEN
 RAISE EXCEPTION 'KF_INGRESS_SCHEMA_CHECK_INCOMPATIBLE: %',spec.conname; END IF; END LOOP;
FOR spec IN SELECT a.attname FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE a.attrelid='pg_temp.kf_channel_session_routes_reference'::regclass LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_attrdef actual ON actual.adrelid=a.attrelid AND actual.adnum=a.attnum
 JOIN pg_attribute expected_a ON expected_a.attrelid='pg_temp.kf_channel_session_routes_reference'::regclass AND expected_a.attname=spec.attname
 JOIN pg_attrdef expected ON expected.adrelid=expected_a.attrelid AND expected.adnum=expected_a.attnum
 WHERE a.attrelid='channel_session_routes'::regclass AND a.attname=spec.attname
 AND pg_get_expr(actual.adbin,actual.adrelid)=pg_get_expr(expected.adbin,expected.adrelid)) THEN
 RAISE EXCEPTION 'KF_INGRESS_SCHEMA_DEFAULT_INCOMPATIBLE: %.%','channel_session_routes',spec.attname; END IF; END LOOP;
DROP TABLE kf_channel_session_routes_reference;
IF to_regclass('pg_temp.kf_wecom_kf_inbox_reference') IS NOT NULL THEN RAISE EXCEPTION 'KF_INGRESS_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE kf_wecom_kf_inbox_reference (LIKE wecom_kf_inbox) ON COMMIT DROP;
ALTER TABLE kf_wecom_kf_inbox_reference ALTER COLUMN actor_id SET DEFAULT '';
ALTER TABLE kf_wecom_kf_inbox_reference ALTER COLUMN state SET DEFAULT 'received';
ALTER TABLE kf_wecom_kf_inbox_reference ALTER COLUMN received_at SET DEFAULT clock_timestamp();
ALTER TABLE kf_wecom_kf_inbox_reference ADD CONSTRAINT ck_kf_inbox_shape CHECK (namespace IN ('sync','callback') AND state='received' AND origin>=0 AND origin<=10 AND send_time>=0 AND jsonb_typeof(payload)='object' AND ((actor_id='' AND route_id IS NULL) OR (actor_id<>'' AND route_id IS NOT NULL)));
FOR spec IN SELECT conname FROM pg_constraint WHERE conrelid='pg_temp.kf_wecom_kf_inbox_reference'::regclass LOOP
IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_constraint expected
 ON expected.conrelid='pg_temp.kf_wecom_kf_inbox_reference'::regclass AND expected.conname=spec.conname
 WHERE actual.conrelid='wecom_kf_inbox'::regclass AND actual.conname=spec.conname
 AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
 AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(expected.conbin,expected.conrelid)) THEN
 RAISE EXCEPTION 'KF_INGRESS_SCHEMA_CHECK_INCOMPATIBLE: %',spec.conname; END IF; END LOOP;
FOR spec IN SELECT a.attname FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE a.attrelid='pg_temp.kf_wecom_kf_inbox_reference'::regclass LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_attrdef actual ON actual.adrelid=a.attrelid AND actual.adnum=a.attnum
 JOIN pg_attribute expected_a ON expected_a.attrelid='pg_temp.kf_wecom_kf_inbox_reference'::regclass AND expected_a.attname=spec.attname
 JOIN pg_attrdef expected ON expected.adrelid=expected_a.attrelid AND expected.adnum=expected_a.attnum
 WHERE a.attrelid='wecom_kf_inbox'::regclass AND a.attname=spec.attname
 AND pg_get_expr(actual.adbin,actual.adrelid)=pg_get_expr(expected.adbin,expected.adrelid)) THEN
 RAISE EXCEPTION 'KF_INGRESS_SCHEMA_DEFAULT_INCOMPATIBLE: %.%','wecom_kf_inbox',spec.attname; END IF; END LOOP;
DROP TABLE kf_wecom_kf_inbox_reference;
IF pg_get_serial_sequence('wecom_kf_account_sync','account_order') IS NULL THEN RAISE EXCEPTION 'KF_INGRESS_SCHEMA_ORDER_SEQUENCE_INCOMPATIBLE'; END IF;
IF NOT EXISTS (SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum
 WHERE a.attrelid='wecom_kf_account_sync'::regclass AND a.attname='account_order'
 AND pg_get_expr(d.adbin,d.adrelid)=format('nextval(%L::regclass)',pg_get_serial_sequence('wecom_kf_account_sync','account_order')::regclass::text)) THEN
 RAISE EXCEPTION 'KF_INGRESS_SCHEMA_ORDER_DEFAULT_INCOMPATIBLE'; END IF;
END $$;

-- M6a text source admission: original source receipt and execution input facts.
DO $$
DECLARE spec RECORD; BEGIN
ALTER TABLE wecom_kf_account_sync ADD COLUMN IF NOT EXISTS inbox_seq BIGINT NOT NULL DEFAULT 0;
ALTER TABLE wecom_kf_inbox ADD COLUMN IF NOT EXISTS receive_seq BIGINT;
ALTER TABLE wecom_kf_inbox ADD COLUMN IF NOT EXISTS receipt_order BIGSERIAL NOT NULL;
ALTER TABLE wecom_kf_inbox ADD COLUMN IF NOT EXISTS accepted_input_ref TEXT;
CREATE TABLE IF NOT EXISTS agent_runner_inputs (
    input_ref TEXT NOT NULL,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    locator JSONB NOT NULL,
    provenance JSONB NOT NULL,
    intent JSONB NOT NULL,
    intent_digest TEXT NOT NULL,
    receipt_seq BIGINT NOT NULL,
    ordinal BIGSERIAL NOT NULL,
    accepted_runner_id TEXT NOT NULL,
    current_runner_id TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'accepted',
    attached_revision BIGINT,
    deferred_to_runner TEXT,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(input_ref)
);
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='wecom_kf_account_sync'::regclass AND conname='ck_kf_account_inbox_seq') THEN
ALTER TABLE wecom_kf_account_sync ADD CONSTRAINT ck_kf_account_inbox_seq CHECK (inbox_seq>=0);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='wecom_kf_inbox'::regclass AND conname='ck_kf_inbox_receipt_order') THEN
ALTER TABLE wecom_kf_inbox ADD CONSTRAINT ck_kf_inbox_receipt_order CHECK (receipt_order>0 AND (receive_seq IS NULL OR receive_seq>0) AND (accepted_input_ref IS NULL OR receive_seq IS NOT NULL));
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='agent_runner_inputs'::regclass AND conname='ck_runner_input_shape') THEN
ALTER TABLE agent_runner_inputs ADD CONSTRAINT ck_runner_input_shape CHECK (source IN ('wecom_kf','feishu','dingtalk') AND phase IN ('accepted','attached','appended','applied','deferred','cancelled') AND receipt_seq>0 AND ordinal>0 AND (attached_revision IS NULL OR attached_revision>=0) AND jsonb_typeof(locator)='object' AND jsonb_typeof(provenance)='object' AND jsonb_typeof(intent)='object');
END IF;
CREATE UNIQUE INDEX IF NOT EXISTS uq_runner_input_source ON agent_runner_inputs(source_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_runner_input_ordinal ON agent_runner_inputs(ordinal);
CREATE INDEX IF NOT EXISTS idx_runner_input_current ON agent_runner_inputs(current_runner_id,ordinal);
CREATE UNIQUE INDEX IF NOT EXISTS uq_kf_inbox_receipt_order ON wecom_kf_inbox(receipt_order);
CREATE UNIQUE INDEX IF NOT EXISTS uq_kf_inbox_receive_seq ON wecom_kf_inbox(account_id,receive_seq);
FOR spec IN SELECT * FROM (VALUES ('wecom_kf_account_sync','inbox_seq','int8',TRUE),
('wecom_kf_inbox','receive_seq','int8',FALSE),
('wecom_kf_inbox','receipt_order','int8',TRUE),
('wecom_kf_inbox','accepted_input_ref','text',FALSE),
('agent_runner_inputs','input_ref','text',TRUE),
('agent_runner_inputs','source','text',TRUE),
('agent_runner_inputs','source_key','text',TRUE),
('agent_runner_inputs','locator','jsonb',TRUE),
('agent_runner_inputs','provenance','jsonb',TRUE),
('agent_runner_inputs','intent','jsonb',TRUE),
('agent_runner_inputs','intent_digest','text',TRUE),
('agent_runner_inputs','receipt_seq','int8',TRUE),
('agent_runner_inputs','ordinal','int8',TRUE),
('agent_runner_inputs','accepted_runner_id','text',TRUE),
('agent_runner_inputs','current_runner_id','text',TRUE),
('agent_runner_inputs','phase','text',TRUE),
('agent_runner_inputs','attached_revision','int8',FALSE),
('agent_runner_inputs','deferred_to_runner','text',FALSE),
('agent_runner_inputs','accepted_at','timestamptz',TRUE)) AS wanted(table_name,column_name,type_name,required) LOOP
IF NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid
 WHERE a.attrelid=to_regclass(spec.table_name) AND a.attname=spec.column_name
 AND t.typname=spec.type_name AND a.attnotnull=spec.required AND NOT a.attisdropped
 AND a.atttypmod=-1 AND a.attgenerated='' AND a.attidentity='') THEN
 RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_COLUMN_INCOMPATIBLE: %.%',spec.table_name,spec.column_name;
END IF; END LOOP;
FOR spec IN SELECT * FROM (VALUES ('agent_runner_inputs','agent_runner_inputs_pkey',ARRAY['input_ref']::text[],TRUE,TRUE),
('agent_runner_inputs','uq_runner_input_source',ARRAY['source_key']::text[],TRUE,FALSE),
('agent_runner_inputs','uq_runner_input_ordinal',ARRAY['ordinal']::text[],TRUE,FALSE),
('agent_runner_inputs','idx_runner_input_current',ARRAY['current_runner_id','ordinal']::text[],FALSE,FALSE),
('wecom_kf_inbox','uq_kf_inbox_receipt_order',ARRAY['receipt_order']::text[],TRUE,FALSE),
('wecom_kf_inbox','uq_kf_inbox_receive_seq',ARRAY['account_id','receive_seq']::text[],TRUE,FALSE)) AS wanted(table_name,index_name,column_names,is_unique,is_primary) LOOP
IF NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am am ON am.oid=c.relam
 WHERE c.oid=to_regclass(spec.index_name) AND i.indrelid=to_regclass(spec.table_name)
 AND i.indisunique=spec.is_unique AND i.indisprimary=spec.is_primary AND i.indisvalid AND i.indisready
 AND am.amname='btree' AND i.indexprs IS NULL AND i.indpred IS NULL
 AND i.indnkeyatts=array_length(spec.column_names,1) AND i.indnatts=i.indnkeyatts
 AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum,ordinal)
 JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum ORDER BY k.ordinal)=spec.column_names) THEN
 RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_INDEX_INCOMPATIBLE: %',spec.index_name;
END IF; END LOOP;
IF to_regclass('pg_temp.source_wecom_kf_account_sync_reference') IS NOT NULL THEN RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE source_wecom_kf_account_sync_reference (LIKE wecom_kf_account_sync) ON COMMIT DROP;
ALTER TABLE source_wecom_kf_account_sync_reference ALTER COLUMN inbox_seq SET DEFAULT 0;
ALTER TABLE source_wecom_kf_account_sync_reference ADD CONSTRAINT ck_kf_account_inbox_seq CHECK (inbox_seq>=0);
FOR spec IN SELECT conname FROM pg_constraint WHERE conrelid='pg_temp.source_wecom_kf_account_sync_reference'::regclass LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_constraint expected
 ON expected.conrelid='pg_temp.source_wecom_kf_account_sync_reference'::regclass AND expected.conname=spec.conname
 WHERE actual.conrelid='wecom_kf_account_sync'::regclass AND actual.conname=spec.conname
 AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
 AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(expected.conbin,expected.conrelid)) THEN
 RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_CHECK_INCOMPATIBLE: %',spec.conname; END IF; END LOOP;
FOR spec IN SELECT a.attname FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE a.attrelid='pg_temp.source_wecom_kf_account_sync_reference'::regclass LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_attrdef actual ON actual.adrelid=a.attrelid AND actual.adnum=a.attnum
 JOIN pg_attribute expected_a ON expected_a.attrelid='pg_temp.source_wecom_kf_account_sync_reference'::regclass AND expected_a.attname=spec.attname
 JOIN pg_attrdef expected ON expected.adrelid=expected_a.attrelid AND expected.adnum=expected_a.attnum
 WHERE a.attrelid='wecom_kf_account_sync'::regclass AND a.attname=spec.attname
 AND pg_get_expr(actual.adbin,actual.adrelid)=pg_get_expr(expected.adbin,expected.adrelid)) THEN
 RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_DEFAULT_INCOMPATIBLE: %.%','wecom_kf_account_sync',spec.attname; END IF; END LOOP;
DROP TABLE source_wecom_kf_account_sync_reference;
IF to_regclass('pg_temp.source_wecom_kf_inbox_reference') IS NOT NULL THEN RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE source_wecom_kf_inbox_reference (LIKE wecom_kf_inbox) ON COMMIT DROP;
ALTER TABLE source_wecom_kf_inbox_reference ADD CONSTRAINT ck_kf_inbox_receipt_order CHECK (receipt_order>0 AND (receive_seq IS NULL OR receive_seq>0) AND (accepted_input_ref IS NULL OR receive_seq IS NOT NULL));
FOR spec IN SELECT conname FROM pg_constraint WHERE conrelid='pg_temp.source_wecom_kf_inbox_reference'::regclass LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_constraint expected
 ON expected.conrelid='pg_temp.source_wecom_kf_inbox_reference'::regclass AND expected.conname=spec.conname
 WHERE actual.conrelid='wecom_kf_inbox'::regclass AND actual.conname=spec.conname
 AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
 AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(expected.conbin,expected.conrelid)) THEN
 RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_CHECK_INCOMPATIBLE: %',spec.conname; END IF; END LOOP;
FOR spec IN SELECT a.attname FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE a.attrelid='pg_temp.source_wecom_kf_inbox_reference'::regclass LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_attrdef actual ON actual.adrelid=a.attrelid AND actual.adnum=a.attnum
 JOIN pg_attribute expected_a ON expected_a.attrelid='pg_temp.source_wecom_kf_inbox_reference'::regclass AND expected_a.attname=spec.attname
 JOIN pg_attrdef expected ON expected.adrelid=expected_a.attrelid AND expected.adnum=expected_a.attnum
 WHERE a.attrelid='wecom_kf_inbox'::regclass AND a.attname=spec.attname
 AND pg_get_expr(actual.adbin,actual.adrelid)=pg_get_expr(expected.adbin,expected.adrelid)) THEN
 RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_DEFAULT_INCOMPATIBLE: %.%','wecom_kf_inbox',spec.attname; END IF; END LOOP;
DROP TABLE source_wecom_kf_inbox_reference;
IF to_regclass('pg_temp.source_agent_runner_inputs_reference') IS NOT NULL THEN RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE source_agent_runner_inputs_reference (LIKE agent_runner_inputs) ON COMMIT DROP;
ALTER TABLE source_agent_runner_inputs_reference ALTER COLUMN phase SET DEFAULT 'accepted';
ALTER TABLE source_agent_runner_inputs_reference ALTER COLUMN accepted_at SET DEFAULT clock_timestamp();
ALTER TABLE source_agent_runner_inputs_reference ADD CONSTRAINT ck_runner_input_shape CHECK (source IN ('wecom_kf','feishu','dingtalk') AND phase IN ('accepted','attached','appended','applied','deferred','cancelled') AND receipt_seq>0 AND ordinal>0 AND (attached_revision IS NULL OR attached_revision>=0) AND jsonb_typeof(locator)='object' AND jsonb_typeof(provenance)='object' AND jsonb_typeof(intent)='object');
FOR spec IN SELECT conname FROM pg_constraint WHERE conrelid='pg_temp.source_agent_runner_inputs_reference'::regclass LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_constraint expected
 ON expected.conrelid='pg_temp.source_agent_runner_inputs_reference'::regclass AND expected.conname=spec.conname
 WHERE actual.conrelid='agent_runner_inputs'::regclass AND actual.conname=spec.conname
 AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
 AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(expected.conbin,expected.conrelid)) THEN
 RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_CHECK_INCOMPATIBLE: %',spec.conname; END IF; END LOOP;
FOR spec IN SELECT a.attname FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE a.attrelid='pg_temp.source_agent_runner_inputs_reference'::regclass LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_attrdef actual ON actual.adrelid=a.attrelid AND actual.adnum=a.attnum
 JOIN pg_attribute expected_a ON expected_a.attrelid='pg_temp.source_agent_runner_inputs_reference'::regclass AND expected_a.attname=spec.attname
 JOIN pg_attrdef expected ON expected.adrelid=expected_a.attrelid AND expected.adnum=expected_a.attnum
 WHERE a.attrelid='agent_runner_inputs'::regclass AND a.attname=spec.attname
 AND pg_get_expr(actual.adbin,actual.adrelid)=pg_get_expr(expected.adbin,expected.adrelid)) THEN
 RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_DEFAULT_INCOMPATIBLE: %.%','agent_runner_inputs',spec.attname; END IF; END LOOP;
DROP TABLE source_agent_runner_inputs_reference;
IF pg_get_serial_sequence('wecom_kf_inbox','receipt_order') IS NULL THEN RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_SEQUENCE_INCOMPATIBLE'; END IF;
IF NOT EXISTS (SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum
 WHERE a.attrelid='wecom_kf_inbox'::regclass AND a.attname='receipt_order'
 AND pg_get_expr(d.adbin,d.adrelid)=format('nextval(%L::regclass)',pg_get_serial_sequence('wecom_kf_inbox','receipt_order')::regclass::text)) THEN
 RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_SEQUENCE_DEFAULT_INCOMPATIBLE'; END IF;
IF pg_get_serial_sequence('agent_runner_inputs','ordinal') IS NULL THEN RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_SEQUENCE_INCOMPATIBLE'; END IF;
IF NOT EXISTS (SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum
 WHERE a.attrelid='agent_runner_inputs'::regclass AND a.attname='ordinal'
 AND pg_get_expr(d.adbin,d.adrelid)=format('nextval(%L::regclass)',pg_get_serial_sequence('agent_runner_inputs','ordinal')::regclass::text)) THEN
 RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_SEQUENCE_DEFAULT_INCOMPATIBLE'; END IF;
IF EXISTS (SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid='wecom_kf_inbox'::regclass AND a.attname='receive_seq') THEN RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_NULL_DEFAULT_INCOMPATIBLE'; END IF;
IF EXISTS (SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid='wecom_kf_inbox'::regclass AND a.attname='accepted_input_ref') THEN RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_NULL_DEFAULT_INCOMPATIBLE'; END IF;
IF EXISTS (SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid='agent_runner_inputs'::regclass AND a.attname='attached_revision') THEN RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_NULL_DEFAULT_INCOMPATIBLE'; END IF;
IF EXISTS (SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid='agent_runner_inputs'::regclass AND a.attname='deferred_to_runner') THEN RAISE EXCEPTION 'SOURCE_INPUT_SCHEMA_NULL_DEFAULT_INCOMPATIBLE'; END IF;
END $$;



-- Native accepted AI voice preparation; private facts, no SQL FK.
DO $$
DECLARE spec RECORD;
BEGIN
CREATE TABLE IF NOT EXISTS wecom_kf_input_preparations (
    preparation_ref TEXT PRIMARY KEY,
    input_ref TEXT NOT NULL,
    operation_version INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    intent_digest TEXT NOT NULL,
    provenance JSONB NOT NULL,
    media_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    artifact JSONB,
    transcript TEXT,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    result_kind TEXT,
    fee_owner_runner_id TEXT,
    authorized_attempt INTEGER,
    authorized_worker_id TEXT,
    physical_call_id TEXT,
    receipt_id TEXT,
    provider_status BIGINT,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    started_at TIMESTAMPTZ,
    io_config_version TIMESTAMPTZ,
    dispatch_observed_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ,
    UNIQUE(input_ref,operation_version),
    CONSTRAINT ck_kf_preparation_shape CHECK (
        operation_version>0 AND length(tenant_id)>0 AND length(intent_digest)=64
        AND jsonb_typeof(provenance)='object'
        AND phase IN ('media_ready','started','known','unknown')
        AND (artifact IS NULL OR jsonb_typeof(artifact)='object')
        AND (transcript IS NULL OR octet_length(transcript)<=32768)
        AND (authorized_attempt IS NULL OR authorized_attempt>0)
        AND (result_kind IS NULL OR result_kind IN ('recognition','preflight','provider'))
        AND (phase<>'media_ready' OR artifact IS NOT NULL)
        AND (phase<>'known' OR (result_kind IS NOT NULL AND transcript IS NOT NULL AND observed_at IS NOT NULL))
        AND (NOT success OR (phase='known' AND length(transcript)>0))
        AND ((receipt_id IS NULL AND fee_owner_runner_id IS NULL AND authorized_attempt IS NULL
              AND authorized_worker_id IS NULL AND physical_call_id IS NULL AND started_at IS NULL AND io_config_version IS NULL AND dispatch_observed_at IS NULL)
             OR (receipt_id IS NOT NULL AND fee_owner_runner_id IS NOT NULL AND authorized_attempt IS NOT NULL
              AND authorized_worker_id IS NOT NULL AND physical_call_id IS NOT NULL AND started_at IS NOT NULL AND io_config_version IS NOT NULL AND dispatch_observed_at IS NOT NULL))
        AND (phase NOT IN ('started','unknown') OR receipt_id IS NOT NULL)
        AND (result_kind IS DISTINCT FROM 'provider' OR receipt_id IS NOT NULL)
    )
);
IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid='wecom_kf_input_preparations'::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'VOICE_PREPARATION_SCHEMA_RELATION_INCOMPATIBLE'; END IF;
IF to_regclass('pg_temp.voice_preparation_reference') IS NOT NULL THEN
 RAISE EXCEPTION 'VOICE_PREPARATION_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE voice_preparation_reference (
    preparation_ref TEXT PRIMARY KEY,
    input_ref TEXT NOT NULL,
    operation_version INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    intent_digest TEXT NOT NULL,
    provenance JSONB NOT NULL,
    media_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    artifact JSONB,
    transcript TEXT,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    result_kind TEXT,
    fee_owner_runner_id TEXT,
    authorized_attempt INTEGER,
    authorized_worker_id TEXT,
    physical_call_id TEXT,
    receipt_id TEXT,
    provider_status BIGINT,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    started_at TIMESTAMPTZ,
    io_config_version TIMESTAMPTZ,
    dispatch_observed_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ,
    UNIQUE(input_ref,operation_version),
    CONSTRAINT ck_kf_preparation_shape CHECK (
        operation_version>0 AND length(tenant_id)>0 AND length(intent_digest)=64
        AND jsonb_typeof(provenance)='object'
        AND phase IN ('media_ready','started','known','unknown')
        AND (artifact IS NULL OR jsonb_typeof(artifact)='object')
        AND (transcript IS NULL OR octet_length(transcript)<=32768)
        AND (authorized_attempt IS NULL OR authorized_attempt>0)
        AND (result_kind IS NULL OR result_kind IN ('recognition','preflight','provider'))
        AND (phase<>'media_ready' OR artifact IS NOT NULL)
        AND (phase<>'known' OR (result_kind IS NOT NULL AND transcript IS NOT NULL AND observed_at IS NOT NULL))
        AND (NOT success OR (phase='known' AND length(transcript)>0))
        AND ((receipt_id IS NULL AND fee_owner_runner_id IS NULL AND authorized_attempt IS NULL
              AND authorized_worker_id IS NULL AND physical_call_id IS NULL AND started_at IS NULL AND io_config_version IS NULL AND dispatch_observed_at IS NULL)
             OR (receipt_id IS NOT NULL AND fee_owner_runner_id IS NOT NULL AND authorized_attempt IS NOT NULL
              AND authorized_worker_id IS NOT NULL AND physical_call_id IS NOT NULL AND started_at IS NOT NULL AND io_config_version IS NOT NULL AND dispatch_observed_at IS NOT NULL))
        AND (phase NOT IN ('started','unknown') OR receipt_id IS NOT NULL)
        AND (result_kind IS DISTINCT FROM 'provider' OR receipt_id IS NOT NULL)
    )
) ON COMMIT DROP;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid='wecom_kf_input_preparations'::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid='pg_temp.voice_preparation_reference'::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'VOICE_PREPARATION_SCHEMA_COLUMNS_INCOMPATIBLE'; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid='pg_temp.voice_preparation_reference'::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_attribute actual WHERE actual.attrelid='wecom_kf_input_preparations'::regclass
  AND actual.attname=spec.attname AND actual.attnum>0 AND NOT actual.attisdropped
  AND actual.atttypid=spec.atttypid AND actual.atttypmod=spec.atttypmod AND actual.attnotnull=spec.attnotnull
  AND actual.attidentity=spec.attidentity AND actual.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'VOICE_PREPARATION_SCHEMA_COLUMN_INCOMPATIBLE: %',spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum
  WHERE a.attrelid='wecom_kf_input_preparations'::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d
  WHERE d.adrelid='pg_temp.voice_preparation_reference'::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'VOICE_PREPARATION_SCHEMA_DEFAULT_INCOMPATIBLE: %',spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid='wecom_kf_input_preparations'::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid='pg_temp.voice_preparation_reference'::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'VOICE_PREPARATION_SCHEMA_CONSTRAINTS_INCOMPATIBLE'; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid='pg_temp.voice_preparation_reference'::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint actual WHERE actual.conrelid='wecom_kf_input_preparations'::regclass
   AND actual.conname=spec.conname AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
   AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)) THEN
   RAISE EXCEPTION 'VOICE_PREPARATION_SCHEMA_CHECK_INCOMPATIBLE'; END IF;
 ELSE
  IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_index i ON i.indexrelid=actual.conindid
   WHERE actual.conrelid='wecom_kf_input_preparations'::regclass AND actual.contype=spec.contype
   AND actual.conkey=spec.conkey AND NOT actual.condeferrable AND i.indisvalid AND i.indisready
   AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=array_length(spec.conkey,1)
   AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'VOICE_PREPARATION_SCHEMA_INDEX_INCOMPATIBLE'; END IF;
 END IF;
END LOOP;
DROP TABLE voice_preparation_reference;
END $$;

-- M6a fixed receipt classification and ordinary context history; no task dispatch.
DO $$
DECLARE spec RECORD; table_name TEXT; reference_name TEXT; BEGIN
CREATE TABLE IF NOT EXISTS wecom_kf_receipt_classifications (
    account_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    route_id TEXT,
    payload_digest TEXT NOT NULL,
    scope JSONB NOT NULL,
    classification TEXT NOT NULL,
    observed_state INTEGER,
    observed_at TIMESTAMPTZ,
    io_config_version TIMESTAMP,
    event_state INTEGER,
    classification_resolved BOOLEAN NOT NULL,
    business_pending BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(account_id,namespace,message_id),
    CONSTRAINT ck_kf_classification_shape CHECK (
        namespace IN ('sync','callback') AND length(payload_digest)=64 AND jsonb_typeof(scope)='object'
        AND classification IN ('ai','human','ended','employee','unknown','event')
        AND (observed_state IS NULL OR observed_state BETWEEN 0 AND 4)
        AND (event_state IS NULL OR (classification='event' AND event_state BETWEEN 0 AND 4))
        AND ((observed_state IS NULL AND observed_at IS NULL AND io_config_version IS NULL)
             OR (observed_state IS NOT NULL AND observed_at IS NOT NULL AND io_config_version IS NOT NULL))
        AND (classification='event' OR (route_id IS NOT NULL AND (classification='employee' OR observed_state IS NOT NULL)))
        AND (classification<>'ai' OR observed_state=1)
        AND (classification<>'human' OR observed_state=3)
        AND (classification<>'ended' OR observed_state=4)
    )
);
CREATE TABLE IF NOT EXISTS wecom_kf_context_consumptions (
    account_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    route_id TEXT,
    session_id TEXT,
    payload_digest TEXT NOT NULL,
    accepted_input_ref TEXT,
    completion_observation JSONB,
    history_id TEXT,
    target_message_id TEXT,
    disposition TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(account_id,namespace,message_id),
    CONSTRAINT ck_kf_context_consumption_shape CHECK (
        namespace IN ('sync','callback') AND length(payload_digest)=64
        AND disposition IN ('persisted','pending_asr','pending_history','pending_target','recalled','accepted_target','pending_scope','pending_business','source_terminal')
        AND (disposition<>'source_terminal' OR (route_id IS NOT NULL AND session_id IS NOT NULL AND accepted_input_ref IS NOT NULL AND jsonb_typeof(completion_observation)='object'))
        AND (disposition NOT IN ('persisted','pending_asr') OR (route_id IS NOT NULL AND session_id IS NOT NULL AND history_id IS NOT NULL))
        AND (disposition NOT IN ('pending_target','recalled','accepted_target') OR (route_id IS NOT NULL AND target_message_id IS NOT NULL))
    )
);
CREATE TABLE IF NOT EXISTS wecom_kf_context_task_intents (
    task_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    route_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    history_id TEXT NOT NULL,
    task_name TEXT NOT NULL,
    operation_version INTEGER NOT NULL,
    scope JSONB NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending_adapter',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(account_id,namespace,message_id,task_name,operation_version),
    CONSTRAINT ck_kf_context_task_shape CHECK (
        namespace IN ('sync','callback') AND operation_version=1
        AND task_name IN ('lead_refresh','external_push_human')
        AND state='pending_adapter' AND jsonb_typeof(scope)='object'
    )
);
IF to_regclass('pg_temp.context_reference_wecom_kf_receipt_classifications') IS NOT NULL THEN RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE context_reference_wecom_kf_receipt_classifications (
    account_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    route_id TEXT,
    payload_digest TEXT NOT NULL,
    scope JSONB NOT NULL,
    classification TEXT NOT NULL,
    observed_state INTEGER,
    observed_at TIMESTAMPTZ,
    io_config_version TIMESTAMP,
    event_state INTEGER,
    classification_resolved BOOLEAN NOT NULL,
    business_pending BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(account_id,namespace,message_id),
    CONSTRAINT ck_kf_classification_shape CHECK (
        namespace IN ('sync','callback') AND length(payload_digest)=64 AND jsonb_typeof(scope)='object'
        AND classification IN ('ai','human','ended','employee','unknown','event')
        AND (observed_state IS NULL OR observed_state BETWEEN 0 AND 4)
        AND (event_state IS NULL OR (classification='event' AND event_state BETWEEN 0 AND 4))
        AND ((observed_state IS NULL AND observed_at IS NULL AND io_config_version IS NULL)
             OR (observed_state IS NOT NULL AND observed_at IS NOT NULL AND io_config_version IS NOT NULL))
        AND (classification='event' OR (route_id IS NOT NULL AND (classification='employee' OR observed_state IS NOT NULL)))
        AND (classification<>'ai' OR observed_state=1)
        AND (classification<>'human' OR observed_state=3)
        AND (classification<>'ended' OR observed_state=4)
    )
) ON COMMIT DROP;
table_name:='wecom_kf_receipt_classifications'; reference_name:='pg_temp.context_reference_wecom_kf_receipt_classifications';
IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid=table_name::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_RELATION_INCOMPATIBLE: %',table_name; END IF;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid=table_name::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_COLUMNS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=table_name::regclass
  AND a.attname=spec.attname AND a.attnum>0 AND NOT a.attisdropped
  AND a.atttypid=spec.atttypid AND a.atttypmod=spec.atttypmod AND a.attnotnull=spec.attnotnull
  AND a.attidentity=spec.attidentity AND a.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_COLUMN_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a
  ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d
  WHERE d.adrelid=reference_name::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_DEFAULT_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_name::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_CONSTRAINTS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint actual WHERE actual.conrelid=table_name::regclass
   AND actual.conname=spec.conname AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
   AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)) THEN
   RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_CHECK_INCOMPATIBLE: %',table_name; END IF;
 ELSE
  IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_index i ON i.indexrelid=actual.conindid
   WHERE actual.conrelid=table_name::regclass AND actual.contype=spec.contype
   AND (SELECT array_agg(a.attname ORDER BY k.position) FROM unnest(actual.conkey)
        WITH ORDINALITY AS k(attnum,position) JOIN pg_attribute a
        ON a.attrelid=actual.conrelid AND a.attnum=k.attnum AND NOT a.attisdropped)
     = (SELECT array_agg(a.attname ORDER BY k.position) FROM unnest(spec.conkey)
        WITH ORDINALITY AS k(attnum,position) JOIN pg_attribute a
        ON a.attrelid=spec.conrelid AND a.attnum=k.attnum AND NOT a.attisdropped)
   AND NOT actual.condeferrable AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL
   AND i.indnkeyatts=array_length(spec.conkey,1) AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_INDEX_INCOMPATIBLE: %',table_name; END IF;
 END IF;
END LOOP;
DROP TABLE context_reference_wecom_kf_receipt_classifications;
IF to_regclass('pg_temp.context_reference_wecom_kf_context_consumptions') IS NOT NULL THEN RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_REFERENCE_COLLISION'; END IF;
IF to_regclass('pg_temp.context_compat_consumption') IS NOT NULL THEN RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE context_compat_consumption (
    account_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    route_id TEXT,
    session_id TEXT,
    payload_digest TEXT NOT NULL,
    accepted_input_ref TEXT,
    completion_observation JSONB,
    history_id TEXT,
    target_message_id TEXT,
    disposition TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(account_id,namespace,message_id),
    CONSTRAINT ck_kf_context_consumption_shape CHECK (
        namespace IN ('sync','callback') AND length(payload_digest)=64
        AND disposition IN ('persisted','pending_history','pending_target','recalled','accepted_target','pending_scope','pending_business','source_terminal')
        AND (disposition<>'source_terminal' OR (route_id IS NOT NULL AND session_id IS NOT NULL AND accepted_input_ref IS NOT NULL AND jsonb_typeof(completion_observation)='object'))
        AND (disposition<>'persisted' OR (route_id IS NOT NULL AND session_id IS NOT NULL AND history_id IS NOT NULL))
        AND (disposition NOT IN ('pending_target','recalled','accepted_target') OR (route_id IS NOT NULL AND target_message_id IS NOT NULL))
    )
) ON COMMIT DROP;
CREATE TEMP TABLE context_reference_wecom_kf_context_consumptions (
    account_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    route_id TEXT,
    session_id TEXT,
    payload_digest TEXT NOT NULL,
    accepted_input_ref TEXT,
    completion_observation JSONB,
    history_id TEXT,
    target_message_id TEXT,
    disposition TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(account_id,namespace,message_id),
    CONSTRAINT ck_kf_context_consumption_shape CHECK (
        namespace IN ('sync','callback') AND length(payload_digest)=64
        AND disposition IN ('persisted','pending_asr','pending_history','pending_target','recalled','accepted_target','pending_scope','pending_business','source_terminal')
        AND (disposition<>'source_terminal' OR (route_id IS NOT NULL AND session_id IS NOT NULL AND accepted_input_ref IS NOT NULL AND jsonb_typeof(completion_observation)='object'))
        AND (disposition NOT IN ('persisted','pending_asr') OR (route_id IS NOT NULL AND session_id IS NOT NULL AND history_id IS NOT NULL))
        AND (disposition NOT IN ('pending_target','recalled','accepted_target') OR (route_id IS NOT NULL AND target_message_id IS NOT NULL))
    )
) ON COMMIT DROP;
table_name:='wecom_kf_context_consumptions'; reference_name:='pg_temp.context_reference_wecom_kf_context_consumptions';
IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid=table_name::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_RELATION_INCOMPATIBLE: %',table_name; END IF;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid=table_name::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_COLUMNS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=table_name::regclass
  AND a.attname=spec.attname AND a.attnum>0 AND NOT a.attisdropped
  AND a.atttypid=spec.atttypid AND a.atttypmod=spec.atttypmod AND a.attnotnull=spec.attnotnull
  AND a.attidentity=spec.attidentity AND a.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_COLUMN_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a
  ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d
  WHERE d.adrelid=reference_name::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_DEFAULT_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_name::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_CONSTRAINTS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint actual WHERE actual.conrelid=table_name::regclass
   AND actual.conname=spec.conname AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
   AND (pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)
   OR (spec.conname='ck_kf_context_consumption_shape' AND pg_get_expr(actual.conbin,actual.conrelid)=
       (SELECT pg_get_expr(c.conbin,c.conrelid) FROM pg_constraint c
        WHERE c.conrelid='pg_temp.context_compat_consumption'::regclass
          AND c.conname='ck_kf_context_consumption_shape')))) THEN
   RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_CHECK_INCOMPATIBLE: %',table_name; END IF;
 ELSE
  IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_index i ON i.indexrelid=actual.conindid
   WHERE actual.conrelid=table_name::regclass AND actual.contype=spec.contype
   AND (SELECT array_agg(a.attname ORDER BY k.position) FROM unnest(actual.conkey)
        WITH ORDINALITY AS k(attnum,position) JOIN pg_attribute a
        ON a.attrelid=actual.conrelid AND a.attnum=k.attnum AND NOT a.attisdropped)
     = (SELECT array_agg(a.attname ORDER BY k.position) FROM unnest(spec.conkey)
        WITH ORDINALITY AS k(attnum,position) JOIN pg_attribute a
        ON a.attrelid=spec.conrelid AND a.attnum=k.attnum AND NOT a.attisdropped)
   AND NOT actual.condeferrable AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL
   AND i.indnkeyatts=array_length(spec.conkey,1) AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_INDEX_INCOMPATIBLE: %',table_name; END IF;
 END IF;
END LOOP;
DROP TABLE context_reference_wecom_kf_context_consumptions;
DROP TABLE context_compat_consumption;
IF to_regclass('pg_temp.context_reference_wecom_kf_context_task_intents') IS NOT NULL THEN RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE context_reference_wecom_kf_context_task_intents (
    task_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    route_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    history_id TEXT NOT NULL,
    task_name TEXT NOT NULL,
    operation_version INTEGER NOT NULL,
    scope JSONB NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending_adapter',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(account_id,namespace,message_id,task_name,operation_version),
    CONSTRAINT ck_kf_context_task_shape CHECK (
        namespace IN ('sync','callback') AND operation_version=1
        AND task_name IN ('lead_refresh','external_push_human')
        AND state='pending_adapter' AND jsonb_typeof(scope)='object'
    )
) ON COMMIT DROP;
CREATE TEMP TABLE context_reference_task_effects (LIKE context_reference_wecom_kf_context_task_intents INCLUDING ALL) ON COMMIT DROP;
ALTER TABLE context_reference_task_effects DROP CONSTRAINT ck_kf_context_task_shape;
ALTER TABLE context_reference_task_effects ADD CONSTRAINT ck_kf_context_task_shape CHECK (
 namespace IN ('sync','callback') AND operation_version=1
 AND task_name IN ('lead_refresh','external_push_human','external_push')
 AND state IN ('pending_adapter','claimed','started','dispatch_returned','unknown','suppressed') AND jsonb_typeof(scope)='object');
table_name:='wecom_kf_context_task_intents'; reference_name:='pg_temp.context_reference_wecom_kf_context_task_intents';
IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid=table_name::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_RELATION_INCOMPATIBLE: %',table_name; END IF;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid=table_name::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_COLUMNS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=table_name::regclass
  AND a.attname=spec.attname AND a.attnum>0 AND NOT a.attisdropped
  AND a.atttypid=spec.atttypid AND a.atttypmod=spec.atttypmod AND a.attnotnull=spec.attnotnull
  AND a.attidentity=spec.attidentity AND a.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_COLUMN_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a
  ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d
  WHERE d.adrelid=reference_name::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_DEFAULT_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_name::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_CONSTRAINTS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint actual WHERE actual.conrelid=table_name::regclass
   AND actual.conname=spec.conname AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
   AND (pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)
   OR pg_get_expr(actual.conbin,actual.conrelid)=(SELECT pg_get_expr(v.conbin,v.conrelid) FROM pg_constraint v WHERE v.conrelid='pg_temp.context_reference_task_effects'::regclass AND v.conname='ck_kf_context_task_shape'))) THEN
   RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_CHECK_INCOMPATIBLE: %',table_name; END IF;
 ELSE
  IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_index i ON i.indexrelid=actual.conindid
   WHERE actual.conrelid=table_name::regclass AND actual.contype=spec.contype
   AND (SELECT array_agg(a.attname ORDER BY k.position) FROM unnest(actual.conkey)
        WITH ORDINALITY AS k(attnum,position) JOIN pg_attribute a
        ON a.attrelid=actual.conrelid AND a.attnum=k.attnum AND NOT a.attisdropped)
     = (SELECT array_agg(a.attname ORDER BY k.position) FROM unnest(spec.conkey)
        WITH ORDINALITY AS k(attnum,position) JOIN pg_attribute a
        ON a.attrelid=spec.conrelid AND a.attnum=k.attnum AND NOT a.attisdropped)
   AND NOT actual.condeferrable AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL
   AND i.indnkeyatts=array_length(spec.conkey,1) AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_INDEX_INCOMPATIBLE: %',table_name; END IF;
 END IF;
END LOOP;
DROP TABLE context_reference_task_effects;
DROP TABLE context_reference_wecom_kf_context_task_intents;
END $$;

-- M6a human/employee voice owner, pending display and original independent ASR billing.
DO $$
DECLARE spec RECORD; table_name TEXT; reference_name TEXT; BEGIN
IF to_regclass('pg_temp.context_voice_old_consumption') IS NOT NULL THEN RAISE EXCEPTION 'KF_CONTEXT_VOICE_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE context_voice_old_consumption (
    account_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    route_id TEXT,
    session_id TEXT,
    payload_digest TEXT NOT NULL,
    accepted_input_ref TEXT,
    completion_observation JSONB,
    history_id TEXT,
    target_message_id TEXT,
    disposition TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(account_id,namespace,message_id),
    CONSTRAINT ck_kf_context_consumption_shape CHECK (
        namespace IN ('sync','callback') AND length(payload_digest)=64
        AND disposition IN ('persisted','pending_history','pending_target','recalled','accepted_target','pending_scope','pending_business','source_terminal')
        AND (disposition<>'source_terminal' OR (route_id IS NOT NULL AND session_id IS NOT NULL AND accepted_input_ref IS NOT NULL AND jsonb_typeof(completion_observation)='object'))
        AND (disposition<>'persisted' OR (route_id IS NOT NULL AND session_id IS NOT NULL AND history_id IS NOT NULL))
        AND (disposition NOT IN ('pending_target','recalled','accepted_target') OR (route_id IS NOT NULL AND target_message_id IS NOT NULL))
    )
) ON COMMIT DROP;
IF to_regclass('pg_temp.context_voice_new_consumption') IS NOT NULL THEN RAISE EXCEPTION 'KF_CONTEXT_VOICE_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE context_voice_new_consumption (
    account_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    route_id TEXT,
    session_id TEXT,
    payload_digest TEXT NOT NULL,
    accepted_input_ref TEXT,
    completion_observation JSONB,
    history_id TEXT,
    target_message_id TEXT,
    disposition TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(account_id,namespace,message_id),
    CONSTRAINT ck_kf_context_consumption_shape CHECK (
        namespace IN ('sync','callback') AND length(payload_digest)=64
        AND disposition IN ('persisted','pending_asr','pending_history','pending_target','recalled','accepted_target','pending_scope','pending_business','source_terminal')
        AND (disposition<>'source_terminal' OR (route_id IS NOT NULL AND session_id IS NOT NULL AND accepted_input_ref IS NOT NULL AND jsonb_typeof(completion_observation)='object'))
        AND (disposition NOT IN ('persisted','pending_asr') OR (route_id IS NOT NULL AND session_id IS NOT NULL AND history_id IS NOT NULL))
        AND (disposition NOT IN ('pending_target','recalled','accepted_target') OR (route_id IS NOT NULL AND target_message_id IS NOT NULL))
    )
) ON COMMIT DROP;
IF NOT EXISTS (SELECT 1 FROM pg_constraint a WHERE a.conrelid='wecom_kf_context_consumptions'::regclass
 AND a.conname='ck_kf_context_consumption_shape' AND a.contype='c' AND a.convalidated AND NOT a.connoinherit
 AND pg_get_expr(a.conbin,a.conrelid) IN (
  SELECT pg_get_expr(c.conbin,c.conrelid) FROM pg_constraint c
  WHERE c.conrelid IN ('pg_temp.context_voice_old_consumption'::regclass,'pg_temp.context_voice_new_consumption'::regclass)
    AND c.conname='ck_kf_context_consumption_shape')) THEN
 RAISE EXCEPTION 'KF_CONTEXT_VOICE_CONSUMPTION_INCOMPATIBLE'; END IF;
ALTER TABLE wecom_kf_context_consumptions DROP CONSTRAINT ck_kf_context_consumption_shape;
ALTER TABLE wecom_kf_context_consumptions ADD CONSTRAINT ck_kf_context_consumption_shape CHECK (
        namespace IN ('sync','callback') AND length(payload_digest)=64
        AND disposition IN ('persisted','pending_asr','pending_history','pending_target','recalled','accepted_target','pending_scope','pending_business','source_terminal')
        AND (disposition<>'source_terminal' OR (route_id IS NOT NULL AND session_id IS NOT NULL AND accepted_input_ref IS NOT NULL AND jsonb_typeof(completion_observation)='object'))
        AND (disposition NOT IN ('persisted','pending_asr') OR (route_id IS NOT NULL AND session_id IS NOT NULL AND history_id IS NOT NULL))
        AND (disposition NOT IN ('pending_target','recalled','accepted_target') OR (route_id IS NOT NULL AND target_message_id IS NOT NULL))
    );
table_name:='wecom_kf_context_consumptions'; reference_name:='pg_temp.context_voice_new_consumption';
IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid=table_name::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_RELATION_INCOMPATIBLE: %',table_name; END IF;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid=table_name::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_COLUMNS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=table_name::regclass
  AND a.attname=spec.attname AND a.attnum>0 AND NOT a.attisdropped
  AND a.atttypid=spec.atttypid AND a.atttypmod=spec.atttypmod AND a.attnotnull=spec.attnotnull
  AND a.attidentity=spec.attidentity AND a.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_COLUMN_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a
  ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d
  WHERE d.adrelid=reference_name::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_DEFAULT_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_name::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_CONSTRAINTS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint actual WHERE actual.conrelid=table_name::regclass
   AND actual.conname=spec.conname AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
   AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)) THEN
   RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_CHECK_INCOMPATIBLE: %',table_name; END IF;
 ELSE
  IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_index i ON i.indexrelid=actual.conindid
   WHERE actual.conrelid=table_name::regclass AND actual.contype=spec.contype
   AND (SELECT array_agg(a.attname ORDER BY k.position) FROM unnest(actual.conkey)
        WITH ORDINALITY AS k(attnum,position) JOIN pg_attribute a
        ON a.attrelid=actual.conrelid AND a.attnum=k.attnum AND NOT a.attisdropped)
     = (SELECT array_agg(a.attname ORDER BY k.position) FROM unnest(spec.conkey)
        WITH ORDINALITY AS k(attnum,position) JOIN pg_attribute a
        ON a.attrelid=spec.conrelid AND a.attnum=k.attnum AND NOT a.attisdropped)
   AND NOT actual.condeferrable AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL
   AND i.indnkeyatts=array_length(spec.conkey,1) AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_INDEX_INCOMPATIBLE: %',table_name; END IF;
 END IF;
END LOOP;
DROP TABLE context_voice_old_consumption;
DROP TABLE context_voice_new_consumption;
CREATE TABLE IF NOT EXISTS wecom_kf_context_voice_preparations (
    operation_ref TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    message_id TEXT NOT NULL,
    operation_version INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    scope JSONB NOT NULL,
    io_config_version TIMESTAMP NOT NULL,
    media_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    artifact JSONB,
    authorized_epoch BIGINT NOT NULL DEFAULT 0,
    price_snapshot JSONB,
    record_id TEXT NOT NULL UNIQUE,
    cost NUMERIC,
    finance_pending BOOLEAN NOT NULL,
    success BOOLEAN,
    transcript TEXT,
    result_kind TEXT,
    provider_status BIGINT,
    failure_code TEXT,
    history_projection TEXT NOT NULL DEFAULT 'pending',
    started_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(account_id,namespace,message_id,operation_version),
    CONSTRAINT ck_kf_context_voice_shape CHECK (
        namespace='sync' AND operation_version=1 AND length(payload_digest)=64
        AND jsonb_typeof(scope)='object' AND length(media_id)>0 AND authorized_epoch>=0
        AND phase IN ('media_ready','started','unknown','known')
        AND history_projection IN ('pending','projected','cleared','recalled')
        AND (artifact IS NULL OR jsonb_typeof(artifact)='object')
        AND (price_snapshot IS NULL OR jsonb_typeof(price_snapshot)='object')
        AND (cost IS NULL OR (cost>=0 AND cost<>'NaN'::numeric))
        AND ((phase='known' AND success IS NOT NULL AND transcript IS NOT NULL
              AND result_kind IN ('recognition','preflight','asr') AND observed_at IS NOT NULL
              AND cost IS NOT NULL AND NOT finance_pending)
          OR (phase IN ('media_ready','started','unknown') AND success IS NULL AND transcript IS NULL
              AND result_kind IS NULL AND observed_at IS NULL AND cost IS NULL))
        AND (phase<>'media_ready' OR (artifact IS NOT NULL AND NOT finance_pending))
        AND (phase NOT IN ('started','unknown') OR (authorized_epoch>0 AND price_snapshot IS NOT NULL
             AND artifact IS NOT NULL AND started_at IS NOT NULL AND lease_expires_at IS NOT NULL AND finance_pending))
        AND (result_kind IS DISTINCT FROM 'asr' OR (authorized_epoch>0 AND price_snapshot IS NOT NULL
             AND provider_status IS NOT NULL))
        AND (result_kind IS DISTINCT FROM 'recognition' OR (authorized_epoch=0 AND success AND length(transcript)>0))
        AND (success IS DISTINCT FROM TRUE OR length(transcript)>0)
    )
);
IF to_regclass('pg_temp.context_voice_reference_preparations') IS NOT NULL THEN RAISE EXCEPTION 'KF_CONTEXT_VOICE_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE context_voice_reference_preparations (
    operation_ref TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    message_id TEXT NOT NULL,
    operation_version INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    scope JSONB NOT NULL,
    io_config_version TIMESTAMP NOT NULL,
    media_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    artifact JSONB,
    authorized_epoch BIGINT NOT NULL DEFAULT 0,
    price_snapshot JSONB,
    record_id TEXT NOT NULL UNIQUE,
    cost NUMERIC,
    finance_pending BOOLEAN NOT NULL,
    success BOOLEAN,
    transcript TEXT,
    result_kind TEXT,
    provider_status BIGINT,
    failure_code TEXT,
    history_projection TEXT NOT NULL DEFAULT 'pending',
    started_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(account_id,namespace,message_id,operation_version),
    CONSTRAINT ck_kf_context_voice_shape CHECK (
        namespace='sync' AND operation_version=1 AND length(payload_digest)=64
        AND jsonb_typeof(scope)='object' AND length(media_id)>0 AND authorized_epoch>=0
        AND phase IN ('media_ready','started','unknown','known')
        AND history_projection IN ('pending','projected','cleared','recalled')
        AND (artifact IS NULL OR jsonb_typeof(artifact)='object')
        AND (price_snapshot IS NULL OR jsonb_typeof(price_snapshot)='object')
        AND (cost IS NULL OR (cost>=0 AND cost<>'NaN'::numeric))
        AND ((phase='known' AND success IS NOT NULL AND transcript IS NOT NULL
              AND result_kind IN ('recognition','preflight','asr') AND observed_at IS NOT NULL
              AND cost IS NOT NULL AND NOT finance_pending)
          OR (phase IN ('media_ready','started','unknown') AND success IS NULL AND transcript IS NULL
              AND result_kind IS NULL AND observed_at IS NULL AND cost IS NULL))
        AND (phase<>'media_ready' OR (artifact IS NOT NULL AND NOT finance_pending))
        AND (phase NOT IN ('started','unknown') OR (authorized_epoch>0 AND price_snapshot IS NOT NULL
             AND artifact IS NOT NULL AND started_at IS NOT NULL AND lease_expires_at IS NOT NULL AND finance_pending))
        AND (result_kind IS DISTINCT FROM 'asr' OR (authorized_epoch>0 AND price_snapshot IS NOT NULL
             AND provider_status IS NOT NULL))
        AND (result_kind IS DISTINCT FROM 'recognition' OR (authorized_epoch=0 AND success AND length(transcript)>0))
        AND (success IS DISTINCT FROM TRUE OR length(transcript)>0)
    )
) ON COMMIT DROP;
table_name:='wecom_kf_context_voice_preparations'; reference_name:='pg_temp.context_voice_reference_preparations';
IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid=table_name::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_RELATION_INCOMPATIBLE: %',table_name; END IF;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid=table_name::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_COLUMNS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=table_name::regclass
  AND a.attname=spec.attname AND a.attnum>0 AND NOT a.attisdropped
  AND a.atttypid=spec.atttypid AND a.atttypmod=spec.atttypmod AND a.attnotnull=spec.attnotnull
  AND a.attidentity=spec.attidentity AND a.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_COLUMN_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a
  ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d
  WHERE d.adrelid=reference_name::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_DEFAULT_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_name::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_CONSTRAINTS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint actual WHERE actual.conrelid=table_name::regclass
   AND actual.conname=spec.conname AND actual.contype='c' AND actual.convalidated AND NOT actual.connoinherit
   AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)) THEN
   RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_CHECK_INCOMPATIBLE: %',table_name; END IF;
 ELSE
  IF NOT EXISTS (SELECT 1 FROM pg_constraint actual JOIN pg_index i ON i.indexrelid=actual.conindid
   WHERE actual.conrelid=table_name::regclass AND actual.contype=spec.contype
   AND (SELECT array_agg(a.attname ORDER BY k.position) FROM unnest(actual.conkey)
        WITH ORDINALITY AS k(attnum,position) JOIN pg_attribute a
        ON a.attrelid=actual.conrelid AND a.attnum=k.attnum AND NOT a.attisdropped)
     = (SELECT array_agg(a.attname ORDER BY k.position) FROM unnest(spec.conkey)
        WITH ORDINALITY AS k(attnum,position) JOIN pg_attribute a
        ON a.attrelid=spec.conrelid AND a.attnum=k.attnum AND NOT a.attisdropped)
   AND NOT actual.condeferrable AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL
   AND i.indnkeyatts=array_length(spec.conkey,1) AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'KF_CONTEXT_SCHEMA_INDEX_INCOMPATIBLE: %',table_name; END IF;
 END IF;
END LOOP;
DROP TABLE context_voice_reference_preparations;
END $$;

-- KF complete receipt batching and customer wire facts.
DO $$ DECLARE spec RECORD; table_name TEXT; reference_name TEXT; BEGIN
ALTER TABLE agent_runner_inputs ADD COLUMN IF NOT EXISTS source_control_id TEXT;
IF NOT EXISTS(SELECT 1 FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE a.attrelid='agent_runner_inputs'::regclass AND a.attname='source_control_id' AND NOT a.attisdropped
 AND a.atttypid='text'::regtype AND a.atttypmod=-1 AND NOT a.attnotnull AND d.adbin IS NULL) THEN
 RAISE EXCEPTION 'SOURCE_CONTROL_SCHEMA_COLUMN_INCOMPATIBLE'; END IF;
CREATE UNIQUE INDEX IF NOT EXISTS uq_runner_input_source_control ON agent_runner_inputs(source_control_id);
IF NOT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class idx ON idx.oid=i.indexrelid
 JOIN pg_am am ON am.oid=idx.relam WHERE i.indexrelid='uq_runner_input_source_control'::regclass
 AND i.indrelid='agent_runner_inputs'::regclass AND i.indisunique AND i.indisvalid AND i.indisready
 AND i.indpred IS NULL AND i.indexprs IS NULL AND am.amname='btree' AND i.indnkeyatts=1 AND i.indnatts=1
 AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(attnum,n)
 JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum ORDER BY k.n)=ARRAY['source_control_id']) THEN
 RAISE EXCEPTION 'SOURCE_CONTROL_SCHEMA_INDEX_INCOMPATIBLE'; END IF;
ALTER TABLE wecom_kf_context_task_intents DROP CONSTRAINT ck_kf_context_task_shape;
ALTER TABLE wecom_kf_context_task_intents ADD CONSTRAINT ck_kf_context_task_shape CHECK (
 namespace IN ('sync','callback') AND operation_version=1
 AND task_name IN ('lead_refresh','external_push_human','external_push')
 AND state IN ('pending_adapter','claimed','started','dispatch_returned','unknown','suppressed') AND jsonb_typeof(scope)='object');
CREATE TABLE IF NOT EXISTS wecom_kf_input_batches (
 batch_ref TEXT PRIMARY KEY,
 tenant_id TEXT NOT NULL, account_id TEXT NOT NULL, scope JSONB NOT NULL,
 opened_at TIMESTAMPTZ NOT NULL, deadline_at TIMESTAMPTZ NOT NULL,
 sealed_at TIMESTAMPTZ, phase TEXT NOT NULL, accepted_runner_id TEXT,
 CONSTRAINT ck_kf_batch_shape CHECK(jsonb_typeof(scope)='object'
  AND deadline_at>=opened_at AND phase IN ('collecting','sealed','accepted')
  AND (phase='collecting' OR sealed_at IS NOT NULL)
  AND ((phase='accepted')=(accepted_runner_id IS NOT NULL)))
);
IF to_regclass('pg_temp.kf_completion_reference_0') IS NOT NULL THEN RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE kf_completion_reference_0 (
 batch_ref TEXT PRIMARY KEY,
 tenant_id TEXT NOT NULL, account_id TEXT NOT NULL, scope JSONB NOT NULL,
 opened_at TIMESTAMPTZ NOT NULL, deadline_at TIMESTAMPTZ NOT NULL,
 sealed_at TIMESTAMPTZ, phase TEXT NOT NULL, accepted_runner_id TEXT,
 CONSTRAINT ck_kf_batch_shape CHECK(jsonb_typeof(scope)='object'
  AND deadline_at>=opened_at AND phase IN ('collecting','sealed','accepted')
  AND (phase='collecting' OR sealed_at IS NOT NULL)
  AND ((phase='accepted')=(accepted_runner_id IS NOT NULL)))
) ON COMMIT DROP;
table_name:='wecom_kf_input_batches'; reference_name:='pg_temp.kf_completion_reference_0';
IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid=table_name::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_RELATION_INCOMPATIBLE: %',table_name; END IF;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid=table_name::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMNS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname
  AND a.attnum>0 AND NOT a.attisdropped AND a.atttypid=spec.atttypid AND a.atttypmod=spec.atttypmod
  AND a.attnotnull=spec.attnotnull AND a.attidentity=spec.attidentity AND a.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMN_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a
  ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d WHERE d.adrelid=reference_name::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_DEFAULT_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_name::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CONSTRAINTS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a WHERE a.conrelid=table_name::regclass AND a.contype='c'
   AND a.conname=spec.conname AND a.convalidated AND NOT a.connoinherit
   AND pg_get_expr(a.conbin,a.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CHECK_INCOMPATIBLE: %',table_name; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a JOIN pg_index i ON i.indexrelid=a.conindid
   WHERE a.conrelid=table_name::regclass AND a.contype=spec.contype AND NOT a.condeferrable
   AND (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(a.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=a.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
    = (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(spec.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=spec.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
   AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL
   AND i.indnkeyatts=array_length(spec.conkey,1) AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_INDEX_INCOMPATIBLE: %',table_name; END IF;
 END IF;
END LOOP;
DROP TABLE kf_completion_reference_0;
CREATE TABLE IF NOT EXISTS wecom_kf_input_batch_members (
 batch_ref TEXT NOT NULL, ordinal INTEGER NOT NULL,
 account_id TEXT NOT NULL, namespace TEXT NOT NULL, message_id TEXT NOT NULL,
 payload_digest TEXT NOT NULL, receipt_seq BIGINT NOT NULL,
 PRIMARY KEY(batch_ref,ordinal), UNIQUE(account_id,namespace,message_id),
 CONSTRAINT ck_kf_batch_member_shape CHECK(ordinal>=0 AND ordinal<32
  AND namespace='sync' AND receipt_seq>0 AND length(payload_digest)=64)
);
IF to_regclass('pg_temp.kf_completion_reference_1') IS NOT NULL THEN RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE kf_completion_reference_1 (
 batch_ref TEXT NOT NULL, ordinal INTEGER NOT NULL,
 account_id TEXT NOT NULL, namespace TEXT NOT NULL, message_id TEXT NOT NULL,
 payload_digest TEXT NOT NULL, receipt_seq BIGINT NOT NULL,
 PRIMARY KEY(batch_ref,ordinal), UNIQUE(account_id,namespace,message_id),
 CONSTRAINT ck_kf_batch_member_shape CHECK(ordinal>=0 AND ordinal<32
  AND namespace='sync' AND receipt_seq>0 AND length(payload_digest)=64)
) ON COMMIT DROP;
table_name:='wecom_kf_input_batch_members'; reference_name:='pg_temp.kf_completion_reference_1';
IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid=table_name::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_RELATION_INCOMPATIBLE: %',table_name; END IF;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid=table_name::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMNS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname
  AND a.attnum>0 AND NOT a.attisdropped AND a.atttypid=spec.atttypid AND a.atttypmod=spec.atttypmod
  AND a.attnotnull=spec.attnotnull AND a.attidentity=spec.attidentity AND a.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMN_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a
  ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d WHERE d.adrelid=reference_name::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_DEFAULT_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_name::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CONSTRAINTS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a WHERE a.conrelid=table_name::regclass AND a.contype='c'
   AND a.conname=spec.conname AND a.convalidated AND NOT a.connoinherit
   AND pg_get_expr(a.conbin,a.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CHECK_INCOMPATIBLE: %',table_name; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a JOIN pg_index i ON i.indexrelid=a.conindid
   WHERE a.conrelid=table_name::regclass AND a.contype=spec.contype AND NOT a.condeferrable
   AND (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(a.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=a.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
    = (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(spec.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=spec.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
   AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL
   AND i.indnkeyatts=array_length(spec.conkey,1) AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_INDEX_INCOMPATIBLE: %',table_name; END IF;
 END IF;
END LOOP;
DROP TABLE kf_completion_reference_1;
CREATE TABLE IF NOT EXISTS agent_runner_input_batch_members (
 input_ref TEXT PRIMARY KEY,
 batch_ref TEXT NOT NULL, ordinal INTEGER NOT NULL, members_digest TEXT NOT NULL,
 history_group_ref TEXT NOT NULL, UNIQUE(batch_ref,ordinal),
 CONSTRAINT ck_runner_batch_member_shape CHECK(ordinal>=0 AND ordinal<32 AND length(members_digest)=64)
);
IF to_regclass('pg_temp.kf_completion_reference_2') IS NOT NULL THEN RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE kf_completion_reference_2 (
 input_ref TEXT PRIMARY KEY,
 batch_ref TEXT NOT NULL, ordinal INTEGER NOT NULL, members_digest TEXT NOT NULL,
 history_group_ref TEXT NOT NULL, UNIQUE(batch_ref,ordinal),
 CONSTRAINT ck_runner_batch_member_shape CHECK(ordinal>=0 AND ordinal<32 AND length(members_digest)=64)
) ON COMMIT DROP;
table_name:='agent_runner_input_batch_members'; reference_name:='pg_temp.kf_completion_reference_2';
IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid=table_name::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_RELATION_INCOMPATIBLE: %',table_name; END IF;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid=table_name::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMNS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname
  AND a.attnum>0 AND NOT a.attisdropped AND a.atttypid=spec.atttypid AND a.atttypmod=spec.atttypmod
  AND a.attnotnull=spec.attnotnull AND a.attidentity=spec.attidentity AND a.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMN_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a
  ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d WHERE d.adrelid=reference_name::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_DEFAULT_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_name::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CONSTRAINTS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a WHERE a.conrelid=table_name::regclass AND a.contype='c'
   AND a.conname=spec.conname AND a.convalidated AND NOT a.connoinherit
   AND pg_get_expr(a.conbin,a.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CHECK_INCOMPATIBLE: %',table_name; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a JOIN pg_index i ON i.indexrelid=a.conindid
   WHERE a.conrelid=table_name::regclass AND a.contype=spec.contype AND NOT a.condeferrable
   AND (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(a.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=a.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
    = (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(spec.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=spec.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
   AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL
   AND i.indnkeyatts=array_length(spec.conkey,1) AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_INDEX_INCOMPATIBLE: %',table_name; END IF;
 END IF;
END LOOP;
DROP TABLE kf_completion_reference_2;
CREATE TABLE IF NOT EXISTS wecom_kf_deliveries (
 delivery_id TEXT PRIMARY KEY, input_ref TEXT NOT NULL,
 runner_id TEXT NOT NULL, tenant_id TEXT NOT NULL, locator JSONB NOT NULL,
 scope JSONB NOT NULL, payload_digest TEXT NOT NULL,
 presentation_digest TEXT NOT NULL, presentation JSONB NOT NULL,
 view_revision BIGINT NOT NULL, control_revision BIGINT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'open', owner_id TEXT, claim_epoch BIGINT NOT NULL DEFAULT 0,
 lease_until TIMESTAMPTZ, sealed_at TIMESTAMPTZ, closed_at TIMESTAMPTZ,
 closed_outcome TEXT, policy_version INTEGER NOT NULL DEFAULT 1,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(input_ref,runner_id,presentation_digest),
 CONSTRAINT ck_kf_delivery_shape CHECK(jsonb_typeof(locator)='object' AND jsonb_typeof(scope)='object'
  AND jsonb_typeof(presentation)='object' AND length(payload_digest)=64 AND length(presentation_digest)=64
  AND view_revision>=0 AND control_revision>=0 AND claim_epoch>=0 AND policy_version=1
  AND phase IN ('open','sealed','closed') AND (phase='open' OR sealed_at IS NOT NULL)
  AND ((phase='closed')=(closed_at IS NOT NULL))
  AND ((phase='closed')=(closed_outcome IS NOT NULL))
  AND (closed_outcome IS NULL OR closed_outcome IN ('closed_accepted_known','closed_failed_known','closed_suppressed_known','closed_unknown')))
);
IF to_regclass('pg_temp.kf_completion_reference_3') IS NOT NULL THEN RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE kf_completion_reference_3 (
 delivery_id TEXT PRIMARY KEY, input_ref TEXT NOT NULL,
 runner_id TEXT NOT NULL, tenant_id TEXT NOT NULL, locator JSONB NOT NULL,
 scope JSONB NOT NULL, payload_digest TEXT NOT NULL,
 presentation_digest TEXT NOT NULL, presentation JSONB NOT NULL,
 view_revision BIGINT NOT NULL, control_revision BIGINT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'open', owner_id TEXT, claim_epoch BIGINT NOT NULL DEFAULT 0,
 lease_until TIMESTAMPTZ, sealed_at TIMESTAMPTZ, closed_at TIMESTAMPTZ,
 closed_outcome TEXT, policy_version INTEGER NOT NULL DEFAULT 1,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(input_ref,runner_id,presentation_digest),
 CONSTRAINT ck_kf_delivery_shape CHECK(jsonb_typeof(locator)='object' AND jsonb_typeof(scope)='object'
  AND jsonb_typeof(presentation)='object' AND length(payload_digest)=64 AND length(presentation_digest)=64
  AND view_revision>=0 AND control_revision>=0 AND claim_epoch>=0 AND policy_version=1
  AND phase IN ('open','sealed','closed') AND (phase='open' OR sealed_at IS NOT NULL)
  AND ((phase='closed')=(closed_at IS NOT NULL))
  AND ((phase='closed')=(closed_outcome IS NOT NULL))
  AND (closed_outcome IS NULL OR closed_outcome IN ('closed_accepted_known','closed_failed_known','closed_suppressed_known','closed_unknown')))
) ON COMMIT DROP;
IF to_regclass('pg_temp.kf_completion_old_delivery_reference') IS NOT NULL THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE kf_completion_old_delivery_reference
 (LIKE kf_completion_reference_3 INCLUDING ALL) ON COMMIT DROP;
ALTER TABLE kf_completion_old_delivery_reference DROP CONSTRAINT ck_kf_delivery_shape;
ALTER TABLE kf_completion_old_delivery_reference ADD CONSTRAINT ck_kf_delivery_shape CHECK(jsonb_typeof(locator)='object' AND jsonb_typeof(scope)='object'
  AND jsonb_typeof(presentation)='object' AND length(payload_digest)=64 AND length(presentation_digest)=64
  AND view_revision>=0 AND control_revision>=0 AND claim_epoch>=0 AND policy_version=1
  AND phase IN ('open','sealed','closed') AND (phase='open' OR sealed_at IS NOT NULL)
  AND ((phase='closed')=(closed_at IS NOT NULL))
  AND ((phase='closed')=(closed_outcome IS NOT NULL))
  AND (closed_outcome IS NULL OR closed_outcome IN ('closed_accepted_known','closed_failed_known','closed_suppressed_known')));
-- Accept only the exact previously validated shape before widening its outcome.
IF EXISTS(SELECT 1 FROM pg_constraint actual JOIN pg_constraint prior
 ON prior.conrelid='pg_temp.kf_completion_old_delivery_reference'::regclass
 AND prior.conname='ck_kf_delivery_shape'
 WHERE actual.conrelid='wecom_kf_deliveries'::regclass
 AND actual.conname='ck_kf_delivery_shape' AND actual.contype='c'
 AND actual.convalidated AND NOT actual.connoinherit
 AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(prior.conbin,prior.conrelid)) THEN
 ALTER TABLE wecom_kf_deliveries DROP CONSTRAINT ck_kf_delivery_shape;
 ALTER TABLE wecom_kf_deliveries ADD CONSTRAINT ck_kf_delivery_shape CHECK(jsonb_typeof(locator)='object' AND jsonb_typeof(scope)='object'
  AND jsonb_typeof(presentation)='object' AND length(payload_digest)=64 AND length(presentation_digest)=64
  AND view_revision>=0 AND control_revision>=0 AND claim_epoch>=0 AND policy_version=1
  AND phase IN ('open','sealed','closed') AND (phase='open' OR sealed_at IS NOT NULL)
  AND ((phase='closed')=(closed_at IS NOT NULL))
  AND ((phase='closed')=(closed_outcome IS NOT NULL))
  AND (closed_outcome IS NULL OR closed_outcome IN ('closed_accepted_known','closed_failed_known','closed_suppressed_known','closed_unknown')));
END IF;
DROP TABLE kf_completion_old_delivery_reference;
table_name:='wecom_kf_deliveries'; reference_name:='pg_temp.kf_completion_reference_3';
IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid=table_name::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_RELATION_INCOMPATIBLE: %',table_name; END IF;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid=table_name::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMNS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname
  AND a.attnum>0 AND NOT a.attisdropped AND a.atttypid=spec.atttypid AND a.atttypmod=spec.atttypmod
  AND a.attnotnull=spec.attnotnull AND a.attidentity=spec.attidentity AND a.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMN_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a
  ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d WHERE d.adrelid=reference_name::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_DEFAULT_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_name::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CONSTRAINTS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a WHERE a.conrelid=table_name::regclass AND a.contype='c'
   AND a.conname=spec.conname AND a.convalidated AND NOT a.connoinherit
   AND pg_get_expr(a.conbin,a.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CHECK_INCOMPATIBLE: %',table_name; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a JOIN pg_index i ON i.indexrelid=a.conindid
   WHERE a.conrelid=table_name::regclass AND a.contype=spec.contype AND NOT a.condeferrable
   AND (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(a.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=a.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
    = (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(spec.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=spec.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
   AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL
   AND i.indnkeyatts=array_length(spec.conkey,1) AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_INDEX_INCOMPATIBLE: %',table_name; END IF;
 END IF;
END LOOP;
DROP TABLE kf_completion_reference_3;
CREATE TABLE IF NOT EXISTS wecom_kf_wire_operations (
 operation_ref TEXT PRIMARY KEY, delivery_id TEXT,
 tenant_id TEXT NOT NULL, locator JSONB NOT NULL, scope JSONB NOT NULL,
 payload_digest TEXT NOT NULL, operation_version INTEGER NOT NULL DEFAULT 1,
 ordinal INTEGER NOT NULL, purpose TEXT NOT NULL, endpoint TEXT NOT NULL,
 request_digest TEXT NOT NULL, fallback_of TEXT,
 phase TEXT NOT NULL, authorized_epoch BIGINT NOT NULL,
 started_at TIMESTAMPTZ, observed_at TIMESTAMPTZ,
 response_origin TEXT, errcode BIGINT, result JSONB, proof JSONB,
 UNIQUE(delivery_id,ordinal),
 CONSTRAINT ck_kf_wire_shape CHECK(jsonb_typeof(locator)='object' AND jsonb_typeof(scope)='object'
  AND length(payload_digest)=64 AND length(request_digest)=64 AND ordinal>=0 AND ordinal<128
  AND operation_version=1 AND authorized_epoch>0
  AND phase IN ('started','ack','reject','unknown','suppressed','unwritten')
  AND (phase NOT IN ('started','ack','reject','unknown') OR started_at IS NOT NULL)
  AND (phase NOT IN ('ack','reject') OR (observed_at IS NOT NULL AND response_origin='platform' AND errcode IS NOT NULL))
  AND (phase<>'ack' OR errcode=0) AND (phase<>'reject' OR errcode<>0)
  AND (phase<>'suppressed' OR (observed_at IS NOT NULL AND jsonb_typeof(proof)='object'))
  AND (result IS NULL OR jsonb_typeof(result)='object') AND (proof IS NULL OR jsonb_typeof(proof)='object'))
);
IF to_regclass('pg_temp.kf_completion_reference_4') IS NOT NULL THEN RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE kf_completion_reference_4 (
 operation_ref TEXT PRIMARY KEY, delivery_id TEXT,
 tenant_id TEXT NOT NULL, locator JSONB NOT NULL, scope JSONB NOT NULL,
 payload_digest TEXT NOT NULL, operation_version INTEGER NOT NULL DEFAULT 1,
 ordinal INTEGER NOT NULL, purpose TEXT NOT NULL, endpoint TEXT NOT NULL,
 request_digest TEXT NOT NULL, fallback_of TEXT,
 phase TEXT NOT NULL, authorized_epoch BIGINT NOT NULL,
 started_at TIMESTAMPTZ, observed_at TIMESTAMPTZ,
 response_origin TEXT, errcode BIGINT, result JSONB, proof JSONB,
 UNIQUE(delivery_id,ordinal),
 CONSTRAINT ck_kf_wire_shape CHECK(jsonb_typeof(locator)='object' AND jsonb_typeof(scope)='object'
  AND length(payload_digest)=64 AND length(request_digest)=64 AND ordinal>=0 AND ordinal<128
  AND operation_version=1 AND authorized_epoch>0
  AND phase IN ('started','ack','reject','unknown','suppressed','unwritten')
  AND (phase NOT IN ('started','ack','reject','unknown') OR started_at IS NOT NULL)
  AND (phase NOT IN ('ack','reject') OR (observed_at IS NOT NULL AND response_origin='platform' AND errcode IS NOT NULL))
  AND (phase<>'ack' OR errcode=0) AND (phase<>'reject' OR errcode<>0)
  AND (phase<>'suppressed' OR (observed_at IS NOT NULL AND jsonb_typeof(proof)='object'))
  AND (result IS NULL OR jsonb_typeof(result)='object') AND (proof IS NULL OR jsonb_typeof(proof)='object'))
) ON COMMIT DROP;
table_name:='wecom_kf_wire_operations'; reference_name:='pg_temp.kf_completion_reference_4';
IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid=table_name::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_RELATION_INCOMPATIBLE: %',table_name; END IF;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid=table_name::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMNS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname
  AND a.attnum>0 AND NOT a.attisdropped AND a.atttypid=spec.atttypid AND a.atttypmod=spec.atttypmod
  AND a.attnotnull=spec.attnotnull AND a.attidentity=spec.attidentity AND a.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMN_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a
  ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d WHERE d.adrelid=reference_name::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_DEFAULT_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_name::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CONSTRAINTS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a WHERE a.conrelid=table_name::regclass AND a.contype='c'
   AND a.conname=spec.conname AND a.convalidated AND NOT a.connoinherit
   AND pg_get_expr(a.conbin,a.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CHECK_INCOMPATIBLE: %',table_name; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a JOIN pg_index i ON i.indexrelid=a.conindid
   WHERE a.conrelid=table_name::regclass AND a.contype=spec.contype AND NOT a.condeferrable
   AND (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(a.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=a.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
    = (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(spec.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=spec.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
   AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL
   AND i.indnkeyatts=array_length(spec.conkey,1) AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_INDEX_INCOMPATIBLE: %',table_name; END IF;
 END IF;
END LOOP;
DROP TABLE kf_completion_reference_4;
CREATE TABLE IF NOT EXISTS wecom_kf_business_facts (
 business_ref TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
 account_id TEXT NOT NULL, namespace TEXT NOT NULL, message_id TEXT NOT NULL,
 route_id TEXT, scope JSONB NOT NULL, payload_digest TEXT NOT NULL,
 business_kind TEXT NOT NULL, phase TEXT NOT NULL, value JSONB NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(account_id,namespace,message_id,business_kind),
 CONSTRAINT ck_kf_business_shape CHECK(jsonb_typeof(scope)='object' AND jsonb_typeof(value)='object'
  AND length(payload_digest)=64 AND phase IN ('known','unknown','suppressed'))
);
IF to_regclass('pg_temp.kf_completion_reference_5') IS NOT NULL THEN RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE kf_completion_reference_5 (
 business_ref TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
 account_id TEXT NOT NULL, namespace TEXT NOT NULL, message_id TEXT NOT NULL,
 route_id TEXT, scope JSONB NOT NULL, payload_digest TEXT NOT NULL,
 business_kind TEXT NOT NULL, phase TEXT NOT NULL, value JSONB NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(account_id,namespace,message_id,business_kind),
 CONSTRAINT ck_kf_business_shape CHECK(jsonb_typeof(scope)='object' AND jsonb_typeof(value)='object'
  AND length(payload_digest)=64 AND phase IN ('known','unknown','suppressed'))
) ON COMMIT DROP;
table_name:='wecom_kf_business_facts'; reference_name:='pg_temp.kf_completion_reference_5';
IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid=table_name::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_RELATION_INCOMPATIBLE: %',table_name; END IF;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid=table_name::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMNS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname
  AND a.attnum>0 AND NOT a.attisdropped AND a.atttypid=spec.atttypid AND a.atttypmod=spec.atttypmod
  AND a.attnotnull=spec.attnotnull AND a.attidentity=spec.attidentity AND a.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMN_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a
  ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d WHERE d.adrelid=reference_name::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_DEFAULT_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_name::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CONSTRAINTS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a WHERE a.conrelid=table_name::regclass AND a.contype='c'
   AND a.conname=spec.conname AND a.convalidated AND NOT a.connoinherit
   AND pg_get_expr(a.conbin,a.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CHECK_INCOMPATIBLE: %',table_name; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a JOIN pg_index i ON i.indexrelid=a.conindid
   WHERE a.conrelid=table_name::regclass AND a.contype=spec.contype AND NOT a.condeferrable
   AND (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(a.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=a.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
    = (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(spec.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=spec.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
   AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL
   AND i.indnkeyatts=array_length(spec.conkey,1) AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_INDEX_INCOMPATIBLE: %',table_name; END IF;
 END IF;
END LOOP;
DROP TABLE kf_completion_reference_5;
END $$;

-- KF ended unknown sends: retain wire facts and release only terminal chat ownership.
DO $$ DECLARE spec RECORD; table_name TEXT; reference_name TEXT; BEGIN
CREATE TABLE IF NOT EXISTS wecom_kf_deliveries (
 delivery_id TEXT PRIMARY KEY, input_ref TEXT NOT NULL,
 runner_id TEXT NOT NULL, tenant_id TEXT NOT NULL, locator JSONB NOT NULL,
 scope JSONB NOT NULL, payload_digest TEXT NOT NULL,
 presentation_digest TEXT NOT NULL, presentation JSONB NOT NULL,
 view_revision BIGINT NOT NULL, control_revision BIGINT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'open', owner_id TEXT, claim_epoch BIGINT NOT NULL DEFAULT 0,
 lease_until TIMESTAMPTZ, sealed_at TIMESTAMPTZ, closed_at TIMESTAMPTZ,
 closed_outcome TEXT, policy_version INTEGER NOT NULL DEFAULT 1,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(input_ref,runner_id,presentation_digest),
 CONSTRAINT ck_kf_delivery_shape CHECK(jsonb_typeof(locator)='object' AND jsonb_typeof(scope)='object'
  AND jsonb_typeof(presentation)='object' AND length(payload_digest)=64 AND length(presentation_digest)=64
  AND view_revision>=0 AND control_revision>=0 AND claim_epoch>=0 AND policy_version=1
  AND phase IN ('open','sealed','closed') AND (phase='open' OR sealed_at IS NOT NULL)
  AND ((phase='closed')=(closed_at IS NOT NULL))
  AND ((phase='closed')=(closed_outcome IS NOT NULL))
  AND (closed_outcome IS NULL OR closed_outcome IN ('closed_accepted_known','closed_failed_known','closed_suppressed_known','closed_unknown')))
);
IF to_regclass('pg_temp.kf_completion_reference_3') IS NOT NULL THEN RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE kf_completion_reference_3 (
 delivery_id TEXT PRIMARY KEY, input_ref TEXT NOT NULL,
 runner_id TEXT NOT NULL, tenant_id TEXT NOT NULL, locator JSONB NOT NULL,
 scope JSONB NOT NULL, payload_digest TEXT NOT NULL,
 presentation_digest TEXT NOT NULL, presentation JSONB NOT NULL,
 view_revision BIGINT NOT NULL, control_revision BIGINT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'open', owner_id TEXT, claim_epoch BIGINT NOT NULL DEFAULT 0,
 lease_until TIMESTAMPTZ, sealed_at TIMESTAMPTZ, closed_at TIMESTAMPTZ,
 closed_outcome TEXT, policy_version INTEGER NOT NULL DEFAULT 1,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(input_ref,runner_id,presentation_digest),
 CONSTRAINT ck_kf_delivery_shape CHECK(jsonb_typeof(locator)='object' AND jsonb_typeof(scope)='object'
  AND jsonb_typeof(presentation)='object' AND length(payload_digest)=64 AND length(presentation_digest)=64
  AND view_revision>=0 AND control_revision>=0 AND claim_epoch>=0 AND policy_version=1
  AND phase IN ('open','sealed','closed') AND (phase='open' OR sealed_at IS NOT NULL)
  AND ((phase='closed')=(closed_at IS NOT NULL))
  AND ((phase='closed')=(closed_outcome IS NOT NULL))
  AND (closed_outcome IS NULL OR closed_outcome IN ('closed_accepted_known','closed_failed_known','closed_suppressed_known','closed_unknown')))
) ON COMMIT DROP;
IF to_regclass('pg_temp.kf_completion_old_delivery_reference') IS NOT NULL THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_REFERENCE_COLLISION'; END IF;
CREATE TEMP TABLE kf_completion_old_delivery_reference
 (LIKE kf_completion_reference_3 INCLUDING ALL) ON COMMIT DROP;
ALTER TABLE kf_completion_old_delivery_reference DROP CONSTRAINT ck_kf_delivery_shape;
ALTER TABLE kf_completion_old_delivery_reference ADD CONSTRAINT ck_kf_delivery_shape CHECK(jsonb_typeof(locator)='object' AND jsonb_typeof(scope)='object'
  AND jsonb_typeof(presentation)='object' AND length(payload_digest)=64 AND length(presentation_digest)=64
  AND view_revision>=0 AND control_revision>=0 AND claim_epoch>=0 AND policy_version=1
  AND phase IN ('open','sealed','closed') AND (phase='open' OR sealed_at IS NOT NULL)
  AND ((phase='closed')=(closed_at IS NOT NULL))
  AND ((phase='closed')=(closed_outcome IS NOT NULL))
  AND (closed_outcome IS NULL OR closed_outcome IN ('closed_accepted_known','closed_failed_known','closed_suppressed_known')));
-- Accept only the exact previously validated shape before widening its outcome.
IF EXISTS(SELECT 1 FROM pg_constraint actual JOIN pg_constraint prior
 ON prior.conrelid='pg_temp.kf_completion_old_delivery_reference'::regclass
 AND prior.conname='ck_kf_delivery_shape'
 WHERE actual.conrelid='wecom_kf_deliveries'::regclass
 AND actual.conname='ck_kf_delivery_shape' AND actual.contype='c'
 AND actual.convalidated AND NOT actual.connoinherit
 AND pg_get_expr(actual.conbin,actual.conrelid)=pg_get_expr(prior.conbin,prior.conrelid)) THEN
 ALTER TABLE wecom_kf_deliveries DROP CONSTRAINT ck_kf_delivery_shape;
 ALTER TABLE wecom_kf_deliveries ADD CONSTRAINT ck_kf_delivery_shape CHECK(jsonb_typeof(locator)='object' AND jsonb_typeof(scope)='object'
  AND jsonb_typeof(presentation)='object' AND length(payload_digest)=64 AND length(presentation_digest)=64
  AND view_revision>=0 AND control_revision>=0 AND claim_epoch>=0 AND policy_version=1
  AND phase IN ('open','sealed','closed') AND (phase='open' OR sealed_at IS NOT NULL)
  AND ((phase='closed')=(closed_at IS NOT NULL))
  AND ((phase='closed')=(closed_outcome IS NOT NULL))
  AND (closed_outcome IS NULL OR closed_outcome IN ('closed_accepted_known','closed_failed_known','closed_suppressed_known','closed_unknown')));
END IF;
DROP TABLE kf_completion_old_delivery_reference;
table_name:='wecom_kf_deliveries'; reference_name:='pg_temp.kf_completion_reference_3';
IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid=table_name::regclass AND relkind='r') THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_RELATION_INCOMPATIBLE: %',table_name; END IF;
IF (SELECT count(*) FROM pg_attribute WHERE attrelid=table_name::regclass AND attnum>0 AND NOT attisdropped)
 <> (SELECT count(*) FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMNS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_attribute WHERE attrelid=reference_name::regclass AND attnum>0 AND NOT attisdropped LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname
  AND a.attnum>0 AND NOT a.attisdropped AND a.atttypid=spec.atttypid AND a.atttypmod=spec.atttypmod
  AND a.attnotnull=spec.attnotnull AND a.attidentity=spec.attidentity AND a.attgenerated=spec.attgenerated) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_COLUMN_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
 IF (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d JOIN pg_attribute a
  ON a.attrelid=d.adrelid AND a.attnum=d.adnum WHERE a.attrelid=table_name::regclass AND a.attname=spec.attname)
 IS DISTINCT FROM (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d WHERE d.adrelid=reference_name::regclass AND d.adnum=spec.attnum) THEN
  RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_DEFAULT_INCOMPATIBLE: %.%',table_name,spec.attname; END IF;
END LOOP;
IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_name::regclass AND contype IN ('c','p','u','f'))
 <> (SELECT count(*) FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u','f')) THEN
 RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CONSTRAINTS_INCOMPATIBLE: %',table_name; END IF;
FOR spec IN SELECT * FROM pg_constraint WHERE conrelid=reference_name::regclass AND contype IN ('c','p','u') LOOP
 IF spec.contype='c' THEN
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a WHERE a.conrelid=table_name::regclass AND a.contype='c'
   AND a.conname=spec.conname AND a.convalidated AND NOT a.connoinherit
   AND pg_get_expr(a.conbin,a.conrelid)=pg_get_expr(spec.conbin,spec.conrelid)) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_CHECK_INCOMPATIBLE: %',table_name; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM pg_constraint a JOIN pg_index i ON i.indexrelid=a.conindid
   WHERE a.conrelid=table_name::regclass AND a.contype=spec.contype AND NOT a.condeferrable
   AND (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(a.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=a.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
    = (SELECT array_agg(t.attname ORDER BY k.n) FROM unnest(spec.conkey) WITH ORDINALITY k(attnum,n)
    JOIN pg_attribute t ON t.attrelid=spec.conrelid AND t.attnum=k.attnum AND NOT t.attisdropped)
   AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL
   AND i.indnkeyatts=array_length(spec.conkey,1) AND i.indnatts=i.indnkeyatts) THEN
   RAISE EXCEPTION 'KF_COMPLETION_SCHEMA_INDEX_INCOMPATIBLE: %',table_name; END IF;
 END IF;
END LOOP;
DROP TABLE kf_completion_reference_3;
END $$;

-- KF 原渠道恢复（2026-10-08）：保留累积建表记录及旧 KF 表，不再由运行代码消费。
-- 与 db_update.yaml 的 2026-10-08 15:21:10 增量块对齐；新库最终同样无 delivery gate。
ALTER TABLE IF EXISTS agent_runner_session_claims DROP COLUMN IF EXISTS gate;
