-- 数据库加表、加字段等SQL语句，记录在本文件，以便升级部署

-- 2026-4-21，添加租户初始管理员信息
ALTER TABLE tenants add column IF NOT EXISTS initial_admin_name TEXT default '',add column IF NOT EXISTS initial_admin_phone TEXT default '';

-- 2026-4-24，chat_sessions 增加租户隔离字段
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS tenant_id TEXT;
CREATE INDEX IF NOT EXISTS idx_chat_sessions_tenant_user ON chat_sessions(tenant_id, user_id, updated_at DESC);

-- 2026-4-25，chat_sessions 增加 subagent_id 字段，记录会话关联的数字员工ID
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS subagent_id TEXT;

-- 2026-4-25，根据数据库开发规范，移除所有外键约束、移除非必要字段的 NOT NULL 约束、移除所有触发器
-- 外键完整性检查放到 Python 应用层实现，业务非空检查放到 Pydantic 模型层实现

-- 2026-4-26，新增数字员工授权体系两张表
-- 租户级数字员工授权表
CREATE TABLE IF NOT EXISTS tenant_agent_permissions (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    tenant_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tenant_agent_permissions_tenant ON tenant_agent_permissions(tenant_id);

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

-- 1. subscriptions 表增强：增加实例配额和生效时间
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS instance_quota INTEGER DEFAULT 1;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_subscriptions_time_range ON subscriptions(tenant_id, subagent_type, starts_at, expires_at, status);

-- 2. 数据迁移：将 tenant_agent_permissions 的授权迁移到 subscriptions 表
--    将现有权限转换为永久有效的订阅（expires_at = null）
INSERT INTO subscriptions (
    subscription_id, tenant_id, subagent_type, instance_quota,
    status, starts_at, created_at, updated_at
)
SELECT
    'sub_mig_' || md5(random()::text)::uuid::text AS subscription_id,
    tenant_id,
    agent_id AS subagent_type,
    1 AS instance_quota,
    'active' AS status,
    CURRENT_TIMESTAMP AS starts_at,
    CURRENT_TIMESTAMP AS created_at,
    CURRENT_TIMESTAMP AS updated_at
FROM tenant_agent_permissions tap
WHERE NOT EXISTS (
    SELECT 1 FROM subscriptions s
    WHERE s.tenant_id = tap.tenant_id
      AND s.subagent_type = tap.agent_id
      AND s.status = 'active'
);

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