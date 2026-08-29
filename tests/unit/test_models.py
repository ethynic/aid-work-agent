"""
模型单元测试

测试 UnifiedMessage, Session, User, Task, ExecutionPlan
"""

import pytest

from src.models.message import UnifiedMessage, MessageType, ChannelType
from src.models.session import Session, SessionContext, SessionState
from src.models.user import User, UserRole
from src.models.plan import ExecutionPlan, Task, TaskStatus


class TestUnifiedMessage:
    """统一消息模型测试"""

    def test_from_text(self):
        message = UnifiedMessage.from_text(
            text="你好",
            user_id="test_user",
            channel_type=ChannelType.WEB,
        )
        assert message.text == "你好"
        assert message.user_id == "test_user"
        assert message.message_type == MessageType.TEXT

    def test_to_llm_message(self):
        message = UnifiedMessage.from_text(
            text="测试消息",
            user_id="test_user",
            channel_type=ChannelType.WEB,
        )
        llm_msg = message.to_llm_message()
        assert llm_msg["role"] == "user"
        assert llm_msg["content"] == "测试消息"


class TestSession:
    """会话模型测试"""

    def test_create_session(self):
        session = Session(
            session_id="test_session",
            user_id="test_user",
        )
        assert session.session_id == "test_session"
        assert session.user_id == "test_user"
        assert session.context.state == SessionState.IDLE

    def test_add_message(self):
        session = Session(
            session_id="test_session",
            user_id="test_user",
        )
        session.context.add_message("user", "你好")
        session.context.add_message("assistant", "您好！")
        assert len(session.context.history) == 2
        assert session.context.history[0]["role"] == "user"


class TestUser:
    """用户模型测试"""

    def test_create_user(self):
        user = User(
            user_id="test_user",
            name="测试用户",
        )
        assert user.user_id == "test_user"
        assert user.name == "测试用户"
        assert user.role == UserRole.EMPLOYEE

    def test_tenant_id_serialization_preserves_unknown_and_public(self):
        unknown = User(user_id="u_unknown", name="未知")
        public = User(user_id="u_public", name="公共", tenant_id="")

        assert unknown.to_dict()["tenant_id"] is None
        assert public.to_dict()["tenant_id"] == ""
        assert User.from_dict(unknown.to_dict()).tenant_id is None
        assert User.from_dict(public.to_dict()).tenant_id == ""

    def test_has_permission(self):
        user = User(
            user_id="test_user",
            name="测试用户",
            role=UserRole.EMPLOYEE,
        )
        permissions = user.get_default_permissions()
        assert "email_process" in permissions


class TestTask:
    """任务模型测试"""

    def test_create_task(self):
        task = Task(
            task_id="task_1",
            tool_name="email_send",
            parameters={"to": "test@example.com"},
        )
        assert task.task_id == "task_1"
        assert task.tool_name == "email_send"
        assert task.status == TaskStatus.PENDING

    def test_task_completion(self):
        task = Task(
            task_id="task_1",
            tool_name="test_tool",
            parameters={},
        )
        task.start()
        assert task.status == TaskStatus.RUNNING

        task.complete({"success": True})
        assert task.status == TaskStatus.COMPLETED


class TestExecutionPlan:
    """执行计划测试"""

    def test_create_plan(self):
        plan = ExecutionPlan(
            plan_id="plan_1",
            intent="email_send",
        )
        assert plan.plan_id == "plan_1"
        assert plan.intent == "email_send"

    def test_add_task(self):
        plan = ExecutionPlan(
            plan_id="plan_1",
            intent="test",
        )
        task = Task(
            task_id="task_1",
            tool_name="test_tool",
            parameters={},
        )
        plan.add_task(task)
        assert len(plan.tasks) == 1
