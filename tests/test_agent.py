"""
单元测试

测试核心组件
"""

import pytest
from src.models.message import UnifiedMessage, MessageType, ChannelType
from src.models.session import Session, SessionContext, SessionState
from src.models.user import User, UserRole
from src.models.plan import ExecutionPlan, Task, TaskStatus
from src.memory.short_term import ShortTermMemory


class TestUnifiedMessage:
    """统一消息模型测试"""
    
    def test_from_text(self):
        """测试从文本创建消息"""
        message = UnifiedMessage.from_text(
            text="你好",
            user_id="test_user",
            channel_type=ChannelType.WEB,
        )
        
        assert message.text == "你好"
        assert message.user_id == "test_user"
        assert message.message_type == MessageType.TEXT
    
    def test_to_llm_message(self):
        """测试转换为LLM消息格式"""
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
        """测试创建会话"""
        session = Session(
            session_id="test_session",
            user_id="test_user",
        )
        
        assert session.session_id == "test_session"
        assert session.user_id == "test_user"
        assert session.context.state == SessionState.IDLE
    
    def test_add_message(self):
        """测试添加消息"""
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
        """测试创建用户"""
        user = User(
            user_id="test_user",
            name="测试用户",
        )
        
        assert user.user_id == "test_user"
        assert user.name == "测试用户"
        assert user.role == UserRole.EMPLOYEE
    
    def test_has_permission(self):
        """测试权限检查"""
        user = User(
            user_id="test_user",
            name="测试用户",
            role=UserRole.EMPLOYEE,
        )
        
        # 员工默认权限
        permissions = user.get_default_permissions()
        assert "email_send" in permissions


class TestShortTermMemory:
    """短期记忆测试"""
    
    def test_add_and_get_message(self):
        """测试添加和获取消息"""
        memory = ShortTermMemory(max_messages=5)
        session_id = "test_session"
        
        memory.add_message(session_id, {"role": "user", "content": "你好"})
        memory.add_message(session_id, {"role": "assistant", "content": "您好！"})
        
        context = memory.get_context(session_id)
        assert len(context) == 2
    
    def test_max_messages(self):
        """测试最大消息数限制"""
        memory = ShortTermMemory(max_messages=3)
        session_id = "test_session"
        
        for i in range(5):
            memory.add_message(session_id, {"role": "user", "content": f"消息{i}"})
        
        context = memory.get_context(session_id)
        assert len(context) == 3
    
    def test_clear_session(self):
        """测试清除会话"""
        memory = ShortTermMemory(max_messages=5)
        session_id = "test_session"
        
        memory.add_message(session_id, {"role": "user", "content": "测试"})
        memory.clear(session_id)
        
        context = memory.get_context(session_id)
        assert len(context) == 0


class TestTask:
    """任务模型测试"""
    
    def test_create_task(self):
        """测试创建任务"""
        task = Task(
            task_id="task_1",
            tool_name="email_send",
            parameters={"to": "test@example.com"},
        )
        
        assert task.task_id == "task_1"
        assert task.tool_name == "email_send"
        assert task.status == TaskStatus.PENDING
    
    def test_task_completion(self):
        """测试任务完成"""
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
        """测试创建计划"""
        plan = ExecutionPlan(
            plan_id="plan_1",
            intent="email_send",
        )
        
        assert plan.plan_id == "plan_1"
        assert plan.intent == "email_send"
    
    def test_add_task(self):
        """测试添加任务"""
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
