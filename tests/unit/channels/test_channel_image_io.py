"""多渠道图片收发单元测试（2026-11-09，对齐 wecom_kf 模式）

覆盖：
1. _build_image_attachment_from_bytes — 落盘模式 / local_path 复用模式 / tenant_id 缺失 fail-fast
2. 企微应用 send_message images 分支 — ImageRef 解析 → upload_media → send_image
3. 企微应用 send_message files 图片分支 — Redis 解析本地路径 → upload+send；
   超过 2MB 临时素材限制降级 textcard
4. 企微应用 files 解析失败降级 textcard（不记失败）
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.saas.api.channel_routes import _build_image_attachment_from_bytes


# ---------- _build_image_attachment_from_bytes ----------


class TestBuildImageAttachmentFromBytes:
    def test_writes_to_tenant_conversation_dir(self, tmp_path):
        """未传 local_path：落盘到 tenants/{tid}/conversation/{timestamp}_{token}.jpg"""
        with patch("src.saas.api.channel_routes.ensure_tenant_storage_dir") as mock_dir:
            mock_dir.return_value = str(tmp_path)
            att = _build_image_attachment_from_bytes("t1", b"fakejpeg", "MEDIAID123", "T")

        assert att is not None
        assert att["type"] == "image"
        assert att["file_name"] == "image_MEDIAID123.jpg"
        assert att["mime_type"] == "image/jpeg"
        assert att["file_size"] == 8
        assert os.path.basename(att["local_path"]).endswith("_MEDIAID123.jpg")
        assert os.path.exists(att["local_path"])
        assert att["content"] == "ZmFrZWpwZWc="  # base64(b"fakejpeg")

    def test_reuses_provided_local_path(self, tmp_path):
        """传 local_path（飞书/钉钉 media 已落盘）：直接复用不重复写盘"""
        p = tmp_path / "feishu_img.jpg"
        p.write_bytes(b"xyz")

        with patch("src.saas.api.channel_routes.ensure_tenant_storage_dir") as mock_dir:
            att = _build_image_attachment_from_bytes(
                "t1", b"xyz", "imgkey1", "T", local_path=str(p)
            )
            mock_dir.assert_not_called()

        assert att is not None
        assert att["local_path"] == str(p)
        assert att["file_name"] == "feishu_img.jpg"
        assert att["file_size"] == 3

    def test_missing_tenant_id_raises(self):
        with pytest.raises(RuntimeError):
            _build_image_attachment_from_bytes("", b"x", "k", "T")

    def test_empty_bytes_returns_none(self):
        assert _build_image_attachment_from_bytes("t1", b"", "k", "T") is None


# ---------- 企微应用 send_message images / files ----------


def _make_wecom_response(text="", images=None, downloadable_files=None):
    from src.models.message import DownloadableFileInfo, UnifiedResponse

    return UnifiedResponse(
        message_id="msg_w",
        reply_to="userid_w",
        content={"text": text, **({"images": images} if images is not None else {})},
        downloadable_files=downloadable_files or [],
    )


def _image_ref(file_id="file_img1"):
    return {
        "file_id": file_id,
        "download_url": f"/api/files/{file_id}/download",
        "display_name": "效果图",
        "placement": "after_text",
        "source": "tool_generated",
        "usage": "inline",
        "mime_type": "image/png",
        "size_bytes": 1024,
    }


@pytest.fixture
def wecom_adapter():
    from src.channels.wecom.adapter import WeComAdapter

    a = WeComAdapter(corp_id="c1", agent_id="1000002", secret="s", rate_limit_enabled=False)
    a._tenant_id = "tenant_a"
    a.send_long_message = AsyncMock(return_value=True)
    a.send_image = AsyncMock(return_value=True)
    a.send_textcard = AsyncMock(return_value=True)
    a.media = MagicMock()
    a.media.upload_media = AsyncMock(return_value="media_id_x")
    return a


class TestWecomSendImages:
    @pytest.mark.asyncio
    async def test_images_sent_via_upload_and_send(self, wecom_adapter):
        """images 分支：resolve → upload_media("image") → send_image(media_id)"""
        msg = _make_wecom_response(text="效果图如下", images=[_image_ref()])
        with patch("src.core.image_asset.get_image_registry") as mock_reg:
            registry = MagicMock()
            registry.get_ref_by_file_id = AsyncMock(return_value=MagicMock())
            registry.resolve_local_path = AsyncMock(return_value="/tmp/render.png")
            mock_reg.return_value = registry

            ok = await wecom_adapter.send_message(msg)

        assert ok is True
        wecom_adapter.media.upload_media.assert_awaited_once_with("/tmp/render.png", "image")
        wecom_adapter.send_image.assert_awaited_once_with("media_id_x", "userid_w")
        wecom_adapter.send_long_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_image_file_sent_as_image(self, wecom_adapter):
        """files 图片分支：Redis 解析本地路径 → upload → send_image，不走 textcard"""
        from src.models.message import DownloadableFileInfo

        fi = DownloadableFileInfo(
            file_id="file_out1", file_name="render.png",
            file_size=1000, download_url="/api/files/file_out1/download",
        )
        msg = _make_wecom_response(text="done", downloadable_files=[fi])

        with patch(
            "src.channels.wecom.adapter.resolve_downloadable_file_local_path",
            return_value="/tmp/tenant/render.png",
        ):
            ok = await wecom_adapter.send_message(msg)

        assert ok is True
        wecom_adapter.media.upload_media.assert_awaited_once_with("/tmp/tenant/render.png", "image")
        wecom_adapter.send_image.assert_awaited_once()
        wecom_adapter.send_textcard.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oversized_image_file_falls_back_to_textcard(self, wecom_adapter):
        """超过企微临时素材 2MB：跳过 image 直发，走 textcard 下载链接"""
        from src.models.message import DownloadableFileInfo

        fi = DownloadableFileInfo(
            file_id="file_big", file_name="render.png",
            file_size=3 * 1024 * 1024, download_url="/api/files/file_big/download",
        )
        msg = _make_wecom_response(text="done", downloadable_files=[fi])

        with patch(
            "src.channels.wecom.adapter.resolve_downloadable_file_local_path",
            return_value="/tmp/big.png",
        ) as mock_resolve:
            ok = await wecom_adapter.send_message(msg)

        mock_resolve.assert_not_called()
        assert ok is True
        wecom_adapter.send_textcard.assert_awaited_once()
        wecom_adapter.send_image.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_image_file_resolve_failure_falls_back_to_textcard(self, wecom_adapter):
        """图片 files 路径解析失败（Redis 无记录/跨租户）：降级 textcard 不记失败"""
        from src.models.message import DownloadableFileInfo

        fi = DownloadableFileInfo(
            file_id="file_lost", file_name="render.jpg",
            file_size=1000, download_url="/api/files/file_lost/download",
        )
        msg = _make_wecom_response(text="done", downloadable_files=[fi])

        with patch(
            "src.channels.wecom.adapter.resolve_downloadable_file_local_path",
            return_value=None,
        ):
            ok = await wecom_adapter.send_message(msg)

        assert ok is True
        wecom_adapter.send_textcard.assert_awaited_once()
        wecom_adapter.media.upload_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_image_file_keeps_textcard(self, wecom_adapter):
        from src.models.message import DownloadableFileInfo

        fi = DownloadableFileInfo(
            file_id="file_doc", file_name="报价.xlsx",
            file_size=2048, download_url="/api/files/file_doc/download",
        )
        msg = _make_wecom_response(text="done", downloadable_files=[fi])

        ok = await wecom_adapter.send_message(msg)

        assert ok is True
        wecom_adapter.send_textcard.assert_awaited_once()
        wecom_adapter.send_image.assert_not_awaited()
