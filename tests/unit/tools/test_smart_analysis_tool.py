"""
SmartDataAnalysisTool 单元测试（mock AnalysisAgent）

测试内容：
1. 工具定义（name, description, schema）
2. 参数校验（requirement 和 tables_metadata 必填）
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
        assert "tables_metadata" in required

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
        tool = SmartDataAnalysisTool()
        missing = tool.get_missing_parameters(requirement="test")
        assert "tables_metadata" in missing

    def test_missing_all_required(self):
        tool = SmartDataAnalysisTool()
        missing = tool.get_missing_parameters()
        assert "requirement" in missing
        assert "tables_metadata" in missing

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
            "summary": "分析完成：共统计5个区域",
            "tables": [{"columns": ["区域", "金额"], "rows": [["华东", 100]], "row_count": 1}],
            "charts": [],
            "steps": [],
            "intermediate_files": [],
            "total_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "iterations": 3,
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
            assert result["summary"] == "分析完成：共统计5个区域"
            assert len(result["tables"]) == 1
            assert result["iterations"] == 3

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
            "summary": "趋势分析完成",
            "tables": [],
            "charts": [
                {
                    "file_path": "storage/analysis_charts/trend.png",
                    "chart_type": "line",
                    "title": "月度趋势",
                }
            ],
            "steps": [
                {"step": 1, "method": "trend", "description": "月度趋势"},
                {"step": 2, "method": "to_chart", "description": "生成line图表"},
            ],
            "intermediate_files": [],
            "total_usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
            "iterations": 2,
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
            assert len(result["charts"]) == 1
            assert result["charts"][0]["chart_type"] == "line"
            assert len(result["steps"]) == 2

