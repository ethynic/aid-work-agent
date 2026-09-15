"""WP2 独立验证：API 层外部文档边界探针（开发者自承未验证项）

- 外部文档删除/移动拦截的 API 级状态码
- include_deleted 权限边界：非 platform_admin 角色（含租户管理员）传入被忽略
- 跨租户下载外部无文件文档的响应码顺序（权限检查应先于来源检查）
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from starlette.requests import Request

from src.knowledge.service import KnowledgeBaseService
from src.saas.context import set_tenant_context, clear_tenant_context


class _FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *a, **k):
        return self._cursor

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _req(role):
    scope = {"type": "http", "method": "GET", "path": "/x", "raw_path": b"/x",
             "headers": [], "query_string": b"", "server": ("t", 80),
             "scheme": "http", "http_version": "1.1"}
    r = Request(scope)
    r.state.user_role = role
    return r


EXT_ROW = {
    "file_path": None, "title": "外部文档", "tenant_id": "t2",
    "source_type": "wechat_mp", "origin": "wechat_mp", "status": "active",
    "metadata": json.dumps({"original_url": "https://mp.weixin.qq.com/s/leak-probe"}),
}


@pytest.fixture(autouse=True)
def _ctx():
    set_tenant_context(None, None)
    yield
    clear_tenant_context()


class TestApiDeleteExternal:
    @pytest.mark.asyncio
    async def test_delete_route_returns_400_not_404(self):
        """API 级：外部文档删除经路由返回 400（service status 透出）"""
        from src.knowledge import api as kb_api
        cur = _FakeCursor([{"file_path": None, "origin": "wechat_mp"}])
        set_tenant_context("t1", "u1")
        with patch.object(KnowledgeBaseService, "_get_db_connection",
                          return_value=_FakeConn(cur)):
            resp = await kb_api.delete_document(7, http_request=_req("employee"))
        assert resp.status_code == 400
        assert "同步任务管理" in json.loads(bytes(resp.body))["error"]


class TestIncludeDeletedRoles:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", ["employee", "admin", "tenant_admin"])
    async def test_non_platform_admin_include_deleted_ignored(self, role):
        """任意非 platform_admin 角色传 include_deleted=true 均被静默忽略"""
        from src.knowledge import api as kb_api
        set_tenant_context("t1", "u1")
        with patch.object(kb_api.knowledge_service, "list_documents", MagicMock(return_value=[])) as m_list, \
             patch.object(kb_api.knowledge_service, "count_documents", MagicMock(return_value=0)):
            await kb_api.list_documents(include_deleted=True, http_request=_req(role))
        assert m_list.call_args.kwargs["include_deleted"] is False


class TestCrossTenantDownloadOrder:
    @pytest.mark.asyncio
    async def test_cross_tenant_external_download_must_not_leak_url(self):
        """跨租户探测他人外部文档：应先 403/404，不得先返回 400 + original_url。
        （WP2 复核 P1 修复后转正：权限检查已前移到来源分支之前）"""
        from fastapi import HTTPException
        from src.knowledge import api as kb_api
        set_tenant_context("t1", "u1")
        with patch.object(KnowledgeBaseService, "_get_db_connection",
                          return_value=_FakeConn(_FakeCursor([EXT_ROW]))):
            with pytest.raises(HTTPException) as exc_info:
                await kb_api.download_document(7, http_request=_req("employee"))
        assert exc_info.value.status_code in (403, 404), (
            f"跨租户外部文档返回 {exc_info.value.status_code}，"
            f"detail={exc_info.value.detail}")

    @pytest.mark.asyncio
    async def test_cross_tenant_ticket_must_not_leak_url(self):
        """票据路径：权限检查在外部来源 400 之前（对照组，应通过）"""
        from fastapi import HTTPException
        from src.knowledge import api as kb_api
        set_tenant_context("t1", "u1")
        with patch.object(KnowledgeBaseService, "_get_db_connection",
                          return_value=_FakeConn(_FakeCursor([EXT_ROW]))):
            with pytest.raises(HTTPException) as exc_info:
                await kb_api.create_download_ticket(7, http_request=_req("employee"))
        assert exc_info.value.status_code in (403, 404)
