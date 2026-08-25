"""skill_ws 临时工作目录残留清理单元测试

覆盖 src/scheduler/manager.py `ScheduledTaskManager._run_skill_ws_cleanup`：
- 超期（>3 天）skill_ws_* 目录被清理
- 新鲜（<3 天）skill_ws_* 目录保留
- 非 skill_ws_* 目录忽略
- tenants 根不存在返回 0
- 单个目录删除失败不阻断其余清理（异常隔离）
"""

import os
import time
from unittest.mock import patch

import pytest

from src.scheduler.manager import ScheduledTaskManager


@pytest.fixture
def manager():
    return ScheduledTaskManager()


def _touch_ws(root: str, name: str, mtime: float) -> str:
    """创建 skill_ws 目录并设置 mtime（秒级精度，避免精度抖动）"""
    d = os.path.join(root, "t1", "temp", name)
    os.makedirs(d)
    os.utime(d, (mtime, mtime))
    # 内部文件同样设置旧 mtime，确保 st_mtime 由目录自身控制
    f = os.path.join(d, "voice.wav")
    with open(f, "w") as fh:
        fh.write("x")
    os.utime(f, (mtime, mtime))
    os.utime(d, (mtime, mtime))
    return d


class TestRunSkillWsCleanup:
    @patch("src.core.storage.get_tenants_storage_root")
    def test_removes_expired_keeps_fresh(self, mock_root, tmp_path, manager):
        """超期目录清理、新鲜目录保留"""
        mock_root.return_value = str(tmp_path)
        now = time.time()
        expired = _touch_ws(str(tmp_path), "skill_ws_old", now - 4 * 24 * 3600)
        fresh = _touch_ws(str(tmp_path), "skill_ws_new", now - 3600)

        cleaned = manager._run_skill_ws_cleanup()

        assert cleaned == 1
        assert not os.path.exists(expired)
        assert os.path.exists(fresh)

    @patch("src.core.storage.get_tenants_storage_root")
    def test_ignores_non_skill_ws_dirs(self, mock_root, tmp_path, manager):
        """非 skill_ws_* 的目录（如 conversation/upload/图片）不得被清理"""
        mock_root.return_value = str(tmp_path)
        now = time.time()
        other = os.path.join(str(tmp_path), "t1", "temp", "upload")
        os.makedirs(other)
        os.utime(other, (now - 10 * 24 * 3600, now - 10 * 24 * 3600))

        cleaned = manager._run_skill_ws_cleanup()

        assert cleaned == 0
        assert os.path.exists(other)

    @patch("src.core.storage.get_tenants_storage_root")
    def test_missing_tenants_root_returns_zero(self, mock_root, tmp_path, manager):
        """tenants 根目录不存在时安全返回 0"""
        mock_root.return_value = str(tmp_path / "not_exists")

        cleaned = manager._run_skill_ws_cleanup()

        assert cleaned == 0

    @patch("src.core.storage.get_tenants_storage_root")
    def test_single_failure_does_not_block_others(self, mock_root, tmp_path, manager, monkeypatch):
        """某个 skill_ws 目录删除失败不阻断其余目录清理"""
        import shutil

        mock_root.return_value = str(tmp_path)
        now = time.time()
        bad = _touch_ws(str(tmp_path), "skill_ws_bad", now - 5 * 24 * 3600)
        good = _touch_ws(str(tmp_path), "skill_ws_good", now - 5 * 24 * 3600)

        _real_rmtree = shutil.rmtree  # patch 前保存真实引用

        def _fake_rmtree(path, *args, **kwargs):
            if "skill_ws_bad" in str(path):
                raise PermissionError("denied")
            return _real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr("shutil.rmtree", _fake_rmtree)

        cleaned = manager._run_skill_ws_cleanup()

        # bad 删除失败被异常隔离（warning），good 正常清理
        assert cleaned == 1
        assert os.path.exists(bad)
        assert not os.path.exists(good)
