"""知识库文档可见性与外部文档边界单元测试（无 PG 环境可跑，SQL 捕获断言）。

公众号内容入知识库 WP2（设计 §7.3/§7.4）：
- 检索三分支（本租户/全局/无主，向量 + FTS）在排序/LIMIT 前过滤
  status='active' 且未过期；OR 组合的租户范围条件必须整体加括号，
  否则可见性条件只约束最后一个 OR 分支（本租户 deleted 文档会泄漏）
- list/count 默认隐藏 deleted，include_deleted=True 放行；origin 过滤
- chunks 默认隐藏 deleted 文档，include_deleted 放行
- 外部来源文档（origin != 'manual_upload'）禁物理删除、禁移动（含批量）
- 外部来源无本地文件文档下载/票据返回明确错误 + metadata.original_url
"""
import json
from pathlib import Path
import importlib.util
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

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


class _SeqCursor:
    """按 fetchone 调用顺序返回预置结果的假游标（move/delete 多步查询用）"""

    def __init__(self, fetchone_results=None):
        self._fetchone_results = list(fetchone_results or [])
        self.executed = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return []

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *args, **kwargs):
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
    spec = importlib.util.spec_from_file_location("_real_vector_db_for_visibility_test", str(file_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


VISIBILITY_FRAGMENT = "d.status = 'active'"
EXPIRY_FRAGMENT = "d.expires_at IS NULL OR d.expires_at > now()"


class TestFtsVisibilitySQL:
    """FTS 检索三分支均带 active/未过期过滤，租户范围 OR 条件整体加括号"""

    @staticmethod
    def _make_retriever(cur):
        from src.knowledge.retriever.hybrid_retriever import HybridRetriever
        return HybridRetriever(
            vector_db=MagicMock(), embedding_client=MagicMock(), conn=_FakeConn(cur))

    def test_tenant_branch_visibility_and_wrapped_range(self):
        cur = _FakeCursor([])
        self._make_retriever(cur)._postgres_fts_search(
            "差旅", 5, tenant_id="t1", shared_ranges=[("t2", "policy")])

        sql, params = cur.executed[0]
        flat = _flat(sql)
        assert VISIBILITY_FRAGMENT in flat
        assert EXPIRY_FRAGMENT in flat
        # OR 组合整体括号包裹，且可见性条件在 ORDER BY/LIMIT 之前
        assert "AND ((d.tenant_id = %s) OR (d.tenant_id = %s AND d.source_type = %s))" in flat
        assert flat.index(VISIBILITY_FRAGMENT) < flat.index("ORDER BY")
        # 参数顺序不变：查询词×2 + 租户范围参数 + LIMIT
        assert params == ["差旅", "差旅", "t1", "t2", "policy", 5]

    def test_global_view_branch_visibility(self):
        cur = _FakeCursor([])
        self._make_retriever(cur)._postgres_fts_search("差旅", 5, tenant_id=None, global_view=True)

        sql, params = cur.executed[0]
        flat = _flat(sql)
        assert VISIBILITY_FRAGMENT in flat
        assert EXPIRY_FRAGMENT in flat
        assert "tenant_id" not in flat  # 全局视图仍无租户收窄
        assert params == ["差旅", "差旅", 5]

    def test_none_tenant_branch_visibility(self):
        cur = _FakeCursor([])
        self._make_retriever(cur)._postgres_fts_search("差旅", 5, tenant_id=None)

        sql, params = cur.executed[0]
        flat = _flat(sql)
        assert "d.tenant_id IS NULL" in flat
        assert VISIBILITY_FRAGMENT in flat
        assert EXPIRY_FRAGMENT in flat
        assert params == ["差旅", "差旅", 5]


class TestVectorVisibilitySQL:
    """向量检索三分支均带 active/未过期过滤"""

    @staticmethod
    async def _search(cur, **kwargs):
        vdb_cls = _load_real_vector_db().VectorDBPostgreSQL
        vdb = vdb_cls(dimension=4, conn=_FakeConn(cur))
        return await vdb.search([0.1, 0.2, 0.3, 0.4], top_k=5, **kwargs)

    @staticmethod
    def _select_sql(cur):
        selects = [(s, p) for s, p in cur.executed if _flat(s).upper().startswith("SELECT")]
        assert len(selects) == 1
        return selects[0]

    @pytest.mark.asyncio
    async def test_tenant_branch_visibility_and_wrapped_range(self):
        cur = _FakeCursor([])
        await self._search(cur, tenant_id="t1", shared_ranges=[("t2", "policy")])

        sql, params = self._select_sql(cur)
        flat = _flat(sql)
        assert VISIBILITY_FRAGMENT in flat
        assert EXPIRY_FRAGMENT in flat
        assert "WHERE ((d.tenant_id = %s) OR (d.tenant_id = %s AND d.source_type = %s))" in flat
        assert flat.index(VISIBILITY_FRAGMENT) < flat.index("ORDER BY")
        assert params == ["[0.1,0.2,0.3,0.4]", "t1", "t2", "policy", 5]

    @pytest.mark.asyncio
    async def test_global_view_branch_visibility(self):
        cur = _FakeCursor([])
        await self._search(cur, tenant_id=None, global_view=True)

        sql, params = self._select_sql(cur)
        flat = _flat(sql)
        assert VISIBILITY_FRAGMENT in flat
        assert "tenant_id" not in flat
        assert params == ["[0.1,0.2,0.3,0.4]", 5]

    @pytest.mark.asyncio
    async def test_none_tenant_branch_visibility(self):
        cur = _FakeCursor([])
        await self._search(cur, tenant_id=None)

        sql, params = self._select_sql(cur)
        flat = _flat(sql)
        assert "d.tenant_id IS NULL" in flat
        assert VISIBILITY_FRAGMENT in flat
        assert params == ["[0.1,0.2,0.3,0.4]", 5]


class TestListCountVisibility:
    """list/count 默认过滤 deleted；include_deleted 放行；origin 过滤"""

    def test_list_default_filters_deleted(self):
        _, executed = _run("list_documents", tenant_id="t1")
        sql = _flat(executed[0][0])
        assert "status = 'active'" in sql
        assert "expires_at IS NULL" not in sql  # 过期只限检索，不过滤管理端列表
        assert executed[0][1] == ["t1"]

    def test_list_include_deleted_omits_status_filter(self):
        _, executed = _run("list_documents", tenant_id="t1", include_deleted=True)
        assert "status = 'active'" not in _flat(executed[0][0])

    def test_list_origin_filter(self):
        _, executed = _run("list_documents", tenant_id="t1", origin="wechat_mp")
        sql, params = executed[0]
        assert "origin = %s" in _flat(sql)
        assert params == ["t1", "wechat_mp"]

    def test_count_default_filters_deleted(self):
        _, executed = _run("count_documents", rows=[{"count": 0}], tenant_id="t1")
        assert "status = 'active'" in _flat(executed[0][0])

    def test_count_include_deleted_omits_status_filter(self):
        _, executed = _run("count_documents", rows=[{"count": 0}], tenant_id="t1", include_deleted=True)
        assert "status" not in _flat(executed[0][0])

    def test_count_origin_filter_global_view(self):
        _, executed = _run(
            "count_documents", rows=[{"count": 0}],
            tenant_id=None, global_view=True, origin="wechat_mp")
        sql, params = executed[0]
        flat = _flat(sql)
        assert "origin = %s" in flat
        assert "status = 'active'" in flat
        assert params == ["wechat_mp"]


class TestChunksVisibility:
    """chunks 默认隐藏 deleted 文档；include_deleted 放行；参数不变"""

    def test_default_filters_deleted(self):
        _, executed = _run("get_document_chunks", 7, tenant_id="t1")
        sql, params = executed[0]
        flat = _flat(sql)
        assert "d.status = 'active'" in flat
        assert params == ["t1", 7]

    def test_include_deleted_omits_status_filter(self):
        _, executed = _run("get_document_chunks", 7, tenant_id="t1", include_deleted=True)
        sql, params = executed[0]
        assert "status" not in _flat(sql)
        assert params == ["t1", 7]


class TestExternalDocWriteBoundary:
    """origin != 'manual_upload' 的文档禁物理删除/移动（含批量），platform_admin 亦然"""

    @pytest.mark.asyncio
    async def test_delete_external_doc_rejected(self):
        cur = _FakeCursor(rows=[{"file_path": None, "origin": "wechat_mp"}])
        svc = KnowledgeBaseService()
        with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)):
            result = await svc.delete_document(7, tenant_id="t1")

        assert result["success"] is False
        assert result["status"] == 400
        assert "同步任务管理" in result["error"]
        # 只执行了 SELECT，未触碰 DELETE
        assert len(cur.executed) == 1

    @pytest.mark.asyncio
    async def test_delete_external_doc_rejected_even_global_view(self):
        """platform_admin 全局视图同样不可物理删除外部文档（审计只读）"""
        cur = _FakeCursor(rows=[{"file_path": None, "origin": "wechat_mp"}])
        svc = KnowledgeBaseService()
        with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)):
            result = await svc.delete_document(7, tenant_id=None, global_view=True)

        assert result["success"] is False
        assert result["status"] == 400
        assert len(cur.executed) == 1

    @pytest.mark.asyncio
    async def test_delete_manual_doc_unchanged(self):
        """手动上传文档删除行为零变化（origin 缺省/显式 manual_upload 均放行）"""
        for row in ({"file_path": "nonexistent_kb_guard.txt", "origin": "manual_upload"},
                    {"file_path": "nonexistent_kb_guard.txt", "origin": None}):
            cur = _FakeCursor(rows=[row])
            svc = KnowledgeBaseService()
            with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)), \
                 patch("src.knowledge.service.get_vector_db", return_value=AsyncMock()):
                result = await svc.delete_document(7, tenant_id="t1")
            assert result["success"] is True

    def test_move_external_doc_rejected(self):
        """批量移动任一命中外部文档 → 整批拒绝（在 UPDATE 之前）"""
        # fetchone 序列：目标顶级分类存在 → 外部文档计数 >0
        cur = _SeqCursor(fetchone_results=[{"?column?": 1}, {"c": 2}])
        svc = KnowledgeBaseService()
        with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)):
            result = svc.move_documents("t1", [7, 8], "policy", None)

        assert result["success"] is False
        assert result["status"] == 400
        assert "同步任务管理" in result["error"]
        sqls = [_flat(s) for s, _ in cur.executed]
        assert not any(s.upper().startswith("UPDATE DOCUMENTS") for s in sqls)

    def test_move_manual_docs_unchanged(self):
        """手动上传文档批量移动行为零变化"""
        cur = _SeqCursor(fetchone_results=[{"?column?": 1}, {"c": 0}])
        cur.rowcount = 2
        svc = KnowledgeBaseService()
        with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)):
            result = svc.move_documents("t1", [7, 8], "policy", None)

        assert result["success"] is True
        assert result["moved"] == 2
        assert any(_flat(s).upper().startswith("UPDATE DOCUMENTS") for s, _ in cur.executed)


class TestApiListIncludeDeleted:
    """include_deleted 仅 platform_admin 生效；其他角色静默忽略（同 global_view 收窄风格）"""

    def setup_method(self):
        set_tenant_context(None, None)

    def teardown_method(self):
        set_tenant_context(None, None)

    @staticmethod
    async def _call(user_role, **query):
        from src.knowledge import api as kb_api
        request = _make_api_request(user_role=user_role)
        calls = {}
        with patch.object(kb_api.knowledge_service, "list_documents", MagicMock(return_value=[])) as m_list, \
             patch.object(kb_api.knowledge_service, "count_documents", MagicMock(return_value=0)) as m_count:
            await kb_api.list_documents(http_request=request, **query)
            calls["list"] = m_list.call_args.kwargs
            calls["count"] = m_count.call_args.kwargs
        return calls

    @pytest.mark.asyncio
    async def test_platform_admin_include_deleted_honored(self):
        set_tenant_context(None, "admin-1")
        calls = await self._call("platform_admin", include_deleted=True)
        assert calls["list"]["include_deleted"] is True
        assert calls["count"]["include_deleted"] is True

    @pytest.mark.asyncio
    async def test_employee_include_deleted_ignored(self):
        set_tenant_context("t1", "u1")
        calls = await self._call("employee", include_deleted=True)
        assert calls["list"]["include_deleted"] is False
        assert calls["count"]["include_deleted"] is False

    @pytest.mark.asyncio
    async def test_default_include_deleted_false(self):
        set_tenant_context("t1", "u1")
        calls = await self._call("employee")
        assert calls["list"]["include_deleted"] is False

    @pytest.mark.asyncio
    async def test_origin_filter_passthrough(self):
        set_tenant_context("t1", "u1")
        calls = await self._call("employee", origin="wechat_mp")
        assert calls["list"]["origin"] == "wechat_mp"
        assert calls["count"]["origin"] == "wechat_mp"


def _patch_doc_row(row):
    """把 knowledge_service._get_db_connection 替换为返回指定文档行的假连接"""
    return patch.object(
        KnowledgeBaseService, "_get_db_connection",
        return_value=_FakeConn(_FakeCursor([row])))


class TestDownloadExternalBoundary:
    """外部来源无文件文档：下载/票据返回明确错误 + metadata.original_url；
    软删除文档仅 platform_admin 可审计下载"""

    EXT_URL = "https://mp.weixin.qq.com/s/abc123"

    def setup_method(self):
        set_tenant_context(None, None)

    def teardown_method(self):
        set_tenant_context(None, None)

    def _external_row(self, **overrides):
        row = {
            "file_path": None, "title": "公众号文章", "tenant_id": "t1",
            "source_type": "wechat_mp", "origin": "wechat_mp", "status": "active",
            "metadata": json.dumps({"original_url": self.EXT_URL}),
        }
        row.update(overrides)
        return row

    @pytest.mark.asyncio
    async def test_download_external_no_file_400_with_original_url(self):
        from fastapi import HTTPException
        from src.knowledge import api as kb_api

        set_tenant_context("t1", "u1")
        with _patch_doc_row(self._external_row()):
            with pytest.raises(HTTPException) as exc_info:
                await kb_api.download_document(7, http_request=_make_api_request(user_role="employee"))

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["original_url"] == self.EXT_URL
        assert "原文" in exc_info.value.detail["error"]

    @pytest.mark.asyncio
    async def test_download_ticket_external_no_file_400_with_original_url(self):
        from src.knowledge import api as kb_api

        set_tenant_context("t1", "u1")
        with _patch_doc_row(self._external_row()):
            resp = await kb_api.create_download_ticket(7, http_request=_make_api_request(user_role="employee"))

        assert resp.status_code == 400
        body = json.loads(bytes(resp.body))
        assert body["original_url"] == self.EXT_URL
        assert "原文" in body["error"]

    @pytest.mark.asyncio
    async def test_download_external_with_url_as_file_path_still_400(self):
        """WP12 定版：外部文档 file_path 存原文链接（文档位置字段）——下载仍按
        origin 拦截返回 400+原文链接，不得把 URL 当本地文件路径打开（防泄漏进报错）。"""
        from fastapi import HTTPException
        from src.knowledge import api as kb_api

        set_tenant_context("t1", "u1")
        with _patch_doc_row(self._external_row(file_path=self.EXT_URL)):
            with pytest.raises(HTTPException) as exc_info:
                await kb_api.download_document(7, http_request=_make_api_request(user_role="employee"))

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["original_url"] == self.EXT_URL
        assert "原文" in exc_info.value.detail["error"]

    @pytest.mark.asyncio
    async def test_download_ticket_external_with_url_as_file_path_still_400(self):
        """同上：票据端点对 file_path=原文链接 的外部文档仍按 origin 拦截。"""
        from src.knowledge import api as kb_api

        set_tenant_context("t1", "u1")
        with _patch_doc_row(self._external_row(file_path=self.EXT_URL)):
            resp = await kb_api.create_download_ticket(7, http_request=_make_api_request(user_role="employee"))

        assert resp.status_code == 400
        body = json.loads(bytes(resp.body))
        assert body["original_url"] == self.EXT_URL
        assert "原文" in body["error"]

    @pytest.mark.asyncio
    async def test_download_deleted_doc_hidden_from_tenant(self):
        """deleted 文档租户侧下载按不存在处理（不泄漏存在性）"""
        from fastapi import HTTPException
        from src.knowledge import api as kb_api

        set_tenant_context("t1", "u1")
        row = self._external_row(origin="manual_upload", file_path="/tmp/x.txt", status="deleted")
        with _patch_doc_row(row):
            with pytest.raises(HTTPException) as exc_info:
                await kb_api.download_document(7, http_request=_make_api_request(user_role="employee"))

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_download_ticket_deleted_doc_platform_admin_allowed(self):
        """deleted 文档 platform_admin 可审计：票据正常签发"""
        from src.knowledge import api as kb_api

        set_tenant_context(None, "admin-1")
        row = self._external_row(origin="manual_upload", file_path="/tmp/x.txt",
                                 tenant_id="t1", status="deleted")
        with _patch_doc_row(row), \
             patch("src.core.download_ticket.issue_download_ticket", return_value="tok") as m_issue:
            resp = await kb_api.create_download_ticket(
                7, http_request=_make_api_request(user_role="platform_admin"))

        assert resp["ticket"] == "tok"
        assert m_issue.called
        # 票据携带角色：middleware 经票据还原 user_role，admin 审计链路不失效
        assert m_issue.call_args.kwargs["role"] == "platform_admin"

    @pytest.mark.asyncio
    async def test_download_manual_doc_unchanged(self, tmp_path):
        """手动上传 active 文档下载行为零变化"""
        from fastapi.responses import FileResponse
        from src.knowledge import api as kb_api

        f = tmp_path / "kb_manual.txt"
        f.write_text("正文", encoding="utf-8")
        set_tenant_context("t1", "u1")
        row = self._external_row(origin="manual_upload", file_path=str(f),
                                 metadata=None)
        with _patch_doc_row(row):
            resp = await kb_api.download_document(7, http_request=_make_api_request(user_role="employee"))

        assert isinstance(resp, FileResponse)
