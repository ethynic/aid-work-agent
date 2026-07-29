"""
generator 模块单测

覆盖：
- ReportGenerator.generate_personal：mock aggregator/summarizer/billing/db，验证主流程
- _personal_source_type：report_type 到 source_type 的映射
- _team_source_type：团队报告 source_type 映射
- 计费链路：写 chat_records 的 source_type 和 credit_cost 正确
"""

from datetime import date
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from src.reports.generator import ReportGenerator, ReportScope
from src.saas.models.enums import ChatRecordSourceType


class TestSourceTypeMapping:
    """report_type 到 source_type 的映射测试"""

    def test_personal_daily(self):
        gen = ReportGenerator()
        assert gen._personal_source_type("daily") == ChatRecordSourceType.REPORT_PERSONAL.value

    def test_personal_weekly(self):
        gen = ReportGenerator()
        assert gen._personal_source_type("weekly") == ChatRecordSourceType.REPORT_PERSONAL_WEEKLY.value

    def test_personal_monthly(self):
        gen = ReportGenerator()
        assert gen._personal_source_type("monthly") == ChatRecordSourceType.REPORT_PERSONAL_MONTHLY.value

    def test_team_daily(self):
        gen = ReportGenerator()
        assert gen._team_source_type("daily") == ChatRecordSourceType.REPORT_TEAM.value

    def test_team_weekly(self):
        gen = ReportGenerator()
        assert gen._team_source_type("weekly") == ChatRecordSourceType.REPORT_TEAM_WEEKLY.value

    def test_team_monthly(self):
        gen = ReportGenerator()
        assert gen._team_source_type("monthly") == ChatRecordSourceType.REPORT_TEAM_MONTHLY.value


class TestGeneratePersonal:
    """generate_personal 主流程测试"""

    @pytest.mark.asyncio
    async def test_success_with_records(self):
        """有对话记录时正常生成报告"""
        gen = ReportGenerator()

        # mock aggregator
        agg_result = {
            "records": [{"user_message": "查客户", "duration_ms": 5000, "execution_details": "{}"}],
            "dialog_count": 1,
            "credit_cost": 10,
            "subagent_distribution": {"trade-specialist": 1},
            "tool_distribution": {"query_customer": 1},
            "source_distribution": {"chat": 1},
            "saved_minutes": 2.9,
            "time_range": {"start": "2026-07-22T00:00:00", "end": "2026-07-23T00:00:00"},
        }

        # mock summarizer
        summary_text = "工作摘要：今天查了客户"
        usage = {"prompt_tokens": 500, "completion_tokens": 200}

        # mock billing
        credit_cost = 5

        # mock db
        upsert_result = {"id": 1, "report_id": "wdr_abc123"}

        with patch("src.reports.generator.aggregate_personal", return_value=agg_result), \
             patch("src.reports.generator.summarize_personal", new=AsyncMock(return_value=(summary_text, usage))), \
             patch("src.reports.generator.calculate_credit_cost", return_value=credit_cost), \
             patch("src.reports.generator.WorkDailyReportDB.upsert", return_value=upsert_result) as mock_upsert, \
             patch("src.reports.generator.ChatRecordDB.create") as mock_create_record, \
             patch("src.reports.generator.get_report_model", return_value="deepseek-v4-flash"):
            result = await gen.generate_personal(
                tenant_id="t1",
                user_id="u1",
                user_name="张三",
                department=None,
                report_date=date(2026, 7, 22),
                report_type="daily",
            )

        # 验证返回结构
        assert result["report_id"] == "wdr_abc123"
        assert result["scope"] == "personal"
        assert result["report_type"] == "daily"
        assert result["report_date"] == "2026-07-22"
        assert result["summary_text"] == summary_text
        assert result["credit_cost"] == 5
        assert result["model"] == "deepseek-v4-flash"

        # 验证 work_daily_reports UPSERT 被调用
        mock_upsert.assert_called_once()
        upsert_kwargs = mock_upsert.call_args.kwargs
        assert upsert_kwargs["tenant_id"] == "t1"
        assert upsert_kwargs["scope"] == "personal"
        assert upsert_kwargs["report_type"] == "daily"
        assert upsert_kwargs["credit_cost"] == 5
        assert upsert_kwargs["model"] == "deepseek-v4-flash"

        # 验证 chat_records 写入（计费链路）
        mock_create_record.assert_called_once()
        create_kwargs = mock_create_record.call_args.kwargs
        assert create_kwargs["tenant_id"] == "t1"
        assert create_kwargs["user_id"] == "u1"
        assert create_kwargs["source_type"] == "report_personal"
        assert create_kwargs["credit_cost"] == 5
        assert create_kwargs["model"] == "deepseek-v4-flash"
        assert create_kwargs["prompt_tokens"] == 500
        assert create_kwargs["completion_tokens"] == 200
        # session_id 格式：report:{tenant_id}:{user_id}:{date}:{type}
        assert create_kwargs["session_id"] == "report:t1:u1:2026-07-22:daily"

    @pytest.mark.asyncio
    async def test_empty_records(self):
        """无对话记录时生成空报告，不调 LLM，不写 chat_records"""
        gen = ReportGenerator()

        agg_result = {
            "records": [],
            "dialog_count": 0,
            "credit_cost": 0,
            "subagent_distribution": {},
            "tool_distribution": {},
            "source_distribution": {},
            "saved_minutes": 0.0,
            "time_range": {"start": "2026-07-22T00:00:00", "end": "2026-07-23T00:00:00"},
        }

        upsert_result = {"id": 1, "report_id": "wdr_empty"}

        with patch("src.reports.generator.aggregate_personal", return_value=agg_result), \
             patch("src.reports.generator.summarize_personal") as mock_summarize, \
             patch("src.reports.generator.WorkDailyReportDB.upsert", return_value=upsert_result) as mock_upsert, \
             patch("src.reports.generator.ChatRecordDB.create") as mock_create_record, \
             patch("src.reports.generator.get_report_model", return_value="deepseek-v4-flash"):
            result = await gen.generate_personal(
                tenant_id="t1",
                user_id="u1",
                user_name="张三",
                department=None,
                report_date=date(2026, 7, 22),
                report_type="daily",
            )

        # 不应调 LLM
        mock_summarize.assert_not_called()
        # 不应写 chat_records
        mock_create_record.assert_not_called()
        # 但应落库空报告
        mock_upsert.assert_called_once()
        assert result["credit_cost"] == 0
        assert "暂无对话记录" in result["summary_text"]

    @pytest.mark.asyncio
    async def test_chat_record_failure_does_not_fail_report(self):
        """chat_records 写入失败不影响报告本身"""
        gen = ReportGenerator()

        agg_result = {
            "records": [{"user_message": "test", "duration_ms": 5000, "execution_details": "{}"}],
            "dialog_count": 1,
            "credit_cost": 10,
            "subagent_distribution": {},
            "tool_distribution": {},
            "source_distribution": {"chat": 1},
            "saved_minutes": 2.9,
            "time_range": {"start": "2026-07-22T00:00:00", "end": "2026-07-23T00:00:00"},
        }

        with patch("src.reports.generator.aggregate_personal", return_value=agg_result), \
             patch("src.reports.generator.summarize_personal", new=AsyncMock(return_value=("摘要", {"prompt_tokens": 100, "completion_tokens": 50}))), \
             patch("src.reports.generator.calculate_credit_cost", return_value=3), \
             patch("src.reports.generator.WorkDailyReportDB.upsert", return_value={"id": 1, "report_id": "wdr_x"}), \
             patch("src.reports.generator.ChatRecordDB.create", side_effect=RuntimeError("DB 错误")), \
             patch("src.reports.generator.get_report_model", return_value="deepseek-v4-flash"):
            # 不应抛出异常
            result = await gen.generate_personal(
                tenant_id="t1",
                user_id="u1",
                user_name="张三",
                department=None,
                report_date=date(2026, 7, 22),
                report_type="daily",
            )

        # 报告仍应正常返回
        assert result["report_id"] == "wdr_x"
        assert result["summary_text"] == "摘要"

    @pytest.mark.asyncio
    async def test_regenerate_increments_count(self):
        """is_regenerate=True 时 UPSERT 传 is_regenerate=True"""
        gen = ReportGenerator()

        agg_result = {
            "records": [{"user_message": "test", "duration_ms": 5000, "execution_details": "{}"}],
            "dialog_count": 1,
            "credit_cost": 10,
            "subagent_distribution": {},
            "tool_distribution": {},
            "source_distribution": {"chat": 1},
            "saved_minutes": 2.9,
            "time_range": {"start": "2026-07-22T00:00:00", "end": "2026-07-23T00:00:00"},
        }

        with patch("src.reports.generator.aggregate_personal", return_value=agg_result), \
             patch("src.reports.generator.summarize_personal", new=AsyncMock(return_value=("摘要", {"prompt_tokens": 100, "completion_tokens": 50}))), \
             patch("src.reports.generator.calculate_credit_cost", return_value=3), \
             patch("src.reports.generator.WorkDailyReportDB.upsert", return_value={"id": 1, "report_id": "wdr_x"}) as mock_upsert, \
             patch("src.reports.generator.ChatRecordDB.create"), \
             patch("src.reports.generator.get_report_model", return_value="deepseek-v4-flash"):
            await gen.generate_personal(
                tenant_id="t1",
                user_id="u1",
                user_name="张三",
                department=None,
                report_date=date(2026, 7, 22),
                report_type="daily",
                is_regenerate=True,
            )

        upsert_kwargs = mock_upsert.call_args.kwargs
        assert upsert_kwargs["is_regenerate"] is True


class TestGenerateTeam:
    """generate_team 主流程测试"""

    @pytest.mark.asyncio
    async def test_success_with_active_users(self):
        """有活跃成员时正常生成团队报告"""
        gen = ReportGenerator()

        agg_result = {
            "user_stats": [{"user_id": "u1", "dialog_count": 10, "credit_cost": 50, "saved_minutes": 30.0}],
            "total_dialog_count": 10,
            "total_credit_cost": 50,
            "total_saved_minutes": 30.0,
            "active_user_count": 1,
            "source_distribution": {"chat": 10},
            "members_dialogs": [
                {"user_id": "u1", "dialog_count": 10, "user_messages": ["查客户A", "发邮件"]},
            ],
            "input_truncated": False,
            "input_member_count": 1,
            "input_dialog_count": 2,
            "input_char_count": 50,
            "time_range": {"start": "2026-07-22T00:00:00", "end": "2026-07-23T00:00:00"},
        }

        with patch("src.reports.generator.aggregate_team", return_value=agg_result), \
             patch("src.reports.generator.summarize_team", new=AsyncMock(return_value=("团队摘要", {"prompt_tokens": 800, "completion_tokens": 400}))) as mock_summarize, \
             patch("src.reports.generator.calculate_credit_cost", return_value=8), \
             patch("src.reports.generator.WorkDailyReportDB.upsert", return_value={"id": 1, "report_id": "wdr_team1"}) as mock_upsert, \
             patch("src.reports.generator.ChatRecordDB.create") as mock_create_record, \
             patch("src.reports.generator.get_report_model", return_value="deepseek-v4-flash"):
            result = await gen.generate_team(
                tenant_id="t1",
                tenant_name="某公司",
                total_users=20,
                report_date=date(2026, 7, 22),
                report_type="daily",
            )

        # 验证返回
        assert result["scope"] == "team"
        assert result["report_type"] == "daily"
        assert result["summary_text"] == "团队摘要"
        assert result["credit_cost"] == 8
        assert result["metrics"]["active_user_count"] == 1
        assert result["metrics"]["active_rate"] == 0.05  # 1/20
        # 采样元数据应进入 metrics
        assert result["metrics"]["input_truncated"] is False
        assert result["metrics"]["input_member_count"] == 1
        assert result["metrics"]["input_dialog_count"] == 2

        # 验证 LLM 被调用，且 member_dialogs 作为参数传入
        mock_summarize.assert_called_once()
        summarize_kwargs = mock_summarize.call_args.kwargs
        assert "member_dialogs" in summarize_kwargs
        assert len(summarize_kwargs["member_dialogs"]) == 1
        # 验证 chat_records 写入，source_type=report_team
        mock_create_record.assert_called_once()
        create_kwargs = mock_create_record.call_args.kwargs
        assert create_kwargs["source_type"] == "report_team"
        assert create_kwargs["user_id"] is None  # 团队报告 user_id 为空
        # execution_details 应包含采样元数据
        ed = create_kwargs["execution_details"]
        assert ed["input_truncated"] is False
        assert ed["input_member_count"] == 1
        assert ed["input_dialog_count"] == 2

    @pytest.mark.asyncio
    async def test_no_active_users(self):
        """无活跃成员时不调 LLM，credit_cost=0，但仍写 chat_records 留痕"""
        gen = ReportGenerator()

        agg_result = {
            "user_stats": [],
            "total_dialog_count": 0,
            "total_credit_cost": 0,
            "total_saved_minutes": 0.0,
            "active_user_count": 0,
            "source_distribution": {},
            "members_dialogs": [],
            "input_truncated": False,
            "input_member_count": 0,
            "input_dialog_count": 0,
            "input_char_count": 0,
            "time_range": {"start": "2026-07-22T00:00:00", "end": "2026-07-23T00:00:00"},
        }

        with patch("src.reports.generator.aggregate_team", return_value=agg_result), \
             patch("src.reports.generator.summarize_team") as mock_summarize, \
             patch("src.reports.generator.WorkDailyReportDB.upsert", return_value={"id": 1, "report_id": "wdr_team_empty"}), \
             patch("src.reports.generator.ChatRecordDB.create") as mock_create_record, \
             patch("src.reports.generator.get_report_model", return_value="deepseek-v4-flash"):
            result = await gen.generate_team(
                tenant_id="t1",
                tenant_name="某公司",
                total_users=20,
                report_date=date(2026, 7, 22),
                report_type="daily",
            )

        # 不应调 LLM
        mock_summarize.assert_not_called()
        # 仍应写 chat_records（credit_cost=0，留痕用量页）
        mock_create_record.assert_called_once()
        create_kwargs = mock_create_record.call_args.kwargs
        assert create_kwargs["credit_cost"] == 0
        assert create_kwargs["prompt_tokens"] == 0
        assert create_kwargs["completion_tokens"] == 0
        assert result["credit_cost"] == 0
        assert "无活跃成员" in result["summary_text"]

    @pytest.mark.asyncio
    async def test_truncated_metadata_propagated(self):
        """采样元数据 input_truncated=True 时正确传递到 metrics 和 chat_records"""
        gen = ReportGenerator()

        agg_result = {
            "user_stats": [
                {"user_id": f"u{i}", "dialog_count": 50 - i, "credit_cost": 10, "saved_minutes": 30.0}
                for i in range(30)
            ],
            "total_dialog_count": 1000,
            "total_credit_cost": 500,
            "total_saved_minutes": 3000.0,
            "active_user_count": 30,
            "source_distribution": {"chat": 1000},
            "members_dialogs": [
                {"user_id": "u0", "dialog_count": 50, "user_messages": [f"msg {i}" for i in range(50)]},
            ],
            "input_truncated": True,
            "input_member_count": 1,
            "input_dialog_count": 50,
            "input_char_count": 80000,
            "time_range": {"start": "2026-07-01T00:00:00", "end": "2026-08-01T00:00:00"},
        }

        with patch("src.reports.generator.aggregate_team", return_value=agg_result), \
             patch("src.reports.generator.summarize_team", new=AsyncMock(return_value=("团队月报摘要", {"prompt_tokens": 8000, "completion_tokens": 1000}))), \
             patch("src.reports.generator.calculate_credit_cost", return_value=20), \
             patch("src.reports.generator.WorkDailyReportDB.upsert", return_value={"id": 1, "report_id": "wdr_team_trunc"}), \
             patch("src.reports.generator.ChatRecordDB.create") as mock_create_record, \
             patch("src.reports.generator.get_report_model", return_value="deepseek-v4-flash"):
            result = await gen.generate_team(
                tenant_id="t1",
                tenant_name="某公司",
                total_users=30,
                report_date=date(2026, 7, 1),
                report_type="monthly",
            )

        # metrics 应包含采样元数据
        assert result["metrics"]["input_truncated"] is True
        assert result["metrics"]["input_member_count"] == 1
        assert result["metrics"]["input_dialog_count"] == 50
        # chat_records 的 execution_details 应包含采样元数据
        create_kwargs = mock_create_record.call_args.kwargs
        ed = create_kwargs["execution_details"]
        assert ed["input_truncated"] is True
        assert ed["input_member_count"] == 1
        assert ed["input_dialog_count"] == 50
        assert ed["input_char_count"] == 80000
        # user_message 应包含"已截断"标记
        assert "已截断" in create_kwargs["user_message"]
