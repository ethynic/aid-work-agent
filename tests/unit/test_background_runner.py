"""独立后台运行时单元测试（plan-background-runner.md §5）

覆盖：
- ScheduledTaskDB.list_all_for_reconcile / request_manual_trigger / clear_manual_trigger（mock DB）
- ScheduledTaskManager._reconcile 全分支（mock list_all_for_reconcile）：
  新增注册 / 暂停移除 / DB 删除移除 / 手动触发+清空 / 签名不变不重注册
- _register_system_jobs 注册 reconcile / memory_cleanup / dedup_cleanup / wecom_kf_timeout（按间隔/条件）
- background_runner import 安全（不拉起 master_agent）
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ==================== ScheduledTaskDB 三方法（mock DB）====================

class TestScheduledTaskDBReconcileMethods:
    """db.py 新增三方法的 SQL/参数行为（mock get_db_connection）"""

    @patch("src.scheduler.db.get_db_connection")
    def test_list_all_for_reconcile_returns_active_and_paused(self, mock_conn):
        """list_all_for_reconcile 只返回 active + paused，含 manual_trigger_at/updated_at"""
        from src.scheduler.db import ScheduledTaskDB

        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {"task_id": "t1", "status": "active", "manual_trigger_at": None, "updated_at": "2026-07-21"},
            {"task_id": "t2", "status": "paused", "manual_trigger_at": None, "updated_at": "2026-07-20"},
        ]
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        rows = ScheduledTaskDB.list_all_for_reconcile()
        assert len(rows) == 2
        assert {r["task_id"] for r in rows} == {"t1", "t2"}
        # SQL 必须过滤 active + paused（不能只 active，否则 paused 任务无法被移除）
        executed_sql = cursor.execute.call_args[0][0]
        assert "active" in executed_sql and "paused" in executed_sql

    @patch("src.scheduler.db.get_db_connection")
    def test_request_manual_trigger_sets_now(self, mock_conn):
        """request_manual_trigger 写 NOW()，且只对 active/paused 生效"""
        from src.scheduler.db import ScheduledTaskDB

        cursor = MagicMock()
        cursor.rowcount = 1
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        result = ScheduledTaskDB.request_manual_trigger("t1")
        assert result is True
        sql, params = cursor.execute.call_args[0][0], cursor.execute.call_args[0][1]
        assert "manual_trigger_at = NOW()" in sql
        assert params == ("t1",)
        # SQL 必须 WHERE 限制 status，避免对 cancelled 任务误触发
        assert "active" in sql and "paused" in sql

    @patch("src.scheduler.db.get_db_connection")
    def test_request_manual_trigger_returns_false_when_no_row(self, mock_conn):
        """无匹配行返回 False"""
        from src.scheduler.db import ScheduledTaskDB

        cursor = MagicMock()
        cursor.rowcount = 0
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        assert ScheduledTaskDB.request_manual_trigger("ghost") is False

    @patch("src.scheduler.db.get_db_connection")
    def test_clear_manual_trigger_sets_null(self, mock_conn):
        """clear_manual_trigger 把 manual_trigger_at 置 NULL"""
        from src.scheduler.db import ScheduledTaskDB

        cursor = MagicMock()
        cursor.rowcount = 1
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        assert ScheduledTaskDB.clear_manual_trigger("t1") is True
        sql = cursor.execute.call_args[0][0]
        assert "manual_trigger_at = NULL" in sql


# ==================== ScheduledTaskManager._reconcile 全分支 ====================

@pytest.fixture
def manager_with_fake_scheduler():
    """构造一个 ScheduledTaskManager，scheduler 是 mock，绕过 Redis 锁 + DB 启动加载。

    直接 __new__ 跳过 __init__，手动设置必要字段，避免触发 start() 的完整初始化链路。
    """
    from src.scheduler.manager import ScheduledTaskManager

    m = ScheduledTaskManager.__new__(ScheduledTaskManager)
    m._scheduler = MagicMock()
    m._jobs = {}
    m._running = True
    m._lock_value = None
    m._reconcile_seen = {}

    # _register_job 内部会调 _scheduler.add_job 返回的 job.id；mock 返回一个假 job
    fake_job = MagicMock()
    fake_job.id = "job_fake"
    m._scheduler.add_job.return_value = fake_job

    return m


class TestReconcile:
    """_reconcile 对账核心逻辑（D11）"""

    def test_register_new_active_task(self, manager_with_fake_scheduler):
        """DB 新增 active 任务 → 注册到 APScheduler + 记录签名"""
        m = manager_with_fake_scheduler
        with patch("src.scheduler.manager.ScheduledTaskDB") as mock_db:
            mock_db.list_all_for_reconcile.return_value = [
                {"task_id": "t1", "schedule_type": "cron", "cron_expression": "0 9 * * *",
                 "interval_seconds": None, "status": "active", "updated_at": "2026-07-21 10:00:00",
                 "manual_trigger_at": None, "name": "T1"}
            ]
            m._reconcile()

        m._scheduler.add_job.assert_called_once()
        assert "t1" in m._jobs
        assert "t1" in m._reconcile_seen

    def test_skip_reregister_when_signature_unchanged(self, manager_with_fake_scheduler):
        """签名不变（updated_at 相同）→ 不重注册（避免重置 interval 计时）"""
        m = manager_with_fake_scheduler
        sig_tuple = ("cron", "0 9 * * *", None, "active", "2026-07-21 10:00:00")
        # 预置已注册状态
        m._reconcile_seen["t1"] = sig_tuple
        m._jobs["t1"] = "job_t1"

        with patch("src.scheduler.manager.ScheduledTaskDB") as mock_db:
            mock_db.list_all_for_reconcile.return_value = [
                {"task_id": "t1", "schedule_type": "cron", "cron_expression": "0 9 * * *",
                 "interval_seconds": None, "status": "active", "updated_at": "2026-07-21 10:00:00",
                 "manual_trigger_at": None, "name": "T1"}
            ]
            m._reconcile()

        # 未变化 → 不应再调 add_job
        m._scheduler.add_job.assert_not_called()

    def test_reregister_when_schedule_changes(self, manager_with_fake_scheduler):
        """cron 变化（updated_at 变化）→ 重注册"""
        m = manager_with_fake_scheduler
        m._reconcile_seen["t1"] = ("cron", "0 9 * * *", None, "active", "2026-07-20 10:00:00")
        m._jobs["t1"] = "job_t1_old"

        with patch("src.scheduler.manager.ScheduledTaskDB") as mock_db:
            mock_db.list_all_for_reconcile.return_value = [
                {"task_id": "t1", "schedule_type": "cron", "cron_expression": "0 10 * * *",
                 "interval_seconds": None, "status": "active", "updated_at": "2026-07-21 10:00:00",
                 "manual_trigger_at": None, "name": "T1"}
            ]
            m._reconcile()

        m._scheduler.add_job.assert_called_once()

    def test_paused_task_removed_from_scheduler(self, manager_with_fake_scheduler):
        """active → paused：从调度器移除，签名缓存清理"""
        m = manager_with_fake_scheduler
        m._jobs["t1"] = "job_t1"
        m._reconcile_seen["t1"] = ("cron", "0 9 * * *", None, "active", "2026-07-20")
        m._scheduler.remove_job.return_value = None  # remove_task 内部会调

        with patch("src.scheduler.manager.ScheduledTaskDB") as mock_db:
            mock_db.list_all_for_reconcile.return_value = [
                {"task_id": "t1", "schedule_type": "cron", "cron_expression": "0 9 * * *",
                 "interval_seconds": None, "status": "paused", "updated_at": "2026-07-21 10:00:00",
                 "manual_trigger_at": None, "name": "T1"}
            ]
            m._reconcile()

        assert "t1" not in m._jobs
        assert "t1" not in m._reconcile_seen
        m._scheduler.remove_job.assert_called()

    def test_db_deleted_task_removed_from_scheduler(self, manager_with_fake_scheduler):
        """DB 中已不存在的任务（cancel/delete）→ 从调度器移除"""
        m = manager_with_fake_scheduler
        m._jobs["t_ghost"] = "job_ghost"
        m._reconcile_seen["t_ghost"] = ("cron", "0 9 * * *", None, "active", "2026-07-20")

        with patch("src.scheduler.manager.ScheduledTaskDB") as mock_db:
            mock_db.list_all_for_reconcile.return_value = []  # DB 空
            m._reconcile()

        assert "t_ghost" not in m._jobs
        assert "t_ghost" not in m._reconcile_seen

    def test_manual_trigger_fires_once_and_clears(self, manager_with_fake_scheduler):
        """manual_trigger_at 非空 → _fire_once 执行 + 清空标记"""
        m = manager_with_fake_scheduler
        fire_calls = []

        with patch("src.scheduler.manager.ScheduledTaskDB") as mock_db, \
             patch.object(m, "_fire_once", side_effect=lambda tid, trigger_type="manual": fire_calls.append((tid, trigger_type))) as mock_fire:
            mock_db.list_all_for_reconcile.return_value = [
                {"task_id": "t1", "schedule_type": "cron", "cron_expression": "0 9 * * *",
                 "interval_seconds": None, "status": "active", "updated_at": "2026-07-21 10:00:00",
                 "manual_trigger_at": "2026-07-21 10:00:05", "name": "T1"}
            ]
            m._reconcile()

        mock_fire.assert_called_once_with("t1", trigger_type="manual")
        mock_db.clear_manual_trigger.assert_called_once_with("t1")

    def test_reconcile_swallows_exceptions(self, manager_with_fake_scheduler):
        """DB 异常不应抛出（reconcile 是周期任务，单次失败不能崩调度器）"""
        m = manager_with_fake_scheduler
        with patch("src.scheduler.manager.ScheduledTaskDB") as mock_db:
            mock_db.list_all_for_reconcile.side_effect = RuntimeError("db down")
            # 不应抛
            m._reconcile()

    def test_reconcile_isolates_per_task_failures(self, manager_with_fake_scheduler):
        """单任务处理失败（如 _register_job 抛异常）不得中断同轮其他任务的对账。

        场景：t1 触发 _register_job 抛错，t2 正常。t2 必须仍被注册。
        修复背景：原实现把整轮 for-body 放在外层 try 内，一条畸形任务会
        每 30s 卡死所有其他任务的注册/移除/触发。
        """
        m = manager_with_fake_scheduler

        good_row = {
            "task_id": "t_good", "schedule_type": "cron", "cron_expression": "0 9 * * *",
            "interval_seconds": None, "status": "active", "updated_at": "2026-07-21 10:00:00",
            "manual_trigger_at": None, "name": "Good",
        }
        bad_row = {
            "task_id": "t_bad", "schedule_type": "cron", "cron_expression": "not_a_cron",
            "interval_seconds": None, "status": "active", "updated_at": "2026-07-21 10:00:00",
            "manual_trigger_at": None, "name": "Bad",
        }

        # _register_job 对 t_bad 抛错（模拟坏 cron / scheduler 错），对 t_good 正常
        def _fake_register(task):
            if task["task_id"] == "t_bad":
                raise ValueError("malformed cron")
            m._jobs[task["task_id"]] = "job_" + task["task_id"]
            return m._jobs[task["task_id"]]

        with patch("src.scheduler.manager.ScheduledTaskDB") as mock_db, \
             patch.object(m, "_register_job", side_effect=_fake_register):
            mock_db.list_all_for_reconcile.return_value = [bad_row, good_row]
            # 不应抛
            m._reconcile()

        # 关键断言：t_good 必须被注册成功（t_bad 的异常被隔离）
        assert "t_good" in m._jobs, "坏任务 t_bad 不应阻断好任务 t_good 的注册"
        assert "t_good" in m._reconcile_seen
        # t_bad 未注册成功
        assert "t_bad" not in m._jobs


# ==================== _register_system_jobs 注册验证 ====================

class TestRegisterSystemJobs:
    """验证 3 个迁入循环 + reconcile 按计划注册到 APScheduler（D14/D15/D11）"""

    def _make_manager(self):
        from src.scheduler.manager import ScheduledTaskManager
        m = ScheduledTaskManager.__new__(ScheduledTaskManager)
        m._scheduler = MagicMock()
        m._jobs = {}
        m._running = True
        m._lock_value = None
        m._reconcile_seen = {}
        return m

    def test_registers_reconcile_memory_cleanup_dedup_cleanup(self):
        """注册 reconcile(30s) / memory_cleanup / dedup_cleanup / memory_summarizer / wecom_kf_timeout"""
        m = self._make_manager()
        with patch("src.config.settings.settings") as mock_settings:
            mock_settings.memory.long_term.enabled = True
            mock_settings.memory.long_term.summary_cron = "0 2 * * *"
            mock_settings.memory.mid_term.enabled = False
            mock_settings.memory.mid_term.background_scan_enabled = False
            mock_settings.memory.cleanup_interval = 300

            m._register_system_jobs()

        # add_job 被调用：memory_summarizer + reconcile + memory_cleanup + dedup_cleanup + wecom_kf_timeout
        job_ids = [c.kwargs.get("id") for c in m._scheduler.add_job.call_args_list]
        assert "job_system_memory_summarizer" in job_ids
        assert "job_system_reconcile" in job_ids
        assert "job_system_memory_cleanup" in job_ids
        assert "job_system_dedup_cleanup" in job_ids
        # wecom_kf_timeout 无条件注册（SaaS 开关已移除）
        assert "job_system_wecom_kf_timeout" in job_ids

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

    def test_reconcile_uses_30s_interval(self):
        """reconcile 必须是 30s interval（D11，对账延迟上限）"""
        from apscheduler.triggers.interval import IntervalTrigger
        m = self._make_manager()
        with patch("src.config.settings.settings") as mock_settings:
            mock_settings.memory.long_term.enabled = False
            mock_settings.memory.mid_term.enabled = False
            mock_settings.memory.cleanup_interval = 300
            m._register_system_jobs()

        reconcile_call = next(
            c for c in m._scheduler.add_job.call_args_list if c.kwargs.get("id") == "job_system_reconcile"
        )
        trigger = reconcile_call.args[1] if len(reconcile_call.args) > 1 else reconcile_call.kwargs.get("trigger")
        assert isinstance(trigger, IntervalTrigger)
        assert trigger.interval.total_seconds() == 30


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
    """验证 _run 协程：初始化 → 启调度器 → 启 poller → 启心跳 → 停机"""

    def test_run_starts_scheduler_and_poller_then_stops(self, tmp_path, monkeypatch):
        """_run 应启动 scheduler + poller，并在 _stop.set 后优雅关闭"""
        import asyncio
        from src import background_runner

        # 重置 _stop
        background_runner._stop = asyncio.Event()
        # 心跳写到临时路径，避免污染 storage/.bg_runner_alive
        monkeypatch.setattr(background_runner, "HEARTBEAT_FILE", str(tmp_path / ".bg_runner_alive"))

        mock_manager = MagicMock()
        mock_poller = MagicMock()
        # poller 的 start/stop 是 async，必须用 AsyncMock 才能被 await
        mock_poller.start = AsyncMock()
        mock_poller.stop = AsyncMock()

        with patch("src.background_runner._init_resources", return_value=None), \
             patch("src.scheduler.manager.scheduled_task_manager", mock_manager), \
             patch("src.channels.wecom_personal_rpa.archive.poller.poller", mock_poller):

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
        mock_poller.start.assert_awaited_once()
        mock_poller.stop.assert_awaited_once()
