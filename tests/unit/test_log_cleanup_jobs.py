"""日志清理调度任务单元测试

覆盖 src/scheduler/manager.py 三个日志清理回调：
- _run_log_file_cleanup：委托 cleanup_old_logs，异常隔离返回 0
- _run_log_error_cleanup：分批删除，批满继续、未满停止、异常返回已删行数
- _run_temp_log_cleanup：清空 log/temp 全部文件与目录，异常隔离
"""

from unittest.mock import MagicMock, patch

import pytest

from src.scheduler.manager import ScheduledTaskManager


@pytest.fixture
def manager():
    return ScheduledTaskManager()


class TestRunLogFileCleanup:
    def test_delegates_to_cleanup_old_logs(self, manager):
        """正常路径委托 cleanup_old_logs 并返回删除数"""
        with patch("src.core.log_retention.cleanup_old_logs", return_value=7) as m:
            assert manager._run_log_file_cleanup() == 7
        m.assert_called_once()

    def test_exception_returns_zero(self, manager):
        """清理异常时返回 0，不向上抛"""
        with patch(
            "src.core.log_retention.cleanup_old_logs",
            side_effect=RuntimeError("boom"),
        ):
            assert manager._run_log_file_cleanup() == 0


def _make_db_conn(batch_results):
    """构造 get_db_connection 的 mock：每次 execute 后 rowcount 依次取值"""
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.rowcount = 0
    results = list(batch_results)

    def _execute(*args, **kwargs):
        cursor.rowcount = results.pop(0)

    cursor.execute.side_effect = _execute
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


class TestRunLogErrorCleanup:
    def test_single_batch_under_limit(self, manager):
        """删除行数小于批大小时一次完成"""
        ctx = _make_db_conn([3])
        with patch("src.db.database.get_db_connection", return_value=ctx):
            assert manager._run_log_error_cleanup() == 3
        assert ctx.__enter__.return_value.cursor.return_value.execute.call_count == 1

    def test_full_batch_loops_until_short(self, manager):
        """批满 1 万行时继续下一批，直到不足批大小停止"""
        ctx = _make_db_conn([10000, 10000, 120])
        with patch("src.db.database.get_db_connection", return_value=ctx):
            assert manager._run_log_error_cleanup() == 20120
        assert ctx.__enter__.return_value.cursor.return_value.execute.call_count == 3

    def test_exception_returns_deleted_so_far(self, manager):
        """中途异常返回已删除行数，不向上抛"""
        ctx = _make_db_conn([10000])
        cursor = ctx.__enter__.return_value.cursor.return_value
        calls = {"n": 0}
        orig = cursor.execute.side_effect

        def _execute(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("db down")
            orig(*args, **kwargs)

        cursor.execute.side_effect = _execute
        with patch("src.db.database.get_db_connection", return_value=ctx):
            assert manager._run_log_error_cleanup() == 10000


class TestRunTempLogCleanup:
    def test_removes_all_entries(self, tmp_path, manager, monkeypatch):
        """文件与子目录全部删除"""
        monkeypatch.chdir(tmp_path)
        temp = tmp_path / "log" / "temp"
        temp.mkdir(parents=True)
        (temp / "语音合并.log").write_text("x")
        (temp / "外部推送.log").write_text("y")
        sub = temp / "sub"
        sub.mkdir()
        (sub / "inner.log").write_text("z")

        cleaned = manager._run_temp_log_cleanup()

        assert cleaned == 3
        assert list(temp.iterdir()) == []

    def test_missing_dir_returns_zero(self, tmp_path, manager, monkeypatch):
        """log/temp 不存在时安全返回 0"""
        monkeypatch.chdir(tmp_path)
        assert manager._run_temp_log_cleanup() == 0

    def test_single_failure_does_not_block_others(self, tmp_path, manager, monkeypatch):
        """单个文件删除失败不阻断其余清理"""
        monkeypatch.chdir(tmp_path)
        temp = tmp_path / "log" / "temp"
        temp.mkdir(parents=True)
        bad = temp / "bad.log"
        bad.write_text("x")
        good = temp / "good.log"
        good.write_text("y")

        real_unlink = type(good).unlink

        def _fake_unlink(self, *args, **kwargs):
            if self.name == "bad.log":
                raise PermissionError("denied")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr("pathlib.Path.unlink", _fake_unlink)

        cleaned = manager._run_temp_log_cleanup()

        assert cleaned == 1
        assert bad.exists()
        assert not good.exists()
