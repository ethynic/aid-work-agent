"""
知识库检索工具 - 提供给 Agent 调用
"""

from typing import Dict, Any, List, Optional
import logging

from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.knowledge.vector_db.vector_db import get_vector_db
from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
from src.knowledge.retriever.hybrid_retriever import HybridRetriever
from src.db.database import get_db_connection

logger = logging.getLogger(__name__)


class KnowledgeBaseSearchInput(BaseModel):
    """搜索知识库参数"""
    query: str = Field(..., description="用户问题或查询关键词")
    top_k: Optional[int] = Field(10, description="返回的相关段落数量，默认 10")


class KnowledgeBaseTool(BaseTool):
    """知识库检索工具"""

    name = "knowledge_base_search"
    description = "从企业知识库中检索相关信息，回答用户问题。当用户询问关于公司制度、文档资料、产品信息等问题时使用此工具。"
    display_name = "搜索知识库"
    InputModel = KnowledgeBaseSearchInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """动态显示名，展示查询关键词"""
        base = self.display_name
        if tool_args:
            query = tool_args.get("query", "")
            if query:
                return f"{base}「{query}」"
        return base

    def __init__(self):
        self.retriever = self._init_retriever()

    def _init_retriever(self):
        """初始化检索器（适配 PostgreSQL）"""
        # 从配置获取 Qwen API Key（支持 key 池）
        from src.config.settings import settings
        qwen_keys = settings.llm.qwen.get_effective_keys()
        qwen_api_key = qwen_keys[0] if qwen_keys else ""

        vector_db = get_vector_db(dimension=1024)
        embedding_client = TextEmbeddingV3Client(api_key=qwen_api_key)

        # 不传 conn 参数，让 VectorDB 和 HybridRetriever 使用连接池管理连接
        return HybridRetriever(
            vector_db=vector_db,
            embedding_client=embedding_client,
            conn=None
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行知识库检索

        Args:
            query: 用户查询
            top_k: 返回结果数量

        Returns:
            检索结果
        """
        query = kwargs.get("query")
        top_k = kwargs.get("top_k", 10)

        if not query:
            return {
                "success": False,
                "error": "查询不能为空",
                "results": [],
                "count": 0
            }

        try:
            results = await self.retriever.retrieve(query=query, top_k=top_k)

            # 提取文档标题
            if results:
                doc_ids = {r["doc_id"] for r in results}
                placeholder = "%s"
                placeholders = ','.join([placeholder] * len(doc_ids))

                conn_cm = get_db_connection()
                conn = conn_cm.__enter__()
                try:
                    cursor = conn.cursor()
                    cursor.execute(f"""
                        SELECT id, title FROM documents WHERE id IN ({placeholders})
                    """, list(doc_ids))

                    doc_titles = {row["id"]: row["title"] for row in cursor.fetchall()}
                finally:
                    conn_cm.__exit__(None, None, None)

                # 格式化结果
                formatted_results = [
                    {
                        "text": r["text"],
                        "doc_title": doc_titles.get(r["doc_id"], "未知文档"),
                        "score": round(r["score"], 4),
                        "metadata": r["metadata"]
                    }
                    for r in results
                ]
            else:
                formatted_results = []

            return {
                "success": True,
                "results": formatted_results,
                "count": len(formatted_results)
            }

        except Exception as e:
            logger.error(f"后端日志：知识库检索失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "results": [],
                "count": 0
            }
