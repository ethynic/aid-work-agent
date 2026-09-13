"""微信会话绑定业务表（C1，设计 §13.3）。

bs_ 业务表：模块自建、不进 db_update.yaml（沿 weixin_marketing 范式）。
骨架字段从 C1 落全：pending 的验证字段（verifier_version/verified_at/expires_at）
及 encrypted_identity_evidence 允许 NULL；只有服务端接纳受信 Provider 真机
验证证据后才可写 verified、递增 identity_version 并设置有效期。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("weixin_conversation.init")

DDL_STATEMENTS = (
    """
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
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bs_wx_conv_bindings_owner
        ON bs_weixin_conversation_bindings (tenant_id, user_id, device_id, conversation_type)
    """,
)


def init_weixin_conversation_tables(conn) -> None:  # noqa: ANN001
    for ddl in DDL_STATEMENTS:
        cursor = conn.cursor()
        cursor.execute(ddl)
    conn.commit()


def ensure_tables() -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        init_weixin_conversation_tables(conn)
