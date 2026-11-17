"""聊易就业模块自举（部署期种植，平台 DDL 不含租户专名）。

- 建源行：connection_encrypted（连接配置 Fernet 整包 JSON 加密，整包落库；
  data_connectors.password_encrypted 同模式）+ enabled/频率/全范围默认值，幂等；
- 计费种子：本模块零 VL，embedding 计费走平台既有 text-embedding-v3 单价行
  （_record_knowledge_embedding_billing 通用链路），无需模块伪模型种子行
  （宏陶种子行是 VL 伪模型专用，本模块无对应物）。

种植入口：CLI `--init-source`（测试/部署兜底；P2 起 portal 开通为正式入口）。
"""

from __future__ import annotations

import json
from typing import Any, Dict

from loguru import logger

DEFAULT_SYNC_INTERVAL_HOURS = 1  # 就业岗位时效性强（研究 §5 建议 0.5~1h；P2 可改）


def build_connection_payload(config: Dict[str, Any]) -> str:
    """连接配置 → Fernet 整包加密串（host/port/database/user/password）。"""
    from src.db.encryption import encryption_manager

    required = ("host", "port", "database", "user", "password")
    missing = [k for k in required if not config.get(k)]
    if missing:
        raise ValueError(f"连接配置缺少字段: {','.join(missing)}")
    return encryption_manager.encrypt(json.dumps({
        "host": str(config["host"]),
        "port": int(config["port"]),
        "database": str(config["database"]),
        "user": str(config["user"]),
        "password": str(config["password"]),
    }))


def init_source(tenant_id: str, connection_config: Dict[str, Any]) -> bool:
    """幂等建/更新源行（含连接配置加密写入）。

    源行存在时不覆盖既有 enabled/频率/范围（运营状态以 DB 为准），
    但连接配置随本次显式传入时更新（换库/换密码走同一路径）。
    Returns: 是否新插入源行。
    """
    from src.db.database import get_db_connection

    connection_encrypted = build_connection_payload(connection_config)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM bs_content_sync_sources "
            "WHERE tenant_id = %s AND module = 'lyjy_employment'",
            (tenant_id,),
        )
        existed = cursor.fetchone() is not None
        cursor.execute(
            """
            INSERT INTO bs_content_sync_sources
                (tenant_id, module, enabled, sync_interval_hours,
                 selection_mode, selected_ids, connection_encrypted)
            VALUES (%s, 'lyjy_employment', TRUE, %s, 'all', NULL, %s)
            ON CONFLICT (tenant_id, module) DO UPDATE
            SET connection_encrypted = EXCLUDED.connection_encrypted,
                last_error = NULL
            """,
            (tenant_id, DEFAULT_SYNC_INTERVAL_HOURS, connection_encrypted),
        )
        conn.commit()
    logger.bind(module="lyjy_employment").info(
        "lyjy_employment 源行{}: tenant={} interval={}h mode=all（连接配置已加密写入）",
        "已存在（连接配置已更新）" if existed else "已创建",
        tenant_id, DEFAULT_SYNC_INTERVAL_HOURS,
    )
    return not existed
