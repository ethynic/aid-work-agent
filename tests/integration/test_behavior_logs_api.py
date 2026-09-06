"""用户行为审计日志查询 API 集成测试（真实 PG）

Phase 4 查询端点：
- GET /api/admin/behavior-logs        租户管理员 / 平台管理员代租户（租户隔离核心）
- GET /api/saas/behavior-logs         仅平台管理员全局查询 + tenant_id 筛选

测试策略（避开 test_session/test_knowledge_endpoints 的 UserDB.create 既有失败模式，
不 mock UserDB，鉴权直接 mock src.api.behavior_logs.get_current_user 返回用户 dict，
checker.is_tenant_admin / is_platform_admin 读 dict 原生生效）：
- 租户上下文：patch TenantContextMiddleware._resolve_user_tenant 注入 request.state.tenant_id
- 数据：真实库直接 INSERT user_behavior_logs，双租户行隔离 + 筛选/分页/时间范围
- 表不存在时用幂等 DDL 兜底（与 deploy/init-postgres.sql 逐句一致）

DB 不可达时整模块 pytest.skip（init_db_pool session fixture）。
"""

import json
import uuid

import pytest

pytestmark = pytest.mark.integration

from fastapi.testclient import TestClient
from unittest.mock import patch

from src.main import app
from src.saas.models.enums import BehaviorAction, BehaviorResourceType


client = TestClient(app)

# 与 deploy/init-postgres.sql「user_behavior_logs」节逐句一致（幂等）
_DDL_BLOCK = """
CREATE TABLE IF NOT EXISTS user_behavior_logs (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    user_role TEXT,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    resource_name TEXT,
    detail JSONB DEFAULT '{}',
    client_ip VARCHAR(45),
    user_agent VARCHAR(512),
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_msg TEXT,
    entry VARCHAR(16),
    login_method VARCHAR(32),
    channel VARCHAR(16),
    channel_user_id TEXT,
    token_id TEXT,
    request_id TEXT,
    http_method VARCHAR(10),
    path VARCHAR(255),
    device_type VARCHAR(16),
    device_info VARCHAR(32),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ubl_tenant_time ON user_behavior_logs(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ubl_user_time ON user_behavior_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ubl_action_time ON user_behavior_logs(action, created_at DESC);
"""

_INSERT_ROW = """
INSERT INTO user_behavior_logs (
    tenant_id, user_id, user_role, action, resource_type, resource_id, resource_name,
    detail, client_ip, success, error_msg, entry, http_method, path
) VALUES (
    %(tenant_id)s, %(user_id)s, %(user_role)s, %(action)s, %(resource_type)s, %(resource_id)s,
    %(resource_name)s, %(detail)s, %(client_ip)s, %(success)s, %(error_msg)s, %(entry)s,
    %(http_method)s, %(path)s
)
RETURNING id
"""

# 旧行：created_at 用固定 SQL 常量（NOW()-40 天），走 DB 端计算
_INSERT_ROW_OLD = """
INSERT INTO user_behavior_logs (
    tenant_id, user_id, user_role, action, resource_type, resource_id, resource_name,
    detail, client_ip, success, error_msg, entry, http_method, path, created_at
) VALUES (
    %(tenant_id)s, %(user_id)s, %(user_role)s, %(action)s, %(resource_type)s, %(resource_id)s,
    %(resource_name)s, %(detail)s, %(client_ip)s, %(success)s, %(error_msg)s, %(entry)s,
    %(http_method)s, %(path)s, NOW() - INTERVAL '40 days'
)
RETURNING id
"""


@pytest.fixture(scope="module", autouse=True)
def _ensure_table(init_db_pool):
    """模块级幂等 DDL（测试库可能未跑过服务启动迁移，确保表存在）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(_DDL_BLOCK)
        conn.commit()


@pytest.fixture
def env():
    """双租户 + 行为日志行：租户 A 4 条（含 1 条 40 天前旧行）+ 租户 B 2 条"""
    from src.db.database import get_db_connection
    from src.saas.db.tenant_db import TenantDB

    code = uuid.uuid4().hex[:6].upper()
    tenant_a = TenantDB.create(
        company_name=f"行为日志测试租户-A-{code}", tenant_code=f"BL{code}",
        contact_name="测试联系人", contact_phone="13800000000")
    tenant_b = TenantDB.create(
        company_name=f"行为日志测试租户-B-{code}", tenant_code=f"BM{code}",
        contact_name="测试联系人", contact_phone="13800000001")
    if not tenant_a or not tenant_b:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_a_id, tenant_b_id = tenant_a["tenant_id"], tenant_b["tenant_id"]

    marker = uuid.uuid4().hex[:8]
    user_a = f"ua_{marker}"
    user_b = f"ub_{marker}"

    # detail 列为 JSONB，写入用 JSON 字符串（与 behavior_log._insert_sync 一致）
    update_detail = json.dumps({"fields": ["name"]}, ensure_ascii=False)

    rows = [
        # 租户 A：登录成功
        {"tenant_id": tenant_a_id, "user_id": user_a, "user_role": "tenant_admin",
         "action": BehaviorAction.LOGIN.value, "resource_type": None, "resource_id": None,
         "resource_name": None, "detail": None, "client_ip": "10.0.0.1", "success": True,
         "error_msg": None, "entry": "web", "http_method": "POST", "path": "/api/login",
         "created_at": None},
        # 租户 A：更新租户（关键字命中 resource_name）
        {"tenant_id": tenant_a_id, "user_id": user_a, "user_role": "tenant_admin",
         "action": BehaviorAction.UPDATE.value, "resource_type": BehaviorResourceType.TENANT.value,
         "resource_id": tenant_a_id, "resource_name": f"特殊资源名_{marker}", "detail": update_detail,
         "client_ip": "10.0.0.1", "success": True, "error_msg": None, "entry": "web",
         "http_method": "PUT", "path": "/api/saas/tenants", "created_at": None},
        # 租户 A：删除失败
        {"tenant_id": tenant_a_id, "user_id": user_a, "user_role": "tenant_admin",
         "action": BehaviorAction.DELETE.value, "resource_type": BehaviorResourceType.TENANT.value,
         "resource_id": "tgt_x", "resource_name": None, "detail": None, "client_ip": "10.0.0.1",
         "success": False, "error_msg": "测试失败原因", "entry": "web",
         "http_method": "DELETE", "path": "/api/saas/tenants/tgt_x", "created_at": None},
        # 租户 A：40 天前旧行（时间范围过滤用）
        {"tenant_id": tenant_a_id, "user_id": user_a, "user_role": "tenant_admin",
         "action": BehaviorAction.LOGOUT.value, "resource_type": None, "resource_id": None,
         "resource_name": None, "detail": None, "client_ip": "10.0.0.1", "success": True,
         "error_msg": None, "entry": "web", "http_method": None, "path": None,
         "created_at": "old"},
        # 租户 B：登录成功（跨租户不可见用）
        {"tenant_id": tenant_b_id, "user_id": user_b, "user_role": "tenant_admin",
         "action": BehaviorAction.LOGIN.value, "resource_type": None, "resource_id": None,
         "resource_name": None, "detail": None, "client_ip": "10.0.0.2", "success": True,
         "error_msg": None, "entry": "web", "http_method": "POST", "path": "/api/login",
         "created_at": None},
        # 租户 B：导出
        {"tenant_id": tenant_b_id, "user_id": user_b, "user_role": "tenant_admin",
         "action": BehaviorAction.EXPORT.value, "resource_type": BehaviorResourceType.BILLING.value,
         "resource_id": None, "resource_name": None, "detail": None, "client_ip": "10.0.0.2",
         "success": True, "error_msg": None, "entry": "web", "http_method": "GET",
         "path": "/api/saas/usage", "created_at": None},
    ]

    inserted_ids = []
    with get_db_connection() as conn:
        cur = conn.cursor()
        for row in rows:
            params = {k: v for k, v in row.items() if k != "created_at"}
            if row["created_at"] == "old":
                cur.execute(_INSERT_ROW_OLD, params)
            else:
                cur.execute(_INSERT_ROW, params)
            inserted_ids.append(cur.fetchone()["id"])
        conn.commit()

    yield {
        "tenant_a": tenant_a_id, "tenant_b": tenant_b_id,
        "user_a": user_a, "user_b": user_b, "marker": marker,
        "row_ids": inserted_ids,
        "tenant_a_count": 4, "tenant_b_count": 2,
    }

    # 清理：行为日志行 + 租户
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM user_behavior_logs WHERE tenant_id = ANY(%s)",
                ([tenant_a_id, tenant_b_id],))
            conn.commit()
    except Exception:
        pass
    TenantDB.delete(tenant_a_id)
    TenantDB.delete(tenant_b_id)


# ============== 鉴权 mock 工具 ==============

def _patch_tenant_admin(tenant_id: str, user_id: str = "ta_test"):
    """mock 租户管理员（get_current_user 返回 dict，checker 读 dict 原生生效）"""
    return patch(
        "src.api.behavior_logs.get_current_user",
        return_value={"user_id": user_id, "role": "tenant_admin", "tenant_id": tenant_id},
    )


def _patch_platform_admin(user_id: str = "pa_test"):
    return patch(
        "src.api.behavior_logs.get_current_user",
        return_value={"user_id": user_id, "role": "platform_admin", "tenant_id": None},
    )


def _patch_normal_user(tenant_id: str):
    return patch(
        "src.api.behavior_logs.get_current_user",
        return_value={"user_id": "nu_test", "role": "user", "tenant_id": tenant_id},
    )


def _patch_unauthenticated():
    return patch("src.api.behavior_logs.get_current_user", return_value=None)


def _patch_middleware_tenant(tenant_id):
    """注入租户上下文（模拟中间件解析 Bearer token / X-Tenant-Id 后的 request.state.tenant_id）"""
    async def fake_resolve(self, request):
        return (tenant_id, "mw_user")

    return patch(
        "src.saas.middleware.TenantContextMiddleware._resolve_user_tenant",
        fake_resolve,
    )


def _patch_middleware_no_tenant():
    """注入空租户上下文（platform_admin 未带 X-Tenant-Id 场景）"""
    async def fake_resolve(self, request):
        return (None, "mw_user")

    return patch(
        "src.saas.middleware.TenantContextMiddleware._resolve_user_tenant",
        fake_resolve,
    )


# ============== 鉴权 ==============

class TestAuth:
    """两个端点的鉴权分支"""

    def test_admin_endpoint_unauthenticated_returns_401(self, env):
        with _patch_unauthenticated():
            response = client.get("/api/admin/behavior-logs")
        assert response.status_code == 401

    def test_platform_endpoint_unauthenticated_returns_401(self, env):
        with _patch_unauthenticated():
            response = client.get("/api/saas/behavior-logs")
        assert response.status_code == 401

    def test_admin_endpoint_normal_user_returns_403(self, env):
        """普通用户访问租户视角端点返回 403"""
        with _patch_normal_user(env["tenant_a"]), _patch_middleware_tenant(env["tenant_a"]):
            response = client.get("/api/admin/behavior-logs")
        assert response.status_code == 403

    def test_platform_endpoint_tenant_admin_returns_403(self, env):
        """租户管理员访问平台全局端点返回 403"""
        with _patch_tenant_admin(env["tenant_a"]):
            response = client.get("/api/saas/behavior-logs")
        assert response.status_code == 403


# ============== 租户视角端点（/api/admin/behavior-logs）==============

class TestTenantEndpoint:
    """GET /api/admin/behavior-logs：租户隔离 + 筛选 + 分页"""

    def test_tenant_admin_sees_only_own_tenant(self, env):
        """租户管理员只看本租户数据，跨租户行不可见"""
        with _patch_tenant_admin(env["tenant_a"], user_id=env["user_a"]), \
                _patch_middleware_tenant(env["tenant_a"]):
            response = client.get("/api/admin/behavior-logs")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["total"] == env["tenant_a_count"]
        # 所有返回行都属于本租户
        for row in data["data"]:
            assert row["tenant_id"] == env["tenant_a"]
        # 跨租户用户的行为不可见
        user_ids = {row["user_id"] for row in data["data"]}
        assert env["user_b"] not in user_ids
        assert env["user_a"] in user_ids

    def test_platform_admin_without_tenant_context_returns_400(self, env):
        """platform_admin 未带 X-Tenant-Id（无租户上下文）返回 400"""
        with _patch_platform_admin(), _patch_middleware_no_tenant():
            response = client.get("/api/admin/behavior-logs")
        assert response.status_code == 400
        assert "X-Tenant-Id" in response.json()["detail"]

    def test_platform_admin_with_target_tenant_sees_target_only(self, env):
        """platform_admin 带 X-Tenant-Id 代租户查询：只返回目标租户数据"""
        with _patch_platform_admin(), _patch_middleware_tenant(env["tenant_b"]):
            response = client.get("/api/admin/behavior-logs")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == env["tenant_b_count"]
        for row in data["data"]:
            assert row["tenant_id"] == env["tenant_b"]

    def test_filter_action(self, env):
        """action 筛选只返回对应行为类型"""
        with _patch_tenant_admin(env["tenant_a"]), _patch_middleware_tenant(env["tenant_a"]):
            response = client.get("/api/admin/behavior-logs?action=login")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["action"] == "login"

    def test_filter_success_false(self, env):
        """success=false 只返回失败行，且带 error_msg"""
        with _patch_tenant_admin(env["tenant_a"]), _patch_middleware_tenant(env["tenant_a"]):
            response = client.get("/api/admin/behavior-logs?success=false")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["success"] is False
        assert data["data"][0]["error_msg"] == "测试失败原因"

    def test_filter_keyword_matches_resource_name(self, env):
        """keyword ILIKE 命中 resource_name"""
        with _patch_tenant_admin(env["tenant_a"]), _patch_middleware_tenant(env["tenant_a"]):
            response = client.get(f"/api/admin/behavior-logs?keyword=特殊资源名_{env['marker']}")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["resource_name"] == f"特殊资源名_{env['marker']}"

    def test_filter_keyword_matches_user_id(self, env):
        """keyword ILIKE 命中 user_id"""
        with _patch_tenant_admin(env["tenant_a"]), _patch_middleware_tenant(env["tenant_a"]):
            response = client.get(f"/api/admin/behavior-logs?keyword={env['user_a']}")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == env["tenant_a_count"]

    def test_time_range_excludes_old_row(self, env):
        """time_range=30d 排除 40 天前的旧行"""
        with _patch_tenant_admin(env["tenant_a"]), _patch_middleware_tenant(env["tenant_a"]):
            resp_all = client.get("/api/admin/behavior-logs")
            resp_30d = client.get("/api/admin/behavior-logs?time_range=30d")

        assert resp_all.status_code == 200
        assert resp_30d.status_code == 200
        assert resp_all.json()["total"] == env["tenant_a_count"]
        assert resp_30d.json()["total"] == env["tenant_a_count"] - 1

    def test_time_range_today_includes_recent_rows(self, env):
        """time_range=today 包含刚插入的行"""
        with _patch_tenant_admin(env["tenant_a"]), _patch_middleware_tenant(env["tenant_a"]):
            response = client.get("/api/admin/behavior-logs?time_range=today")

        assert response.status_code == 200
        assert response.json()["total"] == env["tenant_a_count"] - 1  # 旧行被排除

    def test_pagination(self, env):
        """分页：page_size=1 第 2 页返回 1 条，total 不变"""
        with _patch_tenant_admin(env["tenant_a"]), _patch_middleware_tenant(env["tenant_a"]):
            response = client.get("/api/admin/behavior-logs?page=2&page_size=1")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["page"] == 2
        assert data["page_size"] == 1
        assert len(data["data"]) == 1
        assert data["total"] == env["tenant_a_count"]
        assert data["total_pages"] == env["tenant_a_count"]

    def test_response_row_fields_complete(self, env):
        """返回行为完整行 dict（含 detail / client_ip / entry / http_method 等字段）"""
        with _patch_tenant_admin(env["tenant_a"]), _patch_middleware_tenant(env["tenant_a"]):
            response = client.get("/api/admin/behavior-logs?success=false")

        row = response.json()["data"][0]
        for key in ("id", "tenant_id", "user_id", "user_role", "action", "resource_type",
                    "resource_id", "resource_name", "detail", "client_ip", "success",
                    "error_msg", "entry", "http_method", "path", "created_at"):
            assert key in row, f"缺少字段 {key}"

    def test_invalid_action_returns_400(self, env):
        with _patch_tenant_admin(env["tenant_a"]), _patch_middleware_tenant(env["tenant_a"]):
            response = client.get("/api/admin/behavior-logs?action=not_a_valid_action")
        assert response.status_code == 400

    def test_invalid_time_range_returns_400(self, env):
        with _patch_tenant_admin(env["tenant_a"]), _patch_middleware_tenant(env["tenant_a"]):
            response = client.get("/api/admin/behavior-logs?time_range=yesterday")
        assert response.status_code == 400


# ============== 平台全局端点（/api/saas/behavior-logs）==============

class TestPlatformEndpoint:
    """GET /api/saas/behavior-logs：全局查询 + tenant_id 筛选"""

    def test_platform_admin_sees_all_tenants(self, env):
        """平台管理员全局查询：两个租户的数据都可见"""
        with _patch_platform_admin():
            response = client.get("/api/saas/behavior-logs")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        tenant_ids = {row["tenant_id"] for row in data["data"]}
        assert env["tenant_a"] in tenant_ids
        assert env["tenant_b"] in tenant_ids
        # total 至少包含本 fixture 插入的 6 条
        assert data["total"] >= env["tenant_a_count"] + env["tenant_b_count"]

    def test_platform_admin_tenant_id_filter(self, env):
        """tenant_id 筛选只返回指定租户数据"""
        with _patch_platform_admin():
            response = client.get(f"/api/saas/behavior-logs?tenant_id={env['tenant_b']}")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == env["tenant_b_count"]
        for row in data["data"]:
            assert row["tenant_id"] == env["tenant_b"]

    def test_platform_admin_filter_combination(self, env):
        """tenant_id + action 组合筛选"""
        with _patch_platform_admin():
            response = client.get(
                f"/api/saas/behavior-logs?tenant_id={env['tenant_b']}&action=export")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["action"] == "export"
