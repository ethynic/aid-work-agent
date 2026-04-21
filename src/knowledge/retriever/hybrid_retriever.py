"""
混合检索器 - 向量检索 + 全文检索 + RRF 融合
PostgreSQL 版本
"""

import json
import re
from typing import List, Dict, Tuple, Optional, Any
from loguru import logger


class HybridRetriever:
    """混合检索器（向量 + FTS5 + 加权 RRF 融合）"""

    # RRF 参数：k 值越大，排名差异对分数的影响越小
    RRF_K = 60

    # 权重配置：向量检索权重 > FTS5 权重（语义匹配更准）
    VECTOR_WEIGHT = 0.7
    FTS_WEIGHT = 0.3

    # 向量检索的余弦相似度阈值（低于此值视为不相关）
    # 中文 embedding 模型对无关词汇的基线相似度约 0.3~0.4，
    # 需要设置较高阈值才能有效过滤。实测：
    #   - 完全无关查询（如"歼击机"匹配企业文档）：最高 ~0.43
    #   - 相关查询（如"差旅政策"匹配差旅文档）：通常 > 0.55
    VECTOR_SIMILARITY_THRESHOLD = 0.5

    # 最小分数阈值（RRF 原始值），低于此值的结果被过滤（第一道防线）
    MIN_SCORE_THRESHOLD = 0.003

    # 归一化后的相关度截断阈值：当结果数超过 min_keep 时，丢弃 score < 此值的低质结果
    RELEVANCE_THRESHOLD = 0.3

    # 最少保留结果数：仅用于有多条高分结果时避免截断过多
    MIN_KEEP_RESULTS = 1

    # FTS5 查询最大长度，超过则提取关键词
    FTS_QUERY_MAX_LENGTH = 30

    # 停用词表（常见但无实际意义的词）
    STOP_WORDS = {
        "的", "了", "是", "在", "有", "和", "与", "或", "等", "及",
        "这", "那", "它", "她", "他", "我", "你", "们",
        "如果", "可以", "会", "将", "被", "把", "对", "从", "到",
        "时", "当", "后", "前", "中", "上", "下", "内", "外",
        "之", "以", "于", "为", "而", "也", "但", "且",
        "一个", "这个", "那个", "什么", "如何", "怎么",
        "系统", "提示", "相关"
    }

    def __init__(
        self,
        vector_db,
        embedding_client,
        conn,
        db_type: str = "postgresql"
    ):
        self.vector_db = vector_db
        self.embedding_client = embedding_client
        self.conn = conn
        self.db_type = db_type

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        混合检索（加权 RRF 融合 + 向量相似度阈值 + 相关度截断）

        Args:
            query: 用户查询
            top_k: 返回结果数量
            user_id: 用户 ID（权限控制，暂未实现）

        Returns:
            检索结果列表
        """
        # 1. 向量检索（语义相似度，权重更高）
        query_embedding = await self.embedding_client.embed(query)
        raw_vector_results = await self.vector_db.search(query_embedding, top_k=top_k * 3)

        # 后端日志：输出原始向量检索结果（调优用）
        logger.info(
            f"后端日志：[检索调优] 查询={query}, "
            f"阈值 VECTOR_SIMILARITY_THRESHOLD={self.VECTOR_SIMILARITY_THRESHOLD}, "
            f"MIN_SCORE_THRESHOLD={self.MIN_SCORE_THRESHOLD}, "
            f"RELEVANCE_THRESHOLD={self.RELEVANCE_THRESHOLD}, "
            f"MIN_KEEP_RESULTS={self.MIN_KEEP_RESULTS}"
        )
        logger.info(
            f"后端日志：[检索调优] 步骤1-向量检索原始结果(top {len(raw_vector_results)}): "
            + ", ".join(f"(chunk_id={cid}, sim={sim:.4f})" for cid, sim in raw_vector_results[:10])
        )

        # 2. 过滤低相似度的向量结果（关键：将 L2 距离转为余弦相似度）
        vector_results = [
            (cid, sim) for cid, sim in raw_vector_results
            if sim >= self.VECTOR_SIMILARITY_THRESHOLD
        ]

        # 后端日志：步骤2-向量相似度过滤详情
        filtered_out = [(cid, sim) for cid, sim in raw_vector_results if sim < self.VECTOR_SIMILARITY_THRESHOLD]
        logger.info(
            f"后端日志：[检索调优] 步骤2-向量相似度过滤: "
            f"原始={len(raw_vector_results)}, 通过(>={self.VECTOR_SIMILARITY_THRESHOLD})={len(vector_results)}, "
            f"过滤掉={len(filtered_out)}"
        )
        if filtered_out:
            logger.info(
                f"后端日志：[检索调优] 被过滤的向量结果: "
                + ", ".join(f"(chunk_id={cid}, sim={sim:.4f})" for cid, sim in filtered_out[:5])
            )

        # 记录向量检索的最高相似度，用于判断是否完全无相关结果
        best_vector_sim = raw_vector_results[0][1] if raw_vector_results else 0

        # 3. FTS5 全文检索（关键词精确匹配）
        fts_query = self._preprocess_fts_query(query)
        fts_results = self._fts_search(fts_query, top_k=top_k * 3)

        # 后端日志：步骤3-FTS5全文检索详情
        logger.info(
            f"后端日志：[检索调优] 步骤3-FTS5检索: fts_query='{fts_query}', "
            f"结果数={len(fts_results)}"
            + (", " + ", ".join(f"(chunk_id={cid}, bm25={score:.4f})" for cid, score in fts_results[:5]) if fts_results else "")
        )

        # 4. 两种检索均无结果 → 直接返回空
        if not vector_results and not fts_results:
            logger.info(f"后端日志：混合检索无结果，查询={query}, 最佳向量相似度={best_vector_sim:.4f}")
            return []

        # 5. 加权 RRF 融合
        fused = self._weighted_rrf_fusion(
            vector_results, fts_results,
            k=self.RRF_K,
            vector_weight=self.VECTOR_WEIGHT,
            fts_weight=self.FTS_WEIGHT
        )

        # 后端日志：步骤5-RRF融合详情
        logger.info(
            f"后端日志：[检索调优] 步骤5-RRF融合(RRF_K={self.RRF_K}, vec_w={self.VECTOR_WEIGHT}, fts_w={self.FTS_WEIGHT}): "
            f"融合结果数={len(fused)}"
            + ", " + ", ".join(f"(chunk_id={cid}, rrf={score:.6f})" for cid, score in fused[:10])
        )

        # 6. 应用最小分数阈值 + 取 top_k
        filtered = [(cid, score) for cid, score in fused if score >= self.MIN_SCORE_THRESHOLD]

        # 后端日志：步骤6-最小分数阈值过滤详情
        filtered_out_rrf = [(cid, score) for cid, score in fused if score < self.MIN_SCORE_THRESHOLD]
        logger.info(
            f"后端日志：[检索调优] 步骤6-MIN_SCORE_THRESHOLD过滤(>={self.MIN_SCORE_THRESHOLD}): "
            f"融合={len(fused)}, 通过={len(filtered)}, 过滤掉={len(filtered_out_rrf)}"
        )

        if not filtered:
            logger.info(f"后端日志：混合检索阈值过滤后无结果，查询={query}, 最佳向量相似度={best_vector_sim:.4f}")
            return []

        # 7. 归一化分数到 0-1 区间
        normalized = self._normalize_scores(filtered[:top_k])

        # 后端日志：步骤7-归一化详情
        if filtered:
            raw_max = filtered[0][1] if filtered else 0
            raw_min = filtered[-1][1] if filtered else 0
            norm_details = ", ".join(
                f"(chunk_id={cid}, raw={raw_score:.6f}, norm={norm_score:.4f})"
                for (cid, raw_score), (_, norm_score) in zip(filtered[:top_k], normalized[:10])
            )
            logger.info(
                f"后端日志：[检索调优] 步骤7-归一化(min-max): "
                f"原始分数范围=[{raw_min:.6f}, {raw_max:.6f}], 归一化后: {norm_details}"
            )

        # 8. 相关度截断：保留前 min_keep 个 + 高分结果
        final_results = [
            (cid, score) for rank, (cid, score) in enumerate(normalized)
            if rank < self.MIN_KEEP_RESULTS or score >= self.RELEVANCE_THRESHOLD
        ]

        # 后端日志：步骤8-相关度截断详情
        truncated = [
            (cid, score) for rank, (cid, score) in enumerate(normalized)
            if rank >= self.MIN_KEEP_RESULTS and score < self.RELEVANCE_THRESHOLD
        ]
        logger.info(
            f"后端日志：[检索调优] 步骤8-RELEVANCE_THRESHOLD截断(>={self.RELEVANCE_THRESHOLD}, min_keep={self.MIN_KEEP_RESULTS}): "
            f"归一化后={len(normalized)}, 最终保留={len(final_results)}, 被截断={len(truncated)}"
        )
        if truncated:
            logger.info(
                f"后端日志：[检索调优] 被截断的低分结果: "
                + ", ".join(f"(chunk_id={cid}, norm_score={score:.4f})" for cid, score in truncated)
            )

        # 9. 构建完整结果
        results = self._build_results(final_results)

        logger.info(
            f"后端日志：混合检索完成，查询={query}, "
            f"向量结果(原始/过滤后)={len(raw_vector_results)}/{len(vector_results)}, "
            f"FTS结果={len(fts_results)}, "
            f"融合后={len(fused)}, 过滤后={len(results)}, "
            f"最佳向量相似度={best_vector_sim:.4f} (阈值={self.VECTOR_SIMILARITY_THRESHOLD})"
        )
        return results

    @staticmethod
    def _preprocess_fts_query(query: str) -> str:
        """
        FTS5 查询预处理

        - 长句提取关键词（去除停用词、短词）
        - 短查询保持原样
        """
        if len(query) <= HybridRetriever.FTS_QUERY_MAX_LENGTH:
            return query

        # 提取中文词汇（2字及以上），过滤停用词和短词
        words = re.findall(r'[\u4e00-\u9fa5]{2,}', query)
        keywords = [w for w in words if w not in HybridRetriever.STOP_WORDS]

        if not keywords:
            # 回退到原始查询
            return query

        # 用 OR 连接关键词（FTS5 语法：匹配任一关键词即可）
        return " OR ".join(keywords)

    def _fts_search(self, query: str, top_k: int) -> List[Tuple[int, float]]:
        """全文检索（SQLite FTS5 或 PostgreSQL tsvector）"""
        cursor = self.conn.cursor()

        if self.db_type == "postgresql":
            return self._postgres_fts_search(query, top_k)
        else:
            return self._sqlite_fts_search(query, top_k)

    def _sqlite_fts_search(self, query: str, top_k: int) -> List[Tuple[int, float]]:
        """SQLite FTS5 全文检索"""
        cursor = self.conn.cursor()

        try:
            cursor.execute("""
                SELECT rowid, bm25(chunks_fts) as score
                FROM chunks_fts
                WHERE chunks_fts MATCH ?
                ORDER BY score
                LIMIT ?
            """, (query, top_k))

            return [(row[0], row[1]) for row in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"后端日志：FTS5 检索失败（查询可能包含特殊字符）: {e}")
            return []

    def _postgres_fts_search(self, query: str, top_k: int) -> List[Tuple[int, float]]:
        """PostgreSQL 全文检索（使用 tsvector + tsquery）"""
        cursor = self.conn.cursor()

        try:
            # 预处理查询：将空格替换为 |（OR 语义）以支持多关键词
            processed_query = self._preprocess_fts_query(query)

            # PostgreSQL 全文搜索：使用 to_tsquery 和 ts_rank
            cursor.execute("""
                SELECT c.id, ts_rank(c.text_vec, plainto_tsquery(%s)) as score
                FROM chunks c
                WHERE c.text_vec @@ plainto_tsquery(%s)
                ORDER BY score DESC
                LIMIT %s
            """, (processed_query, processed_query, top_k))

            results = cursor.fetchall()
            # ts_rank 返回的是排名分数，越大越相关
            # 转换为负数以便与 SQLite BM25 分数格式一致（越小越相关）
            return [(row[0], -row[1]) for row in results if row[1] > 0]
        except Exception as e:
            logger.warning(f"后端日志：PostgreSQL 全文检索失败: {e}")
            return []

    @staticmethod
    def _weighted_rrf_fusion(
        vector_results: List[Tuple[int, float]],
        fts_results: List[Tuple[int, float]],
        k: int = 60,
        vector_weight: float = 0.7,
        fts_weight: float = 0.3
    ) -> List[Tuple[int, float]]:
        """
        加权 RRF（Reciprocal Rank Fusion）融合

        Args:
            vector_results: [(chunk_id, similarity), ...] 来自向量检索
            fts_results: [(chunk_id, bm25_score), ...] 来自 FTS5 检索
            k: RRF 参数（通常 60）
            vector_weight: 向量检索权重（0-1）
            fts_weight: FTS5 权重（0-1）

        Returns:
            [(chunk_id, fused_score), ...]
        """
        scores: Dict[int, float] = {}

        # 向量检索：按排名计算分数，乘以权重
        for rank, (chunk_id, _) in enumerate(vector_results):
            rrf_score = 1 / (k + rank + 1)
            scores[chunk_id] = scores.get(chunk_id, 0) + rrf_score * vector_weight

        # FTS5 检索：按排名计算分数，乘以权重
        for rank, (chunk_id, _) in enumerate(fts_results):
            rrf_score = 1 / (k + rank + 1)
            scores[chunk_id] = scores.get(chunk_id, 0) + rrf_score * fts_weight

        # 按融合分数排序
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results

    @staticmethod
    def _normalize_scores(results: List[Tuple[int, float]]) -> List[Tuple[int, float]]:
        """
        归一化分数到 0-1 区间

        使用 min-max 归一化，使分数更直观
        """
        if not results:
            return results

        if len(results) == 1:
            return [(results[0][0], 1.0)]

        max_score = results[0][1]
        min_score = results[-1][1]

        if max_score == min_score:
            # 所有分数相同，统一归一化为中间值
            return [(cid, 0.5) for cid, _ in results]

        normalized = [
            (cid, (score - min_score) / (max_score - min_score))
            for cid, score in results
        ]
        return normalized

    def _build_results(self, fused: List[Tuple[int, float]]) -> List[Dict[str, Any]]:
        """构建完整的检索结果"""
        if not fused:
            return []

        chunk_ids = [chunk_id for chunk_id, _ in fused]
        placeholder = "%s"
        placeholders = ','.join([placeholder] * len(chunk_ids))

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
