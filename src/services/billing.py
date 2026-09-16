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
- token_cost_prices 无匹配记录时按兜底模型 deepseek-flash 的价格计费
  （2026-09-15 负责人定版，breakdown 记 price_model 供对账；兜底也无价目行
  或 model 为空时 credit_cost = 0，不阻断对话，记 warning 日志）
"""

import math
from typing import Optional, Dict, Any

from loguru import logger

from src.config.settings import create_settings
from src.db.models import TokenCostPriceDB

# 模型未配置单价时的兜底计价模型（负责人定版 2026-09-15：匹配不到按
# deepseek v4 flash 的价格计算，不再落 0——部署主模型名可能不在价目表）
BILLING_FALLBACK_MODEL = "deepseek-flash"


def calculate_credit_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model: Optional[str],
    cached_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    usage_factor_override: Optional[int] = None,
) -> float:
    """计算本轮对话消耗的积分

    Args:
        prompt_tokens: 输入 token 数（含 cached_input_tokens）
        completion_tokens: 输出 token 数
        model: 模型名，用于查询单价
        cached_input_tokens: 命中缓存的输入 token 数（已包含在 prompt_tokens 内）
        cache_creation_input_tokens: 显式缓存创建 token 数（按输入价 125% 计费）
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
        cache_creation_input_tokens=cache_creation_input_tokens,
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


def _pick_tier(tiers: list, input_tokens: int) -> dict:
    """取单次请求输入 token 数所在的分段档位；超过最大档用最后一档兜底。

    Args:
        tiers: token_cost_prices.tiered_pricing，元素为
            {"max_input": int, "input_per_m": float, "cached_input_per_m": float|None, "output_per_m": float}
            按 max_input 升序排列（隐含区间：上一档 max < T ≤ 本档 max）
        input_tokens: 单次请求的输入 token 数

    Returns:
        命中的档位 dict；tiers 为空时返回 {}
    """
    if not tiers:
        return {}
    for tier in tiers:
        if input_tokens <= int(tier.get("max_input", 0)):
            return tier
    return tiers[-1]


def _resolve_unit_prices(tcp: dict, prompt_tokens: int) -> tuple:
    """解析模型单价（tiered 模型按单次输入 token 查档，否则用统一单价）

    Args:
        tcp: TokenCostPriceDB.get_by_model_name 返回的行
        prompt_tokens: 单次请求的输入 token 数（用于 tiered 查档）

    Returns:
        (input_price, output_price, has_cached_price, cached_input_price)
    """
    tiers = tcp.get("tiered_pricing")
    if tiers:
        tier = _pick_tier(tiers, prompt_tokens or 0)
        cached_raw = tier.get("cached_input_per_m")
        return (
            float(tier.get("input_per_m") or 0),
            float(tier.get("output_per_m") or 0),
            cached_raw is not None,
            float(cached_raw) if cached_raw is not None else 0.0,
        )
    cached_raw = tcp.get("cached_input_price_per_m")
    return (
        float(tcp.get("input_price_per_m") or 0),
        float(tcp.get("output_price_per_m") or 0),
        cached_raw is not None,
        float(cached_raw) if cached_raw is not None else 0.0,
    )


def calculate_credit_cost_with_breakdown(
    prompt_tokens: int,
    completion_tokens: int,
    model: Optional[str],
    cached_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
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
    cache_creation_input_tokens 按输入单价 125% 计费（显式缓存创建，百炼官方口径）。
    模型未配置单价时按兜底模型 deepseek-flash 的价格计费（2026-09-15 负责人定版，
    breakdown 记 price_model 供对账）；兜底模型也无价目行或 model 为空时返回 (0.0, {})。
    """
    if not model:
        logger.warning("计费：model 为空，credit_cost=0")
        return 0.0, {}

    tcp = TokenCostPriceDB.get_by_model_name(model)
    price_model = model
    if not tcp:
        # 负责人定版（2026-09-15）：模型未配置单价时按兜底模型价格计费，不再落 0——
        # 部署主模型名可能不在价目表（实测 agent2 deepseek-flash 致总结计费 0）
        tcp = TokenCostPriceDB.get_by_model_name(BILLING_FALLBACK_MODEL)
        price_model = BILLING_FALLBACK_MODEL
        if not tcp:
            logger.warning(
                f"计费：模型 {model} 未配置单价且兜底模型 {BILLING_FALLBACK_MODEL} "
                f"也无价目行，credit_cost=0"
            )
            return 0.0, {}
        logger.warning(
            f"计费：模型 {model} 未配置单价，按兜底模型 {BILLING_FALLBACK_MODEL} 价格计费"
        )

    input_price, output_price, has_cached_price, cached_input_price = _resolve_unit_prices(tcp, prompt_tokens)

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
    cache_creation_input_tokens = cache_creation_input_tokens or 0

    if has_cached_price:
        non_cached_input = max(prompt_tokens - cached_input_tokens - cache_creation_input_tokens, 0)
        non_cached_input_cost = non_cached_input * input_price / 1_000_000
        cached_input_cost = cached_input_tokens * cached_input_price / 1_000_000
        # 显式缓存创建按输入价 125% 计费（百炼官方口径）
        creation_input_cost = cache_creation_input_tokens * input_price * 1.25 / 1_000_000
        output_cost = completion_tokens * output_price / 1_000_000
    else:
        non_cached_input = prompt_tokens
        non_cached_input_cost = prompt_tokens * input_price / 1_000_000
        cached_input_cost = 0.0
        creation_input_cost = 0.0
        output_cost = completion_tokens * output_price / 1_000_000

    token_cost = non_cached_input_cost + cached_input_cost + creation_input_cost + output_cost
    if token_cost <= 0:
        return 0.0, {}

    credit_cost = math.ceil(token_cost * usage_factor * 100) / 100
    credit_cost = max(credit_cost, 0.0)

    breakdown = {
        "non_cached_input_tokens": non_cached_input,
        "unit_prices": {
            "input_per_m": input_price,
            "cached_input_per_m": cached_input_price if has_cached_price else None,
            "cache_creation_input_per_m": round(input_price * 1.25, 6) if has_cached_price else None,
            "output_per_m": output_price,
        },
        "usage_factor": usage_factor,
        # 实际计价所用模型（模型无价目行时为兜底模型，供对账识别）
        "price_model": price_model,
        "credits": {
            "non_cached_input": round(non_cached_input_cost * usage_factor, 6),
            "cached_input": round(cached_input_cost * usage_factor, 6),
            "cache_creation_input": round(creation_input_cost * usage_factor, 6),
            "output": round(output_cost * usage_factor, 6),
        },
    }
    if price_model != model:
        breakdown["price_fallback"] = True
    if tcp.get("tiered_pricing"):
        breakdown["tiered"] = True
    return credit_cost, breakdown


def calculate_llm_credit_cost_with_breakdown(
    usage_calls: list,
    model: Optional[str],
    usage_factor_override: Optional[int] = None,
) -> tuple:
    """多轮 LLM 调用合并计费（分段计价模型专用入口）

    SessionRecordService.save() 使用：agent 多轮循环每轮 LLM 调用是独立的 API 请求，
    分段计价模型（tiered_pricing）每轮的输入 token 数可能落在不同档位、单价不同，
    累加后按单一单价计费无意义。本函数对每轮按该轮输入 token 数取档累加成本，
    再按 {成本价合计}/{token数合计} 反向算出合并单价记入 breakdown，保证
    「合并单价 × 累计 token = 累计成本」对账自洽。

    Args:
        usage_calls: 每轮 LLM 调用 usage 快照列表，元素为
            {"prompt_tokens": int, "completion_tokens": int, "cached_tokens": int}
        model: 模型名
        usage_factor_override: 用量系数覆盖值；不传读 settings.billing.usage_factor

    Returns:
        (credit_cost, breakdown)。非 tiered 模型回退原逻辑（按累计 token 统一算）；
        model 缺失或 tcp 无单价返回 (0.0, {})。
    """
    if not model:
        logger.warning("计费：model 为空，credit_cost=0")
        return 0.0, {}
    if not usage_calls:
        return 0.0, {}

    tcp = TokenCostPriceDB.get_by_model_name(model)
    if not tcp:
        logger.warning(f"计费：模型 {model} 未配置单价，credit_cost=0")
        return 0.0, {}

    tiers = tcp.get("tiered_pricing")
    if not tiers:
        # 非分段模型：按累计 token 走原逻辑
        return calculate_credit_cost_with_breakdown(
            prompt_tokens=sum(int(c.get("prompt_tokens", 0) or 0) for c in usage_calls),
            completion_tokens=sum(int(c.get("completion_tokens", 0) or 0) for c in usage_calls),
            model=model,
            cached_input_tokens=sum(int(c.get("cached_tokens", 0) or 0) for c in usage_calls),
            cache_creation_input_tokens=sum(int(c.get("cache_creation_tokens", 0) or 0) for c in usage_calls),
            usage_factor_override=usage_factor_override,
        )

    settings = create_settings()
    if usage_factor_override is not None:
        usage_factor = usage_factor_override
    else:
        usage_factor = getattr(settings.billing, "usage_factor", 100) or 100

    # 逐轮取档，累加「单价 × token 数」加权和（中间量，/1e6 后为元）与各分项 token 数
    sum_input = 0.0
    sum_cached = 0.0
    sum_creation = 0.0
    sum_output = 0.0
    w_input = 0
    w_cached = 0
    w_creation = 0
    w_output = 0
    total_prompt = 0
    total_completion = 0
    total_cached = 0
    total_creation = 0
    for c in usage_calls:
        prompt = int(c.get("prompt_tokens", 0) or 0)
        completion = int(c.get("completion_tokens", 0) or 0)
        cached = int(c.get("cached_tokens", 0) or 0)
        creation = int(c.get("cache_creation_tokens", 0) or 0)
        total_prompt += prompt
        total_completion += completion
        total_cached += cached
        total_creation += creation
        tier = _pick_tier(tiers, prompt)
        cached_raw = tier.get("cached_input_per_m")
        has_cached = cached_raw is not None
        if has_cached:
            non_cached = max(prompt - cached - creation, 0)
        else:
            non_cached = prompt
            cached = 0
            creation = 0
        input_price = float(tier.get("input_per_m") or 0)
        output_price = float(tier.get("output_per_m") or 0)
        cached_price = float(cached_raw) if has_cached else 0.0
        # 显式缓存创建按输入价 125% 计费（百炼官方口径），与命中（10%）分开核算
        creation_price = input_price * 1.25
        sum_input += non_cached * input_price
        sum_cached += cached * cached_price
        sum_creation += creation * creation_price
        sum_output += completion * output_price
        w_input += non_cached
        w_cached += cached
        w_creation += creation
        w_output += completion

    token_cost = (sum_input + sum_cached + sum_creation + sum_output) / 1_000_000
    if token_cost <= 0:
        return 0.0, {}

    credit_cost = math.ceil(token_cost * usage_factor * 100) / 100
    credit_cost = max(credit_cost, 0.0)

    # 合并反向单价（元/百万 token）= 分项成本价合计 / 分项 token 数合计；分母为 0 置 None
    def _merged_price(cost_part: float, weight: int):
        return (cost_part / weight) if weight and weight > 0 else None

    breakdown = {
        "tiered": True,
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "cached_input_tokens": total_cached,
        "cache_creation_input_tokens": total_creation,
        "non_cached_input_tokens": w_input,
        "unit_prices": {
            "input_per_m": _merged_price(sum_input, w_input),
            "cached_input_per_m": _merged_price(sum_cached, w_cached),
            "cache_creation_input_per_m": _merged_price(sum_creation, w_creation),
            "output_per_m": _merged_price(sum_output, w_output),
        },
        "usage_factor": usage_factor,
        "credits": {
            "non_cached_input": round(sum_input / 1_000_000 * usage_factor, 6),
            "cached_input": round(sum_cached / 1_000_000 * usage_factor, 6),
            "cache_creation_input": round(sum_creation / 1_000_000 * usage_factor, 6),
            "output": round(sum_output / 1_000_000 * usage_factor, 6),
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
