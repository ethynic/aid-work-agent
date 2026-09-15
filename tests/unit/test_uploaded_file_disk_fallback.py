"""
find_uploaded_file_on_disk / resolve_uploaded_file_path 的单测

背景（tr_56de3d10c0a842b4）：迁移后 Redis 元数据丢失，excel 等工具按
file_id 解析时磁盘兜底只扫 conversation/ 场景目录，templates/ 等场景
解析失败报「文件不存在」。统一兜底改为全场景扫描 + 命中后回写 Redis 自愈。
"""

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.tools]


def _make_redis_patches():
    """Redis 全 mock（hgetall miss + hset/expire 可调用断言）"""
    return (
        patch("src.core.redis_client.redis_client.hgetall", return_value={}),
        patch("src.core.redis_client.redis_client.hset", return_value=1),
        patch("src.core.redis_client.redis_client.expire", return_value=True),
        patch(
            "src.core.redis_client.redis_client.make_key",
            side_effect=lambda prefix, fid: f"{prefix}:{fid}",
        ),
    )


class TestFindUploadedFileOnDisk:
    """find_uploaded_file_on_disk 全场景磁盘扫描"""

    def test_hit_in_templates_scene(self, tmp_path, monkeypatch):
        """templates/ 场景目录下的文件可按 file_id 命中（本次事故场景）"""
        from src.core.storage import find_uploaded_file_on_disk

        tenants_root = tmp_path / "tenants"
        scene_dir = tenants_root / "c148f4efb4dc" / "templates"
        scene_dir.mkdir(parents=True)
        target = scene_dir / "file_27ea4f110405.xlsx"
        target.write_text("x")

        monkeypatch.setattr("src.core.storage._TENANTS_ROOT", str(tenants_root))

        hgetall, hset, expire, make_key = _make_redis_patches()
        with hgetall, hset as m_hset, expire as m_expire, make_key:
            info = find_uploaded_file_on_disk("file_27ea4f110405")

        assert info is not None
        assert Path(info["path"]).resolve() == target.resolve()
        assert info["mime_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert info["type"] == "file"
        # 自愈回写 Redis，TTL 24h
        m_hset.assert_called()
        m_expire.assert_called_once()

    def test_hit_in_scene_subdir(self, tmp_path, monkeypatch):
        """scene 下 1 层子目录（如 images/2026-08/）也可命中"""
        from src.core.storage import find_uploaded_file_on_disk

        tenants_root = tmp_path / "tenants"
        sub_dir = tenants_root / "tenant_a" / "images" / "2026-08"
        sub_dir.mkdir(parents=True)
        target = sub_dir / "file_img000001.png"
        target.write_text("x")

        monkeypatch.setattr("src.core.storage._TENANTS_ROOT", str(tenants_root))

        hgetall, hset, expire, make_key = _make_redis_patches()
        with hgetall, hset, expire, make_key:
            info = find_uploaded_file_on_disk("file_img000001")

        assert info is not None
        assert info["type"] == "image"
        assert info["mime_type"] == "image/png"

    def test_input_with_extension_matches_stem(self, tmp_path, monkeypatch):
        """输入带扩展名（file_xxx.xlsx）也按 stem 匹配命中"""
        from src.core.storage import find_uploaded_file_on_disk

        tenants_root = tmp_path / "tenants"
        scene_dir = tenants_root / "tenant_a" / "knowledge"
        scene_dir.mkdir(parents=True)
        target = scene_dir / "file_kb00000001.xlsx"
        target.write_text("x")

        monkeypatch.setattr("src.core.storage._TENANTS_ROOT", str(tenants_root))

        hgetall, hset, expire, make_key = _make_redis_patches()
        with hgetall, hset, expire, make_key:
            info = find_uploaded_file_on_disk("file_kb00000001.xlsx")

        assert info is not None
        assert Path(info["path"]).resolve() == target.resolve()

    def test_miss_returns_none(self, tmp_path, monkeypatch):
        """磁盘上不存在 -> 返回 None，不触发 Redis 回写"""
        from src.core.storage import find_uploaded_file_on_disk

        tenants_root = tmp_path / "tenants"
        (tenants_root / "tenant_a" / "conversation").mkdir(parents=True)
        monkeypatch.setattr("src.core.storage._TENANTS_ROOT", str(tenants_root))

        hgetall, hset, expire, make_key = _make_redis_patches()
        with hgetall, hset as m_hset, expire, make_key:
            info = find_uploaded_file_on_disk("file_nothere000")

        assert info is None
        m_hset.assert_not_called()

    def test_non_file_id_inputs_rejected(self):
        """无扩展名且非 file_ 前缀输入不做扫描；空输入直接拒绝"""
        from src.core.storage import find_uploaded_file_on_disk

        assert find_uploaded_file_on_disk("report") is None
        assert find_uploaded_file_on_disk("") is None

    def test_register_disabled_skips_redis(self, tmp_path, monkeypatch):
        """register_to_redis=False 时不回写 Redis"""
        from src.core.storage import find_uploaded_file_on_disk

        tenants_root = tmp_path / "tenants"
        scene_dir = tenants_root / "tenant_a" / "report"
        scene_dir.mkdir(parents=True)
        (scene_dir / "file_rp000000001.png").write_text("x")
        monkeypatch.setattr("src.core.storage._TENANTS_ROOT", str(tenants_root))

        hgetall, hset, expire, make_key = _make_redis_patches()
        with hgetall, hset as m_hset, expire as m_expire, make_key:
            info = find_uploaded_file_on_disk("file_rp000000001", register_to_redis=False)

        assert info is not None
        m_hset.assert_not_called()
        m_expire.assert_not_called()


class TestResolveUploadedFilePath:
    """resolve_uploaded_file_path 组合入口"""

    def test_redis_hit_takes_priority(self, tmp_path, monkeypatch):
        """Redis 元数据命中优先于磁盘扫描"""
        from src.core.storage import resolve_uploaded_file_path

        real_file = tmp_path / "file_via_redis.xlsx"
        real_file.write_text("x")

        fake_redis_data = {
            "uploaded_file:file_redishit01": {"path": str(real_file.absolute())}
        }

        with patch(
            "src.core.redis_client.redis_client.hgetall",
            side_effect=lambda k: fake_redis_data.get(k, {}),
        ), patch(
            "src.core.redis_client.redis_client.make_key",
            side_effect=lambda prefix, fid: f"{prefix}:{fid}",
        ), patch(
            "src.core.storage.find_uploaded_file_on_disk"
        ) as mock_scan:
            resolved = resolve_uploaded_file_path("file_redishit01")

        assert Path(resolved).resolve() == real_file.resolve()
        mock_scan.assert_not_called()

    def test_disk_fallback_when_redis_miss(self, tmp_path, monkeypatch):
        """Redis miss -> 磁盘全场景扫描兜底"""
        from src.core.storage import resolve_uploaded_file_path

        tenants_root = tmp_path / "tenants"
        scene_dir = tenants_root / "tenant_a" / "templates"
        scene_dir.mkdir(parents=True)
        target = scene_dir / "file_diskf000001.xlsx"
        target.write_text("x")
        monkeypatch.setattr("src.core.storage._TENANTS_ROOT", str(tenants_root))

        hgetall, hset, expire, make_key = _make_redis_patches()
        with hgetall, hset, expire, make_key:
            resolved = resolve_uploaded_file_path("file_diskf000001")

        assert Path(resolved).resolve() == target.resolve()

    def test_none_when_not_found(self, monkeypatch):
        """Redis 和磁盘都未命中 -> None"""
        from src.core.storage import resolve_uploaded_file_path

        monkeypatch.setattr(
            "src.core.storage._TENANTS_ROOT", "/nonexistent_tenants_root"
        )

        hgetall, hset, expire, make_key = _make_redis_patches()
        with hgetall, hset, expire, make_key:
            assert resolve_uploaded_file_path("file_nothing0000") is None


class TestHandlersUseFullSceneFallback:
    """三个文件 Handler 的 resolve_path 走全场景兜底（事故回归）"""

    def _resolve_via_handler(self, handler_cls, file_id, tmp_path, monkeypatch):
        tenants_root = tmp_path / "tenants"
        scene_dir = tenants_root / "tenant_a" / "templates"
        scene_dir.mkdir(parents=True)
        target = scene_dir / f"{file_id}.xlsx"
        target.write_text("x")
        monkeypatch.setattr("src.core.storage._TENANTS_ROOT", str(tenants_root))

        hgetall, hset, expire, make_key = _make_redis_patches()
        with hgetall, hset, expire, make_key:
            return handler_cls.resolve_path(file_id), target

    def test_excel_resolve_path_templates_scene(self, tmp_path, monkeypatch):
        """ExcelFileHandler.resolve_path：Redis miss + templates/ 场景 -> 扫描命中"""
        from src.tools.excel.excel_lib import ExcelFileHandler

        resolved, target = self._resolve_via_handler(
            ExcelFileHandler, "file_27ea4f110405", tmp_path, monkeypatch
        )
        assert Path(resolved).resolve() == target.resolve()

    def test_word_resolve_path_templates_scene(self, tmp_path, monkeypatch):
        """WordFileHandler.resolve_path：Redis miss + templates/ 场景 -> 扫描命中"""
        from src.tools.word.word_lib import WordFileHandler

        resolved, target = self._resolve_via_handler(
            WordFileHandler, "file_wordtpl0001", tmp_path, monkeypatch
        )
        assert Path(resolved).resolve() == target.resolve()

    def test_pdf_resolve_path_templates_scene(self, tmp_path, monkeypatch):
        """PdfFileHandler.resolve_path：Redis miss + templates/ 场景 -> 扫描命中（此前 pdf 连 Redis 都不查）"""
        from src.tools.pdf.pdf_lib import PdfFileHandler

        resolved, target = self._resolve_via_handler(
            PdfFileHandler, "file_pdftpl00001", tmp_path, monkeypatch
        )
        assert Path(resolved).resolve() == target.resolve()
