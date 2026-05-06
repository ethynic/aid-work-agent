"""
知识库服务 - 文档上传、解析、向量化的业务逻辑
"""

import os
import json
import uuid
from contextlib import closing
from pathlib import Path
from typing import Optional, List, Dict, Any
import logging

from src.knowledge.parsers.parser_factory import parser_factory
from src.knowledge.chunker import TextChunker
from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client, sanitize_error_info
from src.knowledge.vector_db.vector_db import get_vector_db
from src.config.settings import settings
from src.db.database import get_db_connection
from src.llm.gateway import LLMGateway

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """知识库服务"""

    def __init__(self):
        self.chunk_size = getattr(settings, 'knowledge_chunk_size', 512)
        self.chunk_overlap = getattr(settings, 'knowledge_chunk_overlap', 64)
        # 知识库文档路径（保持向后兼容：如果配置了 knowledge_upload_path，使用自定义值）
        # 新默认结构: storage/uploads/{tenant_id}/knowledge/
        if hasattr(settings, 'knowledge_upload_path'):
            self.base_upload_path = Path(getattr(settings, 'knowledge_upload_path'))
        else:
            # 默认使用统一存储根路径，_get_upload_path 会拼接 tenant_id/knowledge
            self.base_upload_path = Path(settings.storage.uploads_dir)
        self.base_upload_path.mkdir(parents=True, exist_ok=True)

        self.chunker = TextChunker(
            chunk_size=self.chunk_size,
            overlap=self.chunk_overlap
        )
        # LLM 网关（懒加载）
        self._llm_gateway = None

    @property
    def llm_gateway(self):
        """获取 LLM 网关实例（懒加载）"""
        if self._llm_gateway is None:
            self._llm_gateway = LLMGateway()
        return self._llm_gateway

    async def generate_summary(self, text: str, title: str, max_length: int = 300) -> str:
        """
        调用 LLM 生成文档摘要

        Args:
            text: 文档原文
            title: 文档标题
            max_length: 摘要最大长度（字符）

        Returns:
            文档摘要字符串
        """
        if not text or not text.strip():
            return ""

        # 限制输入文本长度，避免超出 LLM 上下文限制
        # 取前 5000 字符（大约 1250 tokens）用于生成摘要
        truncated_text = text[:5000]
        if len(text) > 5000:
            truncated_text += "..."

        prompt = f"""请为以下文档生成一个简洁的中文摘要，不超过 {max_length} 个字符。

文档标题: {title}

文档内容:
{truncated_text}

要求:
1. 准确概括文档的核心内容
2. 语言简洁通顺
3. 不要包含"摘要"、"本文"等字样
4. 直接输出摘要内容，不需要其他说明
"""

        try:
            response = await self.llm_gateway.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500
            )
            summary = response.get("content", "") or ""
            return summary.strip()
        except Exception as e:
            logger.warning(f"生成文档摘要失败: {e}")
            return ""

    def _get_upload_path(self, tenant_id: Optional[str] = None) -> Path:
        """获取知识库文档上传路径
        有租户: storage/uploads/{tenant_id}/knowledge/
        无租户: storage/uploads/knowledge/
        """
        if hasattr(settings, 'knowledge_upload_path'):
            # 自定义路径模式：保持原有拼接逻辑
            if tenant_id:
                path = self.base_upload_path / tenant_id
            else:
                path = self.base_upload_path
        else:
            # 新默认结构：统一使用 storage/uploads
            if tenant_id:
                path = self.base_upload_path / tenant_id / "knowledge"
            else:
                path = self.base_upload_path / "knowledge"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _get_db_connection(self):
        """获取数据库连接（使用统一的数据库连接管理）"""
        return get_db_connection()

    async def upload_document(
        self,
        file_path: str,
        file_filename: str,
        user_id: Optional[int] = None,
        tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        上传并处理文档

        Args:
            file_path: 文件保存路径
            file_filename: 原始文件名
            user_id: 用户 ID
            tenant_id: 租户 ID

        Returns:
            处理结果
        """
        try:
            # 1. 解析文档
            parser = parser_factory.get_parser(file_path)
            if not parser:
                raise ValueError(f"不支持的文件格式: {file_filename}")

            parse_result = await parser.parse(file_path)

            # 2. 分块（将文件名加入文本内容，便于搜索时匹配文件名）
            text_with_filename = f"文档标题：{file_filename}\n\n{parse_result.text}"
            chunks = self.chunker.chunk(text_with_filename)
            if not chunks:
                raise ValueError("文档内容为空或无法提取文本")

            # 3. 向量化（批量）
            qwen_keys = settings.llm.qwen.get_effective_keys()
            if not qwen_keys:
                raise ValueError("QWEN API key 未配置，请检查 API_KEYS 环境变量")
            # TODO: 后续支持 key 池轮询或并发控制，避免单 key 限流
            embedding_client = TextEmbeddingV3Client(api_key=qwen_keys[0])
            chunk_texts = [c["text"] for c in chunks]
            embeddings = await embedding_client.embed_batch(chunk_texts)

            # 4. 生成文档摘要
            summary = await self.generate_summary(
                text=parse_result.text or "",
                title=file_filename
            )

            # 5. 保存到数据库
            with self._get_db_connection() as conn:
                cursor = conn.cursor()

                # 获取文件扩展名
                ext = Path(file_filename).suffix.lower().lstrip('.')

                # 插入文档记录（PostgreSQL 使用 RETURNING 获取 ID）
                cursor.execute("""
                    INSERT INTO documents (
                        user_id, tenant_id, title, source_type, file_type, file_path,
                        file_size, total_chunks, embedding_model,
                        raw_text, metadata, summary
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    user_id,
                    tenant_id,
                    file_filename,
                    "file",
                    ext,
                    file_path,
                    os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                    len(chunks),
                    "text-embedding-v3",
                    parse_result.text if parse_result.text else None,
                    json.dumps(parse_result.metadata) if parse_result.metadata else None,
                    summary or None
                ))
                # row 是 tuple: (id,)，对应 RETURNING id
                doc_id = cursor.fetchone()[0]  # row["id"]

                # 插入 chunks
                chunk_ids = []
                for chunk in chunks:
                    cursor.execute("""
                        INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        doc_id,
                        chunk["index"],
                        chunk["text"],
                        chunk["tokens"],
                        json.dumps({"char_count": len(chunk["text"])})
                    ))
                    # row 是 tuple: (id,)，对应 RETURNING id
                    chunk_ids.append(cursor.fetchone()[0])  # row["id"]

                # 插入向量（复用同一个数据库连接，避免锁冲突）
                vector_db = get_vector_db(dimension=1024, conn=conn)
                await vector_db.insert(chunk_ids, embeddings)

                # FTS5 触发器会自动处理，无需手动插入

                conn.commit()

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
            with self._get_db_connection() as conn:
                cursor = conn.cursor()

                # 获取文件路径
                cursor.execute("SELECT file_path FROM documents WHERE id = %s", (doc_id,))
                row = cursor.fetchone()
                if not row:
                    return {"success": False, "error": "文档不存在"}

                # row 是 tuple: (file_path,)，对应 SELECT file_path
                file_path = row[0]  # row["file_path"]

                # 删除向量（复用同一个数据库连接，避免锁冲突）
                vector_db = get_vector_db(dimension=1024, conn=conn)
                await vector_db.delete_by_doc(doc_id)

                # 删除 chunks（FTS 触发器会自动删除）
                cursor.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))

                # 删除文档记录
                cursor.execute("DELETE FROM documents WHERE id = %s", (doc_id,))

                conn.commit()

            # 删除文件
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                # 清理空的租户目录
                try:
                    parent_dir = os.path.dirname(file_path)
                    if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                        os.rmdir(parent_dir)
                        logger.info(f"已清理空目录: {parent_dir}")
                except OSError:
                    pass  # 目录非空或无权限，忽略

            logger.info(f"后端日志：文档删除成功，doc_id={doc_id}")
            return {"success": True, "message": "文档已删除"}

        except Exception as e:
            logger.error(f"后端日志：文档删除失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def count_documents(self, user_id: Optional[int] = None, tenant_id: Optional[str] = None) -> int:
        """获取文档总数"""
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()

                conditions = []
                params = []

                if tenant_id is not None:
                    conditions.append("tenant_id = %s")
                    params.append(tenant_id)
                if user_id:
                    conditions.append("user_id = %s")
                    params.append(user_id)

                where_clause = " AND ".join(conditions)
                if where_clause:
                    cursor.execute(f"SELECT COUNT(*) FROM documents WHERE {where_clause}", params)
                else:
                    cursor.execute("SELECT COUNT(*) FROM documents")

                # row 是 tuple: (count,)，对应 SELECT COUNT(*)
                count = cursor.fetchone()[0]  # row["count"]
                return count
        except Exception as e:
            logger.error(f"后端日志：获取文档总数失败: {e}", exc_info=True)
            return 0

    def list_documents(
        self,
        user_id: Optional[int] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """获取文档列表"""
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                placeholder = "%s"

                conditions = []
                params = []

                if tenant_id is not None:
                    conditions.append(f"tenant_id = {placeholder}")
                    params.append(tenant_id)
                if user_id:
                    conditions.append(f"user_id = {placeholder}")
                    params.append(user_id)

                where_clause = ""
                if conditions:
                    where_clause = "WHERE " + " AND ".join(conditions)

                cursor.execute(f"""
                    SELECT id, title, source_type, file_type, file_path, file_size,
                           total_chunks, created_at, summary
                    FROM documents
                    {where_clause}
                    ORDER BY created_at DESC
                    LIMIT {limit} OFFSET {offset}
                """, params)

                rows = cursor.fetchall()

                # 转换 datetime 为字符串
                result = []
                for row in rows:
                    doc = dict(row)
                    if doc.get("created_at"):
                        doc["created_at"] = doc["created_at"].isoformat()
                    result.append(doc)
                return result

        except Exception as e:
            logger.error(f"后端日志：获取文档列表失败: {e}", exc_info=True)
            return []

    def get_document_chunks(self, doc_id: int) -> List[Dict[str, Any]]:
        """获取文档的所有分块"""
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT id, chunk_index, text, tokens, metadata
                    FROM chunks
                    WHERE doc_id = %s
                    ORDER BY chunk_index
                """, (doc_id,))

                rows = cursor.fetchall()

                # 转换为 dict 以便访问列名
                rows = [dict(row) for row in rows]

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

    async def search_documents(
        self,
        query: str,
        user_id: Optional[int] = None,
        tenant_id: Optional[str] = None,
        top_k: int = 10
    ) -> Dict[str, Any]:
        """
        根据内容搜索文档（混合检索：向量 + FTS5 + RRF）

        Args:
            query: 搜索关键词
            user_id: 用户 ID（权限控制，暂未实现）
            tenant_id: 租户 ID
            top_k: 返回结果数量

        Returns:
            搜索结果
        """
        try:
            # 初始化 HybridRetriever
            from src.knowledge.retriever.hybrid_retriever import HybridRetriever
            from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
            from src.knowledge.vector_db.vector_db import get_vector_db

            qwen_keys = settings.llm.qwen.get_effective_keys()
            if not qwen_keys:
                return {
                    "success": False,
                    "error": "API key 未配置",
                    "results": [],
                    "count": 0
                }

            with self._get_db_connection() as conn:
                embedding_client = TextEmbeddingV3Client(api_key=qwen_keys[0])
                vector_db = get_vector_db(dimension=1024, conn=conn)

                retriever = HybridRetriever(
                    vector_db=vector_db,
                    embedding_client=embedding_client,
                    conn=conn
                )

                # 执行混合检索
                results = await retriever.retrieve(query=query, top_k=top_k, user_id=user_id)

                # 提取文档标题
                if results:
                    doc_ids = {r["doc_id"] for r in results}
                    placeholders = ','.join(['%s'] * len(doc_ids))

                    cursor = conn.cursor()
                    # 按 tenant_id 过滤，确保租户隔离
                    if tenant_id is not None:
                        cursor.execute(f"""
                            SELECT id, title, file_type, file_path FROM documents
                            WHERE id IN ({placeholders}) AND tenant_id = %s
                        """, list(doc_ids) + [tenant_id])
                    else:
                        cursor.execute(f"""
                            SELECT id, title, file_type, file_path FROM documents WHERE id IN ({placeholders})
                        """, list(doc_ids))

                    # 转换为 dict 以便访问列名
                    rows = [dict(row) for row in cursor.fetchall()]
                    doc_info = {row["id"]: {"title": row["title"], "file_type": row["file_type"], "file_path": row["file_path"]} for row in rows}

                    # 格式化结果
                    formatted_results = [
                        {
                            "doc_id": r["doc_id"],
                            "chunk_id": r["chunk_id"],
                            "text": r["text"],
                            "title": doc_info.get(r["doc_id"], {}).get("title", "未知文档"),
                            "file_type": doc_info.get(r["doc_id"], {}).get("file_type", ""),
                            "file_path": doc_info.get(r["doc_id"], {}).get("file_path", ""),
                            "score": round(r["score"], 4)
                        }
                        for r in results
                    ]
                else:
                    formatted_results = []

                logger.info(f"后端日志：文档搜索完成，查询={query}, 结果数={len(formatted_results)}")
                return {
                    "success": True,
                    "results": formatted_results,
                    "count": len(formatted_results)
                }

        except Exception as e:
            error_str = str(e)
            if "=***" not in error_str:
                error_str = sanitize_error_info(error_str)
            logger.error(f"后端日志：文档搜索失败: {error_str}", exc_info=True)
            return {
                "success": False,
                "error": "搜索失败，请稍后重试",
                "debug": error_str,
                "results": [],
                "count": 0
            }


# 全局单例
knowledge_service = KnowledgeBaseService()
