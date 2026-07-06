"""
登录速率限制

基于 IP / 手机号的滑动窗口速率限制。
支持通过环境变量 RATE_LIMIT_LOGIN 配置，默认 5 次/分钟。
优先使用 Redis Sorted Set 实现跨 worker 统一的滑动窗口，Redis 不可用时降级到内存。
"""

import os
import time
from collections import defaultdict
from threading import Lock

from loguru import logger

from src.core.redis_client import redis_client

# 默认配置：每个 key 在窗口内允许的最大请求数
DEFAULT_LOGIN_LIMIT = 5
# 窗口大小（秒）
WINDOW_SECONDS = 60


class SlidingWindowRateLimiter:
    """滑动窗口速率限制器（线程安全，支持 Redis 跨 worker 共享）"""

    def __init__(self, max_requests: int = DEFAULT_LOGIN_LIMIT, window_seconds: int = WINDOW_SECONDS):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._local_requests: dict[str, list[float]] = defaultdict(list)
        self._local_lock = Lock()

    def _redis_key(self, key: str) -> str:
        return redis_client.make_key("rate_limit:login", key)

    def is_allowed(self, key: str) -> bool:
        """检查 key 是否在限制内允许请求"""
        now = time.time()
        cutoff = now - self.window_seconds
        window_key = self._redis_key(key)

        # 优先尝试 Redis 跨 worker 计数
        try:
            redis_client.zremrangebyscore(window_key, 0, cutoff)
            count = redis_client.zcard(window_key)
            if count >= self.max_requests:
                return False
            redis_client.zadd(window_key, {str(now): now})
            redis_client.expire(window_key, self.window_seconds)
            return True
        except Exception as e:
            logger.warning(f"Redis 速率限制失败，降级到内存: {e}")

        # 降级到内存滑动窗口（单 worker 内仍有效）
        with self._local_lock:
            self._local_requests[key] = [t for t in self._local_requests[key] if t > cutoff]
            if len(self._local_requests[key]) >= self.max_requests:
                return False
            self._local_requests[key].append(now)
            return True

    def get_remaining(self, key: str) -> int:
        """获取 key 剩余可用次数"""
        now = time.time()
        cutoff = now - self.window_seconds
        window_key = self._redis_key(key)

        try:
            redis_client.zremrangebyscore(window_key, 0, cutoff)
            count = redis_client.zcard(window_key)
            return max(0, self.max_requests - count)
        except Exception as e:
            logger.warning(f"Redis 速率限制查询失败，降级到内存: {e}")

        with self._local_lock:
            self._local_requests[key] = [t for t in self._local_requests[key] if t > cutoff]
            return max(0, self.max_requests - len(self._local_requests[key]))


def _get_login_limit() -> int:
    """从环境变量获取登录速率限制，默认 5 次/分钟"""
    try:
        val = os.getenv("RATE_LIMIT_LOGIN", "")
        if val:
            return int(val)
    except ValueError:
        logger.warning(f"Invalid RATE_LIMIT_LOGIN value: {val}, using default {DEFAULT_LOGIN_LIMIT}")
    return DEFAULT_LOGIN_LIMIT


# 全局登录速率限制器实例
login_rate_limiter = SlidingWindowRateLimiter(max_requests=_get_login_limit())


def check_login_rate_limit(key: str) -> tuple[bool, str]:
    """
    检查登录速率限制

    Args:
        key: 限制维度（IP 地址或手机号）

    Returns:
        (allowed, message) 元组。allowed=True 表示允许，False 表示被限制
    """
    if login_rate_limiter.is_allowed(key):
        return True, ""
    remaining = login_rate_limiter.get_remaining(key)
    return False, f"登录尝试过于频繁，请稍后再试（限制：{_get_login_limit()}次/分钟）"
