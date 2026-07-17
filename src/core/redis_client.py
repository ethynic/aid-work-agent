#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Redis Client - 统一的 Redis 连接和操作封装

特性：
1. 连接池管理
2. JSON 序列化/反序列化
3. TTL 管理
4. 连接失败时降级到内存存储，记录 warning
"""

import fnmatch
import json
import threading
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from src.config.settings import settings


class _InMemoryFallback:
    """当 Redis 不可用时的内存降级存储"""

    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._types: Dict[str, str] = {}  # 键 → Redis 类型标记：string/hash/list/set/zset
        self._ttls: Dict[str, float] = {}  # 键 → 过期时间戳（秒），不存在表示永不过期
        self._lock = threading.Lock()

    def _make_key(self, key: str) -> str:
        return key

    def _is_expired(self, key: str) -> bool:
        """检查键是否已过期（过期则清理并返回 True）。调用方需持锁。"""
        import time
        exp = self._ttls.get(key)
        if exp is None:
            return False
        if time.time() >= exp:
            self._data.pop(key, None)
            self._types.pop(key, None)
            self._ttls.pop(key, None)
            return True
        return False

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if self._is_expired(key):
                return None
            return self._data.get(key)

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        with self._lock:
            # ex<=0 等价于立即删除（Redis 语义）
            if ex is not None and ex <= 0:
                self._data.pop(key, None)
                self._types.pop(key, None)
                self._ttls.pop(key, None)
                return
            self._data[key] = value
            self._types[key] = "string"
            if ex is not None:
                import time
                self._ttls[key] = time.time() + ex
            else:
                self._ttls.pop(key, None)

    def delete(self, key: str) -> int:
        with self._lock:
            existed = key in self._data
            self._data.pop(key, None)
            self._types.pop(key, None)
            self._ttls.pop(key, None)
            return 1 if existed else 0

    def hset(self, key: str, field: str, value: Any) -> None:
        with self._lock:
            if key not in self._data or self._types.get(key) != "hash":
                self._data[key] = {}
                self._types[key] = "hash"
            self._data[key][field] = value

    def hget(self, key: str, field: str) -> Optional[Any]:
        with self._lock:
            if self._is_expired(key):
                return None
            return self._data.get(key, {}).get(field)

    def hgetall(self, key: str) -> Dict[str, Any]:
        with self._lock:
            if self._is_expired(key):
                return {}
            return dict(self._data.get(key, {}))

    def hdel(self, key: str, field: str) -> int:
        with self._lock:
            if key in self._data and field in self._data[key]:
                del self._data[key][field]
                return 1
            return 0

    def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        """Hash field 原子自增（v3.2.1 P1-4，CompressionMetrics 使用）。

        内存降级路径：用锁保证线程安全，但跨 worker 不一致（无共享存储）。
        """
        with self._lock:
            bucket = self._data.setdefault(key, {})
            try:
                current = int(bucket.get(field, 0))
            except (TypeError, ValueError):
                current = 0
            new_val = current + int(amount)
            bucket[field] = new_val
            return new_val

    def sadd(self, key: str, member: str) -> int:
        with self._lock:
            if key not in self._data or self._types.get(key) != "set":
                self._data[key] = set()
                self._types[key] = "set"
            self._data[key].add(member)
            return 1

    def sismember(self, key: str, member: str) -> bool:
        with self._lock:
            if self._is_expired(key):
                return False
            return member in self._data.get(key, set())

    def srem(self, key: str, member: str) -> int:
        with self._lock:
            if key in self._data and member in self._data[key]:
                self._data[key].discard(member)
                return 1
            return 0

    def exists(self, key: str) -> bool:
        with self._lock:
            if self._is_expired(key):
                return False
            return key in self._data

    def expire(self, key: str, seconds: int) -> bool:
        with self._lock:
            if key not in self._data:
                return False
            import time
            if seconds > 0:
                self._ttls[key] = time.time() + seconds
            else:
                # seconds <= 0 等价于立即删除
                self._data.pop(key, None)
                self._types.pop(key, None)
                self._ttls.pop(key, None)
            return True

    def keys(self, pattern: str) -> List[str]:
        with self._lock:
            # 先清理过期键
            expired = [k for k in list(self._data.keys()) if self._is_expired(k)]
            # 简单匹配：支持 * 通配符（fnmatch）
            return [k for k in self._data.keys() if fnmatch.fnmatch(k, pattern)]

    # ============== Sorted Set 降级操作 ==============

    def zadd(self, key: str, mapping: dict) -> int:
        with self._lock:
            if key not in self._data or self._types.get(key) != "zset":
                self._data[key] = []
                self._types[key] = "zset"
            items = self._data[key]
            for member, score in mapping.items():
                items = [(m, s) for m, s in items if m != member]
                items.append((member, float(score)))
            self._data[key] = items
            return len(mapping)

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        with self._lock:
            if key not in self._data:
                return 0
            original_len = len(self._data[key])
            self._data[key] = [
                (m, s) for m, s in self._data[key]
                if not (float(min_score) <= s <= float(max_score))
            ]
            return original_len - len(self._data[key])

    def zcard(self, key: str) -> int:
        with self._lock:
            return len(self._data.get(key, []))

    def zrange(self, key: str, start: int, end: int) -> List[str]:
        with self._lock:
            items = self._data.get(key, [])
            sorted_items = sorted(items, key=lambda x: x[1])
            if end == -1:
                selected = sorted_items[start:]
            else:
                selected = sorted_items[start:end + 1]
            return [m for m, s in selected]

    def publish(self, channel: str, message: str) -> int:
        # 内存降级不支持 pub/sub，静默忽略
        return 0

    # ============== 管理页扩展方法 ==============

    def scan(self, cursor: int, match: str, count: int = 100) -> Tuple[int, List[str]]:
        """游标式扫描键（内存降级版：一次返回全部匹配结果，cursor 立即归零）"""
        with self._lock:
            # 清理过期键
            for k in list(self._data.keys()):
                self._is_expired(k)
            matched = [k for k in self._data.keys() if fnmatch.fnmatch(k, match)]
            return 0, matched

    def type(self, key: str) -> str:
        """获取键的 Redis 类型"""
        with self._lock:
            if self._is_expired(key):
                return "none"
            if key not in self._data:
                return "none"
            return self._types.get(key, "string")

    def ttl(self, key: str) -> int:
        """获取 TTL（秒）：-1=永不过期，-2=键不存在"""
        import time
        with self._lock:
            if self._is_expired(key):
                return -2
            if key not in self._data:
                return -2
            exp = self._ttls.get(key)
            if exp is None:
                return -1
            remaining = int(exp - time.time())
            return max(remaining, 1)

    def hkeys(self, key: str) -> List[str]:
        """获取 Hash 的所有 field 名"""
        with self._lock:
            if self._is_expired(key):
                return []
            bucket = self._data.get(key, {})
            if not isinstance(bucket, dict):
                return []
            return list(bucket.keys())

    def llen(self, key: str) -> int:
        """获取 List 长度"""
        with self._lock:
            if self._is_expired(key):
                return 0
            bucket = self._data.get(key, [])
            if not isinstance(bucket, list):
                return 0
            return len(bucket)

    def lrange(self, key: str, start: int, end: int) -> List[Any]:
        """获取 List 范围元素"""
        with self._lock:
            if self._is_expired(key):
                return []
            bucket = self._data.get(key, [])
            if not isinstance(bucket, list):
                return []
            if end == -1:
                return list(bucket[start:])
            return list(bucket[start:end + 1])

    def smembers(self, key: str) -> List[str]:
        """获取 Set 所有成员"""
        with self._lock:
            if self._is_expired(key):
                return []
            bucket = self._data.get(key, set())
            if not isinstance(bucket, set):
                return []
            return list(bucket)

    def memory_usage(self, key: str) -> Optional[int]:
        """获取单键内存占用（字节）。内存降级无真实值，返回 None"""
        return None

    def dbsize(self) -> int:
        """当前 db 键总数"""
        with self._lock:
            # 清理过期键后再统计
            for k in list(self._data.keys()):
                self._is_expired(k)
            return len(self._data)

    def acquire_lock(self, key: str, value: str, ex: int = 60) -> bool:
        with self._lock:
            if key in self._data:
                return False
            self._data[key] = value
            return True

    def release_lock(self, key: str, value: str) -> bool:
        with self._lock:
            if self._data.get(key) == value:
                del self._data[key]
                return True
            return False

class RedisClient:
    """
    统一的 Redis 客户端封装

    提供 JSON 序列化、TTL 管理和连接失败降级。
    """

    def __init__(self):
        self._client = None
        self._fallback = _InMemoryFallback()
        self._connected = False
        self._key_prefix = ""
        self._lock = threading.Lock()
        self._connect()

    def _connect(self) -> None:
        """尝试连接 Redis，失败时使用内存降级"""
        try:
            redis_cfg = getattr(settings, 'redis', None)
            if redis_cfg is None:
                logger.warning("[Redis] 配置中缺少 redis 节，使用内存降级")
                self._connected = False
                return

            enabled = getattr(redis_cfg, 'enabled', False)
            if not enabled:
                logger.info("[Redis] 已禁用，使用内存降级")
                self._connected = False
                return

            host = getattr(redis_cfg, 'host', 'localhost')
            port = int(getattr(redis_cfg, 'port', 6379))
            password = getattr(redis_cfg, 'password', None) or None
            db = int(getattr(redis_cfg, 'db', 0))
            ssl = getattr(redis_cfg, 'ssl', False)
            self._key_prefix = getattr(redis_cfg, 'key_prefix', '') or ''

            import redis as redis_lib
            self._client = redis_lib.Redis(
                host=host,
                port=port,
                password=password,
                db=db,
                ssl=ssl,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                health_check_interval=30,
                protocol=2,  # 强制 RESP2 协议，兼容 Redis 5.x，避免 HELLO 命令
            )
            # 测试连接
            self._client.ping()
            self._connected = True
            logger.info(f"[Redis] 连接成功: {host}:{port}/{db}")
        except Exception as e:
            logger.warning(f"[Redis] 连接失败，降级到内存存储: {e}")
            self._connected = False
            self._client = None

    def _ensure_connection(self) -> bool:
        """确保连接可用，尝试重连"""
        if self._connected and self._client:
            try:
                self._client.ping()
                return True
            except Exception:
                self._connected = False
                self._client = None

        if not self._connected:
            self._connect()

        return self._connected

    def is_available(self) -> bool:
        """返回真实 Redis 是否可用；安全关键分布式状态不得把内存降级视为可用。"""
        return self._ensure_connection()

    def _get_backend(self):
        """获取实际后端（Redis 或降级内存）"""
        if self._ensure_connection():
            return self._client
        return self._fallback

    # ============== String 操作 ==============

    def get(self, key: str) -> Optional[Any]:
        """获取值并反序列化 JSON"""
        backend = self._get_backend()
        try:
            raw = backend.get(key)
            if raw is None:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8')
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"[Redis] get 失败 [{key}]: {e}")
            return None

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        """设置值并序列化 JSON"""
        backend = self._get_backend()
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
            backend.set(key, raw, ex=ex)
        except Exception as e:
            logger.warning(f"[Redis] set 失败 [{key}]: {e}")

    def delete(self, key: str) -> bool:
        """删除键"""
        backend = self._get_backend()
        try:
            result = backend.delete(key)
            return bool(result)
        except Exception as e:
            logger.warning(f"[Redis] delete 失败 [{key}]: {e}")
            return False

    # ============== Hash 操作 ==============

    def hset(self, key: str, field: str, value: Any) -> None:
        """设置 Hash 字段（JSON 序列化）"""
        backend = self._get_backend()
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
            backend.hset(key, field, raw)
        except Exception as e:
            logger.warning(f"[Redis] hset 失败 [{key}:{field}]: {e}")

    def hget(self, key: str, field: str) -> Optional[Any]:
        """获取 Hash 字段（JSON 反序列化）"""
        backend = self._get_backend()
        try:
            raw = backend.hget(key, field)
            if raw is None:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8')
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"[Redis] hget 失败 [{key}:{field}]: {e}")
            return None

    def hgetall(self, key: str) -> Dict[str, Any]:
        """获取完整 Hash（所有字段 JSON 反序列化）"""
        backend = self._get_backend()
        try:
            raw_dict = backend.hgetall(key)
            result = {}
            for field, raw in raw_dict.items():
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8')
                try:
                    result[field] = json.loads(raw)
                except json.JSONDecodeError:
                    result[field] = raw
            return result
        except Exception as e:
            logger.warning(f"[Redis] hgetall 失败 [{key}]: {e}")
            return {}

    def hdel(self, key: str, field: str) -> bool:
        """删除 Hash 字段"""
        backend = self._get_backend()
        try:
            result = backend.hdel(key, field)
            return bool(result)
        except Exception as e:
            logger.warning(f"[Redis] hdel 失败 [{key}:{field}]: {e}")
            return False

    def hexists(self, key: str, field: str) -> bool:
        """检查 Hash 字段是否存在"""
        backend = self._get_backend()
        try:
            return bool(backend.hexists(key, field))
        except Exception as e:
            logger.warning(f"[Redis] hexists 失败 [{key}:{field}]: {e}")
            return False

    def hincrby(self, key: str, field: str, amount: int = 1) -> Optional[int]:
        """Hash field 原子自增（v3.2.1 P1-4，CompressionMetrics 使用）。

        Redis 后端：底层 redis-py 的 HINCRBY 在 Redis 端原子执行，多 worker 完全一致。
        内存降级：通过 _InMemoryFallback 内部锁保证单进程线程安全（跨 worker 不一致）。

        Returns:
            自增后的新值；Redis 异常时返回 None（调用方自行降级）。
        """
        backend = self._get_backend()
        try:
            return backend.hincrby(key, field, amount)
        except Exception as e:
            logger.warning(f"[Redis] hincrby 失败 [{key}:{field}]: {e}")
            return None

    # ============== Set 操作 ==============

    def sadd(self, key: str, member: str) -> bool:
        """添加到 Set"""
        backend = self._get_backend()
        try:
            backend.sadd(key, member)
            return True
        except Exception as e:
            logger.warning(f"[Redis] sadd 失败 [{key}:{member}]: {e}")
            return False

    def sismember(self, key: str, member: str) -> bool:
        """检查是否在 Set 中"""
        backend = self._get_backend()
        try:
            return bool(backend.sismember(key, member))
        except Exception as e:
            logger.warning(f"[Redis] sismember 失败 [{key}:{member}]: {e}")
            return False

    def srem(self, key: str, member: str) -> bool:
        """从 Set 移除"""
        backend = self._get_backend()
        try:
            return bool(backend.srem(key, member))
        except Exception as e:
            logger.warning(f"[Redis] srem 失败 [{key}:{member}]: {e}")
            return False

    # ============== TTL 操作 ==============

    def expire(self, key: str, seconds: int) -> bool:
        """设置 TTL"""
        backend = self._get_backend()
        try:
            return bool(backend.expire(key, seconds))
        except Exception as e:
            logger.warning(f"[Redis] expire 失败 [{key}]: {e}")
            return False

    # ============== 其他操作 ==============

    def exists(self, key: str) -> bool:
        """检查键是否存在"""
        backend = self._get_backend()
        try:
            return bool(backend.exists(key))
        except Exception as e:
            logger.warning(f"[Redis] exists 失败 [{key}]: {e}")
            return False

    def keys(self, pattern: str) -> List[str]:
        """匹配键列表"""
        backend = self._get_backend()
        try:
            result = backend.keys(pattern)
            if isinstance(result, list):
                return [k.decode('utf-8') if isinstance(k, bytes) else k for k in result]
            return []
        except Exception as e:
            logger.warning(f"[Redis] keys 失败 [{pattern}]: {e}")
            return []

    # ============== 管理页扩展方法（Phase 1） ==============

    def scan(self, cursor: int, match: str, count: int = 100) -> Tuple[int, List[str]]:
        """游标式扫描键（生产安全，替代 KEYS）

        Returns:
            (next_cursor, keys) —— next_cursor=0 表示遍历结束
        """
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                next_cursor, batch = backend.scan(cursor=cursor, match=match, count=count)
                decoded = [k.decode('utf-8') if isinstance(k, bytes) else k for k in batch]
                return int(next_cursor), decoded
            return self._fallback.scan(cursor, match, count)
        except Exception as e:
            logger.warning(f"[Redis] scan 失败 [cursor={cursor} match={match}]: {e}")
            return 0, []

    def type(self, key: str) -> str:
        """获取键的 Redis 类型：string/hash/list/set/zset/none"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                result = backend.type(key)
                if isinstance(result, bytes):
                    result = result.decode('utf-8')
                return result
            return self._fallback.type(key)
        except Exception as e:
            logger.warning(f"[Redis] type 失败 [{key}]: {e}")
            return "none"

    def ttl(self, key: str) -> int:
        """获取 TTL（秒）：-1=永不过期，-2=键不存在"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                return int(backend.ttl(key))
            return self._fallback.ttl(key)
        except Exception as e:
            logger.warning(f"[Redis] ttl 失败 [{key}]: {e}")
            return -2

    def hkeys(self, key: str) -> List[str]:
        """获取 Hash 的所有 field 名"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                result = backend.hkeys(key)
                return [k.decode('utf-8') if isinstance(k, bytes) else k for k in result]
            return self._fallback.hkeys(key)
        except Exception as e:
            logger.warning(f"[Redis] hkeys 失败 [{key}]: {e}")
            return []

    def llen(self, key: str) -> int:
        """获取 List 长度"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                return int(backend.llen(key))
            return self._fallback.llen(key)
        except Exception as e:
            logger.warning(f"[Redis] llen 失败 [{key}]: {e}")
            return 0

    def lrange(self, key: str, start: int, end: int) -> List[Any]:
        """获取 List 范围元素（JSON 反序列化）"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                raw_list = backend.lrange(key, start, end)
                result = []
                for raw in raw_list:
                    if isinstance(raw, bytes):
                        raw = raw.decode('utf-8')
                    try:
                        result.append(json.loads(raw))
                    except (json.JSONDecodeError, TypeError):
                        result.append(raw)
                return result
            return self._fallback.lrange(key, start, end)
        except Exception as e:
            logger.warning(f"[Redis] lrange 失败 [{key}]: {e}")
            return []

    def smembers(self, key: str) -> List[Any]:
        """获取 Set 所有成员（JSON 反序列化）"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                raw_set = backend.smembers(key)
                result = []
                for raw in raw_set:
                    if isinstance(raw, bytes):
                        raw = raw.decode('utf-8')
                    try:
                        result.append(json.loads(raw))
                    except (json.JSONDecodeError, TypeError):
                        result.append(raw)
                return result
            return self._fallback.smembers(key)
        except Exception as e:
            logger.warning(f"[Redis] smembers 失败 [{key}]: {e}")
            return []

    def memory_usage(self, key: str) -> Optional[int]:
        """获取单键内存占用（字节），Redis 4.0+。降级模式返回 None"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                return backend.memory_usage(key)
            return self._fallback.memory_usage(key)
        except Exception as e:
            logger.warning(f"[Redis] memory_usage 失败 [{key}]: {e}")
            return None

    def dbsize(self) -> int:
        """当前 db 键总数"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                return int(backend.dbsize())
            return self._fallback.dbsize()
        except Exception as e:
            logger.warning(f"[Redis] dbsize 失败: {e}")
            return 0

    @property
    def connected(self) -> bool:
        """Redis 是否已连接（用于概览页展示降级状态）"""
        return self._connected

    # ============== Sorted Set 操作 ==============

    def zadd(self, key: str, mapping: dict) -> int:
        """添加成员到 Sorted Set，score 为 float"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                return backend.zadd(key, mapping)
            return self._fallback.zadd(key, mapping)
        except Exception as e:
            logger.warning(f"[Redis] zadd 失败 [{key}]: {e}")
            return 0

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        """按 score 范围移除成员"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                return backend.zremrangebyscore(key, min_score, max_score)
            return self._fallback.zremrangebyscore(key, min_score, max_score)
        except Exception as e:
            logger.warning(f"[Redis] zremrangebyscore 失败 [{key}]: {e}")
            return 0

    def zcard(self, key: str) -> int:
        """获取 Sorted Set 成员数"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                return backend.zcard(key)
            return self._fallback.zcard(key)
        except Exception as e:
            logger.warning(f"[Redis] zcard 失败 [{key}]: {e}")
            return 0

    def zrange(self, key: str, start: int, end: int) -> List[str]:
        """获取 Sorted Set 范围内的成员（按 score 升序）"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                result = backend.zrange(key, start, end)
                return [r.decode('utf-8') if isinstance(r, bytes) else r for r in result]
            return self._fallback.zrange(key, start, end)
        except Exception as e:
            logger.warning(f"[Redis] zrange 失败 [{key}]: {e}")
            return []

    # ============== 分布式锁 ==============

    def acquire_lock(self, key: str, value: str, ex: int = 60) -> bool:
        """尝试获取分布式锁（setnx + expire）"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                acquired = backend.set(key, value, nx=True, ex=ex)
                return bool(acquired)
            return self._fallback.acquire_lock(key, value, ex)
        except Exception as e:
            logger.warning(f"[Redis] acquire_lock 失败 [{key}]: {e}")
            return False

    def release_lock(self, key: str, value: str) -> bool:
        """释放分布式锁（只有持有正确 value 才释放）"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                lua_script = """
                if redis.call("get", KEYS[1]) == ARGV[1] then
                    return redis.call("del", KEYS[1])
                else
                    return 0
                end
                """
                result = backend.eval(lua_script, 1, key, value)
                return bool(result)
            return self._fallback.release_lock(key, value)
        except Exception as e:
            logger.warning(f"[Redis] release_lock 失败 [{key}]: {e}")
            return False

    def renew_lock(self, key: str, value: str, ex: int) -> bool:
        """仅锁持有者可原子续期；安全关键 lease 不使用内存降级模拟跨 worker。"""
        if not self.is_available() or self._client is None:
            return False
        try:
            script = """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('expire', KEYS[1], tonumber(ARGV[2]))
            end
            return 0
            """
            return bool(self._client.eval(script, 1, key, value, ex))
        except Exception as e:
            logger.warning(f"[Redis] renew_lock 失败 [{key}]: {e}")
            return False

    def session_finalize_if_quiet(
        self, lock_key: str, lock_value: str, cancel_key: str,
        merge_key: str, pending_key: str, finalizing_key: str,
        state_guard_key: str, expected_input: str, ttl: int,
    ) -> bool:
        """原子确认本轮输入已消费完毕，并切换到持久化/出站交接态。"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                script = r'''
                local function decode_payload(raw)
                    if not raw then return {} end
                    local ok, value = pcall(cjson.decode, raw)
                    if not ok then return {} end
                    if type(value) == 'string' then
                        ok, value = pcall(cjson.decode, value)
                        if not ok then return {} end
                    end
                    return value
                end
                if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
                if redis.call('exists', KEYS[6]) == 1 then return 0 end
                if redis.call('exists', KEYS[5]) == 1 then return 1 end
                if redis.call('exists', KEYS[4]) == 1 then return 0 end
                local merge = decode_payload(redis.call('get', KEYS[3]))
                if redis.call('exists', KEYS[2]) == 1 and
                   tostring(merge['text'] or '') ~= ARGV[2] then return 0 end
                redis.call('set', KEYS[5], '1', 'EX', tonumber(ARGV[3]))
                redis.call('expire', KEYS[1], tonumber(ARGV[3]))
                return 1
                '''
                return bool(backend.eval(
                    script, 6, lock_key, cancel_key, merge_key, pending_key,
                    finalizing_key, state_guard_key, lock_value, expected_input, ttl,
                ))
            with self._fallback._lock:
                if self._fallback._data.get(lock_key) != lock_value:
                    return False
                if state_guard_key in self._fallback._data:
                    return False
                if finalizing_key in self._fallback._data:
                    return True
                if pending_key in self._fallback._data:
                    return False
                merge = self._decode_session_payload(
                    self._fallback._data.get(merge_key)
                )
                if cancel_key in self._fallback._data and merge.get("text", "") != expected_input:
                    return False
                self._fallback._data[finalizing_key] = "1"
                self._fallback._types[finalizing_key] = "string"
                import time
                self._fallback._ttls[finalizing_key] = time.time() + ttl
                self._fallback._ttls[lock_key] = time.time() + ttl
                return True
        except Exception as e:
            logger.warning(f"[Redis] session finalize 失败 [{lock_key}]: {e}")
            return False

    def session_release_finalized(
        self, lock_key: str, lock_value: str, cancel_key: str,
        merge_key: str, responding_key: str, finalizing_key: str,
    ) -> bool:
        """原子释放 finalizing ownership，避免先清标记后清锁的交接窗口。"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                script = r'''
                if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
                redis.call('del', KEYS[2], KEYS[3], KEYS[4], KEYS[5], KEYS[1])
                return 1
                '''
                return bool(backend.eval(
                    script, 5, lock_key, cancel_key, merge_key,
                    responding_key, finalizing_key, lock_value,
                ))
            with self._fallback._lock:
                if self._fallback._data.get(lock_key) != lock_value:
                    return False
                for item_key in (
                    cancel_key, merge_key, responding_key, finalizing_key, lock_key
                ):
                    self._fallback._data.pop(item_key, None)
                    self._fallback._types.pop(item_key, None)
                    self._fallback._ttls.pop(item_key, None)
                return True
        except Exception as e:
            logger.warning(f"[Redis] session finalized release 失败 [{lock_key}]: {e}")
            return False

    @staticmethod
    def _decode_session_payload(raw: Any) -> Dict[str, Any]:
        value = raw
        for _ in range(2):
            if not isinstance(value, str):
                break
            try:
                value = json.loads(value)
            except Exception:
                return {}
        return value if isinstance(value, dict) else {}

    # ============== Pub/Sub ==============

    def publish(self, channel: str, message: str) -> int:
        """发布消息到频道"""
        backend = self._get_backend()
        try:
            if self._connected and self._client:
                return backend.publish(channel, message)
            return self._fallback.publish(channel, message)
        except Exception as e:
            logger.warning(f"[Redis] publish 失败 [{channel}]: {e}")
            return 0

    def subscribe(self, channel: str):
        """订阅频道，返回 pubsub 对象（仅原生 Redis）"""
        if self._connected and self._client:
            try:
                return self._client.pubsub()
            except Exception as e:
                logger.warning(f"[Redis] subscribe 失败 [{channel}]: {e}")
        return None

    # ============== 工厂方法 ==============

    def make_key(self, prefix: str, identifier: str) -> str:
        """按规范生成 Redis Key，自动拼接全局 key_prefix 实现多实例隔离

        格式：{key_prefix}:{prefix}:{identifier}   （key_prefix 为空时省略）
        例如：key_prefix="prod", prefix="cancelled_session", identifier="abc123"
             → "prod:cancelled_session:abc123"
        """
        parts = [self._key_prefix, prefix, identifier] if self._key_prefix else [prefix, identifier]
        return ":".join(parts)

    def clear_all(self) -> Dict[str, int]:
        """清空所有本应用写入的 Redis 键

        按 REDIS_KEY_PREFIX 隔离：
        - 配置了 key_prefix：仅清空以 `{key_prefix}:` 开头的键
        - 未配置 key_prefix：清空当前 db 中所有键（keys('*') 扫描后逐个删除）

        同时清空内存降级存储，避免 Redis 重连后残留脏数据。
        不使用 flushdb，避免误删其他系统共享同一 Redis 实例的键。

        Returns:
            {"keys_found": 扫描到的键数, "deleted": 实际删除的键数}
        """
        # 计算匹配模式：配了 prefix 只清本应用键；没配 prefix 才全量清
        pattern = f"{self._key_prefix}:*" if self._key_prefix else "*"
        keys_found = self.keys(pattern)

        deleted = 0
        for k in keys_found:
            if self.delete(k):
                deleted += 1

        # 清空内存降级缓存（Redis 重连后可能残留历史数据）
        with self._fallback._lock:
            fallback_cleared = len(self._fallback._data)
            self._fallback._data.clear()

        logger.info(
            f"[Redis] clear_all pattern={pattern} deleted={deleted} "
            f"fallback_cleared={fallback_cleared}"
        )
        return {"keys_found": len(keys_found), "deleted": deleted, "fallback_cleared": fallback_cleared}


# 全局 Redis 客户端实例
redis_client = RedisClient()
