"""
RedisClient 单元测试

覆盖序列化、TTL、内存降级等核心功能。
由于测试环境可能无 Redis，主要测试降级到内存的场景，
并提供可选的 Redis 真实连接测试（通过环境变量 REDIS_TEST_HOST 启用）。
"""

import json
import os
import time
from unittest.mock import MagicMock

import pytest

from src.core.redis_client import RedisClient, _InMemoryFallback


class TestInMemoryFallback:
    """测试内存降级存储"""

    def test_basic_get_set(self):
        fallback = _InMemoryFallback()
        fallback.set("key1", "value1")
        assert fallback.get("key1") == "value1"

    def test_get_nonexistent(self):
        fallback = _InMemoryFallback()
        assert fallback.get("nonexistent") is None

    def test_delete(self):
        fallback = _InMemoryFallback()
        fallback.set("key1", "value1")
        assert fallback.delete("key1") == 1
        assert fallback.get("key1") is None
        assert fallback.delete("key1") == 0

    def test_hash_operations(self):
        fallback = _InMemoryFallback()
        fallback.hset("hash1", "field1", "value1")
        fallback.hset("hash1", "field2", "value2")

        assert fallback.hget("hash1", "field1") == "value1"
        assert fallback.hget("hash1", "field2") == "value2"
        assert fallback.hget("hash1", "nonexistent") is None

        all_fields = fallback.hgetall("hash1")
        assert all_fields == {"field1": "value1", "field2": "value2"}

        assert fallback.hdel("hash1", "field1") == 1
        assert fallback.hdel("hash1", "field1") == 0
        assert fallback.hget("hash1", "field1") is None

    def test_set_operations(self):
        fallback = _InMemoryFallback()
        fallback.sadd("set1", "member1")
        fallback.sadd("set1", "member2")

        assert fallback.sismember("set1", "member1") is True
        assert fallback.sismember("set1", "member2") is True
        assert fallback.sismember("set1", "nonexistent") is False

        assert fallback.srem("set1", "member1") == 1
        assert fallback.srem("set1", "member1") == 0
        assert fallback.sismember("set1", "member1") is False

    def test_keys_pattern(self):
        fallback = _InMemoryFallback()
        fallback.set("prefix:a", "1")
        fallback.set("prefix:b", "2")
        fallback.set("other:c", "3")

        keys = fallback.keys("prefix:*")
        assert sorted(keys) == ["prefix:a", "prefix:b"]

    def test_json_serialization(self):
        fallback = _InMemoryFallback()
        data = {"nested": {"key": "value"}, "list": [1, 2, 3]}
        fallback.set("json_key", data)
        # fallback 不做自动 JSON 序列化，直接存对象
        assert fallback.get("json_key") == data


class TestInMemoryFallbackAdminExtensions:
    """测试内存降级存储的管理页扩展方法（Phase 1）"""

    def test_scan_returns_all_matches(self):
        fallback = _InMemoryFallback()
        fallback.set("aid-local:token:abc", "1")
        fallback.set("aid-local:token:def", "2")
        fallback.set("aid-local:user:xxx", "3")

        cursor, keys = fallback.scan(0, "aid-local:token:*")
        assert cursor == 0
        assert set(keys) == {"aid-local:token:abc", "aid-local:token:def"}

    def test_scan_no_match(self):
        fallback = _InMemoryFallback()
        fallback.set("foo", "1")
        cursor, keys = fallback.scan(0, "bar:*")
        assert cursor == 0
        assert keys == []

    def test_type_string(self):
        fallback = _InMemoryFallback()
        fallback.set("k1", "v1")
        assert fallback.type("k1") == "string"

    def test_type_hash(self):
        fallback = _InMemoryFallback()
        fallback.hset("h1", "f1", "v1")
        assert fallback.type("h1") == "hash"

    def test_type_set(self):
        fallback = _InMemoryFallback()
        fallback.sadd("s1", "m1")
        assert fallback.type("s1") == "set"

    def test_type_zset(self):
        fallback = _InMemoryFallback()
        fallback.zadd("z1", {"m1": 1.0})
        assert fallback.type("z1") == "zset"

    def test_type_none_for_nonexistent(self):
        fallback = _InMemoryFallback()
        assert fallback.type("nonexistent") == "none"

    def test_ttl_persistent(self):
        fallback = _InMemoryFallback()
        fallback.set("k1", "v1")
        assert fallback.ttl("k1") == -1  # 永不过期

    def test_ttl_with_expiration(self):
        fallback = _InMemoryFallback()
        fallback.set("k1", "v1", ex=100)
        ttl = fallback.ttl("k1")
        assert 1 <= ttl <= 100  # 剩余秒数

    def test_ttl_nonexistent(self):
        fallback = _InMemoryFallback()
        assert fallback.ttl("nonexistent") == -2

    def test_hkeys(self):
        fallback = _InMemoryFallback()
        fallback.hset("h1", "f1", "v1")
        fallback.hset("h1", "f2", "v2")
        keys = fallback.hkeys("h1")
        assert set(keys) == {"f1", "f2"}

    def test_hkeys_nonexistent(self):
        fallback = _InMemoryFallback()
        assert fallback.hkeys("nonexistent") == []

    def test_llen_and_lrange(self):
        """_InMemoryFallback 没有 lpush/rpush，但管理页需要 llen/lrange 工作。
        由于 Phase 1 内存降级不会真正使用 list 类型（项目代码无 list 写入），
        这里只验证接口存在且对空键返回 0/[]。"""
        fallback = _InMemoryFallback()
        assert fallback.llen("nonexistent") == 0
        assert fallback.lrange("nonexistent", 0, -1) == []

    def test_smembers(self):
        fallback = _InMemoryFallback()
        fallback.sadd("s1", "m1")
        fallback.sadd("s1", "m2")
        members = fallback.smembers("s1")
        assert set(members) == {"m1", "m2"}

    def test_smembers_nonexistent(self):
        fallback = _InMemoryFallback()
        assert fallback.smembers("nonexistent") == []

    def test_memory_usage_returns_none(self):
        """内存降级无真实内存占用，返回 None"""
        fallback = _InMemoryFallback()
        fallback.set("k1", "v1")
        assert fallback.memory_usage("k1") is None

    def test_dbsize(self):
        fallback = _InMemoryFallback()
        fallback.set("k1", "v1")
        fallback.set("k2", "v2")
        fallback.hset("h1", "f1", "v1")
        # k1, k2, h1 共 3 个键
        assert fallback.dbsize() == 3

    def test_dbsize_empty(self):
        fallback = _InMemoryFallback()
        assert fallback.dbsize() == 0

    def test_dbsize_excludes_expired(self):
        """过期的键不应计入 dbsize"""
        fallback = _InMemoryFallback()
        fallback.set("k1", "v1", ex=0)  # ex=0 立即删除
        assert fallback.dbsize() == 0

    def test_ttl_expire_zero_deletes_key(self):
        """expire(key, 0) 等价于立即删除"""
        fallback = _InMemoryFallback()
        fallback.set("k1", "v1")
        result = fallback.expire("k1", 0)
        assert result is True
        assert fallback.get("k1") is None
        assert fallback.dbsize() == 0

    def test_scan_supports_glob_pattern(self):
        """scan 支持 fnmatch 通配符，不仅仅是后缀匹配"""
        fallback = _InMemoryFallback()
        fallback.set("aid-local:token:abc", "1")
        fallback.set("aid-local:user:abc", "2")
        fallback.set("aid-local:token:def", "3")

        # 通配 *abc 应该匹配所有以 abc 结尾的键
        cursor, keys = fallback.scan(0, "*abc")
        assert cursor == 0
        assert set(keys) == {"aid-local:token:abc", "aid-local:user:abc"}


class TestRedisClientInterface:
    """测试 RedisClient 接口（默认使用内存降级）"""

    @pytest.fixture
    def client(self):
        """创建独立的 RedisClient 实例（隔离内存状态）"""
        c = RedisClient()
        c._connected = False
        c._client = None
        c._fallback = _InMemoryFallback()
        return c

    def test_set_get_json(self, client):
        data = {"name": "test", "value": 42, "nested": {"a": 1}}
        client.set("test_key", data)
        result = client.get("test_key")
        assert result == data

    def test_get_nonexistent(self, client):
        assert client.get("nonexistent") is None

    def test_delete(self, client):
        client.set("del_key", "value")
        assert client.delete("del_key") is True
        assert client.get("del_key") is None

    def test_hash_set_get(self, client):
        client.hset("hash_key", "field1", "value1")
        client.hset("hash_key", "field2", {"nested": True})

        assert client.hget("hash_key", "field1") == "value1"
        assert client.hget("hash_key", "field2") == {"nested": True}

        all_fields = client.hgetall("hash_key")
        assert all_fields["field1"] == "value1"
        assert all_fields["field2"] == {"nested": True}

    def test_set_operations(self, client):
        client.sadd("set_key", "member1")
        client.sadd("set_key", "member2")

        assert client.sismember("set_key", "member1") is True
        assert client.sismember("set_key", "nonexistent") is False

        assert client.srem("set_key", "member1") == 1
        assert client.sismember("set_key", "member1") is False

    def test_exists(self, client):
        client.set("exists_key", "value")
        assert client.exists("exists_key") is True
        assert client.exists("nonexistent") is False

    def test_make_key(self, client):
        assert client.make_key("prefix", "id123") == "prefix:id123"

    def test_ttl_expiration_simulated(self, client):
        """内存降级模式下 expire 返回成功但不真正过期，验证接口可用"""
        client.set("ttl_key", "value")
        assert client.expire("ttl_key", 1) is True
        # 内存降级不真正支持 TTL，但接口可用
        assert client.exists("ttl_key") is True

    def test_complex_data_types(self, client):
        """测试复杂数据类型的 JSON 序列化/反序列化"""
        data = {
            "string": "hello",
            "integer": 42,
            "float": 3.14,
            "boolean": True,
            "null": None,
            "list": [1, 2, {"nested": "value"}],
            "dict": {"a": 1, "b": ["x", "y"]},
        }
        client.set("complex", data)
        result = client.get("complex")
        assert result == data


class TestRedisClientAdminExtensions:
    """测试 RedisClient 管理页扩展方法（9 个新方法，降级模式）"""

    @pytest.fixture
    def client(self):
        """创建独立的 RedisClient 实例（强制使用内存降级）"""
        c = RedisClient()
        c._connected = False
        c._client = None
        c._fallback = _InMemoryFallback()
        return c

    def test_scan_with_pattern(self, client):
        client.set("aid-local:token:abc", {"v": 1})
        client.set("aid-local:token:def", {"v": 2})
        client.set("aid-local:user:xxx", {"v": 3})

        cursor, keys = client.scan(0, "aid-local:token:*")
        assert cursor == 0
        assert set(keys) == {"aid-local:token:abc", "aid-local:token:def"}

    def test_scan_empty(self, client):
        cursor, keys = client.scan(0, "nonexistent:*")
        assert cursor == 0
        assert keys == []

    def test_type_for_string(self, client):
        client.set("str_key", "value")
        assert client.type("str_key") == "string"

    def test_type_for_hash(self, client):
        client.hset("hash_key", "f1", "v1")
        assert client.type("hash_key") == "hash"

    def test_type_for_set(self, client):
        client.sadd("set_key", "m1")
        assert client.type("set_key") == "set"

    def test_type_for_zset(self, client):
        client.zadd("zset_key", {"m1": 1.0})
        assert client.type("zset_key") == "zset"

    def test_type_for_nonexistent(self, client):
        assert client.type("nonexistent") == "none"

    def test_ttl_persistent(self, client):
        client.set("k1", "v1")
        assert client.ttl("k1") == -1

    def test_ttl_nonexistent(self, client):
        assert client.ttl("nonexistent") == -2

    def test_hkeys(self, client):
        client.hset("h1", "f1", "v1")
        client.hset("h1", "f2", "v2")
        keys = client.hkeys("h1")
        assert set(keys) == {"f1", "f2"}

    def test_hkeys_nonexistent(self, client):
        assert client.hkeys("nonexistent") == []

    def test_llen_nonexistent(self, client):
        assert client.llen("nonexistent") == 0

    def test_lrange_nonexistent(self, client):
        assert client.lrange("nonexistent", 0, -1) == []

    def test_smembers(self, client):
        client.sadd("s1", "m1")
        client.sadd("s1", "m2")
        members = client.smembers("s1")
        assert set(members) == {"m1", "m2"}

    def test_smembers_nonexistent(self, client):
        assert client.smembers("nonexistent") == []

    def test_memory_usage_returns_none_in_fallback(self, client):
        """降级模式无真实内存占用"""
        client.set("k1", "v1")
        assert client.memory_usage("k1") is None

    def test_memory_usage_nonexistent(self, client):
        assert client.memory_usage("nonexistent") is None

    def test_dbsize(self, client):
        client.set("k1", "v1")
        client.set("k2", "v2")
        client.hset("h1", "f1", "v1")
        assert client.dbsize() == 3

    def test_dbsize_empty(self, client):
        assert client.dbsize() == 0

    def test_connected_property(self, client):
        """降级模式下 connected 应为 False"""
        assert client.connected is False

    def test_make_key_with_prefix(self, client):
        """验证 make_key 拼接逻辑（空段被过滤，无尾冒号）"""
        # 默认无 key_prefix
        assert client.make_key("token", "abc123") == "token:abc123"
        assert client.make_key("", "") == ""
        assert client.make_key("bare", "") == "bare"


@pytest.mark.skipif(
    not os.getenv("REDIS_TEST_HOST"),
    reason="需要设置 REDIS_TEST_HOST 环境变量以启用真实 Redis 测试"
)
class TestRedisClientWithRealRedis:
    """使用真实 Redis 的集成测试（可选）"""

    @pytest.fixture
    def client(self):
        import redis as redis_lib
        host = os.getenv("REDIS_TEST_HOST", "localhost")
        port = int(os.getenv("REDIS_TEST_PORT", "6379"))
        c = RedisClient()
        # 强制使用真实 Redis
        c._client = redis_lib.Redis(
            host=host, port=port, decode_responses=True,
            socket_connect_timeout=5, socket_timeout=5,
        )
        c._connected = True
        c._fallback = _InMemoryFallback()
        # 清理测试数据
        for key in c._client.keys("test:*"):
            c._client.delete(key)
        return c

    def test_set_get_with_real_redis(self, client):
        client.set("test:real", {"hello": "world"})
        result = client.get("test:real")
        assert result == {"hello": "world"}

    def test_ttl_with_real_redis(self, client):
        client.set("test:ttl", "value", ex=2)
        assert client.exists("test:ttl") is True
        time.sleep(3)
        assert client.exists("test:ttl") is False

    def test_hash_with_real_redis(self, client):
        client.hset("test:hash", "field1", {"a": 1})
        client.hset("test:hash", "field2", "string")
        assert client.hget("test:hash", "field1") == {"a": 1}
        assert client.hgetall("test:hash")["field2"] == "string"

    def test_set_with_real_redis(self, client):
        client.sadd("test:set", "member1")
        assert client.sismember("test:set", "member1") is True
        client.srem("test:set", "member1")
        assert client.sismember("test:set", "member1") is False


# ---------------------------------------------------------------------------
# 连接重试与降级恢复
# ---------------------------------------------------------------------------


def _make_redis_cfg():
    from types import SimpleNamespace
    return SimpleNamespace(
        enabled=True, host="localhost", port=6379,
        password=None, db=0, ssl=False, key_prefix="",
    )


def _bare_client():
    """构造未连接的裸实例（跳过 __init__ 的首连）"""
    from src.core.redis_client import RedisClient as RC
    c = RC.__new__(RC)
    c._client = None
    c._fallback = _InMemoryFallback()
    c._connected = False
    c._key_prefix = ""
    import threading
    c._lock = threading.Lock()
    return c


def test_connect_retries_then_succeeds(monkeypatch):
    """启动首连失败时按 retries 退避重试，恢复后不再降级"""
    import src.core.redis_client as rc_mod

    monkeypatch.setattr(rc_mod.settings, "redis", _make_redis_cfg(), raising=False)
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    fake_redis = MagicMock()
    fake_redis.ping.side_effect = [Exception("Error -3 name resolution"), Exception("conn refused"), None]
    monkeypatch.setattr("redis.Redis", lambda **kw: fake_redis)

    client = _bare_client()
    client._connect(retries=3)

    assert client._connected is True
    assert client._client is fake_redis
    assert fake_redis.ping.call_count == 3
    assert sleeps == [2, 4]  # 第 3 次成功，不再 sleep


def test_connect_exhausts_retries_degrades(monkeypatch):
    """启动首连重试耗尽后降级内存"""
    import src.core.redis_client as rc_mod

    monkeypatch.setattr(rc_mod.settings, "redis", _make_redis_cfg(), raising=False)
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    fake_redis = MagicMock()
    fake_redis.ping.side_effect = Exception("Error -3 name resolution")
    monkeypatch.setattr("redis.Redis", lambda **kw: fake_redis)

    client = _bare_client()
    client._connect(retries=3)

    assert client._connected is False
    assert client._client is None
    assert sleeps == [2, 4, 8]


def test_connect_runtime_default_no_retry(monkeypatch):
    """运行期重连（retries=0 缺省）只尝试一次，快速失败"""
    import src.core.redis_client as rc_mod

    monkeypatch.setattr(rc_mod.settings, "redis", _make_redis_cfg(), raising=False)
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    fake_redis = MagicMock()
    fake_redis.ping.side_effect = Exception("conn refused")
    monkeypatch.setattr("redis.Redis", lambda **kw: fake_redis)

    client = _bare_client()
    client._connect()

    assert client._connected is False
    assert sleeps == []


def test_ensure_connection_recovery_clears_fallback(monkeypatch):
    """从降级恢复连接后，内存降级存储残留数据被清空"""
    import src.core.redis_client as rc_mod

    monkeypatch.setattr(rc_mod.settings, "redis", _make_redis_cfg(), raising=False)

    client = _bare_client()
    client._fallback.set("cancelled_session:s1", {"v": 1})
    assert client._fallback.exists("cancelled_session:s1")

    fake_redis = MagicMock()
    fake_redis.ping.return_value = None

    def fake_connect(retries=0):
        client._connected = True
        client._client = fake_redis

    monkeypatch.setattr(client, "_connect", fake_connect)
    assert client._ensure_connection() is True
    assert client._fallback.exists("cancelled_session:s1") is False
