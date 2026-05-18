"""
景点知识库搜索工具 - 提供给 Agent 调用

使用向量语义搜索（pgvector cosine distance）在景点知识库中检索景点。
搜索范围限制在 source_type='attraction_resource' + chunk_index=0（景点信息摘要）。
"""

import json
from typing import Dict, Any, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.db.database import get_db_connection


class AttractionSearchInput(BaseModel):
    """搜索景点知识库参数"""
    query: str = Field(..., description="搜索关键词（城市名、区域名或景点名）")
    top_k: Optional[int] = Field(20, description="返回结果数量，默认20")


class AttractionSearchTool(BaseTool):
    """景点知识库搜索工具"""

    name = "attraction_search"
    description = (
        "从景点知识库中搜索景点信息。输入城市名、区域名或景点名称，"
        "返回匹配的景点列表（含名称、区域、类型、信息摘要等）。"
        "搜索结果来自景点知识库，后续报价时可直接使用。"
    )
    display_name = "搜索景点"
    InputModel = AttractionSearchInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        base = self.display_name
        if tool_args:
            query = tool_args.get("query", "")
            if query:
                return f"{base}「{query}」"
        return base

    def __init__(self):
        self._embedding_client = None
        self._tenant_id = None

    def set_tenant_id(self, tenant_id: str):
        """由 Agent 注入 tenant_id（子智能体线程中 ContextVar 不可用）"""
        self._tenant_id = tenant_id

    def _get_embedding_client(self):
        if self._embedding_client is None:
            from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
            from src.config.settings import get_embedding_api_key

            embedding_api_key = get_embedding_api_key()
            self._embedding_client = TextEmbeddingV3Client(api_key=embedding_api_key)
        return self._embedding_client

    def _embed(self, text: str) -> List[float]:
        import dashscope
        from src.config.settings import get_embedding_api_key

        dashscope.api_key = get_embedding_api_key()

        resp = dashscope.TextEmbedding.call(
            model="text-embedding-v3",
            input=text,
            dimension=1024,
            text_type="document",
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Embedding API 调用失败: {resp.message}")
        return resp.output["embeddings"][0]["embedding"]

    async def execute(self, **kwargs) -> Dict[str, Any]:
        query = kwargs.get("query")
        top_k = kwargs.get("top_k", 20)

        if not query:
            return {"success": False, "error": "查询不能为空", "results": [], "count": 0}

        try:
            # 获取 tenant_id：优先 ContextVar（HTTP请求场景），其次从 agent 注入（子智能体线程场景）
            tenant_id = self._tenant_id
            if not tenant_id:
                try:
                    from src.saas.context import get_current_tenant_id
                    tenant_id = get_current_tenant_id()
                except Exception:
                    pass
            if not tenant_id:
                return {"success": False, "error": "无法确定租户ID", "results": [], "count": 0}

            # 向量化查询
            embedding = self._embed(query)
            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

            # 向量语义搜索，限定景点知识库
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT cv.embedding <=> %s::vector AS distance,
                           c.doc_id, c.text, d.title, d.metadata, d.file_path
                    FROM chunks_vec cv
                    JOIN chunks c ON cv.chunk_id = c.id
                    JOIN documents d ON c.doc_id = d.id
                    WHERE d.source_type = 'attraction_resource'
                      AND d.tenant_id = %s
                      AND c.chunk_index = 0
                    ORDER BY distance
                    LIMIT %s
                """, (embedding_str, tenant_id, top_k))
                rows = cursor.fetchall()

            # 批量查询 chunk_index=2 的项目/服务信息，避免 N+1
            doc_ids = [row["doc_id"] for row in rows]
            project_table_map = {}
            if doc_ids:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    placeholders = ",".join(["%s"] * len(doc_ids))
                    cursor.execute(f"""
                        SELECT doc_id, text FROM chunks
                        WHERE doc_id IN ({placeholders}) AND chunk_index = 2
                    """, doc_ids)
                    for pt_row in cursor.fetchall():
                        project_table_map[pt_row["doc_id"]] = pt_row["text"] or ""

            formatted = []
            for row in rows:
                meta = row["metadata"]
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except (json.JSONDecodeError, TypeError):
                        meta = {}

                info = row["text"] or ""
                formatted.append({
                    "doc_id": row["doc_id"],
                    "title": row["title"],
                    "info": info[:500],
                    "project_table": project_table_map.get(row["doc_id"], ""),
                    "score": round(1.0 - float(row["distance"]), 4),
                    "region": meta.get("region", ""),
                    "category": meta.get("category", ""),
                    "category_cn": meta.get("category_cn", ""),
                })

            logger.info(f"[AttractionSearch] query='{query}' → {len(formatted)} results (tenant={tenant_id})")

            return {
                "success": True,
                "results": formatted,
                "count": len(formatted),
            }

        except Exception as e:
            logger.error(f"景点搜索失败: {e}", exc_info=True)
            return {"success": False, "error": str(e), "results": [], "count": 0}
