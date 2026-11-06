"""图片工具路径沙箱 guard 单元测试（P1-1 修复回归）"""
from pathlib import Path

import pytest

from src.tools.image._path_guard import guard_local_image_path


class TestGuardLocalImagePath:
    def test_tenant_dir_allowed(self):
        from src.tools.image import _path_guard
        project_root = Path(_path_guard.__file__).resolve().parents[3]
        tenant_dir = project_root / "storage" / "tenants" / "t_test_guard"
        tenant_dir.mkdir(parents=True, exist_ok=True)
        tenant_file = tenant_dir / "img.png"
        tenant_file.touch()
        try:
            resolved = guard_local_image_path(tenant_file, "t_test_guard")
            assert resolved == tenant_file.resolve()
        finally:
            tenant_file.unlink()

    def test_tmp_dir_allowed(self):
        import tempfile
        f = Path(tempfile.gettempdir()) / "guard_test_tmp.png"
        f.touch()
        try:
            assert guard_local_image_path(f, "t_x") == f.resolve()
        finally:
            f.unlink()

    def test_project_root_allowed(self):
        from src.tools.image import _path_guard
        project_root = Path(_path_guard.__file__).resolve().parents[3]
        p = project_root / "README.md"
        assert guard_local_image_path(p, "t_x") == p.resolve()

    def test_outside_rejected(self):
        with pytest.raises(ValueError, match="超出允许范围"):
            guard_local_image_path(Path("/etc/passwd"), "t_x")

    def test_other_tenant_rejected(self):
        """跨租户路径拒绝：当前租户 t_a 不能读 t_b 目录"""
        from src.tools.image import _path_guard
        project_root = Path(_path_guard.__file__).resolve().parents[3]
        other = project_root / "storage" / "tenants" / "t_other_tenant" / "x.png"
        with pytest.raises(ValueError, match="禁止读取"):
            guard_local_image_path(other, "t_a")
