#!/usr/bin/env python3
"""摘要生成模型性价比实验

对 3 个模型（deepseek-v4-flash / qwen3.8-flash / GLM-5.3-Flash）× 思考开/关，
用与 KnowledgeBaseService.generate_summary 完全相同的提示词生成 5 篇知识库文档摘要，
记录真实 usage（含 cached_tokens）并按 token_cost_prices 单价计算成本。

与 chat_lite 的差异（有意为之）：
- max_tokens 统一 2048（生产 lite 通道为 500，思考开启时会被 reasoning 烧穿，无法公平对比）
- 直接构建 Provider 调用，不走 gateway（避免 failover/计费旁路干扰，纯实验不计费）

用法:
  python scripts/benchmark_summary_models.py --docs tmp/bench_docs.json --out tmp/bench_results.json
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 与 gateway._LITE_THINKING_OFF_PARAMS 同源的关思考参数（provider 级写法）
THINKING_OFF_PARAMS = {
    "deepseek": {"enable_thinking": False},   # provider 转 thinking={"type":"disabled"}
    "qwen": {"enable_thinking": False},
    "zhipu": {"reasoning_effort": "low"},     # GLM Flash 始终思考，low 档 reasoning 归零
}
THINKING_ON_PARAMS = {
    "deepseek": {"enable_thinking": True},
    "qwen": {"enable_thinking": True},
    "zhipu": {},  # GLM Flash 默认即思考开启
}

# 与 token_cost_prices 表一致（元 / 百万 token）
PRICES = {
    "deepseek-v4-flash": {"input": 2.0, "output": 8.0, "cached_input": 0.04},
    "qwen3.8-flash": {"input": 0.8, "output": 2.7, "cached_input": 0.16},
    "GLM-5.3-Flash": {"input": 0.8, "output": 2.8, "cached_input": 0.8},
}

MODELS = [
    ("deepseek", "deepseek-v4-flash"),
    ("qwen", "qwen3.8-flash"),
    ("zhipu", "GLM-5.3-Flash"),
]

MAX_TOKENS = 2048
TEMPERATURE = 0.3


def build_prompt(text: str, title: str, max_length: int = 300) -> str:
    """与 KnowledgeBaseService.generate_summary 完全一致的提示词"""
    truncated_text = text[:5000]
    if len(text) > 5000:
        truncated_text += "..."
    return f"""请为以下文档生成一个简洁的中文摘要，不超过 {max_length} 个字符。

文档标题: {title}

文档内容:
{truncated_text}

要求:
1. 准确概括文档的核心内容
2. 语言简洁通顺
3. 不要包含"摘要"、"本文"等字样
4. 直接输出摘要内容，不需要其他说明
"""


def build_provider(provider_name: str, model: str):
    from src.llm.gateway import _build_provider
    from src.config.settings import settings
    import os

    if provider_name == "qwen":
        cfg = settings.llm.qwen
    elif provider_name == "zhipu":
        cfg = settings.llm.zhipu
    elif provider_name == "deepseek":
        cfg = settings.llm.deepseek
    else:
        raise ValueError(provider_name)
    keys = cfg.get_effective_keys()
    if not keys:
        raise ValueError(f"provider {provider_name} 无 API Key")
    return _build_provider(provider_name, keys[0], model=model)


def calc_cost(model: str, usage: dict) -> float:
    p = PRICES[model]
    prompt = usage.get("prompt_tokens", 0) or 0
    completion = usage.get("completion_tokens", 0) or 0
    cached = usage.get("cached_tokens", 0) or 0
    return (
        (prompt - cached) / 1e6 * p["input"]
        + cached / 1e6 * p["cached_input"]
        + completion / 1e6 * p["output"]
    )


async def run_one(provider_name: str, model: str, thinking: str, doc: dict) -> dict:
    provider = build_provider(provider_name, model)
    params = (THINKING_OFF_PARAMS if thinking == "off" else THINKING_ON_PARAMS)[provider_name]
    prompt = build_prompt(doc["raw_text"], doc["title"])
    start = time.perf_counter()
    err = None
    try:
        resp = await provider.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            **params,
        )
    except Exception as e:
        resp = {}
        err = f"{type(e).__name__}: {e}"
    duration = time.perf_counter() - start
    usage = resp.get("usage") or {}
    return {
        "doc_id": doc["id"],
        "provider": provider_name,
        "model": model,
        "thinking": thinking,
        "duration_s": round(duration, 2),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "cached_tokens": usage.get("cached_tokens", 0) or 0,
        "finish_reason": resp.get("finish_reason"),
        "content_chars": len((resp.get("content") or "").strip()),
        "cost_yuan": round(calc_cost(model, usage), 8) if usage else None,
        "error": err,
    }


async def main_async(args) -> int:
    docs = json.loads(Path(args.docs).read_text(encoding="utf-8"))
    results = []
    for provider_name, model in MODELS:
        for thinking in ("off", "on"):
            for doc in docs:
                r = await run_one(provider_name, model, thinking, doc)
                results.append(r)
                status = f"err={r['error']}" if r["error"] else (
                    f"p={r['prompt_tokens']} c={r['completion_tokens']} "
                    f"cache={r['cached_tokens']} {r['duration_s']}s "
                    f"cost={r['cost_yuan']} fr={r['finish_reason']}"
                )
                print(f"[{model} / thinking-{thinking}] doc={doc['id']} {status}", flush=True)
                if args.out:
                    Path(args.out).write_text(
                        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                await asyncio.sleep(args.interval)
    return 0


def main():
    parser = argparse.ArgumentParser(description="摘要模型性价比实验")
    parser.add_argument("--docs", required=True, help="文档 JSON 文件（含 raw_text/title）")
    parser.add_argument("--out", default="tmp/bench_results.json", help="结果输出 JSON")
    parser.add_argument("--interval", type=float, default=1.0, help="每次调用间隔秒")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
