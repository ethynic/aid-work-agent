"""知识库栏目移动（move_category）单元测试。

规则：
- 顶级分类不可移动（其 source_type 是 documents.source_type 与授权配置的锚点）
- 目标不能是自身或其子孙
- 同顶级移动：仅改 parent_id，子树文档零级联
- 跨顶级移动：子树文档的 source_type（恒存顶级代号）批量回填为新顶级代号

用假连接捕获 execute 的 SQL 文本与参数，离线断言（无 PG 环境可跑），
模式参考 test_knowledge_include_subcategories.py。
"""
from unittest.mock import patch

from src.knowledge.service import KnowledgeBaseService


class _MoveFakeCursor:
    """move_category 专用假游标：fetchall 返回分类全表；rowcount 按 SQL 区分"""

    def __init__(self, cat_rows, doc_rowcount=0):
        self._cat_rows = cat_rows
        self._doc_rowcount = doc_rowcount
        self.executed = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "UPDATE documents" in sql:
            self.rowcount = self._doc_rowcount
        else:
            self.rowcount = 1

    def fetchall(self):
        return self._cat_rows

    def fetchone(self):
        return self._cat_rows[0] if self._cat_rows else None


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


def _run(cat_rows, category_id, target_parent_id, doc_rowcount=0):
    cur = _MoveFakeCursor(cat_rows, doc_rowcount)
    svc = KnowledgeBaseService()
    with patch.object(KnowledgeBaseService, "_get_db_connection", return_value=_FakeConn(cur)):
        result = svc.move_category(category_id, "t1", target_parent_id)
    return result, cur.executed


def _flat(sql):
    return " ".join(str(sql).split())


# 栏目树：a(顶级) -> a1 -> a11；b(顶级)
CAT_ROWS = [
    {"id": 1, "source_type": "a", "parent_id": None},
    {"id": 2, "source_type": "a1", "parent_id": 1},
    {"id": 3, "source_type": "a11", "parent_id": 2},
    {"id": 4, "source_type": "b", "parent_id": None},
]


class TestMoveCategory:
    def test_same_top_move_updates_parent_only(self):
        """同顶级异父移动：仅改 parent_id，不更新 documents，moved_documents=0"""
        result, executed = _run(CAT_ROWS, 3, 1)  # a11 从 a1 下移到顶级 a 直接挂载（仍在顶级 a 树内）
        assert result["success"] is True
        assert result["moved_documents"] == 0
        update_docs = [e for e in executed if "UPDATE documents" in e[0]]
        assert update_docs == []
        cat_sql = " ".join(executed[1][0].split())
        assert "UPDATE knowledge_categories" in cat_sql

    def test_cross_top_move_backfills_documents(self):
        """跨顶级移动：子树内文档（sub_category IN 子树代号）source_type 回填为新顶级代号"""
        result, executed = _run(CAT_ROWS, 2, 4, doc_rowcount=5)  # a1 从 a 移到 b 下
        assert result["success"] is True
        assert result["moved_documents"] == 5
        update_docs = [e for e in executed if "UPDATE documents" in e[0]]
        assert len(update_docs) == 1
        sql, params = update_docs[0]
        assert "source_type = %s" in _flat(sql)
        assert "tenant_id = %s" in _flat(sql)
        # 参数：新顶级代号 b、租户、子树代号 [a1, a11]
        assert params[0] == "b"
        assert params[1] == "t1"
        assert sorted(params[2]) == ["a1", "a11"]

    def test_cross_top_move_deep_subtree_codes(self):
        """跨顶级移动深层节点：子树代号含自身与全部后代"""
        # 给 a11 加一个子级 a111，把 a11 移到 b 下
        rows = CAT_ROWS + [{"id": 5, "source_type": "a111", "parent_id": 3}]
        result, executed = _run(rows, 3, 4, doc_rowcount=2)
        assert result["success"] is True
        update_docs = [e for e in executed if "UPDATE documents" in e[0]]
        assert sorted(update_docs[0][1][2]) == ["a11", "a111"]

    def test_top_level_rejected(self):
        """顶级分类不可移动"""
        result, _ = _run(CAT_ROWS, 1, 4)
        assert result["success"] is False
        assert "顶级分类不可移动" in result["error"]

    def test_target_is_self_rejected(self):
        """目标父分类不能是自身"""
        result, _ = _run(CAT_ROWS, 2, 2)
        assert result["success"] is False

    def test_target_is_descendant_rejected(self):
        """目标父分类是自身的子孙时拒绝（移动到自己的子树会造成环）"""
        result, _ = _run(CAT_ROWS, 2, 3)  # a1 移到其子孙 a11 下
        assert result["success"] is False
        assert "子分类" in result["error"]

    def test_category_not_found(self):
        result, _ = _run(CAT_ROWS, 999, 4)
        assert result["success"] is False
        assert result.get("status") == 404

    def test_target_not_found(self):
        result, _ = _run(CAT_ROWS, 2, 999)
        assert result["success"] is False
        assert result.get("status") == 404
