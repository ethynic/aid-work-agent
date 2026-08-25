"""
知识库分类树形（多级分类）测试
覆盖：子分类创建/校验、list_categories 树形字段与 document_count 统计、
删除含子分类分类被拒、sub_category 上传校验、按 sub_category 列表过滤
"""

import pytest
import uuid

from src.knowledge.service import knowledge_service
from src.db.database import get_db_connection


class TestKnowledgeCategoryTree:
    """知识库分类树形测试"""

    @pytest.fixture
    def tenant_id(self):
        """独立的临时租户 ID，测试后清理分类与文档"""
        tid = f"kb_tree_{uuid.uuid4().hex[:8]}"
        yield tid
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM knowledge_categories WHERE tenant_id = %s", (tid,))
            cur.execute("DELETE FROM documents WHERE tenant_id = %s", (tid,))
            conn.commit()

    def _insert_doc(self, tenant_id, title, source_type, sub_category):
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO documents (tenant_id, title, source_type, sub_category, file_type, file_path) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (tenant_id, title, source_type, sub_category, "txt", "/tmp/test.txt"),
            )
            conn.commit()

    def test_create_top_and_child_category(self, tenant_id):
        """创建顶级分类 + 子分类"""
        top = knowledge_service.create_category(tenant_id, "product", "产品资料")
        assert top["success"] is True
        child = knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top["id"])
        assert child["success"] is True
        assert child["parent_id"] == top["id"]

    def test_create_child_with_missing_parent(self, tenant_id):
        """父分类不存在时拒绝"""
        result = knowledge_service.create_category(tenant_id, "orphan", "孤立分类", parent_id=999999)
        assert result["success"] is False
        assert result.get("status") == 404

    def test_create_child_cross_tenant_rejected(self, tenant_id):
        """跨租户引用父分类被拒绝"""
        other_tid = f"kb_tree_other_{uuid.uuid4().hex[:8]}"
        try:
            top = knowledge_service.create_category(other_tid, "other", "其他租户分类")
            assert top["success"] is True
            result = knowledge_service.create_category(tenant_id, "child2", "子分类", parent_id=top["id"])
            assert result["success"] is False
            assert result.get("status") == 404
        finally:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM knowledge_categories WHERE tenant_id = %s", (other_tid,))
                conn.commit()

    def test_list_categories_includes_parent_id(self, tenant_id):
        """list_categories 返回 parent_id 字段"""
        top = knowledge_service.create_category(tenant_id, "product", "产品资料")
        knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top["id"])
        cats = knowledge_service.list_categories(tenant_id)
        top_cat = next(c for c in cats if c["source_type"] == "product")
        child_cat = next(c for c in cats if c["source_type"] == "manual")
        assert top_cat["parent_id"] is None
        assert child_cat["parent_id"] == top_cat["id"]

    def test_document_count_top_includes_subcategory_docs(self, tenant_id):
        """顶级分类 document_count 含子分类文档，子分类仅统计直接归属"""
        top = knowledge_service.create_category(tenant_id, "product", "产品资料")
        child = knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top["id"])
        self._insert_doc(tenant_id, "顶级文档", "product", None)
        self._insert_doc(tenant_id, "子分类文档", "product", "manual")
        cats = knowledge_service.list_categories(tenant_id)
        top_cat = next(c for c in cats if c["id"] == top["id"])
        child_cat = next(c for c in cats if c["id"] == child["id"])
        assert top_cat["document_count"] == 2  # source_type=product 全部文档（含子分类）
        assert child_cat["document_count"] == 1  # sub_category=manual 直接归属

    def test_delete_category_with_children_rejected(self, tenant_id):
        """含子分类的分类禁止删除"""
        top = knowledge_service.create_category(tenant_id, "product", "产品资料")
        knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top["id"])
        result = knowledge_service.delete_category(top["id"], tenant_id)
        assert result["success"] is False
        assert "子分类" in result["error"]

    def test_delete_leaf_category_ok(self, tenant_id):
        """叶子分类可删除（不删文档）"""
        top = knowledge_service.create_category(tenant_id, "product", "产品资料")
        child = knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top["id"])
        self._insert_doc(tenant_id, "子分类文档", "product", "manual")
        result = knowledge_service.delete_category(child["id"], tenant_id)
        assert result["success"] is True
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM documents WHERE tenant_id = %s", (tenant_id,))
            assert cur.fetchone()["c"] == 1  # 文档保留

    async def test_upload_subcategory_validation(self, tenant_id):
        """sub_category 归属校验：不属于所选顶级分类时失败"""
        top = knowledge_service.create_category(tenant_id, "product", "产品资料")
        knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top["id"])
        # 挂 manual 子分类但 source_type 传不匹配的顶级
        result = await knowledge_service.upload_document(
            file_path="/tmp/not_exist.txt",
            file_filename="x.txt",
            tenant_id=tenant_id,
            source_type="other_top",
            sub_category="manual",
        )
        assert result["success"] is False
        assert "子分类" in result.get("debug", "")

    async def test_upload_subcategory_cannot_be_top_level(self, tenant_id):
        """sub_category 不允许传顶级分类自身 source_type（必须挂真正子分类）"""
        top = knowledge_service.create_category(tenant_id, "product", "产品资料")
        # 顶级分类 source_type 作为 sub_category 应被拒绝（非子分类）
        result = await knowledge_service.upload_document(
            file_path="/tmp/not_exist.txt",
            file_filename="x.txt",
            tenant_id=tenant_id,
            source_type="product",
            sub_category="product",
        )
        assert result["success"] is False
        assert "子分类" in result.get("debug", "")

    def test_list_documents_filter_by_sub_category(self, tenant_id):
        """按 sub_category 过滤文档列表"""
        top = knowledge_service.create_category(tenant_id, "product", "产品资料")
        child = knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top["id"])
        self._insert_doc(tenant_id, "顶级文档", "product", None)
        self._insert_doc(tenant_id, "子分类文档", "product", "manual")
        docs = knowledge_service.list_documents(tenant_id=tenant_id, source_type="product", sub_category="manual")
        assert len(docs) == 1
        assert docs[0]["sub_category"] == "manual"
        # 顶级分类下所有文档（含子分类）
        docs_all = knowledge_service.list_documents(tenant_id=tenant_id, source_type="product")
        assert len(docs_all) == 2
