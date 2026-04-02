"""
知识库检索工具 - 提供给 Agent 调用
"""

import os
import sqlite3
from typing import Dict, Any, List
import logging

from src.tools.base import BaseTool
from src.knowledge.vector_db.vector_db import VectorDBSQLite
from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
from src.knowledge.retriever.hybrid_retriever import HybridRetriever

logger = logging.getLogger(__name__)


class KnowledgeBaseTool(BaseTool):
    """知识库检索工具"""

    name = "knowledge_base_search"
    description = "从企业知识库中检索相关信息，回答用户问题。当用户询问关于公司制度、文档资料、产品信息等问题时使用此工具。"

    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "用户问题或查询关键词"
            },
            "top_k": {
                "type": "integer",
                "description": "返回的相关段落数量，默认 10",
                "default": 10
            }
        },
        "required": ["query"]
    }

    def __init__(self):
        self.retriever = self._init_retriever()

    def _init_retriever(self):
        """初始化检索器"""
        # 从环境变量获取数据库路径，与 database.py 保持一致
        database_url = os.getenv("DATABASE_URL", "sqlite:///./aid_work_agent.db")
        db_path = database_url.replace("sqlite:///", "")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # 从配置获取 Qwen API Key（支持 key 池）
        from src.config.settings import settings
        qwen_keys = settings.llm.qwen.get_effective_keys()
        qwen_api_key = qwen_keys[0] if qwen_keys else ""
        vector_db = VectorDBSQLite(db_path=db_path, dimension=1024, conn=conn)
        embedding_client = TextEmbeddingV3Client(api_key=qwen_api_key)

        return HybridRetriever(
            vector_db=vector_db,
            embedding_client=embedding_client,
            conn=conn
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
                placeholders = ','.join(['?'] * len(doc_ids))

                cursor = self.retriever.conn.cursor()
                cursor.execute(f"""
                    SELECT id, title FROM documents WHERE id IN ({placeholders})
                """, list(doc_ids))

                doc_titles = {row[0]: row[1] for row in cursor.fetchall()}

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
