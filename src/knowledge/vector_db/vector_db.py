"""
向量数据库实现
支持 SQLite + sqlite-vec 和 PostgreSQL + pgvector
"""

import json
import os
import sqlite3
from typing import List, Tuple, Optional
import logging

from loguru import logger

from src.db.database import DB_TYPE, get_db_connection, get_db_placeholder, DB_CONFIG

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

    def close(self):
        """关闭数据库连接"""
        raise NotImplementedError


class VectorDBSQLite(VectorDatabase):
    """SQLite + sqlite-vec 实现"""

    def __init__(self, db_path: str, dimension: int = 1024, conn: sqlite3.Connection = None):
        self.db_path = db_path
        self.dimension = dimension
        if conn is not None:
            self.conn = conn
            self._external_conn = True
            # 外部连接也需要加载 sqlite-vec 扩展和设置 PRAGMA
            import sqlite_vec
            sqlite_vec.load(self.conn)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=10000")
        else:
            self._external_conn = False
            self.conn: Optional[sqlite3.Connection] = None
            self._connect()
        self._ensure_table()

    def _connect(self):
        """建立数据库连接，加载 sqlite-vec 扩展"""
        import sqlite_vec

        self.conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=10.0
        )
        self.conn.row_factory = sqlite3.Row

        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")

        sqlite_vec.load(self.conn)

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

        # sqlite-vec 要求 embedding 为 JSON 数组字符串格式
        data = [
            (chunk_id, json.dumps(embedding))
            for chunk_id, embedding in zip(chunk_ids, embeddings)
        ]

        cursor.executemany(
            "INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
            data
        )

        # 外部连接由调用方统一提交，避免中间提交导致锁问题
        if not self._external_conn:
            self.conn.commit()
        logger.info(f"后端日志：插入了 {len(chunk_ids)} 个向量")

    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """向量相似度搜索"""
        cursor = self.conn.cursor()

        # sqlite-vec 使用 L2 距离，距离越小越相似
        # 注意：embedding MATCH ? 需要 JSON 字符串格式，与 insert 时一致
        cursor.execute("""
            SELECT chunk_id, distance
            FROM chunks_vec
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
        """, (json.dumps(query_embedding), top_k))

        results = cursor.fetchall()

        # 将 L2 距离转换为余弦相似度
        # 归一化向量下：cos_sim = 1 - L2^2 / 2
        # 范围 [0, 1]，1 表示完全相同，0 表示正交/无关
        cosine_results = []
        for row in results:
            l2_dist = row["distance"]
            # 防止浮点误差导致超出 [0, 1] 范围
            cos_sim = max(0.0, min(1.0, 1.0 - (l2_dist ** 2) / 2.0))
            cosine_results.append((row["chunk_id"], cos_sim))

        return cosine_results

    async def delete_by_doc(self, doc_id: int) -> None:
        """删除文档的所有向量"""
        cursor = self.conn.cursor()

        cursor.execute("""
            DELETE FROM chunks_vec
            WHERE chunk_id IN (
                SELECT id FROM chunks WHERE doc_id = ?
            )
        """, (doc_id,))

        # 外部连接由调用方统一提交，避免中间提交导致锁问题
        if not self._external_conn:
            self.conn.commit()
        logger.info(f"后端日志：删除了文档 {doc_id} 的所有向量")

    def close(self):
        """关闭数据库连接（仅关闭自行创建的连接）"""
        if self.conn and not self._external_conn:
            self.conn.close()
            self.conn = None


class VectorDBPostgreSQL(VectorDatabase):
    """PostgreSQL + pgvector 实现"""

    def __init__(self, dimension: int = 1024, conn=None):
        self.dimension = dimension
        self._external_conn = conn is not None

        if conn is not None:
            self.conn = conn
        else:
            self._connect()

        self._ensure_table()

    def _connect(self):
        """建立 PostgreSQL 数据库连接"""
        import psycopg2
        from psycopg2 import extras as pg_extras

        self.conn = psycopg2.connect(
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            database=DB_CONFIG["database"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"]
        )
        self.conn.autocommit = False

    def _ensure_table(self):
        """确保向量表存在并启用 pgvector 扩展"""
        cursor = self.conn.cursor()

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

        self.conn.commit()
        logger.info(f"PostgreSQL pgvector 表初始化完成，维度: {self.dimension}")

    async def insert(self, chunk_ids: List[int], embeddings: List[List[float]]) -> None:
        """批量插入向量"""
        if not chunk_ids:
            return

        cursor = self.conn.cursor()

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
            self.conn.commit()

        logger.info(f"后端日志：插入了 {len(chunk_ids)} 个向量 (pgvector)")

    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """向量相似度搜索（使用余弦相似度）"""
        cursor = self.conn.cursor()

        # 将查询向量转换为 vector 类型字符串
        vector_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        # pgvector 使用余弦距离 (<=>)，距离越小越相似
        # 为了返回相似度（0-1），使用 1 - distance
        cursor.execute("""
            SELECT chunk_id, embedding <=> %s::vector as distance
            FROM chunks_vec
            ORDER BY distance
            LIMIT %s
        """, (vector_str, top_k))

        results = cursor.fetchall()

        # 将余弦距离转换为余弦相似度
        cosine_results = []
        for row in results:
            distance = row[1]
            # 距离范围 [0, 2]，相似度 = 1 - distance/2
            similarity = max(0.0, min(1.0, 1.0 - distance / 2.0))
            cosine_results.append((row[0], similarity))

        return cosine_results

    async def delete_by_doc(self, doc_id: int) -> None:
        """删除文档的所有向量"""
        cursor = self.conn.cursor()

        cursor.execute("""
            DELETE FROM chunks_vec
            WHERE chunk_id IN (
                SELECT id FROM chunks WHERE doc_id = %s
            )
        """, (doc_id,))

        if not self._external_conn:
            self.conn.commit()

        logger.info(f"后端日志：删除了文档 {doc_id} 的所有向量 (pgvector)")

    def close(self):
        """关闭数据库连接（仅关闭自行创建的连接）"""
        if self.conn and not self._external_conn:
            self.conn.close()
            self.conn = None


def get_vector_db(dimension: int = 1024, conn=None) -> VectorDatabase:
    """根据数据库类型获取对应的向量数据库实例"""
    if DB_TYPE == "postgresql":
        return VectorDBPostgreSQL(dimension=dimension, conn=conn)
    else:
        # 获取 SQLite 数据库路径
        from src.db.database import get_sqlite_path
        db_path = get_sqlite_path()
        return VectorDBSQLite(dimension=dimension, db_path=db_path, conn=conn)