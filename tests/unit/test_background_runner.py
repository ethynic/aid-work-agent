"""独立后台运行时单元测试（plan-background-runner.md §5）

覆盖：
- _register_system_jobs 注册 memory_cleanup / dedup_cleanup / wecom_kf_timeout（按间隔/条件）
- background_runner import 安全（不拉起 master_agent）

用户定时任务 reconcile/手动触发相关用例已随 Runner 化 Phase 1 清场移除
（见 docs/plans/plan-scheduled-task-runner-refactor.md）。
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ==================== _register_system_jobs 注册验证 ====================

class TestRegisterSystemJobs:
    """验证迁入循环按计划注册到 APScheduler（D14/D15）"""

    def _make_manager(self):
        from src.scheduler.manager import ScheduledTaskManager
        m = ScheduledTaskManager.__new__(ScheduledTaskManager)
        m._scheduler = MagicMock()
        m._running = True
        m._lock_value = None
        return m

    def test_registers_memory_cleanup_dedup_cleanup(self):
        """注册 memory_summarizer / memory_cleanup / dedup_cleanup / wecom_kf_timeout"""
        m = self._make_manager()
        with patch("src.config.settings.settings") as mock_settings:
            mock_settings.memory.long_term.enabled = True
            mock_settings.memory.long_term.summary_cron = "0 2 * * *"
            mock_settings.memory.mid_term.enabled = False
            mock_settings.memory.mid_term.background_scan_enabled = False
            mock_settings.memory.cleanup_interval = 300

            m._register_system_jobs()

        # add_job 被调用：memory_summarizer + memory_cleanup + dedup_cleanup + wecom_kf_timeout
        job_ids = [c.kwargs.get("id") for c in m._scheduler.add_job.call_args_list]
        assert "job_system_memory_summarizer" in job_ids
        assert "job_system_memory_cleanup" in job_ids
        assert "job_system_dedup_cleanup" in job_ids
        # wecom_kf_timeout 无条件注册（SaaS 开关已移除）
        assert "job_system_wecom_kf_timeout" in job_ids
        # 用户任务对账 job 已随 Runner 化清场移除（用户任务改由 dispatch 循环驱动）
        assert "job_system_reconcile" not in job_ids

    def test_registers_wecom_kf_timeout_unconditionally(self):
        """wecom_kf_timeout 无条件注册（原 SaaS 门控已移除）"""
        m = self._make_manager()
        with patch("src.config.settings.settings") as mock_settings:
            mock_settings.memory.long_term.enabled = False
            mock_settings.memory.mid_term.enabled = False
            mock_settings.memory.cleanup_interval = 300

            m._register_system_jobs()

        job_ids = [c.kwargs.get("id") for c in m._scheduler.add_job.call_args_list]
        assert "job_system_wecom_kf_timeout" in job_ids

    def test_s1_hook_skips_when_dispatcher_not_importable(self):
        """S1 未落地（ImportError）→ 不报错，不注册 publish_dispatch"""
        m = self._make_manager()
        with patch("src.config.settings.settings") as mock_settings:
            mock_settings.memory.long_term.enabled = False
            mock_settings.memory.mid_term.enabled = False
            mock_settings.memory.cleanup_interval = 300
            # src.social_media.publishing.dispatcher 不存在 → ImportError 被吞
            m._register_system_jobs()

        job_ids = [c.kwargs.get("id") for c in m._scheduler.add_job.call_args_list]
        assert "job_system_publish_dispatch" not in job_ids

    def test_registers_wecom_proxy_group_discovery(self):
        """后台每 10 秒登记活跃设备组，设备慢调用由各组 job 承担。"""
        from datetime import timedelta

        m = self._make_manager()
        with patch("src.config.settings.settings") as mock_settings:
            mock_settings.memory.long_term.enabled = False
            mock_settings.memory.mid_term.enabled = False
            mock_settings.memory.cleanup_interval = 300

            m._register_system_jobs()

        calls = {c.kwargs.get("id"): c for c in m._scheduler.add_job.call_args_list}
        assert "job_system_wecom_proxy_groups" in calls
        assert "job_system_wecom_proxy_monitor" not in calls
        assert "job_system_wecom_proxy_dispatch" not in calls
        call = calls["job_system_wecom_proxy_groups"]
        assert call.args[1].interval == timedelta(seconds=10)  # trigger 为位置参数
        assert call.kwargs["max_instances"] == 1  # 整轮未完成不重叠
        assert call.kwargs["coalesce"] is True

    def test_wecom_proxy_monitor_callback_runs_tick_and_swallows_errors(self):
        """回调经新 event loop 执行 run_tick（同视频轮询模式）；异常不外抛线程"""
        from unittest.mock import AsyncMock

        import src.wecom_conversation.proxy_monitor as proxy_monitor
        from src.scheduler.manager import ScheduledTaskManager

        m = ScheduledTaskManager.__new__(ScheduledTaskManager)
        with patch.object(proxy_monitor, "run_tick", AsyncMock()) as tick:
            m._run_wecom_proxy_monitor(('tenant', 'device', 'account'))
        tick.assert_awaited_once_with(('tenant', 'device', 'account'))
        with patch.object(proxy_monitor, "run_tick",
                          AsyncMock(side_effect=RuntimeError("boom"))):
            m._run_wecom_proxy_monitor(('tenant', 'device', 'account'))  # 不外抛

    def test_wecom_proxy_dispatch_callback_runs_tick_and_swallows_errors(self):
        """派发回调经新 event loop 执行 run_tick；异常不外抛线程"""
        from unittest.mock import AsyncMock

        import src.wecom_conversation.proxy_dispatcher as proxy_dispatcher
        from src.scheduler.manager import ScheduledTaskManager

        m = ScheduledTaskManager.__new__(ScheduledTaskManager)
        with patch.object(proxy_dispatcher, "run_tick", AsyncMock()) as tick:
            m._run_wecom_proxy_dispatch(('tenant', 'device', 'account'))
        tick.assert_awaited_once_with(('tenant', 'device', 'account'))
        with patch.object(proxy_dispatcher, "run_tick",
                          AsyncMock(side_effect=RuntimeError("boom"))):
            m._run_wecom_proxy_dispatch(('tenant', 'device', 'account'))  # 不外抛

    def test_slow_device_does_not_block_other_device_second_tick(self):
        """A 超过轮询间隔未结束，B 的监控与派发仍各执行第二轮。"""
        import asyncio
        import threading
        import time

        from apscheduler.schedulers.background import BackgroundScheduler

        import src.wecom_conversation.proxy_dispatcher as proxy_dispatcher
        import src.wecom_conversation.proxy_monitor as proxy_monitor

        m = self._make_manager()
        scheduler = BackgroundScheduler(timezone='Asia/Shanghai')
        scheduler.start(paused=True)
        m._scheduler = scheduler
        m._load_wecom_proxy_groups = lambda: [
            ('tenant', 'device-a', 'account'), ('tenant', 'device-b', 'account')]
        release_a = threading.Event()
        a_started = threading.Event()
        counts = {'monitor': 0, 'dispatch': 0}
        count_lock = threading.Lock()

        async def device_tick(kind, group):
            if group[1] == 'device-a':
                a_started.set()
                await asyncio.to_thread(release_a.wait)
                return
            with count_lock:
                counts[kind] += 1

        async def monitor_tick(group):
            await device_tick('monitor', group)

        async def dispatch_tick(group):
            await device_tick('dispatch', group)

        try:
            with patch.object(proxy_monitor, 'run_tick', monitor_tick), \
                    patch.object(proxy_dispatcher, 'run_tick', dispatch_tick):
                m._refresh_wecom_proxy_group_jobs()
                jobs = [job for job in scheduler.get_jobs()
                        if job.id.startswith('job_system_wecom_proxy_')]
                assert len(jobs) == 4
                assert all(job.max_instances == 1 for job in jobs)
                started_at = time.monotonic()
                scheduler.resume()
                deadline = started_at + 13
                while time.monotonic() < deadline:
                    with count_lock:
                        enough = counts['monitor'] >= 2 and counts['dispatch'] >= 2
                    if a_started.is_set() and enough:
                        break
                    time.sleep(0.01)
                assert a_started.is_set()
                assert counts['monitor'] >= 2 and counts['dispatch'] >= 2
                assert time.monotonic() - started_at >= 10
                assert not release_a.is_set()  # B 第二轮发生时 A 仍在原轮次
        finally:
            release_a.set()
            scheduler.shutdown(wait=True)


# ==================== background_runner import 安全 ====================

class TestBackgroundRunnerImportSafety:
    """background_runner 模块导入不得拉起 master_agent（D5）"""

    def test_import_does_not_load_master_agent(self):
        """import src.background_runner 后 sys.modules 不含 src.core.agent"""
        # 清除可能已加载的模块，重新 import
        for mod in list(sys.modules):
            if mod in ("src.background_runner",) or mod.startswith("src.background_runner."):
                del sys.modules[mod]
        # 同时确保 src.core.agent 未被其他测试预先加载
        had_agent = "src.core.agent" in sys.modules
        import src.background_runner  # noqa: F401
        # 若原本就没加载，import 后也不应有
        if not had_agent:
            assert "src.core.agent" not in sys.modules, \
                "background_runner 模块级导入拉起了 master_agent，违反 D5"

    def test_module_level_has_no_master_agent_import(self):
        """源码静态检查：模块顶层不得 import master_agent / src.core.agent"""
        import inspect
        import src.background_runner as br
        source = inspect.getsource(br)
        # 取顶层（非函数体内）import 区域的粗略校验：source 中不应出现顶层 import master_agent
        lines = source.splitlines()
        for line in lines:
            stripped = line.lstrip()
            # 仅检查顶层（无缩进）的 import/from 语句
            if (stripped.startswith("import ") or stripped.startswith("from ")) and line[0] != " ":
                assert "src.core.agent" not in stripped, \
                    f"background_runner 顶层不应 import master_agent: {line}"
                assert "master_agent" not in stripped, \
                    f"background_runner 顶层不应 import master_agent: {line}"


# ==================== background_runner._run 轻量流程（mock）====================

class TestBackgroundRunnerRun:
    """验证 _run 协程：初始化 → 启调度器 → 启心跳 → 停机"""

    def test_run_starts_scheduler_then_stops(self, tmp_path, monkeypatch):
        """_run 应启动 scheduler，并在 _stop.set 后优雅关闭"""
        import asyncio
        from src import background_runner

        # 重置 _stop
        background_runner._stop = asyncio.Event()
        # 心跳写到临时路径，避免污染 storage/.bg_runner_alive
        monkeypatch.setattr(background_runner, "HEARTBEAT_FILE", str(tmp_path / ".bg_runner_alive"))

        mock_manager = MagicMock()

        with patch("src.background_runner._init_resources", return_value=None), \
             patch("src.scheduler.manager.scheduled_task_manager", mock_manager):

            # 在后台跑 _run，然后触发停机
            async def _driver():
                task = asyncio.create_task(background_runner._run())
                # 让 _run 有机会执行到 await _stop.wait()
                await asyncio.sleep(0.05)
                background_runner._stop.set()
                await asyncio.wait_for(task, timeout=5)

            asyncio.new_event_loop().run_until_complete(_driver())

        mock_manager.start.assert_called_once()
        mock_manager.shutdown.assert_called_once()
