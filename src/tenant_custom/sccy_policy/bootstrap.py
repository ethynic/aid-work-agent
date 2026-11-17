"""水城创业网模块自举（部署期种植；连接加密/无计费种子同 lyjy bootstrap）。"""

from __future__ import annotations

from typing import Any, Dict

from loguru import logger

DEFAULT_SYNC_INTERVAL_HOURS = 24  # 每日全量（研究 §5：sccy 日更兜底足够）
MODULE = "sccy_policy"


def init_source(tenant_id: str, connection_config: Dict[str, Any]) -> bool:
    """幂等建/更新源行（连接配置 Fernet 整包加密；语义同 lyjy bootstrap.init_source）。"""
    from src.db.database import get_db_connection
    from src.tenant_custom.lyjy_employment.bootstrap import build_connection_payload

    connection_encrypted = build_connection_payload(connection_config)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM bs_content_sync_sources "
            "WHERE tenant_id = %s AND module = %s",
            (tenant_id, MODULE),
        )
        existed = cursor.fetchone() is not None
        cursor.execute(
            """
            INSERT INTO bs_content_sync_sources
                (tenant_id, module, enabled, sync_interval_hours,
                 selection_mode, selected_ids, connection_encrypted)
            VALUES (%s, %s, TRUE, %s, 'all', NULL, %s)
            ON CONFLICT (tenant_id, module) DO UPDATE
            SET connection_encrypted = EXCLUDED.connection_encrypted,
                last_error = NULL
            """,
            (tenant_id, MODULE, DEFAULT_SYNC_INTERVAL_HOURS, connection_encrypted),
        )
        conn.commit()
    logger.bind(module=MODULE).info(
        "sccy_policy 源行{}: tenant={} interval={}h mode=all（连接配置已加密写入）",
        "已存在（连接配置已更新）" if existed else "已创建",
        tenant_id, DEFAULT_SYNC_INTERVAL_HOURS,
    )
    return not existed
