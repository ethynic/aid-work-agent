"""
积分计费服务（#37 租户积分充值与计费）

算法：
    当 token_cost_prices.cached_input_price_per_m 有值（区分缓存命中）时：
        token_cost = (prompt_tokens - cached_input_tokens) * input_price_per_m
                   + completion_tokens * output_price_per_m
                   + cached_input_tokens * cached_input_price_per_m
    否则（该模型计费不区分缓存命中）：
        token_cost = prompt_tokens * input_price_per_m + completion_tokens * output_price_per_m
    credit_cost = math.ceil(token_cost * usage_factor * 100) / 100

- 单价单位：元/百万 token（_per_m 后缀）
- credit_cost 精度：2 位小数，向上取整到 0.01
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
    cached_input_tokens: int = 0,
    usage_factor_override: Optional[int] = None,
) -> float:
    """计算本轮对话消耗的积分

    Args:
        prompt_tokens: 输入 token 数（含 cached_input_tokens）
        completion_tokens: 输出 token 数
        model: 模型名，用于查询单价
        cached_input_tokens: 命中缓存的输入 token 数（已包含在 prompt_tokens 内）
        usage_factor_override: 用量系数覆盖值；传入时覆盖 settings.billing.usage_factor，
            不传时维持原行为（读 settings.billing.usage_factor，默认 100）

    Returns:
        积分用量（2 位小数，向上取整到 0.01）；单价缺失返回 0.0
    """
    credit_cost, _ = calculate_credit_cost_with_breakdown(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model=model,
        cached_input_tokens=cached_input_tokens,
        usage_factor_override=usage_factor_override,
    )
    return credit_cost


def calculate_video_credit_cost(
    seconds: float,
    cost_per_second_yuan: float,
    provider: str,
    model: str,
) -> float:
    """计算视频生成消耗的积分（按秒计费）

    视频创作智能体（video-agent）专用计费函数，区别于主业务按 token 计费。
    公式：credit_cost = ceil(seconds × 单价 × video_gen_usage_factor × 100) / 100

    Args:
        seconds: 视频时长（秒）
        cost_per_second_yuan: 视频模型单价（元/秒），从 token_cost_prices.price_per_second 取
        provider: 视频生成 provider（wanx / minimax），用于日志
        model: 视频模型名（如 wan2.7-r2v / MiniMax-H3），用于日志

    Returns:
        积分用量（2 位小数，向上取整到 0.01）；单价缺失或秒数 ≤ 0 返回 0.0
    """
    credit_cost, _ = calculate_video_credit_cost_with_breakdown(
        seconds=seconds,
        cost_per_second_yuan=cost_per_second_yuan,
        provider=provider,
        model=model,
    )
    return credit_cost


def calculate_embedding_credit_cost(
    embedding_tokens: int,
    model: Optional[str] = "text-embedding-v3",
    usage_factor_override: Optional[int] = None,
) -> float:
    """计算 embedding 调用消耗的积分（按 token 计费）

    知识库向量化、检索 query 向量化等场景调用 text-embedding-v3 等向量模型时，
    按 token_cost_prices.embedding_price_per_m 单价计算积分。

    公式：credit_cost = ceil(embedding_tokens × embedding_price_per_m / 1e6 × embedding_usage_factor × 100) / 100

    Args:
        embedding_tokens: embedding 调用消耗的 token 数
        model: 向量模型名（默认 text-embedding-v3），用于查 embedding_price_per_m 单价
        usage_factor_override: 用量系数覆盖值；传入时覆盖 settings.billing.embedding_usage_factor

    Returns:
        积分用量（2 位小数，向上取整到 0.01）；单价缺失或 token ≤ 0 返回 0.0
    """
    credit_cost, _ = calculate_embedding_credit_cost_with_breakdown(
        embedding_tokens=embedding_tokens,
        model=model,
        usage_factor_override=usage_factor_override,
    )
    return credit_cost


def calculate_asr_credit_cost(
    asr_calls: int,
    model: str = "aliyun-nls-asr",
    usage_factor_override: Optional[int] = None,
) -> float:
    """计算 ASR 语音识别消耗的积分（按调用次数计费）

    阿里云 NLS 一句话识别按调用次数计费（响应不返回音频时长，无法按时长计量）。
    公式：credit_cost = ceil(asr_calls × asr_price_per_call × asr_usage_factor × 100) / 100

    Args:
        asr_calls: ASR 调用次数
        model: ASR 模型名（默认 aliyun-nls-asr），用于查 asr_price_per_call 单价
        usage_factor_override: 用量系数覆盖值

    Returns:
        积分用量（2 位小数，向上取整到 0.01）；单价缺失或次数 ≤ 0 返回 0.0
    """
    credit_cost, _ = calculate_asr_credit_cost_with_breakdown(
        asr_calls=asr_calls,
        model=model,
        usage_factor_override=usage_factor_override,
    )
    return credit_cost


# ============== 带 breakdown 的计算函数（usage_breakdown 单价追溯） ==============
# 返回 (credit_cost, breakdown)，breakdown 记录写入时的单价快照与分项积分，
# 使 chat_records.usage_breakdown 不依赖 token_cost_prices 表的当前快照即可独立对账。


def calculate_credit_cost_with_breakdown(
    prompt_tokens: int,
    completion_tokens: int,
    model: Optional[str],
    cached_input_tokens: int = 0,
    usage_factor_override: Optional[int] = None,
) -> tuple:
    """计算本轮对话消耗的积分，并返回分项单价/积分 breakdown。

    返回 (credit_cost, breakdown)，breakdown 结构：
    {
        "non_cached_input_tokens": int,
        "unit_prices": {"input_per_m": float, "cached_input_per_m": float|None, "output_per_m": float},
        "usage_factor": int,
        "credits": {"non_cached_input": float, "cached_input": float, "output": float},
    }
    单价缺失或 model 为空时返回 (0.0, {})。
    """
    if not model:
        logger.warning("计费：model 为空，credit_cost=0")
        return 0.0, {}

    tcp = TokenCostPriceDB.get_by_model_name(model)
    if not tcp:
        logger.warning(f"计费：模型 {model} 未配置单价，credit_cost=0")
        return 0.0, {}

    input_price = float(tcp.get("input_price_per_m") or 0)
    output_price = float(tcp.get("output_price_per_m") or 0)
    cached_input_price_raw = tcp.get("cached_input_price_per_m")
    has_cached_price = cached_input_price_raw is not None
    cached_input_price = float(cached_input_price_raw) if has_cached_price else 0.0

    if input_price <= 0 and output_price <= 0:
        logger.warning(f"计费：模型 {model} 单价全部为 0，credit_cost=0")
        return 0.0, {}

    settings = create_settings()
    if usage_factor_override is not None:
        usage_factor = usage_factor_override
    else:
        usage_factor = getattr(settings.billing, "usage_factor", 100) or 100

    prompt_tokens = prompt_tokens or 0
    completion_tokens = completion_tokens or 0
    cached_input_tokens = cached_input_tokens or 0

    if has_cached_price:
        non_cached_input = max(prompt_tokens - cached_input_tokens, 0)
        non_cached_input_cost = non_cached_input * input_price / 1_000_000
        cached_input_cost = cached_input_tokens * cached_input_price / 1_000_000
        output_cost = completion_tokens * output_price / 1_000_000
    else:
        non_cached_input = prompt_tokens
        non_cached_input_cost = prompt_tokens * input_price / 1_000_000
        cached_input_cost = 0.0
        output_cost = completion_tokens * output_price / 1_000_000

    token_cost = non_cached_input_cost + cached_input_cost + output_cost
    if token_cost <= 0:
        return 0.0, {}

    credit_cost = math.ceil(token_cost * usage_factor * 100) / 100
    credit_cost = max(credit_cost, 0.0)

    breakdown = {
        "non_cached_input_tokens": non_cached_input,
        "unit_prices": {
            "input_per_m": input_price,
            "cached_input_per_m": cached_input_price if has_cached_price else None,
            "output_per_m": output_price,
        },
        "usage_factor": usage_factor,
        "credits": {
            "non_cached_input": round(non_cached_input_cost * usage_factor, 6),
            "cached_input": round(cached_input_cost * usage_factor, 6),
            "output": round(output_cost * usage_factor, 6),
        },
    }
    return credit_cost, breakdown


def calculate_embedding_credit_cost_with_breakdown(
    embedding_tokens: int,
    model: Optional[str] = "text-embedding-v3",
    usage_factor_override: Optional[int] = None,
) -> tuple:
    """计算 embedding 积分，并返回单价/系数 breakdown。

    返回 (credit_cost, breakdown)，breakdown 结构：
    {"unit_price_per_m": float, "usage_factor": int}
    """
    if not model:
        logger.warning("embedding 计费：model 为空，credit_cost=0")
        return 0.0, {}

    if not embedding_tokens or embedding_tokens <= 0:
        return 0.0, {}

    tcp = TokenCostPriceDB.get_by_model_name(model)
    if not tcp:
        logger.warning(f"embedding 计费：模型 {model} 未配置单价，credit_cost=0")
        return 0.0, {}

    embedding_price = float(tcp.get("embedding_price_per_m") or 0)
    if embedding_price <= 0:
        logger.warning(f"embedding 计费：模型 {model} 的 embedding_price_per_m 为空或 0，credit_cost=0")
        return 0.0, {}

    settings = create_settings()
    if usage_factor_override is not None:
        usage_factor = usage_factor_override
    else:
        usage_factor = getattr(settings.billing, "embedding_usage_factor", 100) or 100

    token_cost = embedding_tokens * embedding_price / 1_000_000
    if token_cost <= 0:
        return 0.0, {}

    credit_cost = math.ceil(token_cost * usage_factor * 100) / 100
    credit_cost = max(credit_cost, 0.0)

    breakdown = {
        "unit_price_per_m": embedding_price,
        "usage_factor": usage_factor,
    }
    return credit_cost, breakdown


def calculate_asr_credit_cost_with_breakdown(
    asr_calls: int,
    model: str = "aliyun-nls-asr",
    usage_factor_override: Optional[int] = None,
) -> tuple:
    """计算 ASR 积分，并返回单价/系数 breakdown。

    返回 (credit_cost, breakdown)，breakdown 结构：
    {"unit_price_per_call": float, "usage_factor": int}
    """
    if not asr_calls or asr_calls <= 0:
        return 0.0, {}

    if not model:
        logger.warning("ASR 计费：model 为空，credit_cost=0")
        return 0.0, {}

    tcp = TokenCostPriceDB.get_by_model_name(model)
    if not tcp:
        logger.warning(f"ASR 计费：模型 {model} 未配置单价，credit_cost=0")
        return 0.0, {}

    asr_price = float(tcp.get("asr_price_per_call") or 0)
    if asr_price <= 0:
        logger.warning(f"ASR 计费：模型 {model} 的 asr_price_per_call 为空或 0，credit_cost=0")
        return 0.0, {}

    settings = create_settings()
    if usage_factor_override is not None:
        usage_factor = usage_factor_override
    else:
        usage_factor = getattr(settings.billing, "asr_usage_factor", 100) or 100

    cost = asr_calls * asr_price
    if cost <= 0:
        return 0.0, {}

    credit_cost = math.ceil(cost * usage_factor * 100) / 100
    credit_cost = max(credit_cost, 0.0)

    breakdown = {
        "unit_price_per_call": asr_price,
        "usage_factor": usage_factor,
    }
    return credit_cost, breakdown


def calculate_video_credit_cost_with_breakdown(
    seconds: float,
    cost_per_second_yuan: float,
    provider: str,
    model: str,
) -> tuple:
    """计算视频生成积分，并返回单价/系数 breakdown。

    返回 (credit_cost, breakdown)，breakdown 结构：
    {"unit_price_per_second": float, "usage_factor": int}
    """
    if seconds <= 0:
        return 0.0, {}
    if not cost_per_second_yuan or cost_per_second_yuan <= 0:
        logger.warning(f"视频计费：{provider}/{model} 单价为空，credit_cost=0")
        return 0.0, {}

    settings = create_settings()
    factor = getattr(settings.billing, "video_gen_usage_factor", 33) or 33

    credit_cost = math.ceil(seconds * cost_per_second_yuan * factor * 100) / 100
    credit_cost = max(credit_cost, 0.0)

    breakdown = {
        "unit_price_per_second": cost_per_second_yuan,
        "usage_factor": factor,
    }
    return credit_cost, breakdown
