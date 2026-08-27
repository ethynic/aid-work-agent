"""
summarizer 模块单测

覆盖：
- get_lite_model：转发 LLMConfig.get_lite_model（解析逻辑在 gateway 单测覆盖）
- summarize_personal：mock LLM Gateway，验证 prompt 构造和返回解析
- summarize_team：mock LLM Gateway，验证团队摘要生成
"""

from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from src.reports.summarizer import (
    get_lite_model,
    summarize_personal,
    summarize_team,
)


class TestGetLiteModel:
    """get_lite_model 测试（解析逻辑在 test_llm_gateway_lite_model.py 覆盖）"""

    def test_returns_lite_model_when_configured(self):
        """lite_model 已配置时返回对应模型名"""
        mock_settings = MagicMock()
        mock_settings.llm.get_lite_model.return_value = "qwen3.7-flash"

        with patch("src.reports.summarizer.create_settings", return_value=mock_settings):
            assert get_lite_model() == "qwen3.7-flash"

    def test_fallback_to_main_model(self):
        """lite_model 未配置时 fallback 到主 model"""
        mock_settings = MagicMock()
        mock_settings.llm.get_lite_model.return_value = "deepseek-v4-pro"

        with patch("src.reports.summarizer.create_settings", return_value=mock_settings):
            assert get_lite_model() == "deepseek-v4-pro"

    def test_no_model_returns_empty(self):
        """无可用模型时返回空字符串"""
        mock_settings = MagicMock()
        mock_settings.llm.get_lite_model.return_value = ""

        with patch("src.reports.summarizer.create_settings", return_value=mock_settings):
            assert get_lite_model() == ""


class TestSummarizePersonal:
    """summarize_personal 测试"""

    @pytest.mark.asyncio
    async def test_success(self):
        """正常生成摘要"""
        mock_gateway = MagicMock()
        mock_gateway.chat_lite = AsyncMock(return_value={
            "content": "工作摘要：今天主要做了...",
            "usage": {"prompt_tokens": 500, "completion_tokens": 200},
        })

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_lite_model", return_value="deepseek-v4-flash"):
            content, usage = await summarize_personal(
                user_name="张三",
                department="销售部",
                report_date_str="2026-07-22",
                records=[
                    {"user_message": "查客户 A", "execution_details": "{}"},
                    {"user_message": "发邮件给 B", "execution_details": "{}"},
                ],
                report_type="daily",
            )

        assert "工作摘要" in content
        assert usage["prompt_tokens"] == 500
        assert usage["completion_tokens"] == 200
        # 验证 gateway.chat_lite 被调用（model 由 chat_lite 内部解析，调用方不传 model）
        mock_gateway.chat_lite.assert_called_once()
        call_kwargs = mock_gateway.chat_lite.call_args.kwargs

    @pytest.mark.asyncio
    async def test_empty_records(self):
        """无对话记录时仍能调用 LLM（prompt 中提示无记录）"""
        mock_gateway = MagicMock()
        mock_gateway.chat_lite = AsyncMock(return_value={
            "content": "今天暂无工作记录。",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        })

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_lite_model", return_value="deepseek-v4-flash"):
            content, usage = await summarize_personal(
                user_name="李四",
                department=None,
                report_date_str="2026-07-22",
                records=[],
                report_type="daily",
            )

        assert "暂无" in content
        assert usage["prompt_tokens"] == 100

    @pytest.mark.asyncio
    async def test_llm_failure_propagates(self):
        """LLM 调用失败时抛出异常"""
        mock_gateway = MagicMock()
        mock_gateway.chat_lite = AsyncMock(side_effect=RuntimeError("API 限流"))

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_lite_model", return_value="deepseek-v4-flash"):
            with pytest.raises(RuntimeError, match="API 限流"):
                await summarize_personal(
                    user_name="王五",
                    department=None,
                    report_date_str="2026-07-22",
                    records=[{"user_message": "test"}],
                    report_type="daily",
                )

    @pytest.mark.asyncio
    async def test_records_truncated_to_50(self):
        """超过 50 条记录时截断"""
        mock_gateway = MagicMock()
        mock_gateway.chat_lite = AsyncMock(return_value={
            "content": "摘要",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 300},
        })

        # 构造 60 条记录
        records = [{"user_message": f"任务 {i}", "execution_details": "{}"} for i in range(60)]

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_lite_model", return_value="deepseek-v4-flash"):
            await summarize_personal(
                user_name="测试",
                department=None,
                report_date_str="2026-07-22",
                records=records,
                report_type="daily",
            )

        # 验证 prompt 中只包含 50 条
        call_kwargs = mock_gateway.chat_lite.call_args.kwargs
        prompt_content = call_kwargs["messages"][0]["content"]
        # prompt 中应有 "1." 到 "50."，但不应有 "51."
        assert "1. 任务 0" in prompt_content
        assert "50. 任务 49" in prompt_content
        assert "51. 任务 50" not in prompt_content


class TestSummarizeTeam:
    """summarize_team 测试"""

    @pytest.mark.asyncio
    async def test_success(self):
        """正常生成团队摘要"""
        mock_gateway = MagicMock()
        mock_gateway.chat_lite = AsyncMock(return_value={
            "content": "团队工作成果：...",
            "usage": {"prompt_tokens": 800, "completion_tokens": 400},
        })

        member_dialogs = [
            {
                "user_id": "u1",
                "dialog_count": 8,
                "user_messages": ["查客户A", "发邮件给B", "录入订单"],
            },
            {
                "user_id": "u2",
                "dialog_count": 5,
                "user_messages": ["审核合同", "导出报表"],
            },
        ]

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_lite_model", return_value="deepseek-v4-flash"):
            content, usage = await summarize_team(
                tenant_name="某公司",
                report_date_str="2026-07-22",
                total_users=20,
                active_users=5,
                member_dialogs=member_dialogs,
                report_type="daily",
            )

        assert "团队工作成果" in content
        assert usage["prompt_tokens"] == 800
        assert usage["completion_tokens"] == 400
        mock_gateway.chat_lite.assert_called_once()
        call_kwargs = mock_gateway.chat_lite.call_args.kwargs
        # 验证 prompt 中包含成员对话片段
        prompt_content = call_kwargs["messages"][0]["content"]
        assert "【成员 1】" in prompt_content
        assert "查客户A" in prompt_content
        assert "【成员 2】" in prompt_content
        assert "审核合同" in prompt_content

    @pytest.mark.asyncio
    async def test_empty_member_dialogs(self):
        """无成员对话时仍能调用 LLM（提示无活跃成员）"""
        mock_gateway = MagicMock()
        mock_gateway.chat_lite = AsyncMock(return_value={
            "content": "本期无活跃成员。",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        })

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_lite_model", return_value="deepseek-v4-flash"):
            content, usage = await summarize_team(
                tenant_name="某公司",
                report_date_str="2026-07-22",
                total_users=20,
                active_users=0,
                member_dialogs=[],
                report_type="daily",
            )

        assert "无活跃成员" in content
        # prompt 中应显示"（无活跃成员）"
        call_kwargs = mock_gateway.chat_lite.call_args.kwargs
        prompt_content = call_kwargs["messages"][0]["content"]
        assert "（无活跃成员）" in prompt_content

    @pytest.mark.asyncio
    async def test_weekly_report_type(self):
        """weekly 报告类型正确生成"""
        mock_gateway = MagicMock()
        mock_gateway.chat_lite = AsyncMock(return_value={
            "content": "本周团队工作...",
            "usage": {"prompt_tokens": 800, "completion_tokens": 400},
        })

        member_dialogs = [
            {"user_id": "u1", "dialog_count": 8, "user_messages": ["任务1", "任务2"]},
        ]

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_lite_model", return_value="deepseek-v4-flash"):
            content, _ = await summarize_team(
                tenant_name="某公司",
                report_date_str="2026-07-22",
                total_users=20,
                active_users=5,
                member_dialogs=member_dialogs,
                report_type="weekly",
            )

        # prompt 中应包含"本周"和"周报"
        call_kwargs = mock_gateway.chat_lite.call_args.kwargs
        prompt_content = call_kwargs["messages"][0]["content"]
        assert "本周" in prompt_content
        assert "周报" in prompt_content

    @pytest.mark.asyncio
    async def test_member_dialogs_truncated_to_30(self):
        """超过 30 个成员时截断"""
        mock_gateway = MagicMock()
        mock_gateway.chat_lite = AsyncMock(return_value={
            "content": "团队摘要",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 300},
        })

        # 构造 35 个成员
        member_dialogs = [
            {"user_id": f"u{i}", "dialog_count": 5, "user_messages": [f"任务 {i}"]}
            for i in range(35)
        ]

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_lite_model", return_value="deepseek-v4-flash"):
            await summarize_team(
                tenant_name="某公司",
                report_date_str="2026-07-22",
                total_users=40,
                active_users=35,
                member_dialogs=member_dialogs,
                report_type="daily",
            )

        # 验证 prompt 中只包含 30 个成员
        call_kwargs = mock_gateway.chat_lite.call_args.kwargs
        prompt_content = call_kwargs["messages"][0]["content"]
        assert "【成员 1】" in prompt_content
        assert "【成员 30】" in prompt_content
        assert "【成员 31】" not in prompt_content
