"""
Redis 缓存管理 API 集成测试

覆盖 4 个端点的鉴权（403）+ 正常路径：
- GET /api/admin/redis/overview
- GET /api/admin/redis/keys
- GET /api/admin/redis/keys/{key:path}
- DELETE /api/admin/redis/keys/{key:path}

测试策略：
- 鉴权：mock get_current_user 返回非平台管理员，断言 403
- 正常路径：mock get_current_user + is_platform_admin，调用真实 redis_client（降级到内存）
- 不依赖真实 Redis，使用 _InMemoryFallback
"""

import pytest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from src.main import app
from src.core.redis_client import RedisClient, _InMemoryFallback


client = TestClient(app)


# ============== Fixtures ==============

@pytest.fixture
def fresh_redis():
    """替换全局 redis_client 为独立的内存降级实例，避免污染真实 Redis"""
    from src.api import admin_redis
    original = admin_redis.redis_client
    new_client = RedisClient()
    new_client._connected = False
    new_client._client = None
    new_client._fallback = _InMemoryFallback()
    admin_redis.redis_client = new_client
    yield new_client
    admin_redis.redis_client = original


def _patch_platform_admin(user_id: str = "admin_test"):
    """mock 平台管理员权限校验"""
    return (
        patch(
            "src.api.admin_redis.get_current_user",
            return_value={"user_id": user_id, "role": "platform_admin", "tenant_id": None},
        ),
        patch("src.api.admin_redis.is_platform_admin", return_value=True),
    )


def _patch_non_admin(user_id: str = "tenant_user"):
    """mock 非平台管理员（租户管理员）"""
    return (
        patch(
            "src.api.admin_redis.get_current_user",
            return_value={"user_id": user_id, "role": "tenant_admin", "tenant_id": "t_001"},
        ),
        patch("src.api.admin_redis.is_platform_admin", return_value=False),
    )


def _patch_unauthenticated():
    """mock 未登录"""
    return (
        patch("src.api.admin_redis.get_current_user", return_value=None),
        patch("src.api.admin_redis.is_platform_admin", return_value=False),
    )


# ============== Overview 端点 ==============

class TestOverviewEndpoint:
    """GET /api/admin/redis/overview"""

    def test_overview_unauthenticated_returns_401(self, fresh_redis):
        """未登录访问返回 401"""
        p1, p2 = _patch_unauthenticated()
        with p1, p2:
            response = client.get("/api/admin/redis/overview")
        assert response.status_code == 401

    def test_overview_non_admin_returns_403(self, fresh_redis):
        """租户管理员访问返回 403"""
        p1, p2 = _patch_non_admin()
        with p1, p2:
            response = client.get("/api/admin/redis/overview")
        assert response.status_code == 403

    def test_overview_platform_admin_success(self, fresh_redis):
        """平台管理员访问返回正常统计"""
        # 准备测试数据（使用 make_key 构建，兼容 key_prefix 配置）
        fresh_redis.set(fresh_redis.make_key("token", "abc"), {"user_id": "u1"})
        fresh_redis.set(fresh_redis.make_key("user", "u1"), {"name": "test"})
        fresh_redis.set(fresh_redis.make_key("bare_key_xyz", ""), {"data": "裸键"})

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get("/api/admin/redis/overview")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["total_keys"] == 3
        assert data["registered_keys"] == 2  # token, user
        assert data["bare_keys"] == 1  # bare_key_xyz
        assert data["redis_connected"] is False
        assert data["fallback_active"] is True

    def test_overview_empty_redis(self, fresh_redis):
        """空 Redis 返回 0"""
        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get("/api/admin/redis/overview")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["total_keys"] == 0
        assert data["registered_keys"] == 0
        assert data["bare_keys"] == 0


# ============== Keys 列表端点 ==============

class TestListKeysEndpoint:
    """GET /api/admin/redis/keys"""

    def test_list_keys_unauthenticated_returns_401(self, fresh_redis):
        p1, p2 = _patch_unauthenticated()
        with p1, p2:
            response = client.get("/api/admin/redis/keys")
        assert response.status_code == 401

    def test_list_keys_non_admin_returns_403(self, fresh_redis):
        p1, p2 = _patch_non_admin()
        with p1, p2:
            response = client.get("/api/admin/redis/keys")
        assert response.status_code == 403

    def test_list_keys_returns_all(self, fresh_redis):
        """不带过滤参数返回所有键"""
        fresh_redis.set(fresh_redis.make_key("token", "abc"), "v1")
        fresh_redis.set(fresh_redis.make_key("user", "u1"), "v2")

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get("/api/admin/redis/keys")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["items"]) == 2
        keys = {item["key"] for item in data["items"]}
        assert fresh_redis.make_key("token", "abc") in keys
        assert fresh_redis.make_key("user", "u1") in keys

    def test_list_keys_with_prefix_filter(self, fresh_redis):
        """按前缀过滤"""
        fresh_redis.set(fresh_redis.make_key("token", "abc"), "v1")
        fresh_redis.set(fresh_redis.make_key("token", "def"), "v2")
        fresh_redis.set(fresh_redis.make_key("user", "u1"), "v3")

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get("/api/admin/redis/keys?prefix=token")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["items"]) == 2
        for item in data["items"]:
            assert "token" in item["key"]

    def test_list_keys_prefix_filter_excludes_sibling_prefixes(self, fresh_redis):
        """前缀过滤必须用冒号锚定段：选 'token' 不应匹配 'token_usage' 键

        回归测试：原 _build_match_pattern 用 rstrip(':') + '*' 拼出 'token*'，
        会误匹配 token_usage / tokenize 等同前缀字符串。
        """
        fresh_redis.set(fresh_redis.make_key("token", "abc"), "v1")
        fresh_redis.set(fresh_redis.make_key("token_usage", "t1:2025"), "v2")

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get("/api/admin/redis/keys?prefix=token")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        keys = {item["key"] for item in data["items"]}
        assert fresh_redis.make_key("token", "abc") in keys
        # token_usage 不应出现在 token 前缀过滤结果中
        assert fresh_redis.make_key("token_usage", "t1:2025") not in keys

    def test_list_keys_with_search(self, fresh_redis):
        """按子串搜索"""
        fresh_redis.set(fresh_redis.make_key("token", "abc"), "v1")
        fresh_redis.set(fresh_redis.make_key("token", "def"), "v2")
        fresh_redis.set(fresh_redis.make_key("user", "xyz"), "v3")

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get("/api/admin/redis/keys?search=abc")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["items"]) == 1
        assert data["items"][0]["key"] == fresh_redis.make_key("token", "abc")

    def test_list_keys_bare_view(self, fresh_redis):
        """裸键视图：prefix=__bare__ 只返回裸键"""
        fresh_redis.set(fresh_redis.make_key("token", "abc"), "v1")  # 已登记
        fresh_redis.set(fresh_redis.make_key("unknown_prefix", "xyz"), "v2")  # 裸键
        fresh_redis.set(fresh_redis.make_key("another_bare", ""), "v3")  # 裸键

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get("/api/admin/redis/keys?prefix=__bare__")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["filter"]["is_bare_view"] is True
        # 应该只返回裸键
        for item in data["items"]:
            assert item["is_bare"] is True

    def test_list_keys_returns_type_ttl_size(self, fresh_redis):
        """列表项应包含 type / ttl / size 字段"""
        fresh_redis.set(fresh_redis.make_key("token", "abc"), {"v": 1})

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get("/api/admin/redis/keys")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["type"] == "string"
        assert item["ttl"] == -1  # 永不过期
        assert "ttl_info" in item
        assert item["ttl_info"]["label"] == "永不过期"
        assert "size_label" in item


# ============== 单键详情端点 ==============

class TestKeyDetailEndpoint:
    """GET /api/admin/redis/keys/{key:path}"""

    def test_key_detail_unauthenticated_returns_401(self, fresh_redis):
        p1, p2 = _patch_unauthenticated()
        with p1, p2:
            response = client.get("/api/admin/redis/keys/some_key")
        assert response.status_code == 401

    def test_key_detail_non_admin_returns_403(self, fresh_redis):
        p1, p2 = _patch_non_admin()
        with p1, p2:
            response = client.get("/api/admin/redis/keys/some_key")
        assert response.status_code == 403

    def test_key_detail_string_type(self, fresh_redis):
        """查看 string 类型键"""
        key = fresh_redis.make_key("token", "abc")
        fresh_redis.set(key, {"user_id": "u1", "role": "admin"})

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get(f"/api/admin/redis/keys/{key}")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["type"] == "string"
        assert data["value"] == {"user_id": "u1", "role": "admin"}
        assert data["ttl"] == -1

    def test_key_detail_hash_type(self, fresh_redis):
        """查看 hash 类型键"""
        key = fresh_redis.make_key("user", "h1")
        fresh_redis.hset(key, "f1", "v1")
        fresh_redis.hset(key, "f2", "v2")

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get(f"/api/admin/redis/keys/{key}")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["type"] == "hash"
        assert data["members"] == {"f1": "v1", "f2": "v2"}
        assert data["member_count"] == 2

    def test_key_detail_set_type(self, fresh_redis):
        """查看 set 类型键"""
        key = fresh_redis.make_key("s1", "test")
        fresh_redis.sadd(key, "m1")
        fresh_redis.sadd(key, "m2")

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get(f"/api/admin/redis/keys/{key}")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["type"] == "set"
        assert set(data["members"]) == {"m1", "m2"}
        assert data["member_count"] == 2

    def test_key_detail_nonexistent(self, fresh_redis):
        """查看不存在的键"""
        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get("/api/admin/redis/keys/nonexistent_key")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "不存在" in data["message"]

    def test_key_detail_path_with_colons(self, fresh_redis):
        """键名含多个冒号分隔符（:path 转换器）"""
        key = fresh_redis.make_key("token", "complex:id:123")
        fresh_redis.set(key, "value")

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get(f"/api/admin/redis/keys/{key}")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["key"] == key


# ============== 删除端点 ==============

class TestDeleteKeyEndpoint:
    """DELETE /api/admin/redis/keys/{key:path}"""

    def test_delete_unauthenticated_returns_401(self, fresh_redis):
        p1, p2 = _patch_unauthenticated()
        with p1, p2:
            response = client.delete("/api/admin/redis/keys/some_key")
        assert response.status_code == 401

    def test_delete_non_admin_returns_403(self, fresh_redis):
        p1, p2 = _patch_non_admin()
        with p1, p2:
            response = client.delete("/api/admin/redis/keys/some_key")
        assert response.status_code == 403

    def test_delete_existing_key(self, fresh_redis):
        """删除存在的键"""
        key = fresh_redis.make_key("token", "del_me")
        fresh_redis.set(key, "v1")
        assert fresh_redis.exists(key) is True

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.delete(f"/api/admin/redis/keys/{key}")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["deleted_key"] == key
        # 验证键已被删除
        assert fresh_redis.exists(key) is False

    def test_delete_nonexistent_returns_404(self, fresh_redis):
        """删除不存在的键返回 404"""
        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.delete("/api/admin/redis/keys/nonexistent_key")

        assert response.status_code == 404

    def test_delete_writes_audit_log(self, fresh_redis, tmp_path, monkeypatch):
        """删除操作应写审计日志到 log/temp/redis_admin.log"""
        # 把临时日志目录重定向到 tmp_path
        import src.core.temp_logger as tlog_mod
        monkeypatch.setattr(tlog_mod, "_TEMP_LOG_DIR", tmp_path)

        key = fresh_redis.make_key("token", "audit_test")
        fresh_redis.set(key, "v1")

        p1, p2 = _patch_platform_admin(user_id="admin_audit")
        with p1, p2:
            response = client.delete(f"/api/admin/redis/keys/{key}")

        assert response.status_code == 200
        log_file = tmp_path / "redis_admin.log"
        assert log_file.exists()
        log_content = log_file.read_text(encoding="utf-8")
        assert "DELETE" in log_content
        assert key in log_content
        assert "admin_audit" in log_content


# ============== 键名含特殊字符 ==============

class TestKeyWithPathConverter:
    """测试 {key:path} 转换器处理含 / 的键名"""

    def test_key_with_slash_in_name(self, fresh_redis):
        """键名含 / 字符（path 转换器应保留）"""
        # 注意：实际项目中 Redis 键名一般不含 /，但 path 转换器应能处理
        key = fresh_redis.make_key("token", "slash/test")
        fresh_redis.set(key, "v1")

        p1, p2 = _patch_platform_admin()
        with p1, p2:
            response = client.get(f"/api/admin/redis/keys/{key}")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["key"] == key
