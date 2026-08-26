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

    def test_tool_name(self):
        tool = SmartDataAnalysisTool()
        assert tool.name == "analyze_data"

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

    def test_tool_has_input_model(self):
        tool = SmartDataAnalysisTool()
        assert tool.InputModel is AnalyzeDataInput

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
        """mock AnalysisAgent 返回成功结果"""
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

        mock_gateway = MagicMock()

        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.core.master_agent") as mock_master:
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

    @pytest.mark.asyncio
    async def test_execute_load_table_failure(self):
        """加载数据表失败时返回错误"""
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer:
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
    async def test_execute_agent_failure(self):
        """AnalysisAgent 运行失败时返回错误"""
        with patch("src.tools.data_analysis.data_analyzer.DataAnalyzer") as MockAnalyzer, \
             patch("src.tools.data_analysis.analysis_agent.AnalysisAgent") as MockAgent, \
             patch("src.core.master_agent") as mock_master:
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
                    "file_path": "storage/analysis_charts/trend.png",
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
             patch("src.core.master_agent") as mock_master:
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
             patch("src.core.master_agent") as mock_master:
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

