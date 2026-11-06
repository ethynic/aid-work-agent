"""image_render 工具单测（全 mock 外部依赖）

覆盖：
- images 契约（顶层 images 键，元素含 file_id/download_url，供 SSE 图片事件）
- 参考图 1-3 校验、n 范围校验、prompt 非空校验、租户上下文缺失
- 参考图解析：本地绝对路径直读 / file_id 经 registry / 相对路径拒绝
- 按成功张数计费：有 record 累加 add_image_usage；无 record 独立 ChatRecordDB.create 兜底
- 失败脱敏（sanitize_error）
"""
import struct
import zlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.image.image_render_tool import ImageRenderTool


def _make_png(width=4, height=4, color=(255, 0, 0)) -> bytes:
    """构造最小合法 PNG（供真实文件路径解析与 base64 读取）"""
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
    p = tmp_path / "product.png"
    p.write_bytes(_make_png())
    return str(p)


@pytest.fixture
def mock_ref():
    ref = MagicMock()
    ref.model_dump.return_value = {
        "file_id": "file_gen001",
        "download_url": "/api/files/file_gen001/download",
        "display_name": "效果图.png",
        "mime_type": "image/png",
        "source": "tool_generated",
        "usage": "inline",
    }
    return ref


class TestValidation:
    @pytest.mark.asyncio
    async def test_missing_tenant_rejected(self):
        tool = ImageRenderTool()
        with tool_execution_scope(ToolExecutionContext()):
            result = await tool.execute(prompt="效果图")
        assert result["success"] is False
        assert "租户" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_prompt_rejected(self):
        tool = ImageRenderTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
            result = await tool.execute(prompt="  ")
        assert result["success"] is False
        assert "prompt" in result["error"]

    @pytest.mark.asyncio
    async def test_too_many_refs_rejected(self):
        tool = ImageRenderTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
            result = await tool.execute(prompt="p", reference_images=["a", "b", "c", "d"])
        assert result["success"] is False
        assert "3 张" in result["error"]

    @pytest.mark.asyncio
    async def test_n_out_of_range_rejected(self):
        tool = ImageRenderTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
            # n=0/None 按缺省语义回退 1（int(x or 1)），越界用 5 与 -1
            for n in (5, -1):
                result = await tool.execute(prompt="p", n=n)
                assert result["success"] is False
                assert "1-4" in result["error"]


class TestReferenceResolution:
    @pytest.mark.asyncio
    async def test_relative_path_rejected(self, png_file):
        tool = ImageRenderTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
            result = await tool.execute(prompt="p", reference_images=["relative/img.png"])
        assert result["success"] is False
        assert "绝对路径" in result["error"]

    @pytest.mark.asyncio
    async def test_path_outside_sandbox_rejected(self):
        """P1-1：沙箱外参考图路径拒绝，防任意文件读取外发"""
        tool = ImageRenderTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")):
            result = await tool.execute(prompt="p", reference_images=["/etc/passwd"])
        assert result["success"] is False
        assert "超出允许范围" in result["error"]

    @pytest.mark.asyncio
    async def test_file_id_not_found_rejected(self, png_file):
        tool = ImageRenderTool()
        registry = MagicMock()
        registry.get_ref_by_file_id = AsyncMock(return_value=None)
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")), \
             patch("src.core.image_asset.get_image_registry", return_value=registry):
            result = await tool.execute(prompt="p", reference_images=["file_missing"])
        assert result["success"] is False
        assert "file_missing" in result["error"]

    @pytest.mark.asyncio
    async def test_local_path_resolved_to_data_url(self, png_file, mock_ref):
        """本地绝对路径直接读文件转 data URL，不经 registry"""
        captured = {}

        async def fake_render(req, **kw):
            captured["refs"] = req.reference_images_data_urls
            return [mock_ref], {"task_id": "task-1", "model": "qwen-image-edit",
                                "requested_n": 1, "succeeded_n": 1}

        tool = ImageRenderTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1", user_id="u1")), \
             patch("src.services.image_gen.get_image_gen_service") as mock_get_svc:
            svc = MagicMock()
            svc.render = AsyncMock(side_effect=fake_render)
            mock_get_svc.return_value = svc
            result = await tool.execute(prompt="p", reference_images=[png_file])

        assert result["success"] is True
        assert captured["refs"][0].startswith("data:image/png;base64,")


class TestExecuteSuccess:
    @pytest.mark.asyncio
    async def test_images_contract_and_billing_with_record(self, mock_ref):
        """成功：顶层 images 键含 file_id/download_url；有 record 时按张累加计费"""
        record = MagicMock()
        tool = ImageRenderTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1", user_id="u1")), \
             patch("src.services.image_gen.get_image_gen_service") as mock_get_svc, \
             patch("src.services.session_record.SessionRecordManager") as mock_mgr:
            svc = MagicMock()
            svc.render = AsyncMock(return_value=(
                [mock_ref, mock_ref],
                {"task_id": "task-2", "model": "qwen-image-edit", "requested_n": 2, "succeeded_n": 2},
            ))
            mock_get_svc.return_value = svc
            mock_mgr.get_current_record.return_value = record
            result = await tool.execute(prompt="效果图", n=2)

        assert result["success"] is True
        assert result["images_count"] == 2
        assert result["requested_n"] == 2
        assert result["model"] == "qwen-image-edit"
        assert result["task_id"] == "task-2"
        img = result["images"][0]
        assert img["file_id"] == "file_gen001"
        assert img["download_url"] == "/api/files/file_gen001/download"
        record.add_image_usage.assert_called_once_with(images=2, model="qwen-image-edit")

    @pytest.mark.asyncio
    async def test_partial_success_billed_by_succeeded_count(self, mock_ref):
        """部分失败：按成功张数计费"""
        record = MagicMock()
        tool = ImageRenderTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")), \
             patch("src.services.image_gen.get_image_gen_service") as mock_get_svc, \
             patch("src.services.session_record.SessionRecordManager") as mock_mgr:
            svc = MagicMock()
            svc.render = AsyncMock(return_value=(
                [mock_ref],
                {"task_id": "t", "model": "qwen-image-edit", "requested_n": 4, "succeeded_n": 1},
            ))
            mock_get_svc.return_value = svc
            mock_mgr.get_current_record.return_value = record
            result = await tool.execute(prompt="p", n=4)

        assert result["images_count"] == 1
        record.add_image_usage.assert_called_once_with(images=1, model="qwen-image-edit")

    @pytest.mark.asyncio
    async def test_billing_fallback_without_record(self, mock_ref):
        """无会话记录（后台调用）→ 独立 ChatRecordDB.create(source_type=tool_billing) 落账"""
        saved = {}

        def _fake_create(**kwargs):
            saved.update(kwargs)
            return {"record_id": 1}

        tool = ImageRenderTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")), \
             patch("src.services.image_gen.get_image_gen_service") as mock_get_svc, \
             patch("src.services.session_record.SessionRecordManager") as mock_mgr, \
             patch("src.services.billing.TokenCostPriceDB") as mock_tcp, \
             patch("src.services.billing.create_settings") as mock_cs, \
             patch("src.db.models.ChatRecordDB.create", side_effect=_fake_create):
            svc = MagicMock()
            svc.render = AsyncMock(return_value=(
                [mock_ref], {"task_id": "t", "model": "qwen-image-edit",
                             "requested_n": 1, "succeeded_n": 1},
            ))
            mock_get_svc.return_value = svc
            mock_mgr.get_current_record.return_value = None
            mock_tcp.get_by_model_name.return_value = {"model_name": "qwen-image-edit",
                                                       "price_per_call": 0.25}
            settings = MagicMock()
            settings.billing.image_usage_factor = 100
            mock_cs.return_value = settings
            result = await tool.execute(prompt="p")

        assert result["success"] is True
        assert saved["source_type"] == "tool_billing"
        assert saved["credit_cost"] == 25.0
        assert saved["usage_breakdown"]["image"]["images"] == 1


class TestExecuteFailure:
    @pytest.mark.asyncio
    async def test_provider_error_sanitized(self):
        from src.services.image_gen.base import ImageGenProviderError

        tool = ImageRenderTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")), \
             patch("src.services.image_gen.get_image_gen_service") as mock_get_svc:
            svc = MagicMock()
            svc.render = AsyncMock(side_effect=ImageGenProviderError("api_key=sk-secret-xxx 失效"))
            mock_get_svc.return_value = svc
            result = await tool.execute(prompt="p")

        assert result["success"] is False
        assert "sk-secret" not in result["error"]

    @pytest.mark.asyncio
    async def test_unexpected_error_generic_message_with_debug(self):
        tool = ImageRenderTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="t1")), \
             patch("src.services.image_gen.get_image_gen_service") as mock_get_svc:
            svc = MagicMock()
            svc.render = AsyncMock(side_effect=RuntimeError("api_key=sk-leaked boom"))
            mock_get_svc.return_value = svc
            result = await tool.execute(prompt="p")

        assert result["success"] is False
        assert result["error"] == "图片生成失败，请稍后重试"
        assert "sk-leaked" not in result.get("debug", "")
