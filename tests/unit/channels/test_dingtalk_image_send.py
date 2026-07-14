"""钉钉适配器图片发送单元测试 (Phase 2 P2.9.2)

验证 send_message 在文本之后、downloadable_files 之前按顺序发送图片:
1. test_send_message_with_images_calls_send_image_per_ref — 2 张图各调一次 send_image
2. test_send_message_text_uses_placeholder_renderer — 调用 render_text_with_image_placeholders
3. test_send_message_image_failure_does_not_block_others — 单图失败不阻断
4. test_send_message_no_images_skips_image_loop — 无图时不调 send_image
5. test_send_message_uses_build_public_url — download_url 被转换为公网 URL

钉钉与 feishu 不同：直接用 build_public_url 把 download_url 转公网 URL，
不下载到本地、不走 upload_from_url。send_image 接收 photo_url（公网 URL）。
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.channels.dingtalk.adapter import DingTalkAdapter
from src.models.message import UnifiedResponse


# ---------- fixtures ----------


@pytest.fixture
def adapter():
    """创建 DingTalkAdapter 实例"""
    return DingTalkAdapter(
        app_key="test_app_key",
        app_secret="test_app_secret_xxxxxxxxxxxxxxxx",
        robot_code="test_robot",
        rate_limit_window=60,
        rate_limit_max=5,
    )


def _make_message(text: str = "", images=None, downloadable_files=None) -> UnifiedResponse:
    """构造含 images 的 UnifiedResponse

    conversation_type="1" 单聊；reply_to 为 userId。
    """
    content: dict = {"conversation_type": "1"}
    if text:
        content["text"] = text
    if images is not None:
        content["images"] = images
    return UnifiedResponse(
        message_id="msg_test",
        reply_to="user_001",
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
    """mock adapter 的 send_long_message / send_image

    返回 adapter，便于断言。
    """
    adapter.send_long_message = AsyncMock(return_value=True)
    adapter.send_image = AsyncMock(return_value=True)
    return adapter


# ---------- 测试用例 ----------


class TestDingTalkSendImages:
    @pytest.mark.asyncio
    async def test_send_message_with_images_calls_send_image_per_ref(self, adapter):
        """两张图：send_image 被调 2 次，send_long_message 仍只 1 次"""
        _install_async_methods(adapter)

        images = [
            _make_image_ref("file_img1", "图1"),
            _make_image_ref("file_img2", "图2"),
        ]
        msg = _make_message(text="hello", images=images)

        with patch("src.channels.dingtalk.adapter.build_public_url") as mock_build:
            mock_build.side_effect = lambda url: f"https://public.example.com{url}"

            result = await adapter.send_message(msg)

        assert result is True
        # 文本仅发送 1 次
        assert adapter.send_long_message.call_count == 1
        # 两张图都发送
        assert adapter.send_image.call_count == 2
        # build_public_url 每张图都被调一次
        assert mock_build.call_count == 2

    @pytest.mark.asyncio
    async def test_send_message_text_uses_placeholder_renderer(self, adapter):
        """验证 render_text_with_image_placeholders 被调用（Spy）"""
        _install_async_methods(adapter)

        images = [_make_image_ref("file_inline1", "内嵌图", placement="before_text")]
        msg = _make_message(text="正文", images=images)

        with patch(
            "src.channels.dingtalk.adapter.render_text_with_image_placeholders",
            wraps=__import__(
                "src.channels._image_text_renderer", fromlist=["render_text_with_image_placeholders"]
            ).render_text_with_image_placeholders,
        ) as spy_render:
            with patch("src.channels.dingtalk.adapter.build_public_url") as mock_build:
                mock_build.side_effect = lambda url: f"https://public.example.com{url}"

                await adapter.send_message(msg)

            # spy 必须被调用一次
            assert spy_render.call_count == 1
            # 参数：原文本 + images 列表
            args, _ = spy_render.call_args
            assert args[0] == "正文"
            assert isinstance(args[1], list) and len(args[1]) == 1

    @pytest.mark.asyncio
    async def test_send_message_image_failure_does_not_block_others(self, adapter):
        """第 1 张图 send_image 抛异常，验证第 2 张仍发送、return False"""
        _install_async_methods(adapter)

        # 第 1 张抛异常，第 2 张正常返回 True
        adapter.send_image = AsyncMock(side_effect=[Exception("network error"), True])

        images = [
            _make_image_ref("file_fail", "失败图"),
            _make_image_ref("file_ok", "成功图"),
        ]
        msg = _make_message(text="hi", images=images)

        with patch("src.channels.dingtalk.adapter.build_public_url") as mock_build:
            mock_build.side_effect = lambda url: f"https://public.example.com{url}"

            result = await adapter.send_message(msg)

        # 整体失败（有图片失败）
        assert result is False
        # 第 2 张仍然发送（异常被捕获不阻断）
        assert adapter.send_image.call_count == 2

    @pytest.mark.asyncio
    async def test_send_message_no_images_skips_image_loop(self, adapter):
        """无图时不调 send_image"""
        _install_async_methods(adapter)

        msg = _make_message(text="only text", images=[])

        with patch("src.channels.dingtalk.adapter.build_public_url") as mock_build:
            result = await adapter.send_message(msg)

        assert result is True
        assert adapter.send_image.call_count == 0
        # build_public_url 也不应被调用
        mock_build.assert_not_called()
        # 文本仍发送
        assert adapter.send_long_message.call_count == 1

    @pytest.mark.asyncio
    async def test_send_message_uses_build_public_url(self, adapter):
        """验证 download_url 被转换为公网 URL，且 send_image 收到的是转换后的 URL"""
        _install_async_methods(adapter)

        images = [_make_image_ref("file_xxx", "图")]
        msg = _make_message(text="txt", images=images)

        captured_urls = []

        def fake_build(url):
            public = f"https://public.example.com{url}"
            captured_urls.append((url, public))
            return public

        with patch("src.channels.dingtalk.adapter.build_public_url", side_effect=fake_build):
            result = await adapter.send_message(msg)

        assert result is True
        # build_public_url 被调一次，入参为相对路径
        assert len(captured_urls) == 1
        rel_url, public_url = captured_urls[0]
        assert rel_url == "/api/files/file_xxx/download"
        assert public_url == "https://public.example.com/api/files/file_xxx/download"
        # send_image 收到的是公网 URL
        sent_url = adapter.send_image.call_args.args[0]
        assert sent_url == public_url
