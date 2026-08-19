#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 调用客户端"""

import time
from typing import Any, Dict, Optional

from loguru import logger


def call_llm(prompt: str, *, timeout: float = 300.0,
             max_tokens: Optional[int] = None, task: str = "",
             model_override: Optional[str] = None,
             extra_body: Optional[Dict[str, Any]] = None) -> str:
    """调用 LLM（子进程安全，直接使用 SDK）"""
    from src.config.settings import settings
    provider = settings.llm.provider
    started = time.perf_counter()

    try:
        if provider == 'qwen':
            import httpx
            keys = settings.llm.qwen.get_effective_keys()
            if not keys:
                raise ValueError("QWEN API key 未配置")
            model = model_override or getattr(settings.llm.qwen, 'model', None) or 'qwen-plus'
            base_url = getattr(settings.llm.qwen, 'base_url', None) or 'https://dashscope.aliyuncs.com/compatible-mode/v1'
            api_url = f"{base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
            }
            # qwen 推理模型关闭思考（enable_thinking），与主链路 QwenProvider 行为一致。
            # 不透传 extra_body：调用方传的是 deepseek 格式 thinking 参数，对 qwen 兼容接口无效，可能触发 400
            if settings.llm.enable_thinking is not None:
                payload["enable_thinking"] = settings.llm.enable_thinking
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            resp = httpx.post(
                api_url,
                headers={"Authorization": f"Bearer {keys[0]}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            result = resp.json()
            _log_llm_call(task, provider, model, prompt, started, True)
            return result["choices"][0]["message"]["content"]
        elif provider == 'zhipu':
            import httpx
            keys = settings.llm.zhipu.get_effective_keys()
            if not keys:
                raise ValueError("ZhipuAI API key 未配置")
            model = model_override or getattr(settings.llm.zhipu, 'model', None) or 'glm-4-flash'
            base_url = getattr(settings.llm.zhipu, 'base_url', None) or 'https://open.bigmodel.cn/api/paas/v4'
            api_url = f"{base_url.rstrip('/')}/chat/completions"
            payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.0}
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            if extra_body:
                payload.update(extra_body)
            resp = httpx.post(
                api_url,
                headers={"Authorization": f"Bearer {keys[0]}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            result = resp.json()
            _log_llm_call(task, provider, model, prompt, started, True)
            return result["choices"][0]["message"]["content"]
        elif provider == 'deepseek':
            import httpx
            keys = settings.llm.deepseek.get_effective_keys()
            if not keys:
                raise ValueError("DeepSeek API key 未配置")
            model = model_override or getattr(settings.llm.deepseek, 'model', None) or 'deepseek-chat'
            base_url = getattr(settings.llm.deepseek, 'base_url', None) or 'https://api.deepseek.com'
            api_url = f"{base_url.rstrip('/')}/chat/completions"
            payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.0}
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            if extra_body:
                payload.update(extra_body)
            resp = httpx.post(
                api_url,
                headers={"Authorization": f"Bearer {keys[0]}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            result = resp.json()
            _log_llm_call(task, provider, model, prompt, started, True)
            return result["choices"][0]["message"]["content"]
        else:
            raise ValueError(f"不支持的 LLM 提供商: {provider}")
    except Exception:
        model = ""
        try:
            model = getattr(getattr(settings.llm, provider), 'model', None) or ""
        except Exception:
            model = ""
        _log_llm_call(task, provider, model, prompt, started, False)
        raise


def _log_llm_call(task: str, provider: str, model: str, prompt: str,
                  started: float, success: bool) -> None:
    duration = time.perf_counter() - started
    logger.info(
        f"[travel-quote][llm] task={task or '-'} provider={provider} model={model or '-'} "
        f"prompt_chars={len(prompt or '')} duration={duration:.2f}s success={str(success).lower()}"
    )
