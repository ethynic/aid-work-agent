#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 调用客户端"""

from loguru import logger


def call_llm(prompt: str) -> str:
    """调用 LLM（子进程安全，直接使用 SDK）"""
    from src.config.settings import settings
    provider = settings.llm.provider

    if provider == 'qwen':
        import dashscope
        keys = settings.llm.qwen.get_effective_keys()
        if not keys:
            raise ValueError("QWEN API key 未配置")
        dashscope.api_key = keys[0]
        model = getattr(settings.llm.qwen, 'model', None) or 'qwen-plus'
        resp = dashscope.Generation.call(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            result_format='message',
            temperature=0.0,
        )
        if resp.status_code == 200:
            return resp.output.choices[0].message.content
        raise RuntimeError(f"LLM 调用失败: {resp.message}")
    elif provider == 'zhipu':
        import httpx
        keys = settings.llm.zhipu.get_effective_keys()
        if not keys:
            raise ValueError("ZhipuAI API key 未配置")
        model = getattr(settings.llm.zhipu, 'model', None) or 'glm-4-flash'
        base_url = getattr(settings.llm.zhipu, 'base_url', None) or 'https://open.bigmodel.cn/api/paas/v4'
        api_url = f"{base_url.rstrip('/')}/chat/completions"
        resp = httpx.post(
            api_url,
            headers={"Authorization": f"Bearer {keys[0]}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.0},
            timeout=300.0,
        )
        resp.raise_for_status()
        result = resp.json()
        return result["choices"][0]["message"]["content"]
    elif provider == 'deepseek':
        import httpx
        keys = settings.llm.deepseek.get_effective_keys()
        if not keys:
            raise ValueError("DeepSeek API key 未配置")
        model = getattr(settings.llm.deepseek, 'model', None) or 'deepseek-chat'
        base_url = getattr(settings.llm.deepseek, 'base_url', None) or 'https://api.deepseek.com'
        api_url = f"{base_url.rstrip('/')}/chat/completions"
        resp = httpx.post(
            api_url,
            headers={"Authorization": f"Bearer {keys[0]}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.0},
            timeout=300.0,
        )
        resp.raise_for_status()
        result = resp.json()
        return result["choices"][0]["message"]["content"]
    else:
        raise ValueError(f"不支持的 LLM 提供商: {provider}")
