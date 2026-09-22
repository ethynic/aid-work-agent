"""知识库文档列表 include_subcategories（不含子栏目）单元测试。

list_documents / count_documents 新增 include_subcategories 参数：
- True（默认）：保持原语义，子分类展开为自身+所有后代（sub_category = ANY），
  顶级分类下含全部子级文档
- False（不含子栏目）：子分类精确匹配（不展开后代）；顶级分类只统计
  直接挂载（sub_category IS NULL）的文档

用假连接捕获 execute 的 SQL 文本与参数，离线断言（无 PG 环境可跑），
模式参考 test_knowledge_tenant_scope.py。
"""
from unittest.mock import patch

from src.knowledge.service import KnowledgeBaseService


class _FakeCursor:
    def __init__(self, rows=None, row_sets=None):
        # row_sets：按 execute 调用序返回不同行集（如先分类表后文档表）；rows：所有调用统一返回
        self._row_sets = list(row_sets) if row_sets is not None else None
        self._rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        if self._row_sets is not None:
            return self._row_sets.pop(0) if self._row_sets else []
        return self._rows

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None


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


def _run(method, *args, rows=None, row_sets=None, **kwargs):
    cur = _FakeCursor(rows, row_sets)
    svc = KnowledgeBaseService()
    with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)):
        result = getattr(svc, method)(*args, **kwargs)
    return result, cur.executed


def _flat(sql):
    return " ".join(str(sql).split())


DOC_ROW = [{"id": 1, "created_at": None, "expires_at": None}]


class TestListDocumentsIncludeSubcategories:
    def test_default_expands_subtree(self):
        """默认（含子栏目）：子分类展开为 ANY，先查分类表收集后代"""
        cat_rows = [
            {"id": 1, "source_type": "a", "parent_id": None},
            {"id": 2, "source_type": "a1", "parent_id": 1},
            {"id": 3, "source_type": "a2", "parent_id": 1},
            {"id": 4, "source_type": "a11", "parent_id": 2},
        ]
        _, executed = _run(
            "list_documents", tenant_id="t1", source_type="a", sub_category="a1",
            row_sets=[cat_rows, DOC_ROW]
        )
        # 第一次 execute 是分类子树查询，第二次是文档列表
        assert len(executed) == 2
        assert "knowledge_categories" in executed[0][0]
        sql = _flat(executed[1][0])
        assert "sub_category = ANY(" in sql
        # 子树展开应包含自身 + 所有后代（不含兄弟分类 a2）
        assert executed[1][1][-1] == ["a1", "a11"]

    def test_direct_sub_category_exact_match(self):
        """不含子栏目：子分类精确匹配，不查分类表、不展开后代，租户过滤保留"""
        _, executed = _run(
            "list_documents", tenant_id="t1", source_type="a", sub_category="a1",
            include_subcategories=False, rows=DOC_ROW
        )
        assert len(executed) == 1
        sql = _flat(executed[0][0])
        assert "tenant_id = %s" in sql
        assert "sub_category = ANY(" not in sql
        assert "sub_category = %s" in sql
        assert executed[0][1][-1] == "a1"

    def test_direct_top_level_null_sub_category(self):
        """不含子栏目：仅选顶级分类时只返回直接挂载（sub_category IS NULL）"""
        _, executed = _run(
            "list_documents", tenant_id="t1", source_type="a",
            include_subcategories=False, rows=DOC_ROW
        )
        sql = _flat(executed[0][0])
        assert "sub_category IS NULL" in sql
        assert "source_type = %s" in sql

    def test_default_top_level_no_null_condition(self):
        """默认（含子栏目）：仅选顶级分类时不附加 IS NULL 条件"""
        _, executed = _run(
            "list_documents", tenant_id="t1", source_type="a", rows=DOC_ROW
        )
        sql = _flat(executed[0][0])
        assert "sub_category IS NULL" not in sql


class TestCountDocumentsIncludeSubcategories:
    def test_default_expands_subtree(self):
        cat_rows = [
            {"id": 1, "source_type": "a", "parent_id": None},
            {"id": 2, "source_type": "a1", "parent_id": 1},
        ]
        _, executed = _run(
            "count_documents", tenant_id="t1", source_type="a", sub_category="a1",
            row_sets=[cat_rows, [{"count": 3}]]
        )
        assert len(executed) == 2
        sql = _flat(executed[1][0])
        assert "sub_category = ANY(" in sql
        assert executed[1][1][-1] == ["a1"]

    def test_direct_sub_category_exact_match(self):
        _, executed = _run(
            "count_documents", tenant_id="t1", source_type="a", sub_category="a1",
            include_subcategories=False, rows=[{"count": 1}]
        )
        assert len(executed) == 1
        sql = _flat(executed[0][0])
        assert "sub_category = ANY(" not in sql
        assert "sub_category = %s" in sql
        assert executed[0][1][-1] == "a1"

    def test_direct_top_level_null_sub_category(self):
        _, executed = _run(
            "count_documents", tenant_id="t1", source_type="a",
            include_subcategories=False, rows=[{"count": 0}]
        )
        sql = _flat(executed[0][0])
        assert "sub_category IS NULL" in sql

    def test_default_top_level_no_null_condition(self):
        _, executed = _run(
            "count_documents", tenant_id="t1", source_type="a", rows=[{"count": 5}]
        )
        sql = _flat(executed[0][0])
        assert "sub_category IS NULL" not in sql
