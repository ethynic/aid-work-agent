#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
短信验证码 CLI 工具

提供两个子命令：
- send: 发送短信验证码到指定手机号
- verify: 校验用户输入的验证码

复用 src.db.models.send_sms_code / verify_sms_code 底层能力，复用 sms_codes 表
（TTL 15 分钟，演示模式固定 888888，qb_sms_code bypass）。

频控（脚本层）：
- 60s 内同一手机号只能发送 1 次（防秒刷）
- 24h 内同一手机号最多 10 次（防配额消耗）
- 走 src.core.redis_client.RedisClient，Redis 不可用降级到内存并记录 warning

用法:
    python sms_cli.py send --mobile 13800138000
    python sms_cli.py verify --mobile 13800138000 --code 123456
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# 添加项目根目录到路径（兼容 Windows 和 Linux）
script_path = Path(__file__).resolve()
if "src" in script_path.parts:
    src_index = script_path.parts.index("src")
    project_root = Path(*script_path.parts[:src_index])
else:
    project_root = script_path.parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger

from src.config.settings import settings
from src.core.redis_client import redis_client
from src.db.models import send_sms_code, verify_sms_code
from src.sms.manager import sms_manager


def _ensure_db_pool() -> None:
    """子进程中 PostgreSQL 连接池未继承父进程，需自动初始化

    skill_execute 通过 asyncio.create_subprocess_shell 启动全新 Python 进程，
    父进程已初始化的 _pg_connection_pool 不会继承，因此底层 get_db_connection
    会抛出 "PostgreSQL 连接池未初始化" 错误。其他 skill 脚本（after_sales_tool.py、
    order_tool.py 等）均采用相同的懒初始化模式。
    """
    from src.db.database import get_postgres_pool, init_postgres_pool

    if get_postgres_pool() is None:
        logger.info("[sms-verification] 子进程中 PostgreSQL 连接池未初始化，正在自动初始化")
        init_postgres_pool()


# ============== 常量 ==============

# 验证码有效期（秒），与 send_sms_code 中 timedelta(minutes=15) 对齐
CODE_TTL_SECONDS = 900

# 60 秒内同一手机号只能发送 1 次
SEND_INTERVAL_SECONDS = 60

# 24 小时内同一手机号最多发送次数
DAILY_LIMIT = 10
DAILY_WINDOW_SECONDS = 86400

# 手机号格式：11 位数字，1 开头
MOBILE_PATTERN = re.compile(r"^1\d{10}$")

# 验证码格式：6 位数字
CODE_PATTERN = re.compile(r"^\d{6}$")


# ============== 敏感信息脱敏 ==============

# 错误信息中需要脱敏的字段模式（密码、token、api_key、验证码）
SENSITIVE_PATTERNS = [
    r'password["\s:=]+\S+',
    r'api[_-]?key["\s:=]+\S+',
    r'token["\s:=]+\S+',
    r'code["\s:=]+\d{4,8}',
]


def sanitize_error_info(error_msg: str) -> str:
    """过滤错误信息中的敏感字段（密码、验证码、token 等）

    遵循 backend_dev.md 错误处理规范，确保异常堆栈中的敏感信息不会透传给用户。
    """
    if not error_msg:
        return ""
    for pattern in SENSITIVE_PATTERNS:
        error_msg = re.sub(
            pattern,
            lambda m: re.split(r'[":=\s]+', m.group(0))[0] + "=***",
            error_msg,
            flags=re.IGNORECASE,
        )
    return error_msg


# ============== 频控键 ==============

def _send_interval_key(mobile: str) -> str:
    """60s 频控锁键"""
    return redis_client.make_key("sms_skill:send", mobile)


def _send_daily_key(mobile: str) -> str:
    """24h 发送记录 sorted set 键"""
    return redis_client.make_key("sms_skill:send24h", mobile)


# ============== 频控检查 ==============

def _check_rate_limit(mobile: str) -> Optional[Dict[str, Any]]:
    """发送前频控检查。

    检查项：
    1. 24h 内发送次数是否已达上限
    2. 60s 内是否已发送过

    Returns:
        None 表示通过；Dict 表示拦截（含 error/reason）
    """
    now = time.time()

    # 24h 计数检查
    try:
        daily_key = _send_daily_key(mobile)
        # 清理 24h 窗口外的旧记录
        redis_client.zremrangebyscore(daily_key, 0, now - DAILY_WINDOW_SECONDS)
        count = redis_client.zcard(daily_key)
        if count >= DAILY_LIMIT:
            logger.warning(
                f"短信验证码 24h 上限拦截: mobile={mobile}, count={count}"
            )
            return {
                "success": False,
                "error": f"24 小时内发送次数已达上限（{DAILY_LIMIT} 次），请明日再试",
                "reason": "daily_limit_exceeded",
            }
    except Exception as e:
        # 频控异常不应阻断主流程，降级放行
        logger.warning(f"短信验证码 24h 频控检查异常（降级放行）: {e}")

    # 60s 间隔检查
    try:
        interval_key = _send_interval_key(mobile)
        if redis_client.exists(interval_key):
            ttl = redis_client.ttl(interval_key)
            retry_after = max(ttl, 1) if ttl and ttl > 0 else SEND_INTERVAL_SECONDS
            logger.info(
                f"短信验证码 60s 频控拦截: mobile={mobile}, ttl={ttl}"
            )
            return {
                "success": False,
                "error": f"发送过于频繁，请 {retry_after} 秒后重试",
                "reason": "interval_too_short",
                "retry_after_seconds": retry_after,
            }
    except Exception as e:
        logger.warning(f"短信验证码 60s 频控检查异常（降级放行）: {e}")

    return None


def _record_send(mobile: str) -> None:
    """发送成功后记录频控计数。

    - 设置 60s 锁
    - 在 24h sorted set 中追加本次时间戳，并刷新 TTL
    """
    now = time.time()

    # 60s 锁
    try:
        redis_client.set(
            _send_interval_key(mobile), 1, ex=SEND_INTERVAL_SECONDS
        )
    except Exception as e:
        logger.warning(f"短信验证码 60s 锁设置失败: {e}")

    # 24h sorted set 计数
    try:
        daily_key = _send_daily_key(mobile)
        redis_client.zadd(daily_key, {str(now): now})
        redis_client.expire(daily_key, DAILY_WINDOW_SECONDS)
    except Exception as e:
        logger.warning(f"短信验证码 24h 计数记录失败: {e}")


# ============== 核心逻辑 ==============

def _validate_mobile(mobile: str) -> bool:
    """校验手机号格式：11 位数字，1 开头"""
    return bool(MOBILE_PATTERN.match(mobile or ""))


def send_sms(
    mobile: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """发送短信验证码。

    流程：
    1. 校验手机号
    2. 检查短信通道可用性（非演示模式）
    3. 频控检查（60s + 24h 上限）
    4. 调用底层 send_sms_code（含演示模式 888888、qb_sms_code bypass、15 分钟 TTL）
    5. 成功后记录频控计数
    6. 返回 JSON（不含 code）

    Args:
        mobile: 手机号
        tenant_id: 租户 ID（仅用于日志追踪）
        user_id: 用户 ID（仅用于日志追踪）
        session_id: 会话 ID（仅用于日志追踪）

    Returns:
        {"success": True, "expires_in_seconds": 900} 或
        {"success": False, "error": ..., "debug": ...}
    """
    # 1. 校验手机号
    if not _validate_mobile(mobile):
        logger.warning(f"短信验证码发送失败：手机号格式错误 mobile={mobile}")
        return {
            "success": False,
            "error": "手机号格式错误，必须是 11 位数字",
            "debug": "invalid mobile format",
        }

    # 2. 检查短信通道可用性（演示模式跳过，底层 send_sms_code 会处理 888888）
    if not settings.demo.enabled:
        try:
            sender = sms_manager.get_sender()
            if sender is None or not sender.is_available():
                logger.error(f"短信通道未配置，无法发送验证码 mobile={mobile}")
                return {
                    "success": False,
                    "error": "短信通道未配置，请联系管理员",
                    "debug": "sms sender not available",
                }
        except Exception as e:
            logger.error(f"短信通道检查异常: {e}", exc_info=True)
            return {
                "success": False,
                "error": "短信通道检查失败",
                "debug": sanitize_error_info(str(e)),
            }

    # 3. 频控检查
    blocked = _check_rate_limit(mobile)
    if blocked:
        return blocked

    # 4. 调用底层发送
    _ensure_db_pool()
    try:
        ok = send_sms_code(mobile)
    except Exception as e:
        logger.error(
            f"短信验证码发送异常 mobile={mobile}: {e}", exc_info=True
        )
        return {
            "success": False,
            "error": "验证码发送失败，请稍后重试",
            "debug": sanitize_error_info(str(e)),
        }

    if not ok:
        logger.error(f"短信验证码发送失败 mobile={mobile}")
        return {
            "success": False,
            "error": "验证码发送失败，请稍后重试",
            "debug": "send_sms_code returned False",
        }

    # 5. 记录频控计数（仅成功后）
    _record_send(mobile)

    logger.info(
        f"短信验证码发送成功 mobile={mobile}, "
        f"tenant={tenant_id}, user={user_id}, session={session_id}"
    )
    return {
        "success": True,
        "expires_in_seconds": CODE_TTL_SECONDS,
    }


def verify_sms(
    mobile: str,
    code: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """校验短信验证码。

    流程：
    1. 校验手机号
    2. bypass 判断（qb_sms_code，优先于格式校验，因 bypass 码可能不是 6 位数字）
    3. 校验验证码格式（6 位数字）
    4. 调用底层 verify_sms_code
    5. 返回 JSON（reason: ok|invalid|bypass）

    Args:
        mobile: 手机号
        code: 用户输入的验证码
        tenant_id: 租户 ID（仅用于日志追踪）
        user_id: 用户 ID（仅用于日志追踪）
        session_id: 会话 ID（仅用于日志追踪）

    Returns:
        {"success": True/False, "reason": "ok"|"invalid"|"bypass"}
    """
    # 1. 校验手机号
    if not _validate_mobile(mobile):
        logger.warning(f"短信验证码校验失败：手机号格式错误 mobile={mobile}")
        return {
            "success": False,
            "reason": "invalid",
            "error": "手机号格式错误，必须是 11 位数字",
        }

    code_str = str(code) if code is not None else ""

    # 2. bypass 判断（优先，qb_sms_code 可能不是 6 位数字）
    qb_code = settings.sms.qb_sms_code
    if qb_code and code_str == qb_code:
        logger.info(f"短信验证码 bypass 验证 mobile={mobile}, code=***")
        return {"success": True, "reason": "bypass"}

    # 3. 校验验证码格式
    if not CODE_PATTERN.match(code_str):
        logger.warning(f"短信验证码校验失败：验证码格式错误 mobile={mobile}")
        return {
            "success": False,
            "reason": "invalid",
            "error": "验证码格式错误，必须是 6 位数字",
        }

    # 4. 调用底层验证
    _ensure_db_pool()
    try:
        ok = verify_sms_code(mobile, code_str)
    except Exception as e:
        logger.error(
            f"短信验证码校验异常 mobile={mobile}: {e}", exc_info=True
        )
        return {
            "success": False,
            "reason": "invalid",
            "error": "验证码校验失败，请稍后重试",
            "debug": sanitize_error_info(str(e)),
        }

    if ok:
        logger.info(
            f"短信验证码校验成功 mobile={mobile}, "
            f"tenant={tenant_id}, user={user_id}, session={session_id}"
        )
        return {"success": True, "reason": "ok"}

    logger.warning(
        f"短信验证码校验失败 mobile={mobile}（验证码错误/过期/已使用）"
    )
    return {
        "success": False,
        "reason": "invalid",
        "error": "验证码错误或已过期",
    }


# ============== CLI 入口 ==============

def main() -> None:
    """CLI 入口：解析参数并调用对应子命令"""
    parser = argparse.ArgumentParser(description="短信验证码 CLI 工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # send 子命令
    send_parser = subparsers.add_parser("send", help="发送短信验证码")
    send_parser.add_argument(
        "--mobile", required=True, help="手机号（11 位数字）"
    )
    send_parser.add_argument(
        "--tenant-id", help="租户 ID（可选，仅用于日志追踪）"
    )
    send_parser.add_argument(
        "--user-id", help="用户 ID（可选，仅用于日志追踪）"
    )
    send_parser.add_argument(
        "--session-id", help="会话 ID（可选，仅用于日志追踪）"
    )

    # verify 子命令
    verify_parser = subparsers.add_parser("verify", help="校验短信验证码")
    verify_parser.add_argument(
        "--mobile", required=True, help="手机号（11 位数字）"
    )
    verify_parser.add_argument(
        "--code", required=True, help="短信验证码（6 位数字）"
    )
    verify_parser.add_argument(
        "--tenant-id", help="租户 ID（可选，仅用于日志追踪）"
    )
    verify_parser.add_argument(
        "--user-id", help="用户 ID（可选，仅用于日志追踪）"
    )
    verify_parser.add_argument(
        "--session-id", help="会话 ID（可选，仅用于日志追踪）"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "send":
        result = send_sms(
            mobile=args.mobile,
            tenant_id=args.tenant_id,
            user_id=args.user_id,
            session_id=args.session_id,
        )
    elif args.command == "verify":
        result = verify_sms(
            mobile=args.mobile,
            code=args.code,
            tenant_id=args.tenant_id,
            user_id=args.user_id,
            session_id=args.session_id,
        )
    else:
        result = {"success": False, "error": f"未知命令: {args.command}"}

    # 输出 JSON（确保不含验证码）
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
