-- 数据库加表、加字段等SQL语句，记录在本文件，以便升级部署
-- 所有SQL语句必须幂等安全（可重复执行），使用 IF NOT EXISTS、DROP TABLE IF EXISTS 等保护措施

-- 2026-08-05 视频生成 MVP：新增 gen_sessions / gen_cards 表
-- 详情见 docs/system/content-production/mvp-design.md §3
-- gen_sessions：一次抽卡会话（选场景+上传产品图+填文案+生成N条）
-- gen_cards：抽出的视频卡片（每条对应一次万相提交）
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
    status            TEXT NOT NULL DEFAULT 'generating',  -- generating/done/failed
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 幂等加列：已建表（无 model_image_fid）补列，新表上面 CREATE 已含
ALTER TABLE gen_sessions ADD COLUMN IF NOT EXISTS model_image_fid TEXT;

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

-- 2026-8-5，"社媒运营智能体"改名为"视频创作智能体"（agent_id 不变，仅改显示名）
UPDATE subagent_definitions
SET name = '视频创作智能体', updated_at = NOW()
WHERE agent_id = 'social-media-operations' AND name = '社媒运营智能体';

-- 2026-8-5，视频生成会话增加 AI角标开关与时长字段（mvp-design.md §6 新增参数）
ALTER TABLE gen_sessions ADD COLUMN IF NOT EXISTS enable_ai_label BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE gen_sessions ADD COLUMN IF NOT EXISTS duration_sec INT NOT NULL DEFAULT 5;

-- 2026-8-6，视频生成会话增加分辨率字段（720P/1080P，默认 720P；万相 r2v 不支持 480P）
ALTER TABLE gen_sessions ADD COLUMN IF NOT EXISTS resolution TEXT NOT NULL DEFAULT '720P';

-- 2026-8-6，修正：曾误用 480P 作为默认值，万相 r2v 不支持，回填历史数据为 720P
UPDATE gen_sessions SET resolution = '720P' WHERE resolution = '480P';
ALTER TABLE gen_sessions ALTER COLUMN resolution SET DEFAULT '720P';

-- 2026-8-6，视频生成会话增加视频画面比例字段（9:16 竖版 / 16:9 横版 / 1:1 / 4:3 / 3:4 / 21:9）
ALTER TABLE gen_sessions ADD COLUMN IF NOT EXISTS ratio TEXT NOT NULL DEFAULT '9:16';


-- 2026-8-6，协会信息收集客户端：激活码 / 客户端绑定 / 消耗日志 3 张表
-- 设计文档：docs/tools/association-client-design.md

CREATE TABLE IF NOT EXISTS client_activation_codes (
    id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,                       -- 激活码明文，格式 AC-XXXXXXXXXXXX（AC-前缀+12位去混淆字符）
    code_hash TEXT NOT NULL,                         -- bcrypt(code) 哈希
    tenant_id TEXT NOT NULL,                         -- 绑定的租户
    client_name TEXT,                                -- 客户端标识名（如"中国黄金协会-张三电脑"）
    status TEXT NOT NULL DEFAULT 'unused',           -- unused / used / disabled
    activated_at TIMESTAMP,                          -- 激活时间
    activated_machine TEXT,                          -- 激活机器标识（机器码）
    expires_at TIMESTAMP,                            -- 激活码本身的有效期（过期不可激活，可空=不过期）
    max_uses INTEGER DEFAULT 1,                      -- 最大可激活次数（默认1次）
    used_count INTEGER DEFAULT 0,                    -- 已激活次数
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_client_activation_codes_tenant
    ON client_activation_codes(tenant_id);

CREATE TABLE IF NOT EXISTS client_bindings (
    id SERIAL PRIMARY KEY,
    binding_id TEXT UNIQUE NOT NULL,                 -- 绑定ID，格式 cb_<32位hex>
    tenant_id TEXT NOT NULL,                         -- 绑定的租户
    activation_code_id INTEGER,                      -- 来源激活码（可空，支持非激活码创建）
    client_name TEXT,                                -- 客户端显示名
    machine_id TEXT,                                 -- 绑定的机器码
    access_token TEXT UNIQUE NOT NULL,               -- 长期访问令牌 secrets.token_urlsafe(48)
    status TEXT NOT NULL DEFAULT 'active',           -- active / disabled
    last_seen_at TIMESTAMP,                          -- 最后活跃时间
    expires_at TIMESTAMP,                            -- 绑定过期时间（默认null=不过期）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_client_bindings_tenant ON client_bindings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_client_bindings_token ON client_bindings(access_token);

CREATE TABLE IF NOT EXISTS client_usage_logs (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,                         -- 租户
    binding_id TEXT NOT NULL,                        -- 客户端绑定
    client_name TEXT,                                -- 客户端名快照
    session_id TEXT,                                 -- 客户端会话ID（一次协会收集任务）
    association_name TEXT,                           -- 协会名
    role TEXT,                                       -- 角色（会长/秘书长，微信RPA用）
    stage TEXT,                                      -- 流水线阶段（search_profile/official_profile/wechat_search_leader/wechat_mobile/judge/ocr）
    status TEXT,                                     -- 结果状态（success/failed/not_found/inconclusive/aborted）
    model TEXT,                                      -- 使用的模型
    provider TEXT,                                   -- 提供商
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    raw_credit_cost NUMERIC(12,2) DEFAULT 0,         -- 标准积分成本（未乘5）
    credit_cost NUMERIC(12,2) DEFAULT 0,             -- 实际扣除积分（raw_credit_cost × 5）
    error_code TEXT,                                 -- 错误码
    detail TEXT,                                     -- 脱敏详情（JSON字符串）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_client_usage_logs_tenant ON client_usage_logs(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_client_usage_logs_binding ON client_usage_logs(binding_id, created_at);
-- 2026-08-10，客户端遥测/日志上报后，运营后台按状态跨租户筛错误需要 status 索引
CREATE INDEX IF NOT EXISTS idx_client_usage_logs_status ON client_usage_logs(status, created_at);

-- ============================================================================
-- 2026-08-07 视频创作智能体（video-agent）Phase 1：素材库 + 提示词库 + subagent_definitions 扩展
-- 详见 docs/plans/plan-video-agent-phase1.md §1.1 / §1.2 / §1.5
-- ============================================================================

-- 素材库：跨会话的图片/视频素材（视频创作聊天自动入库 + 用户手动上传）
CREATE TABLE IF NOT EXISTS asset_library (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    file_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    source TEXT NOT NULL,
    scene TEXT,
    width INT,
    height INT,
    portrait_authorized BOOLEAN DEFAULT FALSE,
    portrait_auth_expire_at TIMESTAMP,
    portrait_auth_scope TEXT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_asset_library_tenant ON asset_library(tenant_id);
CREATE INDEX IF NOT EXISTS idx_asset_library_source ON asset_library(source);
CREATE INDEX IF NOT EXISTS idx_asset_library_tenant_scene ON asset_library(tenant_id, scene);

-- 提示词库：留用 / 黑名单 / 模版 三类合并一表，用 category 区分
CREATE TABLE IF NOT EXISTS prompt_library (
    id SERIAL PRIMARY KEY,
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
    promoted_from_kept_id INT,
    promoted_by_user_id TEXT,
    promoted_at TIMESTAMP,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prompt_library_tenant_category ON prompt_library(tenant_id, category);
CREATE INDEX IF NOT EXISTS idx_prompt_library_tenant_scene ON prompt_library(tenant_id, scene_tag);

-- subagent_definitions 扩展：声明式 UI 配置（chat_toolbar 额外按钮 + upload_accept 上传类型限定）
ALTER TABLE subagent_definitions ADD COLUMN IF NOT EXISTS chat_toolbar JSONB DEFAULT '[]'::jsonb;
ALTER TABLE subagent_definitions ADD COLUMN IF NOT EXISTS upload_accept TEXT;

-- ============================================================================
-- 2026-08-07 视频创作智能体 Phase 2：token_cost_prices 扩展 price_per_second 字段 + 视频模型记录
-- 详见 docs/plans/plan-video-agent-phase1.md §2.2
-- ============================================================================
ALTER TABLE token_cost_prices ADD COLUMN IF NOT EXISTS price_per_second NUMERIC(10,4);

INSERT INTO token_cost_prices (model_name, price_per_second)
VALUES ('wan2.7-r2v', 0.20)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, price_per_second)
VALUES ('MiniMax-H3', 0.30)
ON CONFLICT (model_name) DO NOTHING;

-- ============================================================================
-- 2026-08-07 视频创作智能体 Phase 1.4：chat_sessions 增加 metadata JSONB 列
-- 用于存储 video_gen_params（创作模式/时长/比例/分辨率/生成条数）等子智能体会话级元数据
-- ============================================================================
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS metadata JSONB;

-- ============================================================================
-- 2026-08-10 token_cost_prices 扩展按分辨率单价列 + 更正万相/MiniMax 单价为按分辨率真实价
-- 原占位价 wan2.7-r2v=0.20 / MiniMax-H3=0.30 与实际公开价不符，本次更正
-- 万相 r2v：720P=0.6, 1080P=1.0；MiniMax-H3 主生成：768P=0.5, 2K=0.8
-- price_per_second 保留最低分辨率单价值作 fallback（代码未传 resolution 时兜底）
-- ============================================================================
ALTER TABLE token_cost_prices ADD COLUMN IF NOT EXISTS price_per_second_by_resolution JSONB;

INSERT INTO token_cost_prices (model_name, price_per_second, price_per_second_by_resolution)
VALUES ('wan2.7-r2v', 0.6, '{"720P": 0.6, "1080P": 1.0}'::jsonb)
ON CONFLICT (model_name) DO UPDATE SET
  price_per_second = EXCLUDED.price_per_second,
  price_per_second_by_resolution = EXCLUDED.price_per_second_by_resolution;
INSERT INTO token_cost_prices (model_name, price_per_second, price_per_second_by_resolution)
VALUES ('MiniMax-H3', 0.5, '{"768P": 0.5, "2K": 0.8}'::jsonb)
ON CONFLICT (model_name) DO UPDATE SET
  price_per_second = EXCLUDED.price_per_second,
  price_per_second_by_resolution = EXCLUDED.price_per_second_by_resolution;

-- 2026-8-10，tenants 增加 logo_file_id 字段，存储租户 Logo 文件 ID
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS logo_file_id TEXT;

-- ============================================================================
-- 2026-08-10 本地工具基础设施（M0.3）：本地设备配对/调用/事件 四张系统表
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

-- ============================================================================
-- 2026-08-11 video-agent 切 qwen-vl-plus 多模态模型，补充计费单价
-- qwen-vl-plus 在 DashScope 与 qwen-plus 同价位：输入 0.8 / 输出 2.0 / 缓存 0.16 元/百万 tokens
-- 百炼平台模型价格 https://bailian.console.aliyun.com/cn-beijing/?msctype=email&mscareaid=cn&mscsiteid=cn&mscmsgid=5380126031901110341&yunge_info=email___5380126031901110341&spm=a2c4k.32345051.zh-cnc.9&msctype=pmsg&mscareaid=cn&mscsiteid=cn&mscmsgid=4960126031300407999&yunge_info=pmsg___4960126031300407999&tab=doc#/doc/?type=model&url=2987148
-- 百炼平台缓存命中后价格为 20%，具体参考 https://bailian.console.aliyun.com/cn-beijing/?msctype=email&mscareaid=cn&mscsiteid=cn&mscmsgid=5380126031901110341&yunge_info=email___5380126031901110341&spm=a2c4k.32345051.zh-cnc.9&msctype=pmsg&mscareaid=cn&mscsiteid=cn&mscmsgid=4960126031300407999&yunge_info=pmsg___4960126031300407999&tab=doc#/doc/?type=model&url=2862577
-- ============================================================================
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('qwen3.7-plus', 2.0, 8.0, 0.4)
ON CONFLICT (model_name) DO UPDATE SET
  input_price_per_m = EXCLUDED.input_price_per_m,
  output_price_per_m = EXCLUDED.output_price_per_m,
  cached_input_price_per_m = EXCLUDED.cached_input_price_per_m;

INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('qwen-vl-max', 1.6, 4.0, 0.32)
ON CONFLICT (model_name) DO UPDATE SET
  input_price_per_m = EXCLUDED.input_price_per_m,
  output_price_per_m = EXCLUDED.output_price_per_m,
  cached_input_price_per_m = EXCLUDED.cached_input_price_per_m;

INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('qwen-vl-plus', 0.8, 2.0, 0.16)
ON CONFLICT (model_name) DO UPDATE SET
  input_price_per_m = EXCLUDED.input_price_per_m,
  output_price_per_m = EXCLUDED.output_price_per_m,
  cached_input_price_per_m = EXCLUDED.cached_input_price_per_m;

INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m, cached_input_price_per_m)
VALUES ('qwen3-vl-flash', 0.6, 6, 0.012)    -- 按顶格 128K<Token≤256K 计算
ON CONFLICT (model_name) DO UPDATE SET
  input_price_per_m = EXCLUDED.input_price_per_m,
  output_price_per_m = EXCLUDED.output_price_per_m,
  cached_input_price_per_m = EXCLUDED.cached_input_price_per_m;

-- ============================================================================
-- 2026-08-12 LLM 计费接入改造：chat_records 增加 embedding/ASR 计费维度
-- 详见 docs/plans/plan-llm-billing-integration.md
-- ============================================================================
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS embedding_tokens INTEGER DEFAULT 0;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS asr_calls INTEGER DEFAULT 0;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS usage_breakdown JSONB;

-- ============================================================================
-- 2026-08-12 LLM 计费接入改造：token_cost_prices 增加 embedding/ASR 单价列
-- embedding_price_per_m: 向量模型单价（元/百万 token），用于 text-embedding-v3 等
-- asr_price_per_call: 语音识别单价（元/次），用于阿里云 NLS 一句话识别
-- ============================================================================
ALTER TABLE token_cost_prices ADD COLUMN IF NOT EXISTS embedding_price_per_m NUMERIC(10,4);
ALTER TABLE token_cost_prices ADD COLUMN IF NOT EXISTS asr_price_per_call NUMERIC(10,4);

-- text-embedding-v3 单价：0.5 元/百万 tokens（阿里云百炼官方定价）
INSERT INTO token_cost_prices (model_name, embedding_price_per_m)
VALUES ('text-embedding-v3', 0.5)
ON CONFLICT (model_name) DO UPDATE SET
  embedding_price_per_m = EXCLUDED.embedding_price_per_m;

-- 阿里云 NLS 一句话识别单价：0.01 元/次（1次最多60s）
INSERT INTO token_cost_prices (model_name, asr_price_per_call)
VALUES ('aliyun-nls-asr', 0.01)
ON CONFLICT (model_name) DO UPDATE SET
  asr_price_per_call = EXCLUDED.asr_price_per_call;
