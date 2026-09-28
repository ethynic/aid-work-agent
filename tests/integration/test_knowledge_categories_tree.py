"""
知识库分类树形（多级分类）测试
覆盖：子分类创建/校验、list_categories 树形字段与 document_count 统计、
删除含子分类分类被拒、sub_category 上传校验、按 sub_category 列表过滤
"""

import asyncio
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

    def test_create_category_auto_source_type(self, tenant_id):
        """不传 source_type 时自动生成唯一代号（前端添加分类不再要求手填英文代号）"""
        result = knowledge_service.create_category(tenant_id, None, "自动代号分类")
        assert result["success"] is True
        assert result["source_type"].startswith("k_")
        assert result["display_name"] == "自动代号分类"
        # 自动生成的代号在租户内唯一，可重复创建不冲突
        result2 = knowledge_service.create_category(tenant_id, None, "自动代号分类2")
        assert result2["success"] is True
        assert result2["source_type"] != result["source_type"]

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

    def test_delete_category_cascades_knowledge_sources(self, tenant_id):
        """删除栏目级联清理 subagent_knowledge_sources：本租户自有项 + 他租户共享项，
        同名 source_type 的他租户自有项与本租户共享项（来源为他租户）不受影响"""
        from src.db.subagent_knowledge_source_db import SubagentKnowledgeSourceDB

        other_tid = f"kb_tree_other_{uuid.uuid4().hex[:8]}"
        cat = knowledge_service.create_category(tenant_id, "policy_gradual", "梯度培育政策")
        assert cat["success"] is True
        st = cat["source_type"]
        try:
            # 本租户销售助手：自有项(待清理) + 其他自有项(保留) + 他租户共享项(保留，来源他租户)
            SubagentKnowledgeSourceDB.set(tenant_id, "sales-helper", [
                {"source_type": st, "display_name": "梯度培育政策", "owner_tenant_id": None},
                {"source_type": "other_kept", "display_name": "其他栏目", "owner_tenant_id": None},
                {"source_type": st, "display_name": "他租户同名栏目", "owner_tenant_id": other_tid},
            ])
            # 他租户数字员工：共享项(来源本租户，待清理) + 同名自有项(保留)
            SubagentKnowledgeSourceDB.set(other_tid, "some-agent", [
                {"source_type": st, "display_name": "借用的梯度培育", "owner_tenant_id": tenant_id},
                {"source_type": st, "display_name": "我自己的同名栏目", "owner_tenant_id": None},
            ])

            result = knowledge_service.delete_category(cat["id"], tenant_id)
            assert result["success"] is True

            own_sources = SubagentKnowledgeSourceDB.get(tenant_id, "sales-helper")
            own_pairs = {(s["source_type"], s.get("owner_tenant_id")) for s in own_sources}
            assert (st, None) not in own_pairs  # 自有项已清理
            assert ("other_kept", None) in own_pairs  # 其他自有项保留
            assert (st, other_tid) in own_pairs  # 他租户来源的共享项保留

            other_sources = SubagentKnowledgeSourceDB.get(other_tid, "some-agent")
            other_pairs = {(s["source_type"], s.get("owner_tenant_id")) for s in other_sources}
            assert (st, tenant_id) not in other_pairs  # 共享项已清理
            assert (st, None) in other_pairs  # 他租户同名自有项保留
        finally:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM subagent_knowledge_sources WHERE tenant_id IN (%s, %s)",
                            (tenant_id, other_tid))
                conn.commit()

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

    def test_document_count_subcategory_includes_grandchild(self, tenant_id):
        """二级分类 document_count 含三级文档（每级统计其下所有子级）"""
        top = knowledge_service.create_category(tenant_id, "product", "产品资料")
        child = knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top["id"])
        grandchild = knowledge_service.create_category(tenant_id, "spec", "规格书", parent_id=child["id"])
        self._insert_doc(tenant_id, "二级文档", "product", "manual")
        self._insert_doc(tenant_id, "三级文档", "product", "spec")
        cats = knowledge_service.list_categories(tenant_id)
        top_cat = next(c for c in cats if c["id"] == top["id"])
        child_cat = next(c for c in cats if c["id"] == child["id"])
        grandchild_cat = next(c for c in cats if c["id"] == grandchild["id"])
        assert top_cat["document_count"] == 2  # 自身 + 二级 + 三级
        assert child_cat["document_count"] == 2  # 自身 manual + 后代 spec
        assert grandchild_cat["document_count"] == 1  # 无子级，仅自身

    def test_list_documents_subcategory_includes_descendants(self, tenant_id):
        """点子分类文档列表含三级文档；点三级只含自身；document_count 与列表 total 对齐"""
        top = knowledge_service.create_category(tenant_id, "product", "产品资料")
        child = knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top["id"])
        grandchild = knowledge_service.create_category(tenant_id, "spec", "规格书", parent_id=child["id"])
        self._insert_doc(tenant_id, "二级文档", "product", "manual")
        self._insert_doc(tenant_id, "三级文档", "product", "spec")
        # 点二级分类 -> 含自身 + 三级文档
        docs = knowledge_service.list_documents(tenant_id=tenant_id, source_type="product", sub_category="manual")
        assert len(docs) == 2
        assert {d["sub_category"] for d in docs} == {"manual", "spec"}
        # 点三级分类 -> 仅自身文档
        docs_gc = knowledge_service.list_documents(tenant_id=tenant_id, source_type="product", sub_category="spec")
        assert len(docs_gc) == 1
        assert docs_gc[0]["sub_category"] == "spec"
        # count 与 list 对齐（交叉验证）
        assert knowledge_service.count_documents(
            tenant_id=tenant_id, source_type="product", sub_category="manual"
        ) == len(docs)
        assert knowledge_service.count_documents(
            tenant_id=tenant_id, source_type="product", sub_category="spec"
        ) == len(docs_gc)

    def test_search_subcategory_filters_descendants(self, tenant_id):
        """搜索跟随选中分类：sub_categories 集合只检索选中分类（含子级）的文档"""
        from src.knowledge.retriever.hybrid_retriever import HybridRetriever
        top = knowledge_service.create_category(tenant_id, "product", "产品资料")
        child = knowledge_service.create_category(tenant_id, "manual", "产品手册", parent_id=top["id"])
        grandchild = knowledge_service.create_category(tenant_id, "spec", "规格书", parent_id=child["id"])
        # 每级各挂 1 个文档（含 1 个 chunk），text 用英文关键词保证 simple 分词命中
        doc_top = self._insert_doc(tenant_id, "顶级文档", "product", None)
        doc_child = self._insert_doc(tenant_id, "二级文档", "product", "manual")
        doc_gc = self._insert_doc(tenant_id, "三级文档", "product", "spec")
        for doc_id in (doc_top, doc_child, doc_gc):
            self._insert_chunk(doc_id, "This document is about a dinosaur.")

        with get_db_connection() as conn:
            retriever = HybridRetriever(vector_db=None, embedding_client=None, conn=conn)
            # 选中顶级分类：source_type 过滤天然含全部子级（sub_categories 为空）
            r_all = retriever._postgres_fts_search(
                "dinosaur", top_k=10, tenant_id=tenant_id, source_type="product"
            )
            assert len(r_all) == 3
            # 选中二级分类：子树 = [manual, spec]，返回二级 + 三级文档
            r_child = retriever._postgres_fts_search(
                "dinosaur", top_k=10, tenant_id=tenant_id, source_type="product",
                sub_categories=["manual", "spec"]
            )
            assert len(r_child) == 2
            # 选中三级分类：子树 = [spec]，只返回三级文档
            r_gc = retriever._postgres_fts_search(
                "dinosaur", top_k=10, tenant_id=tenant_id, source_type="product",
                sub_categories=["spec"]
            )
            assert len(r_gc) == 1


class TestDataAnalysisSystemCategory:
    """「数据分析-数据源」系统栏目：schema 保存自动建栏目 + 禁删保护"""

    @pytest.fixture
    def tenant_id(self):
        tid = f"kb_da_{uuid.uuid4().hex[:8]}"
        yield tid
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE tenant_id = %s)",
                (tid,),
            )
            cur.execute("DELETE FROM documents WHERE tenant_id = %s", (tid,))
            cur.execute("DELETE FROM knowledge_categories WHERE tenant_id = %s", (tid,))
            conn.commit()

    def test_save_schema_creates_category(self, tenant_id):
        """save_schema_to_knowledge 自动创建「数据分析-数据源」栏目，文档可被授权弹框勾选"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.services.data_analysis.schema_saver import save_schema_to_knowledge
        from src.services.data_analysis.constants import DATA_SOURCE_CATEGORY_DISPLAY_NAME

        client = MagicMock()
        client.embed_batch = AsyncMock(return_value=[[0.0] * 8])
        client.last_usage_tokens = 0
        client.model = "text-embedding-v3"
        with patch(
            "src.services.data_analysis.schema_saver.TextEmbeddingV3Client",
            return_value=client,
        ), patch("src.services.data_analysis.schema_saver.get_vector_db") as vec_mock:
            vec_client = MagicMock()
            vec_client.insert = AsyncMock()
            vec_mock.return_value = vec_client
            result = asyncio.run(save_schema_to_knowledge(
                tenant_id=tenant_id,
                table_name="订单明细",
                description="订单维度",
                columns=[{"name": "amount", "data_type": "decimal"}],
                source_info="orders.xlsx",
            ))

        assert result["success"] is True

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT source_type, display_name FROM knowledge_categories WHERE tenant_id = %s",
                (tenant_id,),
            )
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["source_type"] == "data-analysis-metadata"
        assert rows[0]["display_name"] == DATA_SOURCE_CATEGORY_DISPLAY_NAME

    def test_save_schema_category_idempotent(self, tenant_id):
        """重复保存不同表，栏目不重复创建"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.services.data_analysis.schema_saver import save_schema_to_knowledge

        client = MagicMock()
        client.embed_batch = AsyncMock(return_value=[[0.0] * 8])
        client.last_usage_tokens = 0
        client.model = "text-embedding-v3"
        with patch(
            "src.services.data_analysis.schema_saver.TextEmbeddingV3Client",
            return_value=client,
        ), patch("src.services.data_analysis.schema_saver.get_vector_db") as vec_mock:
            vec_client = MagicMock()
            vec_client.insert = AsyncMock()
            vec_mock.return_value = vec_client
            for i in range(2):
                asyncio.run(save_schema_to_knowledge(
                    tenant_id=tenant_id,
                    table_name=f"表{i}",
                    description="",
                    columns=[{"name": "a"}],
                    source_info=f"f{i}.xlsx",
                ))

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM knowledge_categories WHERE tenant_id = %s",
                (tenant_id,),
            )
            assert cur.fetchone()["cnt"] == 1

    def test_delete_system_category_rejected(self, tenant_id):
        """系统栏目禁删，普通栏目可删"""
        created = knowledge_service.create_category(
            tenant_id, source_type="data-analysis-metadata", display_name="数据分析-数据源",
        )
        assert created["success"] is True

        result = knowledge_service.delete_category(created["id"], tenant_id)
        assert result["success"] is False
        assert "系统栏目" in result["error"]

        # 普通栏目不受影响
        normal = knowledge_service.create_category(tenant_id, source_type="normal_cat")
        assert knowledge_service.delete_category(normal["id"], tenant_id)["success"] is True
