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
# Fixtures
# ============================================================


@pytest.fixture(autouse=True)
def _isolated_tenants_root(tmp_path, monkeypatch):
    """把 storage._TENANTS_ROOT 重定向到 tmp_path，图表/CSV 产物不污染仓库 storage/"""
    from src.core import storage as storage_mod
    monkeypatch.setattr(storage_mod, "_TENANTS_ROOT", str(tmp_path / "tenants"))
    return tmp_path


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

        agent._chart_nudge_done = True  # 跳过出图兜底，专注测 query→to_table 流程
        result = await agent.run("按区域统计销售额")

        assert result["success"] is True
        assert result["conclusion"] == "按区域统计完成"
        tables = [a for a in result["artifacts"] if a["type"] == "table"]
        assert len(tables) == 1
        assert tables[0]["id"] == "table_1"
        assert len(agent._steps) == 2
        assert agent._steps[0]["method"] == "query"
        assert agent._steps[1]["method"] == "to_table"
        assert result["analysis_meta"]["iterations"] == 3


class TestModelBilling:
    """模型与计费：内层 LLM 调用不硬编码模型（跟随外层智能体网关），
    record_background_llm_usage 显式传实际模型名修正兜底落库单价。"""

    @pytest.mark.asyncio
    async def test_chat_with_tools_has_no_hardcoded_model(self, agent, mock_llm):
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(content="done", tool_calls=None),
        ]
        agent._chart_nudge_done = True
        await agent.run("按区域统计销售额")

        _, kwargs = mock_llm.chat_with_tools.call_args
        assert "model" not in kwargs

    @pytest.mark.asyncio
    async def test_record_usage_passes_actual_model(self, agent, mock_llm):
        with patch("src.services.session_record.record_background_llm_usage") as mock_record:
            mock_llm.chat_with_tools.side_effect = [
                _make_llm_response(content="done", tool_calls=None),
            ]
            agent._chart_nudge_done = True
            await agent.run("按区域统计销售额")

        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs.get("model") == "test-model"


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
        charts = [a for a in result["artifacts"] if a["type"] == "chart"]
        assert len(charts) == 1
        assert len(agent._steps) == 2
        assert agent._steps[0]["method"] == "aggregate"
        assert agent._steps[0]["output_var"] == "agg1"
        assert "file_path" in agent._steps[0]


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
        # 第一步失败（source 不存在）不记录，只有成功的步骤被记录
        assert agent._steps[0]["method"] == "query"
        assert len(agent._steps) == 1


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
        assert result["analysis_meta"]["iterations"] == MAX_ITERATIONS
        assert result["conclusion"]


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
        step = agent._steps[0]

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

        agent._chart_nudge_done = True  # 跳过出图兜底
        result = await agent.run("输出表格")
        step = agent._steps[0]

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
        step = agent._steps[0]

        assert step["method"] == "to_chart"
        assert "file_path" in step
        assert step["chart_type"] == "bar"
        assert step["title"] == "区域销售额"


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
        agent._chart_nudge_done = True  # 跳过出图兜底，专注测 token 累加

        result = await agent.run("统计测试")

        assert agent._total_usage["prompt_tokens"] == 450
        assert agent._total_usage["completion_tokens"] == 190
        assert agent._total_usage["total_tokens"] == 640


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
        assert result["analysis_meta"]["trace_id"] == "test_analysis_001"

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
        assert result["analysis_meta"]["iterations"] == 1


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


# ============================================================
# Tests: 空总结兜底（content 空 + 无 tool_calls 时重试一次）
# ============================================================


class TestEmptySummaryFallback:
    """无工具调用且 content 为空（推理烧穿 max_tokens 的典型特征）时的重试与失败兜底。"""

    async def test_empty_summary_retries_then_success(self, agent, mock_llm):
        """首次空总结 → 重试 → 第二次正常输出 → success=True"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(content=""),  # 第一次：空（模拟烧穿截断）
            _make_llm_response(content="2025年7月各品类毛利同比：A品类上涨12%，B品类下降5%"),
        ]
        result = await agent.run("各品类毛利同比分析")
        assert result["success"] is True
        assert "同比" in result["conclusion"]
        assert mock_llm.chat_with_tools.call_count == 2

    async def test_empty_summary_retries_then_fails(self, agent, mock_llm):
        """首次空总结 → 重试 → 第二次仍空 → 返回失败，绝不把空串当结论"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(content=""),
            _make_llm_response(content=""),
        ]
        result = await agent.run("各品类毛利同比分析")
        assert result["success"] is False
        assert "输出为空" in result["error"]
        assert mock_llm.chat_with_tools.call_count == 2

    async def test_empty_summary_retry_disables_thinking_for_qwen(self, agent, mock_llm):
        """空总结重试时，qwen 通道必须传 enable_thinking=False（防思考再次烧穿输出预算）"""
        mock_llm.get_provider_name = MagicMock(return_value="qwen")
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(content=""),
            _make_llm_response(content="结论"),
        ]
        result = await agent.run("各品类毛利同比分析")
        assert result["success"] is True
        first_kwargs = mock_llm.chat_with_tools.call_args_list[0].kwargs
        second_kwargs = mock_llm.chat_with_tools.call_args_list[1].kwargs
        assert "enable_thinking" not in first_kwargs
        assert second_kwargs.get("enable_thinking") is False

    async def test_main_call_does_not_pass_hard_max_tokens(self, agent, mock_llm):
        """主调用不显式传 max_tokens，由网关按模型配置上限解析（思考模式预算充足）"""
        mock_llm.chat_with_tools.return_value = _make_llm_response(content="done")
        await agent.run("测试需求")
        kwargs = mock_llm.chat_with_tools.call_args.kwargs
        assert "max_tokens" not in kwargs

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
        """白名单包含全部 16 个方法"""
        from src.tools.data_analysis.analysis_tools_schema import ALLOWED_METHODS
        expected = {"search_data_tables", "list_data_tables", "load_table", "load_output", "describe",
                    "query", "aggregate", "merge", "append", "pivot", "calculate",
                    "compare", "trend", "extract_hierarchy", "to_table", "to_chart"}
        assert expected == ALLOWED_METHODS

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
        assert result["success"] is True
        assert len(agent._steps) == 0


# ============================================================
# Tests: Schema 定义验证
# ============================================================


class TestSchemaDefinition:
    def test_tools_count(self):
        """工具定义数量为 16"""
        from src.tools.data_analysis.analysis_tools_schema import ANALYSIS_TOOLS
        assert len(ANALYSIS_TOOLS) == 16

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
        assert names == {"search_data_tables", "list_data_tables", "load_table", "load_output", "describe",
                         "query", "aggregate", "merge", "append", "pivot", "calculate",
                         "compare", "trend", "extract_hierarchy", "to_table", "to_chart"}

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
        assert len(ag._steps) == 1
        step = ag._steps[0]
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
        assert "conclusion" in result
        assert "artifacts" in result
        assert "analysis_meta" in result
        assert "iterations" in result["analysis_meta"]
        assert "trace_id" in result["analysis_meta"]


# ============================================================
# Tests: 跨租户共享检索范围（_build_tenant_scope / _load_shared_ranges）
# ============================================================


class TestSharedTenantScope:
    def _make_agent(self, tenant_id=None, subagent_id=None):
        analyzer = _loaded_analyzer()
        return AnalysisAgent(
            llm_gateway=MagicMock(),
            analyzer=analyzer,
            analysis_id="test_shared",
            tables_metadata=[],
            tenant_id=tenant_id,
            subagent_id=subagent_id,
        )

    def test_no_tenant_cannot_form_unfiltered_runtime_scope(self):
        """无可信租户时不得构造全表运行时查询。"""
        ag = self._make_agent(tenant_id=None, subagent_id="travel-quote")
        sql, params = ag._build_tenant_scope()
        assert sql == "AND FALSE"
        assert params == []

    def test_tenant_without_subagent_degenerates_to_own_tenant(self):
        """有 tenant 无 subagent（主智能体场景）→ 仅本租户"""
        ag = self._make_agent(tenant_id="t_own", subagent_id=None)
        sql, params = ag._build_tenant_scope()
        assert sql == "AND tenant_id = %s"
        assert params == ["t_own"]

    @patch("src.knowledge.retriever.tenant_range.load_shared_ranges")
    def test_tenant_with_subagent_aggregates_shared_owners(self, mock_load):
        """有 tenant + subagent → 聚合已启用共享来源租户为 ANY 条件"""
        mock_load.return_value = [("t_shared_a", "data-analysis-metadata"), ("t_shared_b", "data-analysis-metadata")]
        ag = self._make_agent(tenant_id="t_own", subagent_id="travel-quote")
        sql, params = ag._build_tenant_scope()
        assert sql == "AND tenant_id = ANY(%s)"
        assert params == [["t_own", "t_shared_a", "t_shared_b"]]

    @patch("src.knowledge.retriever.tenant_range.load_shared_ranges")
    def test_load_shared_ranges_called_with_source_type(self, mock_load):
        """_load_shared_ranges 固定传 data-analysis-metadata 分类"""
        mock_load.return_value = [("t_shared_a", "data-analysis-metadata")]
        ag = self._make_agent(tenant_id="t_own", subagent_id="travel-quote")
        ag._load_shared_ranges()
        mock_load.assert_called_once_with("t_own", "travel-quote", "data-analysis-metadata")

    @pytest.mark.asyncio
    @patch("src.knowledge.retriever.tenant_range.load_shared_ranges")
    async def test_search_data_tables_passes_shared_ranges_to_retriever(self, mock_load, mock_llm):
        """search_data_tables 语义检索应把共享范围传给 retriever.retrieve"""
        mock_load.return_value = [("t_shared_a", "data-analysis-metadata")]
        analyzer = _loaded_analyzer()
        ag = AnalysisAgent(
            llm_gateway=mock_llm,
            analyzer=analyzer,
            analysis_id="test_search_shared",
            tables_metadata=[],
            tenant_id="t_own",
            subagent_id="travel-quote",
        )
        fake_retriever = MagicMock()
        fake_retriever.retrieve = AsyncMock(return_value=[])
        ag._retriever = fake_retriever

        result = await ag._handle_search_data_tables("销售数据", top_k=5)

        assert result["success"] is True
        fake_retriever.retrieve.assert_awaited_once()
        kwargs = fake_retriever.retrieve.await_args.kwargs
        assert kwargs["tenant_id"] == "t_own"
        assert kwargs["source_type"] == "data-analysis-metadata"
        assert kwargs["shared_ranges"] == [("t_shared_a", "data-analysis-metadata")]

    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_immediately(self, agent, mock_llm):
        """LLM 不调用任何工具 → 直接返回"""
        mock_llm.chat_with_tools.side_effect = [
            _make_llm_response(content="这个问题不需要数据分析", tool_calls=None),
        ]

        result = await agent.run("不需要分析的问题")
        assert result["success"] is True
        assert result["conclusion"] == "这个问题不需要数据分析"
        assert result["analysis_meta"]["iterations"] == 1
        assert len(agent._steps) == 0
        assert len(result["artifacts"]) == 0


# ============================================================
# Tests: 数据表移动兜底（2026-09-20 设计 §4.4）
# ============================================================


def _moved_table_db_mock(list_rows=None, single_row=None):
    """构造 DB mock：fetchall 返回列表查询行，fetchone 返回单表行"""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = list_rows or []
    mock_cursor.fetchone.return_value = single_row
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=None)
    return mock_cm


def _moved_table_agent():
    return AnalysisAgent(
        llm_gateway=MagicMock(),
        analyzer=_loaded_analyzer(),
        analysis_id="test_moved",
        tenant_id="tenant_t1",
        subagent_id="data-analysis",
    )


class TestMovedTableFallback:
    """被移动出 data-analysis-metadata 的 [数据表] 文档仍可发现/加载"""

    def test_list_includes_moved_table_via_title_prefix(self):
        """列表查询：OR 兜底分支按标题前缀纳入本租户被移动的表"""
        agent = _moved_table_agent()
        rows = [{
            "id": 2341, "title": "[数据表] 渠道销售", "summary": "渠道维度",
            "metadata": json.dumps({"table_name": "渠道销售", "columns": [{"name": "amount"}]}),
        }]
        mock_cm = _moved_table_db_mock(list_rows=rows)
        with patch("src.db.database.get_db_connection", return_value=mock_cm):
            tables = agent._query_data_tables_list("")

        assert [t["table_id"] for t in tables] == ["2341"]
        sql = " ".join(mock_cm.__enter__().cursor().execute.call_args[0][0].split())
        # 兜底分支 SQL 存在（ILIKE 模式参数化传值，避免字面 % 被 psycopg2 当占位符），
        # 且共享侧未放宽（无共享时不产生额外租户）
        assert "title ILIKE %s" in sql
        params = mock_cm.__enter__().cursor().execute.call_args[0][1]
        assert list(params) == [["tenant_t1"], "tenant_t1", "[数据表] %"]

    def test_missing_tenant_never_queries_shared_tables(self):
        from src.tools.data_analysis.analysis_agent import (
            query_data_tables_list, fetch_table_metadata_from_db, filter_available_table_ids,
        )
        with patch("src.db.database.get_db_connection") as db:
            assert query_data_tables_list(tenant_id=None, agent_id="main") == []
            assert fetch_table_metadata_from_db("101", None, None, "main") is None
            assert filter_available_table_ids(["101"], None, None, "main") == set()
        db.assert_not_called()

    def test_load_moved_table_requires_table_metadata(self):
        """加载：移动分支命中的文档必须带 table_name/columns，普通同名文档排除"""
        agent = _moved_table_agent()

        # 真数据表（metadata 完整）→ 正常返回
        real_row = {
            "id": 2341, "title": "[数据表] 渠道销售", "summary": "渠道维度",
            "metadata": json.dumps({"table_name": "渠道销售", "columns": [{"name": "amount"}]}),
            "source_type": "k_cf06ec9b497b",
        }
        mock_cm = _moved_table_db_mock(single_row=real_row)
        mock_cm.__enter__().cursor().fetchone.side_effect = [
            {"sources": [], "share_owners": []}, real_row,
        ]
        with patch("src.db.database.get_db_connection", return_value=mock_cm):
            meta = agent._fetch_single_table_metadata("2341")
        assert meta is not None
        assert meta["table_name"] == "渠道销售"
        assert meta["columns"] == [{"name": "amount"}]

        # 普通同名文档（缺 table_name/columns）→ 排除
        plain_row = {
            "id": 2342, "title": "[数据表] 冒名文档", "summary": "",
            "metadata": json.dumps({"note": "普通文档"}), "source_type": "file",
        }
        mock_cm = _moved_table_db_mock(single_row=plain_row)
        mock_cm.__enter__().cursor().fetchone.side_effect = [
            {"sources": [], "share_owners": []}, plain_row,
        ]
        with patch("src.db.database.get_db_connection", return_value=mock_cm):
            assert agent._fetch_single_table_metadata("2342") is None

        # 未被移动（source_type 仍是 data-analysis-metadata）→ 不做 metadata 排除
        registered_row = dict(real_row, source_type="data-analysis-metadata", metadata="{}")
        mock_cm = _moved_table_db_mock(single_row=registered_row)
        mock_cm.__enter__().cursor().fetchone.side_effect = [
            {"sources": [], "share_owners": []}, registered_row,
        ]
        with patch("src.db.database.get_db_connection", return_value=mock_cm):
            meta = agent._fetch_single_table_metadata("2341")
        assert meta is not None

    async def test_search_zero_hit_falls_back_to_widened_list(self):
        """语义检索 0 命中且放宽列表非空：返回列表并附引导 hint"""
        agent = _moved_table_agent()
        agent._retriever = MagicMock()
        agent._retriever.retrieve = AsyncMock(return_value=[])
        fallback = [{
            "table_id": "2341", "table_name": "渠道销售", "description": "渠道维度",
            "columns_count": 1, "loaded": False,
        }]
        with patch.object(agent, "_query_data_tables_list", return_value=fallback):
            result = await agent._handle_search_data_tables("上个月各渠道销售总额")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["results"][0]["table_id"] == "2341"
        assert "load_table" in result["hint"]

    async def test_search_zero_hit_with_empty_list_keeps_original_hint(self):
        """语义检索 0 命中且放宽列表为空：维持原提示"""
        agent = _moved_table_agent()
        agent._retriever = MagicMock()
        agent._retriever.retrieve = AsyncMock(return_value=[])
        with patch.object(agent, "_query_data_tables_list", return_value=[]):
            result = await agent._handle_search_data_tables("不存在的表")
        assert result["count"] == 0
        assert "未找到匹配的数据表" in result["hint"]


# ============================================================
# Tests: 开局预加载指令与 load_table 精简确认（2026-10 改进1）
# ============================================================


class TestPreloadInstructionAndSlimLoad:
    """预加载表开局明示禁重复 search/load；已加载表 load_table 只回精简确认。"""

    def test_build_user_message_preload_instruction(self, agent):
        """已加载表清单前必须带明确指令：预加载完成、清单内 search/load 属于错误"""
        msg = agent._build_user_message("按区域统计销售额")
        assert "以下 1 张表已由系统预加载完成并附完整字段结构" in msg
        assert "（表名/列名/类型/说明）" in msg
        assert "对清单内的表调用 load_table 或 search_data_tables 属于错误" in msg
        assert "仅当确实需要清单外的表时才允许" in msg

    def test_build_tables_info_renders_nonempty_enum_only(self, mock_llm):
        """列级过滤：非空 enum_values 渲染（最多 10 个），空枚举与内部字段不进消息"""
        meta = [{
            "table_id": "t9",
            "table_name": "订单",
            "description": "订单数据",
            "columns": [
                {"name": "status", "data_type": "text", "description": "状态",
                 "enum_values": ["paid", "refunded", "closed"]},
                {"name": "amount", "data_type": "number", "description": "金额",
                 "enum_values": []},
            ],
            "source": {"type": "excel", "file_path": "/data/secret/orders.xlsx"},
        }]
        ag = AnalysisAgent(mock_llm, _loaded_analyzer(), "t_enum", tables_metadata=meta)

        info = ag._build_tables_info()
        assert "枚举值: paid, refunded, closed" in info
        assert info.count("枚举值") == 1  # 空 enum_values 跳过
        assert "orders.xlsx" not in info  # source 文件路径不带进消息

    def test_build_tables_info_derives_column_count_when_missing(self, mock_llm):
        """批量回查的 metadata 无 column_count 时按 columns 推导，不渲染 '?'"""
        meta = [{
            "table_id": "t10",
            "table_name": "库存",
            "columns": [{"name": "sku", "data_type": "text", "description": "编码"}],
        }]
        ag = AnalysisAgent(mock_llm, _loaded_analyzer(), "t_colcount", tables_metadata=meta)
        info = ag._build_tables_info()
        assert "列数: 1" in info

    @pytest.mark.asyncio
    async def test_load_table_already_loaded_returns_slim_confirm(self, agent):
        """清单内已加载表再 load_table → 精简确认，不回吐全量 columns（列定义已在开局消息）"""
        result = await agent._handle_load_table("tbl_sales")
        assert result["success"] is True
        assert "columns" not in result
        assert "预加载" in result["message"]
        assert result["column_count"] == 3

    @pytest.mark.asyncio
    async def test_load_table_new_table_returns_full_columns(self, agent):
        """清单外的表（例外情况）加载 → 仍返回完整列定义并计入已加载"""
        new_meta = {
            "table_id": "tbl_new", "table_name": "新表", "description": "补充数据",
            "columns": [{"name": "city", "data_type": "text", "description": "城市"}],
        }
        with patch.object(agent, "_fetch_single_table_metadata", return_value=new_meta), \
             patch.object(agent.analyzer, "load_table", AsyncMock(return_value="tbl_new")):
            agent.analyzer._tables["tbl_new"] = pd.DataFrame({"city": ["A"]})
            result = await agent._handle_load_table("tbl_new")

        assert result["success"] is True
        assert result["columns"][0]["name"] == "city"
        assert "tbl_new" in agent._loaded_table_ids


# ============================================================
# Tests: 成本优化（2026-10 改进点 4 + 追加改进 A/B）
# ============================================================


class TestReasoningContentPassthrough:
    """DeepSeek 思考模式：thinking 轮响应的 reasoning_content 必须随历史回传。

    2026-10-12 真实 API 实测：DeepSeek 服务端新增校验，开思考请求的历史
    assistant 消息缺 rc 键 -> 400 "must be passed back"，failover 到 qwen
    既污染计费又毁成本对照。机械轮关思考分级也因此撤回（混用开/关思考
    会被该校验不稳定击穿），全场统一开思考后真值回传是唯一防线。
    """

    def _make_agent(self, mock_llm):
        return AnalysisAgent(
            llm_gateway=mock_llm, analyzer=_loaded_analyzer(),
            analysis_id="t_tier", tables_metadata=_sample_metadata(),
        )

    @pytest.mark.asyncio
    async def test_reasoning_content_passed_back_in_history(self, mock_llm):
        """thinking 轮的 rc 挂到历史 assistant 消息，下一轮请求原样携带"""
        resp1 = _make_llm_response(tool_calls=[
            _make_tool_call("query", {"source": "tbl_sales", "output_var": "q1"}),
        ])
        resp1["reasoning_content"] = "先查询区域销售额"
        mock_llm.chat_with_tools.side_effect = [
            resp1,
            _make_llm_response(content="完成"),
        ]
        agent = self._make_agent(mock_llm)
        result = await agent.run("按区域统计")

        assert result["success"] is True
        second_messages = mock_llm.chat_with_tools.call_args_list[1].kwargs["messages"]
        asst_msgs = [m for m in second_messages if m.get("role") == "assistant"]
        assert len(asst_msgs) == 1
        assert asst_msgs[0].get("reasoning_content") == "先查询区域销售额"

    @pytest.mark.asyncio
    async def test_response_without_rc_leaves_history_clean(self, mock_llm):
        """响应无 rc（qwen failover 轮）-> 不伪造空串键，历史保持干净

        混合思考模式已撤回，空串占位失去了服务对象；伪造键在 DeepSeek
        校验口径未定时反而引入新风险（run3 空串补齐仍 400），宁缺勿假。
        """
        resp1 = _make_llm_response(tool_calls=[
            _make_tool_call("query", {"source": "tbl_sales", "output_var": "q1"}),
        ])
        assert "reasoning_content" not in resp1
        mock_llm.chat_with_tools.side_effect = [
            resp1,
            _make_llm_response(content="完成"),
        ]
        agent = self._make_agent(mock_llm)
        result = await agent.run("按区域统计")

        assert result["success"] is True
        second_messages = mock_llm.chat_with_tools.call_args_list[1].kwargs["messages"]
        asst_msgs = [m for m in second_messages if m.get("role") == "assistant"]
        assert len(asst_msgs) == 1
        assert "reasoning_content" not in asst_msgs[0]


class TestDescribeSlimming:
    """追加改进 A：describe 结果截断（20->8）+ memo 缓存与失效。

    为什么重要：describe 每列 20 个唯一值使主分析 27 次 ≈150k 新发 prompt（新发
    最大单一来源）；但缓存过期（源数据被改写后仍返回旧结构）是正确性风险，
    「非 describe 工具成功后清缓存」的失效策略必须被测试钉住。
    """

    def _make_agent(self, mock_llm, wide=False):
        if wide:
            analyzer = DataAnalyzer(session_id="test_session")
            df = pd.DataFrame({
                "region": [f"区域{i}" for i in range(12)] * 2,  # 12 个唯一值 > CAP
                "amount": range(24),
            })
            analyzer._tables["tbl_wide"] = df
        else:
            analyzer = _loaded_analyzer()
        return AnalysisAgent(
            llm_gateway=mock_llm, analyzer=analyzer,
            analysis_id="t_describe", tables_metadata=[],
        )

    def test_describe_truncates_unique_values(self, mock_llm):
        """12 个唯一值列 -> unique_values 截断到 8 条，unique_count 标量保留，附提示"""
        agent = self._make_agent(mock_llm, wide=True)
        result = agent._handle_describe({"source": "tbl_wide"}, 0)

        assert result["success"] is True
        stat = next(s for s in result["columns_summary"] if s["column"] == "region")
        assert stat["unique_count"] == 12
        assert len(stat["unique_values"]) == 8
        assert "values_hint" in result

    def test_describe_within_cap_no_truncation(self, mock_llm):
        """唯一值数不超过上限 -> 原样返回，不加提示（不误伤小基数列）"""
        agent = self._make_agent(mock_llm)
        result = agent._handle_describe({"source": "tbl_sales"}, 0)

        stat = next(s for s in result["columns_summary"] if s["column"] == "region")
        assert stat["unique_values"] == ["华东", "华南", "华北"]
        assert "values_hint" not in result

    def test_describe_memo_cache_hit(self, mock_llm):
        """同参数重复 describe -> 第二次命中缓存（analyzer.describe 只算一次）"""
        agent = self._make_agent(mock_llm)
        calls = []
        real_describe = agent.analyzer.describe

        def spy(**kwargs):
            calls.append(kwargs)
            return real_describe(**kwargs)

        agent.analyzer.describe = spy
        r1 = agent._handle_describe({"source": "tbl_sales"}, 0)
        r2 = agent._handle_describe({"source": "tbl_sales"}, 1)

        assert len(calls) == 1
        assert r2.get("cached") is True
        assert r2["row_count"] == r1["row_count"]
        assert r2["columns_summary"] == r1["columns_summary"]

    @pytest.mark.asyncio
    async def test_cache_invalidated_after_successful_data_method(self, mock_llm):
        """成功的 data_method 改变数据面 -> 缓存清空，再次 describe 重新计算"""
        agent = self._make_agent(mock_llm)
        calls = []
        real_describe = agent.analyzer.describe

        def spy(**kwargs):
            calls.append(kwargs)
            return real_describe(**kwargs)

        agent.analyzer.describe = spy
        agent._handle_describe({"source": "tbl_sales"}, 0)

        res = await agent._execute_tool(
            "query", {"source": "tbl_sales", "columns": ["region"], "output_var": "q1"}, 1
        )
        assert res["success"] is True

        r3 = agent._handle_describe({"source": "tbl_sales"}, 2)
        assert len(calls) == 2
        assert r3.get("cached") is None

    @pytest.mark.asyncio
    async def test_cache_kept_after_failed_tool(self, mock_llm):
        """失败的工具（数据面未变）-> 缓存保留（避免无谓重算）"""
        agent = self._make_agent(mock_llm)
        calls = []
        real_describe = agent.analyzer.describe

        def spy(**kwargs):
            calls.append(kwargs)
            return real_describe(**kwargs)

        agent.analyzer.describe = spy
        agent._handle_describe({"source": "tbl_sales"}, 0)

        res = await agent._execute_tool(
            "query", {"source": "不存在的表", "output_var": "q1"}, 1
        )
        assert res["success"] is False

        r2 = agent._handle_describe({"source": "tbl_sales"}, 1)
        assert len(calls) == 1
        assert r2.get("cached") is True


class TestPromptCostPrinciples:
    """追加改进 B：执行原则提示——同构多组查询合并 + 禁止冗余 describe。

    为什么重要：六国×维度切片 18 次同构 query 均可用一次分组聚合表达；
    提示词是行为改动的唯一载体（无代码强制），存在性测试防止提示被误删。
    """

    def test_system_prompt_has_merge_principle(self):
        from src.tools.data_analysis.analysis_tools_schema import ANALYSIS_SYSTEM_PROMPT
        assert "同构多组查询必须合并" in ANALYSIS_SYSTEM_PROMPT
        assert "group_by" in ANALYSIS_SYSTEM_PROMPT

    def test_system_prompt_has_describe_discipline(self):
        from src.tools.data_analysis.analysis_tools_schema import ANALYSIS_SYSTEM_PROMPT
        assert "禁止冗余 describe" in ANALYSIS_SYSTEM_PROMPT


# ============================================================
# Phase 2（数据表按智能体配置）：名单范围在列表/单表/批量回查/检索统一生效
# ============================================================

_AGENT_SCOPE_SQL_MARK = "NOT (metadata::jsonb ? 'allowed_agent_ids')"
# 带共享来源的调用会先执行 load_shared_ranges 的授权查询，打桩为空让 documents
# 查询成为第一条 executed 记录
_PATCH_SHARED_RANGES = patch("src.knowledge.retriever.tenant_range.load_shared_ranges", return_value=[])


class _FakeResultRows:
    """fetchall 返回 dict 行的最小游标替身（捕获 SQL 与参数）"""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(str(sql).split()), list(params or [])))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *args, **kwargs):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestAgentScopeSqlShape:
    """列表/单表回查 SQL：名单条件 AND 在完整 `(范围 OR 移动兜底)` 括号外侧，
    参数与占位符成对追加（tenant → moved → scope）。"""

    def test_query_data_tables_list_appends_scope_condition(self):
        from src.tools.data_analysis.analysis_agent import query_data_tables_list

        cur = _FakeResultRows([])
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(cur)), \
                _PATCH_SHARED_RANGES:
            query_data_tables_list("", "t1", "sub-a", None, "sub-a")

        sql, params = cur.executed[0]
        # OR 范围整体括号包裹（移动兜底为最后分支），名单条件在整体括号外 AND
        assert "AND tenant_id = ANY(%s) OR (tenant_id = %s AND title ILIKE %s))" in sql
        assert "AND ((NOT (metadata::jsonb ? 'allowed_agent_ids')" in sql
        assert _AGENT_SCOPE_SQL_MARK in sql
        # 参数顺序：范围 + 移动兜底 + 名单片段（agent_id 命中 → 数组包含参数 + 租户绑定）
        assert params[:3] == [["t1"], "t1", "[数据表] %"]
        import json as _json
        assert params[3] == _json.dumps(["sub-a"], ensure_ascii=False)
        assert params[4] == "t1"

    def test_query_data_tables_list_unknown_identity_shared_only_params(self):
        """agent_id=None：名单片段仅共享语义（无包含参数）"""
        from src.tools.data_analysis.analysis_agent import query_data_tables_list

        cur = _FakeResultRows([])
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(cur)):
            query_data_tables_list("", "t1", None, None, None)

        sql, params = cur.executed[0]
        assert "@>" not in sql
        # 参数顺序：范围（单租户）+ 移动兜底；名单片段无参（仅共享语义）
        assert params == ["t1", "t1", "[数据表] %"]

    def test_fetch_table_metadata_appends_scope_condition(self):
        from src.tools.data_analysis.analysis_agent import fetch_table_metadata_from_db

        row = {
            "id": 9078, "title": "[数据表] 订单", "summary": "s",
            "metadata": {"table_name": "orders", "columns": [{"name": "a"}]},
            "source_type": "data-analysis-metadata", "tenant_id": "t_src",
        }
        cur = _FakeResultRows([row])
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(cur)), \
                _PATCH_SHARED_RANGES:
            meta = fetch_table_metadata_from_db("9078", "t1", "sub-a", "sub-a")

        assert meta is not None and meta["table_id"] == "9078"
        sql, params = cur.executed[0]
        assert _AGENT_SCOPE_SQL_MARK in sql
        import json as _json
        # 参数顺序：id + 范围 + 移动兜底 + 名单片段（数组包含 + 租户绑定）
        assert params[0] == 9078
        assert params[4] == _json.dumps(["sub-a"], ensure_ascii=False)
        assert params[5] == "t1"

    def test_fetch_table_metadata_carries_owner_tenant(self):
        """服务端回查把文档来源租户作为 source.owner_tenant_id 内部字段带入，
        供 DataAnalyzer 数据库分支做连接器归属校验"""
        from src.tools.data_analysis.analysis_agent import fetch_table_metadata_from_db

        row = {
            "id": 9078, "title": "订单", "summary": "s",
            "metadata": {"table_name": "orders", "columns": [{"name": "a"}],
                         "connector_id": "c1"},
            "source_type": "data-analysis-metadata", "tenant_id": "t_src",
        }
        cur = _FakeResultRows([row])
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(cur)):
            meta = fetch_table_metadata_from_db("9078", "t1", None, "main")

        assert meta["source"]["connector_id"] == "c1"
        assert meta["source"]["owner_tenant_id"] == "t_src"

    def test_filter_available_table_ids_single_batch_query(self):
        """产物来源表可用性：去重一次查询，移动兜底分支含结构有效性"""
        from src.tools.data_analysis.analysis_agent import filter_available_table_ids

        cur = _FakeResultRows([{"id": 9078}, {"id": 9079}])
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(cur)):
            available = filter_available_table_ids(
                {"9078", "9078", "9079", "abc"}, "t1", None, "main"
            )
        assert available == {"9078", "9079"}
        sql, params = cur.executed[0]
        assert "id = ANY(%s)" in sql
        assert "jsonb_typeof(metadata::jsonb->'columns') = 'array'" in sql  # 移动分支结构校验
        assert _AGENT_SCOPE_SQL_MARK in sql
        assert params[0] == [9078, 9079]

    def test_filter_available_table_ids_no_tenant_returns_empty(self):
        from src.tools.data_analysis.analysis_agent import filter_available_table_ids

        with patch("src.db.database.get_db_connection") as mock_conn:
            assert filter_available_table_ids({"9078"}, None, None, None) == set()
        mock_conn.assert_not_called()

    def test_query_data_tables_list_excludes_prefix_non_table_docs(self):
        """移动兜底分支结构有效性（§18.6）：仅同名前缀的普通知识文档不纳入列表
        （不影响 50 表阈值计数）；真实移动表与 source_type 命中的表照常返回"""
        from src.tools.data_analysis.analysis_agent import query_data_tables_list

        moved_table = {
            "id": 9078, "title": "[数据表] 订单", "summary": "s",
            "metadata": {"table_name": "orders", "columns": [{"name": "a"}]},
            "source_type": "file",
        }
        prefix_plain_doc = {
            "id": 9079, "title": "[数据表] 使用说明", "summary": "s",
            "metadata": {"foo": "bar"},
            "source_type": "file",
        }
        source_type_table = {
            "id": 9080, "title": "库存", "summary": "s",
            "metadata": {"table_name": "stock", "columns": [{"name": "b"}]},
            "source_type": "data-analysis-metadata",
        }
        cur = _FakeResultRows([moved_table, prefix_plain_doc, source_type_table])
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(cur)), \
                _PATCH_SHARED_RANGES:
            tables = query_data_tables_list("", "t1", None, None, None)

        assert [t["table_id"] for t in tables] == ["9078", "9080"]
        # 列表 SELECT 需带 source_type 供 Python 结构判定
        assert "source_type" in cur.executed[0][0]


class TestAgentScopePlumbing:
    """检索/加载链路的身份透传与顺序约束"""

    def _make_agent(self, tenant_id="t1", subagent_id=None, agent_id="main"):
        return AnalysisAgent(
            llm_gateway=MagicMock(),
            analyzer=_loaded_analyzer(),
            analysis_id="t_scope",
            tables_metadata=[],
            tenant_id=tenant_id,
            subagent_id=subagent_id,
            agent_id=agent_id,
        )

    @pytest.mark.asyncio
    async def test_search_passes_agent_id_to_retriever(self):
        """search_data_tables 语义检索透传可信 agent_id（排名/LIMIT 前过滤在检索层）"""
        ag = self._make_agent(tenant_id="t1", subagent_id=None, agent_id="main")
        fake_retriever = MagicMock()
        fake_retriever.retrieve = AsyncMock(return_value=[])
        ag._retriever = fake_retriever

        await ag._handle_search_data_tables("销售")
        kwargs = fake_retriever.retrieve.await_args.kwargs
        assert kwargs["agent_id"] == "main"
        assert kwargs["source_type"] == "data-analysis-metadata"

    @pytest.mark.asyncio
    async def test_loaded_table_stays_usable_without_recheck(self):
        """进行中变更：已在内存的表不撤回——重复 load_table 直接确认，不再回查名单"""
        ag = self._make_agent()
        ag._loaded_table_ids.add("tbl_x")
        ag.tables_metadata = [{"table_id": "tbl_x", "table_name": "X", "columns": []}]
        with patch.object(ag, "_fetch_single_table_metadata") as mock_fetch:
            result = await ag._handle_load_table("tbl_x")
        assert result["success"] is True
        mock_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_unavailable_table_rejects_before_lineage(self):
        """顺序：回查在等价产物提示之前——不可用表的拒绝不携带产物信息"""
        ag = self._make_agent()
        ag._lineage_intercepted.clear()
        with patch.object(ag, "_fetch_single_table_metadata", return_value=None):
            result = await ag._handle_load_table("revoked")
        assert result["success"] is False
        assert "equivalent_outputs" not in result
        assert "intercepted" not in result

    def test_fetch_documents_metadata_scoped(self):
        """批量 metadata 回查与 search 同一可见范围（租户/共享 + 名单），无租户不查询"""
        ag = self._make_agent(tenant_id="t1", subagent_id=None, agent_id="main")
        rows = [
            {"id": 9078, "title": "订单", "summary": "s",
             "metadata": {"table_name": "orders", "columns": []}},
            {"id": 9079, "title": "库存", "summary": "s2",
             "metadata": {"table_name": "stock", "columns": []}},
        ]
        cur = _FakeResultRows(rows)
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(cur)):
            result = ag._fetch_documents_metadata([9078, 9079])
        assert 9078 in result and 9079 in result
        sql, params = cur.executed[0]
        assert "id IN (%s,%s)" in sql
        assert "AND (source_type = 'data-analysis-metadata'AND tenant_id = %s)" in sql
        assert _AGENT_SCOPE_SQL_MARK in sql

        # 无租户：不回查（不放大为全局视图）
        ag_anon = self._make_agent(tenant_id=None)
        with patch("src.db.database.get_db_connection") as mock_conn:
            assert ag_anon._fetch_documents_metadata([9078]) == {}
        mock_conn.assert_not_called()
