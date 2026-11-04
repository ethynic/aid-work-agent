"""HybridRetriever 精确型号检索单测"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.knowledge.retriever.hybrid_retriever import HybridRetriever


def _build_retriever(conn=None):
    vector_db = MagicMock()
    embedding = MagicMock()
    embedding.reset_usage = MagicMock()
    embedding.embed = AsyncMock(return_value=[0.1, 0.2])
    embedding.last_usage_tokens = 0
    return HybridRetriever(vector_db=vector_db, embedding_client=embedding, conn=conn)


class TestExtractExactTokens:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("TJ1890027 配套 750*1500 子母砖", ["TJ1890027"]),
            ("1890027，子母款", ["1890027"]),
            ("TFD2300P的配套75乘1.5米的型号", ["TFD2300P"]),
            ("TDA157003硬度达到多少", ["TDA157003"]),
            ("9-TS2680D004 配套", ["TS2680D004"]),
            ("26002是什么工艺", ["26002"]),
            ("帮我找一下75×150的瓷砖", []),
            ("2024年差旅报销标准", []),
            ("TJ1890027 TG80227 TJ157013", ["TJ1890027", "TG80227", "TJ157013"]),
        ],
    )
    def test_extract_exact_tokens(self, query, expected):
        assert HybridRetriever._extract_exact_tokens(query) == expected

    def test_extract_tokens_limit(self):
        tokens = HybridRetriever._extract_exact_tokens(
            "TJ1890027 TG80227 TJ157013 TDA157003 TFZ26006"
        )
        assert tokens == ["TJ1890027", "TG80227", "TJ157013", "TDA157003"]


class TestExactPriority:
    def test_exact_match_outranks_vector_and_fts(self):
        # chunk 1 同时命中向量+全文；chunk 2 仅精确型号命中
        fused = HybridRetriever._weighted_rrf_fusion(
            vector_results=[(1, 0.9)],
            fts_results=[(1, 0.8)],
            exact_results=[(2, 0.0)],
            exact_weight=HybridRetriever.EXACT_TOKEN_WEIGHT,
        )
        assert fused[0][0] == 2


class TestExactSearch:
    def test_no_token_skips_database(self):
        conn = MagicMock()
        retriever = _build_retriever(conn)
        assert retriever._exact_search("子母砖配套规格", top_k=5) == []
        conn.cursor.assert_not_called()

    def test_tenant_branch_queries_with_patterns(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = [
            {"id": 11, "doc_id": 2, "chunk_index": 3, "hits": 1},
        ]
        retriever = _build_retriever(conn)

        rows = retriever._exact_search(
            "TJ1890027 配套", top_k=5, tenant_id="tenant_a",
            shared_ranges=[], agent_id="sales-assistant",
        )

        assert rows[0]["id"] == 11
        sql, params = cursor.execute.call_args[0]
        assert "c.text ILIKE ANY(%s::text[])" in sql
        assert "ORDER BY hits DESC" in sql
        assert params[0] == "%TJ1890027%"   # SELECT 中的 CASE 命中计数
        assert params[1] == ["%TJ1890027%"]  # WHERE 中的 ILIKE ANY 数组
        assert params[-1] == 5

    def test_neighbor_ids_excludes_hits_and_caps(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = [
            {"id": 10, "doc_id": 2, "chunk_index": 2},  # 命中本身，应排除
            {"id": 11, "doc_id": 2, "chunk_index": 1},
            {"id": 12, "doc_id": 2, "chunk_index": 3},
        ]
        retriever = _build_retriever(conn)

        ids = retriever._neighbor_chunk_ids(
            [{"id": 10, "doc_id": 2, "chunk_index": 2}], max_extra=1,
        )
        assert ids == [11]
        sql, params = cursor.execute.call_args[0]
        assert "(doc_id, chunk_index) IN" in sql
        assert params == [2, 1, 2, 3]


class TestRetrieveMergesExact:
    @pytest.mark.asyncio
    async def test_exact_chunk_kept_and_ranked_first(self, monkeypatch):
        retriever = _build_retriever(conn=None)
        retriever.vector_db.search = AsyncMock(return_value=[(1, 0.9)])
        monkeypatch.setattr(retriever, "_fts_search", MagicMock(return_value=[(1, -0.5)]))
        monkeypatch.setattr(
            retriever, "_exact_search",
            MagicMock(return_value=[{"id": 2, "doc_id": 9, "chunk_index": 14, "hits": 1}]),
        )
        monkeypatch.setattr(retriever, "_neighbor_chunk_ids", MagicMock(return_value=[3]))
        monkeypatch.setattr(
            retriever, "_build_results",
            lambda fused: [
                {"chunk_id": cid, "doc_id": cid, "chunk_index": 0,
                 "text": f"chunk-{cid}", "score": score, "metadata": {}, "tokens": 1}
                for cid, score in fused
            ],
        )

        results = await retriever.retrieve(
            query="TJ1890027 配套", top_k=5, tenant_id="tenant_a",
            shared_ranges=[], agent_id="sales-assistant",
        )

        ids = [item["chunk_id"] for item in results]
        assert ids[0] == 2
        # 邻居片段虽归一化分数为 0，也必须保留
        assert 3 in ids
