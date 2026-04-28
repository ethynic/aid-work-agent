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