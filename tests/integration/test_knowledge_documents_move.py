"""
知识库「移动文档」测试
覆盖：批量移动的两字段更新（source_type + sub_category）、分类统计联动、
检索跟随新分类（chunks 无需改动）、目标分类校验分支、租户隔离、幂等
"""

import pytest
import uuid

from src.knowledge.service import knowledge_service
from src.db.database import get_db_connection


class TestKnowledgeDocumentsMove:
    """知识库移动文档测试"""

    @pytest.fixture
    def tenant_id(self):
        """独立的临时租户 ID，测试后清理分类与文档"""
        tid = f"kb_move_{uuid.uuid4().hex[:8]}"
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
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (tenant_id, title, source_type, sub_category, "txt", "/tmp/test.txt"),
            )
            doc_id = cur.fetchone()["id"]
            conn.commit()
        return doc_id

    def _insert_chunk(self, doc_id, text):
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO chunks (doc_id, chunk_index, text, tokens) VALUES (%s, 0, %s, %s) RETURNING id",
                (doc_id, text, 10),
            )
            chunk_id = cur.fetchone()["id"]
            conn.commit()
        return chunk_id

    def _doc_row(self, doc_id):
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT source_type, sub_category FROM documents WHERE id = %s", (doc_id,))
            return cur.fetchone()

    def test_move_top_to_top_updates_fields_and_lists(self, tenant_id):
        """顶级分类 -> 顶级分类：source_type 更新、sub_category 保持 NULL、列表与统计联动"""
        knowledge_service.create_category(tenant_id, "product", "产品资料")
        knowledge_service.create_category(tenant_id, "policy", "政策文件")
        doc = self._insert_doc(tenant_id, "文档A", "product", None)
        self._insert_doc(tenant_id, "文档B", "product", None)

        result = knowledge_service.move_documents(tenant_id, [doc], "policy", None)
        assert result["success"] is True
        assert result["moved"] == 1
        assert result["skipped"] == 0

        row = self._doc_row(doc)
        assert row["source_type"] == "policy"
        assert row["sub_category"] is None

        # 列表：新分类命中，旧分类消失
        assert len(knowledge_service.list_documents(tenant_id=tenant_id, source_type="product")) == 1
        assert len(knowledge_service.list_documents(tenant_id=tenant_id, source_type="policy")) == 1

        # 分类统计联动：product 减 1、policy 加 1
        cats = knowledge_service.list_categories(tenant_id)
        cnt = {c["source_type"]: c["document_count"] for c in cats}
        assert cnt["product"] == 1
        assert cnt["policy"] == 1

    def test_move_top_to_child_sets_both_fields(self, tenant_id):
        """顶级分类 -> 子分类：source_type + sub_category 同时更新"""
        top = knowledge_service.create_category(tenant_id, "product", "产品资料")
        knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top["id"])
        doc = self._insert_doc(tenant_id, "文档A", "product", None)

        result = knowledge_service.move_documents(tenant_id, [doc], "product", "manual")
        assert result["success"] is True
        assert result["moved"] == 1

        row = self._doc_row(doc)
        assert row["source_type"] == "product"
        assert row["sub_category"] == "manual"

    def test_move_child_to_other_top_search_follows_new_category(self, tenant_id):
        """子分类文档 -> 另一顶级分类：检索跟随新分类命中，旧分类不再命中（chunks 无需改动）"""
        from src.knowledge.retriever.hybrid_retriever import HybridRetriever
        top_a = knowledge_service.create_category(tenant_id, "product", "产品资料")
        knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top_a["id"])
        top_b = knowledge_service.create_category(tenant_id, "policy", "政策文件")
        doc = self._insert_doc(tenant_id, "文档A", "product", "manual")
        self._insert_chunk(doc, "This document is about a dinosaur.")

        result = knowledge_service.move_documents(tenant_id, [doc], "policy", None)
        assert result["success"] is True
        assert result["moved"] == 1

        with get_db_connection() as conn:
            retriever = HybridRetriever(vector_db=None, embedding_client=None, conn=conn)
            # 旧分类不再命中
            r_old = retriever._postgres_fts_search(
                "dinosaur", top_k=10, tenant_id=tenant_id, source_type="product",
                sub_categories=["manual"]
            )
            assert len(r_old) == 0
            # 新顶级分类命中（chunks 未动，靠 join documents 过滤）
            r_new = retriever._postgres_fts_search(
                "dinosaur", top_k=10, tenant_id=tenant_id, source_type="policy"
            )
            assert len(r_new) == 1

    def test_move_missing_doc_ids_rejected(self, tenant_id):
        """空 doc_ids 拒绝"""
        result = knowledge_service.move_documents(tenant_id, [], "policy", None)
        assert result["success"] is False
        assert result.get("status") == 400
        assert "未选择文档" in result["error"]

    def test_move_missing_source_type_rejected(self, tenant_id):
        """空 source_type 拒绝"""
        doc = self._insert_doc(tenant_id, "文档A", "product", None)
        result = knowledge_service.move_documents(tenant_id, [doc], "", None)
        assert result["success"] is False
        assert result.get("status") == 400
        assert "目标顶级分类不能为空" in result["error"]

    def test_move_target_subcategory_missing_rejected(self, tenant_id):
        """目标子分类不存在拒绝"""
        knowledge_service.create_category(tenant_id, "product", "产品资料")
        doc = self._insert_doc(tenant_id, "文档A", "product", None)
        result = knowledge_service.move_documents(tenant_id, [doc], "product", "not_exist")
        assert result["success"] is False
        assert result.get("status") == 400
        assert "子分类不存在" in result["error"]

    def test_move_subcategory_cannot_be_top_level(self, tenant_id):
        """sub_category 不允许传顶级分类自身 source_type"""
        knowledge_service.create_category(tenant_id, "product", "产品资料")
        doc = self._insert_doc(tenant_id, "文档A", "product", None)
        result = knowledge_service.move_documents(tenant_id, [doc], "product", "product")
        assert result["success"] is False
        assert result.get("status") == 400
        assert "顶级分类" in result["error"]

    def test_move_subcategory_not_belong_to_top_rejected(self, tenant_id):
        """子分类不属于入参顶级分类拒绝"""
        top_a = knowledge_service.create_category(tenant_id, "product", "产品资料")
        knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top_a["id"])
        knowledge_service.create_category(tenant_id, "policy", "政策文件")
        doc = self._insert_doc(tenant_id, "文档A", "product", "manual")
        result = knowledge_service.move_documents(tenant_id, [doc], "policy", "manual")
        assert result["success"] is False
        assert result.get("status") == 400
        assert "子分类不属于所选顶级分类" in result["error"]

    def test_move_source_type_not_top_level_rejected(self, tenant_id):
        """sub_category 为空时 source_type 必须是本租户顶级分类（保持 source_type 恒为顶级代号不变量）"""
        top_a = knowledge_service.create_category(tenant_id, "product", "产品资料")
        child = knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top_a["id"])
        doc = self._insert_doc(tenant_id, "文档A", "product", "manual")
        # 把子分类代号当顶级用（sub_category 为空）应被拒绝
        result = knowledge_service.move_documents(tenant_id, [doc], "manual", None)
        assert result["success"] is False
        assert result.get("status") == 400
        assert "目标顶级分类不存在" in result["error"]
        # 子分类代号只允许通过 sub_category 挂载
        result2 = knowledge_service.move_documents(tenant_id, [doc], "product", "manual")
        assert result2["success"] is True
        assert child["id"] is not None

    def test_move_cross_tenant_isolation(self, tenant_id):
        """doc_ids 混入他租户文档：只移动本租户文档，他租户文档不受影响"""
        other_tid = f"kb_move_other_{uuid.uuid4().hex[:8]}"
        try:
            knowledge_service.create_category(tenant_id, "product", "产品资料")
            knowledge_service.create_category(tenant_id, "policy", "政策文件")
            knowledge_service.create_category(other_tid, "product", "产品资料")
            knowledge_service.create_category(other_tid, "policy", "政策文件")
            my_doc = self._insert_doc(tenant_id, "我的文档", "product", None)
            other_doc = self._insert_doc(other_tid, "他租户文档", "product", None)

            result = knowledge_service.move_documents(tenant_id, [my_doc, other_doc], "policy", None)
            assert result["success"] is True
            assert result["moved"] == 1  # 只移动本租户文档
            assert result["skipped"] == 1  # 他租户文档被跳过

            assert self._doc_row(my_doc)["source_type"] == "policy"
            assert self._doc_row(other_doc)["source_type"] == "product"  # 未被改动
        finally:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM knowledge_categories WHERE tenant_id = %s", (other_tid,))
                cur.execute("DELETE FROM documents WHERE tenant_id = %s", (other_tid,))
                conn.commit()

    def test_move_already_in_target_is_idempotent(self, tenant_id):
        """文档已在目标分类：moved=0、skipped=全量，仍返回 success"""
        knowledge_service.create_category(tenant_id, "product", "产品资料")
        doc = self._insert_doc(tenant_id, "文档A", "product", None)
        result = knowledge_service.move_documents(tenant_id, [doc], "product", None)
        assert result["success"] is True
        assert result["moved"] == 0
        assert result["skipped"] == 1
        assert self._doc_row(doc)["source_type"] == "product"
