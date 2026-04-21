"""
Scheduled Task API 端到端测试
测试数据库交互层：任务创建、查询、暂停/恢复、删除
"""

import pytest
import uuid
from datetime import datetime


class TestScheduledTaskCRUD:
    """定时任务 CRUD 测试"""

    @pytest.fixture
    def test_user_for_task(self):
        """创建测试用户"""
        from src.db.models import UserDB, hash_password

        user_id = f"task_test_{uuid.uuid4().hex[:8]}"
        phone = f"138{uuid.uuid4().hex[:8]}"

        user_data = {
            "user_id": user_id,
            "username": "task_test",
            "phone": phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        yield user_id

        # 清理
        try:
            UserDB.delete(user_id)
        except Exception:
            pass

    def test_create_task(self, test_user_for_task):
        """测试任务创建"""
        from src.scheduler.db import ScheduledTaskDB

        user_id = test_user_for_task

        task_data = {
            "name": "Test Task",
            "description": "A test scheduled task",
            "task_prompt": "Say hello every day",
            "schedule_type": "interval",
            "interval_seconds": 3600  # 每小时执行一次
        }

        result = ScheduledTaskDB.create(user_id, **task_data)

        assert result is True

    def test_get_task_by_id(self, test_user_for_task):
        """测试根据 ID 获取任务"""
        from src.scheduler.db import ScheduledTaskDB

        user_id = test_user_for_task

        # 先创建任务
        task_data = {
            "name": "Test Task",
            "description": "A test scheduled task",
            "task_prompt": "Say hello",
            "schedule_type": "interval",
            "interval_seconds": 3600
        }
        ScheduledTaskDB.create(user_id, **task_data)

        # 获取任务列表
        tasks = ScheduledTaskDB.list_by_user(user_id)
        task_id = tasks[0]["task_id"]

        # 根据 ID 获取
        retrieved = ScheduledTaskDB.get_by_id(task_id)

        assert retrieved is not None
        assert retrieved["task_id"] == task_id

    def test_list_tasks_by_user(self, test_user_for_task):
        """测试列出用户的所有任务"""
        from src.scheduler.db import ScheduledTaskDB

        user_id = test_user_for_task

        # 创建多个任务
        for i in range(3):
            task_data = {
                "name": f"Task {i}",
                "description": f"Description {i}",
                "task_prompt": f"Prompt {i}",
                "schedule_type": "interval",
                "interval_seconds": 3600
            }
            ScheduledTaskDB.create(user_id, **task_data)

        tasks = ScheduledTaskDB.list_by_user(user_id)

        assert len(tasks) >= 3

    def test_list_tasks_by_status(self, test_user_for_task):
        """测试按状态列出任务"""
        from src.scheduler.db import ScheduledTaskDB

        user_id = test_user_for_task

        # 创建一个活动任务和一个暂停任务
        task_data1 = {
            "name": "Active Task",
            "description": "Active task",
            "task_prompt": "Active prompt",
            "schedule_type": "interval",
            "interval_seconds": 3600
        }
        task_data2 = {
            "name": "Paused Task",
            "description": "Paused task",
            "task_prompt": "Paused prompt",
            "schedule_type": "interval",
            "interval_seconds": 7200,
            "status": "paused"
        }
        ScheduledTaskDB.create(user_id, **task_data1)
        ScheduledTaskDB.create(user_id, **task_data2)

        active_tasks = ScheduledTaskDB.list_by_user(user_id, status="active")
        paused_tasks = ScheduledTaskDB.list_by_user(user_id, status="paused")

        assert len(active_tasks) >= 1
        assert len(paused_tasks) >= 1

    def test_update_task_schedule(self, test_user_for_task):
        """测试更新任务调度"""
        from src.scheduler.db import ScheduledTaskDB

        user_id = test_user_for_task

        # 创建任务
        task_data = {
            "name": "Task to Update",
            "description": "Task description",
            "task_prompt": "Original prompt",
            "schedule_type": "interval",
            "interval_seconds": 3600
        }
        ScheduledTaskDB.create(user_id, **task_data)

        # 获取任务
        tasks = ScheduledTaskDB.list_by_user(user_id)
        task_id = tasks[0]["task_id"]

        # 更新调度
        result = ScheduledTaskDB.update_schedule(
            task_id,
            cron_expression="0 9 * * *"  # 每天早上9点
        )

        assert result is True

    def test_update_task_status(self, test_user_for_task):
        """测试更新任务状态（暂停/恢复）"""
        from src.scheduler.db import ScheduledTaskDB

        user_id = test_user_for_task

        # 创建任务
        task_data = {
            "name": "Status Test Task",
            "description": "Test task status",
            "task_prompt": "Test prompt",
            "schedule_type": "interval",
            "interval_seconds": 3600
        }
        ScheduledTaskDB.create(user_id, **task_data)

        # 获取任务
        tasks = ScheduledTaskDB.list_by_user(user_id)
        task_id = tasks[0]["task_id"]

        # 暂停任务
        result = ScheduledTaskDB.update_status(task_id, "paused")
        assert result is True

        # 恢复任务
        result = ScheduledTaskDB.update_status(task_id, "active")
        assert result is True

    def test_delete_task(self, test_user_for_task):
        """测试删除任务"""
        from src.scheduler.db import ScheduledTaskDB

        user_id = test_user_for_task

        # 创建任务
        task_data = {
            "name": "Task to Delete",
            "description": "Will be deleted",
            "task_prompt": "Delete me",
            "schedule_type": "interval",
            "interval_seconds": 3600
        }
        ScheduledTaskDB.create(user_id, **task_data)

        # 获取任务
        tasks = ScheduledTaskDB.list_by_user(user_id)
        task_id = tasks[0]["task_id"]

        # 删除任务
        result = ScheduledTaskDB.delete(task_id)

        assert result is True

        # 验证删除成功
        retrieved = ScheduledTaskDB.get_by_id(task_id)
        assert retrieved is None


class TestScheduledTaskLogs:
    """定时任务日志测试"""

    @pytest.fixture
    def test_user_and_task(self):
        """创建测试用户和任务"""
        from src.db.models import UserDB, hash_password
        from src.scheduler.db import ScheduledTaskDB

        user_id = f"task_log_test_{uuid.uuid4().hex[:8]}"
        phone = f"138{uuid.uuid4().hex[:8]}"

        user_data = {
            "user_id": user_id,
            "username": "task_log_test",
            "phone": phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        # 创建任务
        task_data = {
            "name": "Log Test Task",
            "description": "Task for log testing",
            "task_prompt": "Test prompt",
            "schedule_type": "interval",
            "interval_seconds": 3600
        }
        ScheduledTaskDB.create(user_id, **task_data)

        tasks = ScheduledTaskDB.list_by_user(user_id)
        task_id = tasks[0]["task_id"]

        yield user_id, task_id

        # 清理
        try:
            ScheduledTaskDB.delete(task_id)
        except Exception:
            pass
        try:
            UserDB.delete(user_id)
        except Exception:
            pass

    def test_create_task_log(self, test_user_and_task):
        """测试创建任务日志"""
        from src.scheduler.db import ScheduledTaskLogDB

        user_id, task_id = test_user_and_task

        log_data = {
            "task_id": task_id,
            "user_id": user_id,
            "session_id": f"session_{uuid.uuid4().hex[:8]}",
            "status": "success",
            "trigger_type": "manual",
            "result_summary": "Task completed successfully",
            "started_at": datetime.now().isoformat()
        }

        result = ScheduledTaskLogDB.create(**log_data)

        assert result is True

    def test_get_log_by_id(self, test_user_and_task):
        """测试根据 ID 获取日志"""
        from src.scheduler.db import ScheduledTaskLogDB
        from src.db.models import SessionDB

        user_id, task_id = test_user_and_task

        # 创建会话以获取 session_id
        session = SessionDB.create(user_id, title="Task Log Session")
        session_id = session["session_id"]

        # 创建日志
        log_data = {
            "task_id": task_id,
            "user_id": user_id,
            "session_id": session_id,
            "status": "success",
            "trigger_type": "manual",
            "result_summary": "Test log",
            "started_at": datetime.now().isoformat()
        }
        ScheduledTaskLogDB.create(**log_data)

        # 获取日志列表
        logs = ScheduledTaskLogDB.list_by_task(task_id)
        log_id = logs[0]["log_id"]

        # 根据 ID 获取
        retrieved = ScheduledTaskLogDB.get_by_id(log_id)

        assert retrieved is not None

    def test_list_logs_by_task(self, test_user_and_task):
        """测试列出任务的所有日志"""
        from src.scheduler.db import ScheduledTaskLogDB
        from src.db.models import SessionDB

        user_id, task_id = test_user_and_task

        # 创建会话
        session = SessionDB.create(user_id, title="Log List Session")
        session_id = session["session_id"]

        # 创建多条日志
        for i in range(3):
            log_data = {
                "task_id": task_id,
                "user_id": user_id,
                "session_id": session_id,
                "status": "success" if i % 2 == 0 else "failed",
                "trigger_type": "manual",
                "result_summary": f"Log entry {i}",
                "started_at": datetime.now().isoformat()
            }
            ScheduledTaskLogDB.create(**log_data)

        logs = ScheduledTaskLogDB.list_by_task(task_id)

        assert len(logs) >= 3

    def test_list_logs_by_user(self, test_user_and_task):
        """测试列出用户的所有任务日志"""
        from src.scheduler.db import ScheduledTaskLogDB

        user_id, task_id = test_user_and_task

        logs = ScheduledTaskLogDB.list_by_user(user_id)

        assert len(logs) >= 1