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
        """白名单包含全部 14 个方法"""
        from src.tools.data_analysis.analysis_tools_schema import ALLOWED_METHODS
        expected = {"search_data_tables", "list_data_tables", "load_table", "describe",
                    "query", "aggregate", "merge", "pivot", "calculate",
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
        """工具定义数量为 14"""
        from src.tools.data_analysis.analysis_tools_schema import ANALYSIS_TOOLS
        assert len(ANALYSIS_TOOLS) == 14

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
        assert names == {"search_data_tables", "list_data_tables", "load_table", "describe",
                         "query", "aggregate", "merge", "pivot", "calculate",
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

    def test_no_tenant_returns_empty_scope(self):
        """无 tenant_id → 空 SQL、空参数（无租户过滤）"""
        ag = self._make_agent(tenant_id=None, subagent_id="travel-quote")
        sql, params = ag._build_tenant_scope()
        assert sql == ""
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
