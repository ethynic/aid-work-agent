"""
RedisClient 单元测试

覆盖序列化、TTL、内存降级等核心功能。
由于测试环境可能无 Redis，主要测试降级到内存的场景，
并提供可选的 Redis 真实连接测试（通过环境变量 REDIS_TEST_HOST 启用）。
"""

import json
import os
import time
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
