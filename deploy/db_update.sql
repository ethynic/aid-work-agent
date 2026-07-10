-- 数据库加表、加字段等SQL语句，记录在本文件，以便升级部署
-- 所有SQL语句必须幂等安全（可重复执行），使用 IF NOT EXISTS、DROP TABLE IF EXISTS 等保护措施

-- ============================================================================
-- 2026-06-30 修复 metadata 字段类型：TEXT 转换为 JSONB
-- 解决撤回消息功能中 metadata->>'msgid' 操作符不存在的错误
-- ============================================================================

-- channel_messages.metadata: TEXT → JSONB
DO $$
BEGIN
    PERFORM 1 FROM information_schema.columns
    WHERE table_name = 'channel_messages' AND column_name = 'metadata' AND data_type <> 'jsonb';
    IF FOUND THEN
        ALTER TABLE channel_messages ALTER COLUMN metadata TYPE JSONB USING metadata::JSONB;
    END IF;
END $$;

-- channel_sessions.metadata: TEXT → JSONB
DO $$
BEGIN
    PERFORM 1 FROM information_schema.columns
    WHERE table_name = 'channel_sessions' AND column_name = 'metadata' AND data_type <> 'jsonb';
    IF FOUND THEN
        ALTER TABLE channel_sessions ALTER COLUMN metadata TYPE JSONB USING metadata::JSONB;
    END IF;
END $$;

-- ============================================================================
-- 2026-06-30 撤回消息功能：channel_messages 增加 is_recalled 和 recalled_at 字段
-- ============================================================================
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS is_recalled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS recalled_at TIMESTAMP;

-- ============================================================================
-- 2026-06-01 回复风格管理系统：新增 reply_styles 表 + agent_instances 增加 reply_style_id 字段
-- ============================================================================

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

ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS reply_style_id TEXT;

-- 种子数据：系统内置拟人风格
INSERT INTO reply_styles (style_id, tenant_id, name, description, content, version, is_active)
SELECT 'human-like', 'system', '拟人风格', '像真人同事一样对话，隐藏 AI 工作过程',
$$## 回复风格指南

你是一个真实的同事，不是 AI 助手。请在回复时遵循以下规则：

### 身份与语气
- 你是一个真实的人，用自然、亲切的语气交流，就像和同事面对面聊天
- 使用"我"来称呼自己，用"你"称呼用户
- 适当使用口语化表达（如"嗯"、"好的"、"没问题"），但不要过度
- 回复简洁，像真人对话一样，不要写长篇大论

### 信息呈现
- 直接给出结论或答案，不要解释你是怎么知道的
- 不要提及你使用了什么工具、技能或知识库
- 不要说"根据搜索结果"、"我查看了数据库"、"信息查到了"这类话
- 禁止说"在知识库中"、"知识库里提到"、"知识库中有"之类的话，你就是在回答问题，不要透露信息来源
- 如果需要展示数据，直接整理成用户关心的形式呈现
- 不用重复之前的话，除非是重要信息提示用户

### 禁止事项
- 禁止暴露内部工作过程（工具调用、技能加载、数据库查询等）
- 禁止使用"作为 AI 助手"、"我可以帮你"这类暴露 AI 身份的表达
- 禁止在回复中列出执行步骤或工具链路
- 禁止使用编号列表来组织回复内容（除非用户明确要求）$$, 1, TRUE
WHERE NOT EXISTS (
    SELECT 1 FROM reply_styles WHERE style_id = 'human-like' AND tenant_id = 'system'
);

ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS reply_style_id TEXT;

-- 2026-06-02，users 表增加 source 字段，区分用户来源（NULL=内部用户，wecom_kf=企业微信客服）
ALTER TABLE users ADD COLUMN IF NOT EXISTS source TEXT;

-- 2026-06-02，Prompt Version Management System - Phase 1

-- 1. prompt_registry — Prompt 注册表
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

-- 2. prompt_versions — Prompt 版本（不可变）
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

-- 3. prompt_labels — 标签（命名指针）
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

-- 4. prompt_drafts — 草稿（每 Prompt 最多一条）
CREATE TABLE IF NOT EXISTS prompt_drafts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id     UUID NOT NULL UNIQUE,
    content       TEXT NOT NULL,
    variables     JSONB,
    base_version  INTEGER,
    updated_by    TEXT,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2026-06-03，Phase 2 — 子智能体定义表（system_prompt 由 prompt_versions 管理）

-- 5. subagent_definitions — 子智能体元数据定义
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
-- 2026-6-4, Phase 3.1 -- 移除 capabilities 字段（不再使用能力标签匹配）
ALTER TABLE subagent_definitions DROP COLUMN IF EXISTS capabilities;
-- 2026-6-5, Phase 3.2 -- 知识库关联配置（source_type + display_name）
ALTER TABLE subagent_definitions ADD COLUMN IF NOT EXISTS knowledge_sources JSONB DEFAULT '[]';

-- 2026-6-5, Phase 3.2 -- 租户级子智能体知识库关联表（per-tenant per-agent）
CREATE TABLE IF NOT EXISTS subagent_knowledge_sources (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    subagent_name TEXT NOT NULL,
    sources JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, subagent_name)
);

-- 2026-6-3，数据连接器表，用于数据分析智能体的数据库连接管理
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

-- 2026-6-4，知识库分类表（Phase 0：分类管理）
CREATE TABLE IF NOT EXISTS knowledge_categories (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    display_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, source_type)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_categories_tenant ON knowledge_categories(tenant_id);

-- 为已有租户自动注册已有 source_type 分类（幂等）
INSERT INTO knowledge_categories (tenant_id, source_type, display_name)
SELECT DISTINCT d.tenant_id, d.source_type, d.source_type
FROM documents d
WHERE d.tenant_id IS NOT NULL
  AND d.source_type IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_categories kc
    WHERE kc.tenant_id = d.tenant_id AND kc.source_type = d.source_type
  );

-- ============================================================================
-- 2026-06-05，业务数据表统一增加 user_id 字段 + 规范化 created_at 字段
-- ============================================================================

-- 1. bs_customer_followup_assign_rules 增加 user_id 字段
ALTER TABLE bs_customer_followup_assign_rules ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 2. bs_customer_followup_conversion_funnel 增加 user_id 字段，changed_at 重命名为 created_at
ALTER TABLE bs_customer_followup_conversion_funnel ADD COLUMN IF NOT EXISTS user_id TEXT;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bs_customer_followup_conversion_funnel' AND column_name = 'changed_at'
    ) THEN
        ALTER TABLE bs_customer_followup_conversion_funnel RENAME COLUMN changed_at TO created_at;
    END IF;
END $$;
-- 重建依赖 changed_at 的索引
DROP INDEX IF EXISTS idx_cf_funnel_date;
CREATE INDEX IF NOT EXISTS idx_cf_funnel_date ON bs_customer_followup_conversion_funnel(tenant_id, created_at);

-- 3. bs_order_processing_order_items 增加 user_id 字段
ALTER TABLE bs_order_processing_order_items ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 4. bs_order_processing_status_history 增加 user_id 字段
ALTER TABLE bs_order_processing_status_history ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 5. bs_order_processing_approvals 增加 user_id 字段
ALTER TABLE bs_order_processing_approvals ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 6. bs_order_processing_webhook_events 增加 user_id 字段，received_at 重命名为 created_at
ALTER TABLE bs_order_processing_webhook_events ADD COLUMN IF NOT EXISTS user_id TEXT;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bs_order_processing_webhook_events' AND column_name = 'received_at'
    ) THEN
        ALTER TABLE bs_order_processing_webhook_events RENAME COLUMN received_at TO created_at;
    END IF;
END $$;
-- 重建依赖 received_at 的索引
DROP INDEX IF EXISTS idx_webhook_processed_received;
CREATE INDEX IF NOT EXISTS idx_webhook_processed_received ON bs_order_processing_webhook_events(processed, created_at);

-- 7. bs_order_processing_products 增加 user_id 字段
ALTER TABLE bs_order_processing_products ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 8. bs_order_processing_inventory 增加 user_id 字段
ALTER TABLE bs_order_processing_inventory ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 9. bs_order_processing_inventory_reservations 增加 user_id 字段
ALTER TABLE bs_order_processing_inventory_reservations ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 10. bs_order_processing_shipments 增加 user_id 字段
ALTER TABLE bs_order_processing_shipments ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 11. bs_travel_quote_vehicles 增加 user_id 字段
ALTER TABLE bs_travel_quote_vehicles ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 12. bs_travel_quote_meals 增加 user_id 字段
ALTER TABLE bs_travel_quote_meals ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 13. bs_travel_quote_guides 增加 user_id 字段
ALTER TABLE bs_travel_quote_guides ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 14. bs_travel_quote_fees 增加 user_id 字段
ALTER TABLE bs_travel_quote_fees ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 15. bs_travel_quote_seasons 增加 user_id 字段
ALTER TABLE bs_travel_quote_seasons ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 16. bs_complaint_handling_interactions 增加 user_id 字段
ALTER TABLE bs_complaint_handling_interactions ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 17. bs_complaint_handling_case_solutions 增加 user_id 字段
ALTER TABLE bs_complaint_handling_case_solutions ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 18. bs_complaint_handling_followups 增加 user_id 字段
ALTER TABLE bs_complaint_handling_followups ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 19. bs_after_sales_ticket_messages 增加 user_id 字段
ALTER TABLE bs_after_sales_ticket_messages ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 2026-06-06，调整 SessionDB.delete() 行为：保留 chat_records 不删除，用于计费/审计聚合（无需 SQL 变更）

-- 2026-6-5, Phase 3.7 — System Prompt 分段管理
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

-- 2026-6-8，数据迁移：为知识库表和业务表添加 uuid 列，支持跨库迁移
ALTER TABLE knowledge_categories ADD COLUMN IF NOT EXISTS uuid TEXT;
UPDATE knowledge_categories SET uuid = 'kc_' || substring(md5(random()::text || id::text), 1, 12) WHERE uuid IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_categories_uuid ON knowledge_categories(uuid);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS uuid TEXT;
UPDATE documents SET uuid = 'doc_' || substring(md5(random()::text || id::text), 1, 12) WHERE uuid IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_uuid ON documents(uuid);

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS uuid TEXT;
UPDATE chunks SET uuid = 'chunk_' || substring(md5(random()::text || id::text), 1, 12) WHERE uuid IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_uuid ON chunks(uuid);

ALTER TABLE bs_travel_quote_vehicles ADD COLUMN IF NOT EXISTS uuid TEXT;
UPDATE bs_travel_quote_vehicles SET uuid = 'tqv_' || substring(md5(random()::text || id::text), 1, 12) WHERE uuid IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_travel_vehicles_uuid ON bs_travel_quote_vehicles(uuid);

ALTER TABLE bs_travel_quote_meals ADD COLUMN IF NOT EXISTS uuid TEXT;
UPDATE bs_travel_quote_meals SET uuid = 'tqm_' || substring(md5(random()::text || id::text), 1, 12) WHERE uuid IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_travel_meals_uuid ON bs_travel_quote_meals(uuid);

ALTER TABLE bs_travel_quote_guides ADD COLUMN IF NOT EXISTS uuid TEXT;
UPDATE bs_travel_quote_guides SET uuid = 'tqg_' || substring(md5(random()::text || id::text), 1, 12) WHERE uuid IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_travel_guides_uuid ON bs_travel_quote_guides(uuid);

ALTER TABLE bs_travel_quote_fees ADD COLUMN IF NOT EXISTS uuid TEXT;
UPDATE bs_travel_quote_fees SET uuid = 'tqf_' || substring(md5(random()::text || id::text), 1, 12) WHERE uuid IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_travel_fees_uuid ON bs_travel_quote_fees(uuid);

ALTER TABLE bs_travel_quote_seasons ADD COLUMN IF NOT EXISTS uuid TEXT;
UPDATE bs_travel_quote_seasons SET uuid = 'tqs_' || substring(md5(random()::text || id::text), 1, 12) WHERE uuid IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_travel_seasons_uuid ON bs_travel_quote_seasons(uuid);

-- ============================================================
-- 2026-6-10，为上述 8 张表加 BEFORE INSERT 触发器，新数据自动生成 UUID
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

-- ============================================================================
-- 2026-06-19，channel_messages / channel_sessions 增加数字型自增 id + created_at 修正为 TIMESTAMP
-- 解决同一秒内多条消息无法区分先后顺序的问题
-- ============================================================================

-- channel_messages：加自增 id，原 message_id 保留唯一约束
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS id SERIAL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_messages_msgid ON channel_messages(message_id);
-- 如果主键还在 message_id 上（非 id），则交换到 id
DO $$
DECLARE
    pk_on_id boolean;
BEGIN
    SELECT EXISTS(
        SELECT 1 FROM information_schema.key_column_usage kcu
        JOIN information_schema.table_constraints tc USING (constraint_name, table_name)
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_name = 'channel_messages'
          AND kcu.column_name = 'id'
    ) INTO pk_on_id;
    IF NOT pk_on_id THEN
        ALTER TABLE channel_messages DROP CONSTRAINT IF EXISTS channel_messages_pkey;
        ALTER TABLE channel_messages ADD PRIMARY KEY (id);
    END IF;
END $$;

-- channel_sessions：加自增 id，原 session_id 保留唯一约束
ALTER TABLE channel_sessions ADD COLUMN IF NOT EXISTS id SERIAL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_sessions_sid ON channel_sessions(session_id);
DO $$
DECLARE
    pk_on_id boolean;
BEGIN
    SELECT EXISTS(
        SELECT 1 FROM information_schema.key_column_usage kcu
        JOIN information_schema.table_constraints tc USING (constraint_name, table_name)
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_name = 'channel_sessions'
          AND kcu.column_name = 'id'
    ) INTO pk_on_id;
    IF NOT pk_on_id THEN
        ALTER TABLE channel_sessions DROP CONSTRAINT IF EXISTS channel_sessions_pkey;
        ALTER TABLE channel_sessions ADD PRIMARY KEY (id);
    END IF;
END $$;

-- created_at 类型修正：TEXT → TIMESTAMP，添加数据库默认值
-- 幂等：只在当前类型为 text 时转换（PostgreSQL 会隐式将 text 时间串转 timestamp）
DO $$
BEGIN
    PERFORM 1 FROM information_schema.columns
    WHERE table_name = 'channel_messages' AND column_name = 'created_at' AND data_type = 'text';
    IF FOUND THEN
        ALTER TABLE channel_messages ALTER COLUMN created_at TYPE TIMESTAMP USING created_at::TIMESTAMP;
        ALTER TABLE channel_messages ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP;
        ALTER TABLE channel_messages ALTER COLUMN created_at DROP NOT NULL;
    END IF;
END $$;

DO $$
BEGIN
    PERFORM 1 FROM information_schema.columns
    WHERE table_name = 'channel_sessions' AND column_name = 'created_at' AND data_type = 'text';
    IF FOUND THEN
        ALTER TABLE channel_sessions ALTER COLUMN created_at TYPE TIMESTAMP USING created_at::TIMESTAMP;
        ALTER TABLE channel_sessions ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP;
        ALTER TABLE channel_sessions ALTER COLUMN created_at DROP NOT NULL;
    END IF;
END $$;

-- updated_at 类型修正：TEXT → TIMESTAMP（仅 channel_sessions）
DO $$
BEGIN
    PERFORM 1 FROM information_schema.columns
    WHERE table_name = 'channel_sessions' AND column_name = 'updated_at' AND data_type = 'text';
    IF FOUND THEN
        ALTER TABLE channel_sessions ALTER COLUMN updated_at TYPE TIMESTAMP USING updated_at::TIMESTAMP;
        ALTER TABLE channel_sessions ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;
        ALTER TABLE channel_sessions ALTER COLUMN updated_at DROP NOT NULL;
    END IF;
END $$;

-- last_message_at 类型修正：TEXT → TIMESTAMP（仅 channel_sessions，无默认值）
DO $$
BEGIN
    PERFORM 1 FROM information_schema.columns
    WHERE table_name = 'channel_sessions' AND column_name = 'last_message_at' AND data_type = 'text';
    IF FOUND THEN
        ALTER TABLE channel_sessions ALTER COLUMN last_message_at TYPE TIMESTAMP USING last_message_at::TIMESTAMP;
    END IF;
END $$;


-- ============================================================================
-- 2026-06-22 企业微信个人账号 RPA 渠道：新增 5 张服务端表
-- 客户端注册 / 账号 / 会话绑定 / 出站动作队列 / 审计日志
-- 规范对齐 .claude/rules/database_dev.md：TEXT 存枚举/状态、必带 tenant_id/user_id/created_at、
-- 仅主键/唯一键 NOT NULL、无外键、无触发器、幂等。
-- 表结构必须与 src/channels/wecom_personal_rpa/db.py 保持一致。
-- ============================================================================

-- 1. 客户端注册表
CREATE TABLE IF NOT EXISTS wecom_rpa_clients (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    name TEXT,
    encrypted_secret TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    min_version TEXT,
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, search_key)
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_bindings_tenant_account
    ON wecom_rpa_conversation_bindings(tenant_id, account_id);

-- 4. 出站动作队列（离线客户端拉取执行）
CREATE TABLE IF NOT EXISTS wecom_rpa_action_outbox (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    account_id TEXT NOT NULL,
    conversation_id TEXT,
    request_id TEXT NOT NULL,
    session_id TEXT,
    actions TEXT,
    reply_context JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT,
    dedup_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_outbox_status_retry
    ON wecom_rpa_action_outbox(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_wecom_rpa_outbox_tenant_account
    ON wecom_rpa_action_outbox(tenant_id, account_id);
ALTER TABLE wecom_rpa_action_outbox
    ADD COLUMN IF NOT EXISTS reply_context JSONB;

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

-- 2026-6-24, 会话内上下文压缩：新增 chat_context_summaries 表 + chat_messages/channel_messages 加 compacted 标记
CREATE TABLE IF NOT EXISTS chat_context_summaries (
    id SERIAL PRIMARY KEY,
    summary_id TEXT UNIQUE NOT NULL,
    session_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    tenant_id TEXT,
    user_id TEXT,
    subagent_id TEXT,
    summary_text TEXT NOT NULL,
    summary_version INTEGER NOT NULL DEFAULT 1,
    compressed_message_ids BIGINT[] NOT NULL,
    compressed_message_count INTEGER NOT NULL,
    original_token_count INTEGER NOT NULL,
    compressed_token_count INTEGER NOT NULL,
    compression_ratio REAL NOT NULL,
    llm_provider TEXT,
    llm_model TEXT,
    llm_tokens_used INTEGER,
    fallback_used BOOLEAN DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    superseded_at TIMESTAMP
);
-- 部分唯一索引：保证同一 (session_id, source_type) 同时只有一条 active summary
-- 修复 P0-1：原非唯一索引无法阻止并发竞态产生多条 active
CREATE UNIQUE INDEX IF NOT EXISTS idx_ccs_session_active
    ON chat_context_summaries (session_id, source_type)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_ccs_session_list
    ON chat_context_summaries (session_id, source_type, created_at DESC);
-- 2026-6-25, v3.2.1 P1-1：管理后台 list_summaries 按 tenant_id + created_at DESC 查询，
-- 需要此索引避免全表扫描（租户量大时显著加速）
CREATE INDEX IF NOT EXISTS idx_ccs_tenant_time
    ON chat_context_summaries (tenant_id, created_at DESC);

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS compacted BOOLEAN DEFAULT FALSE;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS compacted_by TEXT;
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS compacted BOOLEAN DEFAULT FALSE;
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS compacted_by TEXT;

-- 2026-6-25, v3.1: session 级上下文 token 缓存（Agent 主循环每次 LLM 调用后写入最后一次 prompt+completion tokens）
-- 压缩服务 _should_compress 优先读此字段，避免每次全量 count_tokens
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS context_token_count INTEGER DEFAULT 0;
ALTER TABLE channel_sessions ADD COLUMN IF NOT EXISTS context_token_count INTEGER DEFAULT 0;

-- ============================================================================
-- 2026-06-24，企业微信个人账号 RPA 平台后台绑定管理：wecom_rpa_clients 增加 agent_base_url 字段
-- 用途：客户端回填的服务端生产地址，供运维排查"客户端连不上服务端"类问题时快速定位。
-- 字段非必填（保留向后兼容），前端 UI 使用占位地址 https://agent.example.com。
-- 规范对齐 database_dev.md：TEXT 类型、可空、无触发器。
-- ============================================================================
ALTER TABLE wecom_rpa_clients ADD COLUMN IF NOT EXISTS agent_base_url TEXT;

-- ============================================================================
-- 2026-06-25，v3.2.1 P2-1：为 chat_messages 增加 (session_id, created_at) 复合索引
-- 用途：MessageDB.count_messages_by_session（压缩阈值快路径检查）走该索引，
-- COUNT(*) 性能从 O(n) 顺序扫描降到 O(log n) 索引扫描。
-- channel_messages 表已有同名索引（init-postgres.sql:154），无需新增。
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
    ON chat_messages (session_id, created_at DESC);


-- ============================================================================
-- 2026-6-26，企业微信个人账号 RPA 会话存档：wecom_rpa_conversation_bindings 增加监控白名单字段
-- 用途：绑定级监控白名单，发送方不在白名单内的消息由服务端二次过滤（不投递 agent）。
-- 客户端缓存白名单只是优化（减少 callback），真正的过滤必须服务端做。
-- monitor_user_names：发送人显示名数组（任一匹配即上报）；空数组 = 不按名字过滤
-- monitor_user_ids：发送人稳定 ID 数组（external_userid/userid/room_id）；空数组 = 不按 ID 过滤
-- 两个字段任一非空即按白名单过滤；都为空 = 监控所有（首版默认）。
-- 规范对齐 database_dev.md：TEXT[] 类型、DEFAULT '{}' 数组字面量、ADD COLUMN IF NOT EXISTS 幂等。
-- ============================================================================
ALTER TABLE wecom_rpa_conversation_bindings
    ADD COLUMN IF NOT EXISTS monitor_user_names TEXT[] DEFAULT '{}';
ALTER TABLE wecom_rpa_conversation_bindings
    ADD COLUMN IF NOT EXISTS monitor_user_ids TEXT[] DEFAULT '{}';

-- ============================================================================
-- 2026-06-30，社媒内容运营智能体与聚合平台核心表
-- 用途：平台账号、内容计划、母版、平台版本、审核、发布任务和数据指标。
-- 规范：所有业务表包含 tenant_id；凭证密文存储；发布任务使用唯一幂等键。
-- ============================================================================
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
CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_due
    ON social_publish_jobs(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_tenant_account
    ON social_publish_jobs(tenant_id, account_id, created_at);
CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_external_task
    ON social_publish_jobs(external_task_id);
CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_lease
    ON social_publish_jobs(lease_expires_at);

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

-- 2026-07-02 wecom_personal_rpa 渠道配置：会话存档服务端拉取模式
-- 1) 同租户单例约束：同 tenant 只能有一份 wecom_personal_rpa 类型配置
CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_channel_configs_wecom_personal_rpa
    ON tenant_channel_configs(tenant_id, channel_type)
    WHERE channel_type = 'wecom_personal_rpa';

-- 2) 客户端表增加 listen_mode 字段（NULL 或 client；服务端拉取模式时客户端拉取禁用）
--    服务端拉取模式下客户端拉 listen_mode 永远为 server 或 NULL，客户端不启动本地 ChatArchiveListener
ALTER TABLE wecom_rpa_clients ADD COLUMN IF NOT EXISTS listen_mode TEXT;

-- ============================================================================
-- 2026-07-02 追踪库 obs_traces 新增 user_message_id 字段，用于精确关联
-- channel_messages.message_id，修复会话追踪页面撤回标记误标问题
-- 注意：obs_traces 表在追踪库（aid_work_logs2）中，不在主库，
-- 实际 ALTER 语句在 deploy/init-postgres-logs.sql 末尾，由 init_logs_tables() 幂等执行
-- ============================================================================
-- ALTER TABLE obs_traces ADD COLUMN IF NOT EXISTS user_message_id TEXT;
-- CREATE INDEX IF NOT EXISTS idx_obs_traces_user_msg_id ON obs_traces(user_message_id);

-- ============================================================================
-- 2026-07-09 tenant_channel_configs 增加 name 字段，用于用户手动为同一租户的
-- 多个同类渠道（如两个飞书）标注区分名称
-- ============================================================================
ALTER TABLE tenant_channel_configs ADD COLUMN IF NOT EXISTS name TEXT;
