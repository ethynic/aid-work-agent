"""
工作成果复盘任务单元测试

覆盖场景：
- _parse_review_response：JSON 解析（正常 / markdown 包裹 / 非法 JSON / 空内容）
- _load_review_prompt：提示词文件加载 + 内置兜底
- _format_session_for_review：会话消息格式化
- _has_file_outcome：文件型成果存在性检查
- run_daily_review：主流程（mock 依赖）
"""

from datetime import date
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from src.reports.work_outcome_review import (
    _parse_review_response,
    _load_review_prompt,
    _format_session_for_review,
    _has_file_outcome,
    SessionInfo,
    run_daily_review,
)


pytestmark = [pytest.mark.tools]


# ============== _parse_review_response ==============

class TestParseReviewResponse:
    """_parse_review_response JSON 解析测试"""

    def test_empty_content(self):
        assert _parse_review_response("") == []
        assert _parse_review_response(None) == []

    def test_valid_json(self):
        content = '''
        {
          "outcomes": [
            {
              "summary": "修改客户李明的订单收货地址为上海",
              "outcome_type": "action",
              "metadata": {"customer": "李明", "order_id": "ORD001"},
              "chat_record_id": 123,
              "confidence": 0.9
            },
            {
              "summary": "建议退货并补偿200元",
              "outcome_type": "decision",
              "metadata": {},
              "chat_record_id": 124,
              "confidence": 0.85
            }
          ]
        }
        '''
        result = _parse_review_response(content)
        assert len(result) == 2
        assert result[0]["summary"] == "修改客户李明的订单收货地址为上海"
        assert result[0]["outcome_type"] == "action"
        assert result[0]["confidence"] == 0.9
        assert result[1]["outcome_type"] == "decision"

    def test_markdown_code_block(self):
        """响应被 ```json ... ``` 包裹"""
        content = '''```json
        {"outcomes": [{"summary": "test", "outcome_type": "other", "confidence": 0.7}]}
        ```'''
        result = _parse_review_response(content)
        assert len(result) == 1
        assert result[0]["summary"] == "test"
        assert result[0]["outcome_type"] == "other"

    def test_invalid_json(self):
        """非法 JSON 返回空列表"""
        result = _parse_review_response("not a json")
        assert result == []

    def test_empty_outcomes(self):
        content = '{"outcomes": []}'
        result = _parse_review_response(content)
        assert result == []

    def test_missing_summary_skipped(self):
        """summary 为空的条目被跳过"""
        content = '''{
          "outcomes": [
            {"summary": "", "outcome_type": "action", "confidence": 0.9},
            {"summary": "valid", "outcome_type": "action", "confidence": 0.9}
          ]
        }'''
        result = _parse_review_response(content)
        assert len(result) == 1
        assert result[0]["summary"] == "valid"

    def test_invalid_outcome_type_defaults_to_other(self):
        """非法 outcome_type 默认为 other"""
        content = '''{
          "outcomes": [
            {"summary": "test", "outcome_type": "invalid_type", "confidence": 0.9}
          ]
        }'''
        result = _parse_review_response(content)
        assert len(result) == 1
        assert result[0]["outcome_type"] == "other"

    def test_missing_confidence_defaults_to_0_5(self):
        """未传 confidence 默认 0.5"""
        content = '''{
          "outcomes": [
            {"summary": "test", "outcome_type": "action"}
          ]
        }'''
        result = _parse_review_response(content)
        assert len(result) == 1
        assert result[0]["confidence"] == 0.5

    def test_invalid_confidence_defaults_to_0_5(self):
        """非法 confidence 默认 0.5"""
        content = '''{
          "outcomes": [
            {"summary": "test", "outcome_type": "action", "confidence": "high"}
          ]
        }'''
        result = _parse_review_response(content)
        assert result[0]["confidence"] == 0.5

    def test_outcomes_not_list_returns_empty(self):
        """outcomes 不是列表返回空"""
        content = '{"outcomes": "not a list"}'
        result = _parse_review_response(content)
        assert result == []

    def test_outcome_not_dict_skipped(self):
        """非 dict 的 outcome 被跳过"""
        content = '{"outcomes": ["str", 123, null, {"summary": "valid", "confidence": 0.9}]}'
        result = _parse_review_response(content)
        assert len(result) == 1
        assert result[0]["summary"] == "valid"


# ============== _load_review_prompt ==============

class TestLoadReviewPrompt:
    """_load_review_prompt 提示词加载测试"""

    def test_loads_from_file(self):
        """成功从文件加载提示词"""
        prompt = _load_review_prompt()
        # 提示词应包含关键内容
        assert "工作成果复盘" in prompt or "action" in prompt or "decision" in prompt
        assert "outcomes" in prompt

    def test_fallback_on_file_error(self):
        """文件加载失败时返回内置默认"""
        with patch("src.reports.work_outcome_review._PROMPT_FILE") as mock_path:
            mock_path.read_text.side_effect = FileNotFoundError("no such file")
            prompt = _load_review_prompt()
            assert "工作成果复盘助手" in prompt
            assert "outcomes" in prompt


# ============== _format_session_for_review ==============

class TestFormatSessionForReview:
    """_format_session_for_review 会话格式化测试"""

    def test_basic_format(self):
        """基本格式化：包含会话 ID、子智能体、渠道、对话记录"""
        session = SessionInfo(
            session_id="s_abc",
            tenant_id="t1",
            user_id="u1",
            subagent_id="trade-specialist",
            channel="web",
        )
        messages = [
            {
                "id": 101,
                "user_message": "帮我修改客户李明的订单地址",
                "assistant_message": "已修改订单 ORD001 的收货地址为上海",
                "created_at": None,
            },
        ]
        result = _format_session_for_review(messages, session)
        assert "s_abc" in result
        assert "trade-specialist" in result
        assert "web" in result
        assert "record_id=101" in result
        assert "帮我修改客户李明的订单地址" in result
        assert "已修改订单 ORD001" in result

    def test_truncates_long_messages(self):
        """超长消息被截断"""
        session = SessionInfo(
            session_id="s_long",
            tenant_id="t1",
            user_id="u1",
            subagent_id=None,
            channel="web",
        )
        long_msg = "x" * 1000
        messages = [
            {"id": 1, "user_message": long_msg, "assistant_message": "", "created_at": None},
        ]
        result = _format_session_for_review(messages, session)
        # 截断后应明显短于原长
        assert "x" * 1000 not in result
        # _MAX_MESSAGE_CHARS = 500
        assert "x" * 500 in result

    def test_no_subagent_shows_main(self):
        """无 subagent_id 显示为主智能体"""
        session = SessionInfo(
            session_id="s1",
            tenant_id="t1",
            user_id="u1",
            subagent_id=None,
            channel="web",
        )
        messages = []
        result = _format_session_for_review(messages, session)
        assert "主智能体" in result


# ============== _has_file_outcome ==============

class TestHasFileOutcome:
    """_has_file_outcome 检查测试"""

    def test_returns_true_when_exists(self):
        with patch("src.reports.work_outcome_db.WorkOutcomeDB.exists_by_session_and_type") as mock_exists:
            mock_exists.return_value = True
            assert _has_file_outcome("s1") is True

    def test_returns_false_when_not_exists(self):
        with patch("src.reports.work_outcome_db.WorkOutcomeDB.exists_by_session_and_type") as mock_exists:
            mock_exists.return_value = False
            assert _has_file_outcome("s1") is False


# ============== run_daily_review ==============

class TestRunDailyReview:
    """run_daily_review 主流程测试"""

    @pytest.mark.asyncio
    async def test_returns_batch_id(self):
        """返回批次 ID"""
        with (
            patch("src.reports.work_outcome_review._list_active_sessions_on_date", new_callable=AsyncMock),
            patch("src.reports.work_outcome_review._has_file_outcome") as mock_has,
            patch("src.reports.work_outcome_review._review_session_with_llm", new_callable=AsyncMock),
            patch("src.reports.work_outcome_db.WorkOutcomeDB.create"),
        ):
            from src.reports.work_outcome_review import _list_active_sessions_on_date
            _list_active_sessions_on_date.return_value = []
            mock_has.return_value = False

            batch_id = await run_daily_review(date(2026, 7, 28))

            assert batch_id.startswith("rb_20260728_")
            assert len(batch_id) == len("rb_20260728_") + 8

    @pytest.mark.asyncio
    async def test_filters_sessions_with_file_outcome(self):
        """有文件型成果的会话被过滤"""
        sessions_with_file = SessionInfo(
            session_id="s_with_file",
            tenant_id="t1",
            user_id="u1",
            subagent_id=None,
            channel="web",
        )
        sessions_without_file = SessionInfo(
            session_id="s_no_file",
            tenant_id="t1",
            user_id="u1",
            subagent_id=None,
            channel="web",
        )

        async def mock_list(*args, **kwargs):
            return [sessions_with_file, sessions_without_file]

        with (
            patch("src.reports.work_outcome_review._list_active_sessions_on_date", new_callable=AsyncMock, side_effect=mock_list),
            patch("src.reports.work_outcome_review._has_file_outcome") as mock_has,
            patch("src.reports.work_outcome_review._review_session_with_llm", new_callable=AsyncMock) as mock_review,
            patch("src.reports.work_outcome_db.WorkOutcomeDB.create"),
        ):
            mock_has.side_effect = lambda sid: sid == "s_with_file"
            mock_review.return_value = []

            await run_daily_review(date(2026, 7, 28))

            # 只有 s_no_file 被复盘
            assert mock_review.call_count == 1
            called_session = mock_review.call_args[0][0]
            assert called_session.session_id == "s_no_file"

    @pytest.mark.asyncio
    async def test_no_outcomes_when_empty_sessions(self):
        """无候选会话时返回批次 ID，total_outcomes=0"""
        with (
            patch("src.reports.work_outcome_review._list_active_sessions_on_date", new_callable=AsyncMock) as mock_list,
            patch("src.reports.work_outcome_review._has_file_outcome") as mock_has,
        ):
            mock_list.return_value = []
            mock_has.return_value = False

            batch_id = await run_daily_review(date(2026, 7, 28))
            assert batch_id.startswith("rb_20260728_")
