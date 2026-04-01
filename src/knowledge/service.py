"""
知识库服务 - 文档上传、解析、向量化的业务逻辑
"""

import os
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any
import logging

from src.knowledge.parsers.parser_factory import parser_factory
from src.knowledge.chunker import TextChunker
from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client, sanitize_error_info
from src.knowledge.vector_db.vector_db import VectorDBSQLite
from src.config.settings import settings

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """知识库服务"""

    def __init__(self):
        self.chunk_size = getattr(settings, 'knowledge_chunk_size', 512)
        self.chunk_overlap = getattr(settings, 'knowledge_chunk_overlap', 64)
        self.upload_path = Path(getattr(settings, 'knowledge_upload_path', 'uploads/knowledge'))
        self.upload_path.mkdir(parents=True, exist_ok=True)

        # 数据库路径
        database_url = os.environ.get("DATABASE_URL", "sqlite:///./aid_work_agent.db")
        self.db_path = database_url.replace("sqlite:///", "")

        self.chunker = TextChunker(
            chunk_size=self.chunk_size,
            overlap=self.chunk_overlap
        )

    def _get_db_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30.0  # 等待锁释放的最长时间（秒）
        )
        conn.row_factory = sqlite3.Row
        # 启用 WAL 模式，提高并发性能
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")  # 30秒 busy timeout
        return conn

    async def upload_document(
        self,
        file_path: str,
        file_filename: str,
        user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        上传并处理文档

        Args:
            file_path: 文件保存路径
            file_filename: 原始文件名
            user_id: 用户 ID

        Returns:
            处理结果
        """
        try:
            # 1. 解析文档
            parser = parser_factory.get_parser(file_path)
            if not parser:
                raise ValueError(f"不支持的文件格式: {file_filename}")

            parse_result = await parser.parse(file_path)

            # 2. 分块
            chunks = self.chunker.chunk(parse_result.text)
            if not chunks:
                raise ValueError("文档内容为空或无法提取文本")

            # 3. 向量化（批量）
            qwen_keys = settings.llm.qwen.get_effective_keys()
            if not qwen_keys:
                raise ValueError("QWEN API key 未配置，请检查 QWEN_API_KEYS 环境变量")
            # TODO: 后续支持 key 池轮询或并发控制，避免单 key 限流
            embedding_client = TextEmbeddingV3Client(api_key=qwen_keys[0])
            chunk_texts = [c["text"] for c in chunks]
            embeddings = await embedding_client.embed_batch(chunk_texts)

            # 4. 保存到数据库
            conn = self._get_db_connection()
            cursor = conn.cursor()

            # 获取文件扩展名
            ext = Path(file_filename).suffix.lower().lstrip('.')

            # 插入文档记录
            cursor.execute("""
                INSERT INTO documents (
                    user_id, title, source_type, file_type, file_path,
                    file_size, total_chunks, embedding_model,
                    raw_text, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                file_filename,
                "file",
                ext,
                file_path,
                os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                len(chunks),
                "text-embedding-v3",
                parse_result.text[:10000] if parse_result.text else None,
                json.dumps(parse_result.metadata) if parse_result.metadata else None
            ))

            doc_id = cursor.lastrowid

            # 插入 chunks
            chunk_ids = []
            for chunk in chunks:
                cursor.execute("""
                    INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    doc_id,
                    chunk["index"],
                    chunk["text"],
                    chunk["tokens"],
                    json.dumps({"char_count": len(chunk["text"])})
                ))
                chunk_ids.append(cursor.lastrowid)

            # 插入向量（复用同一个数据库连接，避免锁冲突）
            vector_db = VectorDBSQLite(
                db_path=self.db_path,
                dimension=1536,
                conn=conn
            )
            await vector_db.insert(chunk_ids, embeddings)

            # 插入 FTS5（触发器会自动处理，但确保数据一致）
            for chunk in chunks:
                cursor.execute("""
                    INSERT INTO chunks_fts(rowid, text)
                    SELECT id, text FROM chunks WHERE id = last_insert_rowid()
                """)

            conn.commit()
            conn.close()

            logger.info(f"后端日志：文档上传成功，doc_id={doc_id}, 文件={file_filename}, chunks={len(chunks)}")

            return {
                "success": True,
                "document_id": doc_id,
                "title": file_filename,
                "total_chunks": len(chunks),
                "message": "文档处理成功"
            }

        except Exception as e:
            error_str = str(e)
            # 避免重复过滤
            if "=***" not in error_str:
                error_str = sanitize_error_info(error_str)
            logger.error(f"后端日志：文档上传失败: {error_str}", exc_info=True)
            return {
                "success": False,
                "error": "文档上传失败，请稍后重试",
                "debug": error_str,
                "document_id": None
            }

    async def delete_document(self, doc_id: int) -> Dict[str, Any]:
        """删除文档"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()

            # 获取文件路径
            cursor.execute("SELECT file_path FROM documents WHERE id = ?", (doc_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return {"success": False, "error": "文档不存在"}

            file_path = row["file_path"]

            # 删除向量（复用同一个数据库连接，避免锁冲突）
            vector_db = VectorDBSQLite(
                db_path=self.db_path,
                dimension=1024,
                conn=conn
            )
            await vector_db.delete_by_doc(doc_id)

            # 删除 chunks（FTS 触发器会自动删除）
            cursor.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

            # 删除文档记录
            cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

            conn.commit()
            conn.close()

            # 删除文件
            if file_path and os.path.exists(file_path):
                os.remove(file_path)

            logger.info(f"后端日志：文档删除成功，doc_id={doc_id}")
            return {"success": True, "message": "文档已删除"}

        except Exception as e:
            logger.error(f"后端日志：文档删除失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def list_documents(
        self,
        user_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """获取文档列表"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()

            if user_id:
                cursor.execute("""
                    SELECT id, title, source_type, file_type, file_size,
                           total_chunks, created_at
                    FROM documents
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                """, (user_id, limit, offset))
            else:
                cursor.execute("""
                    SELECT id, title, source_type, file_type, file_size,
                           total_chunks, created_at
                    FROM documents
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                """, (limit, offset))

            rows = cursor.fetchall()
            conn.close()

            return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"后端日志：获取文档列表失败: {e}", exc_info=True)
            return []

    def get_document_chunks(self, doc_id: int) -> List[Dict[str, Any]]:
        """获取文档的所有分块"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, chunk_index, text, tokens, metadata
                FROM chunks
                WHERE doc_id = ?
                ORDER BY chunk_index
            """, (doc_id,))

            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    "chunk_id": row["id"],
                    "index": row["chunk_index"],
                    "text": row["text"],
                    "tokens": row["tokens"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {}
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(f"后端日志：获取文档分块失败: {e}", exc_info=True)
            return []


# 全局单例
knowledge_service = KnowledgeBaseService()
