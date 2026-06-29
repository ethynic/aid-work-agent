"""
日志保留期管理

统一清理 log/ 目录下超过保留期的日志文件。

保留期：15 天

清理范围（按文件名中的日期判定）：
- log/agent/aid-work-agent_YYYYMMDD.log          （loguru 主日志，新分片格式）
- log/agent/error_YYYYMMDD.log                   （loguru 错误日志，新分片格式）
- log/agent/agent_session_logs_YYYYMMDD.jsonl    （Agent 会话日志）
- log/agent/skill_execute_YYYYMMDD.jsonl         （技能执行日志）
- log/llm/llm_invoke_logs_YYYYMMDD.jsonl         （LLM 调用日志）
- 兼容旧 loguru rotation 文件：aid-work-agent.log.YYYY-MM-DD_*、error.log.YYYY-MM-DD_*

多 worker 安全：删除操作幂等，多 worker 同时清理最多重复扫描，无数据风险。
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

# 日志保留期（天）
RETENTION_DAYS = 15

# 日志根目录
_LOG_ROOT = Path("log")

# 按日期命名的日志文件模式：xxx_YYYYMMDD.log / xxx_YYYYMMDD.jsonl
_DATED_FILENAME_PATTERN = re.compile(r"_(\d{8})\.(log|jsonl)$")

# 旧 loguru rotation 文件模式：xxx.log.YYYY-MM-DD_HH-MM-SS
_OLD_ROTATION_PATTERN = re.compile(r"\.log\.(\d{4}-\d{2}-\d{2})")


def _extract_file_date(filename: str) -> datetime | None:
    """
    从文件名中提取日期。

    支持两种格式：
    - xxx_YYYYMMDD.log / xxx_YYYYMMDD.jsonl  → 解析 YYYYMMDD
    - xxx.log.YYYY-MM-DD_HH-MM-SS            → 解析 YYYY-MM-DD（旧 loguru rotation）
    """
    m = _DATED_FILENAME_PATTERN.search(filename)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            return None

    m = _OLD_ROTATION_PATTERN.search(filename)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            return None

    return None


def cleanup_old_logs(retention_days: int = RETENTION_DAYS) -> int:
    """
    清理超过保留期的日志文件。

    扫描 log/ 下所有文件，按文件名中的日期判定是否过期。
    文件名中无日期模式的文件（如 tlog 临时调试日志、aid-work-agent.log 当前文件）不会被清理。

    Args:
        retention_days: 保留天数，默认 15 天

    Returns:
        删除的文件数量
    """
    deleted_count = 0
    cutoff_date = datetime.now() - timedelta(days=retention_days)

    if not _LOG_ROOT.exists():
        return deleted_count

    for file_path in _LOG_ROOT.rglob("*"):
        if not file_path.is_file():
            continue

        file_date = _extract_file_date(file_path.name)
        if file_date is None:
            continue

        if file_date < cutoff_date:
            try:
                file_path.unlink()
                deleted_count += 1
                logger.info(f"清理过期日志: {file_path}")
            except Exception as e:
                logger.warning(f"清理日志失败 {file_path}: {e}")

    return deleted_count
