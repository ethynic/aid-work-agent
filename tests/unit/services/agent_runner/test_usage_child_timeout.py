"""执行器超时 kill 与记账存储故障语义分离 + 子进程记账运行时惰性初始化。

背景（2026-11-24 聊易 CHANNEL_EXECUTION_INTERRUPTED 事故定性）：
- skill_execute 命令 600s 超时被 SIGKILL 后，原实现走 observe_child_exit(None)
  -> UsageScope.fail() -> sticky fatal -> 整个 runner 中断，客户收到兜底回复；
- 修复后超时走 observe_child_timeout：在途 skill-call 收据标记 unknown（同
  uncertain 口径），超时结果作为工具失败返回模型继续执行；
- bootstrap（sitecustomize -> install_subprocess_observer）不再强依赖 DB，
  非法 LLM 命令（import/文件操作等）完全不触碰数据库。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from src.core.agent_engine.contracts import CheckpointFailure
from src.services.agent_runner.usage_context import (
    UsageScope,
    _ensure_subprocess_runtime,
    _subprocess_scopes,
    _subprocess_tool_scopes,
    install_subprocess_observer,
)


def _attempt(n=1):
    return SimpleNamespace(runner_id=f'runner_test_{n}', worker_id='w1', number=n)


class FakeRepo:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def abandon_inflight_skill_calls(self, attempt):
        if self.error is not None:
            raise self.error
        self.calls.append(attempt)


class TestChildTimeout:
    def test_timeout_marks_inflight_and_keeps_scope_healthy(self):
        repo = FakeRepo()
        attempt = _attempt()
        scope = UsageScope(attempt, repo, 'exec-1', failure_file=None)
        scope.child_timeout()
        assert scope.fatal_error is None
        assert repo.calls == [attempt]

    def test_timeout_repository_error_keeps_fail_semantics(self, tmp_path):
        failure_file = tmp_path / 'failure.signal'
        repo = FakeRepo(error=RuntimeError('storage down'))
        scope = UsageScope(_attempt(), repo, 'exec-1', failure_file=str(failure_file))
        # 与 provider_call 等既有模式一致：fail() 标记后原错误上抛
        with pytest.raises(RuntimeError):
            scope.child_timeout()
        # 记账存储不可信仍走 fail() 原语义：sticky fatal + failure file
        assert scope.fatal_error is not None
        assert failure_file.read_text() == 'RuntimeError'

    def test_child_exit_none_still_fatal(self, tmp_path):
        """回归保护：真实异常退出（崩溃/OOM/exit 87）语义不变。"""
        repo = FakeRepo()
        scope = UsageScope(_attempt(), repo, 'exec-1', failure_file=None)
        with pytest.raises(CheckpointFailure):
            scope.child_exit(None)
        assert scope.fatal_error is not None


class TestExecutorTimeoutBranch:
    @pytest.mark.asyncio
    async def test_execute_command_timeout_uses_child_timeout(self, tmp_path):
        from src.core.skill_executor import SkillExecutor

        executor = SkillExecutor(MagicMock(), workspace=tmp_path)
        fake_proc = SimpleNamespace(pid=4321, returncode=None)
        fake_proc.wait = AsyncMock(return_value=None)
        fake_proc.communicate = AsyncMock(return_value=(b'', b''))
        with patch('asyncio.create_subprocess_shell', AsyncMock(return_value=fake_proc)), \
             patch('asyncio.wait_for', AsyncMock(side_effect=asyncio.TimeoutError)), \
             patch('src.core.subprocess_owner.stop_process_group_async', AsyncMock()) as stop_mock, \
             patch('src.llm.call_observer.observe_child_timeout') as timeout_mock, \
             patch('src.llm.call_observer.observe_child_exit') as exit_mock:
            result = await executor._execute_command('echo hi', tmp_path)
        assert result.timed_out is True
        assert result.success is False
        stop_mock.assert_awaited_once()
        timeout_mock.assert_called_once()
        exit_mock.assert_not_called()


class TestLazySubprocessRuntime:
    @pytest.fixture
    def child_env(self, monkeypatch, tmp_path):
        import src.services.agent_runner.usage_context as uc
        from src.llm.call_observer import install_call_observer

        attempt = _attempt(77)
        failure_signal = tmp_path / 'failure.signal'
        failure_signal.touch()  # 生产中 worker 在 spawn 前预创建空失败信号文件
        monkeypatch.setenv('AID_RUNNER_ID', attempt.runner_id)
        monkeypatch.setenv('AID_RUNNER_WORKER_ID', attempt.worker_id)
        monkeypatch.setenv('AID_RUNNER_ATTEMPT', str(attempt.number))
        monkeypatch.setenv('AID_RUNNER_EXECUTION_ID', 'exec-77')
        monkeypatch.setenv('AID_RUNNER_FAILURE_FILE', str(failure_signal))
        monkeypatch.setenv('AID_RUNNER_TOOL_CALL_ID', 'call-77')
        yield attempt
        # 清理全局 ContextVar 与 scope 注册表，避免污染其他用例
        install_call_observer(None)
        uc._scope.set(None)
        _subprocess_scopes.pop((attempt.runner_id, attempt.worker_id, attempt.number), None)
        _subprocess_tool_scopes.pop((attempt.runner_id, attempt.worker_id, attempt.number), None)

    def test_install_subprocess_observer_never_touches_db(self, child_env, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError('bootstrap 启动期不应触碰数据库')
        monkeypatch.setattr('src.db.database.get_postgres_pool', boom)
        monkeypatch.setattr('src.db.database.init_postgres_pool', boom)
        scope = install_subprocess_observer()
        assert scope.skill_boundary is True
        assert scope.failure_file  # 失败信号文件仍就位
        assert scope.runtime_ready is False

    def test_ensure_subprocess_runtime_lazy_initializes_once(self, child_env, monkeypatch):
        import src.services.agent_runner.usage_context as uc

        install_subprocess_observer()
        init_calls = []
        monkeypatch.setattr('src.db.database.get_postgres_pool', lambda: None)
        monkeypatch.setattr('src.db.database.init_postgres_pool',
                            lambda *a, **k: init_calls.append(1))
        row = {'attempt': child_env.number, 'worker_id': child_env.worker_id,
               'tenant_id': 't1', 'user_id': 'u1', 'session_id': 's1',
               'source': 'chat', 'record_id': 'rec1'}

        class FakeRunnerRepo:
            def __init__(self, *a, **k):
                pass

            def get(self, runner_id):
                return row

        monkeypatch.setattr('src.services.agent_runner.repository.RunnerRepository', FakeRunnerRepo)

        entered = []

        class FakeLifetime:
            def __enter__(self):
                entered.append(1)
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr('src.tools.context.tool_execution_scope',
                            lambda context: FakeLifetime())
        monkeypatch.setattr(
            'src.tools.context.ExecutionContextFactory.for_agent_call',
            staticmethod(lambda **kwargs: SimpleNamespace(**kwargs)))

        key = (child_env.runner_id, child_env.worker_id, child_env.number)
        scope = _subprocess_scopes[key]
        _ensure_subprocess_runtime(scope, key)
        assert scope.runtime_ready is True
        assert len(init_calls) == 1
        assert len(entered) == 1
        # 幂等：二次调用不再初始化
        _ensure_subprocess_runtime(scope, key)
        assert len(init_calls) == 1

    def test_ensure_runtime_lease_lost_fails_scope(self, child_env, monkeypatch):
        import src.services.agent_runner.usage_context as uc
        from src.services.agent_runner.ownership import LeaseLost

        install_subprocess_observer()
        monkeypatch.setattr('src.db.database.get_postgres_pool', lambda: object())

        class FakeRunnerRepo:
            def __init__(self, *a, **k):
                pass

            def get(self, runner_id):
                # attempt 已不属于本 worker（worker 重启场景）
                return {'attempt': 99, 'worker_id': 'other'}

        monkeypatch.setattr('src.services.agent_runner.repository.RunnerRepository', FakeRunnerRepo)

        key = (child_env.runner_id, child_env.worker_id, child_env.number)
        scope = _subprocess_scopes[key]
        with pytest.raises(LeaseLost):
            _ensure_subprocess_runtime(scope, key)
        # 失败经 scope.fail 写 failure file（worker 侧按原语义中断）
        from pathlib import Path
        assert Path(scope.failure_file).read_text() == 'LeaseLost'
