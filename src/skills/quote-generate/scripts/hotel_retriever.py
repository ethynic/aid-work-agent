# -*- coding: utf-8 -*-
"""
酒店专用向量检索器

在 documents + chunks + chunks_vec 知识库中检索酒店资源。
- chunk_index=0: 酒店信息摘要（已向量化）
- chunk_index=1: 价格明细表（不向量化）
- source_type='hotel_resource'
"""

import json
from typing import Any, Dict, List, Optional

from loguru import logger


class HotelRetriever:
    """酒店专用向量检索器"""

    SOURCE_TYPE = "hotel_resource"

    def __init__(self):
        self._embedding_client = None

    # ----------------------------------------------------------
    # 基础设施
    # ----------------------------------------------------------

    def _get_embedding_client(self):
        """延迟初始化 embedding 客户端"""
        if self._embedding_client is None:
            from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
            from src.config.settings import settings

            qwen_keys = settings.llm.qwen.get_effective_keys()
            if not qwen_keys:
                raise ValueError("QWEN API key 未配置，请检查 API_KEYS 环境变量")
            self._embedding_client = TextEmbeddingV3Client(api_key=qwen_keys[0])
        return self._embedding_client

    def _get_conn(self):
        """获取数据库连接"""
        from src.db.database import get_db_connection
        return get_db_connection()

    def _embed(self, text: str) -> List[float]:
        """同步调用 embedding（适配子进程场景）"""
        import dashscope
        from src.config.settings import settings

        qwen_keys = settings.llm.qwen.get_effective_keys()
        if not qwen_keys:
            raise ValueError("QWEN API key 未配置，请检查 API_KEYS 环境变量")
        dashscope.api_key = qwen_keys[0]

        resp = dashscope.TextEmbedding.call(
            model="text-embedding-v3",
            input=text,
            dimension=1024,
            text_type="document",
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Embedding API 调用失败: {resp.message}")
        return resp.output["embeddings"][0]["embedding"]

    # ----------------------------------------------------------
    # 搜索
    # ----------------------------------------------------------

    def search_by_name(self, tenant_id: str, name_query: str, top_k: int = 5) -> List[Dict]:
        """精确/模糊名称匹配（ILIKE）"""
        with self._get_conn() as conn:
            conn.execute("""
                SELECT c.doc_id, c.text, d.title, d.metadata, d.file_path, d.created_at
                FROM chunks c
                JOIN documents d ON c.doc_id = d.id
                WHERE d.source_type = %s
                  AND d.tenant_id = %s
                  AND c.chunk_index = 0
                  AND c.text ILIKE %s
                ORDER BY d.id
                LIMIT %s
            """, (self.SOURCE_TYPE, tenant_id, f'%{name_query}%', top_k))
            rows = conn.fetchall()

        results = []
        for row in rows:
            meta = row["metadata"]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            results.append({
                "doc_id": row["doc_id"],
                "title": row["title"],
                "info": row["text"],
                "metadata": meta or {},
                "source_file": row["file_path"] or "",
                "created_at": str(row["created_at"]) if row["created_at"] else "",
            })
        return results

    def search_by_vector(self, tenant_id: str, query: str, top_k: int = 5) -> List[Dict]:
        """向量语义搜索"""
        embedding = self._embed(query)
        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

        with self._get_conn() as conn:
            conn.execute("""
                SELECT cv.chunk_id,
                       cv.embedding <=> %s::vector AS distance,
                       c.doc_id, c.text, d.title, d.metadata, d.file_path, d.created_at
                FROM chunks_vec cv
                JOIN chunks c ON cv.chunk_id = c.id
                JOIN documents d ON c.doc_id = d.id
                WHERE d.source_type = %s
                  AND d.tenant_id = %s
                  AND c.chunk_index = 0
                ORDER BY distance
                LIMIT %s
            """, (embedding_str, self.SOURCE_TYPE, tenant_id, top_k))
            rows = conn.fetchall()

        results = []
        for row in rows:
            meta = row["metadata"]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            results.append({
                "doc_id": row["doc_id"],
                "title": row["title"],
                "info": row["text"],
                "score": round(1.0 - float(row["distance"]), 4),
                "metadata": meta or {},
                "source_file": row["file_path"] or "",
                "created_at": str(row["created_at"]) if row["created_at"] else "",
            })
        logger.info(f"[HotelRetriever] vector search '{query}' → {len(results)} results (tenant={tenant_id})")
        return results

    def search(self, tenant_id: str, query: str, top_k: int = 5) -> List[Dict]:
        """组合搜索：名称匹配优先，无结果再走向量搜索"""
        name_results = self.search_by_name(tenant_id, query, top_k)
        if name_results:
            return name_results
        return self.search_by_vector(tenant_id, query, top_k)

    def list_all(self, tenant_id: str, limit: int = 200, offset: int = 0) -> Dict:
        """列出所有酒店知识库文档（分页）"""
        with self._get_conn() as conn:
            conn.execute(
                "SELECT COUNT(*) AS cnt FROM documents WHERE source_type = %s AND tenant_id = %s",
                (self.SOURCE_TYPE, tenant_id),
            )
            total = conn.fetchone()["cnt"]

            conn.execute("""
                SELECT d.id AS doc_id, d.title, d.file_path, d.metadata, d.created_at,
                       c.text AS info
                FROM documents d
                LEFT JOIN chunks c ON c.doc_id = d.id AND c.chunk_index = 0
                WHERE d.source_type = %s AND d.tenant_id = %s
                ORDER BY d.id
                LIMIT %s OFFSET %s
            """, (self.SOURCE_TYPE, tenant_id, limit, offset))
            rows = conn.fetchall()

        items = []
        for row in rows:
            meta = row["metadata"]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            items.append({
                "doc_id": row["doc_id"],
                "title": row["title"],
                "info": row["info"] or "",
                "metadata": meta or {},
                "source_file": row["file_path"] or "",
                "created_at": str(row["created_at"]) if row["created_at"] else "",
            })

        return {"total": total, "items": items}

    # ----------------------------------------------------------
    # 单文档读取
    # ----------------------------------------------------------

    def get_hotel_info(self, doc_id: int) -> Optional[str]:
        """取出酒店信息摘要（chunk_index=0）"""
        with self._get_conn() as conn:
            conn.execute(
                "SELECT text FROM chunks WHERE doc_id = %s AND chunk_index = 0",
                (doc_id,)
            )
            row = conn.fetchone()
            return row["text"] if row else None

    def get_price_table(self, doc_id: int) -> Optional[str]:
        """取出酒店价格明细表（chunk_index=1）"""
        with self._get_conn() as conn:
            conn.execute(
                "SELECT text FROM chunks WHERE doc_id = %s AND chunk_index = 1",
                (doc_id,)
            )
            row = conn.fetchone()
            return row["text"] if row else None

    # ----------------------------------------------------------
    # 导入
    # ----------------------------------------------------------

    def import_hotel(self, tenant_id: str, hotel_name: str, region: str,
                     info_text: str, price_table_text: str,
                     metadata: Optional[Dict] = None,
                     source_file: str = "") -> int:
        """
        导入一个酒店到知识库。

        Args:
            tenant_id: 租户 ID
            hotel_name: 酒店名称
            region: 区域
            info_text: 酒店信息摘要（chunk 0，会向量化）
            price_table_text: 价格明细表（chunk 1，不向量化）
            metadata: 额外元信息

        Returns:
            doc_id
        """
        embedding = self._embed(info_text)
        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        with self._get_conn() as conn:
            # 1. 创建 document
            conn.execute("""
                INSERT INTO documents (tenant_id, title, source_type, file_type, file_path,
                                       total_chunks, embedding_model, metadata)
                VALUES (%s, %s, %s, 'text', %s, 2, 'text-embedding-v3', %s)
                RETURNING id
            """, (tenant_id, f"酒店：{hotel_name}", self.SOURCE_TYPE, source_file, meta_json))
            doc_id = conn.fetchone()["id"]

            # 2. 插入 chunk 0（酒店信息摘要）
            conn.execute("""
                INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata)
                VALUES (%s, 0, %s, %s, %s)
                RETURNING id
            """, (doc_id, info_text, len(info_text),
                  json.dumps({"type": "hotel_info", "region": region}, ensure_ascii=False)))
            chunk_id_0 = conn.fetchone()["id"]

            # 3. 插入 chunk 1（价格明细表）
            conn.execute("""
                INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata)
                VALUES (%s, 1, %s, %s, %s)
            """, (doc_id, price_table_text, len(price_table_text),
                  json.dumps({"type": "price_table"}, ensure_ascii=False)))

            # 4. 向量化 chunk 0
            conn.execute("""
                INSERT INTO chunks_vec (chunk_id, embedding)
                VALUES (%s, %s::vector)
            """, (chunk_id_0, embedding_str))

            conn.commit()

        logger.info(f"[HotelRetriever] 导入酒店 '{hotel_name}' 成功, doc_id={doc_id}")
        return doc_id
