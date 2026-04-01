"""
混合检索器 - 向量检索 + FTS5 全文检索 + RRF 融合
"""

import sqlite3
from typing import List, Dict, Tuple, Optional, Any
import logging

logger = logging.getLogger(__name__)


class HybridRetriever:
    """混合检索器（向量 + FTS5 + RRF 融合）"""

    def __init__(
        self,
        vector_db,
        embedding_client,
        conn: sqlite3.Connection
    ):
        self.vector_db = vector_db
        self.embedding_client = embedding_client
        self.conn = conn

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        混合检索

        Args:
            query: 用户查询
            top_k: 返回结果数量
            user_id: 用户 ID（权限控制，暂未实现）

        Returns:
            检索结果列表
        """
        # 1. 向量检索
        query_embedding = await self.embedding_client.embed(query)
        vector_results = await self.vector_db.search(query_embedding, top_k=top_k * 2)

        # 2. FTS5 全文检索
        fts_results = self._fts_search(query, top_k=top_k * 2)

        # 3. RRF 融合
        fused = self._rrf_fusion(vector_results, fts_results, k=60)

        # 4. 构建完整结果
        results = self._build_results(fused[:top_k])

        logger.info(f"后端日志：混合检索完成，查询={query}, 结果数={len(results)}")
        return results

    def _fts_search(self, query: str, top_k: int) -> List[Tuple[int, float]]:
        """FTS5 全文检索"""
        cursor = self.conn.cursor()

        try:
            cursor.execute("""
                SELECT chunk_id, bm25(chunks_fts) as score
                FROM chunks_fts
                WHERE chunks_fts MATCH ?
                ORDER BY score
                LIMIT ?
            """, (query, top_k))

            return [(row[0], row[1]) for row in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"后端日志：FTS5 检索失败: {e}")
            return []

    def _rrf_fusion(
        self,
        vector_results: List[Tuple[int, float]],
        fts_results: List[Tuple[int, float]],
        k: int = 60
    ) -> List[Tuple[int, float]]:
        """
        RRF（Reciprocal Rank Fusion）融合

        Args:
            vector_results: [(chunk_id, score), ...]
            fts_results: [(chunk_id, score), ...]
            k: RRF 参数（通常 60）

        Returns:
            [(chunk_id, fused_score), ...]
        """
        scores: Dict[int, float] = {}

        # 向量检索的排名
        for rank, (chunk_id, _) in enumerate(vector_results):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (k + rank + 1)

        # FTS5 检索的排名
        for rank, (chunk_id, _) in enumerate(fts_results):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (k + rank + 1)

        # 按融合分数排序
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results

    def _build_results(self, fused: List[Tuple[int, float]]) -> List[Dict[str, Any]]:
        """构建完整的检索结果"""
        if not fused:
            return []

        chunk_ids = [chunk_id for chunk_id, _ in fused]
        placeholders = ','.join(['?'] * len(chunk_ids))

        cursor = self.conn.cursor()
        cursor.execute(f"""
            SELECT id, doc_id, text, tokens, metadata
            FROM chunks
            WHERE id IN ({placeholders})
        """, chunk_ids)

        rows = cursor.fetchall()

        # 按 fused 顺序排序
        id_to_row = {row[0]: row for row in rows}
        results = []

        for chunk_id, score in fused:
            row = id_to_row.get(chunk_id)
            if row:
                import json
                metadata = json.loads(row[4]) if row[4] else {}
                results.append({
                    "chunk_id": row[0],
                    "doc_id": row[1],
                    "text": row[2],
                    "tokens": row[3],
                    "metadata": metadata,
                    "score": score
                })

        return results
