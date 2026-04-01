"""
SQLite + sqlite-vec 向量数据库实现
"""

import sqlite3
from typing import List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class VectorDatabase:
    """向量数据库接口"""

    async def insert(self, chunk_ids: List[int], embeddings: List[List[float]]) -> None:
        """插入向量"""
        raise NotImplementedError

    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """
        向量相似度搜索

        Returns:
            List[(chunk_id, similarity)]
        """
        raise NotImplementedError

    async def delete_by_doc(self, doc_id: int) -> None:
        """删除文档的所有向量"""
        raise NotImplementedError


class VectorDBSQLite(VectorDatabase):
    """SQLite + sqlite-vec 实现"""

    def __init__(self, db_path: str, dimension: int = 1536):
        self.db_path = db_path
        self.dimension = dimension
        self.conn: Optional[sqlite3.Connection] = None
        self._connect()

    def _connect(self):
        """建立数据库连接，加载 sqlite-vec 扩展"""
        import sqlite_vec

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        # 加载 sqlite-vec 扩展
        sqlite_vec.load(self.conn)

        # 确保 chunks_vec 虚拟表存在
        self._ensure_table()

    def _ensure_table(self):
        """确保向量表存在"""
        cursor = self.conn.cursor()
        # sqlite-vec 不支持参数绑定，维度必须是硬编码
        cursor.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                chunk_id INTEGER PRIMARY KEY,
                embedding float[{self.dimension}]
            )
        """)
        self.conn.commit()

    async def insert(self, chunk_ids: List[int], embeddings: List[List[float]]) -> None:
        """批量插入向量"""
        if not chunk_ids:
            return

        cursor = self.conn.cursor()

        # 将 embeddings 转换为可序列化的格式
        data = [
            (chunk_id, list(embedding))
            for chunk_id, embedding in zip(chunk_ids, embeddings)
        ]

        cursor.executemany(
            "INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
            data
        )

        self.conn.commit()
        logger.info(f"后端日志：插入了 {len(chunk_ids)} 个向量")

    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """向量相似度搜索（余弦相似度）"""
        cursor = self.conn.cursor()

        # sqlite-vec 使用 L2 距离，距离越小越相似
        cursor.execute("""
            SELECT chunk_id, distance
            FROM chunks_vec
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
        """, (query_embedding, top_k))

        results = cursor.fetchall()

        # 将 L2 距离转换为相似度（距离取负，越大越相似）
        return [(row["chunk_id"], -row["distance"]) for row in results]

    async def delete_by_doc(self, doc_id: int) -> None:
        """删除文档的所有向量"""
        cursor = self.conn.cursor()

        cursor.execute("""
            DELETE FROM chunks_vec
            WHERE chunk_id IN (
                SELECT id FROM chunks WHERE doc_id = ?
            )
        """, (doc_id,))

        self.conn.commit()
        logger.info(f"后端日志：删除了文档 {doc_id} 的所有向量")

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None
