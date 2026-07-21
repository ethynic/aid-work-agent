"""巡检商机模块 - 幂等建表

3 张业务表（遵循 database_dev.md：bs_ 前缀、tenant_id/user_id/created_at 必备、
TEXT 存枚举值、无外键/触发器/存储过程、只主键 NOT NULL）：

- bs_outbound_leads              商机主表
- bs_outbound_lead_interactions  商机互动/跟进记录
- bs_outbound_outreach_actions   我方接触动作审计

本函数**幂等**（CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS），
由调用方在合适的初始化时机显式调用，**不在包顶层 import 链上自动执行**。
未在此建 ``bs_outbound_account_sessions``（登录态子系统，由并行模块负责）。
"""

from loguru import logger


def init_outbound_tables(conn) -> None:
    """初始化巡检商机 3 张表 + 索引。

    参数:
        conn: 已打开的 PostgreSQL 连接（psycopg2）。本函数不负责 commit/close，
              由调用方在批量初始化场景统一管理事务。
    """
    cursor = conn.cursor()

    # ============================================================
    # bs_outbound_leads — 商机主表
    # ============================================================
    # 设计要点：
    # - raw_text_encrypted: 商机原文含他人 PII，必须 SecretCrypto 加密存储
    # - dedup_fingerprint: 同 tenant 内 UNIQUE，命中则更新而非新建
    # - intent_score: 0-100 意向分，列表默认按其 DESC 排序
    # - status: 状态机字段（见 state_machine.py），TEXT 存枚举值
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bs_outbound_leads (
            lead_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            platform TEXT,
            source_type TEXT,
            external_content_id TEXT,
            external_url TEXT,
            raw_text_encrypted TEXT,
            intent_score INTEGER,
            status TEXT DEFAULT 'new',
            assigned_user_id TEXT,
            dedup_fingerprint TEXT,
            contact_points TEXT,
            risk_flags TEXT,
            user_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # 同 tenant 内 dedup_fingerprint 唯一。NULL 指纹允许多条（兼容无指纹场景）。
    # 使用 COALESCE 构造部分唯一索引：仅当指纹非空时强制唯一。
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_bs_outbound_leads_dedup
            ON bs_outbound_leads(tenant_id, dedup_fingerprint)
            WHERE dedup_fingerprint IS NOT NULL
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bs_outbound_leads_tenant_status
            ON bs_outbound_leads(tenant_id, status, created_at DESC)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bs_outbound_leads_assigned
            ON bs_outbound_leads(tenant_id, assigned_user_id, intent_score DESC)
        """
    )

    # ============================================================
    # bs_outbound_lead_interactions — 商机互动/跟进记录
    # ============================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bs_outbound_lead_interactions (
            interaction_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            lead_id TEXT,
            interaction_type TEXT,
            content TEXT,
            actor_user_id TEXT,
            user_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bs_outbound_lead_interactions_lead
            ON bs_outbound_lead_interactions(tenant_id, lead_id, created_at DESC)
        """
    )

    # ============================================================
    # bs_outbound_outreach_actions — 我方接触动作（审计）
    # ============================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bs_outbound_outreach_actions (
            action_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            lead_id TEXT,
            action_type TEXT,
            channel TEXT,
            content_snapshot TEXT,
            execution_status TEXT,
            reviewer_user_id TEXT,
            user_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bs_outbound_outreach_actions_lead
            ON bs_outbound_outreach_actions(tenant_id, lead_id, created_at DESC)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bs_outbound_outreach_actions_tenant_status
            ON bs_outbound_outreach_actions(tenant_id, execution_status, created_at DESC)
        """
    )

    logger.info("巡检商机表初始化完成（bs_outbound_leads / _lead_interactions / _outreach_actions）")
