"""
SaaS 多租户数据库表定义

共 8 张新表，由 init_saas_tables_sqlite() 或 init_saas_tables_postgresql() 统一创建。
在 src/db/database.py 的 init_database() 末尾调用。
"""

import sqlite3
from loguru import logger


def init_saas_tables_sqlite(conn: sqlite3.Connection):
    """初始化 SQLite SaaS 多租户相关表"""
    cursor = conn.cursor()

    # 1. 租户表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT UNIQUE NOT NULL,
            company_name TEXT NOT NULL,
            contact_name TEXT,
            contact_phone TEXT,
            initial_admin_name TEXT,
            initial_admin_phone TEXT,
            status TEXT DEFAULT 'active',
            plan TEXT DEFAULT 'basic',
            max_instances INTEGER DEFAULT 5,
            max_users INTEGER DEFAULT 50,
            settings TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenants_status
        ON tenants(status)
    """)

    # 4. 订阅表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id TEXT UNIQUE NOT NULL,
            tenant_id TEXT,
            user_id TEXT,
            subagent_type TEXT,
            billing_cycle TEXT DEFAULT 'monthly',
            unit_price REAL DEFAULT 0,
            token_quota INTEGER DEFAULT -1,
            tokens_used INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            payment_status TEXT DEFAULT 'pending',
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant
        ON subscriptions(tenant_id, status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_subscriptions_user
        ON subscriptions(user_id, status)
    """)

    # 5. 智能体实例表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_instances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instance_id TEXT UNIQUE NOT NULL,
            tenant_id TEXT NOT NULL,
            subscription_id TEXT,
            subagent_type TEXT NOT NULL,
            display_name TEXT NOT NULL,
            status TEXT DEFAULT 'stopped',
            config TEXT,
            bound_channel_type TEXT,
            allowed_skills TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(subscription_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_instances_tenant
        ON agent_instances(tenant_id, status)
    """)

    # 6. 租户渠道配置表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tenant_channel_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_id TEXT UNIQUE NOT NULL,
            tenant_id TEXT NOT NULL,
            channel_type TEXT NOT NULL,
            config TEXT NOT NULL,
            verified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenant_channel_configs_tenant
        ON tenant_channel_configs(tenant_id, channel_type)
    """)

def init_saas_tables_postgresql(conn):
    """初始化 PostgreSQL SaaS 多租户相关表"""
    cursor = conn.cursor()

    # 1. 租户表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT UNIQUE NOT NULL,
            company_name TEXT NOT NULL,
            contact_name TEXT,
            contact_phone TEXT,
            initial_admin_name TEXT,
            initial_admin_phone TEXT,
            status TEXT DEFAULT 'active',
            plan TEXT DEFAULT 'basic',
            max_instances INTEGER DEFAULT 5,
            max_users INTEGER DEFAULT 50,
            settings TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenants_status
        ON tenants(status)
    """)

    # 4. 订阅表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            subscription_id TEXT UNIQUE NOT NULL,
            tenant_id TEXT,
            user_id TEXT,
            subagent_type TEXT,
            billing_cycle TEXT DEFAULT 'monthly',
            unit_price REAL DEFAULT 0,
            token_quota INTEGER DEFAULT -1,
            tokens_used INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            payment_status TEXT DEFAULT 'pending',
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant
        ON subscriptions(tenant_id, status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_subscriptions_user
        ON subscriptions(user_id, status)
    """)

    # 5. 智能体实例表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_instances (
            id SERIAL PRIMARY KEY,
            instance_id TEXT UNIQUE NOT NULL,
            tenant_id TEXT NOT NULL,
            subscription_id TEXT,
            subagent_type TEXT NOT NULL,
            display_name TEXT NOT NULL,
            status TEXT DEFAULT 'stopped',
            config TEXT,
            bound_channel_type TEXT,
            allowed_skills TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(subscription_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_instances_tenant
        ON agent_instances(tenant_id, status)
    """)

    # 6. 租户渠道配置表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tenant_channel_configs (
            id SERIAL PRIMARY KEY,
            config_id TEXT UNIQUE NOT NULL,
            tenant_id TEXT NOT NULL,
            channel_type TEXT NOT NULL,
            config TEXT NOT NULL,
            verified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenant_channel_configs_tenant
        ON tenant_channel_configs(tenant_id, channel_type)
    """)

    # 8. 支付订单表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payment_orders (
            id SERIAL PRIMARY KEY,
            order_id TEXT UNIQUE NOT NULL,
            tenant_id TEXT NOT NULL,
            subscription_id TEXT,
            amount REAL NOT NULL,
            payment_method TEXT,
            payment_status TEXT DEFAULT 'pending',
            paid_at TIMESTAMP,
            transaction_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(subscription_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_payment_orders_tenant
        ON payment_orders(tenant_id, payment_status)
    """)

    conn.commit()
    logger.info("PostgreSQL SaaS multi-tenant tables initialized")


# 兼容旧接口
def init_saas_tables(conn):
    """兼容旧接口，根据连接类型自动选择"""
    if isinstance(conn, sqlite3.Connection):
        init_saas_tables_sqlite(conn)
    else:
        # 假设是 psycopg2 连接
        init_saas_tables_postgresql(conn)