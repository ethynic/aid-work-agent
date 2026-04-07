"""
子智能体澄清流程测试

验证 SubagentTaskRecord 状态管理和澄清流程
"""

import pytest

pytestmark = pytest.mark.agent
from unittest.mock import AsyncMock, MagicMock, patch

from src.subagents.protocol import SubagentTaskRecord, get_task_record_key
from src.models.subagent import SubagentTaskStatus


class TestSubagentTaskRecord:
    """测试 SubagentTaskRecord 的澄清状态管理"""

    def test_initial_status_is_pending(self):
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        assert record.status == SubagentTaskStatus.PENDING

    def test_request_clarification_sets_status(self):
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        record.request_clarification("请提供邮箱地址")
        assert record.status == SubagentTaskStatus.CLARIFYING
        assert record.clarification_request == "请提供邮箱地址"

    def test_answer_clarification_resumes_running(self):
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        record.request_clarification("请提供邮箱地址")
        assert record.is_clarifying()

        record.answer_clarification("test@example.com")
        assert record.status == SubagentTaskStatus.RUNNING
        assert record.clarification_answer == "test@example.com"
        assert not record.is_clarifying()

    def test_is_clarifying(self):
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        assert not record.is_clarifying()
        record.request_clarification("问题？")
        assert record.is_clarifying()

    def test_clarifying_is_terminal(self):
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        assert not record.is_terminal()
        record.request_clarification("问题？")
        assert record.is_terminal()

    def test_completed_is_terminal(self):
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        record.complete(result={"key": "value"}, summary="完成")
        assert record.is_terminal()

    def test_failed_is_terminal(self):
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        record.fail("执行失败")
        assert record.is_terminal()

    def test_running_is_not_terminal(self):
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        assert not record.is_terminal()


class TestSubagentExecutorClarification:
    """测试 SubagentExecutor 的澄清处理"""

    @pytest.mark.asyncio
    async def test_handle_clarification_returns_false_when_not_clarifying(self):
        from src.subagents.executor import SubagentExecutor

        executor = SubagentExecutor(
            session_memory=MagicMock(),
            registry=MagicMock(),
        )
        result = await executor.handle_clarification("non_existent_id", "answer")
        assert result is False

    def test_get_pending_clarification_returns_none_when_not_clarifying(self):
        from src.subagents.executor import SubagentExecutor

        executor = SubagentExecutor(
            session_memory=MagicMock(),
            registry=MagicMock(),
        )
        result = executor.get_pending_clarification("non_existent_id")
        assert result is None

    def test_get_pending_clarification_returns_record_when_clarifying(self):
        from src.subagents.executor import SubagentExecutor

        executor = SubagentExecutor(
            session_memory=MagicMock(),
            registry=MagicMock(),
        )
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        record.request_clarification("请提供邮箱地址")
        executor._task_records = {"exec_001": record}

        result = executor.get_pending_clarification("exec_001")
        assert result is not None
        assert result.clarification_request == "请提供邮箱地址"


class TestMasterAgentPendingClarification:
    """测试主 Agent 的 pending clarification 管理"""

    @patch("src.core.agent.llm_gateway")
    @patch("src.core.agent.ShortTermMemory")
    @patch("src.core.agent.ToolRegistry")
    @patch("src.core.agent.ToolExecutor")
    @patch("src.core.agent.PlanManager")
    def test_pending_clarifications_initialized(self, mock_pm, mock_te, mock_tr, mock_stm, mock_llm):
        from src.core.agent import Agent

        mock_llm.get_model_name.return_value = "test-model"
        mock_llm.get_provider_name.return_value = "test-provider"
        agent = Agent(is_master=True)
        assert isinstance(agent._pending_clarifications, dict)
        assert len(agent._pending_clarifications) == 0


class TestClarificationFlowIntegration:
    """集成测试：完整的澄清流程"""

    def test_full_clarification_flow(self):
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="hr-expert",
            task_description="查询员工考勤记录",
        )
        # 步骤1：任务开始
        record.start()
        assert record.status == SubagentTaskStatus.RUNNING
        assert not record.is_clarifying()

        # 步骤2：请求澄清
        record.request_clarification("请提供员工工号和查询的月份")
        assert record.status == SubagentTaskStatus.CLARIFYING
        assert record.is_clarifying()

        # 步骤3：回答澄清
        record.answer_clarification("工号 E001，查询 2025 年 3 月")
        assert record.status == SubagentTaskStatus.RUNNING
        assert not record.is_clarifying()

        # 步骤4：完成
        record.complete(
            result={"attendance": "正常出勤 22 天"},
            summary="E001 3月考勤查询完成",
        )
        assert record.status == SubagentTaskStatus.COMPLETED
        assert record.is_terminal()

    def test_multiple_clarification_cycles(self):
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="hr-expert",
            task_description="查询员工考勤记录",
        )
        record.start()

        # 第一次澄清
        record.request_clarification("请提供员工工号")
        assert record.is_clarifying()
        record.answer_clarification("E001")
        assert not record.is_clarifying()

        # 第二次澄清
        record.request_clarification("请提供查询的月份")
        assert record.is_clarifying()
        record.answer_clarification("2025年3月")
        assert not record.is_clarifying()

        # 最终完成
        record.complete(result={}, summary="完成")
        assert record.is_terminal()
