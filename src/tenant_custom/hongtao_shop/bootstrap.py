"""宏陶模块自举（部署期种植，平台 DDL 不含租户专名）。

计费种子行 `hongtao_shop_image_parse`（0.01 元/张）由本模块自己种植，不进
`src/services/content_sync/` 平台 DDL——平台通用模块不得包含租户专名
（2026-09-21 用户决议；现行 VL 按 token 计费，种子行保留备运营切换固定价，
先例 wechat_mp_image_parse 2026-09-15 批次）。

种植入口：CLI `--init-source`（agent2/正式库部署步骤均执行；幂等可重复）。
"""

from __future__ import annotations

from loguru import logger

# 与 wechat_mp_image_parse 同价（×usage_factor 100 = 1 积分/张）
VL_IMAGE_PARSE_MODEL = "hongtao_shop_image_parse"
VL_IMAGE_PARSE_PRICE_PER_CALL = 0.01


def ensure_billing_seed() -> bool:
    """幂等种植 VL 计费伪模型价格行；已存在不改价（运营调价以 DB 为准）。

    Returns: 是否新插入。
    """
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO token_cost_prices (model_name, price_per_call)
            VALUES (%s, %s)
            ON CONFLICT (model_name) DO NOTHING
            RETURNING model_name
            """,
            (VL_IMAGE_PARSE_MODEL, VL_IMAGE_PARSE_PRICE_PER_CALL),
        )
        inserted = cursor.fetchone() is not None
        conn.commit()
    logger.bind(module="hongtao_shop").info(
        "hongtao_shop 计费种子行{}: {}={}",
        "已种植" if inserted else "已存在",
        VL_IMAGE_PARSE_MODEL, VL_IMAGE_PARSE_PRICE_PER_CALL,
    )
    return inserted
