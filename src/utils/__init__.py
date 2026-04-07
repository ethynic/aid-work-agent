"""
错误信息工具函数

提供敏感信息过滤等通用错误处理功能。
"""

import re
from typing import List, Optional

SENSITIVE_PATTERNS = [
    r'password["\s:=]+\S+',
    r'passwd["\s:=]+\S+',
    r'secret["\s:=]+\S+',
    r'token["\s:=]+\S+',
    r'api[_-]?key["\s:=]+\S+',
    r'access[_-]?key["\s:=]+\S+',
    r'private[_-]?key["\s:=]+\S+',
    r'auth[_-]?token["\s:=]+\S+',
]


def sanitize_error_info(error_msg: str, additional_patterns: Optional[List[str]] = None) -> str:
    """
    过滤错误信息中的敏感信息

    Args:
        error_msg: 原始错误信息
        additional_patterns: 额外的敏感信息匹配模式

    Returns:
        过滤后的错误信息
    """
    if not error_msg:
        return error_msg

    patterns = SENSITIVE_PATTERNS + (additional_patterns or [])
    sanitized = error_msg

    for pattern in patterns:
        sanitized = re.sub(
            pattern,
            lambda m: m.group(0).split('=')[0] + '=***',
            sanitized,
            flags=re.IGNORECASE,
        )

    return sanitized
