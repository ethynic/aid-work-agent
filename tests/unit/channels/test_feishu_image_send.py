"""飞书适配器图片发送单元测试 (Phase 2 P2.9.1)

验证 send_message 在文本之后、downloadable_files 之前按顺序发送图片:
1. test_send_message_with_images_appends_after_text — 2 张图依次发送，文本仍发送 1 次
2. test_send_message_text_calls_placeholder_renderer — 调用 render_text_with_image_placeholders
3. test_send_message_image_failure_does_not_block_others — 单图失败不阻断
4. test_send_message_no_images_skips_image_loop — 无图时不调 upload_image
5. test_send_message_image_resolve_failure_skipped — get_ref_by_file_id 返回 None 时跳过
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.feishu.adapter import FeishuAdapter
from src.models.message import UnifiedResponse


# ---------- fixtures ----------


@pytest.fixture
def adapter():
    """创建 FeishuAdapter 实例（无加密模式）"""
    return FeishuAdapter(
        app_id="cli_test",
        app_secret="secret_test",
        verification_token="vt_test",
        encrypt_key="",  # 关闭加密，简化测试
        welcome_message="欢迎",
        max_bytes=4000,
        rate_limit_window=60,
        rate_limit_max=10,
    )


def _make_message(text: str = "", images=None, downloadable_files=None) -> UnifiedResponse:
    """构造含 images 的 UnifiedResponse"""
    content = {"text": text}
    if images is not None:
        content["images"] = images
    return UnifiedResponse(
        message_id="msg_test",
        reply_to="ou_user_001",
        content=content,
        downloadable_files=downloadable_files or [],
    )


def _make_image_ref(file_id: str, display_name: str = "图", placement: str = "after_text") -> dict:
    """构造单张 ImageRef dict"""
    return {
        "file_id": file_id,
        "download_url": f"/api/files/{file_id}/download",
        "display_name": display_name,
        "placement": placement,
        "source": "tool_generated",
        "usage": "inline",
        "mime_type": "image/png",
        "size_bytes": 1024,
    }


def _install_async_methods(adapter):
    """mock adapter 的 send_long_message / media.upload_image / send_image / send_file / media.upload_file

    返回各 mock，便于断言。
    """
    adapter.send_long_message = AsyncMock(return_value=True)
    adapter.send_image = AsyncMock(return_value=True)
    adapter.send_file = AsyncMock(return_value=True)
    adapter.media = MagicMock()
    adapter.media.upload_image = AsyncMock(return_value="img_key_dummy")
    adapter.media.upload_file = AsyncMock(return_value="file_key_dummy")
    return adapter


# ---------- 测试用例 ----------


class TestFeishuSendImages:
    @pytest.mark.asyncio
    async def test_send_message_with_images_appends_after_text(self, adapter):
        """两张图：upload_image / send_image 各被调 2 次，send_long_message 仍只 1 次"""
        _install_async_methods(adapter)

        images = [
            _make_image_ref("file_img1", "图1"),
            _make_image_ref("file_img2", "图2"),
        ]
        msg = _make_message(text="hello", images=images)

        fake_path = Path("/tmp/fake_img.png")

        # patch 模块内 lazy import（adapter._resolve_image_local_path 内 from src.core.image_asset import get_image_registry）
        with patch("src.core.image_asset.get_image_registry") as mock_get_reg:
            registry = MagicMock()
            registry.get_ref_by_file_id = AsyncMock(return_value=MagicMock())
            registry.resolve_local_path = AsyncMock(return_value=fake_path)
            mock_get_reg.return_value = registry

            result = await adapter.send_message(msg)

        assert result is True
        # 文本仅发送 1 次
        assert adapter.send_long_message.call_count == 1
        # 两张图都上传并发送
        assert adapter.media.upload_image.call_count == 2
        assert adapter.send_image.call_count == 2
        # upload_image 入参是 local_path 字符串
        first_call_arg = adapter.media.upload_image.call_args_list[0].args[0]
        assert str(fake_path) == first_call_arg

    @pytest.mark.asyncio
    async def test_send_message_text_calls_placeholder_renderer(self, adapter):
        """验证 render_text_with_image_placeholders 被调用（Spy）"""
        _install_async_methods(adapter)

        images = [_make_image_ref("file_inline1", "内嵌图", placement="before_text")]
        msg = _make_message(text="正文", images=images)

        with patch(
            "src.channels.feishu.adapter.render_text_with_image_placeholders",
            wraps=__import__(
                "src.channels._image_text_renderer", fromlist=["render_text_with_image_placeholders"]
            ).render_text_with_image_placeholders,
        ) as spy_render:
            with patch("src.core.image_asset.get_image_registry") as mock_get_reg:
                registry = MagicMock()
                registry.get_ref_by_file_id = AsyncMock(return_value=MagicMock())
                registry.resolve_local_path = AsyncMock(return_value=Path("/tmp/x.png"))
                mock_get_reg.return_value = registry

                await adapter.send_message(msg)

            # spy 必须被调用一次
            assert spy_render.call_count == 1
            # 参数：原文本 + images 列表
            args, _ = spy_render.call_args
            assert args[0] == "正文"
            assert isinstance(args[1], list) and len(args[1]) == 1

    @pytest.mark.asyncio
    async def test_send_message_image_failure_does_not_block_others(self, adapter):
        """第 1 张图 upload_image 抛异常，验证第 2 张仍发送、return False"""
        _install_async_methods(adapter)

        # 第 1 张抛异常，第 2 张正常返回
        adapter.media.upload_image = AsyncMock(side_effect=[Exception("network error"), "img_key_2"])

        images = [
            _make_image_ref("file_fail", "失败图"),
            _make_image_ref("file_ok", "成功图"),
        ]
        msg = _make_message(text="hi", images=images)

        with patch("src.core.image_asset.get_image_registry") as mock_get_reg:
            registry = MagicMock()
            registry.get_ref_by_file_id = AsyncMock(return_value=MagicMock())
            registry.resolve_local_path = AsyncMock(return_value=Path("/tmp/x.png"))
            mock_get_reg.return_value = registry

            result = await adapter.send_message(msg)

        # 整体失败（有图片失败）
        assert result is False
        # 第 2 张仍然上传并发送
        assert adapter.media.upload_image.call_count == 2
        assert adapter.send_image.call_count == 1

    @pytest.mark.asyncio
    async def test_send_message_no_images_skips_image_loop(self, adapter):
        """无图时不调 upload_image"""
        _install_async_methods(adapter)

        msg = _make_message(text="only text", images=[])

        with patch("src.core.image_asset.get_image_registry") as mock_get_reg:
            registry = MagicMock()
            registry.get_ref_by_file_id = AsyncMock(return_value=None)
            registry.resolve_local_path = AsyncMock(return_value=Path("/tmp/x.png"))
            mock_get_reg.return_value = registry

            result = await adapter.send_message(msg)

        assert result is True
        assert adapter.media.upload_image.call_count == 0
        assert adapter.send_image.call_count == 0
        # registry 没被调
        registry.get_ref_by_file_id.assert_not_called()
        # 文本仍发送
        assert adapter.send_long_message.call_count == 1

    @pytest.mark.asyncio
    async def test_send_message_image_resolve_failure_skipped(self, adapter):
        """get_ref_by_file_id 返回 None，验证跳过该图不阻断"""
        _install_async_methods(adapter)

        images = [
            _make_image_ref("file_missing", "缺失图"),
            _make_image_ref("file_ok", "好图"),
        ]
        msg = _make_message(text="txt", images=images)

        async def fake_get_ref(file_id):
            if file_id == "file_missing":
                return None
            return MagicMock()

        with patch("src.core.image_asset.get_image_registry") as mock_get_reg:
            registry = MagicMock()
            registry.get_ref_by_file_id = AsyncMock(side_effect=fake_get_ref)
            registry.resolve_local_path = AsyncMock(return_value=Path("/tmp/ok.png"))
            mock_get_reg.return_value = registry

            result = await adapter.send_message(msg)

        # 缺图导致整体失败
        assert result is False
        # 只有第 2 张被上传（第 1 张 None 早 return）
        assert adapter.media.upload_image.call_count == 1
        assert adapter.send_image.call_count == 1
