"""
积分计费服务（#37 租户积分充值与计费）

算法：
    token_cost = prompt_tokens × tcp.input_price_per_m + completion_tokens × tcp.output_price_per_m
    credit_cost = math.ceil(token_cost × usage_factor)

- 单价单位：元/百万 token（_per_m 后缀）
- cached_input_tokens 本期不计入
- token_cost_prices 无匹配记录时 credit_cost = 0（不阻断对话，记 warning 日志）
"""

import math
from typing import Optional, Dict, Any

from loguru import logger

from src.config.settings import create_settings
from src.db.models import TokenCostPriceDB


def calculate_credit_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model: Optional[str],
) -> int:
    """计算本轮对话消耗的积分

    Args:
        prompt_tokens: 输入 token 数（不含 cached）
        completion_tokens: 输出 token 数
        model: 模型名，用于查询单价

    Returns:
        积分用量（整数，向上取整）；单价缺失返回 0
    """
    if not model:
        logger.warning("计费：model 为空，credit_cost=0")
        return 0

    tcp = TokenCostPriceDB.get_by_model_name(model)
    if not tcp:
        logger.warning(f"计费：模型 {model} 未配置单价，credit_cost=0")
        return 0

    input_price = float(tcp.get("input_price_per_m") or 0)
    output_price = float(tcp.get("output_price_per_m") or 0)
    if input_price <= 0 and output_price <= 0:
        logger.warning(f"计费：模型 {model} 单价全部为 0，credit_cost=0")
        return 0

    settings = create_settings()
    usage_factor = getattr(settings.billing, "usage_factor", 100) or 100

    # token_cost 单位：元（百万 token 单价 × token 数 / 1e6）
    token_cost = (
        (prompt_tokens or 0) * input_price / 1_000_000
        + (completion_tokens or 0) * output_price / 1_000_000
    )
    if token_cost <= 0:
        return 0

    credit_cost = math.ceil(token_cost * usage_factor)
    return max(credit_cost, 0)
