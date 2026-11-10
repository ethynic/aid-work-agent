"""
/api/upload 认证与大小限制测试（压测缺陷 #6 回归）

覆盖：
- 无认证 → 401
- 有认证无租户上下文（platform_admin 未指定 X-Tenant-Id）→ 403
- 有效 token + 租户 → 200，文件落在租户 conversation 目录
- 声明大小超限（预检）→ 按既有约定 200 + success=false
- 声明大小不可信（size=None）但实际字节超限（分块累计）→ 200 + success=false，半截文件已删除
- GET/DELETE /api/upload/{file_id} 无认证 → 401

测试策略（同 test_tenant_code_api.py 的 TestClient 用法）：
- TestClient 挂 src.main.app，走真实 TenantContextMiddleware（token → users 行 → 租户上下文）
- verify_token 是 src.api.auth 的模块级绑定，middleware 与 get_current_user 共用，单点 patch
- 认证链 DB 查询、Redis、asset_store 全 mock；存储根经 AGENT_RUNNER_STORAGE_ROOT
  重定向到 tmp_path——无外部依赖，故放 tests/unit/api 处处可跑
"""

import io
import json
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.main import app

pytestmark = [pytest.mark.unit, pytest.mark.api]

TENANT_ID = f"tenant_t{uuid.uuid4().hex[:8]}"
USER_ID = f"u_{uuid.uuid4().hex[:8]}"

_TENANT_USER = {"user_id": USER_ID, "role": "tenant_admin", "tenant_id": TENANT_ID}
_ADMIN_USER = {"user_id": USER_ID, "role": "platform_admin", "tenant_id": None}


def _client():
    return TestClient(app)


def _patch_auth_chain(monkeypatch, users_row, cached_user):
    """单点 patch 认证链：verify_token + get_cached（src.api.auth 模块级绑定）
    + middleware 的 users 表查询（get_db_connection）"""
    monkeypatch.setattr("src.api.auth.verify_token", lambda token: USER_ID)
    monkeypatch.setattr("src.api.auth.get_cached", lambda *a, **k: cached_user)

    class _FakeCursor:
        def execute(self, *a, **k):
            pass

        def fetchone(self):
            return users_row

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("src.saas.middleware.get_db_connection", lambda: _FakeConn())


@pytest.fixture
def tenant_storage(monkeypatch, tmp_path):
    """存储根重定向到 tmp_path，上传文件不落仓库 storage/"""
    monkeypatch.setenv("AGENT_RUNNER_STORAGE_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def no_redis(monkeypatch):
    """Redis 全 mock（hset/expire/delete/hgetall/make_key）"""
    monkeypatch.setattr("src.core.redis_client.redis_client.hset", lambda *a, **k: 1)
    monkeypatch.setattr("src.core.redis_client.redis_client.expire", lambda *a, **k: True)
    monkeypatch.setattr("src.core.redis_client.redis_client.delete", lambda *a, **k: 1)
    monkeypatch.setattr("src.core.redis_client.redis_client.hgetall", lambda *a, **k: {})
    monkeypatch.setattr(
        "src.core.redis_client.redis_client.make_key",
        lambda prefix, identifier="": f"{prefix}:{identifier}",
    )


class TestUploadAuth:
    def test_upload_without_auth_returns_401(self, tenant_storage, no_redis):
        """无认证 → 401 未登录"""
        resp = _client().post(
            "/api/upload",
            files={"file": ("a.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 401
        assert "未登录" in resp.json()["detail"]

    def test_upload_with_auth_without_tenant_returns_403(self, monkeypatch, tenant_storage, no_redis):
        """有认证无租户上下文（platform_admin 未指定 X-Tenant-Id）→ 403"""
        _patch_auth_chain(
            monkeypatch,
            users_row={"role": "platform_admin", "tenant_id": None},
            cached_user=_ADMIN_USER,
        )
        resp = _client().post(
            "/api/upload",
            files={"file": ("a.txt", b"hello", "text/plain")},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 403
        assert "缺少租户上下文" in resp.json()["detail"]

    def test_upload_success_file_lands_in_tenant_dir(
        self, monkeypatch, tenant_storage, no_redis
    ):
        """有效 token + 租户 → 200，文件落在租户 conversation 目录"""
        _patch_auth_chain(
            monkeypatch,
            users_row={"role": "tenant_admin", "tenant_id": TENANT_ID},
            cached_user=_TENANT_USER,
        )
        monkeypatch.setattr(
            "src.services.files.asset_store.upsert_file_asset", lambda *a, **k: None
        )

        resp = _client().post(
            "/api/upload",
            files={"file": ("note.txt", b"upload-auth-test-content", "text/plain")},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["name"] == "note.txt"
        assert data["size"] == len(b"upload-auth-test-content")
        assert data["mime_type"] == "text/plain"
        assert data["file_id"].startswith("file_")

        # 租户目录：storage/tenants/{剥离 tenant_ 前缀}/conversation/
        landed = tenant_storage / "tenants" / TENANT_ID[len("tenant_"):] / "conversation"
        files = list(landed.iterdir())
        assert len(files) == 1
        assert files[0].read_bytes() == b"upload-auth-test-content"

    def test_get_and_delete_require_auth(self):
        """GET/DELETE /api/upload/{file_id} 无认证 → 401（无调用方依赖匿名访问，同加门禁）"""
        client = _client()
        assert client.get("/api/upload/file_somefile01").status_code == 401
        assert client.delete("/api/upload/file_somefile01").status_code == 401


class TestUploadSizeLimit:
    def test_oversize_precheck_returns_convention_error(
        self, monkeypatch, tenant_storage, no_redis
    ):
        """声明大小超限（预检路）→ 既有约定：200 + success=false + 文件过大"""
        from src.config.settings import settings

        _patch_auth_chain(
            monkeypatch,
            users_row={"role": "tenant_admin", "tenant_id": TENANT_ID},
            cached_user=_TENANT_USER,
        )
        monkeypatch.setattr(settings.storage, "max_general_file_size", 8)

        resp = _client().post(
            "/api/upload",
            files={"file": ("big.txt", b"x" * 64, "text/plain")},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "文件过大" in data["error"]

    @pytest.mark.asyncio
    async def test_oversize_actual_bytes_deletes_partial_file(
        self, monkeypatch, tenant_storage, no_redis
    ):
        """size 声明不可信（None）但实际字节超限（分块累计路）→ 返回约定错误且半截文件已删除"""
        from src.config.settings import settings
        from starlette.requests import Request

        from src.main import upload_file

        monkeypatch.setattr(settings.storage, "max_general_file_size", 16)
        monkeypatch.setattr("src.api.auth.get_current_user", lambda request: _TENANT_USER)
        monkeypatch.setattr(
            "src.saas.context.get_current_tenant_id", lambda: TENANT_ID
        )

        class _FakeUpload:
            filename = "big.bin"
            size = None  # 声明大小缺失：预检跳过，必须由分块累计兜底
            file = io.BytesIO(b"x" * 64)

        request = Request({
            "type": "http", "method": "POST", "path": "/api/upload",
            "headers": [(b"authorization", b"Bearer test-token")],
            "query_string": b"",
        })
        response = await upload_file(http_request=request, file=_FakeUpload())

        data = json.loads(response.body)
        assert data["success"] is False
        assert "文件过大" in data["error"]

        # 半截文件已删除：租户 conversation 目录为空
        landed = tenant_storage / "tenants" / TENANT_ID[len("tenant_"):] / "conversation"
        assert landed.exists()
        assert list(landed.iterdir()) == []
