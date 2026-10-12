"""
SmartDataAnalysisTool — 对外工具接口

BaseTool 子类，注册到主智能体工具列表。
接收用户分析需求，服务端启动时一次性完成选表和加载（调用方未指定表时：数据表数不超过
FULL_LOAD_THRESHOLD 全量预加载，超过则按需求语义检索 top SEMANTIC_SELECT_TOP_K），
内部 Agent 循环直接在已加载的表上编排分析步骤，返回结果摘要和图表文件。
"""

import asyncio
import uuid
from typing import Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool

# 开局批量选表阈值：本租户（含共享来源）数据表数不超过该值时全量预加载，超过时改为
# 按 requirement 语义检索选中，避免 schema 清单撑爆上下文。可按租户表规模与上下文预算调整
FULL_LOAD_THRESHOLD = 50
# 超过全量阈值时按需求语义检索选中的表数量（可调）
SEMANTIC_SELECT_TOP_K = 30


async def _load_one_table(analyzer, meta: dict) -> str:
    """并发加载单表：数据库源走原生协程；Excel/文件源读取为阻塞 IO，
    用 asyncio.to_thread 放线程池执行（load_table 的文件分支内部无 await，
    可在线程的独立事件循环中同步跑完）。"""
    source = meta.get("source") or {}
    if source.get("type") == "database":
        return await analyzer.load_table(meta)
    return await asyncio.to_thread(lambda: asyncio.run(analyzer.load_table(meta)))


class AnalyzeDataInput(BaseModel):
    requirement: str = Field(
        description="用户的原始分析需求（自然语言）"
    )
    tables_metadata: Optional[List[dict]] = Field(
        None,
        description="[可选] 已匹配到的相关表 Metadata 列表。不传时服务端自动选表并预加载"
        "（表少全量、表多按需求语义检索），分析智能体直接在已加载的表上分析"
    )
    session_id: Optional[str] = Field(
        None,
        description="当前会话 ID，用于 DataFrame 缓存"
    )


class SmartDataAnalysisTool(BaseTool):
    name = "analyze_data"
    description = (
        "智能数据分析工具。接收用户的分析需求，服务端自动选表并一次性预加载相关数据，"
        "编排分析步骤（查询、聚合、关联、对比、趋势、透视、计算、可视化等），"
        "一次性完成分析并返回结果摘要和图表文件。"
        "对同一数据源的多个分析子问题，尽量合并为一次调用完成（不要每个子问题单独调用一次）；"
        "本会话先前调用返回的 reusable_outputs 中的产物（如已合并的宽表）可直接复用，"
        "在 requirement 中注明要复用的 output_var 即可，无需重新分析原始数据。"
    )
    display_name = "智能数据分析"
    category = "data_analysis"
    InputModel = AnalyzeDataInput

    async def execute(self, **kwargs) -> Dict[str, object]:
        requirement = kwargs["requirement"]
        tables_metadata = kwargs.get("tables_metadata")
        session_id = kwargs.get("session_id")

        # 1. 解析 tenant_id / subagent_id（subagent_id 用于共享知识库检索范围）
        tenant_id = None
        subagent_id = None
        context = None
        try:
            from src.saas.context import get_current_tenant_id
            tenant_id = get_current_tenant_id()
        except Exception:
            pass
        try:
            from src.tools.context import current_tool_execution_context
            context = current_tool_execution_context()
            if context:
                subagent_id = context.subagent_id
                if not tenant_id:
                    tenant_id = context.tenant_id
                # session_id 以服务端工具执行上下文为准（LLM 参数可幻觉/被注入，
                # 且是产物注册表路径成分，不可信值会导致复用错位或路径异常）；
                # 无上下文的调用方（后台任务）才用参数值
                if context.session_id:
                    session_id = context.session_id
        except Exception:
            pass

        # 2. 初始化 DataAnalyzer（产物按租户落 storage/tenants/{tid}/report|temp）
        from src.tools.data_analysis.data_analyzer import DataAnalyzer
        analyzer = DataAnalyzer(session_id=session_id, tenant_id=tenant_id or "")

        # 3. 预加载表（仅加载带 table_id/doc_id 的完整 metadata）
        # 主智能体可能传入 search_data_tables 的简化结构（仅 table_id/table_name/description，
        # 无 source），直接加载必失败；先回查 documents 表补全完整 metadata，再加载。
        if tables_metadata:
            from src.tools.data_analysis.analysis_agent import fetch_table_metadata_from_db

            enriched_tables: List[dict] = []
            for meta in tables_metadata:
                table_id = meta.get("table_id") or meta.get("doc_id")
                if not table_id:
                    enriched_tables.append(meta)
                    continue
                if not meta.get("source"):
                    try:
                        full_meta = await asyncio.to_thread(
                            fetch_table_metadata_from_db, str(table_id), tenant_id, subagent_id
                        )
                        if full_meta:
                            meta = full_meta
                    except Exception as e:
                        logger.warning(f"[SmartDataAnalysisTool] 回查表 {table_id} 完整元数据失败: {e}")
                enriched_tables.append(meta)
                try:
                    await analyzer.load_table(meta)
                except Exception as e:
                    logger.opt(exception=True).error(f"[SmartDataAnalysisTool] 加载表失败: {e}")
                    return {"success": False, "error": f"加载数据表失败: {e}"}
            tables_metadata = enriched_tables
        else:
            # 改进1（2026-10）：调用方未指定表时，服务端一次性完成选表和批量加载，
            # 模型开局即面对「已加载的表 + 完整 schema」，禁止逐个 search/load_table
            #（此前 36 张同构表被内层模型逐个 load_table 共 82 次，列定义永久滞留上下文）
            tables_metadata = await self._batch_select_and_load(
                analyzer, requirement, tenant_id, subagent_id
            )

        # 4. 运行 AnalysisAgent
        try:
            from src.core import master_agent
            from src.tools.data_analysis.analysis_agent import AnalysisAgent

            # 优先用调用方智能体的网关（跟随数字员工模型配置），无上下文时兜底 master 网关
            llm = context.llm_gateway if context and context.llm_gateway else master_agent.llm

            analysis_id = f"analysis_{uuid.uuid4().hex[:8]}"
            agent = AnalysisAgent(
                llm_gateway=llm,
                analyzer=analyzer,
                analysis_id=analysis_id,
                tables_metadata=tables_metadata,
                tenant_id=tenant_id,
                subagent_id=subagent_id,
                session_id=session_id,
            )
            result = await agent.run(requirement)
        except Exception as e:
            logger.opt(exception=True).error(f"[SmartDataAnalysisTool] AnalysisAgent 运行失败: {e}")
            return {"success": False, "error": f"分析执行失败: {e}"}

        # 主智能体防线：success=True 但结论为空时降级为失败。
        # 防止空结论被主智能体误判为"知识库中无相关数据"（2026-08 生产事故归因）。
        if result.get("success") and not str(result.get("conclusion", "")).strip():
            logger.error(f"[SmartDataAnalysisTool] 分析返回空结论（trace={result.get('analysis_meta', {}).get('trace_id')}），降级为失败")
            result["success"] = False
            result["error"] = "分析未产出有效结论（输出可能被截断），请重试或简化分析需求"
            result["conclusion"] = result["error"]

        return result

    @staticmethod
    async def _batch_select_and_load(
        analyzer,
        requirement: str,
        tenant_id: Optional[str],
        subagent_id: Optional[str],
    ) -> List[dict]:
        """服务端一次性选表并批量加载，返回成功加载的完整 metadata 列表。

        选表策略：数据表数 <= FULL_LOAD_THRESHOLD 全量选中；超过时按 requirement
        语义检索 top SEMANTIC_SELECT_TOP_K（检索失败退回不预加载，由内层模型自行
        search_data_tables，即改动前的行为）。
        单表回查/加载失败仅跳过并告警，不阻断整体分析（个别孤儿表不应拖垮全部分析）。
        """
        from src.tools.data_analysis.analysis_agent import (
            fetch_table_metadata_from_db,
            query_data_tables_list,
        )

        if not tenant_id:
            # 无租户上下文不批量加载：列表查询会放大为跨租户全局视图
            return []

        # 选表：与内层 list_data_tables 同口径（含共享来源租户与移动兜底）
        try:
            table_summaries = await asyncio.to_thread(
                query_data_tables_list, "", tenant_id, subagent_id
            )
        except Exception as e:
            logger.warning(f"[SmartDataAnalysisTool] 查询数据表列表失败，跳过开局批量加载: {e}")
            return []

        if not table_summaries:
            return []

        if len(table_summaries) <= FULL_LOAD_THRESHOLD:
            selected_ids = [t["table_id"] for t in table_summaries]
            select_desc = "全量"
        else:
            selected_ids = await SmartDataAnalysisTool._semantic_select_ids(
                requirement, tenant_id, subagent_id
            )
            select_desc = f"语义检索 top{SEMANTIC_SELECT_TOP_K}"
            if not selected_ids:
                return []

        logger.info(
            f"[SmartDataAnalysisTool] 开局选表（{select_desc}）："
            f"{len(selected_ids)}/{len(table_summaries)} 张"
        )

        # 列表查询不含 columns/source，无法直接加载与渲染 schema；
        # 按主键复用 fetch_table_metadata_from_db 回查完整 metadata（顺带排除
        # 被移动且缺 table_name/columns 的普通同名文档），整批放一个线程执行
        def _fetch_all() -> List[dict]:
            metas = []
            for tid in selected_ids:
                try:
                    meta = fetch_table_metadata_from_db(str(tid), tenant_id, subagent_id)
                except Exception as e:
                    logger.warning(f"[SmartDataAnalysisTool] 回查表 {tid} 完整元数据失败，跳过: {e}")
                    continue
                if meta:
                    metas.append(meta)
            return metas

        selected_metas = await asyncio.to_thread(_fetch_all)
        if not selected_metas:
            return []

        # 批量并发加载：Excel/文件源在线程池读取，不阻塞事件循环
        load_results = await asyncio.gather(
            *(_load_one_table(analyzer, meta) for meta in selected_metas),
            return_exceptions=True,
        )
        loaded: List[dict] = []
        for meta, res in zip(selected_metas, load_results):
            if isinstance(res, BaseException):
                logger.warning(
                    f"[SmartDataAnalysisTool] 预加载表 {meta.get('table_name', meta.get('table_id'))} "
                    f"失败，已跳过: {res}"
                )
            else:
                loaded.append(meta)
        logger.info(
            f"[SmartDataAnalysisTool] 开局预加载完成：{len(loaded)}/{len(selected_metas)} 张成功"
        )
        return loaded

    @staticmethod
    async def _semantic_select_ids(
        requirement: str, tenant_id: Optional[str], subagent_id: Optional[str]
    ) -> List[str]:
        """超过全量阈值时按需求语义检索选中表 doc_id 列表（复用 HybridRetriever）。"""
        try:
            from src.tools.data_analysis.analysis_agent import create_data_tables_retriever

            retriever = create_data_tables_retriever()
            shared_ranges = []
            if tenant_id and subagent_id:
                from src.knowledge.retriever.tenant_range import load_shared_ranges
                shared_ranges = await asyncio.to_thread(
                    load_shared_ranges, tenant_id, subagent_id, "data-analysis-metadata"
                )
            results = await retriever.retrieve(
                query=requirement,
                top_k=SEMANTIC_SELECT_TOP_K,
                tenant_id=tenant_id,
                source_type="data-analysis-metadata",
                shared_ranges=shared_ranges,
            )
            return [str(r["doc_id"]) for r in results]
        except Exception as e:
            logger.warning(f"[SmartDataAnalysisTool] 语义选表失败，回退为不预加载: {e}")
            return []

    def get_display_name(self, tool_args=None) -> str:
        base = self.display_name
        if tool_args:
            requirement = tool_args.get("requirement", "")
            if requirement:
                preview = requirement[:20] + "..." if len(requirement) > 20 else requirement
                return f"{base}「{preview}」"
        return base
