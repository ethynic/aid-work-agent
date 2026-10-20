"""
AnalysisAgent — 迷你 Agent 循环

LLM 通过 function calling 完成三阶段工作：检索数据表 → 加载表 → 分析数据。
"""

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.core.trace_collector import SpanRecord, TraceRecord
from src.core.trace_persist import schedule_persist
from src.tools.data_analysis.analysis_tools_schema import (
    ALLOWED_METHODS,
    ANALYSIS_SYSTEM_PROMPT,
    ANALYSIS_TOOLS,
)

MAX_ITERATIONS = 100
DATA_PROCESSING_METHODS = {"query", "aggregate", "merge", "append", "pivot", "calculate", "compare", "trend", "extract_hierarchy"}
SEARCH_METHODS = {"search_data_tables", "list_data_tables"}
# describe 结果瘦身上限（2026-10 追加改进 A）：每列 unique_values/top_values 最多返回条数。
# 实测主分析 27 次 describe ≈150k 新发 prompt token（单次 30 列 4-6k），为新发 prompt
# 最大单一来源；8 条足以感知取值分布，完整清单/精确计数属于取数需求（应改用 query/aggregate）
DESCRIBE_VALUE_CAP = 8
# 上下文压缩阈值：当 messages 的 JSON 超过此字节数时，压缩早期的工具结果
CONTEXT_COMPRESS_THRESHOLD = 50000


def _build_tenant_scope_params(tenant_id: Optional[str], subagent_id: Optional[str]) -> tuple:
    """构建租户范围 SQL 与参数：本租户 + 已启用共享来源租户。subagent_id 为空时退化为本租户。"""
    if not tenant_id:
        return "AND FALSE", []
    if not subagent_id:
        return "AND tenant_id = %s", [tenant_id]
    from src.knowledge.retriever.tenant_range import load_shared_ranges

    tenant_ids = [tenant_id]
    for from_tenant_id, _st in load_shared_ranges(tenant_id, subagent_id, "data-analysis-metadata"):
        if from_tenant_id not in tenant_ids:
            tenant_ids.append(from_tenant_id)
    return "AND tenant_id = ANY(%s)", [tenant_ids]


def _build_agent_scope_condition(
    agent_id: Optional[str], tenant_id: Optional[str]
) -> tuple:
    """构建名单可见性 SQL 条件（在完整租户/共享范围括号外 AND，防 OR 分支绕过）。

    agent_id=None（身份未知）→ 仅共享表（片段内实现，不放宽既有共享分类）；
    异常名单配置对任何身份均不可见（fail-closed，不当共享）。
    """
    from src.services.data_analysis.access_scope import agent_scope_visible_sql

    return agent_scope_visible_sql(agent_id, tenant_id)


def fetch_table_metadata_from_db(
    table_id: str,
    tenant_id: Optional[str],
    subagent_id: Optional[str],
    agent_id: Optional[str] = None,
) -> Optional[Dict]:
    """按 doc_id 回查 documents 表完整 metadata，构造 DataAnalyzer 所需格式。

    供 AnalysisAgent.load_table 与 SmartDataAnalysisTool 预加载复用：主智能体可能传入
    search_data_tables 的简化结构（仅 table_id/table_name/description，无 source），
    必须回查补全后才能加载数据。非数字 table_id 返回 None（调用方按"未找到"处理）。

    agent_id 为可信执行身份（ToolExecutionContext 映射，None=身份未知仅共享），
    名单条件 AND 在完整 `(原范围 OR 本租户移动兜底)` 括号外侧——限定表不能经
    共享来源租户或移动兜底分支绕过；来源租户的限定表对其他租户恒不可用
    （片段内绑定当前租户，跨租户同名 agent_id 不放行）。

    移动兜底（2026-09-20 设计 §4.4）：被移动出 data-analysis-metadata 的 [数据表]
    按标题前缀兜底加载；经移动分支命中的文档必须带完整 table_name/columns metadata，
    缺失则视为普通同名文档排除。
    """
    from src.db.database import get_db_connection

    if not tenant_id:
        return None
    try:
        numeric_id = int(table_id)
    except (TypeError, ValueError):
        return None

    tenant_sql, tenant_params = _build_tenant_scope_params(tenant_id, subagent_id)
    scope_sql, scope_params = _build_agent_scope_condition(agent_id, tenant_id)
    # ILIKE 模式必须参数化传值：带参执行时查询串里的字面 % 会被 psycopg2 当占位符解析
    moved_sql = " OR (tenant_id = %s AND title ILIKE %s)" if tenant_id else ""
    moved_params = [tenant_id, "[数据表] %"] if tenant_id else []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id, title, summary, metadata, source_type, tenant_id
            FROM documents
            WHERE id = %s
              AND (source_type = 'data-analysis-metadata'{tenant_sql}{moved_sql})
              AND {scope_sql}
            """,
            (numeric_id, *tenant_params, *moved_params, *scope_params),
        )
        row = cursor.fetchone()

    if not row:
        return None

    r = dict(row)
    meta = r.get("metadata", {})
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}

    # 移动分支命中（source_type 已被移动覆盖）：普通同名文档靠 metadata 判定排除
    if r.get("source_type") != "data-analysis-metadata" and not (
        meta.get("table_name") and meta.get("columns")
    ):
        return None

    result = {
        "table_id": str(r["id"]),
        "table_name": meta.get("table_name", r.get("title", "").replace("[数据表] ", "")),
        "description": r.get("summary", ""),
        "columns": meta.get("columns", []),
    }

    # 构造 source 对象；owner_tenant_id 为内部字段（文档归属租户），数据库分支
    # 取连接器时按「连接器租户 = 表来源租户」校验——跨租户共享表使用来源租户的
    # 连接器，而非调用租户；该字段不得暴露给 LLM 入参构造的 metadata
    owner_tenant_id = r.get("tenant_id")
    source = meta.get("source")
    if source:
        result["source"] = source
    elif meta.get("connector_id"):
        result["source"] = {
            "type": "database",
            "connector_id": str(meta["connector_id"]),
            "db_table_name": meta.get("table_name", ""),
        }
    elif meta.get("source_info"):
        # 尝试从 source_info 解析文件路径
        result["source"] = {
            "type": "excel",
            "file_path": meta["source_info"],
        }
    if isinstance(result.get("source"), dict) and owner_tenant_id:
        result["source"]["owner_tenant_id"] = str(owner_tenant_id)

    return result


def query_data_tables_list(
    keyword: str = "",
    tenant_id: Optional[str] = None,
    subagent_id: Optional[str] = None,
    loaded_table_ids: Optional[set] = None,
    agent_id: Optional[str] = None,
) -> List[Dict]:
    """查询租户范围内全部数据表列表（本租户 + 已启用共享来源租户 + 本租户内被移动的表）。

    供 AnalysisAgent.list_data_tables 与 SmartDataAnalysisTool 开局批量选表复用：
    后者在数据表数不超过全量阈值时据此一次性选中全部表预加载，免去内层模型
    逐个 search/load_table（2026-10 改进1）。名单条件 AND 在完整范围括号外侧，
    自动列表先按范围过滤、再由调用方以过滤后计数走 50/30 阈值原策略。

    agent_id 为可信执行身份（None=身份未知仅共享，不映射 main）。

    移动兜底（2026-09-20 设计 §4.4）：用户在知识库界面移动 [数据表] 文档会覆盖
    source_type，按标题前缀兜底纳入本租户被移动的表；共享侧不放宽（精确对约束）。
    """
    from src.db.database import get_db_connection

    if not tenant_id:
        return []
    loaded = loaded_table_ids or set()
    tenant_sql, tenant_params = _build_tenant_scope_params(tenant_id, subagent_id)
    scope_sql, scope_params = _build_agent_scope_condition(agent_id, tenant_id)
    # ILIKE 模式必须参数化传值：带参执行时查询串里的字面 % 会被 psycopg2 当占位符解析
    moved_sql = " OR (tenant_id = %s AND title ILIKE %s)" if tenant_id else ""
    moved_params = [tenant_id, "[数据表] %"] if tenant_id else []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if keyword:
            cursor.execute(
                f"""
                SELECT id, title, summary, metadata, source_type
                FROM documents
                WHERE (source_type = 'data-analysis-metadata'{tenant_sql}{moved_sql})
                  AND {scope_sql}
                  AND (title ILIKE %s OR summary ILIKE %s)
                ORDER BY created_at DESC
                """,
                (*tenant_params, *moved_params, *scope_params, f"%{keyword}%", f"%{keyword}%"),
            )
        else:
            cursor.execute(
                f"""
                SELECT id, title, summary, metadata, source_type
                FROM documents
                WHERE (source_type = 'data-analysis-metadata'{tenant_sql}{moved_sql})
                  AND {scope_sql}
                ORDER BY created_at DESC
                """,
                (*tenant_params, *moved_params, *scope_params),
            )

        results = []
        for row in cursor.fetchall():
            r = dict(row)
            meta = r.get("metadata", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            table_name = meta.get("table_name", r.get("title", "").replace("[数据表] ", ""))
            # 移动兜底命中（source_type 已被移动覆盖）：结构有效性校验（设计 §18.6
            # 「移动分支同时检查结构有效性」，与 fetch_table_metadata_from_db 的
            # Python 判定同口径）——仅同名前缀的普通知识文档不纳入列表与阈值计数
            if r.get("source_type") != "data-analysis-metadata" and not (
                meta.get("table_name") and meta.get("columns")
            ):
                continue
            table_id = str(r["id"])
            results.append({
                "table_id": table_id,
                "table_name": table_name,
                "description": r.get("summary", ""),
                "columns_count": len(meta.get("columns", [])),
                "loaded": table_id in loaded,
            })
        return results


def create_data_tables_retriever():
    """构建数据表语义检索器（HybridRetriever，向量维度 1024）。

    供 AnalysisAgent._get_retriever 与 SmartDataAnalysisTool 开局语义选表复用。
    """
    from src.config.settings import get_embedding_api_key
    from src.knowledge.vector_db.vector_db import get_vector_db
    from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
    from src.knowledge.retriever.hybrid_retriever import HybridRetriever

    return HybridRetriever(
        vector_db=get_vector_db(dimension=1024),
        embedding_client=TextEmbeddingV3Client(api_key=get_embedding_api_key()),
        conn=None,
    )


def filter_available_table_ids(
    table_ids,
    tenant_id: Optional[str],
    subagent_id: Optional[str],
    agent_id: Optional[str],
) -> set:
    """批量判断表 doc_id 在当前身份可见范围内是否可用（去重后单次查询）。

    产物复用过滤共用入口：范围 = 原 source_type 分支（本租户 + 已启用共享来源
    租户）+ 本租户移动兜底（移动分支同时要求结构有效性，防止同名前缀普通文档
    被当表），并在整体括号外 AND 名单条件；身份未知仅共享。缺租户返回空集
    （与列表/回查口径一致，不查询全表）。DB 异常向上抛出：权限读取失败必须
    明确暴露，不得当成「全部不可用」或「全部可用」静默处理。
    """
    from src.db.database import get_db_connection

    if not tenant_id or not table_ids:
        return set()
    numeric_ids: List[int] = []
    for tid in table_ids:
        try:
            numeric_ids.append(int(str(tid)))
        except (TypeError, ValueError):
            continue
    if not numeric_ids:
        return set()
    # 排序去重：参数顺序确定（日志/测试可复现）， ANY 集合语义不受顺序影响
    numeric_ids = sorted(set(numeric_ids))

    tenant_sql, tenant_params = _build_tenant_scope_params(tenant_id, subagent_id)
    # 移动兜底分支同时校验结构有效性（与 fetch_table_metadata_from_db 的
    # Python 判定同口径），并整体括号后再 AND 名单条件
    moved_sql = (
        " OR (tenant_id = %s AND title ILIKE %s"
        " AND jsonb_typeof(metadata::jsonb->'table_name') = 'string'"
        " AND length(btrim(metadata::jsonb->>'table_name')) > 0"
        " AND jsonb_typeof(metadata::jsonb->'columns') = 'array')"
    )
    moved_params = [tenant_id, "[数据表] %"]
    scope_sql, scope_params = _build_agent_scope_condition(agent_id, tenant_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id FROM documents
            WHERE id = ANY(%s)
              AND (source_type = 'data-analysis-metadata'{tenant_sql}{moved_sql})
              AND {scope_sql}
            """,
            (numeric_ids, *tenant_params, *moved_params, *scope_params),
        )
        return {str(row["id"]) for row in cursor.fetchall()}


def filter_reusable_artifact_entries(
    entries: List[Dict],
    tenant_id: Optional[str],
    subagent_id: Optional[str],
    agent_id: Optional[str],
) -> List[Dict]:
    """按「本会话已登记 + 每张来源表当前均可用」过滤可复用产物条目。

    - source_table_ids 缺失/null（旧条目或来源不全）→ 不复用，提示重算；
    - 全部条目的来源表 ID 去重后一次查询判可用，不做逐产物逐表循环查库；
    - 范围查询失败向上抛出（调用方决定暴露方式），不降级成「无可复用」。
    """
    if not entries:
        return []
    with_sources: List[Dict] = []
    needed: set = set()
    for e in entries:
        src = e.get("source_table_ids") if isinstance(e, dict) else None
        if isinstance(src, list) and src:
            with_sources.append(e)
            needed.update(str(t) for t in src)
    if not with_sources:
        return []
    available = filter_available_table_ids(needed, tenant_id, subagent_id, agent_id)
    return [
        e
        for e in with_sources
        if all(str(t) in available for t in (e.get("source_table_ids") or []))
    ]


class AnalysisAgent:
    """迷你 Agent 循环，LLM 通过 function calling 完成数据表检索、加载和分析。"""

    def __init__(
        self,
        llm_gateway,
        analyzer,
        analysis_id: str,
        tables_metadata: Optional[List[Dict]] = None,
        tenant_id: Optional[str] = None,
        subagent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        export_only_mode: bool = False,
        agent_id: Optional[str] = None,
    ):
        self.llm = llm_gateway
        self.analyzer = analyzer
        self.analysis_id = analysis_id
        self.tables_metadata = tables_metadata or []
        self.tenant_id = tenant_id
        self.subagent_id = subagent_id
        # 数据表名单执行身份：由可信 ToolExecutionContext 映射后传入
        #（subagent_id 非空即该子智能体，为空映射 main）；None=身份未知仅共享，
        # 不在此处自行映射 main（无上下文 ≠ 主智能体，见设计 §18.4）
        self.agent_id = agent_id
        # 会话 ID：分析产物注册表的作用域（同会话多次 analyze_data 复用中间产物）
        self.session_id = session_id or getattr(analyzer, "session_id", None) or None
        # 改进3（2026-10）：导出/整理类调用不预加载原始表（见 SmartDataAnalysisTool），
        # 开局消息改为引导复用已有产物；误判时内层仍可 search/load_table 兜底，fail-soft
        self.export_only_mode = export_only_mode

        self._loaded_table_ids: set = set()
        self._search_count: int = 0
        self._retriever = None
        # 改进3 lineage 拦截：已拦截过的表记入此处，同表二次 load_table 放行
        #（一次提示引导复用，模型坚持视为确需原始明细，不做死拦截）
        self._lineage_intercepted: set = set()
        # 变量 → 原始表来源集合（None=来源不全/未知）：跟随产物注册传播，
        # 决定新产物 source_table_ids；None 一旦出现持续传播不恢复
        self._var_sources: Dict[str, Optional[set]] = {}

        self._total_usage: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        # artifacts 是工具返回给主智能体的唯一产物索引（chart/table/data_file）
        self._artifacts: List[Dict] = []
        # 本次运行新注册的中间产物（供主智能体在后续 analyze_data 中引用复用）
        self._new_artifacts: List[Dict] = []
        # _steps 仅用于 trace 持久化，不放进对外返回值
        self._steps: List[Dict] = []
        self._spans: List[SpanRecord] = []
        # 出图兜底：总结前若"有表无图且数据可可视化"则提示补图（仅触发一次）
        self._chart_nudge_done: bool = False
        # 空总结兜底：无工具调用且 content 为空（推理烧穿 max_tokens 的典型特征）时重试一次
        self._empty_summary_retried: bool = False
        # 空总结重试时关闭思考（思考烧穿输出预算的修复：重试只为拿结论，无需再推理）
        self._disable_thinking_next: bool = False
        # describe memo 缓存（2026-10 追加改进 A）：键为 describe 参数 JSON，
        # 任何非 describe 工具成功执行后清空（_execute_tool 收口），杜绝过期结构
        self._describe_cache: Dict[str, Dict] = {}
        self._last_table_df = None

        # 如果传入了预加载的表，标记为已加载
        for meta in self.tables_metadata:
            tid = str(meta.get("table_id", meta.get("doc_id", "")))
            if tid:
                self._loaded_table_ids.add(tid)

    async def run(self, requirement: str) -> Dict[str, Any]:
        """执行分析任务，返回结果字典。"""
        start_time = time.time()

        # 产物清单复用资格含来源表可用性 DB 回查：async 入口先 to_thread 预取，
        # 不在事件循环上做同步 DB 调用（与 _handle_load_table 同口径）；
        # _build_user_message 保持同步签名，低层/测试直调走同步兜底
        reusable_entries = await asyncio.to_thread(self._reusable_artifacts)
        messages = [
            {"role": "user", "content": self._build_user_message(requirement, reusable_entries)}
        ]

        summary = ""
        iterations = 0

        for iteration in range(MAX_ITERATIONS):
            iterations = iteration + 1

            # 记录 LLM 调用 span
            llm_span_id = f"llm_{iteration}_{uuid.uuid4().hex[:8]}"
            llm_span = SpanRecord(
                span_id=llm_span_id,
                name="llm_call",
                start_time=time.time(),
                span_type="generation",
            )

            try:
                # 不显式传 max_tokens：由网关按当前生效模型查 llm.model_max_tokens
                # 配置取官方上限（qwen3.8-flash=32768），思考模式下复杂分析才有足够输出预算。
                # 显式硬上限（曾为 4000，后 16384）会让推理模型烧穿预算返回空结论（2026-08/09 生产事故）
                llm_kwargs: Dict[str, Any] = {}
                if self._disable_thinking_next:
                    from src.llm.gateway import _lite_thinking_off_params
                    llm_kwargs.update(_lite_thinking_off_params(self.llm.get_provider_name()))
                    self._disable_thinking_next = False
                response = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=ANALYSIS_TOOLS,
                    tool_choice="auto",
                    system_prompt=ANALYSIS_SYSTEM_PROMPT,
                    temperature=0.1,
                    **llm_kwargs,
                )
            except Exception as e:
                logger.error(f"AnalysisAgent LLM call failed: {e}")
                llm_span.success = False
                llm_span.result = str(e)
                llm_span.end_time = time.time()
                llm_span.duration_ms = int((llm_span.end_time - llm_span.start_time) * 1000)
                self._spans.append(llm_span)
                return self._build_result(success=False, error=str(e), iterations=iterations, start_time=start_time)

            # 记录 LLM span
            usage = response.get("usage", {})
            llm_span.end_time = time.time()
            llm_span.duration_ms = int((llm_span.end_time - llm_span.start_time) * 1000)
            llm_span.success = True
            llm_span.usage = usage
            llm_span.model = getattr(self.llm, "_model_name", None) or getattr(self.llm, "get_model_name", lambda: None)()
            llm_span.provider = getattr(self.llm, "get_provider_name", lambda: None)()
            llm_span.request_id = response.get("request_id")
            llm_span.result = response.get("content", "")[:500]
            self._spans.append(llm_span)

            # 累加 token 用量
            self._accumulate_usage(usage)
            # 累加 LLM 用量到当前 SessionRecordService（对话内后台 LLM 调用计费）
            # 显式传实际模型名：无会话记录兜底落库分支按正确单价折算积分
            from src.services.session_record import record_background_llm_usage
            record_background_llm_usage(usage, model=self.llm.get_model_name())

            content = response.get("content", "")
            tool_calls = response.get("tool_calls") or []

            # 无工具调用 → LLM 给出最终总结
            if not tool_calls:
                # 出图兜底：若已输出表格但无图表且数据可可视化，提示补图（仅一次）
                if self._maybe_nudge_to_chart(messages, content):
                    continue
                if not content.strip():
                    # 空总结：推理模型烧穿 max_tokens 时 content 为空（2026-08 生产事故
                    # 特征：completion 恰达上限 + content 空）。重试一次；仍空则明确失败，
                    # 绝不让主智能体拿到空结论误判为"知识库无数据"
                    if not self._empty_summary_retried:
                        self._empty_summary_retried = True
                        # 重试关思考：原样重试会复现思考烧穿（同一上下文 + 思考开启），
                        # 推理已完成，第二次调用只为直接产出结论
                        self._disable_thinking_next = True
                        messages.append({"role": "assistant", "content": content})
                        messages.append({
                            "role": "user",
                            "content": "（系统提示）你上一步没有输出任何分析结论（可能因思考过程耗尽输出预算）。"
                                       "请直接给出简洁的最终分析结论，不要再进行推理验算；"
                                       "如确需补充计算可再调用工具，但不要重复已完成步骤。",
                        })
                        logger.warning(f"[AnalysisAgent] 空总结（iteration={iteration}），关闭思考重试一次")
                        continue
                    logger.error(f"[AnalysisAgent] 空总结重试后仍为空（iteration={iteration}），返回失败")
                    return self._build_result(
                        success=False,
                        error="分析模型输出为空（可能因输出长度超限被截断），请重试或简化分析需求",
                        iterations=iterations,
                        start_time=start_time,
                    )
                summary = content
                break

            # 将 assistant 消息（含 tool_calls）加入对话
            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": content, "tool_calls": tool_calls}
            # DeepSeek 思考模式官方契约：thinking 轮响应的 reasoning_content 须随历史回传，
            # 否则开思考请求 400 "must be passed back"（2026-10-12 服务端新增校验）。
            # 注意：同一会话混合开/关思考轮会被该校验不稳定击穿（关思考轮响应无 rc，
            # 空串占位也防不住，run2/run3 双双 400），故思考模式必须全场统一——
            # 机械轮关思考分级已撤回，本挂载仅在全开思考模式下回传真值。
            # 其他 provider 的 _format_messages 不转发 rc，行为不变
            if response.get("reasoning_content"):
                assistant_msg["reasoning_content"] = response["reasoning_content"]
            messages.append(assistant_msg)

            # 执行每个工具调用（一轮多个 tool_calls 顺序执行，逐个错误隔离：
            # _dispatch_tool 内部捕获异常转为 {"success": False}，单工具失败不中断整轮）
            for tc in tool_calls:
                tool_name = tc.get("name") or tc.get("function", {}).get("name", "")
                raw_args = tc.get("arguments") or tc.get("function", {}).get("arguments", {})

                # 解析参数
                if isinstance(raw_args, str):
                    try:
                        params = json.loads(raw_args)
                    except json.JSONDecodeError:
                        params = {}
                else:
                    params = dict(raw_args) if raw_args else {}

                tc_id = tc.get("id", f"tc_{iteration}_{tool_name}")

                # 执行工具
                tool_span_id = f"tool_{iteration}_{tool_name}_{uuid.uuid4().hex[:8]}"
                tool_span = SpanRecord(
                    span_id=tool_span_id,
                    name=tool_name,
                    start_time=time.time(),
                    span_type="span",
                    tool_args=json.dumps(params, ensure_ascii=False)[:500],
                )

                result = await self._execute_tool(tool_name, params, iteration)
                tool_span.end_time = time.time()
                tool_span.duration_ms = int((tool_span.end_time - tool_span.start_time) * 1000)
                tool_span.success = result.get("success", False)
                tool_span.result = json.dumps(result, ensure_ascii=False)[:500]
                self._spans.append(tool_span)

                # 工具结果加入对话
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

            # 思考分级（2026-10 改进点 4）已撤回（2026-10-12）：DeepSeek 服务端对
            # 混合开/关思考历史的 rc 校验不可预测（空串占位仍 400，run3 六连），
            # 同一会话必须全程统一思考模式，故保留全场开思考。

            # 上下文压缩：当 messages 过大时，压缩早期的工具结果为摘要
            self._compress_messages(messages)
        else:
            # 达到 MAX_ITERATIONS
            summary = content or "分析完成（达到最大迭代次数）"
            logger.warning(f"AnalysisAgent reached MAX_ITERATIONS={MAX_ITERATIONS}")

        return self._build_result(success=True, summary=summary, iterations=iterations, start_time=start_time)

    async def _execute_tool(self, method_name: str, params: Dict, iteration: int) -> Dict[str, Any]:
        """执行单个工具调用，返回结果字典。

        成功执行后输出 [AnalysisAgent][TOOL] 打点（改进1验证依赖）：重放日志据此
        统计内层工具调用次数，对比改进前后的 load_table / search 探索轮数。
        """
        result = await self._dispatch_tool(method_name, params, iteration)
        if result.get("success", False):
            logger.info(f"[AnalysisAgent][TOOL] {method_name}")
            # describe memo 缓存失效（追加改进 A）：任何非 describe 工具成功执行都可能
            # 改变可描述的数据面（data_method 改写/新增产物、load_table/load_output 换源），
            # 统一在此清空，保证缓存绝不返回过期结构（过宽失效只是多算一次，成本可忽略）
            if method_name != "describe" and self._describe_cache:
                self._describe_cache.clear()
        return result

    async def _dispatch_tool(self, method_name: str, params: Dict, iteration: int) -> Dict[str, Any]:
        """按方法名分发执行（_execute_tool 主体，成功打点在其上层统一收口）。"""
        # 白名单校验
        if method_name not in ALLOWED_METHODS:
            return {"success": False, "error": f"不允许的方法: {method_name}"}

        # 检索工具
        if method_name == "search_data_tables":
            self._search_count += 1
            return await self._handle_search_data_tables(
                query=params.get("query", ""),
                top_k=params.get("top_k", 5),
            )
        if method_name == "list_data_tables":
            return await self._handle_list_data_tables(params.get("keyword", ""))

        # 加载工具
        if method_name == "load_table":
            return await self._handle_load_table(params.get("table_id", ""))
        if method_name == "load_output":
            # 复用资格校验含来源表可用性 DB 回查：async 分发路径 to_thread 包裹，
            # 不在事件循环上做同步 DB 调用（与 _handle_load_table 同口径）
            return await asyncio.to_thread(
                self._handle_load_output, params.get("output_var", "")
            )

        # 概览工具（返回 dict，不走 DATA_PROCESSING_METHODS 路径）
        if method_name == "describe":
            return self._handle_describe(params, iteration)

        # 分析工具
        output_var = params.pop("output_var", f"step_{iteration}")

        try:
            method = getattr(self.analyzer, method_name, None)
            if method is None:
                return {"success": False, "error": f"方法不存在: {method_name}"}

            if asyncio.iscoroutinefunction(method):
                result = await method(**params)
            else:
                result = method(**params)

            # 数据处理方法 → 持久化 + 摘要
            if method_name in DATA_PROCESSING_METHODS:
                return self._handle_data_method(method_name, output_var, result, params)

            # to_table → 收集表格输出
            if method_name == "to_table":
                return self._handle_to_table(output_var, result, params)

            # to_chart → 收集图表输出
            if method_name == "to_chart":
                return self._handle_to_chart(output_var, result, params)

            return {"success": False, "error": f"未知方法类型: {method_name}"}

        except Exception as e:
            logger.info(f"AnalysisAgent tool execution failed (可恢复): {method_name} - {e}")
            return {"success": False, "error": str(e)}

    def _handle_data_method(self, method_name: str, output_var: str, df, params: Dict) -> Dict[str, Any]:
        """处理数据处理方法：持久化 DataFrame，返回摘要给 LLM。"""
        import pandas as pd

        if not isinstance(df, pd.DataFrame):
            return {"success": False, "error": f"方法 {method_name} 未返回 DataFrame"}

        file_path = self.analyzer._store(output_var, df)
        columns = list(df.columns)
        rows = len(df)

        # 前 3 行预览
        preview_df = df.head(3)
        preview = self._df_to_native_rows(preview_df)

        # 构建步骤描述
        description = self._describe_step(method_name, params)

        # 数据表来源集合传播（设计 §18.7）：原始表操作得 {table_id}；沿用输入变量
        # 集合；merge/append 取并集；任一输入来源未知或并集超上限 → None 持续传播
        source_table_ids = self._propagate_source_table_ids(output_var, method_name, params)

        # 注册到会话产物注册表：同会话后续 analyze_data 可 load_output 复用，
        # 免去重复「加载原始表 → 加工 → 合并」（392 积分事故主因）。
        # 改进3（2026-10）：同时记录 lineage 直接上游，供产物溯源；
        # 复用资格以 source_table_ids（完整原始表集合）为准，upstreams 仅是提示
        self._register_artifact(
            output_var, method_name, description, rows, columns,
            upstreams=self._extract_source_refs(method_name, params),
            source_table_ids=source_table_ids,
        )

        step = {
            "step": len(self._steps) + 1,
            "method": method_name,
            "output_var": output_var,
            "description": description,
            "file_path": file_path,
            "result_summary": {
                "rows": rows,
                "columns": columns,
                "preview_columns": columns,
                "preview": preview,
            },
        }
        self._steps.append(step)

        return {
            "success": True,
            "output_var": output_var,
            "file_path": file_path,
            "rows": rows,
            "columns": columns,
            "preview_columns": columns,
            "preview_rows": min(3, rows),
            "hint": f"后续步骤可通过 output_var '{output_var}' 或文件路径 '{file_path}' 引用此数据",
        }

    def _handle_to_table(self, output_var: str, result: Dict, params: Dict) -> Dict[str, Any]:
        """处理 to_table 输出方法：收集表格 artifact（含 preview 供主智能体直接回答）。"""
        columns = result.get("columns", [])
        rows = result.get("rows", [])
        row_count = result.get("row_count", len(rows))
        total_count = result.get("total_count", row_count)

        # 出图兜底判定：用 to_table 实际输出（列筛选+行数限制后）而非全量源，避免误判
        try:
            import pandas as pd
            self._last_table_df = pd.DataFrame(rows, columns=columns) if columns else None
        except Exception:
            self._last_table_df = None

        # preview 最多 10 行（含表头），让主智能体能直接回答简单数据问题
        from src.tools.data_analysis.data_analyzer import DataAnalyzer
        preview_rows = rows[:10]
        preview = [columns] + [[DataAnalyzer._to_native(v) for v in row] for row in preview_rows] if columns else []

        # 导出表格为 Excel 文件供下载
        download_path = ""
        try:
            source_name = params.get("source", output_var)
            source_df = self.analyzer._resolve_source(source_name)
            if source_df is not None:
                os.makedirs(self.analyzer.chart_output_dir, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_title = re.sub(r'[\\/:*?"<>|]', "_", params.get("title") or output_var)
                download_path = os.path.join(
                    self.analyzer.chart_output_dir, f"{safe_title}_{timestamp}.xlsx"
                )
                # 如果 to_table 做了列筛选，导出时也只导出对应列
                if columns:
                    export_df = source_df[columns].head(params.get("max_rows", 50))
                else:
                    export_df = source_df.head(params.get("max_rows", 50))
                export_df.to_excel(download_path, index=False, engine="openpyxl")
                logger.info(f"[AnalysisAgent] 表格导出: {download_path}")
        except Exception as e:
            logger.warning(f"[AnalysisAgent] 表格导出失败，不影响预览: {e}")

        artifact = {
            "id": output_var,
            "type": "table",
            "title": params.get("title") or output_var,
            "description": f"{row_count} 行数据" + (f"（共 {total_count} 行）" if total_count != row_count else ""),
            "preview": preview,
            "row_count": row_count,
            "total_count": total_count,
            "download_path": download_path,
            "format": "xlsx",
            "ready_for_download": bool(download_path),
        }
        self._artifacts.append(artifact)

        # 步骤记录（仅 trace 用）
        step = {
            "step": len(self._steps) + 1,
            "method": "to_table",
            "output_var": output_var,
            "description": f"输出结构化表格: {row_count} 行",
            "result_summary": {
                "row_count": row_count,
                "total_count": total_count,
                "columns": columns,
                "preview": preview_rows,
            },
        }
        self._steps.append(step)

        # 返回给 AnalysisAgent 内部 LLM 的摘要
        return {
            "success": True,
            "type": "table",
            "output_var": output_var,
            "row_count": row_count,
            "total_count": total_count,
            "columns": columns,
            "preview": preview_rows,
            "note": "表格已输出，最终结果由 conclusion 统一描述",
        }

    def _handle_to_chart(self, output_var: str, result: Dict, params: Dict) -> Dict[str, Any]:
        """处理 to_chart 输出方法：收集图表 artifact。"""
        file_path = result.get("file_path", "")
        chart_type = result.get("chart_type", params.get("chart_type", ""))
        title = result.get("title", params.get("title", ""))
        theme_name = result.get("theme_name", "")
        theme_suffix = f"（{theme_name}）" if theme_name else ""

        artifact = {
            "id": output_var,
            "type": "chart",
            "title": title,
            "description": f"{chart_type} 图表{theme_suffix}",
            "download_path": file_path,
            "format": "png",
            "ready_for_download": bool(file_path),
        }
        self._artifacts.append(artifact)

        # 步骤记录（仅 trace 用）
        step = {
            "step": len(self._steps) + 1,
            "method": "to_chart",
            "output_var": output_var,
            "description": f"生成{chart_type}图表{theme_suffix}: {title}",
            "file_path": file_path,
            "chart_type": chart_type,
            "title": title,
            "theme_name": theme_name,
        }
        self._steps.append(step)

        # 返回给 AnalysisAgent 内部 LLM 的摘要
        return {
            "success": True,
            "type": "chart",
            "output_var": output_var,
            "title": title,
            "chart_type": chart_type,
            "theme_name": theme_name,
            "note": "图表已生成，最终结果由 conclusion 统一描述",
        }

    def _maybe_nudge_to_chart(self, messages: List[Dict], content: str) -> bool:
        """总结前出图兜底：若已输出表格但无图表、且该表数据可可视化，注入提示让 LLM 补图。

        仅触发一次（_chart_nudge_done）；LLM 若二次仍不补图则放行，避免死循环。
        """
        if self._chart_nudge_done:
            return False
        has_chart = any(a.get("type") == "chart" for a in self._artifacts)
        if has_chart:
            return False
        has_table = any(a.get("type") == "table" for a in self._artifacts)
        if not has_table:
            return False  # 连表格都没有，不强制出图
        if not self._is_chartable(self._last_table_df):
            return False
        # 注入补图提示：附列名（防止 tool 结果被上下文压缩后 LLM 记不清列名）
        df = self._last_table_df
        cols_hint = ", ".join(str(c) for c in df.columns)
        self._chart_nudge_done = True
        messages.append({"role": "assistant", "content": content})
        messages.append({
            "role": "user",
            "content": f"（系统提示）检测到你已输出表格但尚未生成图表。该结果含多行可统计的数值维度，适合可视化——请根据「数据类型→图表决策表」调用 to_chart 生成合适的图表（该表列：{cols_hint}），然后再给出最终总结。若确属单条记录/纯文本等不宜出图场景，可直接总结。",
        })
        logger.info("[AnalysisAgent] 触发出图兜底：提示 LLM 补充图表")
        return True

    @staticmethod
    def _is_chartable(df) -> bool:
        """判断 DataFrame 是否适合出图：行数≥2 + 至少1个数值列 + 至少1个非数值维度列。"""
        if df is None:
            return False
        try:
            import pandas as pd
            if len(df) < 2:
                return False
            has_numeric = any(pd.api.types.is_numeric_dtype(df[c]) for c in df.columns)
            has_dim = any(not pd.api.types.is_numeric_dtype(df[c]) for c in df.columns)
            return has_numeric and has_dim
        except Exception as e:
            logger.debug(f"[AnalysisAgent] _is_chartable 判断异常: {e}")
            return False

    def _build_user_message(
        self, requirement: str, reusable_entries: Optional[List[Dict]] = None
    ) -> str:
        """构建初始用户消息。reusable_entries 可传预取的可复用产物条目（async 入口）。"""
        parts = [f"## 用户需求\n{requirement}"]

        if self._loaded_table_ids:
            # 改进1（2026-10）：表已由服务端预加载并附完整 schema，开局即明示禁止对
            # 清单内的表重复 search/load（此前 36 张同构表被内层模型逐个 load_table
            # 共 82 次，全量列定义永久滞留上下文拖垮成本）
            table_count = len(self.tables_metadata)
            parts.append(
                "## 已加载的数据表（系统已预加载）\n"
                f"以下 {table_count} 张表已由系统预加载完成并附完整字段结构"
                "（表名/列名/类型/说明），直接开始分析；对清单内的表调用 load_table 或 "
                "search_data_tables 属于错误，仅当确实需要清单外的表时才允许，属例外情况。\n"
                f"{self._build_tables_info()}"
            )
        elif self.export_only_mode:
            # 改进3（2026-10）：导出/整理类任务跳过了 49 表批量预加载（导出应基于已有
            # 产物而非重新加载原始数据），开局明示复用优先，杜绝重新加工链路
            parts.append(
                "## 说明（导出/整理任务模式）\n"
                "本需求为导出/整理类任务，系统**有意未预加载原始数据表**：导出应基于本会话"
                "已有分析产物（见下方产物清单）完成，禁止重新加载原始表再加工一遍。"
                "请直接 load_output 加载所需产物后完成导出/格式化；"
                "仅当产物清单确实无法覆盖所需数据时，才允许 search_data_tables / load_table 加载原始表（属例外情况）。"
            )
        else:
            parts.append(
                "## 说明\n"
                "目前没有预加载的数据表。请先分析用户需求，提取搜索关键词，"
                "使用 search_data_tables 搜索相关数据表，"
                "然后用 load_table 加载需要的表进行分析。"
            )

        artifacts_info = self._build_artifacts_info(reusable_entries)
        if artifacts_info:
            parts.append(artifacts_info)

        return "\n\n".join(parts)

    def _build_artifacts_info(self, entries: Optional[List[Dict]] = None) -> str:
        """构建本会话可复用分析产物清单（供 load_output 复用）；无可复用产物返回空串。

        改进3（2026-10）：条目附 lineage 来源（原始表/上游产物），让模型能判断
        「某表的加工产物已存在」，从源头压掉重新 load_table -> 清洗 -> 合并链路。
        复用资格：本会话已登记 + 每张来源表当前均可用（批量去重回查一次）；
        旧条目/来源不全的产物不注入清单（提示重新分析，见 load_output）。
        entries=None 时同步兜底自查（含 DB 回查，供低层/测试直调）；
        async 入口（run）应先 to_thread 预取后传入，避免阻塞事件循环。
        """
        if entries is None:
            entries = self._reusable_artifacts()
        if not entries:
            return ""
        known_vars = {e["var"] for e in entries}
        lines = [
            "## 本会话已有分析产物（优先复用！）",
            "以下产物是本会话此前分析已生成的中间数据，**若其中已覆盖当前需求所需的数据"
            "（如已合并的宽表、已聚合的结果），必须优先调用 load_output(output_var) 加载复用，"
            "禁止重新 load_table 原始表再做一遍相同的加载、清洗和合并**：",
        ]
        for e in entries:
            cols = ", ".join(e.get("columns", [])[:8])
            col_note = f"（列: {cols}）" if cols else ""
            # lineage 来源标注：原始表引用原样展示，指向清单内上游产物的引用折叠为「上游产物 var」
            sources = []
            for ref in e.get("upstreams") or []:
                sources.append("上游产物" if str(ref) in known_vars else str(ref))
            src_note = f"；来源: {'、'.join(sources[:5])}" if sources else ""
            lines.append(
                f"- `{e['var']}`: {e.get('rows', 0)} 行{col_note} — {e.get('description', '')}{src_note}"
            )
        lines.append("若清单中没有所需数据，再按正常流程 search/load 原始表。")
        return "\n".join(lines)

    def _reusable_artifacts(self) -> List[Dict]:
        """本会话当前可复用的产物条目（已登记 + 来源表当前均可用）。

        范围查询失败降级为空清单并告警：清单注入/拦截是优化提示，缺失只会让
        模型重新加工（多耗成本），不得让整次分析因优化路径异常而中断；
        load_output 的校验失败不在此降级（那里必须显式报错）。
        """
        try:
            return filter_reusable_artifact_entries(
                self._session_artifacts(),
                self.tenant_id,
                self.subagent_id,
                self.agent_id,
            )
        except Exception as e:
            logger.warning(f"[AnalysisAgent] 产物来源可用性检查失败（本次不复用）: {e}")
            return []

    def _propagate_source_table_ids(
        self, output_var: str, method_name: str, params: Dict
    ) -> Optional[List[str]]:
        """计算本产物应登记的原始表来源集合，并维护 var→来源映射。

        输入引用解析：内存变量沿用其集合（None=未知传播）；已加载原始表得
        {table_id}；其余引用（文件路径等）视为来源未知 → None。merge/append
        取输入并集，其余方法取单输入集合；并集超 SOURCE_TABLE_IDS_LIMIT 或
        任一输入未知 → None（不保存截断集合，None 不得回退为已知）。
        """
        from src.tools.data_analysis.analysis_artifacts import SOURCE_TABLE_IDS_LIMIT

        refs = self._extract_source_refs(method_name, params)
        merged: Optional[set] = set()
        for ref in refs:
            if ref in self._var_sources:
                src = self._var_sources[ref]
            elif ref in self._loaded_table_ids or ref in self.analyzer._tables:
                src = {ref}
            else:
                src = None
            if src is None:
                merged = None
                break
            merged.update(src)
        if merged is not None and len(merged) > SOURCE_TABLE_IDS_LIMIT:
            merged = None
        self._var_sources[output_var] = merged
        return sorted(merged) if merged is not None else None

    def _compress_messages(self, messages: List[Dict]) -> None:
        """当 messages 过大时，将早期工具结果压缩为摘要，保留最近几轮的详细信息。"""
        total_size = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
        if total_size < CONTEXT_COMPRESS_THRESHOLD:
            return

        # 保留最近 8 轮（assistant + tool 各一条）的详细信息，压缩更早的
        # 找到 tool 消息中可以压缩的部分（从前往后，跳过 user 消息）
        compress_boundary = max(3, len(messages) - 16)
        compressed_count = 0

        for i in range(compress_boundary):
            msg = messages[i]
            if msg.get("role") != "tool":
                continue

            content = msg.get("content", "")
            if not content or len(content) < 200:
                continue

            try:
                data = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue

            # 只压缩包含 preview 的结果（to_table / describe / data_method）
            needs_compress = False
            if "preview" in data and isinstance(data.get("preview"), list):
                needs_compress = True
            if "preview_rows" in data:
                needs_compress = True

            if not needs_compress:
                continue

            # 压缩：去掉 preview，只保留摘要信息
            data.pop("preview", None)
            data.pop("preview_rows", None)
            data.pop("preview_columns", None)
            data["_compressed"] = True

            messages[i]["content"] = json.dumps(data, ensure_ascii=False)
            compressed_count += 1

        if compressed_count > 0:
            logger.debug(f"Context compressed: {compressed_count} early tool results truncated, "
                         f"size {total_size} -> {sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)}")

    def _build_tables_info(self) -> str:
        """构建表 Metadata 文本描述。

        只渲染对模型有意义的白名单字段（表名/说明/行列数/列定义/非空枚举值）；
        source 文件路径、connector_id 等内部字段一律不进给模型的消息。
        """
        parts = []
        for meta in self.tables_metadata:
            table_id = meta.get("table_id", "unknown")
            table_name = meta.get("table_name", meta.get("name", "未命名"))
            description = meta.get("description", "")
            columns = meta.get("columns", [])
            row_count = meta.get("row_count", "?")
            col_count = meta.get("column_count") or (len(columns) if columns else "?")

            info = f"### 表: {table_name} (table_id: {table_id})\n"
            if description:
                info += f"说明: {description}\n"
            info += f"行数: {row_count}, 列数: {col_count}\n"

            if columns:
                info += "字段:\n"
                for col in columns:
                    col_name = col.get("name", col.get("semantic_name", ""))
                    col_type = col.get("data_type", col.get("type", ""))
                    col_desc = col.get("description", "")
                    info += f"  - {col_name} ({col_type})"
                    if col_desc:
                        info += f": {col_desc}"
                    # 列级过滤：仅非空 enum_values 参与渲染（与 schema_saver 口径一致，最多 10 个）
                    enum_values = col.get("enum_values") or []
                    if enum_values:
                        info += ", 枚举值: " + ", ".join(str(v) for v in enum_values[:10])
                    info += "\n"

            parts.append(info)

        return "\n".join(parts) if parts else "无可用数据表"

    def _build_result(
        self,
        success: bool,
        summary: str = "",
        error: str = "",
        iterations: int = 0,
        start_time: float = 0,
    ) -> Dict[str, Any]:
        """构建最终返回结果，持久化 trace。"""
        duration_ms = int((time.time() - start_time) * 1000) if start_time else 0

        trace = TraceRecord(
            trace_id=self.analysis_id,
            session_id=self.analysis_id,
            tenant_id="",
            user_id="",
            subagent_id=None,
            input=self._steps[0].get("description", "")[:500] if self._steps else "",
            source_type="data_analysis",
            start_time=start_time,
            status="completed" if success else "failed",
            output=(summary or error)[:500],
            error_message=error if not success else None,
            spans=self._spans,
            total_tokens=self._total_usage.get("total_tokens", 0),
            model=getattr(self.llm, "_model_name", None) or getattr(self.llm, "get_model_name", lambda: None)(),
            provider=getattr(self.llm, "get_provider_name", lambda: None)(),
            agent_iterations=iterations,
            duration_ms=duration_ms,
        )
        try:
            schedule_persist(trace)
        except Exception as e:
            logger.warning(f"Failed to persist trace: {e}")

        result = {
            "success": success,
            "conclusion": summary or error or "",
            "artifacts": self._artifacts,
            "analysis_meta": {
                "iterations": iterations,
                "duration_ms": duration_ms,
                "tokens_used": self._total_usage.get("total_tokens", 0),
                "tables_used": list(self._loaded_table_ids),
                "trace_id": self.analysis_id,
            },
        }
        # 本次运行新注册的中间产物：主智能体可在后续 analyze_data 的 requirement 中
        # 引用这些 output_var 实现复用（无新产物时不携带，避免结果膨胀）
        if self._new_artifacts:
            result["reusable_outputs"] = self._new_artifacts
        if error:
            result["error"] = error
        return result

    def _accumulate_usage(self, usage: Dict):
        """累加 token 用量。"""
        if not usage:
            return
        self._total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        self._total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
        self._total_usage["total_tokens"] += usage.get("total_tokens", 0)

    def _describe_step(self, method_name: str, params: Dict) -> str:
        """生成步骤描述文本。"""
        if method_name == "query":
            parts = []
            if params.get("filters"):
                parts.append(f"过滤 {len(params['filters'])} 个条件")
            if params.get("columns"):
                parts.append(f"选择 {len(params['columns'])} 列")
            if params.get("sort_by"):
                parts.append(f"按 {params['sort_by']} 排序")
            if params.get("limit"):
                parts.append(f"限 {params['limit']} 行")
            return f"查询数据: {', '.join(parts) or '全部'}"

        if method_name == "aggregate":
            group = ", ".join(params.get("group_by", []))
            agg_count = len(params.get("aggregations", []))
            return f"按 {group} 聚合，{agg_count}个聚合操作"

        if method_name == "merge":
            how = params.get("how", "left")
            return f"{how} 关联 {params.get('left_ref', '?')} 和 {params.get('right_ref', '?')}"

        if method_name == "append":
            sources = params.get("sources", [])
            labels = list((params.get("label_columns") or {}).keys())
            label_note = f"，标签列: {', '.join(labels)}" if labels else ""
            return f"纵向堆叠 {len(sources)} 张表{label_note}"

        if method_name == "pivot":
            return f"透视: {params.get('index', '?')} × {params.get('columns', '?')}"

        if method_name == "calculate":
            ops = params.get("operations", [])
            aliases = [op.get("alias", "?") for op in ops]
            return f"计算新列: {', '.join(aliases)}"

        if method_name == "compare":
            return f"按 {params.get('compare_column', '?')} 对比 {params.get('value_column', '?')}"

        if method_name == "trend":
            return f"{params.get('value_column', '?')} 的 {params.get('freq', 'M')} 趋势"

        if method_name == "extract_hierarchy":
            level = params.get("target_level", "?")
            path_col = params.get("path_column", "?")
            val_col = params.get("value_column", "")
            if val_col:
                return f"从 {path_col} 提取第{level}级，按目标层级聚合 {val_col}"
            return f"从 {path_col} 提取第{level}级层级映射"

        return method_name

    # ==================== 表检索与加载 ====================

    def _get_retriever(self):
        """惰性初始化 HybridRetriever。"""
        if self._retriever is None:
            self._retriever = create_data_tables_retriever()
        return self._retriever

    def _load_shared_ranges(self) -> List[tuple]:
        """读取已启用共享分类（本租户 + 子智能体维度），仅子智能体 + 租户模式生效。"""
        from src.knowledge.retriever.tenant_range import load_shared_ranges

        return load_shared_ranges(self.tenant_id, self.subagent_id, "data-analysis-metadata")

    def _build_tenant_scope(self) -> tuple:
        """构建租户范围 SQL 与参数：本租户 + 已启用共享来源租户。subagent_id 为空时退化为本租户。"""
        return _build_tenant_scope_params(self.tenant_id, self.subagent_id)

    async def _handle_search_data_tables(self, query: str, top_k: int = 5) -> Dict[str, Any]:
        """语义搜索数据表。"""
        if not query:
            return {"success": False, "error": "查询不能为空", "results": [], "count": 0}

        try:
            retriever = self._get_retriever()
            # 共享检索范围：本租户 + 已启用共享来源租户（仅子智能体 + 租户模式生效）
            shared_ranges = []
            if self.tenant_id and self.subagent_id:
                shared_ranges = await asyncio.to_thread(self._load_shared_ranges)
            results = await retriever.retrieve(
                query=query,
                top_k=top_k,
                tenant_id=self.tenant_id,
                source_type="data-analysis-metadata",
                shared_ranges=shared_ranges,
                agent_id=self.agent_id,
            )

            if not results:
                # 移动兜底（2026-09-20 设计 §4.4）：语义检索受 source_type 过滤影响，
                # 对被移动出 data-analysis-metadata 栏目的 [数据表] 失效；0 命中且
                # 放宽后列表非空时，将列表融入返回结果，引导 LLM 直接 load_table
                fallback_tables = []
                if self.tenant_id:
                    try:
                        fallback_tables = await asyncio.to_thread(self._query_data_tables_list, "")
                    except Exception as fallback_err:
                        logger.warning(f"search_data_tables 移动兜底查询失败: {fallback_err}")
                if fallback_tables:
                    formatted = [
                        {
                            "table_id": t["table_id"],
                            "table_name": t["table_name"],
                            "description": t.get("description", ""),
                            "score": 0.0,
                            "columns_count": t.get("columns_count", 0),
                            "loaded": t.get("loaded", False),
                        }
                        for t in fallback_tables
                    ]
                    return {
                        "success": True,
                        "results": formatted,
                        "count": len(formatted),
                        "hint": (
                            "语义检索未命中，以上为本租户全部可统计数据表（含被移动到其他栏目的数据表），"
                            "请从中选择并使用 load_table(table_id) 加载"
                        ),
                    }

                hint = "未找到匹配的数据表"
                if self._search_count >= 3:
                    hint += "。建议使用 list_data_tables 查看全量数据表列表"
                return {"success": True, "results": [], "count": 0, "hint": hint}

            # 用 doc_id 批量查 documents 表获取完整 metadata
            doc_ids = list({r["doc_id"] for r in results})
            doc_map = await asyncio.to_thread(self._fetch_documents_metadata, doc_ids)

            formatted = []
            for r in results:
                doc = doc_map.get(r["doc_id"], {})
                meta = doc.get("metadata", {})
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except (json.JSONDecodeError, TypeError):
                        meta = {}

                table_name = meta.get("table_name", doc.get("title", "").replace("[数据表] ", ""))
                table_id = str(r["doc_id"])

                formatted.append({
                    "table_id": table_id,
                    "table_name": table_name,
                    "description": doc.get("summary", ""),
                    "score": round(r["score"], 4),
                    "columns_count": len(meta.get("columns", [])),
                    "loaded": table_id in self._loaded_table_ids,
                })

            hint = "使用 load_table(table_id) 加载需要的表"
            if self._search_count >= 3:
                hint += "。如果仍未找到所需的表，可使用 list_data_tables 查看全量列表"

            return {
                "success": True,
                "results": formatted,
                "count": len(formatted),
                "hint": hint,
            }

        except Exception as e:
            logger.error(f"search_data_tables failed: {e}")
            return {"success": False, "error": str(e), "results": [], "count": 0}

    async def _handle_list_data_tables(self, keyword: str = "") -> Dict[str, Any]:
        """列出所有可用的数据表。"""
        try:
            tables = await asyncio.to_thread(self._query_data_tables_list, keyword)
            return {
                "success": True,
                "tables": tables,
                "count": len(tables),
                "hint": "使用 load_table(table_id) 加载需要的表进行分析",
            }
        except Exception as e:
            logger.error(f"list_data_tables failed: {e}")
            return {"success": False, "error": str(e), "tables": [], "count": 0}

    async def _handle_load_table(self, table_id: str) -> Dict[str, Any]:
        """加载指定表到分析环境。

        顺序约束（设计 §18.7 生效口径）：已加载的表不撤回（直接确认）；
        尚未加载的表先按当前名单回查范围——不可用统一拒绝（不区分不存在/无权，
        也不在此步返回等价产物信息，避免向已失去访问权的身份泄露产物存在性）；
        通过后再做等价产物拦截，最后才物理加载。检查与读取之间名单变化不做
        长事务锁定，已通过本次检查的读取按既定步骤完成。
        """
        if not table_id:
            return {"success": False, "error": "table_id 不能为空"}

        if table_id in self._loaded_table_ids:
            for meta in self.tables_metadata:
                if str(meta.get("table_id", meta.get("doc_id", ""))) == table_id:
                    # 改进1：表已在开局由系统预加载，列定义已在初始消息清单里——
                    # 再回吐全量 columns 只会重复膨胀上下文，这里只回精简确认
                    return {
                        "success": True,
                        "table_id": table_id,
                        "table_name": meta.get("table_name", ""),
                        "message": "该表已在开局由系统预加载完成，字段结构见初始消息清单，无需重复加载",
                        "row_count": meta.get("row_count", 0),
                        "column_count": len(meta.get("columns", [])),
                    }
            return {"success": True, "table_id": table_id, "message": "表已加载"}

        # 先回查当前范围（含名单）：未命中说明表不存在或当前身份不可用，
        # 统一拒绝，不区分原因、不返回该表的任何产物信息
        table_meta = await asyncio.to_thread(self._fetch_single_table_metadata, table_id)
        if not table_meta:
            return {
                "success": False,
                "error": f"数据表 {table_id} 当前不可用（不存在或未开放给当前调用身份），"
                         "请用 search_data_tables / list_data_tables 选择可用数据表",
            }

        # 改进3（2026-10）lineage 拦截：该表的等价加工产物已存在（且仍可复用）时，
        # 首次调用不执行加载，返回提示引导 load_output 复用，阻断「重新加载-清洗-
        # 合并」链路（392 积分事故主因）。软拦截：同表二次调用放行——模型坚持视为
        # 确需原始明细。等价产物只在可复用集合内判断（已登记 + 来源表均可用）。
        if table_id not in self._lineage_intercepted:
            equivalents = []
            try:
                from src.tools.data_analysis.analysis_artifacts import (
                    find_equivalent_artifacts,
                )

                equivalents = find_equivalent_artifacts(
                    self.tenant_id or "",
                    self.session_id or "",
                    table_id,
                    # 可复用集合含来源表可用性 DB 回查：async 路径沿用 to_thread 模式
                    entries=await asyncio.to_thread(self._reusable_artifacts),
                )
            except Exception as e:
                logger.warning(f"[AnalysisAgent] lineage 拦截查询失败（放行）: {e}")
            if equivalents:
                self._lineage_intercepted.add(table_id)
                logger.info(
                    f"[AnalysisAgent] load_table 拦截: 表 {table_id} 已有 "
                    f"{len(equivalents)} 个等价产物，引导复用"
                )
                return {
                    "success": False,
                    "intercepted": True,
                    "error": (
                        f"表 {table_id} 的等价加工产物已存在，禁止重新加载原始表再做一遍"
                        "相同的加载、清洗和合并。请直接 load_output 复用以下产物；"
                        "若这些产物确实不满足需求，再次调用 load_table(本表) 将放行。"
                    ),
                    "equivalent_outputs": [
                        {
                            "output_var": e["var"],
                            "description": e.get("description", ""),
                            "rows": e.get("rows", 0),
                        }
                        for e in equivalents[:10]
                    ],
                }

        try:
            # 加载到 DataAnalyzer
            loaded_id = await self.analyzer.load_table(table_meta)

            self._loaded_table_ids.add(table_id)
            self._var_sources[table_id] = {table_id}
            self.tables_metadata.append(table_meta)

            # 获取实际行数
            df = self.analyzer._tables.get(loaded_id)
            row_count = len(df) if df is not None else 0

            columns = table_meta.get("columns", [])
            columns_info = []
            for col in columns:
                ci = {
                    "name": col.get("name", ""),
                    "data_type": col.get("data_type", "text"),
                    "description": col.get("description", ""),
                }
                columns_info.append(ci)

            return {
                "success": True,
                "table_id": loaded_id,
                "table_name": table_meta.get("table_name", ""),
                "description": table_meta.get("description", ""),
                "row_count": row_count,
                "column_count": len(columns_info),
                "columns": columns_info,
                "hint": f"表已加载，可使用 table_id '{loaded_id}' 作为 source 参数进行分析",
            }

        except Exception as e:
            logger.error(f"load_table failed: {e}")
            return {"success": False, "error": str(e)}

    def _handle_load_output(self, output_var: str) -> Dict[str, Any]:
        """加载本会话此前 analyze_data 的中间产物（读取登记绑定的唯一 CSV）。

        复用资格按当前名单重查：本会话已登记 + 每张来源表当前均可用。未登记
        变量即使租户 temp 目录恰好存在同名 CSV 也不可读（猜到变量名不能读未
        登记产物）；旧条目缺完整来源集合不复用，提示重新分析；名单修改后此处
        按新规则执行，已在内存的产物不撤回。
        """
        from src.tools.data_analysis.analysis_artifacts import (
            artifact_csv_path,
            is_valid_var_name,
        )

        if not output_var:
            return {"success": False, "error": "output_var 不能为空"}
        if not is_valid_var_name(output_var):
            return {"success": False, "error": f"非法的产物变量名: {output_var}"}
        if not self.tenant_id:
            return {"success": False, "error": "缺少租户上下文，无法复用历史产物"}

        # 复用资格校验（含来源表批量可用性检查）；范围查询失败显式报错，
        # 不得当成「无可复用产物」静默放行或静默拒绝
        try:
            reusable = {
                e["var"]: e
                for e in filter_reusable_artifact_entries(
                    self._session_artifacts(),
                    self.tenant_id,
                    self.subagent_id,
                    self.agent_id,
                )
            }
        except Exception as e:
            logger.error(f"[AnalysisAgent] load_output 来源范围校验失败: {e}")
            return {"success": False, "error": f"产物来源范围校验失败，请稍后重试: {e}"}

        entry = reusable.get(output_var)
        if entry is None:
            available = [v for v in reusable if v != output_var]
            hint = f"，当前可复用: {available}" if available else ""
            return {
                "success": False,
                "error": (
                    f"产物 {output_var} 不可复用（未在本会话登记，或来源数据表当前"
                    f"不可用/来源记录不完整；旧产物请重新分析生成）{hint}。"
                    "若清单中无所需数据，请 load_table 原始表"
                ),
            }

        csv_path = artifact_csv_path(self.tenant_id, entry.get("csv_file"))
        # 路径穿越防线：解析后必须仍位于本租户 temp 目录内
        from src.core.storage import get_tenant_storage_dir

        temp_dir = os.path.realpath(get_tenant_storage_dir(self.tenant_id, "temp"))
        if not csv_path or not os.path.realpath(csv_path).startswith(temp_dir + os.sep):
            return {"success": False, "error": f"非法的产物路径: {output_var}"}
        if not os.path.exists(csv_path):
            available = [v for v in reusable if v != output_var]
            hint = f"，当前可用: {available}" if available else ""
            return {
                "success": False,
                "error": f"产物 {output_var} 不存在{hint}。若清单中无所需数据，请 load_table 原始表",
            }

        try:
            import pandas as pd

            df = pd.read_csv(csv_path)
        except Exception as e:
            return {"success": False, "error": f"产物文件读取失败: {e}"}

        # 装入分析环境：后续 merge/aggregate 等可直接以 var 引用；来源集合随
        # 登记恢复，供后续以其为输入的产物继续传播
        self.analyzer._variables[output_var] = df
        src = entry.get("source_table_ids")
        self._var_sources[output_var] = (
            {str(t) for t in src} if isinstance(src, list) and src else None
        )
        columns = list(df.columns)
        rows = len(df)
        logger.info(
            f"[AnalysisAgent] load_output 复用产物 {output_var}: {rows} 行 × {len(columns)} 列"
        )
        return {
            "success": True,
            "output_var": output_var,
            "rows": rows,
            "columns": columns,
            "preview_columns": columns,
            "preview_rows": min(3, rows),
            "preview": self._df_to_native_rows(df.head(3)),
            "hint": f"已复用本会话历史产物，后续可直接用 output_var '{output_var}' 作为 source 参数",
        }

    @staticmethod
    def _extract_source_refs(method_name: str, params: Dict) -> List[str]:
        """提取本步工具直接引用的数据源（原始表 table_id / 上游产物 var / 文件路径）。

        与 DataAnalyzer._resolve_source 的三种引用方式对应；按方法取参：
        merge 为 left_ref/right_ref，append 为 sources 列表，其余为 source 单引用。
        """
        refs: List[str] = []
        if method_name == "merge":
            refs = [params.get("left_ref"), params.get("right_ref")]
        elif method_name == "append":
            sources = params.get("sources") or []
            refs = list(sources) if isinstance(sources, list) else []
        else:
            refs = [params.get("source")]
        return [str(r) for r in refs if r]

    def _register_artifact(
        self, output_var: str, method_name: str, description: str, rows: int, columns: List[str],
        upstreams: Optional[List[str]] = None,
        source_table_ids: Optional[List[str]] = None,
    ) -> None:
        """中间产物写入会话注册表（容错：失败不影响分析）。

        source_table_ids 为完整原始表 doc_id 集合（None=来源不全/未知），是跨调用
        复用资格的判定依据；upstreams 仅作 lineage 展示，职责独立。
        """
        try:
            from src.tools.data_analysis.analysis_artifacts import record_artifact

            if record_artifact(
                self.tenant_id or "",
                self.session_id or "",
                output_var,
                csv_file=os.path.basename(self.analyzer._artifact_paths.get(output_var, "")),
                description=description,
                method=method_name,
                rows=rows,
                columns=columns,
                upstreams=upstreams,
                source_table_ids=source_table_ids,
            ):
                self._new_artifacts.append({
                    "output_var": output_var,
                    "description": description,
                    "rows": rows,
                })
        except Exception as e:
            logger.warning(f"[AnalysisAgent] 产物注册失败（不影响分析）: {e}")

    def _session_artifacts(self) -> List[Dict]:
        """本会话已注册的历史产物条目（最近的在前）。"""
        try:
            from src.tools.data_analysis.analysis_artifacts import load_artifacts

            return load_artifacts(self.tenant_id or "", self.session_id or "")
        except Exception:
            return []

    @staticmethod
    def _slim_describe_result(result: Dict[str, Any]) -> Dict[str, Any]:
        """瘦身 describe 结果：每列 unique_values/top_values 截断到 DESCRIBE_VALUE_CAP 条。

        实测主分析 27 次 describe ≈150k 新发 prompt token（新发最大单一来源）；
        标量统计（dtype/non_null/unique_count/numeric_summary）全保留，模型仍知道
        完整基数，只砍取值清单体积。describe() 每次新建 dict，就地截断无副作用。
        """
        truncated = False
        for stat in result.get("columns_summary") or []:
            if not isinstance(stat, dict):
                continue
            for key in ("unique_values", "top_values"):
                values = stat.get(key)
                if isinstance(values, list) and len(values) > DESCRIBE_VALUE_CAP:
                    stat[key] = values[:DESCRIBE_VALUE_CAP]
                    truncated = True
        if truncated:
            result["values_hint"] = (
                f"每列取值/高频值最多展示 {DESCRIBE_VALUE_CAP} 条（unique_count 为完整基数）；"
                "需要完整取值清单或精确计数请用 query/aggregate 取数"
            )
        return result

    def _handle_describe(self, params: Dict, iteration: int) -> Dict[str, Any]:
        """处理 describe 工具：返回数据概览统计（memo 缓存 + 取值截断，追加改进 A）。

        缓存键只含参数；失效策略见 _execute_tool——任何非 describe 工具成功执行后
        整体清空，保证不返回过期结构。
        """
        try:
            cache_key = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
            cached = self._describe_cache.get(cache_key)
            if cached is not None:
                return dict(cached, cached=True)

            method = getattr(self.analyzer, "describe", None)
            if method is None:
                return {"success": False, "error": "方法不存在: describe"}

            result = self._slim_describe_result(method(**params))

            # 记录步骤
            step = {
                "step": len(self._steps) + 1,
                "method": "describe",
                "output_var": f"describe_{iteration}",
                "description": f"数据概览: {result.get('row_count', '?')} 行, {result.get('column_count', '?')} 列",
            }
            self._steps.append(step)

            payload = {"success": True, **result}
            self._describe_cache[cache_key] = payload
            return dict(payload)

        except Exception as e:
            logger.error(f"describe failed: {e}")
            return {"success": False, "error": str(e)}

    # ==================== DB 查询辅助方法 ====================

    def _fetch_documents_metadata(self, doc_ids: List[int]) -> Dict[int, Dict]:
        """批量查询 documents 表获取 metadata（同 search 可见范围：租户/共享 + 名单）。

        检索结果已按同一范围过滤，此处按 ID 回查仍叠加范围与名单条件兜底，
        防上游口径漂移导致受限表 schema 经批量回查泄漏。
        """
        from src.db.database import get_db_connection

        if not doc_ids or not self.tenant_id:
            # 无租户上下文不回查（与列表口径一致，不放大为全局视图）
            return {}

        doc_ids = list({int(d) for d in doc_ids})
        tenant_sql, tenant_params = _build_tenant_scope_params(self.tenant_id, self.subagent_id)
        # 回查对象为检索命中的数据表行（source_type 已在范围分支收窄），
        # 名单 cast 只作用于这些行
        scope_sql, scope_params = _build_agent_scope_condition(self.agent_id, self.tenant_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            placeholders = ",".join(["%s"] * len(doc_ids))
            cursor.execute(
                f"""
                SELECT id, title, summary, metadata
                FROM documents
                WHERE id IN ({placeholders})
                  AND (source_type = 'data-analysis-metadata'{tenant_sql})
                  AND {scope_sql}
                """,
                (*doc_ids, *tenant_params, *scope_params),
            )
            result = {}
            for row in cursor.fetchall():
                r = dict(row)
                meta = r.get("metadata", {})
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except (json.JSONDecodeError, TypeError):
                        meta = {}
                result[r["id"]] = {
                    "title": r.get("title", ""),
                    "summary": r.get("summary", ""),
                    "metadata": meta,
                }
            return result

    def _query_data_tables_list(self, keyword: str = "") -> List[Dict]:
        """查询所有数据表列表（委托模块级 query_data_tables_list，供 SmartDataAnalysisTool 复用）。"""
        return query_data_tables_list(
            keyword, self.tenant_id, self.subagent_id, self._loaded_table_ids,
            self.agent_id,
        )

    def _fetch_single_table_metadata(self, table_id: str) -> Optional[Dict]:
        """查询单个表的完整 metadata（本租户 + 已启用共享来源租户 + 本租户内被移动的表 + 名单）。"""
        return fetch_table_metadata_from_db(
            table_id, self.tenant_id, self.subagent_id, self.agent_id
        )

    @staticmethod
    def _df_to_native_rows(df) -> List[List]:
        """将 DataFrame 行转为 Python 原生类型列表。"""
        from src.tools.data_analysis.data_analyzer import DataAnalyzer
        rows = []
        for _, row in df.iterrows():
            rows.append([DataAnalyzer._to_native(v) for v in row])
        return rows
