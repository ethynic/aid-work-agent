#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试子智能体澄清流程

验证 Subagent 在执行任务时需要用户补充信息的完整流程：
1. Subagent 调用 clarify → 被拦截 → 返回 CLARIFYING 状态
2. 主 Agent 检测到 CLARIFYING → yield 澄清问题给用户
3. 用户回复补充信息 → 主 Agent 自动 re-delegate
4. 新 Subagent 携带补充信息继续执行 → 完成

运行: pytest tests/test_subagent_clarification.py -v
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from src.subagents.protocol import SubagentTaskRecord, get_task_record_key
from src.models.subagent import SubagentTaskStatus


class TestSubagentTaskRecord(unittest.TestCase):
    """测试 SubagentTaskRecord 的澄清状态管理"""

    def test_initial_status_is_pending(self):
        """初始状态应为 PENDING"""
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        self.assertEqual(record.status, SubagentTaskStatus.PENDING)

    def test_request_clarification_sets_status(self):
        """调用 request_clarification 应设置 CLARIFYING 状态和问题"""
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        record.request_clarification("请提供邮箱地址")

        self.assertEqual(record.status, SubagentTaskStatus.CLARIFYING)
        self.assertEqual(record.clarification_request, "请提供邮箱地址")

    def test_answer_clarification_resumes_running(self):
        """调用 answer_clarification 应恢复 RUNNING 状态"""
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        record.request_clarification("请提供邮箱地址")
        self.assertTrue(record.is_clarifying())

        record.answer_clarification("test@example.com")
        self.assertEqual(record.status, SubagentTaskStatus.RUNNING)
        self.assertEqual(record.clarification_answer, "test@example.com")
        self.assertFalse(record.is_clarifying())

    def test_is_clarifying(self):
        """测试 is_clarifying 方法"""
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        self.assertFalse(record.is_clarifying())

        record.request_clarification("问题？")
        self.assertTrue(record.is_clarifying())

    def test_is_terminal_includes_clarifying(self):
        """CLARIFYING 应被视为终态（当前执行阶段的终态）"""
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        self.assertFalse(record.is_terminal())

        record.request_clarification("问题？")
        # CLARIFYING 应是终态，因为子智能体执行已暂停
        self.assertTrue(record.is_terminal())

    def test_completed_is_terminal(self):
        """COMPLETED 应是终态"""
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        record.complete(result={"key": "value"}, summary="完成")
        self.assertTrue(record.is_terminal())

    def test_failed_is_terminal(self):
        """FAILED 应是终态"""
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        record.fail("执行失败")
        self.assertTrue(record.is_terminal())

    def test_running_is_not_terminal(self):
        """RUNNING 不是终态"""
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="test-agent",
            task_description="测试任务",
        )
        record.start()
        self.assertFalse(record.is_terminal())


class TestSubagentExecutorClarification(unittest.TestCase):
    """测试 SubagentExecutor 的澄清处理"""

    def test_handle_clarification_returns_false_when_not_clarifying(self):
        """非 CLARIFYING 状态时 handle_clarification 应返回 False"""
        from src.subagents.executor import SubagentExecutor

        executor = SubagentExecutor(
            session_memory=MagicMock(),
            registry=MagicMock(),
        )

        # executor._task_records 为空，没有澄清中的记录
        result = asyncio.get_event_loop().run_until_complete(
            executor.handle_clarification("non_existent_id", "answer")
        )
        self.assertFalse(result)

    def test_get_pending_clarification_returns_none_when_not_clarifying(self):
        """非 CLARIFYING 状态时 get_pending_clarification 应返回 None"""
        from src.subagents.executor import SubagentExecutor

        executor = SubagentExecutor(
            session_memory=MagicMock(),
            registry=MagicMock(),
        )

        result = executor.get_pending_clarification("non_existent_id")
        self.assertIsNone(result)

    def test_get_pending_clarification_returns_record_when_clarifying(self):
        """CLARIFYING 状态时 get_pending_clarification 应返回记录"""
        from src.subagents.executor import SubagentExecutor

        executor = SubagentExecutor(
            session_memory=MagicMock(),
            registry=MagicMock(),
        )

        # 手动创建一个 CLARIFYING 状态的记录
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
        self.assertIsNotNone(result)
        self.assertEqual(result.clarification_request, "请提供邮箱地址")
        self.assertTrue(result.is_clarifying())


class TestMasterAgentPendingClarification(unittest.TestCase):
    """测试主 Agent 的 pending clarification 管理"""

    @patch('src.core.agent.llm_gateway')
    @patch('src.core.agent.ShortTermMemory')
    @patch('src.core.agent.ToolRegistry')
    @patch('src.core.agent.ToolExecutor')
    @patch('src.core.agent.PlanManager')
    def test_pending_clarifications_initialized(self, mock_pm, mock_te, mock_tr, mock_stm, mock_llm):
        """主 Agent 初始化时应有 _pending_clarifications 属性"""
        from src.core.agent import Agent

        mock_llm.get_model_name.return_value = "test-model"
        mock_llm.get_provider_name.return_value = "test-provider"

        agent = Agent(is_master=True)
        self.assertIsInstance(agent._pending_clarifications, dict)
        self.assertEqual(len(agent._pending_clarifications), 0)

    @patch('src.core.agent.llm_gateway')
    @patch('src.core.agent.ShortTermMemory')
    @patch('src.core.agent.ToolRegistry')
    @patch('src.core.agent.ToolExecutor')
    @patch('src.core.agent.PlanManager')
    def test_save_and_clear_pending_clarification(self, mock_pm, mock_te, mock_tr, mock_stm, mock_llm):
        """测试保存和清除 pending clarification"""
        from src.core.agent import Agent

        mock_llm.get_model_name.return_value = "test-model"
        mock_llm.get_provider_name.return_value = "test-provider"

        agent = Agent(is_master=True)

        # 模拟保存 pending clarification
        agent._pending_clarifications["session_001"] = {
            "subagent_name": "hr-expert",
            "execution_id": "exec_001",
            "task_description": "查询员工信息",
            "question": "请提供员工工号",
        }

        self.assertIn("session_001", agent._pending_clarifications)
        self.assertEqual(
            agent._pending_clarifications["session_001"]["subagent_name"],
            "hr-expert"
        )

        # 清除
        agent._pending_clarifications.pop("session_001", None)
        self.assertNotIn("session_001", agent._pending_clarifications)


class TestSubagentClarifyIntercept(unittest.TestCase):
    """测试子智能体 clarify 调用被拦截的流程"""

    @patch('src.core.agent.llm_gateway')
    @patch('src.core.agent.ShortTermMemory')
    @patch('src.core.agent.ToolRegistry')
    @patch('src.core.agent.ToolExecutor')
    @patch('src.core.agent.PlanManager')
    def test_subagent_has_clarification_state(self, mock_pm, mock_te, mock_tr, mock_stm, mock_llm):
        """子智能体应初始化 _clarification_missing_info"""
        from src.core.agent import Agent
        from src.models.subagent import SubagentConfig

        mock_llm.get_model_name.return_value = "test-model"
        mock_llm.get_provider_name.return_value = "test-provider"

        config = SubagentConfig(
            name="test-subagent",
            description="测试子智能体",
        )

        agent = Agent(is_master=False, subagent_config=config)
        self.assertIsInstance(agent._clarification_missing_info, list)


class TestClarificationFlowIntegration(unittest.TestCase):
    """集成测试：完整的澄清流程"""

    def test_full_clarification_flow(self):
        """测试完整的澄清流程：创建 → 请求澄清 → 回答 → 恢复"""
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="hr-expert",
            task_description="查询员工考勤记录",
        )

        # 步骤1：任务开始
        record.start()
        self.assertEqual(record.status, SubagentTaskStatus.RUNNING)
        self.assertFalse(record.is_clarifying())

        # 步骤2：子智能体发现需要补充信息，调用 clarify
        record.request_clarification("请提供员工工号和查询的月份")
        self.assertEqual(record.status, SubagentTaskStatus.CLARIFYING)
        self.assertEqual(record.clarification_request, "请提供员工工号和查询的月份")
        self.assertTrue(record.is_clarifying())
        self.assertTrue(record.is_terminal())  # CLARIFYING 是当前阶段的终态

        # 步骤3：主 Agent 将问题转发给用户，用户回复
        user_answer = "工号 E001，查询 2025 年 3 月"
        record.answer_clarification(user_answer)
        self.assertEqual(record.status, SubagentTaskStatus.RUNNING)
        self.assertEqual(record.clarification_answer, user_answer)
        self.assertFalse(record.is_clarifying())

        # 步骤4：新子智能体实例（re-delegate）继续执行并完成
        record.complete(
            result={"attendance": "正常出勤 22 天"},
            summary="E001 3月考勤查询完成"
        )
        self.assertEqual(record.status, SubagentTaskStatus.COMPLETED)
        self.assertTrue(record.is_terminal())

    def test_multiple_clarification_cycles(self):
        """测试多次澄清循环"""
        record = SubagentTaskRecord.create(
            task_id="task_001",
            execution_id="exec_001",
            subagent_name="hr-expert",
            task_description="查询员工考勤记录",
        )

        record.start()

        # 第一次澄清
        record.request_clarification("请提供员工工号")
        self.assertTrue(record.is_clarifying())
        record.answer_clarification("E001")
        self.assertFalse(record.is_clarifying())

        # 第二次澄清（re-delegate 后又需要信息）
        record.request_clarification("请提供查询的月份")
        self.assertTrue(record.is_clarifying())
        record.answer_clarification("2025年3月")
        self.assertFalse(record.is_clarifying())

        # 最终完成
        record.complete(result={}, summary="完成")
        self.assertTrue(record.is_terminal())


if __name__ == '__main__':
    unittest.main()
