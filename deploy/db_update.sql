-- 数据库加表、加字段等SQL语句，记录在本文件，以便升级部署
-- 所有SQL语句必须幂等安全（可重复执行），使用 IF NOT EXISTS、DROP TABLE IF EXISTS 等保护措施

-- 2026-6-2，users 表增加 nickname 字段，存储微信昵称等渠道用户昵称
ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname TEXT;

-- 2026-4-21，添加租户初始管理员信息
ALTER TABLE tenants add column IF NOT EXISTS initial_admin_name TEXT default '',add column IF NOT EXISTS initial_admin_phone TEXT default '';

-- 2026-4-24，chat_sessions 增加租户隔离字段
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS tenant_id TEXT;
CREATE INDEX IF NOT EXISTS idx_chat_sessions_tenant_user ON chat_sessions(tenant_id, user_id, updated_at DESC);

-- 2026-4-25，chat_sessions 增加 subagent_id 字段，记录会话关联的数字员工ID
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS subagent_id TEXT;

-- 2026-4-25，根据数据库开发规范，移除所有外键约束、移除非必要字段的 NOT NULL 约束、移除所有触发器
-- 外键完整性检查放到 Python 应用层实现，业务非空检查放到 Pydantic 模型层实现

-- 移除 chat_messages 表的外键约束
ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS chat_messages_session_id_fkey;

-- 移除 chat_sessions 表的外键约束
ALTER TABLE chat_sessions DROP CONSTRAINT IF EXISTS chat_sessions_user_id_fkey;

-- 移除 chat_records 表的外键约束
ALTER TABLE chat_records DROP CONSTRAINT IF EXISTS chat_records_session_id_fkey;
ALTER TABLE chat_records DROP CONSTRAINT IF EXISTS chat_records_user_id_fkey;

-- 移除其他表的外键约束（如果存在）
ALTER TABLE scheduled_task_logs DROP CONSTRAINT IF EXISTS scheduled_task_logs_task_id_fkey;
ALTER TABLE scheduled_task_logs DROP CONSTRAINT IF EXISTS scheduled_task_logs_user_id_fkey;
ALTER TABLE user_email_settings DROP CONSTRAINT IF EXISTS user_email_settings_user_id_fkey;
ALTER TABLE chunks DROP CONSTRAINT IF EXISTS chunks_doc_id_fkey;
ALTER TABLE chunks_vec DROP CONSTRAINT IF EXISTS chunks_vec_chunk_id_fkey;
DO $$ BEGIN ALTER TABLE chunks_fts DROP CONSTRAINT IF EXISTS chunks_fts_chunk_id_fkey; EXCEPTION WHEN undefined_table THEN NULL; END $$;

-- 移除 SaaS 表的外键约束
ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_tenant_id_fkey;
ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_user_id_fkey;
ALTER TABLE agent_instances DROP CONSTRAINT IF EXISTS agent_instances_tenant_id_fkey;
ALTER TABLE agent_instances DROP CONSTRAINT IF EXISTS agent_instances_subscription_id_fkey;
ALTER TABLE tenant_channel_configs DROP CONSTRAINT IF EXISTS tenant_channel_configs_tenant_id_fkey;
ALTER TABLE payment_orders DROP CONSTRAINT IF EXISTS payment_orders_tenant_id_fkey;
ALTER TABLE payment_orders DROP CONSTRAINT IF EXISTS payment_orders_subscription_id_fkey;
ALTER TABLE bs_trade_specialist_customer_emails DROP CONSTRAINT IF EXISTS bs_trade_specialist_customer_emails_customer_id_fkey;

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

-- 2026-4-27，用户手机号唯一性从全局改为租户内唯一（不同租户允许相同手机号）
-- 1. 删除 phone 全局唯一约束
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_phone_key;
-- 2. 创建租户内手机号联合唯一索引
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tenant_phone ON users (tenant_id, phone) WHERE phone IS NOT NULL AND tenant_id IS NOT NULL;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_wx_openid_key;

-- 2026-4-28，documents 表增加 tenant_id 字段，支持知识库文件租户隔离
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tenant_id TEXT;
CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents(tenant_id);

-- 2026-4-29，documents 表增加 summary 字段，存储文档摘要
ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary TEXT;

-- 2026-4-29，tenants 表增加 expire_at 字段，租户到期管理
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS expire_at TIMESTAMP;
COMMENT ON COLUMN tenants.expire_at IS '到期日期（时分秒为 23:59:59，当天仍可登录，空表示永久有效）';
CREATE INDEX IF NOT EXISTS idx_tenants_expire_at ON tenants(expire_at);

-- ============================================================================
-- 2026-4-30，数字员工实例并发控制功能开发
-- ============================================================================

-- 2026-5-2，排队系统增强：添加心跳机制和等待时间统计字段
ALTER TABLE agent_instance_queue ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE agent_instance_queue ADD COLUMN IF NOT EXISTS queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE agent_instance_queue ADD COLUMN IF NOT EXISTS started_at TIMESTAMP;
ALTER TABLE agent_instance_queue ADD COLUMN IF NOT EXISTS wait_duration_seconds INTEGER;
CREATE INDEX IF NOT EXISTS idx_agent_instance_queue_heartbeat ON agent_instance_queue(last_heartbeat_at);

-- 1. subscriptions 表增强：增加实例配额和生效时间
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS instance_quota INTEGER DEFAULT 1;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_subscriptions_time_range ON subscriptions(tenant_id, subagent_type, starts_at, expires_at, status);

-- 2. 删除已废弃的 tenant_agent_permissions 表（数据已合并到 subscriptions）
DROP TABLE IF EXISTS tenant_agent_permissions;

-- 3. agent_instances 表增强：增加拟人属性和锁状态字段
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS instance_name TEXT;
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS avatar TEXT DEFAULT '🤖';
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS personality_traits TEXT;
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'idle';
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS current_session_id TEXT;
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS current_user_id TEXT;
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS locked_at TIMESTAMP;
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS lock_expires_at TIMESTAMP;
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS total_chats INTEGER DEFAULT 0;
ALTER TABLE agent_instances ADD COLUMN IF NOT EXISTS total_messages INTEGER DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_agent_instances_tenant_type ON agent_instances(tenant_id, subagent_type, status);

-- 4. 创建实例等待队列表
CREATE TABLE IF NOT EXISTS agent_instance_queue (
    id SERIAL PRIMARY KEY,
    queue_id TEXT UNIQUE NOT NULL,
    instance_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT DEFAULT 'waiting',
    enqueued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    wait_timeout_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_instance_queue_instance ON agent_instance_queue(instance_id, position);
CREATE INDEX IF NOT EXISTS idx_instance_queue_session ON agent_instance_queue(session_id);
CREATE INDEX IF NOT EXISTS idx_instance_queue_timeout ON agent_instance_queue(wait_timeout_at);

-- 5. chat_sessions 表增加 instance_id 字段，与会话绑定
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS instance_id TEXT;
CREATE INDEX IF NOT EXISTS idx_chat_sessions_instance ON chat_sessions(instance_id, created_at DESC);

-- 2026-5-6，chat_sessions 增加 ended_at 字段，记录会话结束时间（用于排队等待时间估算）
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS ended_at TIMESTAMP;

-- 2026-5-6，演示模式创建的用户 tenant_id 为 NULL，将其设置为 'demo' 以便正常访问 /api/chat/instances
UPDATE users SET tenant_id = 'demo' WHERE tenant_id IS NULL;

-- 2026-5-6，同步更新 chat_sessions 表中 demo 用户的会话记录 tenant_id
UPDATE chat_sessions SET tenant_id = 'demo' WHERE tenant_id IS NULL AND user_id IN (SELECT user_id FROM users WHERE tenant_id = 'demo');

-- 2026-5-7，规范化 agent_instances 表 status 字段，只保留 idle 和 busy 状态
UPDATE agent_instances
SET status = CASE
    WHEN status IN ('running', 'stopped', 'offline', 'error') THEN
        CASE
            WHEN current_session_id IS NULL THEN 'idle'
            ELSE 'busy'
        END
    ELSE status  -- 保持现有的 idle 或 busy 不变
END;

-- 将 NULL 状态设为 idle
UPDATE agent_instances SET status = 'idle' WHERE status IS NULL;

-- 将所有非 idle/busy 的状态映射到 idle 或 busy（基于 current_session_id）
UPDATE agent_instances
SET status = CASE
    WHEN current_session_id IS NULL THEN 'idle'
    ELSE 'busy'
END
WHERE status NOT IN ('idle', 'busy');

-- 添加 CHECK 约束确保状态只允许 idle/busy（如果约束不存在）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'agent_instances'::regclass
        AND conname = 'agent_instances_status_check'
    ) THEN
        ALTER TABLE agent_instances ADD CONSTRAINT agent_instances_status_check
            CHECK (status IN ('idle', 'busy'));
    END IF;
END $$;

-- 2026-05-09，chat_records 增加 token 统计和审计字段
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS cached_input_tokens INTEGER DEFAULT 0;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS provider TEXT;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS agent_iterations INTEGER DEFAULT 0;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS subagent_calls TEXT;
CREATE INDEX IF NOT EXISTS idx_chat_records_tenant_time ON chat_records(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_records_tenant_model ON chat_records(tenant_id, model, created_at DESC);

-- 2026-05-20，chat_records 增加 source_type 区分聊天来源（chat/wecom/dingtalk/feishu）
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS source_type TEXT DEFAULT 'chat';
CREATE INDEX IF NOT EXISTS idx_chat_records_source_type ON chat_records(source_type, created_at DESC);

-- 2026-05-09，旅游报价定价数据表（9张）
-- 车型与包车价格
CREATE TABLE IF NOT EXISTS bs_travel_quote_vehicles (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    region_name TEXT,
    vehicle_type TEXT NOT NULL,
    vehicle_type_label TEXT,
    seats_min INT NOT NULL,
    seats_max INT NOT NULL,
    daily_rate DECIMAL(10,2) NOT NULL,
    overtime_rate DECIMAL(10,2),
    overkm_rate DECIMAL(10,2),
    driver_meal_allowance DECIMAL(10,2),
    driver_accommodation DECIMAL(10,2),
    pricing_mode TEXT DEFAULT 'daily',
    per_km_rate DECIMAL(10,2),
    base_km DECIMAL(10,2),
    base_fee DECIMAL(10,2),
    season_type TEXT DEFAULT 'default',
    effective_from DATE,
    effective_to DATE,
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INT DEFAULT 0,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_travel_guides_tenant ON bs_travel_quote_guides(tenant_id, is_active);

-- 其他固定费用
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_travel_seasons_tenant ON bs_travel_quote_seasons(tenant_id, is_active);

-- 2026-05-10，tenants 表增加 tenant_code 字段（4-8位字母数字，唯一性在应用层检查）
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS tenant_code TEXT;
CREATE INDEX IF NOT EXISTS idx_tenants_tenant_code ON tenants(tenant_code);

-- 2026-05-10，为现有租户生成默认租户代码（t + 租户ID后5位大写字母）
UPDATE tenants
SET tenant_code = 't' || UPPER(RIGHT(tenant_id, 5))
WHERE tenant_code IS NULL;

-- 2026-05-11，新增 Token 成本价表，用于平台报表计算各模型 Token 消耗成本
CREATE TABLE IF NOT EXISTS token_cost_prices (
    id SERIAL PRIMARY KEY,
    model_name TEXT UNIQUE NOT NULL,
    input_price_per_m NUMERIC(10,4),
    output_price_per_m NUMERIC(10,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m)
VALUES ('qwen-plus', 0.8, 2.0)
ON CONFLICT (model_name) DO NOTHING;
INSERT INTO token_cost_prices (model_name, input_price_per_m, output_price_per_m)
VALUES ('deepseek-v4-flash', 1.0, 2.0)
ON CONFLICT (model_name) DO NOTHING;

-- 2026-5-12，车辆表新增按公里计费字段
ALTER TABLE bs_travel_quote_vehicles ADD COLUMN IF NOT EXISTS pricing_mode TEXT DEFAULT 'daily';
ALTER TABLE bs_travel_quote_vehicles ADD COLUMN IF NOT EXISTS per_km_rate DECIMAL(10,2);
ALTER TABLE bs_travel_quote_vehicles ADD COLUMN IF NOT EXISTS base_km DECIMAL(10,2);
ALTER TABLE bs_travel_quote_vehicles ADD COLUMN IF NOT EXISTS base_fee DECIMAL(10,2);

-- 2026-5-13，酒店/景点迁移至向量知识库，删除旧表
DROP TABLE IF EXISTS bs_travel_quote_rooms;
DROP TABLE IF EXISTS bs_travel_quote_hotels;
DROP TABLE IF EXISTS bs_travel_quote_tickets;
DROP TABLE IF EXISTS bs_travel_quote_attractions;

-- 2026-5-13，车辆表 daily_rate 去掉 NOT NULL（按公里计价时 daily_rate 为空）
ALTER TABLE bs_travel_quote_vehicles ALTER COLUMN daily_rate DROP NOT NULL;

-- 2026-5-13，车辆表简化：去掉 seats_min/overtime_rate/overkm_rate/base_km/base_fee，新增 seats_max
ALTER TABLE bs_travel_quote_vehicles ALTER COLUMN seats_min DROP NOT NULL;
ALTER TABLE bs_travel_quote_vehicles ALTER COLUMN pricing_mode SET DEFAULT 'per_km';

-- 2026-5-13，车辆表删除 season_type 和 sort_order 字段
ALTER TABLE bs_travel_quote_vehicles DROP COLUMN IF EXISTS season_type;
ALTER TABLE bs_travel_quote_vehicles DROP COLUMN IF EXISTS sort_order;

-- 2026-5-18，tenant_channel_configs 增加 subagent_type 字段，支持渠道配置关联数字员工
ALTER TABLE tenant_channel_configs ADD COLUMN IF NOT EXISTS subagent_type TEXT;

-- 2026-5-18，新增错误日志表（平台级）
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

-- 2026-5-19，channel_sessions 和 channel_messages 增加 tenant_id 字段，支持租户隔离
ALTER TABLE channel_sessions ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
-- 更新索引：替换旧的 idx_channel_sessions_channel 为包含 tenant_id 的新索引
DROP INDEX IF EXISTS idx_channel_sessions_channel;
CREATE INDEX IF NOT EXISTS idx_channel_sessions_tenant_channel ON channel_sessions(tenant_id, channel_type, channel_user_id);
-- 清除旧数据（旧 session_id 不含 tenant_id，且无 tenant_id 字段值，无法区分租户）
DELETE FROM channel_messages WHERE tenant_id = '';
DELETE FROM channel_sessions WHERE tenant_id = '';

-- 2026-5-20，channel_sessions 增加 subagent_id 字段，区分同一用户与不同智能体的对话
ALTER TABLE channel_sessions ADD COLUMN IF NOT EXISTS subagent_id TEXT;
-- 更新索引：包含 subagent_id
DROP INDEX IF EXISTS idx_channel_sessions_tenant_channel;
CREATE INDEX IF NOT EXISTS idx_channel_sessions_tenant_channel ON channel_sessions(tenant_id, channel_type, channel_user_id, subagent_id);

-- 2026-5-14，景点区域搜索：为 documents.metadata 添加 GIN 索引（部分索引，仅景点资源）
CREATE INDEX IF NOT EXISTS idx_documents_metadata_gin
ON documents USING GIN ((metadata::jsonb))
WHERE source_type = 'attraction_resource';

-- ============================================================================
-- 2026-5-21，售后服务子智能体：子智能体环境变量表 + 售后业务表
-- ============================================================================

-- 子智能体环境变量表（系统表，非业务表，供所有子智能体共用）
-- 替代原先的 tenant_api_credentials 表，通用化设计：任何子智能体都可定义自己的环境变量
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

-- 旧表 tenant_api_credentials 已废弃，由 subagent_env_vars 替代
-- CREATE TABLE IF NOT EXISTS tenant_api_credentials (...);

-- 售后工单表
CREATE TABLE IF NOT EXISTS bs_after_sales_tickets (
    id SERIAL PRIMARY KEY,
    ticket_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT NOT NULL,
    session_id TEXT,
    order_id TEXT,
    category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT NOT NULL DEFAULT 'normal',
    description TEXT NOT NULL,
    resolution TEXT,
    external_ticket_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ast_tenant ON bs_after_sales_tickets(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_ast_user ON bs_after_sales_tickets(user_id, status);

-- 工单消息表
CREATE TABLE IF NOT EXISTS bs_after_sales_ticket_messages (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    ticket_id TEXT NOT NULL,
    sender_type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_astm_ticket ON bs_after_sales_ticket_messages(tenant_id, ticket_id);

-- 退换货记录表
CREATE TABLE IF NOT EXISTS bs_after_sales_returns (
    id SERIAL PRIMARY KEY,
    return_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT NOT NULL,
    session_id TEXT,
    order_id TEXT NOT NULL,
    type TEXT NOT NULL,
    reason TEXT NOT NULL,
    items JSON,
    status TEXT NOT NULL DEFAULT 'pending',
    external_return_id TEXT,
    refund_amount NUMERIC(10,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_asr_tenant ON bs_after_sales_returns(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_asr_user ON bs_after_sales_returns(user_id, status);
CREATE INDEX IF NOT EXISTS idx_asr_order ON bs_after_sales_returns(order_id);

-- ============================================================
-- 2026-5-25，投诉处理智能体相关表
-- ============================================================

-- 投诉主表
CREATE TABLE IF NOT EXISTS bs_complaint_handling_complaints (
    id SERIAL PRIMARY KEY,
    complaint_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT NOT NULL,
    session_id TEXT,
    order_id TEXT,
    customer_name TEXT,
    contact_info TEXT,
    category TEXT NOT NULL,
    sub_category TEXT,
    tags TEXT,
    customer_emotion TEXT,
    emotion_intensity REAL,
    urgency TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'open',
    escalation_level INT DEFAULT 0,
    description TEXT NOT NULL,
    resolution TEXT,
    escalated_to TEXT,
    escalation_reason TEXT,
    escalated_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    first_response_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_complaints_tenant_status ON bs_complaint_handling_complaints (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_complaints_user_status ON bs_complaint_handling_complaints (user_id, status);
CREATE INDEX IF NOT EXISTS idx_complaints_urgency ON bs_complaint_handling_complaints (urgency, status);
CREATE INDEX IF NOT EXISTS idx_complaints_category ON bs_complaint_handling_complaints (category);
CREATE INDEX IF NOT EXISTS idx_complaints_created ON bs_complaint_handling_complaints (created_at DESC);

-- 投诉交互记录
CREATE TABLE IF NOT EXISTS bs_complaint_handling_interactions (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    complaint_id TEXT NOT NULL,
    interaction_type TEXT NOT NULL DEFAULT 'message',
    sender_type TEXT NOT NULL,
    sender_name TEXT,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_interactions_complaint ON bs_complaint_handling_interactions (complaint_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_interactions_tenant ON bs_complaint_handling_interactions (tenant_id);

-- 历史案例解决方案库
CREATE TABLE IF NOT EXISTS bs_complaint_handling_case_solutions (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    complaint_id TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL,
    sub_category TEXT,
    problem_summary TEXT NOT NULL,
    root_cause TEXT,
    solution TEXT NOT NULL,
    resolution_time_hours REAL,
    customer_satisfied BOOLEAN,
    compensation_type TEXT,
    compensation_amount NUMERIC(12,2),
    effective BOOLEAN DEFAULT true,
    tags TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_case_solutions_category ON bs_complaint_handling_case_solutions (category);
CREATE INDEX IF NOT EXISTS idx_case_solutions_tenant ON bs_complaint_handling_case_solutions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_case_solutions_effective ON bs_complaint_handling_case_solutions (effective, category);

-- 跟进任务表
CREATE TABLE IF NOT EXISTS bs_complaint_handling_followups (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    complaint_id TEXT NOT NULL,
    action TEXT NOT NULL,
    assigned_to TEXT,
    due_date TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending',
    completed_at TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_followups_complaint ON bs_complaint_handling_followups (complaint_id, status);
CREATE INDEX IF NOT EXISTS idx_followups_due ON bs_complaint_handling_followups (due_date, status);
CREATE INDEX IF NOT EXISTS idx_followups_tenant ON bs_complaint_handling_followups (tenant_id);

-- ============================================
-- 2026-05-25 客户跟进智能体 - 5 张业务表
-- ============================================

-- 1. 线索主表
CREATE TABLE IF NOT EXISTS bs_customer_followup_leads (
    id SERIAL PRIMARY KEY,
    lead_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT,
    company_name TEXT,
    contact_name TEXT,
    phone TEXT,
    email TEXT,
    source TEXT,
    industry TEXT,
    region TEXT,
    address TEXT,
    product_interest TEXT,
    budget_range TEXT,
    estimated_deal_amount NUMERIC(12,2),
    description TEXT,
    stage TEXT DEFAULT 'new',
    stage_entered_at TIMESTAMP,
    score INTEGER DEFAULT 0,
    assigned_to TEXT,
    assigned_at TIMESTAMP,
    assignment_rule TEXT,
    status TEXT DEFAULT 'active',
    lost_reason TEXT,
    next_followup_at TIMESTAMP,
    last_followup_at TIMESTAMP,
    followup_count INTEGER DEFAULT 0,
    import_batch TEXT,
    external_id TEXT,
    tags TEXT[] DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cf_leads_tenant ON bs_customer_followup_leads(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cf_leads_assigned ON bs_customer_followup_leads(tenant_id, assigned_to);
CREATE INDEX IF NOT EXISTS idx_cf_leads_stage ON bs_customer_followup_leads(tenant_id, stage);
CREATE INDEX IF NOT EXISTS idx_cf_leads_status ON bs_customer_followup_leads(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_cf_leads_next_followup ON bs_customer_followup_leads(tenant_id, next_followup_at);
CREATE INDEX IF NOT EXISTS idx_cf_leads_external ON bs_customer_followup_leads(external_id);
CREATE INDEX IF NOT EXISTS idx_cf_leads_tags ON bs_customer_followup_leads USING GIN(tags);

-- 2. 跟进记录表
CREATE TABLE IF NOT EXISTS bs_customer_followup_records (
    id SERIAL PRIMARY KEY,
    record_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    lead_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    followup_type TEXT NOT NULL,
    content TEXT NOT NULL,
    followup_at TIMESTAMP DEFAULT NOW(),
    duration_minutes INTEGER,
    outcome TEXT,
    next_action TEXT,
    next_followup_at TIMESTAMP,
    quality_score INTEGER,
    call_id TEXT,
    call_transcript TEXT,
    call_sentiment TEXT,
    call_summary TEXT,
    attachments JSONB DEFAULT '[]',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cf_records_tenant ON bs_customer_followup_records(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cf_records_lead ON bs_customer_followup_records(lead_id);
CREATE INDEX IF NOT EXISTS idx_cf_records_user ON bs_customer_followup_records(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_cf_records_date ON bs_customer_followup_records(tenant_id, followup_at);

-- 3. 销售人员表
CREATE TABLE IF NOT EXISTS bs_customer_followup_sales_reps (
    id SERIAL PRIMARY KEY,
    rep_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    department TEXT,
    role TEXT DEFAULT 'sales',
    active_lead_count INTEGER DEFAULT 0,
    max_leads INTEGER DEFAULT 50,
    is_active BOOLEAN DEFAULT TRUE,
    skills TEXT[] DEFAULT '{}',
    region TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cf_reps_tenant ON bs_customer_followup_sales_reps(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cf_reps_user ON bs_customer_followup_sales_reps(user_id);
CREATE INDEX IF NOT EXISTS idx_cf_reps_active ON bs_customer_followup_sales_reps(tenant_id, is_active);

-- 4. 分配规则表
CREATE TABLE IF NOT EXISTS bs_customer_followup_assign_rules (
    id SERIAL PRIMARY KEY,
    rule_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT,
    name TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    conditions JSONB DEFAULT '{}',
    target_rep_ids TEXT[] DEFAULT '{}',
    auto_assign BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cf_rules_tenant ON bs_customer_followup_assign_rules(tenant_id);

-- 5. 转化漏斗事件表（仅追加）
CREATE TABLE IF NOT EXISTS bs_customer_followup_conversion_funnel (
    id SERIAL PRIMARY KEY,
    funnel_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT,
    lead_id TEXT NOT NULL,
    from_stage TEXT,
    to_stage TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    changed_by TEXT,
    days_in_previous_stage INTEGER,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_cf_funnel_tenant ON bs_customer_followup_conversion_funnel(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cf_funnel_lead ON bs_customer_followup_conversion_funnel(lead_id);
CREATE INDEX IF NOT EXISTS idx_cf_funnel_stage ON bs_customer_followup_conversion_funnel(tenant_id, to_stage);
CREATE INDEX IF NOT EXISTS idx_cf_funnel_date ON bs_customer_followup_conversion_funnel(tenant_id, created_at);

-- ============================================================================
-- 2026-05-25 性能优化：加速过期锁清理查询 + PostgreSQL 容器参数优化
-- ============================================================================

-- 1. agent_instances 复合索引：加速 lock cleanup 查询
-- 原查询：WHERE current_session_id IS NOT NULL AND lock_expires_at < CURRENT_TIMESTAMP
CREATE INDEX IF NOT EXISTS idx_agent_instances_lock_expires
ON agent_instances(lock_expires_at)
WHERE current_session_id IS NOT NULL;

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
