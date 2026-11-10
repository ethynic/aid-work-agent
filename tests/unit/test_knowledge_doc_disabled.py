"""知识库文档禁用/启用功能单元测试（无 PG 环境可跑，SQL 捕获断言）。

覆盖：
- build_active_document_condition 含 disabled 过滤（模式 A 收口点，
  自动覆盖 hybrid_retriever/vector_db/search_documents_by_title/跨租户共享）
- 模式 B 工具（hotel/attraction_search_tool）检索 SQL 含 disabled 过滤
- 模式 C 脚本（travel-quote hotel/attraction retriever）检索 SQL 含 disabled 过滤
- KnowledgeBaseService.set_document_disabled 租户 scope（命中/跨租户不可见/全局视图/异常）
"""
from unittest.mock import MagicMock, patch

import pytest

from src.knowledge.retriever.tenant_range import build_active_document_condition
from src.knowledge.service import KnowledgeBaseService


class TestActiveDocumentCondition:
    """模式 A 收口点：可见性条件必须排除禁用文档"""

    def test_contains_disabled_filter(self):
        sql = build_active_document_condition()
        assert "disabled IS NOT TRUE" in sql
        assert "status = 'active'" in sql
        assert "expires_at IS NULL OR" in sql

    def test_alias_substitution(self):
        sql = build_active_document_condition("doc")
        assert "doc.disabled IS NOT TRUE" in sql
        assert "doc.status = 'active'" in sql


class _FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows=None):
        self.cursor_obj = _FakeCursor(rows)

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestSetDocumentDisabled:
    """service 层禁用/启用：租户 scope 与 delete_document 同口径"""

    def _svc(self, rows=None):
        return KnowledgeBaseService.__new__(KnowledgeBaseService)

    def test_tenant_scoped_update(self):
        svc = self._svc()
        fake = _FakeConn(rows=[{"id": 7, "title": "过时文档", "disabled": True}])
        with patch.object(svc, "_get_db_connection", return_value=fake):
            row = svc.set_document_disabled(7, True, tenant_id="t1")
        assert row == {"id": 7, "title": "过时文档", "disabled": True}
        sql, params = fake.cursor_obj.executed[0]
        assert "UPDATE documents" in sql
        assert "SET disabled = %s" in sql
        assert "tenant_id = %s" in sql
        assert "RETURNING" in sql
        assert params == [True, 7, "t1"]

    def test_cross_tenant_returns_none(self):
        svc = self._svc()
        fake = _FakeConn(rows=[])
        with patch.object(svc, "_get_db_connection", return_value=fake):
            row = svc.set_document_disabled(7, True, tenant_id="t1")
        assert row is None

    def test_no_tenant_context_narrows_to_ownerless(self):
        svc = self._svc()
        fake = _FakeConn(rows=[])
        with patch.object(svc, "_get_db_connection", return_value=fake):
            svc.set_document_disabled(7, False, tenant_id=None, global_view=False)
        sql, _ = fake.cursor_obj.executed[0]
        assert "tenant_id IS NULL" in sql

    def test_global_view_no_tenant_filter(self):
        svc = self._svc()
        fake = _FakeConn(rows=[])
        with patch.object(svc, "_get_db_connection", return_value=fake):
            svc.set_document_disabled(7, True, tenant_id=None, global_view=True)
        sql, _ = fake.cursor_obj.executed[0]
        assert "1=1" in sql

    def test_db_error_returns_none(self):
        svc = self._svc()
        with patch.object(svc, "_get_db_connection", side_effect=RuntimeError("db down")):
            row = svc.set_document_disabled(7, True, tenant_id="t1")
        assert row is None


class _ToolFakeConn:
    """捕获第一条 SQL 并返回预设行（覆盖 hotel_search_tool 检索步 + 价格明细步）"""

    def __init__(self, first_rows=None):
        self._first_rows = first_rows or []
        self.sqls = []

    def cursor(self):
        conn = self

        class _C:
            def execute(self, sql, params=None):
                conn.sqls.append((sql, params))

            def fetchall(self):
                return conn._first_rows

            def fetchone(self):
                return None

        return _C()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.tools
class TestModeBToolSQL:
    """模式 B 工具检索 SQL 必须带 disabled 过滤（顺带补齐 active 过滤缺口）"""

    @pytest.mark.asyncio
    async def test_hotel_search_tool_sql(self):
        from src.tools.knowledge.hotel_search_tool import HotelSearchTool

        tool = HotelSearchTool()
        fake = _ToolFakeConn(first_rows=[{
            "doc_id": 1, "text": "酒店信息", "title": "酒店",
            "metadata": {}, "file_path": "",
        }])
        with patch.object(tool, "_resolve_tenant_id", return_value="t1"), \
             patch.object(tool, "_resolve_subagent_id", return_value=None), \
             patch("src.tools.knowledge.hotel_search_tool.get_db_connection", return_value=fake):
            result = await tool.execute(query="酒店")
        assert result.get("success") is True
        first_sql = fake.sqls[0][0]
        assert "disabled IS NOT TRUE" in first_sql
        assert "status = 'active'" in first_sql

    @pytest.mark.asyncio
    async def test_attraction_search_tool_sql(self):
        from types import SimpleNamespace
        from src.tools.knowledge.attraction_search_tool import AttractionSearchTool
        from src.tools import context as tool_context

        tool = AttractionSearchTool()
        fake = _ToolFakeConn(first_rows=[{
            "doc_id": 2, "text": "景点信息", "title": "景点",
            "metadata": {}, "file_path": "",
        }])
        fake_ctx = SimpleNamespace(tenant_id="t1", subagent_id=None)
        fake_client = SimpleNamespace(reset_usage=lambda: None, last_usage_tokens=0, model="test")
        with patch.object(tool_context, "current_tool_execution_context", return_value=fake_ctx), \
             patch.object(tool, "_embed", return_value=[0.0] * 4), \
             patch.object(tool, "_get_embedding_client", return_value=fake_client), \
             patch("src.tools.knowledge.attraction_search_tool.get_db_connection", return_value=fake), \
             patch("src.tools.knowledge.attraction_search_tool.get_image_registry", return_value=MagicMock()):
            await tool.execute(query="景点")
        first_sql = fake.sqls[0][0]
        assert "disabled IS NOT TRUE" in first_sql
        assert "status = 'active'" in first_sql


class _ScriptFakeConn:
    """travel-quote retriever 脚本专用 FakeConn（记录最后一条 SQL）"""

    def __init__(self, rows=None):
        self.last_sql = None
        self.last_args = None
        self._rows = rows or []

    def execute(self, sql, args=None):
        self.last_sql = sql
        self.last_args = args

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestModeCScriptSQL:
    """模式 C 脚本（独立运行不引 src 模块）检索 SQL 必须带 disabled 过滤"""

    def test_hotel_retriever_sql(self):
        import sys as _sys
        from pathlib import Path as _Path

        scripts_dir = _Path(__file__).resolve().parents[2] / "src" / "skills" / "travel-quote" / "scripts"
        if str(scripts_dir) not in _sys.path:
            _sys.path.insert(0, str(scripts_dir))
        import hotel_retriever

        retriever = hotel_retriever.HotelRetriever()
        fake = _ScriptFakeConn()
        with patch.object(retriever, "_get_conn", return_value=fake):
            retriever.search_by_name("t1", "酒店")
        assert "disabled IS NOT TRUE" in fake.last_sql
        assert "status = 'active'" in fake.last_sql

    def test_attraction_retriever_sql(self):
        import sys as _sys
        from pathlib import Path as _Path

        scripts_dir = _Path(__file__).resolve().parents[2] / "src" / "skills" / "travel-quote" / "scripts"
        if str(scripts_dir) not in _sys.path:
            _sys.path.insert(0, str(scripts_dir))
        import attraction_retriever

        retriever = attraction_retriever.AttractionRetriever()
        fake = _ScriptFakeConn()
        with patch.object(retriever, "_get_conn", return_value=fake):
            retriever.search_by_name("t1", "景点")
        assert "disabled IS NOT TRUE" in fake.last_sql
        assert "status = 'active'" in fake.last_sql
