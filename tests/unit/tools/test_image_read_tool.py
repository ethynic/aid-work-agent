"""image_read 工具单测（全 mock 外部依赖）

覆盖：
- model 白名单校验（默认免检 / 白名单内 / 白名单外 / 格式非法）
- answer + file_path 返回契约（file_path 供 skill_execute 跑 sample_color.py）
- chat_direct 的 VL messages 组装（image_url data URL）与温度 0.1
- VL usage 手动记账（chat_direct 不自动记账）
- 本地路径解析 / file_id 经 registry / 相对路径拒绝 / 文件不存在
"""
import struct
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.image.image_read_tool import ImageReadTool


def _make_png(width=4, height=4, color=(0, 128, 255)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


@pytest.fixture
def png_file(tmp_path):
    p = tmp_path / "floorplan.png"
    p.write_bytes(_make_png())
    return str(p)


@pytest.fixture
def vl_settings():
    settings = MagicMock()
    settings.image_gen.vl.model = "qwen/qwen3-vl-plus"
    settings.image_gen.vl.allowed_models = ["qwen/qwen3-vl-plus"]
    return settings


class TestResolveModel:
    def test_default_model_no_whitelist_check(self, vl_settings):
        tool = ImageReadTool()
        with patch("src.config.settings.create_settings", return_value=vl_settings):
            provider, model = tool._resolve_model(None)
        assert (provider, model) == ("qwen", "qwen3-vl-plus")

    def test_model_equal_default_passes(self, vl_settings):
        tool = ImageReadTool()
        with patch("src.config.settings.create_settings", return_value=vl_settings):
            provider, model = tool._resolve_model("qwen/qwen3-vl-plus")
        assert (provider, model) == ("qwen", "qwen3-vl-plus")

    def test_model_not_in_whitelist_rejected(self, vl_settings):
        """等于默认模型免白名单校验；其他显式指定须命中白名单"""
        tool = ImageReadTool()
        with patch("src.config.settings.create_settings", return_value=vl_settings):
            with pytest.raises(ValueError, match="白名单"):
                tool._resolve_model("qwen/qwen-vl-max")

    def test_model_outside_whitelist_rejected(self, vl_settings):
        tool = ImageReadTool()
        with patch("src.config.settings.create_settings", return_value=vl_settings):
            with pytest.raises(ValueError, match="白名单"):
                tool._resolve_model("gpt/gpt-4o")

    def test_bad_format_rejected(self, vl_settings):
        tool = ImageReadTool()
        with patch("src.config.settings.create_settings", return_value=vl_settings):
            for bad in ("qwen-vl-plus", "/model-only", "provider/"):
                with pytest.raises(ValueError, match="格式非法"):
                    tool._resolve_model(bad)


class TestExecuteSuccess:
    @pytest.mark.asyncio
    async def test_local_path_answer_and_file_path(self, png_file, vl_settings):
        """本地路径判读：返回 answer + model + file_path（file_path 是取色脚本桥接）"""
        captured = {}

        async def fake_chat_direct(provider, model, messages=None, **kw):
            captured["provider"] = provider
            captured["model"] = model
            captured["messages"] = messages
            captured["temperature"] = kw.get("temperature")
            return {
                "content": "深色墙围合的房间数：3 个",
                "usage": {"prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550},
            }

        record = MagicMock()
        tool = ImageReadTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1", user_id="u1")), \
             patch("src.config.settings.create_settings", return_value=vl_settings), \
             patch("src.llm.gateway.llm_gateway") as mock_gateway, \
             patch("src.services.session_record.SessionRecordManager") as mock_mgr:
            mock_gateway.chat_direct = AsyncMock(side_effect=fake_chat_direct)
            mock_mgr.get_current_record.return_value = record
            result = await tool.execute(image=png_file, question="数清房间数")

        assert result["success"] is True
        assert result["answer"] == "深色墙围合的房间数：3 个"
        assert result["model"] == "qwen/qwen3-vl-plus"
        assert result["file_path"] == png_file
        msg = captured["messages"][0]
        parts = msg["content"]
        assert parts[0]["type"] == "text" and parts[0]["text"] == "数清房间数"
        assert parts[1]["type"] == "image_url"
        assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert captured["temperature"] == 0.1
        # VL token 手动记账（chat_direct 不自动记账）
        record.add_llm_usage.assert_called_once()
        usage_arg = record.add_llm_usage.call_args[0][0]
        assert usage_arg["total_tokens"] == 550

    @pytest.mark.asyncio
    async def test_file_id_via_registry(self, png_file, vl_settings):
        """file_id 输入：经 ImageRegistry 解析为本地路径"""
        ref = MagicMock()
        registry = MagicMock()
        registry.get_ref_by_file_id = AsyncMock(return_value=ref)
        registry.resolve_local_path = AsyncMock(return_value=Path(png_file))

        async def fake_chat_direct(provider, model, messages=None, **kw):
            return {"content": "结论", "usage": None}

        tool = ImageReadTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")), \
             patch("src.core.image_asset.get_image_registry", return_value=registry), \
             patch("src.config.settings.create_settings", return_value=vl_settings), \
             patch("src.llm.gateway.llm_gateway") as mock_gateway, \
             patch("src.services.session_record.SessionRecordManager") as mock_mgr:
            mock_gateway.chat_direct = AsyncMock(side_effect=fake_chat_direct)
            mock_mgr.get_current_record.return_value = None
            result = await tool.execute(image="file_abc123", question="这是什么")

        assert result["success"] is True
        assert result["file_path"] == png_file


class TestValidation:
    @pytest.mark.asyncio
    async def test_missing_tenant_rejected(self):
        tool = ImageReadTool()
        with tool_execution_scope(ToolExecutionContext()):
            result = await tool.execute(image="/tmp/a.png", question="q")
        assert result["success"] is False
        assert "租户" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_image_and_question_rejected(self):
        tool = ImageReadTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
            r1 = await tool.execute(image="", question="q")
            r2 = await tool.execute(image="/tmp/a.png", question=" ")
        assert r1["success"] is False and r2["success"] is False
        assert "image" in r1["error"] and "question" in r2["error"]

    @pytest.mark.asyncio
    async def test_relative_path_rejected(self):
        tool = ImageReadTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
            result = await tool.execute(image="rel/path.png", question="q")
        assert result["success"] is False
        assert "绝对路径" in result["error"]

    @pytest.mark.asyncio
    async def test_path_outside_sandbox_rejected(self):
        """P1-1：/etc/passwd 等沙箱外路径拒绝，防任意文件读取外发"""
        tool = ImageReadTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
            result = await tool.execute(image="/etc/passwd", question="q")
        assert result["success"] is False
        assert "超出允许范围" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_file_rejected(self):
        # 沙箱内（临时目录）不存在文件 → FileNotFoundError → 解析失败
        import tempfile
        missing = Path(tempfile.gettempdir()) / "img_read_missing_test" / "nope.png"
        tool = ImageReadTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
            result = await tool.execute(image=str(missing), question="q")
        assert result["success"] is False
        assert "解析失败" in result["error"]

    @pytest.mark.asyncio
    async def test_file_id_not_found_rejected(self):
        registry = MagicMock()
        registry.get_ref_by_file_id = AsyncMock(return_value=None)
        tool = ImageReadTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")), \
             patch("src.core.image_asset.get_image_registry", return_value=registry):
            result = await tool.execute(image="file_nope", question="q")
        assert result["success"] is False
        assert "file_nope" in result["error"]


class TestExecuteFailure:
    @pytest.mark.asyncio
    async def test_vl_error_generic_message_with_debug(self, png_file, vl_settings):
        tool = ImageReadTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")), \
             patch("src.config.settings.create_settings", return_value=vl_settings), \
             patch("src.llm.gateway.llm_gateway") as mock_gateway:
            mock_gateway.chat_direct = AsyncMock(side_effect=RuntimeError("api_key=sk-x leak"))
            result = await tool.execute(image=png_file, question="q")

        assert result["success"] is False
        assert result["error"] == "图片判读失败，请稍后重试"
        assert "sk-x" not in result.get("debug", "")

    @pytest.mark.asyncio
    async def test_empty_answer_raises_generic_error(self, png_file, vl_settings):
        tool = ImageReadTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")), \
             patch("src.config.settings.create_settings", return_value=vl_settings), \
             patch("src.llm.gateway.llm_gateway") as mock_gateway:
            mock_gateway.chat_direct = AsyncMock(return_value={"content": ""})
            result = await tool.execute(image=png_file, question="q")

        assert result["success"] is False
        assert result["error"] == "视觉模型未返回判读结果"
