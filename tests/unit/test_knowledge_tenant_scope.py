"""知识库租户作用域 SQL 单元测试（无 PG 环境可跑）。

安全加固设计 §2.4：list/count/delete/chunks 的租户作用域必须在 SQL 本体携带；
无租户上下文（None）收窄到 demo/无主文档（与检索侧 vector_db/hybrid_retriever
及下载侧 _can_download_document 口径一致）。本测试用假连接捕获 execute 的 SQL
文本与参数，离线断言收窄条件与占位符/参数一致性。
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.knowledge.service import KnowledgeBaseService


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

    def cursor(self):
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


class TestListCountTenantScope:
    def test_list_none_tenant_narrowed_to_demo_or_null(self):
        _, executed = _run("list_documents", tenant_id=None)
        sql = _flat(executed[0][0])
        assert "tenant_id = 'demo' OR tenant_id IS NULL" in sql
        assert "tenant_id = %s" not in sql
        assert executed[0][1] == []  # 无租户参数

    def test_count_none_tenant_narrowed_to_demo_or_null(self):
        _, executed = _run("count_documents", rows=[{"count": 0}], tenant_id=None)
        sql = _flat(executed[0][0])
        assert "tenant_id = 'demo' OR tenant_id IS NULL" in sql
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
    def test_get_chunks_none_tenant_joins_demo_or_null(self):
        _, executed = _run("get_document_chunks", 7, tenant_id=None)
        sql, params = executed[0]
        flat = _flat(sql)
        assert "JOIN documents d ON d.id = c.doc_id" in flat
        assert "(d.tenant_id = 'demo' OR d.tenant_id IS NULL)" in flat
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
            assert "tenant_id = 'demo' OR tenant_id IS NULL" in sql
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
