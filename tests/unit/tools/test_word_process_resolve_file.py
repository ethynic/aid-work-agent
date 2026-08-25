"""
word_process_tool file_id 解析修复的单测

覆盖：
- WordFileHandler.resolve_path: Redis 元数据命中 / 目录扫描兜底 / 找不到
- WordProcessTool._resolve_file: file_id 解析为磁盘路径 / 找不到抛 FileNotFoundError
- _handle_read / _handle_word_to_md / _handle_modify 等 handler: file_id 输入正确解析
- _handle_md_to_word: file_paths[0] 解析失败不阻塞流程（md_text 兜底）
- _handle_diff: 两个 file_path 都解析
"""

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.tools]


class TestResolvePathViaRedis:
    """WordFileHandler.resolve_path 通过 Redis 元数据命中"""

    def test_file_id_resolved_via_redis(self, tmp_path):
        """file_id 在 Redis 有元数据 -> 直接返回 path 字段"""
        from src.tools.word.word_lib import WordFileHandler

        # 造一个真实文件
        real_file = tmp_path / "templates" / "file_abc123.docx"
        real_file.parent.mkdir(parents=True)
        real_file.write_text("x")

        fake_redis_data = {
            "uploaded_file:file_abc123": {
                "file_id": "file_abc123",
                "name": "模板.docx",
                "path": str(real_file.absolute()),
                "size": "1",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        }

        with patch(
            "src.core.redis_client.redis_client.hgetall",
            side_effect=lambda k: fake_redis_data.get(k, {}),
        ), patch(
            "src.core.redis_client.redis_client.make_key",
            side_effect=lambda prefix, fid: f"{prefix}:{fid}",
        ):
            result = WordFileHandler.resolve_path("file_abc123")

        assert Path(result).resolve() == real_file.resolve()

    def test_file_id_redis_miss_falls_through(self, tmp_path, monkeypatch):
        """file_id 在 Redis 无元数据 -> 走目录扫描兜底"""
        from src.tools.word.word_lib import WordFileHandler

        # 造一个新路径下的文件
        tenants_root = tmp_path / "tenants"
        tenant_dir = tenants_root / "tenant_x" / "conversation"
        tenant_dir.mkdir(parents=True)
        target = tenant_dir / "file_xyz.docx"
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
            result = WordFileHandler.resolve_path("file_xyz.docx")

        assert Path(result).resolve() == target.resolve()

    def test_non_file_id_input_skips_redis(self):
        """非 file_ 前缀的输入不查 Redis（避免无谓的 Redis 调用）"""
        from src.tools.word.word_lib import WordFileHandler

        with patch(
            "src.core.redis_client.redis_client.hgetall"
        ) as mock_hgetall:
            # "report.docx" 不是 file_id，不应查 Redis
            WordFileHandler.resolve_path("nonexistent_report.docx")
            mock_hgetall.assert_not_called()

    def test_redis_path_not_exists_returns_none(self, tmp_path):
        """Redis 元数据的 path 字段指向不存在的文件 -> 返回 None，走兜底"""
        from src.core.storage import resolve_path_via_redis

        fake_redis_data = {
            "uploaded_file:file_ghost": {
                "path": "/nonexistent/path/file_ghost.docx",
            }
        }

        with patch(
            "src.core.redis_client.redis_client.hgetall",
            side_effect=lambda k: fake_redis_data.get(k, {}),
        ), patch(
            "src.core.redis_client.redis_client.make_key",
            side_effect=lambda prefix, fid: f"{prefix}:{fid}",
        ):
            result = resolve_path_via_redis("file_ghost")

        assert result is None

    def test_non_file_prefix_returns_none_directly(self):
        """非 file_ 前缀直接返回 None，不查 Redis"""
        from src.core.storage import resolve_path_via_redis

        with patch(
            "src.core.redis_client.redis_client.hgetall"
        ) as mock_hgetall:
            assert resolve_path_via_redis("report.docx") is None
            assert resolve_path_via_redis("") is None
            assert resolve_path_via_redis(None) is None
            mock_hgetall.assert_not_called()


class TestResolvePathTraversalGuard:
    """resolve_path 路径穿越防护"""

    def test_path_traversal_blocked(self):
        """含 .. 的相对路径不进入 exists 检查和目录扫描"""
        from src.tools.word.word_lib import WordFileHandler

        with patch(
            "src.core.redis_client.redis_client.hgetall", return_value={}
        ):
            # 含 .. 的相对路径直接返回 absolute()，不查 Redis 之外的扫描
            result = WordFileHandler.resolve_path("../etc/passwd")
            # 不应命中系统文件
            assert not Path(result).exists() or result.endswith("passwd")


class TestWordProcessResolveFile:
    """WordProcessTool._resolve_file 行为"""

    def test_resolve_file_id_success(self, tmp_path):
        """file_id 通过 Redis 解析成功"""
        from src.tools.word.word_process_tool import WordProcessTool

        real_file = tmp_path / "file_ok.docx"
        real_file.write_text("x")

        fake_redis_data = {
            "uploaded_file:file_ok": {"path": str(real_file.absolute())}
        }

        tool = WordProcessTool()
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
        from src.tools.word.word_process_tool import WordProcessTool

        tool = WordProcessTool()
        with patch(
            "src.core.redis_client.redis_client.hgetall", return_value={}
        ):
            with pytest.raises(FileNotFoundError, match="文件不存在"):
                tool._resolve_file("file_nonexistent_xyz")


class TestHandlerUsesResolveFile:
    """handler 入口正确调用 _resolve_file"""

    def test_handle_read_file_id_resolved(self, tmp_path):
        """read handler 接收 file_id 输入时正确解析"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        real_file = tmp_path / "file_read.docx"
        real_file.write_text("x")

        fake_redis_data = {
            "uploaded_file:file_read": {"path": str(real_file.absolute())}
        }

        tool = WordProcessTool()
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
                "src.tools.word.word_reader.read_content"
            ) as mock_read:
                mock_read.return_value = {"success": True, "content": "ok"}
                await tool._handle_read(ctx, {})
                called_path = mock_read.call_args[0][0]
                assert Path(called_path).resolve() == real_file.resolve()

        asyncio.run(run())

    def test_handle_read_file_not_found_returns_error(self):
        """read handler 接收不存在的 file_id -> 返回 success=False"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        tool = WordProcessTool()
        ctx = PipelineContext(file_paths=["file_ghost_xyz"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall", return_value={}
            ):
                return await tool._handle_read(ctx, {})

        result = asyncio.run(run())
        assert result["success"] is False
        assert "文件不存在" in result["error"]

    def test_handle_word_to_md_file_id_resolved(self, tmp_path):
        """word_to_md handler 接收 file_id 输入时正确解析"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        real_file = tmp_path / "file_md.docx"
        real_file.write_text("x")

        fake_redis_data = {
            "uploaded_file:file_md": {"path": str(real_file.absolute())}
        }

        tool = WordProcessTool()
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
                "src.tools.word.word_to_md.convert"
            ) as mock_to_md:
                mock_to_md.return_value = "# OK"
                result = await tool._handle_word_to_md(ctx, {})
                called_path = mock_to_md.call_args[0][0]
                assert Path(called_path).resolve() == real_file.resolve()
                return result

        result = asyncio.run(run())
        assert result["success"] is True
        assert result["markdown"] == "# OK"

    def test_handle_word_to_md_file_not_found_returns_error(self):
        """word_to_md handler 接收不存在的 file_id -> 返回 success=False"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        tool = WordProcessTool()
        ctx = PipelineContext(file_paths=["file_ghost_xyz"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall", return_value={}
            ):
                return await tool._handle_word_to_md(ctx, {})

        result = asyncio.run(run())
        assert result["success"] is False
        assert "文件不存在" in result["error"]

    def test_handle_analyze_file_id_resolved(self, tmp_path):
        """analyze handler 接收 file_id 输入时正确解析"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        real_file = tmp_path / "file_an.docx"
        real_file.write_text("x")

        fake_redis_data = {
            "uploaded_file:file_an": {"path": str(real_file.absolute())}
        }

        tool = WordProcessTool()
        ctx = PipelineContext(file_paths=["file_an"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall",
                side_effect=lambda k: fake_redis_data.get(k, {}),
            ), patch(
                "src.core.redis_client.redis_client.make_key",
                side_effect=lambda prefix, fid: f"{prefix}:{fid}",
            ), patch(
                "src.tools.word.word_reader.analyze_structure"
            ) as mock_an:
                mock_an.return_value = {"success": True, "paragraphs": []}
                await tool._handle_analyze(ctx, {})
                called_path = mock_an.call_args[0][0]
                assert Path(called_path).resolve() == real_file.resolve()

        asyncio.run(run())

    def test_handle_modify_file_not_found_returns_error(self):
        """modify handler 接收不存在的 file_id -> 返回 success=False（不进入 copy_and_open）"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        tool = WordProcessTool()
        ctx = PipelineContext(file_paths=["file_ghost_xyz"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall", return_value={}
            ), patch(
                "src.tools.word.word_lib.WordFileHandler.copy_and_open"
            ) as mock_open:
                return await tool._handle_modify(
                    ctx, {"operations": [{"type": "replace", "from": "a", "to": "b"}]}
                )

        result = asyncio.run(run())
        assert result["success"] is False
        assert "文件不存在" in result["error"]

    def test_handle_format_file_not_found_returns_error(self):
        """format handler 接收不存在的 file_id -> 返回 success=False"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        tool = WordProcessTool()
        ctx = PipelineContext(file_paths=["file_ghost_xyz"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall", return_value={}
            ):
                return await tool._handle_format(
                    ctx, {"format_operations": [{"type": "bold"}]}
                )

        result = asyncio.run(run())
        assert result["success"] is False
        assert "文件不存在" in result["error"]

    def test_handle_fill_template_file_not_found_returns_error(self):
        """fill_template handler 接收不存在的 file_id -> 返回 success=False"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        tool = WordProcessTool()
        ctx = PipelineContext(file_paths=["file_ghost_xyz"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall", return_value={}
            ):
                return await tool._handle_fill_template(
                    ctx, {"variables": {"x": "y"}}
                )

        result = asyncio.run(run())
        assert result["success"] is False
        assert "文件不存在" in result["error"]


class TestHandleMdToWordResolveFile:
    """_handle_md_to_word 的 file_paths[0] 解析行为（特殊：失败不阻塞流程）"""

    def test_md_to_word_reads_resolved_md_file(self, tmp_path):
        """file_paths[0] 是 .md 文件的 file_id -> 通过 Redis 解析后读取内容"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        # 造一个 .md 文件
        real_md = tmp_path / "file_md_ok.md"
        real_md.write_text("# 标题\n\n正文", encoding="utf-8")

        fake_redis_data = {
            "uploaded_file:file_md_ok": {"path": str(real_md.absolute())}
        }

        tool = WordProcessTool()
        ctx = PipelineContext(file_paths=["file_md_ok"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall",
                side_effect=lambda k: fake_redis_data.get(k, {}),
            ), patch(
                "src.core.redis_client.redis_client.make_key",
                side_effect=lambda prefix, fid: f"{prefix}:{fid}",
            ), patch(
                "src.tools.word.md_to_word.convert_async"
            ) as mock_conv, patch(
                "src.tools.word.md_to_word.save_as"
            ) as mock_save:
                mock_conv.return_value = "fake_doc"
                mock_save.return_value = {"file_path": "/tmp/out.docx", "file_size": 10}
                result = await tool._handle_md_to_word(ctx, {"template": "default"})

                # 验证 convert_async 收到从 .md 文件读取的内容
                called_md_text = mock_conv.call_args[0][0]
                assert "# 标题" in called_md_text
                return result

        result = asyncio.run(run())
        assert result["success"] is True

    def test_md_to_word_file_not_found_falls_back_to_error(self):
        """file_paths[0] 是不存在的 file_id 且无 ctx.content -> 返回需要 md_text 的错误"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        tool = WordProcessTool()
        ctx = PipelineContext(file_paths=["file_ghost_xyz"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall", return_value={}
            ):
                return await tool._handle_md_to_word(ctx, {"template": "default"})

        result = asyncio.run(run())
        assert result["success"] is False
        assert "md_to_word" in result["error"]

    def test_md_to_word_uses_ctx_content_first(self, tmp_path):
        """ctx.content 有内容时优先用 content，不解析 file_paths"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        tool = WordProcessTool()
        ctx = PipelineContext(
            file_paths=["file_ghost_wont_resolve"],
            content="# 来自 content 的标题",
        )

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall", return_value={}
            ) as mock_hgetall, patch(
                "src.tools.word.md_to_word.convert_async"
            ) as mock_conv, patch(
                "src.tools.word.md_to_word.save_as"
            ) as mock_save:
                mock_conv.return_value = "fake_doc"
                mock_save.return_value = {"file_path": "/tmp/out.docx", "file_size": 10}
                result = await tool._handle_md_to_word(ctx, {"template": "default"})

                # file_paths 不应被解析（content 优先）
                mock_hgetall.assert_not_called()
                called_md_text = mock_conv.call_args[0][0]
                assert "来自 content" in called_md_text
                return result

        result = asyncio.run(run())
        assert result["success"] is True


class TestHandleDiffResolveFile:
    """_handle_diff 同时解析两个 file_path"""

    def test_diff_both_file_ids_resolved(self, tmp_path):
        """两个 file_id 都通过 Redis 解析"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        real_old = tmp_path / "file_old.docx"
        real_new = tmp_path / "file_new.docx"
        real_old.write_text("old")
        real_new.write_text("new")

        fake_redis_data = {
            "uploaded_file:file_old": {"path": str(real_old.absolute())},
            "uploaded_file:file_new": {"path": str(real_new.absolute())},
        }

        tool = WordProcessTool()
        ctx = PipelineContext(file_paths=["file_old", "file_new"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall",
                side_effect=lambda k: fake_redis_data.get(k, {}),
            ), patch(
                "src.core.redis_client.redis_client.make_key",
                side_effect=lambda prefix, fid: f"{prefix}:{fid}",
            ), patch(
                "src.tools.word.word_differ.diff"
            ) as mock_diff:
                mock_diff.return_value = {"success": True, "diff_text": "..."}
                await tool._handle_diff(ctx, {})

                # 验证 diff 收到两个解析后的路径
                args = mock_diff.call_args[0]
                assert Path(args[0]).resolve() == real_old.resolve()
                assert Path(args[1]).resolve() == real_new.resolve()

        asyncio.run(run())

    def test_diff_first_file_not_found_returns_error(self):
        """第一个 file_id 找不到 -> 返回 success=False"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        tool = WordProcessTool()
        ctx = PipelineContext(file_paths=["file_ghost_xyz", "file_another"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall", return_value={}
            ):
                return await tool._handle_diff(ctx, {})

        result = asyncio.run(run())
        assert result["success"] is False
        assert "文件不存在" in result["error"]

    def test_diff_missing_second_path_returns_error(self):
        """只传一个 file_path -> 返回需要两个路径的错误（不进入 _resolve_file）"""
        from src.tools.word.word_process_tool import WordProcessTool, PipelineContext

        tool = WordProcessTool()
        ctx = PipelineContext(file_paths=["file_only_one"])

        import asyncio

        async def run():
            with patch(
                "src.core.redis_client.redis_client.hgetall", return_value={}
            ):
                return await tool._handle_diff(ctx, {})

        result = asyncio.run(run())
        assert result["success"] is False
        assert "diff" in result["error"]
