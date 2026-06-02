"""
向量数据库实现
PostgreSQL + pgvector
"""

import json
from typing import List, Tuple, Optional
import logging

from loguru import logger

from src.db.database import DB_CONFIG, get_db_connection, get_pooled_connection, return_pooled_connection
import psycopg2.extras

logger = logging.getLogger(__name__)


class VectorDatabase:
    """向量数据库接口"""

    async def insert(self, chunk_ids: List[int], embeddings: List[List[float]]) -> None:
        """插入向量"""
        raise NotImplementedError

    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        tenant_id: Optional[str] = None
    ) -> List[Tuple[int, float]]:
        """
        向量相似度搜索

        Args:
            tenant_id: 租户ID，提供时只搜索该租户的文档

        Returns:
            List[(chunk_id, similarity)]
        """
        raise NotImplementedError

    async def delete_by_doc(self, doc_id: int) -> None:
        """删除文档的所有向量"""
        raise NotImplementedError

    def close(self):
        """关闭数据库连接"""
        raise NotImplementedError


class VectorDBPostgreSQL(VectorDatabase):
    """PostgreSQL + pgvector 实现

    连接管理策略：
    - 如果传入 conn 参数：使用传入的连接（调用者负责连接生命周期）
    - 如果不传 conn：每次操作从连接池获取连接（自动健康检查和重连）
    """

    def __init__(self, dimension: int = 1024, conn=None):
        self.dimension = dimension
        self._external_conn = conn is not None
        self._pool_conn = conn  # 保存传入的连接引用

        # 初始化表结构（只在首次创建时执行）
        if conn is not None:
            self._ensure_table(conn)
        else:
            # 使用连接池连接来初始化表
            pool_conn = get_pooled_connection()
            try:
                self._ensure_table(pool_conn)
            finally:
                return_pooled_connection(pool_conn)

    def _ensure_table(self, conn):
        """确保向量表存在并启用 pgvector 扩展"""
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 确保 pgvector 扩展已启用
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")

        # 创建向量表（如果不存在）
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS chunks_vec (
                chunk_id INTEGER PRIMARY KEY,
                embedding vector({self.dimension})
            )
        """)

        # 创建索引（使用 HNSW 索引以获得更好的搜索性能）
        # 注意：IF NOT EXISTS 在并发场景下仍可能触发 UniqueViolation，需捕获处理
        try:
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunks_vec_embedding
                ON chunks_vec USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """)
        except Exception as e:
            # 忽略索引已存在的错误（并发场景或残留索引）
            if "duplicate key" not in str(e).lower() and "already exists" not in str(e).lower():
                raise

        conn.commit()
        logger.info(f"PostgreSQL pgvector 表初始化完成，维度: {self.dimension}")

    def _get_connection(self):
        """获取数据库连接

        Returns:
            - 如果传入了外部连接：返回外部连接
            - 否则从连接池获取有效连接
        """
        if self._external_conn:
            return self._pool_conn
        return get_pooled_connection()

    async def insert(self, chunk_ids: List[int], embeddings: List[List[float]]) -> None:
        """批量插入向量"""
        if not chunk_ids:
            return

        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # pgvector 使用数组格式 '[1.0, 2.0, ...]'
            data = [
                (chunk_id, embedding)
                for chunk_id, embedding in zip(chunk_ids, embeddings)
            ]

            # 使用 psycopg2.extras.execute_batch 进行批量插入
            from psycopg2 import extras
            extras.execute_batch(
                cursor,
                "INSERT INTO chunks_vec (chunk_id, embedding) VALUES (%s, %s)",
                data
            )

            if not self._external_conn:
                conn.commit()

            logger.info(f"后端日志：插入了 {len(chunk_ids)} 个向量 (pgvector)")
        finally:
            if not self._external_conn:
                return_pooled_connection(conn)

    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        tenant_id: Optional[str] = None
    ) -> List[Tuple[int, float]]:
        """向量相似度搜索（使用余弦相似度）"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # 将查询向量转换为 vector 类型字符串
            vector_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

            if tenant_id:
                # 指定租户：只查该租户的文档
                cursor.execute("""
                    SELECT cv.chunk_id, cv.embedding <=> %s::vector as distance
                    FROM chunks_vec cv
                    JOIN chunks c ON cv.chunk_id = c.id
                    JOIN documents d ON c.doc_id = d.id
                    WHERE d.tenant_id = %s
                    ORDER BY distance
                    LIMIT %s
                """, (vector_str, tenant_id, top_k))
            else:
                # 未指定租户：只查 demo 或无租户的数据，绝不泄露其他租户数据
                cursor.execute("""
                    SELECT cv.chunk_id, cv.embedding <=> %s::vector as distance
                    FROM chunks_vec cv
                    JOIN chunks c ON cv.chunk_id = c.id
                    JOIN documents d ON c.doc_id = d.id
                    WHERE d.tenant_id = 'demo' OR d.tenant_id IS NULL
                    ORDER BY distance
                    LIMIT %s
                """, (vector_str, top_k))

            results = cursor.fetchall()

            # 将余弦距离转换为余弦相似度
            cosine_results = []
            for row in results:
                # row 是 dict: {"chunk_id": ..., "distance": ...}，对应 SELECT chunk_id, ... as distance
                distance = row["distance"]
                # 距离范围 [0, 2]，相似度 = 1 - distance/2
                similarity = max(0.0, min(1.0, 1.0 - distance / 2.0))
                cosine_results.append((row["chunk_id"], similarity))

            return cosine_results
        finally:
            if not self._external_conn:
                return_pooled_connection(conn)

    async def delete_by_doc(self, doc_id: int) -> None:
        """删除文档的所有向量"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            cursor.execute("""
                DELETE FROM chunks_vec
                WHERE chunk_id IN (
                    SELECT id FROM chunks WHERE doc_id = %s
                )
            """, (doc_id,))

            if not self._external_conn:
                conn.commit()

            logger.info(f"后端日志：删除了文档 {doc_id} 的所有向量 (pgvector)")
        finally:
            if not self._external_conn:
                return_pooled_connection(conn)

    def close(self):
        """关闭数据库连接

        注意：当使用连接池时不实际关闭连接（由连接池管理）
        只有使用外部传入连接时才什么都不做（由调用者管理）
        """
        # 连接由连接池管理或外部传入，无需在此关闭
        pass


def get_vector_db(dimension: int = 1024, conn=None) -> VectorDatabase:
    """获取向量数据库实例（PostgreSQL + pgvector）"""
    return VectorDBPostgreSQL(dimension=dimension, conn=conn)