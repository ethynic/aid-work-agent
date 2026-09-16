"""知识库租户作用域 SQL 单元测试（无 PG 环境可跑）。

安全加固设计 §2.4：list/count/delete/chunks 的租户作用域必须在 SQL 本体携带；
无租户上下文（None）收窄到无主文档（与检索侧 vector_db/hybrid_retriever
及下载侧 _can_download_document 口径一致）。本测试用假连接捕获 execute 的 SQL
文本与参数，离线断言收窄条件与占位符/参数一致性。

平台管理员全局视图（global_view）：认证 platform_admin 且无租户上下文时，
list/count/delete/chunks/search 不携带租户过滤（恢复 master 全局口径），
与「未认证 None → 无主文档收窄」显式区分，None 一种取值不再承载两种身份。
search 的 global_view 同时透传到检索层（FTS + 向量两路 SQL 均放开租户收窄），
检索层 SQL 构造由假连接捕获离线断言，而非 mock 掉检索层只验证标题回查。
"""
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from pathlib import Path
import contextlib
import importlib.util

import pytest
from starlette.requests import Request
from starlette.responses import Response

from src.knowledge.service import KnowledgeBaseService
from src.saas.context import set_tenant_context


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
    """满足 `with self._get_db_connection() as conn:` 的最小连接假件"""

    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *args, **kwargs):
        # 检索层以 cursor(cursor_factory=...) 形式获取游标，参数直接忽略
        return self._cursor

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _run(method, *args, rows=None, **kwargs):
    cur = _FakeCursor(rows)
    svc = KnowledgeBaseService()
    with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)):
        result = getattr(svc, method)(*args, **kwargs)
    return result, cur.executed


def _flat(sql):
    return " ".join(str(sql).split())


def _load_real_vector_db():
    """按文件位置加载真实 vector_db 模块（根 conftest 已把
    src.knowledge.vector_db.vector_db 替换为 stub，无法常规导入）"""
    file_path = Path(__file__).resolve().parents[2] / "src" / "knowledge" / "vector_db" / "vector_db.py"
    spec = importlib.util.spec_from_file_location("_real_vector_db_for_scope_test", str(file_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestListCountTenantScope:
    def test_list_none_tenant_narrowed_to_null(self):
        _, executed = _run("list_documents", tenant_id=None)
        sql = _flat(executed[0][0])
        assert "tenant_id IS NULL" in sql
        assert "tenant_id = %s" not in sql
        assert executed[0][1] == []  # 无租户参数

    def test_count_none_tenant_narrowed_to_null(self):
        _, executed = _run("count_documents", rows=[{"count": 0}], tenant_id=None)
        sql = _flat(executed[0][0])
        assert "tenant_id IS NULL" in sql
        assert "tenant_id = %s" not in sql

    def test_list_with_tenant_uses_equality_param(self):
        _, executed = _run("list_documents", tenant_id="t1")
        sql, params = executed[0]
        assert "tenant_id = %s" in _flat(sql)
        assert params == ["t1"]

    def test_count_with_tenant_uses_equality_param(self):
        _, executed = _run("count_documents", rows=[{"count": 1}], tenant_id="t1")
        sql, params = executed[0]
        assert "tenant_id = %s" in _flat(sql)
        assert params == ["t1"]


class TestChunksTenantScope:
    def test_get_chunks_none_tenant_joins_null_scope(self):
        _, executed = _run("get_document_chunks", 7, tenant_id=None)
        sql, params = executed[0]
        flat = _flat(sql)
        assert "JOIN documents d ON d.id = c.doc_id" in flat
        assert "d.tenant_id IS NULL" in flat
        assert params == [7]  # 仅 doc_id，无租户参数

    def test_get_chunks_with_tenant_param_order(self):
        _, executed = _run("get_document_chunks", 7, tenant_id="t1")
        sql, params = executed[0]
        assert "d.tenant_id = %s" in _flat(sql)
        assert params == ["t1", 7]


class TestDeleteTenantScope:
    @pytest.mark.asyncio
    async def test_delete_none_tenant_scope_in_all_sql(self):
        cur = _FakeCursor(rows=[{"file_path": "nonexistent_kb_guard.txt"}])
        svc = KnowledgeBaseService()
        with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)), \
             patch("src.knowledge.service.get_vector_db", return_value=AsyncMock()):
            result = await svc.delete_document(7, tenant_id=None)

        assert result["success"] is True
        sqls = [_flat(s) for s, _ in cur.executed]
        # SELECT 存在性检查 / DELETE chunks / DELETE documents 三处均带收窄条件
        assert len(sqls) == 3
        for sql in sqls:
            assert "tenant_id IS NULL" in sql
        # chunks 的 DELETE 经 documents 子查询关联租户
        assert "doc_id IN (SELECT id FROM documents" in sqls[1]
        # 无租户参数（仅 doc_id）
        assert cur.executed[0][1] == [7]
        assert cur.executed[1][1] == [7, 7]
        assert cur.executed[2][1] == [7]

    @pytest.mark.asyncio
    async def test_delete_with_tenant_param_order(self):
        cur = _FakeCursor(rows=[{"file_path": "nonexistent_kb_guard.txt"}])
        svc = KnowledgeBaseService()
        with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)), \
             patch("src.knowledge.service.get_vector_db", return_value=AsyncMock()):
            result = await svc.delete_document(7, tenant_id="t1")

        assert result["success"] is True
        assert cur.executed[0][1] == [7, "t1"]
        assert cur.executed[1][1] == [7, 7, "t1"]
        assert cur.executed[2][1] == [7, "t1"]


class TestGlobalAdminViewScope:
    """platform_admin + tenant None + global_view=True → SQL 无租户过滤（恢复 master 全局形态）"""

    def test_list_global_view_no_tenant_filter(self):
        _, executed = _run("list_documents", rows=[{"id": 1, "created_at": None}], tenant_id=None, global_view=True)
        sql = _flat(executed[0][0])
        assert "tenant_id" not in sql
        assert executed[0][1] == []

    def test_count_global_view_no_tenant_filter(self):
        _, executed = _run("count_documents", rows=[{"count": 5}], tenant_id=None, global_view=True)
        sql = _flat(executed[0][0])
        assert "tenant_id" not in sql
        assert not executed[0][1]

    def test_global_view_ignored_when_tenant_present(self):
        """global_view 不改变有租户上下文时的作用域（指定租户优先）"""
        _, executed = _run("list_documents", tenant_id="t1", global_view=True)
        sql, params = executed[0]
        assert "tenant_id = %s" in _flat(sql)
        assert params == ["t1"]

    def test_get_chunks_global_view_no_tenant_filter(self):
        cur = _FakeCursor(rows=[{
            "id": 1, "chunk_index": 0, "text": "x", "tokens": 1,
            "metadata": None, "has_vector": False, "vector_text": None,
        }])
        svc = KnowledgeBaseService()
        with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)):
            result = svc.get_document_chunks(7, tenant_id=None, global_view=True)
        assert len(result) == 1
        sql, params = cur.executed[0]
        assert "tenant_id" not in _flat(sql)
        assert params == [7]  # 仅 doc_id，无租户参数

    @pytest.mark.asyncio
    async def test_delete_global_view_no_tenant_filter(self):
        cur = _FakeCursor(rows=[{"file_path": "nonexistent_kb_guard.txt"}])
        svc = KnowledgeBaseService()
        with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)), \
             patch("src.knowledge.service.get_vector_db", return_value=AsyncMock()):
            result = await svc.delete_document(7, tenant_id=None, global_view=True)

        assert result["success"] is True
        sqls = [_flat(s) for s, _ in cur.executed]
        assert len(sqls) == 3
        for i, sql in enumerate(sqls):
            if i == 0:
                # SELECT 带出 tenant_id 列（外部文档删除时按文档归属抑制同步文章行），
                # 但全局视图下不得携带租户过滤条件
                assert "tenant_id = %s" not in sql
                assert "tenant_id IS NULL" not in sql
            else:
                assert "tenant_id" not in sql
        assert cur.executed[0][1] == [7]
        assert cur.executed[1][1] == [7, 7]
        assert cur.executed[2][1] == [7]

    @pytest.mark.asyncio
    async def test_search_global_view_title_lookup_no_tenant_filter(self):
        """search 标题回查在全局视图下不携带租户过滤（检索器走 mock）"""
        retriever = MagicMock()
        retriever.retrieve = AsyncMock(return_value=[{"doc_id": 11, "chunk_id": 111, "text": "片段", "score": 0.9}])
        cur = _FakeCursor(rows=[{"id": 11, "title": "x", "file_type": "txt", "file_path": "/tmp/x.txt"}])
        svc = KnowledgeBaseService()
        with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)), \
             patch("src.knowledge.service.get_vector_db", return_value=MagicMock()), \
             patch("src.knowledge.retriever.hybrid_retriever.HybridRetriever", return_value=retriever), \
             patch("src.config.settings.get_embedding_api_key", return_value="test-key"):
            result = await svc.search_documents("q", tenant_id=None, global_view=True)

        assert result["success"] is True
        sql, params = cur.executed[0]
        assert "tenant_id" not in _flat(sql)
        assert params == [11]

    @pytest.mark.asyncio
    async def test_search_none_tenant_narrowed_by_default(self):
        """默认（非全局视图）搜索标题回查仍收窄到无主文档（既有防泄漏行为保持）"""
        retriever = MagicMock()
        retriever.retrieve = AsyncMock(return_value=[{"doc_id": 11, "chunk_id": 111, "text": "片段", "score": 0.9}])
        cur = _FakeCursor(rows=[{"id": 11, "title": "x", "file_type": "txt", "file_path": "/tmp/x.txt"}])
        svc = KnowledgeBaseService()
        with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)), \
             patch("src.knowledge.service.get_vector_db", return_value=MagicMock()), \
             patch("src.knowledge.retriever.hybrid_retriever.HybridRetriever", return_value=retriever), \
             patch("src.config.settings.get_embedding_api_key", return_value="test-key"):
            result = await svc.search_documents("q", tenant_id=None)

        assert result["success"] is True
        sql = _flat(cur.executed[0][0])
        assert "tenant_id IS NULL" in sql


class TestFtsGlobalViewSQL:
    """FTS 检索层 SQL 构造：global_view=True + tenant None 不携带任何租户收窄条件；
    默认 False（含不传参的既有调用方）与现行收窄逐字节一致"""

    @staticmethod
    def _make_retriever(cur):
        from src.knowledge.retriever.hybrid_retriever import HybridRetriever
        return HybridRetriever(
            vector_db=MagicMock(), embedding_client=MagicMock(), conn=_FakeConn(cur))

    def test_global_view_no_tenant_condition(self):
        cur = _FakeCursor([])
        retriever = self._make_retriever(cur)
        retriever._postgres_fts_search("差旅", 5, tenant_id=None, global_view=True)

        sql, params = cur.executed[0]
        flat = _flat(sql)
        assert "tenant_id" not in flat  # 无任何租户收窄条件
        assert "demo" not in flat
        assert "text_vec @@ plainto_tsquery" in flat
        assert params == ["差旅", "差旅", 5]  # 仅查询词与 LIMIT，无租户参数

    def test_default_global_view_false_keeps_null_narrowing(self):
        """不传 global_view（既有调用方形态）→ 收窄条件与现在一致（防回归）"""
        cur = _FakeCursor([])
        retriever = self._make_retriever(cur)
        retriever._postgres_fts_search("差旅", 5, tenant_id=None)

        sql, params = cur.executed[0]
        flat = _flat(sql)
        assert "d.tenant_id IS NULL" in flat
        assert params == ["差旅", "差旅", 5]

    def test_explicit_global_view_false_identical_to_default(self):
        cur_a, cur_b = _FakeCursor([]), _FakeCursor([])
        retriever_a = self._make_retriever(cur_a)
        retriever_b = self._make_retriever(cur_b)
        retriever_a._postgres_fts_search("差旅", 5, tenant_id=None)
        retriever_b._postgres_fts_search("差旅", 5, tenant_id=None, global_view=False)

        assert _flat(cur_a.executed[0][0]) == _flat(cur_b.executed[0][0])
        assert cur_a.executed[0][1] == cur_b.executed[0][1]

    def test_tenant_present_takes_priority_over_global_view(self):
        """有租户上下文时按该租户收窄（global_view 不扩大范围）"""
        cur = _FakeCursor([])
        retriever = self._make_retriever(cur)
        retriever._postgres_fts_search("差旅", 5, tenant_id="t1", global_view=True)

        sql, params = cur.executed[0]
        flat = _flat(sql)
        assert "(d.tenant_id = %s)" in flat
        assert "demo" not in flat
        assert params == ["差旅", "差旅", "t1", 5]

    def test_fts_search_forwards_global_view(self):
        """_fts_search 包装层将 global_view 透传到 _postgres_fts_search"""
        cur = _FakeCursor([])
        retriever = self._make_retriever(cur)
        retriever._fts_search("差旅", 5, tenant_id=None, global_view=True)

        assert "tenant_id" not in _flat(cur.executed[0][0])


class TestVectorSearchGlobalViewSQL:
    """向量检索层 SQL 构造：global_view=True + tenant None 不携带任何租户收窄条件"""

    @staticmethod
    async def _search(cur, **kwargs):
        vdb_cls = _load_real_vector_db().VectorDBPostgreSQL
        vdb = vdb_cls(dimension=4, conn=_FakeConn(cur))
        return await vdb.search([0.1, 0.2, 0.3, 0.4], top_k=5, **kwargs)

    @staticmethod
    def _select_sql(cur):
        """取真正的搜索语句（过滤掉 __init__ 建表期的 CREATE 语句）"""
        selects = [(s, p) for s, p in cur.executed if _flat(s).upper().startswith("SELECT")]
        assert len(selects) == 1
        return selects[0]

    @pytest.mark.asyncio
    async def test_global_view_no_tenant_condition(self):
        cur = _FakeCursor([])
        await self._search(cur, tenant_id=None, global_view=True)

        sql, params = self._select_sql(cur)
        flat = _flat(sql)
        assert "tenant_id" not in flat  # 无任何租户收窄条件
        assert "demo" not in flat
        assert "embedding <=> %s::vector" in flat
        assert params == ["[0.1,0.2,0.3,0.4]", 5]  # 仅查询向量与 LIMIT，无租户参数

    @pytest.mark.asyncio
    async def test_default_keeps_null_narrowing(self):
        """不传 global_view（既有调用方形态）→ 收窄条件与现在一致（防回归）"""
        cur = _FakeCursor([])
        await self._search(cur, tenant_id=None)

        sql, params = self._select_sql(cur)
        flat = _flat(sql)
        assert "d.tenant_id IS NULL" in flat
        assert params == ["[0.1,0.2,0.3,0.4]", 5]

    @pytest.mark.asyncio
    async def test_tenant_present_takes_priority_over_global_view(self):
        cur = _FakeCursor([])
        await self._search(cur, tenant_id="t1", global_view=True)

        sql, params = self._select_sql(cur)
        flat = _flat(sql)
        assert "(d.tenant_id = %s)" in flat
        assert params == ["[0.1,0.2,0.3,0.4]", "t1", 5]


class TestRetrieveGlobalViewPlumbing:
    """HybridRetriever.retrieve 把 global_view 同时下发到向量与全文两路"""

    @staticmethod
    def _make_retriever():
        from src.knowledge.retriever.hybrid_retriever import HybridRetriever
        embedding_client = MagicMock()
        embedding_client.embed = AsyncMock(return_value=[0.1, 0.2])
        embedding_client.last_usage_tokens = 0
        vector_db = MagicMock()
        vector_db.search = AsyncMock(return_value=[])
        retriever = HybridRetriever(
            vector_db=vector_db, embedding_client=embedding_client, conn=None)
        return retriever, vector_db

    @pytest.mark.asyncio
    async def test_retrieve_passes_global_view_to_both_paths(self):
        from src.knowledge.retriever.hybrid_retriever import HybridRetriever
        retriever, vector_db = self._make_retriever()
        with patch.object(HybridRetriever, "_fts_search", return_value=[]) as fts_mock:
            results = await retriever.retrieve("差旅", top_k=5, tenant_id=None, global_view=True)

        assert results == []
        assert vector_db.search.call_args.kwargs["global_view"] is True
        assert fts_mock.call_args.kwargs["global_view"] is True

    @pytest.mark.asyncio
    async def test_retrieve_default_global_view_false(self):
        from src.knowledge.retriever.hybrid_retriever import HybridRetriever
        retriever, vector_db = self._make_retriever()
        with patch.object(HybridRetriever, "_fts_search", return_value=[]) as fts_mock:
            await retriever.retrieve("差旅", top_k=5, tenant_id=None)

        assert vector_db.search.call_args.kwargs["global_view"] is False
        assert fts_mock.call_args.kwargs["global_view"] is False


class TestSearchGlobalViewPassthrough:
    """service.search_documents 把自身收到的 global_view 透传给检索器（捕获调用 kwargs）"""

    @staticmethod
    def _search_stack(retriever, cur):
        stack = contextlib.ExitStack()
        for p in (
            patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)),
            patch("src.knowledge.service.get_vector_db", return_value=MagicMock()),
            patch("src.knowledge.retriever.hybrid_retriever.HybridRetriever", return_value=retriever),
            patch("src.config.settings.get_embedding_api_key", return_value="test-key"),
        ):
            stack.enter_context(p)
        return stack

    @pytest.mark.asyncio
    async def test_global_view_true_passed_to_retriever(self):
        retriever = MagicMock()
        retriever.retrieve = AsyncMock(return_value=[])
        cur = _FakeCursor([])
        svc = KnowledgeBaseService()
        with self._search_stack(retriever, cur):
            result = await svc.search_documents("q", tenant_id=None, global_view=True)

        assert result["success"] is True
        kwargs = retriever.retrieve.call_args.kwargs
        assert kwargs["global_view"] is True
        assert kwargs["tenant_id"] is None

    @pytest.mark.asyncio
    async def test_default_global_view_false_passed_to_retriever(self):
        retriever = MagicMock()
        retriever.retrieve = AsyncMock(return_value=[])
        cur = _FakeCursor([])
        svc = KnowledgeBaseService()
        with self._search_stack(retriever, cur):
            await svc.search_documents("q", tenant_id=None)

        assert retriever.retrieve.call_args.kwargs["global_view"] is False


def _make_api_request(user_role="unset", path="/api/knowledge/documents", headers=None, method="GET"):
    """构造带 state.user_role 与 headers 的最小 Request 假件（模拟中间件认证后写入）"""
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http", "method": method, "path": path, "raw_path": path.encode(),
        "headers": raw_headers, "query_string": b"", "server": ("testserver", 80),
        "scheme": "http", "http_version": "1.1",
    }
    request = Request(scope)
    if user_role != "unset":
        request.state.user_role = user_role
    return request


class TestIsGlobalAdminViewHelper:
    """三种身份语义的 API 层判定：未认证 None / 普通租户 / platform_admin 全局"""

    def setup_method(self):
        set_tenant_context(None, None)

    def teardown_method(self):
        set_tenant_context(None, None)

    def test_platform_admin_with_none_tenant_is_global(self):
        from src.knowledge.api import _is_global_admin_view
        set_tenant_context(None, "admin-1")
        assert _is_global_admin_view(_make_api_request(user_role="platform_admin")) is True

    def test_platform_admin_with_specified_tenant_not_global(self):
        """admin 经 X-Tenant-Id 代管指定租户 → 该租户作用域，非全局"""
        from src.knowledge.api import _is_global_admin_view
        set_tenant_context("t2", "admin-1")
        assert _is_global_admin_view(_make_api_request(user_role="platform_admin")) is False

    def test_employee_not_global(self):
        from src.knowledge.api import _is_global_admin_view
        set_tenant_context(None, "u1")
        assert _is_global_admin_view(_make_api_request(user_role="employee")) is False

    def test_unauthenticated_not_global(self):
        """未认证（state 无 user_role）→ 收窄"""
        from src.knowledge.api import _is_global_admin_view
        set_tenant_context(None, None)
        assert _is_global_admin_view(_make_api_request()) is False

    def test_regular_tenant_not_global(self):
        """普通租户上下文（如 tenant_test1）→ 该租户作用域，非全局"""
        from src.knowledge.api import _is_global_admin_view
        set_tenant_context("tenant_test1", "u1")
        assert _is_global_admin_view(_make_api_request(user_role="employee")) is False

    def test_missing_request_not_global(self):
        from src.knowledge.api import _is_global_admin_view
        set_tenant_context(None, None)
        assert _is_global_admin_view(None) is False


class TestKnowledgeApiScopeCombination:
    """API 处理器 + 中间件组合：伪造 header 的知识库删除/读取被 403 拦截；admin 指定租户按该租户过滤"""

    @staticmethod
    def _auth_patches(user_row, token_user="user-1"):
        return patch("src.api.auth.verify_token", return_value=token_user), \
            patch("src.saas.middleware.get_db_connection",
                  return_value=_FakeConn(_FakeCursor([user_row])))

    @pytest.mark.asyncio
    async def test_forged_header_delete_blocked_403(self):
        """普通用户伪造他人租户 header → 知识库删除被中间件 403 拦截，service 不被调用"""
        from src.knowledge import api as kb_api
        from src.saas.middleware import TenantContextMiddleware

        request = _make_api_request(
            path="/api/knowledge/documents/7", method="DELETE",
            headers={"Authorization": "Bearer good-token", "X-Tenant-Id": "t2"})
        scope_calls = {"service_called": False}

        async def call_next(req):
            with patch.object(kb_api.knowledge_service, "delete_document", AsyncMock()) as m:
                await kb_api.delete_document(7, http_request=req)
                scope_calls["service_called"] = m.called
            return Response("ok")

        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = self._auth_patches({"role": "employee", "tenant_id": "t1"})
        with verify_patch, db_patch, \
                patch("src.saas.db.tenant_db.TenantDB.get_by_id", return_value={"tenant_id": "t2"}):
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 403
        assert scope_calls["service_called"] is False

    @pytest.mark.asyncio
    async def test_forged_header_chunks_read_blocked_403(self):
        """普通用户伪造他人租户 header → 知识库分块读取被 403 拦截"""
        from src.saas.middleware import TenantContextMiddleware

        request = _make_api_request(
            path="/api/knowledge/documents/7/chunks",
            headers={"Authorization": "Bearer good-token", "X-Tenant-Id": "t2"})
        invoked = {"handler": False}

        async def call_next(req):
            invoked["handler"] = True
            return Response("ok")

        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = self._auth_patches({"role": "tenant_admin", "tenant_id": "t1"})
        with verify_patch, db_patch, \
                patch("src.saas.db.tenant_db.TenantDB.get_by_id", return_value={"tenant_id": "t2"}):
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 403
        assert invoked["handler"] is False

    @pytest.mark.asyncio
    async def test_own_tenant_header_delete_scoped_to_own_tenant(self):
        """普通用户 header==自身租户 → 放行，删除作用域为该租户（global_view=False）"""
        from src.knowledge import api as kb_api
        from src.saas.middleware import TenantContextMiddleware

        request = _make_api_request(
            path="/api/knowledge/documents/7", method="DELETE",
            headers={"Authorization": "Bearer good-token", "X-Tenant-Id": "t1"})
        calls = {}

        async def call_next(req):
            with patch.object(
                kb_api.knowledge_service, "delete_document",
                AsyncMock(return_value={"success": True, "message": "已删除"})
            ) as m:
                resp = await kb_api.delete_document(7, http_request=req)
                calls["kwargs"] = m.call_args.kwargs
            return resp

        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = self._auth_patches({"role": "employee", "tenant_id": "t1"})
        with verify_patch, db_patch:
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["kwargs"]["tenant_id"] == "t1"
        assert calls["kwargs"]["global_view"] is False

    @pytest.mark.asyncio
    async def test_admin_specified_tenant_scoped(self):
        """admin + X-Tenant-Id 指定租户 → 处理器按该租户过滤（global_view=False）"""
        from src.knowledge import api as kb_api

        request = _make_api_request(user_role="platform_admin", path="/api/knowledge/documents")
        calls = {}
        set_tenant_context("t2", "admin-1")
        try:
            with patch.object(kb_api.knowledge_service, "list_documents", MagicMock(return_value=[])) as m_list, \
                 patch.object(kb_api.knowledge_service, "count_documents", MagicMock(return_value=0)):
                await kb_api.list_documents(http_request=request)
                calls["list_kwargs"] = m_list.call_args.kwargs
        finally:
            set_tenant_context(None, None)

        assert calls["list_kwargs"]["tenant_id"] == "t2"
        assert calls["list_kwargs"]["global_view"] is False

    @pytest.mark.asyncio
    async def test_platform_admin_global_list_view(self):
        """认证 platform_admin 无 X-Tenant-Id → 全局视图（tenant None + global_view=True）"""
        from src.knowledge import api as kb_api

        request = _make_api_request(user_role="platform_admin", path="/api/knowledge/documents")
        calls = {}
        set_tenant_context(None, "admin-1")
        try:
            with patch.object(kb_api.knowledge_service, "list_documents", MagicMock(return_value=[])) as m_list, \
                 patch.object(kb_api.knowledge_service, "count_documents", MagicMock(return_value=0)):
                await kb_api.list_documents(http_request=request)
                calls["list_kwargs"] = m_list.call_args.kwargs
        finally:
            set_tenant_context(None, None)

        assert calls["list_kwargs"]["tenant_id"] is None
        assert calls["list_kwargs"]["global_view"] is True

    @pytest.mark.asyncio
    async def test_unauthenticated_list_stays_narrowed(self):
        """未认证（无 user_role）→ global_view=False，保持无主文档收窄"""
        from src.knowledge import api as kb_api

        request = _make_api_request(path="/api/knowledge/documents")
        calls = {}
        set_tenant_context(None, None)
        try:
            with patch.object(kb_api.knowledge_service, "list_documents", MagicMock(return_value=[])) as m_list, \
                 patch.object(kb_api.knowledge_service, "count_documents", MagicMock(return_value=0)):
                await kb_api.list_documents(http_request=request)
                calls["list_kwargs"] = m_list.call_args.kwargs
        finally:
            set_tenant_context(None, None)

        assert calls["list_kwargs"]["tenant_id"] is None
        assert calls["list_kwargs"]["global_view"] is False

    @pytest.mark.asyncio
    async def test_search_global_view_request_reaches_retriever(self):
        """api 层：_is_global_admin_view=True 的搜索请求 → 真实 service 链路把
        global_view=True 传到检索层（mock retriever 捕获 kwargs；检索层 SQL 由
        TestFtsGlobalViewSQL/TestVectorSearchGlobalViewSQL 真实覆盖）"""
        from src.knowledge import api as kb_api
        from src.knowledge.api import SearchRequest

        retriever = MagicMock()
        retriever.retrieve = AsyncMock(return_value=[
            {"doc_id": 11, "chunk_id": 111, "text": "片段", "score": 0.9}])
        cur = _FakeCursor(rows=[{"id": 11, "title": "x", "file_type": "txt", "file_path": "/tmp/x.txt"}])
        request = _make_api_request(
            user_role="platform_admin", path="/api/knowledge/search_documents", method="POST")

        set_tenant_context(None, "admin-1")
        try:
            with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)), \
                 patch("src.knowledge.service.get_vector_db", return_value=MagicMock()), \
                 patch("src.knowledge.retriever.hybrid_retriever.HybridRetriever", return_value=retriever), \
                 patch("src.config.settings.get_embedding_api_key", return_value="test-key"):
                resp = await kb_api.search_documents(SearchRequest(query="差旅"), http_request=request)
        finally:
            set_tenant_context(None, None)

        assert resp.success is True
        kwargs = retriever.retrieve.call_args.kwargs
        assert kwargs["global_view"] is True
        assert kwargs["tenant_id"] is None
        # 标题回查同样不携带租户过滤
        sql, params = cur.executed[0]
        assert "tenant_id" not in _flat(sql)
        assert params == [11]

    @pytest.mark.asyncio
    async def test_search_non_global_request_retriever_narrowed(self):
        """api 层：普通用户（非全局视图）搜索 → 检索层 global_view=False"""
        from src.knowledge import api as kb_api
        from src.knowledge.api import SearchRequest

        retriever = MagicMock()
        retriever.retrieve = AsyncMock(return_value=[])
        cur = _FakeCursor([])
        request = _make_api_request(
            user_role="employee", path="/api/knowledge/search_documents", method="POST")

        set_tenant_context(None, "u1")
        try:
            with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)), \
                 patch("src.knowledge.service.get_vector_db", return_value=MagicMock()), \
                 patch("src.knowledge.retriever.hybrid_retriever.HybridRetriever", return_value=retriever), \
                 patch("src.config.settings.get_embedding_api_key", return_value="test-key"):
                resp = await kb_api.search_documents(SearchRequest(query="差旅"), http_request=request)
        finally:
            set_tenant_context(None, None)

        assert resp.success is True
        assert retriever.retrieve.call_args.kwargs["global_view"] is False
