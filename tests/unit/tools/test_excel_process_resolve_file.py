"""
excel_process_tool file_id 解析修复的单测

覆盖：
- ExcelFileHandler.resolve_path: Redis 元数据命中 / 目录扫描兜底 / 找不到
- ExcelProcessTool._resolve_file: file_id 解析为磁盘路径 / 找不到抛 FileNotFoundError
- _handle_to_md / _handle_read 等 handler: file_id 输入正确解析
"""

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.tools]


class TestResolvePathViaRedis:
    """resolve_path 通过 Redis 元数据命中"""

    def test_file_id_resolved_via_redis(self, tmp_path):
        """file_id 在 Redis 有元数据 -> 直接返回 path 字段"""
        from src.tools.excel.excel_lib import ExcelFileHandler

        # 造一个真实文件
        real_file = tmp_path / "templates" / "file_abc123.xlsx"
        real_file.parent.mkdir(parents=True)
        real_file.write_text("x")

        fake_redis_data = {
            "uploaded_file:file_abc123": {
                "file_id": "file_abc123",
                "name": "模板.xlsx",
                "path": str(real_file.absolute()),
                "size": "1",
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
        }

        with patch(
            "src.core.redis_client.redis_client.hgetall",
            side_effect=lambda k: fake_redis_data.get(k, {}),
        ), patch(
            "src.core.redis_client.redis_client.make_key",
            side_effect=lambda prefix, fid: f"{prefix}:{fid}",
        ):
            result = ExcelFileHandler.resolve_path("file_abc123")

        assert Path(result).resolve() == real_file.resolve()

    def test_file_id_redis_miss_falls_through(self, tmp_path, monkeypatch):
        """file_id 在 Redis 无元数据 -> 走目录扫描兜底"""
        from src.tools.excel.excel_lib import ExcelFileHandler

        # 造一个新路径下的文件
        tenants_root = tmp_path / "tenants"
        tenant_dir = tenants_root / "tenant_x" / "conversation"
        tenant_dir.mkdir(parents=True)
        target = tenant_dir / "file_xyz.xlsx"
        target.write_text("x")

        # patch _TENANTS_ROOT 指向 tmp_path/tenants
        from src.core import storage as storage_mod
        monkeypatch.setattr(storage_mod, "_TENANTS_ROOT", str(tenants_root))

        # Redis miss
        with patch(
            "src.core.redis_client.redis_client.hgetall", return_value={}
        ), patch(
            "src.core.redis_client.redis_client.make_key",
            side_effect=lambda prefix, fid: f"{prefix}:{fid}",
        ):
            result = ExcelFileHandler.resolve_path("file_xyz.xlsx")

        assert Path(result).resolve() == target.resolve()

    def test_non_file_id_input_skips_redis(self):
        """非 file_ 前缀的输入不查 Redis（避免无谓的 Redis 调用）"""
        from src.tools.excel.excel_lib import ExcelFileHandler

        with patch(
            "src.core.redis_client.redis_client.hgetall"
        ) as mock_hgetall:
            # "report.xlsx" 不是 file_id，不应查 Redis
            ExcelFileHandler.resolve_path("nonexistent_report.xlsx")
            mock_hgetall.assert_not_called()

    def test_redis_path_not_exists_returns_none(self, tmp_path):
        """Redis 元数据的 path 字段指向不存在的文件 -> 返回 None，走兜底"""
        from src.tools.excel.excel_lib import _resolve_path_via_redis

        fake_redis_data = {
            "uploaded_file:file_ghost": {
                "path": "/nonexistent/path/file_ghost.xlsx",
            }
        }

        with patch(
            "src.core.redis_client.redis_client.hgetall",
            side_effect=lambda k: fake_redis_data.get(k, {}),
        ), patch(
            "src.core.redis_client.redis_client.make_key",
            side_effect=lambda prefix, fid: f"{prefix}:{fid}",
        ):
            result = _resolve_path_via_redis("file_ghost")

        assert result is None


class TestExcelProcessResolveFile:
    """ExcelProcessTool._resolve_file 行为"""

    def test_resolve_file_id_success(self, tmp_path):
        """file_id 通过 Redis 解析成功"""
        from src.tools.excel.excel_process_tool import ExcelProcessTool

        real_file = tmp_path / "file_ok.xlsx"
        real_file.write_text("x")

        fake_redis_data = {
            "uploaded_file:file_ok": {"path": str(real_file.absolute())}
        }

        tool = ExcelProcessTool()
        with patch(
            "src.core.redis_client.redis_client.hgetall",
            side_effect=lambda k: fake_redis_data.get(k, {}),
        ), patch(
            "src.core.redis_client.redis_client.make_key",
            side_effect=lambda prefix, fid: f"{prefix}:{fid}",
        ):
            resolved = tool._resolve_file("file_ok")

        assert Path(resolved).resolve() == real_file.resolve()

    def test_resolve_file_not_found_raises(self):
        """file_id 找不到 -> 抛 FileNotFoundError"""
        from src.tools.excel.excel_process_tool import ExcelProcessTool

        tool = ExcelProcessTool()
        with patch(
            "src.core.redis_client.redis_client.hgetall", return_value={}
        ):
            with pytest.raises(FileNotFoundError, match="文件不存在"):
                tool._resolve_file("file_nonexistent_xyz")


class TestHandlerUsesResolveFile:
    """handler 入口正确调用 _resolve_file"""

    def test_handle_to_md_file_id_resolved(self, tmp_path):
        """to_md handler 接收 file_id 输入时正确解析"""
        from src.tools.excel.excel_process_tool import ExcelProcessTool, PipelineContext

        # 造一个真实 xlsx 文件（最小有效 xlsx 是 zip 格式，这里用 mock excel_to_markdown）
        real_file = tmp_path / "file_md.xlsx"
        real_file.write_text("x")

        fake_redis_data = {
            "uploaded_file:file_md": {"path": str(real_file.absolute())}
        }

        tool = ExcelProcessTool()
        ctx = PipelineContext(file_paths=["file_md"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall",
                side_effect=lambda k: fake_redis_data.get(k, {}),
            ), patch(
                "src.core.redis_client.redis_client.make_key",
                side_effect=lambda prefix, fid: f"{prefix}:{fid}",
            ), patch(
                "src.tools.excel.excel_to_md.excel_to_markdown"
            ) as mock_to_md:
                mock_to_md.return_value = {"success": True, "markdown": "# OK"}
                result = await tool._handle_to_md(ctx, {})
                # 验证传给 excel_to_markdown 的是解析后的真实路径
                called_path = mock_to_md.call_args[0][0]
                assert Path(called_path).resolve() == real_file.resolve()
                return result

        result = asyncio.run(run())
        assert result["success"] is True

    def test_handle_to_md_file_not_found_returns_error(self):
        """to_md handler 接收不存在的 file_id -> 返回 success=False"""
        from src.tools.excel.excel_process_tool import ExcelProcessTool, PipelineContext

        tool = ExcelProcessTool()
        ctx = PipelineContext(file_paths=["file_ghost_xyz"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall", return_value={}
            ):
                return await tool._handle_to_md(ctx, {})

        result = asyncio.run(run())
        assert result["success"] is False
        assert "文件不存在" in result["error"]

    def test_handle_read_file_id_resolved(self, tmp_path):
        """read handler 接收 file_id 输入时正确解析"""
        from src.tools.excel.excel_process_tool import ExcelProcessTool, PipelineContext

        real_file = tmp_path / "file_read.xlsx"
        real_file.write_text("x")

        fake_redis_data = {
            "uploaded_file:file_read": {"path": str(real_file.absolute())}
        }

        tool = ExcelProcessTool()
        ctx = PipelineContext(file_paths=["file_read"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall",
                side_effect=lambda k: fake_redis_data.get(k, {}),
            ), patch(
                "src.core.redis_client.redis_client.make_key",
                side_effect=lambda prefix, fid: f"{prefix}:{fid}",
            ), patch(
                "src.tools.excel.excel_reader.read_excel_document"
            ) as mock_read:
                mock_read.return_value = {"success": True, "headers": [], "rows": []}
                await tool._handle_read(ctx, {})
                called_path = mock_read.call_args[0][0]
                assert Path(called_path).resolve() == real_file.resolve()

        asyncio.run(run())
