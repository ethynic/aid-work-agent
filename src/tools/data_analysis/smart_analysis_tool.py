"""
SmartDataAnalysisTool — 对外工具接口

BaseTool 子类，注册到主智能体工具列表。
接收用户分析需求，服务端启动时一次性完成选表和加载（调用方未指定表时：数据表数不超过
FULL_LOAD_THRESHOLD 全量预加载，超过则按需求语义检索 top SEMANTIC_SELECT_TOP_K），
内部 Agent 循环直接在已加载的表上编排分析步骤，返回结果摘要和图表文件。
"""

import asyncio
import re
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

# 改进3（2026-10）导出/整理类需求识别：命中导出标记且不含分析意图词时，视为导出类任务。
# 导出应基于本会话已有产物（load_output）完成，跳过 49 表批量预加载与 schema 目录注入。
# 两类词表都是保守匹配：漏判 -> 走正常预加载路径（行为不变），误判 -> 内层仍可
# search/load_table 兜底，两侧均 fail-soft。
_EXPORT_MARKER_RE = re.compile(
    r"导出|另存|整理|保存为|汇总成|生成.{0,8}(?:文件|表格|excel|xlsx|ppt)|输出.{0,8}(?:excel|表格|文件)"
)
_ANALYSIS_WORD_RE = re.compile(
    r"分析|对比|排名|趋势|原因|预测|建议|透视|占比|环比|同比|异常|根因|归因|统计"
)
_NEGATED_RE = re.compile(r"(?:不要|无需|不必|禁止|不用|不做|不再|别)[^，。；！？]{0,12}")


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

        # 1. 解析身份与租户（设计 §18.4 身份语义）：
        #    - ToolExecutionContext 存在 → 可信上下文；tenant_id 冲突以上下文为准，
        #      subagent_id 为空映射 main（可信上下文内的转换）
        #    - 无上下文 ≠ main：有租户无身份时仅共享表 + 诊断提示，不冒充主智能体；
        #      无租户不查询全表（与现状一致）
        tenant_id = None
        subagent_id = None
        agent_id = None  # 数据表名单执行身份：None=身份未知（仅共享）
        context = None
        try:
            from src.saas.context import get_current_tenant_id
            tenant_id = get_current_tenant_id()
        except Exception:
            pass
        try:
            from src.tools.context import current_tool_execution_context
            from src.services.data_analysis.access_scope import MAIN_AGENT_ID
            context = current_tool_execution_context()
            if context:
                subagent_id = context.subagent_id
                if context.tenant_id:
                    # 请求级可信执行上下文优先于进程级租户变量（冲突场景以可信为准）
                    tenant_id = context.tenant_id
                agent_id = context.subagent_id or MAIN_AGENT_ID
                # session_id 以服务端工具执行上下文为准（LLM 参数可幻觉/被注入，
                # 且是产物注册表路径成分，不可信值会导致复用错位或路径异常）；
                # 无上下文的调用方（后台任务）才用参数值
                if context.session_id:
                    session_id = context.session_id
        except Exception:
            pass
        # 诊断提示（不含数据内容）：仅「有租户但身份未确定」场景出现
        identity_notice = (
            "调用身份未确定，本次仅使用共享数据表"
            if (not context and tenant_id)
            else None
        )

        # 2. 初始化 DataAnalyzer（产物按租户落 storage/tenants/{tid}/report|temp）；
        #    生产路径显式关闭任意文件路径 source 逃生（allow_arbitrary_path_source=False）
        from src.tools.data_analysis.data_analyzer import DataAnalyzer
        analyzer = DataAnalyzer(
            session_id=session_id, tenant_id=tenant_id or "",
            allow_arbitrary_path_source=False,
        )

        # 3. 预加载表：显式入参只把 table_id/doc_id 当选择依据，无论是否带 source
        # 一律回查服务端记录（防 LLM 伪造 source 绕过名单）；任一指定表不可用
        # 整体显式失败（不做部分加载、不回退传入 source、不用原始请求 metadata）
        if tables_metadata:
            from src.tools.data_analysis.analysis_agent import fetch_table_metadata_from_db

            enriched_tables: List[dict] = []
            for meta in tables_metadata:
                table_id = meta.get("table_id") or meta.get("doc_id")
                if not table_id:
                    return self._with_identity_notice(
                        {
                            "success": False,
                            "error": "指定的数据表缺少 table_id，无法加载；请通过 "
                                     "search_data_tables 获取 table_id 后重试",
                        },
                        identity_notice,
                    )
                try:
                    full_meta = await asyncio.to_thread(
                        fetch_table_metadata_from_db,
                        str(table_id), tenant_id, subagent_id, agent_id,
                    )
                except Exception as e:
                    logger.warning(f"[SmartDataAnalysisTool] 回查表 {table_id} 完整元数据失败: {e}")
                    return self._with_identity_notice(
                        {"success": False, "error": f"数据表 {table_id} 当前不可用，无法加载"},
                        identity_notice,
                    )
                if not full_meta:
                    # 不区分不存在/无权；传入的 source/原始 metadata 一律弃用
                    return self._with_identity_notice(
                        {
                            "success": False,
                            "error": f"指定的数据表 {table_id} 当前不可用"
                                     "（不存在或未开放给当前调用身份），已跳过本次分析",
                        },
                        identity_notice,
                    )
                enriched_tables.append(full_meta)
            # 全部回查通过后才开始物理加载（不做部分加载）
            for meta in enriched_tables:
                try:
                    await analyzer.load_table(meta)
                except Exception as e:
                    logger.opt(exception=True).error(f"[SmartDataAnalysisTool] 加载表失败: {e}")
                    return self._with_identity_notice(
                        {"success": False, "error": f"加载数据表失败: {e}"},
                        identity_notice,
                    )
            tables_metadata = enriched_tables
            export_only_mode = False
        else:
            # 改进3（2026-10）：导出/整理类需求且会话已有产物时，跳过开局批量预加载
            #（导出应基于已有产物而非重新加载原始数据；此前同会话连续 4 次导出类调用
            # 每次都重付 49 表预加载 + schema 目录注入，实测单次多耗 20-40 积分）
            # 可复用判定含来源表可用性 DB 回查：async 路径沿用 to_thread 模式
            export_only_mode = (
                SmartDataAnalysisTool._is_export_only_requirement(requirement)
                and bool(session_id)
                and await asyncio.to_thread(
                    self._session_has_reusable_artifacts,
                    tenant_id, session_id, subagent_id, agent_id,
                )
            )
            if export_only_mode:
                tables_metadata = []
                logger.info(
                    f"[SmartDataAnalysisTool] 导出/整理类需求，跳过开局批量预加载，"
                    f"引导复用会话产物 (session={session_id})"
                )
            else:
                # 改进1（2026-10）：调用方未指定表时，服务端一次性完成选表和批量加载，
                # 模型开局即面对「已加载的表 + 完整 schema」，禁止逐个 search/load_table
                #（此前 36 张同构表被内层模型逐个 load_table 共 82 次，列定义永久滞留上下文）
                tables_metadata = await self._batch_select_and_load(
                    analyzer, requirement, tenant_id, subagent_id, agent_id
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
                export_only_mode=export_only_mode,
                agent_id=agent_id,
            )
            result = await agent.run(requirement)
        except Exception as e:
            logger.opt(exception=True).error(f"[SmartDataAnalysisTool] AnalysisAgent 运行失败: {e}")
            return self._with_identity_notice(
                {"success": False, "error": f"分析执行失败: {e}"}, identity_notice
            )

        # 主智能体防线：success=True 但结论为空时降级为失败。
        # 防止空结论被主智能体误判为"知识库中无相关数据"（2026-08 生产事故归因）。
        if result.get("success") and not str(result.get("conclusion", "")).strip():
            logger.error(f"[SmartDataAnalysisTool] 分析返回空结论（trace={result.get('analysis_meta', {}).get('trace_id')}），降级为失败")
            result["success"] = False
            result["error"] = "分析未产出有效结论（输出可能被截断），请重试或简化分析需求"
            result["conclusion"] = result["error"]

        return self._with_identity_notice(result, identity_notice)

    @staticmethod
    def _with_identity_notice(result: Dict[str, object], notice: Optional[str]) -> Dict[str, object]:
        """附加身份诊断提示（不含数据内容）：仅「有租户但无可信身份」场景。"""
        if notice and isinstance(result, dict):
            result["identity_notice"] = notice
        return result

    @staticmethod
    def _is_export_only_requirement(requirement: str) -> bool:
        """判定需求是否为导出/整理类（命中导出标记且无分析意图词）。

        先剔除「不要/无需...」否定短语再判分析词：同会话实测导出任务常见
        「不要重新分析原始数据」表述，不剔除会误判回正常路径（fail-soft 但丢收益）。
        """
        if not requirement:
            return False
        text = _NEGATED_RE.sub("", requirement)
        return bool(_EXPORT_MARKER_RE.search(text)) and not _ANALYSIS_WORD_RE.search(text)

    @staticmethod
    def _session_has_reusable_artifacts(
        tenant_id: Optional[str],
        session_id: Optional[str],
        subagent_id: Optional[str],
        agent_id: Optional[str],
    ) -> bool:
        """会话是否存在当前可复用的产物（导出短路前提）。

        复用资格与清单/load_output 同口径：已登记 + source_table_ids 完整 +
        每张来源表当前均可用（批量去重一次查询）。无可复用产物则不短路导出。
        读取失败视为无可复用（走正常路径，fail-soft 与原行为一致）。
        """
        try:
            from src.tools.data_analysis.analysis_agent import filter_reusable_artifact_entries
            from src.tools.data_analysis.analysis_artifacts import load_artifacts

            entries = load_artifacts(tenant_id or "", session_id or "")
            if not entries:
                return False
            return bool(
                filter_reusable_artifact_entries(entries, tenant_id, subagent_id, agent_id)
            )
        except Exception as e:
            logger.warning(f"[SmartDataAnalysisTool] 会话产物查询失败（走正常路径）: {e}")
            return False

    @staticmethod
    async def _batch_select_and_load(
        analyzer,
        requirement: str,
        tenant_id: Optional[str],
        subagent_id: Optional[str],
        agent_id: Optional[str] = None,
    ) -> List[dict]:
        """服务端一次性选表并批量加载，返回成功加载的完整 metadata 列表。

        选表策略：数据表数（已按名单过滤）<= FULL_LOAD_THRESHOLD 全量选中；超过时
        按 requirement 语义检索 top SEMANTIC_SELECT_TOP_K（检索失败退回不预加载，
        由内层模型自行 search_data_tables，即改动前的行为）。过滤发生在计数、
        选表与加载之前，受限表不影响阈值判定。
        单表回查/加载失败仅跳过并告警，不阻断整体分析（个别孤儿表不应拖垮全部分析）。
        """
        from src.tools.data_analysis.analysis_agent import (
            fetch_table_metadata_from_db,
            query_data_tables_list,
        )

        if not tenant_id:
            # 无租户上下文不批量加载：列表查询会放大为跨租户全局视图
            return []

        # 选表：与内层 list_data_tables 同口径（含共享来源租户与移动兜底 + 名单）
        try:
            table_summaries = await asyncio.to_thread(
                query_data_tables_list, "", tenant_id, subagent_id, None, agent_id
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
                requirement, tenant_id, subagent_id, agent_id
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
                    meta = fetch_table_metadata_from_db(str(tid), tenant_id, subagent_id, agent_id)
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
        requirement: str,
        tenant_id: Optional[str],
        subagent_id: Optional[str],
        agent_id: Optional[str] = None,
    ) -> List[str]:
        """超过全量阈值时按需求语义检索选中表 doc_id 列表（复用 HybridRetriever）。

        agent_id 透传检索层：名单条件在排名/LIMIT 前过滤，受限表占前排不会
        挤掉可用表候选。
        """
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
                agent_id=agent_id,
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
