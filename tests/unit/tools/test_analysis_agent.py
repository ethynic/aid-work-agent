"""
AnalysisAgent 单元测试（mock LLM）

覆盖：单步查询、多步聚合、工具执行失败、最大迭代次数、步骤记录、
白名单拒绝、intermediate_files 去重、token 累加、trace 持久化。
"""

import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from src.tools.data_analysis.analysis_agent import AnalysisAgent, MAX_ITERATIONS
from src.tools.data_analysis.data_analyzer import DataAnalyzer


# ============================================================
# Helpers
# ============================================================


def _make_tool_call(name: str, arguments: dict, call_id: str = None) -> dict:
    """构建 tool_call 结构（兼容 OpenAI 格式）。"""
    return {
        "id": call_id or f"call_{name}",
        "name": name,
        "arguments": arguments,
    }


def _make_llm_response(content="分析完成", tool_calls=None, usage=None):
    """构建 mock LLM 响应。"""
    return {
        "content": content,
        "tool_calls": tool_calls,
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        "request_id": "req_test",
    }


def _sample_metadata():
    """构建示例表 metadata。"""
    return [
        {
            "table_id": "tbl_sales",
            "table_name": "销售数据",
            "description": "2025年销售数据",
            "row_count": 1000,
            "column_count": 5,
            "columns": [
                {"name": "region", "data_type": "text", "description": "区域"},
                {"name": "amount", "data_type": "number", "description": "金额"},
                {"name": "date", "data_type": "date", "description": "日期"},
            ],
        }
    ]


def _loaded_analyzer():
    """创建已加载示例数据的 DataAnalyzer。"""
    analyzer = DataAnalyzer(session_id="test_session")
    df = pd.DataFrame({
        "region": ["华东", "华东", "华南", "华南", "华北", "华北"],
        "amount": [100, 200, 150, 250, 180, 220],
        "date": ["2025-01", "2025-02", "2025-01", "2025-02", "2025-01", "2025-02"],
    })
    analyzer._tables["tbl_sales"] = df
    return analyzer


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def mock_llm():
    """Mock LLM Gateway。"""
    gateway = MagicMock()
    gateway.get_model_name = MagicMock(return_value="test-model")
    gateway.get_provider_name = MagicMock(return_value="test-provider")
    gateway.chat_with_tools = AsyncMock()
    return gateway


@pytest.fixture
def agent(mock_llm):
    """创建 AnalysisAgent 实例。"""
    analyzer = _loaded_analyzer()
    return AnalysisAgent(
        llm_gateway=mock_llm,
        analyzer=analyzer,
        analysis_id="test_analysis_001",
        tables_metadata=_sample_metadata(),
    )


# ============================================================
# Tests: 构建初始 prompt
# ============================================================


class TestBuildPrompt:
    def test_build_user_message_contains_requirement(self, agent):
        msg = agent._build_user_message("按区域统计销售额")
        assert "按区域统计销售额" in msg
        assert "tbl_sales" in msg

    def test_build_tables_info_contains_columns(self, agent):
        info = agent._build_tables_info()
        assert "销售数据" in info
        assert "region" in info
        assert "amount" in info

    def test_build_tables_info_empty_metadata(self, mock_llm):
        analyzer = _loaded_analyzer()
        ag = AnalysisAgent(mock_llm, analyzer, "t1", tables_metadata=[])
        assert ag._build_tables_info() == "无可用数据表"


# ============================================================
# Tests: 单步查询 → to_table → 完成
# ============================================================


class TestSingleStepQuery:
    @pytest.mark.asyncio
    async def test_query_then_to_table(self, agent, mock_llm):
        """LLM 调用 query → to_table → 不再调用工具 → 返回总结"""
        # 第一次：LLM 调用 query
        # 第二次：LLM 调用 to_table
        # 第三次：LLM 给出总结（无 tool_calls）
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(tool_calls=[
                _make_tool_call("query", {
                    "source": "tbl_sales",
                    "columns": ["region", "amount"],
                    "output_var": "q1",
                }),
            ]),
            _make_llm_response(tool_calls=[
                _make_tool_call("to_table", {
                    "source": "q1",
                    "output_var": "table_1",
                }),
            ]),
            _make_llm_response(content="按区域统计完成", tool_calls=None),
        ]

        result = await agent.run("按区域统计销售额")

        assert result["success"] is True
        assert result["summary"] == "按区域统计完成"
        assert len(result["tables"]) == 1
        assert result["tables"][0]["output_var"] == "table_1"
        assert len(result["steps"]) == 2
        assert result["steps"][0]["method"] == "query"
        assert result["steps"][1]["method"] == "to_table"
        assert result["iterations"] == 3


# ============================================================
# Tests: 多步聚合 → to_chart
# ============================================================


class TestMultiStepAggregate:
    @pytest.mark.asyncio
    async def test_aggregate_then_to_chart(self, agent, mock_llm):
        """LLM 调用 aggregate → to_chart → 返回总结"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(tool_calls=[
                _make_tool_call("aggregate", {
                    "source": "tbl_sales",
                    "group_by": ["region"],
                    "aggregations": [{"column": "amount", "function": "sum", "alias": "total"}],
                    "output_var": "agg1",
                }),
            ]),
            _make_llm_response(tool_calls=[
                _make_tool_call("to_chart", {
                    "source": "agg1",
                    "chart_type": "bar",
                    "x_column": "region",
                    "y_columns": ["total"],
                    "title": "区域销售额",
                    "output_var": "chart_1",
                }),
            ]),
            _make_llm_response(content="各区域销售额对比完成", tool_calls=None),
        ]

        result = await agent.run("按区域对比销售额")

        assert result["success"] is True
        assert len(result["charts"]) == 1
        assert result["charts"][0]["chart_type"] == "bar"
        assert len(result["steps"]) == 2
        assert result["steps"][0]["method"] == "aggregate"
        assert result["steps"][0]["output_var"] == "agg1"
        assert "file_path" in result["steps"][0]


# ============================================================
# Tests: 工具执行失败 → LLM 收到错误 → 调整策略
# ============================================================


class TestToolExecutionFailure:
    @pytest.mark.asyncio
    async def test_invalid_source_gets_error(self, agent, mock_llm):
        """LLM 调用 query 但 source 不存在 → 收到错误反馈 → 调整"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(tool_calls=[
                _make_tool_call("query", {
                    "source": "nonexistent_table",
                    "output_var": "q1",
                }),
            ]),
            _make_llm_response(tool_calls=[
                _make_tool_call("query", {
                    "source": "tbl_sales",
                    "output_var": "q1",
                }),
            ]),
            _make_llm_response(content="已修正", tool_calls=None),
        ]

        result = await agent.run("查询数据")

        assert result["success"] is True
        # 第一步应该失败（source 不存在）
        assert result["steps"][0]["method"] == "query"
        # 第二步应该成功
        assert len(result["steps"]) == 1  # 只有成功的步骤被记录

    @pytest.mark.asyncio
    async def test_disallowed_method_rejected(self, agent, mock_llm):
        """调用不在白名单的方法 → 被拒绝"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(tool_calls=[
                _make_tool_call("load_table", {"metadata": {}}),
            ]),
            _make_llm_response(content="无法执行", tool_calls=None),
        ]

        result = await agent.run("加载数据")

        assert result["success"] is True
        assert len(result["steps"]) == 0  # 被拒绝的方法不记录步骤


# ============================================================
# Tests: 达到 MAX_ITERATIONS
# ============================================================


class TestMaxIterations:
    @pytest.mark.asyncio
    async def test_reach_max_iterations(self, mock_llm):
        """LLM 持续调用工具 → 达到 MAX_ITERATIONS → 正常退出"""
        analyzer = _loaded_analyzer()
        ag = AnalysisAgent(
            mock_llm, analyzer, "test_max_iter",
            tables_metadata=_sample_metadata(),
        )

        # 每次都返回一个 query tool_call
        def perpetual_query_response(*args, **kwargs):
            return _make_llm_response(
                tool_calls=[_make_tool_call("query", {
                    "source": "tbl_sales",
                    "output_var": "q_loop",
                })],
            )

        mock_llm.chat_with_tools.side_effect = perpetual_query_response

        result = await ag.run("无限循环测试")

        assert result["success"] is True
        assert result["iterations"] == MAX_ITERATIONS
        assert "达到最大迭代次数" in result["summary"] or result["summary"]


# ============================================================
# Tests: 步骤记录完整性
# ============================================================


class TestStepRecording:
    @pytest.mark.asyncio
    async def test_step_has_required_fields(self, agent, mock_llm):
        """验证步骤记录包含必要字段"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(tool_calls=[
                _make_tool_call("aggregate", {
                    "source": "tbl_sales",
                    "group_by": ["region"],
                    "aggregations": [{"column": "amount", "function": "sum"}],
                    "output_var": "agg1",
                }),
            ]),
            _make_llm_response(content="完成", tool_calls=None),
        ]

        result = await agent.run("聚合测试")
        step = result["steps"][0]

        assert step["step"] == 1
        assert step["method"] == "aggregate"
        assert step["output_var"] == "agg1"
        assert "description" in step
        assert "file_path" in step
        assert "result_summary" in step
        summary = step["result_summary"]
        assert "rows" in summary
        assert "columns" in summary
        assert "preview" in summary
        assert len(summary["preview"]) <= 3

    @pytest.mark.asyncio
    async def test_to_table_step_has_preview(self, agent, mock_llm):
        """to_table 步骤包含 preview 数据"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(tool_calls=[
                _make_tool_call("to_table", {
                    "source": "tbl_sales",
                    "output_var": "table_1",
                }),
            ]),
            _make_llm_response(content="完成", tool_calls=None),
        ]

        result = await agent.run("输出表格")
        step = result["steps"][0]

        assert step["method"] == "to_table"
        assert "preview" in step["result_summary"]

    @pytest.mark.asyncio
    async def test_to_chart_step_has_file_path(self, agent, mock_llm):
        """to_chart 步骤包含 file_path"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(tool_calls=[
                _make_tool_call("to_chart", {
                    "source": "tbl_sales",
                    "chart_type": "bar",
                    "x_column": "region",
                    "y_columns": ["amount"],
                    "title": "区域销售额",
                    "output_var": "chart_1",
                }),
            ]),
            _make_llm_response(content="完成", tool_calls=None),
        ]

        result = await agent.run("生成图表")
        step = result["steps"][0]

        assert step["method"] == "to_chart"
        assert "file_path" in step
        assert step["chart_type"] == "bar"
        assert step["title"] == "区域销售额"


# ============================================================
# Tests: intermediate_files 去重
# ============================================================


class TestIntermediateFiles:
    @pytest.mark.asyncio
    async def test_deduplication_by_output_var(self, agent, mock_llm):
        """相同 output_var 的步骤只出现一次"""
        # LLM 调用两次 query 使用相同的 output_var
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(tool_calls=[
                _make_tool_call("query", {
                    "source": "tbl_sales",
                    "output_var": "data",
                }),
            ]),
            _make_llm_response(tool_calls=[
                _make_tool_call("query", {
                    "source": "data",
                    "filters": [{"column": "region", "op": "eq", "value": "华东"}],
                    "output_var": "data",
                }),
            ]),
            _make_llm_response(content="完成", tool_calls=None),
        ]

        result = await agent.run("查询测试")
        output_vars = [f["output_var"] for f in result["intermediate_files"]]
        assert output_vars.count("data") == 1

    @pytest.mark.asyncio
    async def test_intermediate_files_includes_chart(self, agent, mock_llm):
        """intermediate_files 包含图表文件"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(tool_calls=[
                _make_tool_call("to_chart", {
                    "source": "tbl_sales",
                    "chart_type": "line",
                    "x_column": "date",
                    "y_columns": ["amount"],
                    "title": "趋势图",
                    "output_var": "chart_1",
                }),
            ]),
            _make_llm_response(content="完成", tool_calls=None),
        ]

        result = await agent.run("生成趋势图")
        files = result["intermediate_files"]
        chart_files = [f for f in files if f.get("method") == "to_chart"]
        assert len(chart_files) == 1
        assert chart_files[0]["chart_type"] == "line"


# ============================================================
# Tests: Token 用量累加
# ============================================================


class TestTokenAccumulation:
    @pytest.mark.asyncio
    async def test_usage_accumulated_across_iterations(self, agent, mock_llm):
        """多次 LLM 调用的 token 用量正确累加"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(
                tool_calls=[_make_tool_call("query", {"source": "tbl_sales", "output_var": "q1"})],
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            ),
            _make_llm_response(
                tool_calls=[_make_tool_call("to_table", {"source": "q1", "output_var": "t1"})],
                usage={"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280},
            ),
            _make_llm_response(
                content="完成",
                tool_calls=None,
                usage={"prompt_tokens": 150, "completion_tokens": 60, "total_tokens": 210},
            ),
        ]

        result = await agent.run("统计测试")

        assert result["total_usage"]["prompt_tokens"] == 450
        assert result["total_usage"]["completion_tokens"] == 190
        assert result["total_usage"]["total_tokens"] == 640


# ============================================================
# Tests: Trace 记录
# ============================================================


class TestTracePersistence:
    @pytest.mark.asyncio
    async def test_trace_id_in_result(self, agent, mock_llm):
        """结果包含 trace_id"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(content="完成", tool_calls=None),
        ]

        result = await agent.run("测试trace")
        assert result["trace_id"] == "test_analysis_001"

    @pytest.mark.asyncio
    async def test_trace_persist_called(self, agent, mock_llm):
        """schedule_persist 被调用"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(content="完成", tool_calls=None),
        ]

        with patch("src.tools.data_analysis.analysis_agent.schedule_persist") as mock_persist:
            await agent.run("测试trace")
            mock_persist.assert_called_once()

    @pytest.mark.asyncio
    async def test_trace_persist_failure_does_not_crash(self, agent, mock_llm):
        """trace 持久化失败不影响结果"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(content="完成", tool_calls=None),
        ]

        with patch("src.tools.data_analysis.analysis_agent.schedule_persist", side_effect=Exception("DB error")):
            result = await agent.run("测试trace异常")
            assert result["success"] is True


# ============================================================
# Tests: LLM 调用失败
# ============================================================


class TestLLMFailure:
    @pytest.mark.asyncio
    async def test_llm_call_exception(self, agent, mock_llm):
        """LLM 调用异常 → 返回失败结果"""
        mock_llm.chat_with_tools.side_effect = Exception("API error")

        result = await agent.run("测试异常")

        assert result["success"] is False
        assert "API error" in result["error"]
        assert result["iterations"] == 1


# ============================================================
# Tests: 出图兜底（_is_chartable / _maybe_nudge_to_chart）
# ============================================================


class TestChartNudge:
    """总结前出图兜底：有表无图且数据可可视化时提示补图。"""

    def test_is_chartable_multi_row_with_numeric_and_dim(self):
        """多行 + 数值列 + 维度列 → True"""
        df = pd.DataFrame({"region": ["A", "B", "C"], "amount": [10, 20, 30]})
        assert AnalysisAgent._is_chartable(df) is True

    def test_is_chartable_single_row(self):
        """单行 → False"""
        df = pd.DataFrame({"region": ["A"], "amount": [10]})
        assert AnalysisAgent._is_chartable(df) is False

    def test_is_chartable_no_numeric(self):
        """无数值列 → False"""
        df = pd.DataFrame({"region": ["A", "B"], "name": ["x", "y"]})
        assert AnalysisAgent._is_chartable(df) is False

    def test_is_chartable_no_dimension(self):
        """纯数值无维度列 → False"""
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        assert AnalysisAgent._is_chartable(df) is False

    def test_is_chartable_none(self):
        assert AnalysisAgent._is_chartable(None) is False

    def test_nudge_triggers_when_table_without_chart(self, agent):
        """有表无图且数据可可视化 → 注入提示、返回 True、且仅触发一次"""
        agent._artifacts = [{"type": "table", "id": "t1"}]
        agent._last_table_df = pd.DataFrame({"region": ["A", "B"], "amount": [10, 20]})
        messages = []
        triggered = agent._maybe_nudge_to_chart(messages, "这是总结")
        assert triggered is True
        assert agent._chart_nudge_done is True
        assert len(messages) == 2  # assistant 总结 + user 补图提示
        assert "to_chart" in messages[1]["content"]
        # 二次调用应放行（防死循环）
        assert agent._maybe_nudge_to_chart([], "总结2") is False

    def test_nudge_skipped_when_chart_exists(self, agent):
        """已有 chart artifact → 不触发"""
        agent._artifacts = [{"type": "chart", "id": "c1"}, {"type": "table", "id": "t1"}]
        agent._last_table_df = pd.DataFrame({"region": ["A", "B"], "amount": [10, 20]})
        assert agent._maybe_nudge_to_chart([], "总结") is False

    def test_nudge_skipped_when_no_table(self, agent):
        """无任何 artifact → 不触发"""
        agent._artifacts = []
        agent._last_table_df = pd.DataFrame({"region": ["A", "B"], "amount": [10, 20]})
        assert agent._maybe_nudge_to_chart([], "总结") is False

    def test_nudge_skipped_when_data_not_chartable(self, agent):
        """有表但数据不可可视化（单行）→ 不触发"""
        agent._artifacts = [{"type": "table", "id": "t1"}]
        agent._last_table_df = pd.DataFrame({"region": ["A"], "amount": [10]})
        assert agent._maybe_nudge_to_chart([], "总结") is False

    def test_last_table_df_uses_actual_output_not_full_source(self, agent):
        """to_table 实际输出 1 行时，_last_table_df 应反映实际输出（而非全量源）→ 不可可视化"""
        result = {
            "columns": ["region", "amount"],
            "rows": [["华东", 100]],  # 仅 1 行（如查某条明细）
            "row_count": 1,
            "total_count": 1,
        }
        agent._handle_to_table("v1", result, {"source": "v1", "title": "单条明细"})
        assert len(agent._last_table_df) == 1
        assert agent._is_chartable(agent._last_table_df) is False


# ============================================================
# Tests: 白名单校验
# ============================================================


class TestWhitelist:
    def test_allowed_methods(self):
        """白名单包含全部 9 个方法"""
        from src.tools.data_analysis.analysis_tools_schema import ALLOWED_METHODS
        expected = {"query", "aggregate", "merge", "pivot", "calculate",
                    "compare", "trend", "to_table", "to_chart"}
        assert expected == ALLOWED_METHODS

    @pytest.mark.asyncio
    async def test_load_table_rejected(self, agent, mock_llm):
        """load_table 不在白名单 → 被拒绝"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(tool_calls=[
                _make_tool_call("load_table", {"metadata": {}}),
            ]),
            _make_llm_response(content="无法加载", tool_calls=None),
        ]

        result = await agent.run("加载表")
        assert result["success"] is True
        assert len(result["steps"]) == 0

    @pytest.mark.asyncio
    async def test_private_method_rejected(self, agent, mock_llm):
        """私有方法不在白名单 → 被拒绝"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(tool_calls=[
                _make_tool_call("_eval_expression", {"expr": "1+1"}),
            ]),
            _make_llm_response(content="无法执行", tool_calls=None),
        ]

        result = await agent.run("执行表达式")
        assert len(result["steps"]) == 0


# ============================================================
# Tests: Schema 定义验证
# ============================================================


class TestSchemaDefinition:
    def test_tools_count(self):
        """工具定义数量为 9"""
        from src.tools.data_analysis.analysis_tools_schema import ANALYSIS_TOOLS
        assert len(ANALYSIS_TOOLS) == 9

    def test_all_tools_have_required_structure(self):
        """每个工具定义包含 name/description/parameters"""
        from src.tools.data_analysis.analysis_tools_schema import ANALYSIS_TOOLS
        names = set()
        for tool in ANALYSIS_TOOLS:
            func = tool["function"]
            assert func["name"]
            assert func["description"]
            assert "parameters" in func
            params = func["parameters"]
            assert params["type"] == "object"
            assert "properties" in params
            names.add(func["name"])
        assert names == {"query", "aggregate", "merge", "pivot", "calculate",
                         "compare", "trend", "to_table", "to_chart"}

    def test_data_methods_have_output_var_required(self):
        """数据处理方法的 output_var 是必填"""
        from src.tools.data_analysis.analysis_tools_schema import ANALYSIS_TOOLS
        for tool in ANALYSIS_TOOLS:
            func = tool["function"]
            params = func["parameters"]
            if "output_var" in params.get("properties", {}):
                assert "output_var" in params.get("required", []), \
                    f"{func['name']} 的 output_var 应为 required"

    def test_source_in_data_methods(self):
        """有 source 参数的方法包含描述"""
        from src.tools.data_analysis.analysis_tools_schema import ANALYSIS_TOOLS
        for tool in ANALYSIS_TOOLS:
            func = tool["function"]
            props = func["parameters"].get("properties", {})
            if "source" in props:
                assert "table_id" in props["source"]["description"]
                assert "output_var" in props["source"]["description"]


# ============================================================
# Tests: 步骤描述生成
# ============================================================


class TestDescribeStep:
    def test_query_description(self, agent):
        desc = agent._describe_step("query", {
            "filters": [{"column": "region", "op": "eq", "value": "华东"}],
            "columns": ["region", "amount"],
            "sort_by": "amount",
            "limit": 10,
        })
        assert "过滤" in desc
        assert "选择" in desc
        assert "排序" in desc
        assert "10 行" in desc

    def test_aggregate_description(self, agent):
        desc = agent._describe_step("aggregate", {
            "group_by": ["region"],
            "aggregations": [{"column": "amount", "function": "sum"}],
        })
        assert "region" in desc
        assert "1个聚合操作" in desc

    def test_merge_description(self, agent):
        desc = agent._describe_step("merge", {
            "how": "left",
            "left_ref": "t1",
            "right_ref": "t2",
        })
        assert "left" in desc
        assert "t1" in desc

    def test_trend_description(self, agent):
        desc = agent._describe_step("trend", {
            "value_column": "amount",
            "freq": "M",
        })
        assert "amount" in desc


# ============================================================
# Tests: merge 方法（多表关联）
# ============================================================


class TestMergeMethod:
    @pytest.mark.asyncio
    async def test_merge_two_tables(self, mock_llm):
        """merge 关联两个表 → 结果正确"""
        analyzer = _loaded_analyzer()
        # 添加第二个表
        df2 = pd.DataFrame({
            "region": ["华东", "华南", "华北"],
            "manager": ["张三", "李四", "王五"],
        })
        analyzer._tables["tbl_managers"] = df2

        ag = AnalysisAgent(
            mock_llm, analyzer, "test_merge",
            tables_metadata=_sample_metadata(),
        )

        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(tool_calls=[
                _make_tool_call("merge", {
                    "left_ref": "tbl_sales",
                    "right_ref": "tbl_managers",
                    "left_on": "region",
                    "right_on": "region",
                    "how": "left",
                    "output_var": "merged",
                }),
            ]),
            _make_llm_response(content="关联完成", tool_calls=None),
        ]

        result = await ag.run("关联销售和经理")
        assert result["success"] is True
        assert len(result["steps"]) == 1
        step = result["steps"][0]
        assert step["method"] == "merge"
        assert step["output_var"] == "merged"


# ============================================================
# Tests: 完整结果结构
# ============================================================


class TestResultStructure:
    @pytest.mark.asyncio
    async def test_result_has_all_required_fields(self, agent, mock_llm):
        """返回结果包含所有必要字段"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(content="简单分析完成", tool_calls=None),
        ]

        result = await agent.run("简单测试")

        assert "success" in result
        assert "summary" in result
        assert "tables" in result
        assert "charts" in result
        assert "steps" in result
        assert "intermediate_files" in result
        assert "total_usage" in result
        assert "iterations" in result
        assert "trace_id" in result

    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_immediately(self, agent, mock_llm):
        """LLM 不调用任何工具 → 直接返回"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(content="这个问题不需要数据分析", tool_calls=None),
        ]

        result = await agent.run("不需要分析的问题")
        assert result["success"] is True
        assert result["summary"] == "这个问题不需要数据分析"
        assert result["iterations"] == 1
        assert len(result["steps"]) == 0
        assert len(result["tables"]) == 0
        assert len(result["charts"]) == 0
