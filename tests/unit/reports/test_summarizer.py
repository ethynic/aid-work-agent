"""
summarizer 模块单测

覆盖：
- get_report_model：fallback 到主模型
- summarize_personal：mock LLM Gateway，验证 prompt 构造和返回解析
- summarize_team：mock LLM Gateway，验证团队摘要生成
"""

from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from src.reports.summarizer import (
    get_report_model,
    summarize_personal,
    summarize_team,
)


class TestGetReportModel:
    """get_report_model 测试"""

    def test_returns_report_model_when_configured(self):
        """report_model 已配置时返回 report_model"""
        mock_settings = MagicMock()
        mock_settings.llm.provider = "deepseek"
        mock_cfg = MagicMock()
        mock_cfg.get_report_model.return_value = "deepseek-v4-flash"
        mock_settings.llm.deepseek = mock_cfg

        with patch("src.reports.summarizer.create_settings", return_value=mock_settings):
            assert get_report_model() == "deepseek-v4-flash"

    def test_fallback_to_main_model(self):
        """report_model 未配置时 fallback 到主 model"""
        mock_settings = MagicMock()
        mock_settings.llm.provider = "deepseek"
        mock_cfg = MagicMock()
        # 模拟 report_model 为空，get_report_model fallback 到 model
        mock_cfg.get_report_model.return_value = "deepseek-v4-pro"
        mock_settings.llm.deepseek = mock_cfg

        with patch("src.reports.summarizer.create_settings", return_value=mock_settings):
            assert get_report_model() == "deepseek-v4-pro"

    def test_unknown_provider_returns_empty(self):
        """未知的 provider 返回空字符串"""
        mock_settings = MagicMock()
        mock_settings.llm.provider = "unknown_provider"
        mock_settings.llm.unknown_provider = None

        with patch("src.reports.summarizer.create_settings", return_value=mock_settings):
            assert get_report_model() == ""


class TestSummarizePersonal:
    """summarize_personal 测试"""

    @pytest.mark.asyncio
    async def test_success(self):
        """正常生成摘要"""
        mock_gateway = MagicMock()
        mock_gateway.chat = AsyncMock(return_value={
            "content": "工作摘要：今天主要做了...",
            "usage": {"prompt_tokens": 500, "completion_tokens": 200},
        })

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_report_model", return_value="deepseek-v4-flash"):
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
        # 验证 gateway.chat 被调用，且传了 model=deepseek-v4-flash
        mock_gateway.chat.assert_called_once()
        call_kwargs = mock_gateway.chat.call_args.kwargs
        assert call_kwargs["model"] == "deepseek-v4-flash"

    @pytest.mark.asyncio
    async def test_empty_records(self):
        """无对话记录时仍能调用 LLM（prompt 中提示无记录）"""
        mock_gateway = MagicMock()
        mock_gateway.chat = AsyncMock(return_value={
            "content": "今天暂无工作记录。",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        })

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_report_model", return_value="deepseek-v4-flash"):
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
        mock_gateway.chat = AsyncMock(side_effect=RuntimeError("API 限流"))

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_report_model", return_value="deepseek-v4-flash"):
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
        mock_gateway.chat = AsyncMock(return_value={
            "content": "摘要",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 300},
        })

        # 构造 60 条记录
        records = [{"user_message": f"任务 {i}", "execution_details": "{}"} for i in range(60)]

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_report_model", return_value="deepseek-v4-flash"):
            await summarize_personal(
                user_name="测试",
                department=None,
                report_date_str="2026-07-22",
                records=records,
                report_type="daily",
            )

        # 验证 prompt 中只包含 50 条
        call_kwargs = mock_gateway.chat.call_args.kwargs
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
        mock_gateway.chat = AsyncMock(return_value={
            "content": "团队工作成果：...",
            "usage": {"prompt_tokens": 800, "completion_tokens": 400},
        })

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_report_model", return_value="deepseek-v4-flash"):
            content, usage = await summarize_team(
                tenant_name="某公司",
                report_date_str="2026-07-22",
                total_users=20,
                active_users=5,
                personal_summaries=["成员1：...", "成员2：..."],
                report_type="daily",
            )

        assert "团队工作成果" in content
        assert usage["prompt_tokens"] == 800
        assert usage["completion_tokens"] == 400
        mock_gateway.chat.assert_called_once()
        call_kwargs = mock_gateway.chat.call_args.kwargs
        assert call_kwargs["model"] == "deepseek-v4-flash"

    @pytest.mark.asyncio
    async def test_empty_summaries(self):
        """无成员摘要时仍能调用 LLM"""
        mock_gateway = MagicMock()
        mock_gateway.chat = AsyncMock(return_value={
            "content": "本期无活跃成员。",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        })

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_report_model", return_value="deepseek-v4-flash"):
            content, usage = await summarize_team(
                tenant_name="某公司",
                report_date_str="2026-07-22",
                total_users=20,
                active_users=0,
                personal_summaries=[],
                report_type="daily",
            )

        assert "无活跃成员" in content

    @pytest.mark.asyncio
    async def test_weekly_report_type(self):
        """weekly 报告类型正确生成"""
        mock_gateway = MagicMock()
        mock_gateway.chat = AsyncMock(return_value={
            "content": "本周团队工作...",
            "usage": {"prompt_tokens": 800, "completion_tokens": 400},
        })

        with patch("src.reports.summarizer.llm_gateway", mock_gateway), \
             patch("src.reports.summarizer.get_report_model", return_value="deepseek-v4-flash"):
            content, _ = await summarize_team(
                tenant_name="某公司",
                report_date_str="2026-07-22",
                total_users=20,
                active_users=5,
                personal_summaries=["成员1：..."],
                report_type="weekly",
            )

        # prompt 中应包含"本周"
        call_kwargs = mock_gateway.chat.call_args.kwargs
        prompt_content = call_kwargs["messages"][0]["content"]
        assert "本周" in prompt_content
        assert "周报" in prompt_content
