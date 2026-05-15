"""
Redis 跨 Worker 集成测试

模拟两个独立的 "Worker" 进程通过 Redis 共享状态。
由于测试在单进程运行，通过创建两个独立的 RedisClient 实例
（各使用独立的 _InMemoryFallback）来模拟内存隔离，
验证 Redis 作为共享层时状态可互通。

如环境中有真实 Redis，则自动使用真实 Redis 进行测试。
"""

import os
import time
import pytest

from src.core.redis_client import RedisClient, _InMemoryFallback


def _create_isolated_client():
    """创建独立的 RedisClient 实例，模拟另一个 Worker 进程"""
    c = RedisClient()
    # 隔离内存降级状态，模拟不同 Worker
    c._fallback = _InMemoryFallback()
    return c


class TestCrossWorkerCancellation:
    """任务 4.2: 模拟 2 个 Worker 访问同一个会话取消标志"""

    @pytest.fixture
    def worker_a(self):
        return _create_isolated_client()

    @pytest.fixture
    def worker_b(self):
        return _create_isolated_client()

    def test_cancel_flag_shared(self, worker_a, worker_b):
        session_id = "session_cancel_001"
        key = f"cancelled_session:{session_id}"

        # Worker A 设置取消标志
        worker_a.sadd(key, session_id)
        worker_a.expire(key, 300)

        # Worker B 能读取到取消标志
        assert worker_b.sismember(key, session_id) is True

        # Worker B 清除取消标志
        worker_b.delete(key)

        # Worker A 读取不到
        assert worker_a.sismember(key, session_id) is False


class TestCrossWorkerClarification:
    """任务 4.3: 模拟 2 个 Worker 访问同一个待澄清状态"""

    @pytest.fixture
    def worker_a(self):
        return _create_isolated_client()

    @pytest.fixture
    def worker_b(self):
        return _create_isolated_client()

    def test_clarification_shared(self, worker_a, worker_b):
        session_id = "session_clarify_001"
        key = f"pending_clarification:{session_id}"

        clarification = {
            "subagent_name": "code-reviewer",
            "execution_id": "exec_123",
            "task_description": "审查代码",
            "question": "需要更多上下文",
        }

        # Worker A 保存澄清状态
        for field, value in clarification.items():
            worker_a.hset(key, field, value)
        worker_a.expire(key, 3600)

        # Worker B 能读取到澄清状态
        result = worker_b.hgetall(key)
        assert result["subagent_name"] == "code-reviewer"
        assert result["question"] == "需要更多上下文"

        # Worker B 删除澄清状态
        worker_b.delete(key)

        # Worker A 读取不到
        assert worker_a.hgetall(key) == {}


class TestCrossWorkerFileUpload:
    """任务 4.4: 模拟在一个 Worker 上传文件，另一个 Worker 读取元数据"""

    @pytest.fixture
    def worker_a(self):
        return _create_isolated_client()

    @pytest.fixture
    def worker_b(self):
        return _create_isolated_client()

    def test_file_metadata_shared(self, worker_a, worker_b):
        file_id = "file_abc123"
        key = f"uploaded_file:{file_id}"

        file_info = {
            "file_id": file_id,
            "name": "test.pdf",
            "path": "/storage/uploads/test.pdf",
            "size": 1024,
            "mime_type": "application/pdf",
            "type": "file",
        }

        # Worker A 保存文件元数据
        for field, value in file_info.items():
            worker_a.hset(key, field, value)
        worker_a.expire(key, 86400)

        # Worker B 能读取到文件元数据
        result = worker_b.hgetall(key)
        assert result["name"] == "test.pdf"
        assert result["path"] == "/storage/uploads/test.pdf"
        assert result["mime_type"] == "application/pdf"

        # Worker B 删除文件元数据
        worker_b.delete(key)

        # Worker A 读取不到
        assert worker_a.hgetall(key) == {}


class TestCrossWorkerTaskRecord:
    """模拟 2 个 Worker 访问同一个子智能体任务记录"""

    @pytest.fixture
    def worker_a(self):
        return _create_isolated_client()

    @pytest.fixture
    def worker_b(self):
        return _create_isolated_client()

    def test_task_record_shared(self, worker_a, worker_b):
        execution_id = "exec_task_001"
        key = f"task_record:{execution_id}"

        record = {
            "task_id": "task_001",
            "execution_id": execution_id,
            "subagent_name": "email-agent",
            "status": "running",
            "progress_percent": 50.0,
        }

        # Worker A 保存任务记录
        worker_a.hset(key, "data", record)
        worker_a.expire(key, 7200)

        # Worker B 能读取到任务记录
        result = worker_b.hget(key, "data")
        assert result["subagent_name"] == "email-agent"
        assert result["status"] == "running"
        assert result["progress_percent"] == 50.0

        # Worker B 更新任务状态
        result["status"] = "completed"
        result["progress_percent"] = 100.0
        worker_b.hset(key, "data", result)

        # Worker A 读取到更新后的状态
        updated = worker_a.hget(key, "data")
        assert updated["status"] == "completed"
        assert updated["progress_percent"] == 100.0


class TestCrossWorkerExecutionPlan:
    """模拟 2 个 Worker 访问同一个执行计划"""

    @pytest.fixture
    def worker_a(self):
        return _create_isolated_client()

    @pytest.fixture
    def worker_b(self):
        return _create_isolated_client()

    def test_plan_shared(self, worker_a, worker_b):
        session_id = "session_plan_001"
        key = f"execution_plan:{session_id}"

        plan = {
            "plan_id": "plan_001",
            "intent": "查询订单",
            "tasks": [
                {"task_id": "task_1", "tool_name": "search", "status": "pending"},
                {"task_id": "task_2", "tool_name": "email", "status": "pending"},
            ],
            "execution_mode": "sequential",
        }

        # Worker A 创建计划
        worker_a.set(key, plan, ex=3600)

        # Worker B 能读取到计划
        result = worker_b.get(key)
        assert result["intent"] == "查询订单"
        assert len(result["tasks"]) == 2

        # Worker B 更新任务状态
        result["tasks"][0]["status"] = "completed"
        worker_b.set(key, result, ex=3600)

        # Worker A 读取到更新后的计划
        updated = worker_a.get(key)
        assert updated["tasks"][0]["status"] == "completed"
