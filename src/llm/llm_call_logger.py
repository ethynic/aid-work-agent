"""
LLM Provider 调用日志记录器

在 Provider 层记录每次 LLM API 调用的完整信息：
- request_id（API 返回或自动生成的调用ID）
- 时间戳
- 请求参数（完整 messages、tools、temperature、max_tokens 等）
- 响应内容（完整原始响应）
- 错误信息（如有）
- Token 用量

日志文件按天分割，命名格式：log/llm/llm_invoke_logs_20260325.jsonl
每行一条 JSON 记录，一天一个文件。
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


# 日志根目录
_LOG_DIR = Path("log/llm")


def _get_log_file() -> Path:
    """
    获取当天的日志文件路径。

    Returns:
        当天日志文件路径，格式：log/llm/llm_invoke_logs_20260325.jsonl
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    return _LOG_DIR / f"llm_invoke_logs_{date_str}.jsonl"


def log_llm_invoke(
    request_id: str,
    provider: str,
    model: str,
    request_params: Dict[str, Any],
    response_data: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[float] = None,
) -> None:
    """
    记录一次 LLM API 调用。

    Args:
        request_id: 请求ID（API 返回的或自动生成的）
        provider: 提供者名称（qwen / zhipu）
        model: 模型名称
        request_params: 完整的请求参数
        response_data: 完整的原始响应（不做截断）
        error: 错误信息（如有）
        usage: token 用量
        duration_ms: 调用耗时（毫秒）
    """
    try:
        record = {
            "request_id": request_id,
            "timestamp": datetime.now().isoformat(),
            "provider": provider,
            "model": model,
            "request": request_params,
            "response": response_data,
            "error": error,
            "usage": usage,
            "duration_ms": duration_ms,
        }

        log_file = _get_log_file()
        with open(log_file, "a", encoding="utf-8") as f:
            if f.tell() > 0:
                f.write("\n")
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    except Exception:
        # 日志记录失败不应影响主流程，静默忽略
        pass


def generate_request_id() -> str:
    """生成一个唯一的请求ID。"""
    return uuid.uuid4().hex[:16]
