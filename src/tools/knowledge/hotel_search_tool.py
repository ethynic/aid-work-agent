"""
酒店知识库搜索工具 - 提供给 Agent 调用

搜索酒店知识库（source_type='hotel_resource'），返回酒店信息摘要 + 价格明细表。
价格明细表存于 chunk_index=1（未向量化），通用 knowledge_base_search 拿不到，本工具专门返回。

检索策略（与报价 skill 的 HotelRetriever.search 一致）：
1. 名称匹配优先（chunks.text ILIKE），精确命中具体酒店
2. 名称无结果再走向量语义搜索（pgvector cosine distance），兜底城市/区域/特色类查询
两者都限定 chunk_index=0（酒店信息摘要，已向量化）。
"""

import asyncio
import json
from typing import Dict, Any, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.db.database import get_db_connection
from src.knowledge.retriever.tenant_range import load_shared_ranges


class HotelSearchInput(BaseModel):
    """搜索酒店知识库参数"""
    query: str = Field(..., description="搜索关键词（酒店名、城市名或区域名）")
    top_k: Optional[int] = Field(8, description="返回结果数量，默认8")


class HotelSearchTool(BaseTool):
    """酒店知识库搜索工具"""

    SOURCE_TYPE = "hotel_resource"

    name = "hotel_search"
    description = (
        "从酒店知识库中搜索酒店信息及价格。输入酒店名、城市名或区域名，"
        "返回匹配的酒店列表（含名称、区域、星级、信息摘要、价格明细表）。"
        "客户询问酒店或酒店价格（如“XX酒店多少钱”“贵阳有哪些酒店”）时使用此工具。"
    )
    display_name = "搜索酒店"
    InputModel = HotelSearchInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        base = self.display_name
        if tool_args:
            query = tool_args.get("query", "")
            if query:
                return f"{base}「{query}」"
        return base

    def __init__(self):
        self._embedding_client = None

    def _get_embedding_client(self):
        if self._embedding_client is None:
            from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
            from src.config.settings import get_embedding_api_key

            embedding_api_key = get_embedding_api_key()
            self._embedding_client = TextEmbeddingV3Client(api_key=embedding_api_key)
        return self._embedding_client

    def _embed(self, text: str) -> List[float]:
        """使用 TextEmbeddingV3Client 同步向量化（累加 usage 到 client）"""
        client = self._get_embedding_client()
        return client.embed_sync(text)

    def _resolve_tenant_id(self) -> Optional[str]:
        """从当前请求的不可变执行上下文获取租户。"""
        from src.tools.context import current_tool_execution_context
        context = current_tool_execution_context()
        return context.tenant_id if context else None

    def _resolve_subagent_id(self) -> Optional[str]:
        """从当前请求的不可变执行上下文获取子智能体 ID（共享检索前提）。"""
        from src.tools.context import current_tool_execution_context
        context = current_tool_execution_context()
        return context.subagent_id if context else None

    def _build_tenant_scope(self, tenant_id: Optional[str], subagent_id: Optional[str]) -> tuple:
        """构建租户范围 SQL 与参数：本租户 + 已启用共享来源租户（与通用知识库检索的
        shared_ranges 语义一致，仅子智能体 + 租户模式生效）。subagent_id 为空时退化为本租户。"""
        if not tenant_id or not subagent_id:
            return "d.tenant_id = %s", [tenant_id]
        tenant_ids = [tenant_id]
        for from_tenant_id, _st in load_shared_ranges(tenant_id, subagent_id, self.SOURCE_TYPE):
            if from_tenant_id not in tenant_ids:
                tenant_ids.append(from_tenant_id)
        return "d.tenant_id = ANY(%s)", [tenant_ids]

    def _format_row(self, row, score: Optional[float]) -> Dict[str, Any]:
        """将数据库行格式化为统一的输出项"""
        meta = row["metadata"]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}

        info = row["text"] or ""
        return {
            "doc_id": row["doc_id"],
            "title": row["title"],
            "info": info[:500],
            "score": score,
            "region": meta.get("region") or meta.get("sub_region") or "",
            "diamond_level": meta.get("diamond_level") or meta.get("star_level") or "",
        }

    def _fetch_price_tables(self, doc_ids: List[int]) -> Dict[int, str]:
        """批量查询 chunk_index=1 的价格明细表，避免 N+1"""
        if not doc_ids:
            return {}
        price_map: Dict[int, str] = {}
        with get_db_connection() as conn:
            cursor = conn.cursor()
            placeholders = ",".join(["%s"] * len(doc_ids))
            cursor.execute(f"""
                SELECT doc_id, text FROM chunks
                WHERE doc_id IN ({placeholders}) AND chunk_index = 1
            """, doc_ids)
            for pt_row in cursor.fetchall():
                price_map[pt_row["doc_id"]] = pt_row["text"] or ""
        return price_map

    async def execute(self, **kwargs) -> Dict[str, Any]:
        query = kwargs.get("query")
        top_k = kwargs.get("top_k", 8)

        if not query:
            return {"success": False, "error": "查询不能为空", "results": [], "count": 0}

        try:
            tenant_id = self._resolve_tenant_id()
            if not tenant_id:
                return {"success": False, "error": "无法确定租户ID", "results": [], "count": 0}
            subagent_id = self._resolve_subagent_id()
            tenant_sql, tenant_params = self._build_tenant_scope(tenant_id, subagent_id)

            # 第一步：名称匹配（ILIKE），精确命中具体酒店
            rows: List[Any] = []
            used_vector = False
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT c.doc_id, c.text, d.title, d.metadata, d.file_path
                    FROM chunks c
                    JOIN documents d ON c.doc_id = d.id
                    WHERE d.source_type = %s
                      AND {tenant_sql}
                      AND c.chunk_index = 0
                      AND c.text ILIKE %s
                    ORDER BY d.id
                    LIMIT %s
                """, (self.SOURCE_TYPE, *tenant_params, f'%{query}%', top_k))
                rows = cursor.fetchall()

            # 第二步：名称无命中 → 向量语义搜索兜底
            if not rows:
                used_vector = True
                client = self._get_embedding_client()
                client.reset_usage()
                # _embed 内部含 TextEmbedding.call 同步阻塞 + 重试 sleep，必须 to_thread 化避免卡事件循环
                embedding = await asyncio.to_thread(self._embed, query)
                # 累加 embedding usage 到当前 SessionRecordService（对话内检索计费）
                if client.last_usage_tokens > 0:
                    from src.services.session_record import SessionRecordManager
                    record = SessionRecordManager.get_current_record()
                    if record:
                        record.add_embedding_usage(client.last_usage_tokens, model=client.model)
                embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(f"""
                        SELECT cv.embedding <=> %s::vector AS distance,
                               c.doc_id, c.text, d.title, d.metadata, d.file_path
                        FROM chunks_vec cv
                        JOIN chunks c ON cv.chunk_id = c.id
                        JOIN documents d ON c.doc_id = d.id
                        WHERE d.source_type = %s
                          AND {tenant_sql}
                          AND c.chunk_index = 0
                        ORDER BY distance
                        LIMIT %s
                    """, (embedding_str, self.SOURCE_TYPE, *tenant_params, top_k))
                    rows = cursor.fetchall()

            # 批量取价格明细表（chunk_index=1）
            doc_ids = [row["doc_id"] for row in rows]
            price_table_map = self._fetch_price_tables(doc_ids)

            formatted = []
            for row in rows:
                score: Optional[float] = None
                if used_vector:
                    score = round(1.0 - float(row["distance"]), 4)
                item = self._format_row(row, score)
                item["price_table"] = price_table_map.get(row["doc_id"], "")
                formatted.append(item)

            mode = "vector" if used_vector else "name"
            logger.info(f"[HotelSearch] query='{query}' → {len(formatted)} results via {mode} (tenant={tenant_id})")

            return {
                "success": True,
                "results": formatted,
                "count": len(formatted),
            }

        except Exception as e:
            logger.opt(exception=True).error(f"酒店搜索失败: {e}")
            return {"success": False, "error": str(e), "results": [], "count": 0}
