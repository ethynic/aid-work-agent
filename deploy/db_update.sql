-- 数据库加表、加字段等SQL语句，记录在本文件，以便升级部署
-- 所有SQL语句必须幂等安全（可重复执行），使用 IF NOT EXISTS、DROP TABLE IF EXISTS 等保护措施

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
ALTER TABLE chunks_fts DROP CONSTRAINT IF EXISTS chunks_fts_chunk_id_fkey;

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