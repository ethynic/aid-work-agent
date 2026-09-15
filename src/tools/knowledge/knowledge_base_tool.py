"""
知识库检索工具 - 提供给 Agent 调用
"""

from typing import Dict, Any, List, Optional

from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.knowledge.vector_db.vector_db import get_vector_db
from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
from src.knowledge.retriever.hybrid_retriever import HybridRetriever
from src.knowledge.retriever.tenant_range import build_tenant_range_conditions
from src.db.database import get_db_connection
from loguru import logger


class KnowledgeBaseSearchInput(BaseModel):
    """搜索知识库参数"""
    query: str = Field(..., description="用户问题或查询关键词")
    top_k: Optional[int] = Field(10, description="返回的相关段落数量，默认 10")
    source_type: Optional[str] = Field(None, description="文档来源类型过滤，不传则搜索全部知识库，传值则只搜索指定类型的文档")


class KnowledgeBaseTool(BaseTool):
    """知识库检索工具"""

    name = "knowledge_base_search"
    description = "从知识中心检索相关信息，回答用户问题。当用户询问关于公司制度、文档资料、产品信息等问题时使用此工具。"
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
        self._retriever = None  # 惰性初始化，首次使用时创建

    @property
    def retriever(self):
        """惰性获取检索器实例"""
        if self._retriever is None:
            self._retriever = self._init_retriever()
        return self._retriever

    def _init_retriever(self):
        """初始化检索器（适配 PostgreSQL）"""
        # 从配置获取 Embedding API Key（支持独立配置，适配不同 LLM 提供者）
        from src.config.settings import get_embedding_api_key
        embedding_api_key = get_embedding_api_key()

        vector_db = get_vector_db(dimension=1024)
        embedding_client = TextEmbeddingV3Client(api_key=embedding_api_key)

        # 不传 conn 参数，让 VectorDB 和 HybridRetriever 使用连接池管理连接
        return HybridRetriever(
            vector_db=vector_db,
            embedding_client=embedding_client,
            conn=None
        )

    def _load_shared_ranges(self, tenant_id: str, subagent_id: str, source_type: Optional[str]) -> List[tuple]:
        """单条 SQL 取启用清单与有效授权交集，生成共享检索范围（精确 (from_tenant_id, source_type) 对）。

        不信任 LLM/前端传入的租户，由本方法从权威表读取并校验。授权撤销后交集
        为空，共享项自动失效（无快照，撤销立即生效）。LLM 传了 source_type 时
        只取该分类的共享项；未传时取全部已启用共享项。
        """
        try:
            conn_cm = get_db_connection()
            conn = conn_cm.__enter__()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                      (SELECT sources FROM subagent_knowledge_sources
                       WHERE tenant_id = %s AND subagent_name = %s) AS sources,
                      COALESCE((SELECT json_agg(from_tenant_id) FROM tenant_knowledge_shares
                       WHERE to_tenant_id = %s), '[]'::json) AS share_owners
                """, (tenant_id, subagent_id, tenant_id))
                row = cursor.fetchone()
            finally:
                conn_cm.__exit__(None, None, None)
        except Exception as e:
            logger.warning(f"后端日志：加载共享检索范围失败: {e}")
            return []

        if not row:
            return []
        sources = row["sources"] or []
        share_owners = set(row["share_owners"] or [])
        ranges = []
        for s in sources:
            if not isinstance(s, dict):
                continue
            owner = s.get("owner_tenant_id")
            st = s.get("source_type") or ""
            if owner and owner in share_owners:
                ranges.append((owner, st))
        if source_type:
            ranges = [r for r in ranges if r[1] == source_type]
        return ranges

    @staticmethod
    def _attach_owner_metadata(metadata: Dict[str, Any], doc_tenant_id: Optional[str], current_tenant_id: Optional[str]) -> Dict[str, Any]:
        """共享来源标注：文档属于其他租户（共享库）时，给 metadata 附加 owner_tenant_id，
        供 LLM 感知内容来源；本租户结果不加标注，行为与现状一致。"""
        md = dict(metadata or {})
        if doc_tenant_id and doc_tenant_id != current_tenant_id:
            md["owner_tenant_id"] = doc_tenant_id
        return md

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
        source_type = kwargs.get("source_type")

        # LLM 可能把 top_k 以字符串形式传入（如 "10"），强转为 int；
        # 否则 top_k*3 变成字符串拼接、filtered[:top_k] 切片抛 slice indices 错误
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = 10

        if not query:
            return {
                "success": False,
                "error": "查询不能为空",
                "results": [],
                "count": 0
            }

        from src.tools.context import current_tool_execution_context
        context = current_tool_execution_context()
        tenant_id = context.tenant_id if context else None
        subagent_id = context.subagent_id if context else None

        # 构造共享检索范围：仅子智能体 + 租户模式生效（主智能体 subagent_id 为空，恒为空）
        shared_ranges = []
        if tenant_id and subagent_id:
            shared_ranges = self._load_shared_ranges(tenant_id, subagent_id, source_type)

        logger.info(f"后端日志：知识库检索 tenant_id={tenant_id}, subagent_id={subagent_id}, query={query}, source_type={source_type}, shared_ranges={shared_ranges}")

        try:
            results = await self.retriever.retrieve(
                query=query, top_k=top_k, tenant_id=tenant_id,
                source_type=source_type, shared_ranges=shared_ranges,
            )

            # 提取文档标题
            if results:
                doc_ids = {r["doc_id"] for r in results}
                placeholder = "%s"
                placeholders = ','.join([placeholder] * len(doc_ids))

                conn_cm = get_db_connection()
                conn = conn_cm.__enter__()
                try:
                    cursor = conn.cursor()
                    if tenant_id:
                        range_sql, range_params = build_tenant_range_conditions(
                            tenant_id, source_type, shared_ranges, alias="documents",
                        )
                        params = list(doc_ids) + range_params
                        cursor.execute(f"""
                            SELECT id, title, file_path, tenant_id FROM documents
                            WHERE id IN ({placeholders}) AND ({range_sql})
                        """, params)
                    else:
                        source_type_condition = " AND source_type = %s" if source_type else ""
                        params = list(doc_ids)
                        if source_type:
                            params.append(source_type)
                        cursor.execute(f"""
                            SELECT id, title, file_path, tenant_id FROM documents
                            WHERE id IN ({placeholders})
                              AND tenant_id IS NULL{source_type_condition}
                        """, params)

                    rows = cursor.fetchall()
                    doc_titles = {row["id"]: row["title"] for row in rows}
                    doc_file_paths = {row["id"]: row.get("file_path") or "" for row in rows}
                    doc_tenant_ids = {row["id"]: row.get("tenant_id") for row in rows}
                finally:
                    conn_cm.__exit__(None, None, None)

                # 格式化结果
                formatted_results = [
                    {
                        "text": r["text"],
                        "doc_id": r["doc_id"],
                        "doc_title": doc_titles.get(r["doc_id"], "未知文档"),
                        "file_path": doc_file_paths.get(r["doc_id"], ""),
                        "score": round(r["score"], 4),
                        "metadata": self._attach_owner_metadata(
                            r["metadata"], doc_tenant_ids.get(r["doc_id"]), tenant_id,
                        )
                    }
                    for r in results
                ]
            else:
                formatted_results = []

            # _no_truncate: 检索结果每条含完整文本，保头保尾截断会丢失排名靠后的
            # 中间结果（LLM 无法看到完整候选集）。声明不截断，让 LLM 看到全部结果。
            return {
                "success": True,
                "results": formatted_results,
                "count": len(formatted_results),
                "_no_truncate": True
            }

        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：知识库检索失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "results": [],
                "count": 0
            }
