"""worker 内存治理单测（2026-11-12 agent2 棘轮整改）。

覆盖：任务收尾 gc + malloc_trim 钩子（失败静默降级）、RSS/cgroup 探测、
RSS 水位 recycle 决策（空闲退出触发容器重启 / 有任务限频告警 / 无上限不启用）。
"""

import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.agent_runner import worker as worker_module
from src.services.agent_runner.worker import (
    RunnerWorker,
    _cgroup_memory_limit_bytes,
    _worker_rss_bytes,
)


def _runner_config():
    return SimpleNamespace(enabled=True, slot_concurrency=1,
        watchdog_threshold=15, heartbeat_seconds=10, lease_seconds=30,
        poll_seconds=0.05, durable_sleep_enabled=False, concurrency=1,
        browser_owner=None)


def _make_worker(monkeypatch):
    """构造仅依赖 fake 的 RunnerWorker（与 test_slot_concurrency 同模式）。"""
    return RunnerWorker(_runner_config(), slot_concurrency=1,
        execution_repository=SimpleNamespace(reap_expired=lambda: []),
        repository=SimpleNamespace(),
        authorizer=SimpleNamespace(),
        usage_repository=SimpleNamespace(),
        finalizer=SimpleNamespace(settle_ready=lambda: []),
        runtime_factory=SimpleNamespace(),
        tool_recovery=SimpleNamespace())


class FakeCgroupPath:
    """按路径返回预设内容的 Path 替身（_cgroup_memory_limit_bytes 探测）。"""

    contents = {}

    def __init__(self, path):
        self.path = str(path)

    def read_text(self):
        if self.path not in self.contents:
            raise OSError(self.path)
        return self.contents[self.path]


@pytest.fixture
def fake_cgroup(monkeypatch):
    monkeypatch.setattr(worker_module, 'Path', FakeCgroupPath)
    return FakeCgroupPath


class TestReclaimProcessMemory:
    def test_reclaim_runs_and_returns(self):
        # Linux 容器/WSL 下真实执行 gc + malloc_trim；非 Linux malloc_trim 失败静默
        RunnerWorker._reclaim_process_memory()

    def test_reclaim_swallows_ctypes_failure(self, monkeypatch):
        real_import = builtins.__import__

        def broken_import(name, *args, **kwargs):
            if name == 'ctypes':
                raise OSError('no libc')
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', broken_import)
        # ctypes 导入失败必须静默降级，不得抛出阻断收尾链
        RunnerWorker._reclaim_process_memory()


class TestWorkerRss:
    @pytest.mark.skipif(sys.platform != 'linux', reason='依赖 /proc')
    def test_rss_positive_on_linux(self):
        rss = _worker_rss_bytes()
        assert rss is not None and rss > 0

    def test_rss_none_when_proc_unavailable(self, monkeypatch):
        real_open = open

        def broken_open(path, *args, **kwargs):
            if str(path) == '/proc/self/status':
                raise OSError('no proc')
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, 'open', broken_open)
        assert _worker_rss_bytes() is None


class TestCgroupMemoryLimit:
    def test_v1_limit(self, fake_cgroup):
        fake_cgroup.contents = {
            '/sys/fs/cgroup/memory/memory.limit_in_bytes': str(1024**3)}
        assert _cgroup_memory_limit_bytes() == 1024**3

    def test_v2_limit_when_v1_missing(self, fake_cgroup):
        fake_cgroup.contents = {'/sys/fs/cgroup/memory.max': str(5 * 1024**3)}
        assert _cgroup_memory_limit_bytes() == 5 * 1024**3

    def test_v2_unlimited_returns_none(self, fake_cgroup):
        fake_cgroup.contents = {'/sys/fs/cgroup/memory.max': 'max\n'}
        assert _cgroup_memory_limit_bytes() is None

    def test_v1_huge_value_treated_as_unlimited(self, fake_cgroup):
        fake_cgroup.contents = {
            '/sys/fs/cgroup/memory/memory.limit_in_bytes': str(1 << 62)}
        assert _cgroup_memory_limit_bytes() is None

    def test_both_missing_returns_none(self, fake_cgroup):
        fake_cgroup.contents = {}
        assert _cgroup_memory_limit_bytes() is None


class TestMaybeRecycleOnMemory:
    MB = 1024 * 1024

    def _worker_with_limit(self, monkeypatch, limit_bytes):
        worker = _make_worker(monkeypatch)
        worker._memory_limit = limit_bytes
        return worker

    def test_below_threshold_no_op(self, monkeypatch):
        worker = self._worker_with_limit(monkeypatch, 1000 * self.MB)
        monkeypatch.setattr(worker_module, '_worker_rss_bytes',
                            lambda: 100 * self.MB)
        worker._maybe_recycle_on_memory(0)  # 不抛出即通过

    def test_no_limit_disables_recycle(self, monkeypatch):
        worker = self._worker_with_limit(monkeypatch, None)
        monkeypatch.setattr(worker_module, '_worker_rss_bytes',
                            lambda: 10 * 1024 * self.MB)
        worker._maybe_recycle_on_memory(0)  # 无上限不启用 recycle

    def test_over_threshold_with_active_task_defers(self, monkeypatch):
        worker = self._worker_with_limit(monkeypatch, 1000 * self.MB)
        monkeypatch.setattr(worker_module, '_worker_rss_bytes',
                            lambda: 900 * self.MB)
        worker._maybe_recycle_on_memory(2)  # 有任务：仅告警，不退出

    def test_over_threshold_idle_raises_recycle(self, monkeypatch):
        worker = self._worker_with_limit(monkeypatch, 1000 * self.MB)
        monkeypatch.setattr(worker_module, '_worker_rss_bytes',
                            lambda: 900 * self.MB)
        with pytest.raises(RuntimeError, match='AGENT_RUNNER_WORKER_MEMORY_RECYCLE'):
            worker._maybe_recycle_on_memory(0)

    def test_limit_resolved_lazily_once(self, monkeypatch):
        worker = _make_worker(monkeypatch)
        assert worker._memory_limit is None
        calls = []
        monkeypatch.setattr(worker_module, '_cgroup_memory_limit_bytes',
                            lambda: calls.append(1) or 1000 * self.MB)
        monkeypatch.setattr(worker_module, '_worker_rss_bytes',
                            lambda: 100 * self.MB)
        worker._maybe_recycle_on_memory(0)
        worker._maybe_recycle_on_memory(0)
        assert len(calls) == 1  # 上限只探测一次并缓存
