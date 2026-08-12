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

MAX_ITERATIONS = 50
DATA_PROCESSING_METHODS = {"query", "aggregate", "merge", "pivot", "calculate", "compare", "trend", "extract_hierarchy"}
SEARCH_METHODS = {"search_data_tables", "list_data_tables"}
# 上下文压缩阈值：当 messages 的 JSON 超过此字节数时，压缩早期的工具结果
CONTEXT_COMPRESS_THRESHOLD = 50000


class AnalysisAgent:
    """迷你 Agent 循环，LLM 通过 function calling 完成数据表检索、加载和分析。"""

    def __init__(
        self,
        llm_gateway,
        analyzer,
        analysis_id: str,
        tables_metadata: Optional[List[Dict]] = None,
        tenant_id: Optional[str] = None,
    ):
        self.llm = llm_gateway
        self.analyzer = analyzer
        self.analysis_id = analysis_id
        self.tables_metadata = tables_metadata or []
        self.tenant_id = tenant_id

        self._loaded_table_ids: set = set()
        self._search_count: int = 0
        self._retriever = None

        self._total_usage: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        # artifacts 是工具返回给主智能体的唯一产物索引（chart/table/data_file）
        self._artifacts: List[Dict] = []
        # _steps 仅用于 trace 持久化，不放进对外返回值
        self._steps: List[Dict] = []
        self._spans: List[SpanRecord] = []

        # 如果传入了预加载的表，标记为已加载
        for meta in self.tables_metadata:
            tid = str(meta.get("table_id", meta.get("doc_id", "")))
            if tid:
                self._loaded_table_ids.add(tid)

    async def run(self, requirement: str) -> Dict[str, Any]:
        """执行分析任务，返回结果字典。"""
        start_time = time.time()

        messages = [{"role": "user", "content": self._build_user_message(requirement)}]

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
                response = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=ANALYSIS_TOOLS,
                    tool_choice="auto",
                    system_prompt=ANALYSIS_SYSTEM_PROMPT,
                    temperature=0.1,
                    max_tokens=4000,
                    model="deepseek-v4-pro",
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

            content = response.get("content", "")
            tool_calls = response.get("tool_calls") or []

            # 无工具调用 → LLM 给出最终总结
            if not tool_calls:
                summary = content
                break

            # 将 assistant 消息（含 tool_calls）加入对话
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

            # 执行每个工具调用
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

            # 上下文压缩：当 messages 过大时，压缩早期的工具结果为摘要
            self._compress_messages(messages)
        else:
            # 达到 MAX_ITERATIONS
            summary = content or "分析完成（达到最大迭代次数）"
            logger.warning(f"AnalysisAgent reached MAX_ITERATIONS={MAX_ITERATIONS}")

        return self._build_result(success=True, summary=summary, iterations=iterations, start_time=start_time)

    async def _execute_tool(self, method_name: str, params: Dict, iteration: int) -> Dict[str, Any]:
        """执行单个工具调用，返回结果字典。"""
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
                os.makedirs(DataAnalyzer.CHART_OUTPUT_DIR, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_title = re.sub(r'[\\/:*?"<>|]', "_", params.get("title") or output_var)
                download_path = os.path.join(
                    DataAnalyzer.CHART_OUTPUT_DIR, f"{safe_title}_{timestamp}.xlsx"
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

    def _build_user_message(self, requirement: str) -> str:
        """构建初始用户消息。"""
        if self._loaded_table_ids:
            tables_info = self._build_tables_info()
            return f"## 用户需求\n{requirement}\n\n## 已加载的数据表\n{tables_info}"
        else:
            return (
                f"## 用户需求\n{requirement}\n\n"
                f"## 说明\n"
                f"目前没有预加载的数据表。请先分析用户需求，提取搜索关键词，"
                f"使用 search_data_tables 搜索相关数据表，"
                f"然后用 load_table 加载需要的表进行分析。"
            )

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
        """构建表 Metadata 文本描述。"""
        parts = []
        for meta in self.tables_metadata:
            table_id = meta.get("table_id", "unknown")
            table_name = meta.get("table_name", meta.get("name", "未命名"))
            description = meta.get("description", "")
            row_count = meta.get("row_count", "?")
            col_count = meta.get("column_count", "?")

            info = f"### 表: {table_name} (table_id: {table_id})\n"
            if description:
                info += f"说明: {description}\n"
            info += f"行数: {row_count}, 列数: {col_count}\n"

            columns = meta.get("columns", [])
            if columns:
                info += "字段:\n"
                for col in columns:
                    col_name = col.get("name", col.get("semantic_name", ""))
                    col_type = col.get("data_type", col.get("type", ""))
                    col_desc = col.get("description", "")
                    info += f"  - {col_name} ({col_type})"
                    if col_desc:
                        info += f": {col_desc}"
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
            from src.config.settings import get_embedding_api_key
            from src.knowledge.vector_db.vector_db import get_vector_db
            from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
            from src.knowledge.retriever.hybrid_retriever import HybridRetriever

            embedding_api_key = get_embedding_api_key()
            vector_db = get_vector_db(dimension=1024)
            embedding_client = TextEmbeddingV3Client(api_key=embedding_api_key)
            self._retriever = HybridRetriever(
                vector_db=vector_db,
                embedding_client=embedding_client,
                conn=None,
            )
        return self._retriever

    async def _handle_search_data_tables(self, query: str, top_k: int = 5) -> Dict[str, Any]:
        """语义搜索数据表。"""
        if not query:
            return {"success": False, "error": "查询不能为空", "results": [], "count": 0}

        try:
            retriever = self._get_retriever()
            results = await retriever.retrieve(
                query=query,
                top_k=top_k,
                tenant_id=self.tenant_id,
                source_type="data-analysis-metadata",
            )

            if not results:
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
        """加载指定表到分析环境。"""
        if not table_id:
            return {"success": False, "error": "table_id 不能为空"}

        if table_id in self._loaded_table_ids:
            for meta in self.tables_metadata:
                if str(meta.get("table_id", meta.get("doc_id", ""))) == table_id:
                    return {
                        "success": True,
                        "table_id": table_id,
                        "table_name": meta.get("table_name", ""),
                        "message": "表已加载",
                        "row_count": 0,
                        "column_count": len(meta.get("columns", [])),
                        "columns": meta.get("columns", []),
                    }
            return {"success": True, "table_id": table_id, "message": "表已加载"}

        try:
            # 从 documents 表获取完整 metadata
            table_meta = await asyncio.to_thread(self._fetch_single_table_metadata, table_id)

            if not table_meta:
                return {"success": False, "error": f"未找到数据表: {table_id}"}

            # 加载到 DataAnalyzer
            loaded_id = await self.analyzer.load_table(table_meta)

            self._loaded_table_ids.add(table_id)
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

    def _handle_describe(self, params: Dict, iteration: int) -> Dict[str, Any]:
        """处理 describe 工具：返回数据概览统计。"""
        try:
            method = getattr(self.analyzer, "describe", None)
            if method is None:
                return {"success": False, "error": "方法不存在: describe"}

            result = method(**params)

            # 记录步骤
            step = {
                "step": len(self._steps) + 1,
                "method": "describe",
                "output_var": f"describe_{iteration}",
                "description": f"数据概览: {result.get('row_count', '?')} 行, {result.get('column_count', '?')} 列",
            }
            self._steps.append(step)

            return {"success": True, **result}

        except Exception as e:
            logger.error(f"describe failed: {e}")
            return {"success": False, "error": str(e)}

    # ==================== DB 查询辅助方法 ====================

    def _fetch_documents_metadata(self, doc_ids: List[int]) -> Dict[int, Dict]:
        """批量查询 documents 表获取 metadata。"""
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            placeholders = ",".join(["%s"] * len(doc_ids))
            cursor.execute(
                f"""
                SELECT id, title, summary, metadata
                FROM documents
                WHERE id IN ({placeholders})
                """,
                doc_ids,
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
        """查询所有数据表列表。"""
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            if keyword:
                cursor.execute(
                    """
                    SELECT id, title, summary, metadata
                    FROM documents
                    WHERE source_type = 'data-analysis-metadata'
                      AND (title ILIKE %s OR summary ILIKE %s)
                    ORDER BY created_at DESC
                    """,
                    (f"%{keyword}%", f"%{keyword}%"),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, title, summary, metadata
                    FROM documents
                    WHERE source_type = 'data-analysis-metadata'
                    ORDER BY created_at DESC
                    """,
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
                table_id = str(r["id"])
                results.append({
                    "table_id": table_id,
                    "table_name": table_name,
                    "description": r.get("summary", ""),
                    "columns_count": len(meta.get("columns", [])),
                    "loaded": table_id in self._loaded_table_ids,
                })
            return results

    def _fetch_single_table_metadata(self, table_id: str) -> Optional[Dict]:
        """查询单个表的完整 metadata，构造 DataAnalyzer 所需格式。"""
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, title, summary, metadata
                FROM documents
                WHERE id = %s AND source_type = 'data-analysis-metadata'
                """,
                (int(table_id),),
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

            result = {
                "table_id": str(r["id"]),
                "table_name": meta.get("table_name", r.get("title", "").replace("[数据表] ", "")),
                "description": r.get("summary", ""),
                "columns": meta.get("columns", []),
            }

            # 构造 source 对象
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

            return result

    @staticmethod
    def _df_to_native_rows(df) -> List[List]:
        """将 DataFrame 行转为 Python 原生类型列表。"""
        from src.tools.data_analysis.data_analyzer import DataAnalyzer
        rows = []
        for _, row in df.iterrows():
            rows.append([DataAnalyzer._to_native(v) for v in row])
        return rows
