"""
SaaS 多租户数据库表定义

共 8 张新表，由 init_saas_tables() 创建。
在 src/db/database.py 的 init_database() 末尾调用。
"""

from loguru import logger


def init_saas_tables(conn):
    """初始化 PostgreSQL SaaS 多租户相关表"""
    cursor = conn.cursor()
    # 关闭自动提交，使用显式事务
    old_autocommit = conn.autocommit
    conn.autocommit = False
    try:

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
            tenant_code TEXT,
            credit_balance NUMERIC(12,2) NOT NULL DEFAULT 0.00,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenants_status
        ON tenants(status)
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenants_tenant_code
        ON tenants(tenant_code)
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
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

        # 8. 用户+数字员工授权表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_agent_permissions (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, agent_id)
        )
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_agent_permissions_user
        ON user_agent_permissions(user_id)
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_agent_permissions_tenant_user
        ON user_agent_permissions(tenant_id, user_id)
        """)

        # 6. 租户渠道配置表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tenant_channel_configs (
            id SERIAL PRIMARY KEY,
            config_id TEXT UNIQUE NOT NULL,
            tenant_id TEXT NOT NULL,
            channel_type TEXT NOT NULL,
            name TEXT,
            config TEXT NOT NULL,
            verified INTEGER DEFAULT 0,
            subagent_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_payment_orders_tenant
        ON payment_orders(tenant_id, payment_status)
        """)

        # 9. 租户充值流水表（预付费积分计费体系，#37）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tenant_recharges (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            amount_yuan NUMERIC(10,2) NOT NULL,
            credits INTEGER NOT NULL,
            rate INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            payment_order_id TEXT,
            operator_id TEXT,
            operator_name TEXT,
            remark TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenant_recharges_tenant_id
        ON tenant_recharges(tenant_id)
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenant_recharges_created_at
        ON tenant_recharges(created_at DESC)
        """)

        # 10. 客户留资线索表（售前咨询 lead_capture 能力，跨智能体中性命名）
        # 设计文档：docs/subagent/pre-sales/lead-capture-design.md（规范例外：bs_[subagent]_[tablename] → 能力级命名）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS bs_lead_capture_leads (
            id SERIAL PRIMARY KEY,
            lead_id TEXT UNIQUE NOT NULL,
            tenant_id TEXT NOT NULL,
            user_id TEXT,
            customer_user_id TEXT,
            channel_chat_id TEXT,
            kf_account_name TEXT,
            contact_method TEXT,
            phone TEXT,
            contact_name TEXT,
            demand_summary TEXT,
            source TEXT DEFAULT 'lead_capture',
            stage TEXT DEFAULT 'new',
            assigned_to TEXT,
            assignee_name TEXT,
            transferred_to TEXT,
            session_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_lc_leads_tenant
        ON bs_lead_capture_leads(tenant_id)
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_lc_leads_created
        ON bs_lead_capture_leads(created_at)
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_lc_leads_assigned
        ON bs_lead_capture_leads(assigned_to)
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_lc_leads_customer
        ON bs_lead_capture_leads(customer_user_id)
        """)

        conn.commit()
        logger.info("PostgreSQL SaaS multi-tenant tables initialized")
    except Exception as e:
        conn.rollback()
        logger.warning(f"Failed to initialize SaaS tables (rolling back): {e}")
        raise  # 重新抛出，让调用方也能处理
    finally:
        # 恢复原来的 autocommit 设置
        conn.autocommit = old_autocommit