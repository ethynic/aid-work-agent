#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 调用客户端"""

import os
import time
from typing import Any, Dict, Optional

from loguru import logger

# ===== 临时调试日志（验证根因后删除）=====
_DEBUG_LOG_PATH = None


def _debug_log(message: str) -> None:
    """写入项目根 log/temp/travel-quote-llm.log，方便宿主机直接查看。

    子进程 cwd 在 /tmp/skill_executor/xxx，tlog 相对路径会落错位置，
    因此用 PROJECT_ROOT env 拼绝对路径，与主进程日志目录一致。
    """
    global _DEBUG_LOG_PATH
    try:
        if _DEBUG_LOG_PATH is None:
            root = os.environ.get("PROJECT_ROOT", os.getcwd())
            _DEBUG_LOG_PATH = os.path.join(root, "log", "temp", "travel-quote-llm.log")
        if not os.path.isdir(os.path.dirname(_DEBUG_LOG_PATH)):
            os.makedirs(os.path.dirname(_DEBUG_LOG_PATH), exist_ok=True)
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {message}\n"
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
# ===== 临时调试日志结束 =====


def get_effective_provider(provider_override: Optional[str] = None) -> str:
    """解析实际生效的 LLM 提供商：显式参数 > 技能子进程注入的 env（子智能体配置）> 全局默认。

    _llm_kwargs 等模块用它判断思考开关等 provider 相关行为，避免只看全局
    settings.llm.provider 而漏掉子智能体的 provider 覆盖（如全局 qwen + 子智能体 deepseek）。
    """
    from src.config.settings import settings
    env_provider = os.environ.get("SKILL_LLM_PROVIDER")
    provider = provider_override or env_provider or settings.llm.provider
    _debug_log(
        f"[get_effective_provider] override={provider_override or '-'} "
        f"env={env_provider or '-'} global={settings.llm.provider} -> effective={provider}"
    )
    return provider


def _extract_content(result: Dict[str, Any], task: str, provider: str, model: str) -> str:
    """提取 LLM 回答内容；空/纯空白 content 视为失败，抛出带上下文的清晰错误。

    推理模型（如 deepseek）在思考未被禁用或响应被中断时可能返回空白 content，
    直接返回空串会让下游 json.loads 报晦涩的 "Expecting value: line 1 column 1"，
    这里把错误提前到调用点，并带上 task/provider/model 便于定位。
    """
    content = (result["choices"][0]["message"].get("content") or "")
    if not content.strip():
        _debug_log(f"[call_llm] EMPTY_CONTENT task={task} provider={provider} model={model} reasoning_len={len(result['choices'][0]['message'].get('reasoning_content') or '')}")
        raise ValueError(f"LLM 返回空内容: task={task}, provider={provider}, model={model}")
    _debug_log(f"[call_llm] SUCCESS task={task} provider={provider} model={model} content_len={len(content)}")
    return content


def call_llm(prompt: str, *, timeout: float = 300.0,
             max_tokens: Optional[int] = None, task: str = "",
             model_override: Optional[str] = None,
             provider_override: Optional[str] = None,
             extra_body: Optional[Dict[str, Any]] = None) -> str:
    """调用 LLM（子进程安全，直接使用 SDK）"""
    from src.config.settings import settings
    # provider/model 覆盖优先级：显式参数 > 技能子进程注入的 env（子智能体配置）> 全局默认
    provider = get_effective_provider(provider_override)
    env_provider = os.environ.get("SKILL_LLM_PROVIDER")
    # env 注入的 provider/model 成对出现：仅当 provider 确实来自 env 时才应用 env model，
    # 避免显式覆盖 provider 后误用其它 provider 的模型
    if model_override is None and provider == env_provider:
        model_override = os.environ.get("SKILL_LLM_MODEL")
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
            content = _extract_content(result, task, provider, model)
            _log_llm_call(task, provider, model, prompt, started, True)
            return content
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
            content = _extract_content(result, task, provider, model)
            _log_llm_call(task, provider, model, prompt, started, True)
            return content
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
            content = _extract_content(result, task, provider, model)
            _log_llm_call(task, provider, model, prompt, started, True)
            return content
        else:
            raise ValueError(f"不支持的 LLM 提供商: {provider}")
    except Exception as exc:
        model = ""
        try:
            model = getattr(getattr(settings.llm, provider), 'model', None) or ""
        except Exception:
            model = ""
        _debug_log(f"[call_llm] FAILED task={task} provider={provider} model={model or '-'} err={exc!r}")
        _log_llm_call(task, provider, model, prompt, started, False)
        raise


def _log_llm_call(task: str, provider: str, model: str, prompt: str,
                  started: float, success: bool) -> None:
    duration = time.perf_counter() - started
    logger.info(
        f"[travel-quote][llm] task={task or '-'} provider={provider} model={model or '-'} "
        f"prompt_chars={len(prompt or '')} duration={duration:.2f}s success={str(success).lower()}"
    )
