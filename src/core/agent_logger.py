"""
Agent 会话日志记录器

记录 Agent 循环中每次 LLM 调用的迭代信息：
- iteration: 循环轮次
- request_id: LLM API 返回的请求ID（可用于关联 log/llm 中的原始调用日志）
- user_id: 用户ID
- session_id: 会话ID
- model: 模型名称
- provider: 提供者名称
- has_tool_calls: 是否包含工具调用
- tool_calls_count: 工具调用数量
- tool_names: 工具名称列表
- content_length: 回复内容长度
- usage: token 用量
- duration_ms: 本次迭代耗时
- timestamp: 时间戳

日志文件按天分割，命名格式：log/agent/agent_session_logs_20260325.jsonl
每行一条 JSON 记录，一条日志前后各空一行。
"""

import json
from datetime import datetime
from pathlib import Path


# 日志根目录
_LOG_DIR = Path("log/agent")


def _get_log_file() -> Path:
    """
    获取当天的日志文件路径。

    Returns:
        当天日志文件路径，格式：log/agent/agent_session_logs_20260325.jsonl
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    return _LOG_DIR / f"agent_session_logs_{date_str}.jsonl"


def _get_skill_execute_log_file() -> Path:
    """
    获取当天的 skill_execute 日志文件路径。

    Returns:
        当天日志文件路径，格式：log/agent/skill_execute_YYYYMMDD.jsonl
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    return _LOG_DIR / f"skill_execute_{date_str}.jsonl"


def log_agent_iteration(
    iteration: int,
    request_id: str = "",
    user_id: str = "",
    session_id: str = "",
    tenant_id: str = "",
    model: str = "",
    provider: str = "",
    has_tool_calls: bool = False,
    tool_calls_count: int = 0,
    tool_names: list | None = None,
    content_length: int = 0,
    usage: dict | None = None,
    duration_ms: float | None = None,
) -> None:
    """
    记录一次 Agent 迭代的 LLM 调用信息。
    """
    try:
        record = {
            "timestamp": datetime.now().isoformat(),
            "iteration": iteration,
            "request_id": request_id,
            "user_id": user_id,
            "session_id": session_id,
            "tenant_id": tenant_id,
            "model": model,
            "provider": provider,
            "has_tool_calls": has_tool_calls,
            "tool_calls_count": tool_calls_count,
            "tool_names": tool_names or [],
            "content_length": content_length,
            "usage": usage,
            "duration_ms": round(duration_ms, 2) if duration_ms else None,
        }

        log_file = _get_log_file()
        with open(log_file, "a", encoding="utf-8") as f:
            if f.tell() > 0:
                f.write("\n")
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    except Exception:
        # 日志记录失败不影响主流程
        pass


def log_skill_execute(
    skill_name: str,
    command: str,
    session_id: str = "",
    user_id: str = "",
    success: bool = False,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    error: str = "",
    duration: float = 0,
) -> None:
    """
    记录 skill_execute 的执行结果。

    Args:
        skill_name: 技能名称
        command: 执行的命令
        session_id: 会话ID
        user_id: 用户ID
        success: 是否成功
        exit_code: 退出码
        stdout: 标准输出（超出 2000 字符会截断）
        stderr: 标准错误（超出 2000 字符会截断）
        error: 错误信息
        duration: 执行耗时（秒）
    """
    try:
        record = {
            "timestamp": datetime.now().isoformat(),
            "skill_name": skill_name,
            "command": command[:500] if command else "",
            "session_id": session_id,
            "user_id": user_id,
            "success": success,
            "exit_code": exit_code,
            "stdout": stdout[:2000] if stdout else "",
            "stderr": stderr[:2000] if stderr else "",
            "error": error[:500] if error else "",
            "duration": round(duration, 3) if duration else 0,
        }

        log_file = _get_skill_execute_log_file()
        with open(log_file, "a", encoding="utf-8") as f:
            if f.tell() > 0:
                f.write("\n")
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    except Exception:
        # 日志记录失败不影响主流程
        pass
