"""
SmartDataAnalysisTool 单元测试（mock AnalysisAgent）

测试内容：
1. 工具定义（name, description, schema）
2. 参数校验（requirement 必填，tables_metadata 可选——不传时由 AnalysisAgent 自动检索匹配表）
3. execute 完整流程（mock AnalysisAgent）
4. 动态 display_name
"""

import asyncio
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from src.tools.data_analysis.smart_analysis_tool import SmartDataAnalysisTool, AnalyzeDataInput


class TestSmartDataAnalysisToolDefinition:
    """工具定义测试"""

    def test_tool_description(self):
        tool = SmartDataAnalysisTool()
        assert "分析" in tool.description
        assert len(tool.description) > 20

    def test_tool_display_name(self):
        tool = SmartDataAnalysisTool()
        assert tool.display_name == "智能数据分析"

    def test_tool_category(self):
        tool = SmartDataAnalysisTool()
        assert tool.category == "data_analysis"

    def test_tool_definition_schema(self):
        tool = SmartDataAnalysisTool()
        defn = tool.to_tool_definition()
        assert defn["name"] == "analyze_data"
        assert "input_schema" in defn
        schema = defn["input_schema"]
        assert schema["type"] == "object"
        required = schema.get("required", [])
        assert "requirement" in required
        # tables_metadata 是可选的：不传时由 AnalysisAgent 自动检索匹配数据表
        assert "tables_metadata" not in required

    def test_input_model_fields(self):
        fields = AnalyzeDataInput.model_fields
        assert "requirement" in fields
        assert "tables_metadata" in fields
        assert "session_id" in fields
        # session_id 是可选的
        assert fields["session_id"].is_required() is False


class TestParameterValidation:
    """参数校验测试"""

    def test_valid_params(self):
        tool = SmartDataAnalysisTool()
        assert tool.validate_parameters(
            requirement="按区域统计销售额",
            tables_metadata=[{"table_id": "t1"}],
        )

    def test_missing_requirement(self):
        tool = SmartDataAnalysisTool()
        missing = tool.get_missing_parameters(tables_metadata=[{"table_id": "t1"}])
        assert "requirement" in missing

    def test_missing_tables_metadata(self):
        # tables_metadata 可选，缺它不算缺失参数（AnalysisAgent 会自动搜索匹配表）
        tool = SmartDataAnalysisTool()
        missing = tool.get_missing_parameters(requirement="test")
        assert "tables_metadata" not in missing

    def test_missing_all_required(self):
        tool = SmartDataAnalysisTool()
        missing = tool.get_missing_parameters()
        assert "requirement" in missing
        assert "tables_metadata" not in missing

    def test_optional_session_id(self):
        tool = SmartDataAnalysisTool()
        valid = tool.validate_parameters(
            requirement="test",
            tables_metadata=[{"table_id": "t1"}],
            session_id="sess_123",
        )
        assert valid


class TestDisplayName:
    """动态 display_name 测试"""

    def test_static_display_name(self):
        tool = SmartDataAnalysisTool()
        assert tool.get_display_name() == "智能数据分析"

    def test_dynamic_display_name(self):
        tool = SmartDataAnalysisTool()
        name = tool.get_display_name({"requirement": "按区域统计销售额"})
        assert "按区域统计销售额" in name

    def test_dynamic_display_name_long(self):
        tool = SmartDataAnalysisTool()
        long_req = "这是一个非常长的分析需求描述超过二十个字符会被截断"
        name = tool.get_display_name({"requirement": long_req})
        assert "..." in name
        assert len(name) < len(long_req) + 20


class TestExecuteWithMock:
    """execute 方法测试（mock AnalysisAgent 和 DataAnalyzer）

    execute 内部使用延迟导入（from ... import），需要在实际模块路径上 patch。
    """

    @pytest.mark.asyncio
    async def test_execute_success(self):
        """mock AnalysisAgent 返回成功结果；显式入参无论是否带 source 一律回查，
        物理加载使用服务端记录的 source（调用方 source 不参与）"""
        mock_result = {
            "success": True,
            "conclusion": "分析完成：共统计5个区域",
            "artifacts": [
                {"id": "t1", "type": "table", "title": "区域汇总", "preview": [["区域", "金额"], ["华东", 100]]},
            ],
            "analysis_meta": {
                "iterations": 3,
                "duration_ms": 150,
                "tokens_used": 150,
                "tables_used": ["t1"],
                "trace_id": "analysis_test",
            },
        }
        server_meta = {
            "table_id": "t1", "table_name": "销售",
            "columns": [{"name": "region", "data_type": "text"}],
            "source": {"type": "excel", "file_path": "/srv/real.xlsx"},
        }

        mock_gateway = MagicMock()

        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch, \
             patch("src.core.master_agent") as mock_master:
            mock_fetch.return_value = server_meta
            mock_master.llm_gateway = mock_gateway
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock()
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=mock_result)

            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="按区域统计销售额",
                tables_metadata=[{"table_id": "t1", "source": {"type": "excel", "file_path": "test.xlsx"}}],
                session_id="test_session",
            )

            assert result["success"] is True
            assert result["conclusion"] == "分析完成：共统计5个区域"
            assert len(result["artifacts"]) == 1
            assert result["analysis_meta"]["iterations"] == 3
            # 传入的 source 是伪造面：加载与传给 Agent 的都是服务端记录
            mock_analyzer_instance.load_table.assert_awaited_once_with(server_meta)

    @pytest.mark.asyncio
    async def test_execute_load_table_failure(self):
        """加载数据表失败时返回错误"""
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch:
            mock_fetch.return_value = {"table_id": "t1", "source": {"type": "excel", "file_path": "test.xlsx"}}
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock(side_effect=Exception("文件不存在"))

            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="分析数据",
                tables_metadata=[{"table_id": "t1"}],
            )

            assert result["success"] is False
            assert "加载数据表失败" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_enriches_simplified_metadata(self):
        """主智能体传入简化结构（无 source）时回查 DB 补全，用完整 metadata 预加载并传给 AnalysisAgent"""
        mock_result = {
            "success": True,
            "conclusion": "分析完成",
            "artifacts": [],
            "analysis_meta": {"iterations": 1, "duration_ms": 10, "tokens_used": 10, "tables_used": ["9078"], "trace_id": "t"},
        }
        full_meta = {
            "table_id": "9078",
            "table_name": "ecommerce_clothing_orders",
            "description": "电商衣服订单数据",
            "columns": [{"name": "订单编号", "data_type": "text", "description": "订单唯一编号"}],
            "source": {"type": "excel", "file_path": "/app/storage/tenants/t1/data_sources/a.xlsx"},
        }

        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch, \
             patch("src.core.master_agent") as mock_master:
            mock_fetch.return_value = full_meta
            mock_master.llm = MagicMock()
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock()
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=mock_result)

            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="各渠道销售情况",
                tables_metadata=[{"table_id": "9078", "table_name": "ecommerce_clothing_orders", "description": "电商衣服订单数据"}],
            )

            assert result["success"] is True
            # 回查用的是 table_id + 租户上下文 + 可信身份（无上下文 → None 不映射 main）
            mock_fetch.assert_called_once_with("9078", None, None, None)
            # 预加载用的是补全后的完整 metadata（带 source）
            mock_analyzer_instance.load_table.assert_awaited_once_with(full_meta)
            # AnalysisAgent 收到的也是完整 metadata
            assert MockAgent.call_args.kwargs["tables_metadata"] == [full_meta]

    @pytest.mark.asyncio
    async def test_execute_enrich_miss_fails_explicitly(self):
        """回查未命中（表不可见/无权/非数字 id）→ 整体显式失败：
        不回退传入 source、不用原始请求 metadata、不做部分加载、不启动分析"""
        mock_result = {
            "success": True,
            "conclusion": "分析完成",
            "artifacts": [],
            "analysis_meta": {"iterations": 1, "duration_ms": 10, "tokens_used": 10, "tables_used": [], "trace_id": "t"},
        }

        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch, \
             patch("src.core.master_agent") as mock_master:
            mock_fetch.return_value = None
            mock_master.llm = MagicMock()
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock()
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=mock_result)

            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="分析数据",
                tables_metadata=[{
                    "table_id": "not_a_number",
                    "source": {"type": "excel", "file_path": "fake.xlsx"},
                }],
            )

            # 回查未命中 → 显式失败（错误不区分不存在/无权），原始请求 metadata 弃用
            assert result["success"] is False
            assert "不可用" in result["error"]
            mock_analyzer_instance.load_table.assert_not_awaited()
            MockAgent.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_agent_failure(self):
        """AnalysisAgent 运行失败时返回错误"""
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch, \
             patch("src.core.master_agent") as mock_master:
            mock_fetch.return_value = {"table_id": "t1", "source": {"type": "excel", "file_path": "test.xlsx"}}
            mock_master.llm_gateway = MagicMock()
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock()
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(side_effect=Exception("LLM 调用超时"))

            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="分析数据",
                tables_metadata=[{"table_id": "t1"}],
            )

            assert result["success"] is False
            assert "分析执行失败" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_returns_charts(self):
        """验证返回结果包含图表信息"""
        mock_result = {
            "success": True,
            "conclusion": "趋势分析完成",
            "artifacts": [
                {
                    "id": "c1",
                    "type": "chart",
                    "chart_type": "line",
                    "title": "月度趋势",
                    "file_path": "storage/tenants/_anonymous/report/trend.png",
                }
            ],
            "analysis_meta": {
                "iterations": 2,
                "duration_ms": 300,
                "tokens_used": 300,
                "tables_used": [],
                "trace_id": "analysis_test",
            },
        }

        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch, \
             patch("src.core.master_agent") as mock_master:
            mock_fetch.return_value = {"table_id": "t1", "source": {"type": "excel", "file_path": "test.xlsx"}}
            mock_master.llm_gateway = MagicMock()
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock()
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=mock_result)

            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="月度趋势分析",
                tables_metadata=[{"table_id": "t1"}],
            )

            assert result["success"] is True
            assert len(result["artifacts"]) == 1
            assert result["artifacts"][0]["chart_type"] == "line"
            assert result["analysis_meta"]["iterations"] == 2

    @pytest.mark.asyncio
    async def test_execute_empty_conclusion_degrades_to_failure(self):
        """AnalysisAgent 返回 success=True 但结论为空 → 降级为失败，
        防止主智能体误判为"知识库中无相关数据"（2026-08 生产事故归因）。"""
        mock_result = {
            "success": True,
            "conclusion": "",
            "artifacts": [],
            "analysis_meta": {"iterations": 5, "duration_ms": 60000, "tokens_used": 15000, "tables_used": [], "trace_id": "analysis_empty"},
        }

        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch, \
             patch("src.core.master_agent") as mock_master:
            mock_fetch.return_value = {"table_id": "t1", "source": {"type": "excel", "file_path": "test.xlsx"}}
            mock_master.llm_gateway = MagicMock()
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock()
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=mock_result)

            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="各品类同比分析",
                tables_metadata=[{"table_id": "t1"}],
            )

            assert result["success"] is False
            assert "未产出有效结论" in result["error"]
            assert result["conclusion"] == result["error"]


class TestGatewaySelection:
    """LLM 网关选择测试：上下文有 llm_gateway 时优先用（跟随数字员工模型配置），
    无上下文时回退 master_agent.llm（后台调度/渠道侧行为不变）。"""

    @pytest.mark.asyncio
    async def test_execute_uses_context_llm_gateway(self):
        from src.tools.context import ToolExecutionContext, tool_execution_scope

        ctx_gateway = MagicMock()
        mock_result = {"success": True, "conclusion": "ok", "artifacts": [],
                       "analysis_meta": {"iterations": 1, "duration_ms": 10, "tokens_used": 10, "tables_used": [], "trace_id": "t"}}

        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.core.master_agent") as mock_master:
            mock_master.llm = MagicMock()
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock()
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=mock_result)

            tool = SmartDataAnalysisTool()
            with tool_execution_scope(ToolExecutionContext(tenant_id="t1", llm_gateway=ctx_gateway)):
                result = await tool.execute(requirement="按区域统计销售额")

            assert result["success"] is True
            assert MockAgent.call_args.kwargs["llm_gateway"] is ctx_gateway

    @pytest.mark.asyncio
    async def test_execute_falls_back_to_master_llm_without_context(self):
        mock_result = {"success": True, "conclusion": "ok", "artifacts": [],
                       "analysis_meta": {"iterations": 1, "duration_ms": 10, "tokens_used": 10, "tables_used": [], "trace_id": "t"}}

        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.core.master_agent") as mock_master:
            master_llm = MagicMock()
            mock_master.llm = master_llm
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock()
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=mock_result)

            tool = SmartDataAnalysisTool()
            result = await tool.execute(requirement="按区域统计销售额")

            assert result["success"] is True
            assert MockAgent.call_args.kwargs["llm_gateway"] is master_llm


class TestBatchSelectAndLoad:
    """开局服务端批量选表加载（2026-10 改进1）：调用方未传表时由服务端选表并
    批量预加载，模型开局即面对「已加载的表 + 完整 schema」，禁止内层模型逐个
    search/load_table。"""

    def _mock_result(self):
        return {"success": True, "conclusion": "ok", "artifacts": [],
                "analysis_meta": {"iterations": 1, "duration_ms": 10, "tokens_used": 10,
                                  "tables_used": [], "trace_id": "t"}}

    @pytest.mark.asyncio
    async def test_execute_without_metadata_batch_loads_all_tables(self):
        """表数 <= 阈值：全量选中，回查完整 metadata 批量加载后传入 AnalysisAgent"""
        summaries = [
            {"table_id": "11", "table_name": "销售", "description": "d1", "columns_count": 1, "loaded": False},
            {"table_id": "12", "table_name": "库存", "description": "d2", "columns_count": 1, "loaded": False},
        ]
        meta_11 = {"table_id": "11", "table_name": "销售", "description": "d1",
                   "columns": [{"name": "region", "data_type": "text", "description": "区域"}],
                   "source": {"type": "database", "connector_id": "c1", "db_table_name": "sales"}}
        meta_12 = {"table_id": "12", "table_name": "库存", "description": "d2",
                   "columns": [{"name": "sku", "data_type": "text", "description": "编码"}],
                   "source": {"type": "excel", "file_path": "inv.xlsx"}}

        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.query_data_tables_list") as mock_query, \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch, \
             patch("src.core.master_agent"):
            mock_query.return_value = summaries
            mock_fetch.side_effect = lambda tid, tenant, sub, aid: {"11": meta_11, "12": meta_12}[tid]
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock(return_value="ok")
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            from src.tools.context import ToolExecutionContext, tool_execution_scope
            with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
                result = await tool.execute(requirement="各区域销售情况")

            assert result["success"] is True
            # 完整 metadata 列表经 tables_metadata 参数传入 AnalysisAgent
            assert MockAgent.call_args.kwargs["tables_metadata"] == [meta_11, meta_12]
            assert mock_analyzer_instance.load_table.await_count == 2
            # 列表查询先按名单过滤（身份 main 来自可信上下文），过滤后计数决定阈值
            mock_query.assert_called_once_with("", "t1", None, None, "main")

    @pytest.mark.asyncio
    async def test_execute_with_metadata_skips_batch_select(self):
        """调用方已传表：走既有预加载路径，不再触发服务端批量选表"""
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.query_data_tables_list") as mock_query, \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch, \
             patch("src.core.master_agent"):
            mock_fetch.return_value = {"table_id": "t1", "source": {"type": "excel", "file_path": "a.xlsx"}}
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock(return_value="ok")
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="按区域统计销售额",
                tables_metadata=[{"table_id": "t1", "source": {"type": "excel", "file_path": "a.xlsx"}}],
            )

            assert result["success"] is True
            mock_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_over_threshold_uses_semantic_select(self, monkeypatch):
        """表数 > 阈值：按 requirement 语义检索 top N 选中，仅加载选中的表"""
        from src.tools.data_analysis import smart_analysis_tool
        monkeypatch.setattr(smart_analysis_tool, "FULL_LOAD_THRESHOLD", 1)

        summaries = [
            {"table_id": str(i), "table_name": f"表{i}", "description": "", "columns_count": 1, "loaded": False}
            for i in (11, 12, 13)
        ]
        meta_12 = {"table_id": "12", "table_name": "表12",
                   "columns": [{"name": "a", "data_type": "text"}],
                   "source": {"type": "excel", "file_path": "a.xlsx"}}
        meta_13 = {"table_id": "13", "table_name": "表13",
                   "columns": [{"name": "b", "data_type": "text"}],
                   "source": {"type": "excel", "file_path": "b.xlsx"}}

        fake_retriever = MagicMock()
        fake_retriever.retrieve = AsyncMock(return_value=[
            {"doc_id": 12, "score": 0.9}, {"doc_id": 13, "score": 0.8},
        ])

        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.query_data_tables_list") as mock_query, \
             patch("src.tools.data_analysis.analysis_agent.create_data_tables_retriever", return_value=fake_retriever), \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch, \
             patch("src.core.master_agent"):
            mock_query.return_value = summaries
            mock_fetch.side_effect = lambda tid, tenant, sub, aid: {"12": meta_12, "13": meta_13}[tid]
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock(return_value="ok")
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            from src.tools.context import ToolExecutionContext, tool_execution_scope
            with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
                result = await tool.execute(requirement="各渠道销售对比")

            assert result["success"] is True
            # 按 requirement 语义检索，只加载选中的 2 张
            fake_retriever.retrieve.assert_awaited_once()
            kwargs = fake_retriever.retrieve.await_args.kwargs
            assert kwargs["query"] == "各渠道销售对比"
            assert kwargs["top_k"] == smart_analysis_tool.SEMANTIC_SELECT_TOP_K
            assert kwargs["source_type"] == "data-analysis-metadata"
            # 可信上下文存在且 subagent_id 为空 → 名单身份映射 main
            assert kwargs["agent_id"] == "main"
            assert MockAgent.call_args.kwargs["tables_metadata"] == [meta_12, meta_13]

    @pytest.mark.asyncio
    async def test_batch_load_single_failure_skips_table(self):
        """个别表加载失败（孤儿表）→ 跳过并继续，不阻断整体分析"""
        summaries = [
            {"table_id": "11", "table_name": "损坏表", "description": "", "columns_count": 1, "loaded": False},
            {"table_id": "12", "table_name": "正常表", "description": "", "columns_count": 1, "loaded": False},
        ]
        meta_11 = {"table_id": "11", "table_name": "损坏表",
                   "columns": [{"name": "a", "data_type": "text"}],
                   "source": {"type": "excel", "file_path": "missing.xlsx"}}
        meta_12 = {"table_id": "12", "table_name": "正常表",
                   "columns": [{"name": "b", "data_type": "text"}],
                   "source": {"type": "excel", "file_path": "ok.xlsx"}}

        async def _load(meta):
            if meta["table_id"] == "11":
                raise ValueError("源数据不存在")
            return meta["table_id"]

        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.query_data_tables_list") as mock_query, \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch, \
             patch("src.core.master_agent"):
            mock_query.return_value = summaries
            mock_fetch.side_effect = lambda tid, tenant, sub, aid: {"11": meta_11, "12": meta_12}[tid]
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock(side_effect=_load)
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            from src.tools.context import ToolExecutionContext, tool_execution_scope
            with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
                result = await tool.execute(requirement="销售分析")

            assert result["success"] is True
            assert MockAgent.call_args.kwargs["tables_metadata"] == [meta_12]

    @pytest.mark.asyncio
    async def test_batch_select_query_failure_degrades_to_search_mode(self):
        """表列表查询失败 → 退回不预加载（AnalysisAgent 收到空表自行 search），不报错"""
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.query_data_tables_list") as mock_query, \
             patch("src.core.master_agent"):
            mock_query.side_effect = Exception("DB down")
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock()
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            from src.tools.context import ToolExecutionContext, tool_execution_scope
            with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
                result = await tool.execute(requirement="销售分析")

            assert result["success"] is True
            assert MockAgent.call_args.kwargs["tables_metadata"] == []

    @pytest.mark.asyncio
    async def test_batch_select_skipped_without_tenant(self):
        """无租户上下文：不批量选表（避免列表查询放大为跨租户全局视图）"""
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.query_data_tables_list") as mock_query, \
             patch("src.core.master_agent"):
            mock_analyzer_instance = MockAnalyzer.return_value
            mock_analyzer_instance.load_table = AsyncMock()
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            result = await tool.execute(requirement="销售分析")

            assert result["success"] is True
            mock_query.assert_not_called()
            assert MockAgent.call_args.kwargs["tables_metadata"] == []

    @pytest.mark.asyncio
    async def test_load_one_table_excel_runs_in_thread(self, tmp_path, monkeypatch):
        """Excel 源经 asyncio.to_thread 线程加载，数据真正进入分析环境"""
        from src.core import storage as storage_mod
        monkeypatch.setattr(storage_mod, "_TENANTS_ROOT", str(tmp_path / "tenants"))

        from src.tools.data_analysis.data_analyzer import DataAnalyzer
        from src.tools.data_analysis.smart_analysis_tool import _load_one_table

        csv_path = tmp_path / "inv.csv"
        csv_path.write_text("sku,qty\nA,1\nB,2\n", encoding="utf-8")
        analyzer = DataAnalyzer(session_id="s1")

        table_id = await _load_one_table(analyzer, {
            "table_id": "12",
            "table_name": "库存",
            "source": {"type": "excel", "file_path": str(csv_path)},
        })

        assert table_id == "12"
        assert len(analyzer._tables["12"]) == 2


# ============================================================
# 改进3（2026-10）：导出/整理类需求识别与会话产物短路
# ============================================================


class TestExportOnlyDetection:
    """导出意图识别：保守匹配，漏判走正常路径（行为不变），误判内层可兜底"""

    def test_export_only_positive(self):
        # 同会话实测出现过的导出任务表述（agent1 第5场 4 次导出调用原文变体）
        assert SmartDataAnalysisTool._is_export_only_requirement(
            "只做导出动作，不要重新分析原始数据，全部用现有汇总结果"
        )
        assert SmartDataAnalysisTool._is_export_only_requirement(
            "仅执行导出：用 pandas Excel 保存全部汇总表"
        )
        assert SmartDataAnalysisTool._is_export_only_requirement(
            "把结果整理成表格文件"
        )

    def test_negative_phrase_not_treated_as_analysis(self):
        """「不要重新分析」中的分析词已剔除，仍识别为导出（实测导出任务常见表述）"""
        assert SmartDataAnalysisTool._is_export_only_requirement(
            "不要做任何数据分析或计算。仅执行一个导出"
        )

    def test_analysis_requirement_not_export_only(self):
        # 主分析需求原文（含「输出分析总表」）绝不能被短路
        assert not SmartDataAnalysisTool._is_export_only_requirement(
            "请帮我整体汇总和分析六个国家4-6月份的狗粮出口销售和订单数据，"
            "重点分析第二季度整体销售业绩情况以及每个月的销售业绩对比，"
            "找到每个国家的优势渠道和劣势渠道，进行销售人员排名，"
            "并为我下个月的销售提供建议。请输出分析总表、透视图和汇报文件。"
        )

    def test_mixed_export_and_analysis_not_export_only(self):
        """「导出并分析趋势」混合意图不短路（分析需求占主导，走正常预加载路径）"""
        assert not SmartDataAnalysisTool._is_export_only_requirement(
            "导出数据并分析各渠道的趋势和排名"
        )

    def test_reuse_artifact_export_is_export_only(self):
        """「复用已有产物仅做格式整理后导出」应短路——这正是收益场景"""
        assert SmartDataAnalysisTool._is_export_only_requirement(
            "复用本会话已产出的汇总表（output_summary），仅做格式整理后导出为 Excel 文件"
        )

    def test_empty_requirement(self):
        assert not SmartDataAnalysisTool._is_export_only_requirement("")
        assert not SmartDataAnalysisTool._is_export_only_requirement(None)


class TestExportModeExecute:
    """导出短路执行路径：跳过 49 表批量预加载，export_only_mode 传入 AnalysisAgent"""

    @pytest.mark.asyncio
    async def test_export_only_with_artifacts_skips_preload(self):
        """业务意图：导出类调用不应重付 49 表预加载（同会话实测每次多耗 20-40 积分）"""
        mock_result = {
            "success": True, "conclusion": "导出完成", "artifacts": [],
            "analysis_meta": {"iterations": 2, "duration_ms": 10, "tokens_used": 10,
                              "tables_used": [], "trace_id": "t"},
        }
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer"), \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_artifacts.load_artifacts",
                   return_value=[{"var": "wide_existing", "source_table_ids": ["9078"]}]), \
             patch("src.tools.data_analysis.analysis_agent.filter_available_table_ids",
                   return_value={"9078"}), \
             patch.object(SmartDataAnalysisTool, "_batch_select_and_load",
                          new=AsyncMock()) as mock_batch:
            MockAgent.return_value.run = AsyncMock(return_value=mock_result)
            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="仅执行导出：把已有汇总表保存为 Excel 文件",
                session_id="session_s1",
            )
            assert result["success"] is True
            mock_batch.assert_not_awaited()  # 未触发批量预加载
            kwargs = MockAgent.call_args.kwargs
            assert kwargs["export_only_mode"] is True
            assert kwargs["tables_metadata"] == []  # 零预加载
            assert kwargs["session_id"] == "session_s1"  # 产物注册表作用域正确

    @pytest.mark.asyncio
    async def test_export_only_without_artifacts_keeps_preload(self):
        """无产物可复用时导出需求走正常预加载路径（fail-soft 兜底）"""
        mock_result = {
            "success": True, "conclusion": "完成", "artifacts": [],
            "analysis_meta": {"iterations": 1, "duration_ms": 10, "tokens_used": 10,
                              "tables_used": ["t1"], "trace_id": "t"},
        }
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer"), \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_artifacts.load_artifacts",
                   return_value=[]), \
             patch.object(SmartDataAnalysisTool, "_batch_select_and_load",
                          new=AsyncMock(return_value=[])) as mock_batch:
            MockAgent.return_value.run = AsyncMock(return_value=mock_result)
            tool = SmartDataAnalysisTool()
            await tool.execute(requirement="仅执行导出：保存为 Excel", session_id="session_s1")
            mock_batch.assert_awaited_once()
            assert MockAgent.call_args.kwargs["export_only_mode"] is False

    @pytest.mark.asyncio
    async def test_analysis_requirement_never_short_circuits(self):
        """业务意图：主分析需求即使带「输出表格」字样也必须走预加载——
        短路误伤主分析会直接丢分析能力，这是本优化的安全红线"""
        mock_result = {
            "success": True, "conclusion": "完成", "artifacts": [],
            "analysis_meta": {"iterations": 1, "duration_ms": 10, "tokens_used": 10,
                              "tables_used": ["t1"], "trace_id": "t"},
        }
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer"), \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_artifacts.load_artifacts",
                   return_value=[{"var": "wide_existing", "source_table_ids": ["9078"]}]), \
             patch("src.tools.data_analysis.analysis_agent.filter_available_table_ids",
                   return_value={"9078"}), \
             patch.object(SmartDataAnalysisTool, "_batch_select_and_load",
                          new=AsyncMock(return_value=[])) as mock_batch:
            MockAgent.return_value.run = AsyncMock(return_value=mock_result)
            tool = SmartDataAnalysisTool()
            await tool.execute(
                requirement="分析各渠道销售额并输出对比表格", session_id="session_s1",
            )
            mock_batch.assert_awaited_once()
            assert MockAgent.call_args.kwargs["export_only_mode"] is False


# ============================================================
# Phase 2（数据表按智能体配置）：可信身份解析与显式入参绕过封堵
# ============================================================


def _agent_scope_mocks(mock_result, fetch_meta):
    """构造 Phase 2 身份/绕过测试的公共 mock 上下文。"""
    return (
        patch("src.tools.data_analysis.data_analyzer.DataAnalyzer"),
        patch("src.tools.data_analysis.analysis_agent.AnalysisAgent"),
        patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db"),
        patch("src.core.master_agent"),
    )


class TestAgentScopeIdentity:
    """身份语义：可信上下文映射 main/子智能体；无上下文不映射 main 且附诊断提示；
    租户冲突以 ToolExecutionContext 为准。"""

    def _mock_result(self):
        return {"success": True, "conclusion": "ok", "artifacts": [],
                "analysis_meta": {"iterations": 1, "duration_ms": 10, "tokens_used": 10,
                                  "tables_used": [], "trace_id": "t"}}

    @pytest.mark.asyncio
    async def test_context_without_subagent_maps_main(self):
        """可信上下文存在且 subagent_id 为空 → 名单身份为 main（可信上下文内转换）"""
        summaries = [{"table_id": "11", "table_name": "销售", "description": "",
                      "columns_count": 1, "loaded": False}]
        meta = {"table_id": "11", "table_name": "销售",
                "columns": [{"name": "a", "data_type": "text"}],
                "source": {"type": "excel", "file_path": "a.xlsx"}}
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer"), \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.query_data_tables_list") as mock_query, \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch, \
             patch("src.core.master_agent"):
            mock_query.return_value = summaries
            mock_fetch.return_value = meta
            MockAgent.return_value.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            from src.tools.context import ToolExecutionContext, tool_execution_scope
            with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
                result = await tool.execute(requirement="销售分析")

            assert result["success"] is True
            # subagent_id（共享范围）仍为 None，agent_id（名单）映射 main
            mock_query.assert_called_once_with("", "t1", None, None, "main")
            assert "identity_notice" not in result

    @pytest.mark.asyncio
    async def test_context_with_subagent_uses_it_as_identity(self):
        """可信上下文 subagent_id 非空 → 名单身份为该子智能体"""
        summaries = [{"table_id": "11", "table_name": "销售", "description": "",
                      "columns_count": 1, "loaded": False}]
        meta = {"table_id": "11", "table_name": "销售",
                "columns": [{"name": "a", "data_type": "text"}],
                "source": {"type": "excel", "file_path": "a.xlsx"}}
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer"), \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.query_data_tables_list") as mock_query, \
             patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch, \
             patch("src.core.master_agent"):
            mock_query.return_value = summaries
            mock_fetch.return_value = meta
            MockAgent.return_value.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            from src.tools.context import ToolExecutionContext, tool_execution_scope
            with tool_execution_scope(ToolExecutionContext(tenant_id="t1", subagent_id="travel-quote")):
                result = await tool.execute(requirement="销售分析")

            assert result["success"] is True
            mock_query.assert_called_once_with("", "t1", "travel-quote", None, "travel-quote")

    @pytest.mark.asyncio
    async def test_no_context_keeps_unknown_identity_with_notice(self):
        """无 ToolExecutionContext ≠ main：有租户仅共享，返回信息附诊断提示"""
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.query_data_tables_list") as mock_query, \
             patch("src.saas.context.get_current_tenant_id", return_value="t1"), \
             patch("src.core.master_agent"):
            mock_query.return_value = []
            MockAgent.return_value.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            result = await tool.execute(requirement="销售分析")

            assert result["success"] is True
            # 身份未知（None，不映射 main）+ 诊断提示（不含数据内容）
            mock_query.assert_called_once_with("", "t1", None, None, None)
            assert result["identity_notice"] == "调用身份未确定，本次仅使用共享数据表"

    @pytest.mark.asyncio
    async def test_tenant_conflict_prefers_trusted_context(self):
        """进程级租户变量与 ToolExecutionContext 冲突时以可信上下文为准"""
        summaries = []
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer"), \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.tools.data_analysis.analysis_agent.query_data_tables_list") as mock_query, \
             patch("src.saas.context.get_current_tenant_id", return_value="t_saas_var"), \
             patch("src.core.master_agent"):
            mock_query.return_value = summaries
            MockAgent.return_value.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            from src.tools.context import ToolExecutionContext, tool_execution_scope
            with tool_execution_scope(ToolExecutionContext(tenant_id="t_ctx")):
                await tool.execute(requirement="销售分析")

            mock_query.assert_called_once_with("", "t_ctx", None, None, "main")


class TestExplicitBypassBlocked:
    """显式入参绕过封堵：source/ID 组合形态均不能绕过服务端回查"""

    def _mock_result(self):
        return {"success": True, "conclusion": "ok", "artifacts": [],
                "analysis_meta": {"iterations": 1, "duration_ms": 10, "tokens_used": 10,
                                  "tables_used": [], "trace_id": "t"}}

    @pytest.mark.asyncio
    async def test_source_without_table_id_rejected(self):
        """只有 source 没有 table_id/doc_id → 不作为选择依据，整体失败"""
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.core.master_agent"):
            MockAnalyzer.return_value.load_table = AsyncMock()
            MockAgent.return_value.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="分析",
                tables_metadata=[{"source": {"type": "excel", "file_path": "secret.xlsx"}}],
            )

            assert result["success"] is False
            assert "table_id" in result["error"]
            MockAnalyzer.return_value.load_table.assert_not_awaited()
            MockAgent.assert_not_called()

    @pytest.mark.asyncio
    async def test_other_tenant_table_id_rejected_without_source_fallback(self):
        """其他租户表 ID（回查 None）→ 失败且不回退调用方伪造 source"""
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer,              patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent,              patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch,              patch("src.core.master_agent"):
            mock_fetch.return_value = None
            MockAnalyzer.return_value.load_table = AsyncMock()
            MockAgent.return_value.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="分析",
                tables_metadata=[{
                    "table_id": "8888",
                    "source": {"type": "excel", "file_path": "C:/other_tenant/data.xlsx"},
                }],
            )

            assert result["success"] is False
            assert "不可用" in result["error"]
            # 伪造 source 未被读取（无加载、无分析启动），错误不区分不存在/无权
            MockAnalyzer.return_value.load_table.assert_not_awaited()
            MockAgent.assert_not_called()
            assert "无权" not in result["error"]

    @pytest.mark.asyncio
    async def test_fetch_error_fails_explicitly(self):
        """回查 DB 异常 → 显式失败（权限读取失败要暴露，不当成表不存在继续）"""
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer,              patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent,              patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch,              patch("src.core.master_agent"):
            mock_fetch.side_effect = Exception("DB down")
            MockAnalyzer.return_value.load_table = AsyncMock()
            MockAgent.return_value.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="分析",
                tables_metadata=[{"table_id": "11"}],
            )

            assert result["success"] is False
            MockAgent.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_availability_no_partial_load(self):
        """两张指定表一张不可用 → 整体失败，可用表也不做部分加载"""
        good = {"table_id": "11", "table_name": "可用表",
                "columns": [{"name": "a", "data_type": "text"}],
                "source": {"type": "excel", "file_path": "a.xlsx"}}
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer,              patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent,              patch("src.tools.data_analysis.analysis_agent.fetch_table_metadata_from_db") as mock_fetch,              patch("src.core.master_agent"):
            mock_fetch.side_effect = lambda tid, tenant, sub, aid: good if tid == "11" else None
            MockAnalyzer.return_value.load_table = AsyncMock()
            MockAgent.return_value.run = AsyncMock(return_value=self._mock_result())

            tool = SmartDataAnalysisTool()
            result = await tool.execute(
                requirement="分析",
                tables_metadata=[{"table_id": "11"}, {"table_id": "12"}],
            )

            assert result["success"] is False
            MockAnalyzer.return_value.load_table.assert_not_awaited()
