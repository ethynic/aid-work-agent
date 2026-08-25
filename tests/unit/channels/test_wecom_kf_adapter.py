"""
企业微信客服适配器 send_message 单元测试

重点覆盖 downloadable_files 的发送分流逻辑：
- 图片文件（image/*）优先作为 image 消息直接发送
- 图片发送失败/不满足前置条件时降级为 link 卡片或纯文本链接
- 非图片文件保持原 link 卡片 / 纯文本逻辑
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.wecom_kf.adapter import WeComKfAdapter
from src.models.message import DownloadableFileInfo, UnifiedResponse


# ---------- fixtures ----------


@pytest.fixture
def adapter():
    """构造 adapter，避免触发真实加解密/网络。"""
    a = WeComKfAdapter(
        corp_id="test_corp",
        secret="test_secret_xxxxxxxxxxxxxxxx",
        token="test_token",
        encoding_aes_key="",
        kf_account=[{"open_kfid": "kfXXX", "name": "测试客服"}],
    )
    a.current_open_kfid = "kfXXX"
    # mock API 客户端，所有方法返回成功空响应
    a.api_client = MagicMock()
    a.api_client.upload_media = AsyncMock(return_value={"errcode": 0, "media_id": "MEDIA_FAKE"})
    a.api_client.send_msg = AsyncMock(return_value={"errcode": 0, "errmsg": "ok"})
    # mock 默认缩略图，避免触发真实 PNG 生成与上传
    a._get_default_thumb_media_id = AsyncMock(return_value="THUMB_FAKE")
    return a


def _make_response(files):
    """构造只含 downloadable_files 的 UnifiedResponse。"""
    return UnifiedResponse(
        message_id="msg_resp_001",
        reply_to="external_user_001",
        content={"text": ""},
        downloadable_files=files,
    )


def _file(file_id="file_abc123", mime_type="image/png", file_size=1024, file_name="pic.png", download_url="/api/files/file_abc123/download"):
    return DownloadableFileInfo(
        file_id=file_id,
        file_name=file_name,
        file_size=file_size,
        download_url=download_url,
        mime_type=mime_type,
    )


# ---------- 图片文件 -> image 消息 ----------


class TestSendImageFileAsImage:
    """图片文件应直接走 image 消息（用户在微信侧直接看到图片）。"""

    @pytest.mark.asyncio
    async def test_image_file_sent_as_image(self, adapter, tmp_path):
        """mime=image/png 的小文件，应 upload_media + send_msg(msgtype=image)。"""
        img_path = tmp_path / "pic.png"
        img_path.write_bytes(b"fake-png")

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=True):
            mock_redis.make_key.return_value = "key:uploaded_file:file_abc123"
            mock_redis.hgetall.return_value = {"path": str(img_path)}

            response = _make_response([_file(mime_type="image/png", file_size=1024)])
            result = await adapter.send_message(response)

        assert result is True
        # 应上传图片素材
        adapter.api_client.upload_media.assert_awaited()
        upload_args = adapter.api_client.upload_media.call_args
        assert upload_args.args[0] == str(img_path)
        assert upload_args.args[1] == "image"
        # 应以 image 消息发送
        send_call = adapter.api_client.send_msg.call_args
        assert send_call.kwargs["msgtype"] == "image"
        assert send_call.kwargs["content"] == {"media_id": "MEDIA_FAKE"}
        assert send_call.kwargs["touser"] == "external_user_001"
        assert send_call.kwargs["open_kfid"] == "kfXXX"

    @pytest.mark.asyncio
    async def test_image_file_mime_case_insensitive(self, adapter, tmp_path):
        """mime 大小写不敏感（IMAGE/PNG）。"""
        img_path = tmp_path / "pic.png"
        img_path.write_bytes(b"x")

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=True):
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"path": str(img_path)}

            response = _make_response([_file(mime_type="IMAGE/PNG", file_size=512)])
            await adapter.send_message(response)

        send_call = adapter.api_client.send_msg.call_args
        assert send_call.kwargs["msgtype"] == "image"

    @pytest.mark.asyncio
    async def test_jpeg_also_sent_as_image(self, adapter, tmp_path):
        """image/jpeg 同样走 image 消息。"""
        img_path = tmp_path / "a.jpg"
        img_path.write_bytes(b"x")

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=True):
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"path": str(img_path)}

            response = _make_response([_file(mime_type="image/jpeg", file_size=100, file_name="a.jpg")])
            await adapter.send_message(response)

        assert adapter.api_client.send_msg.call_args.kwargs["msgtype"] == "image"


# ---------- 降级场景 ----------


class TestImageFileFallback:
    """图片文件不满足前置条件或发送失败时，应降级到 link 卡片。"""

    @pytest.mark.asyncio
    async def test_image_over_2mb_fallback_to_link(self, adapter):
        """超过企微 image 2MB 限制 -> 走 link 卡片。"""
        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            # 不应被调用到 hgetall
            mock_redis.hgetall.return_value = {}

            response = _make_response([_file(mime_type="image/png", file_size=3 * 1024 * 1024)])
            await adapter.send_message(response)

        # 不应上传图片素材
        adapter.api_client.upload_media.assert_not_awaited()
        # 应以 link 消息发送
        send_call = adapter.api_client.send_msg.call_args
        assert send_call.kwargs["msgtype"] == "link"
        assert send_call.kwargs["content"]["thumb_media_id"] == "THUMB_FAKE"

    @pytest.mark.asyncio
    async def test_image_no_redis_meta_fallback_to_link(self, adapter):
        """Redis 无文件元数据 -> 走 link 卡片。"""
        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {}

            response = _make_response([_file(mime_type="image/png", file_size=1024)])
            await adapter.send_message(response)

        adapter.api_client.upload_media.assert_not_awaited()
        assert adapter.api_client.send_msg.call_args.kwargs["msgtype"] == "link"

    @pytest.mark.asyncio
    async def test_image_local_path_missing_fallback_to_link(self, adapter):
        """本地文件已被清理 -> 走 link 卡片。"""
        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=False):
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"path": "/nonexistent/pic.png"}

            response = _make_response([_file(mime_type="image/png", file_size=1024)])
            await adapter.send_message(response)

        adapter.api_client.upload_media.assert_not_awaited()
        assert adapter.api_client.send_msg.call_args.kwargs["msgtype"] == "link"

    @pytest.mark.asyncio
    async def test_image_upload_no_media_id_fallback_to_link(self, adapter, tmp_path):
        """upload_media 返回无 media_id -> 走 link 卡片。"""
        img_path = tmp_path / "pic.png"
        img_path.write_bytes(b"x")
        adapter.api_client.upload_media = AsyncMock(return_value={"errcode": 40001, "errmsg": "invalid credential"})

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=True):
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"path": str(img_path)}

            response = _make_response([_file(mime_type="image/png", file_size=1024)])
            await adapter.send_message(response)

        # upload 调用过但 send_msg 应是 link（image 发送未成功）
        adapter.api_client.upload_media.assert_awaited()
        send_call = adapter.api_client.send_msg.call_args
        assert send_call.kwargs["msgtype"] == "link"

    @pytest.mark.asyncio
    async def test_image_send_errcode_nonzero_fallback_to_link(self, adapter, tmp_path):
        """image 消息发送 errcode!=0 -> 降级走 link 卡片。"""
        img_path = tmp_path / "pic.png"
        img_path.write_bytes(b"x")
        # 第一次 image 发送失败，第二次 link 发送成功
        adapter.api_client.send_msg = AsyncMock(
            side_effect=[
                {"errcode": 40001, "errmsg": "fail"},
                {"errcode": 0, "errmsg": "ok"},
            ]
        )

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=True):
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"path": str(img_path)}

            response = _make_response([_file(mime_type="image/png", file_size=1024)])
            await adapter.send_message(response)

        # 两次 send_msg：先 image 失败，再 link 成功
        assert adapter.api_client.send_msg.await_count == 2
        first = adapter.api_client.send_msg.call_args_list[0].kwargs
        second = adapter.api_client.send_msg.call_args_list[1].kwargs
        assert first["msgtype"] == "image"
        assert second["msgtype"] == "link"

    @pytest.mark.asyncio
    async def test_image_no_thumb_fallback_to_text_link(self, adapter, tmp_path):
        """无 thumb_media_id 时，图片发送失败应降级为纯文本链接（而不是 link 卡片）。"""
        img_path = tmp_path / "pic.png"
        img_path.write_bytes(b"x")
        adapter._get_default_thumb_media_id = AsyncMock(return_value="")
        adapter.api_client.upload_media = AsyncMock(return_value={"errcode": -1, "errmsg": "upload fail"})

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=True):
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"path": str(img_path)}

            response = _make_response([_file(mime_type="image/png", file_size=1024)])
            await adapter.send_message(response)

        # upload 调用过；send_msg 应以 text 形式（纯文本链接）
        adapter.api_client.upload_media.assert_awaited()
        send_call = adapter.api_client.send_msg.call_args
        assert send_call.kwargs["msgtype"] == "text"
        assert "pic.png" in send_call.kwargs["content"]["content"]
        assert "/api/files/file_abc123/download" in send_call.kwargs["content"]["content"]


# ---------- 非图片文件保持原逻辑 ----------


class TestNonImageFile:
    """非图片文件应走原有 link 卡片 / 纯文本逻辑。"""

    @pytest.mark.asyncio
    async def test_pdf_file_sent_as_link(self, adapter):
        """PDF 文件 -> link 卡片。"""
        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {}

            response = _make_response([
                _file(mime_type="application/pdf", file_size=10240, file_name="report.pdf")
            ])
            await adapter.send_message(response)

        # 不应上传图片素材
        adapter.api_client.upload_media.assert_not_awaited()
        send_call = adapter.api_client.send_msg.call_args
        assert send_call.kwargs["msgtype"] == "link"
        assert send_call.kwargs["content"]["title"] == "report.pdf"
        assert send_call.kwargs["content"]["thumb_media_id"] == "THUMB_FAKE"

    @pytest.mark.asyncio
    async def test_unknown_mime_sent_as_link(self, adapter):
        """未知 mime -> link 卡片。"""
        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {}

            response = _make_response([
                _file(mime_type="", file_size=100, file_name="data.bin")
            ])
            await adapter.send_message(response)

        adapter.api_client.upload_media.assert_not_awaited()
        assert adapter.api_client.send_msg.call_args.kwargs["msgtype"] == "link"

    @pytest.mark.asyncio
    async def test_non_image_no_thumb_fallback_to_text(self, adapter):
        """非图片文件 + 无 thumb -> 纯文本链接。"""
        adapter._get_default_thumb_media_id = AsyncMock(return_value="")

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {}

            response = _make_response([
                _file(mime_type="application/pdf", file_size=1024, file_name="r.pdf")
            ])
            await adapter.send_message(response)

        send_call = adapter.api_client.send_msg.call_args
        assert send_call.kwargs["msgtype"] == "text"
        content = send_call.kwargs["content"]["content"]
        assert "r.pdf" in content


# ---------- 混合场景 ----------


class TestMixedFiles:
    """一次响应中混合多种文件类型。"""

    @pytest.mark.asyncio
    async def test_image_then_pdf(self, adapter, tmp_path):
        """先图片（image 消息）后 PDF（link 卡片）。"""
        img_path = tmp_path / "pic.png"
        img_path.write_bytes(b"x")

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=True):
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"path": str(img_path)}

            response = _make_response([
                _file(mime_type="image/png", file_size=512, file_name="pic.png"),
                _file(mime_type="application/pdf", file_size=2048, file_name="r.pdf", file_id="file_pdf1"),
            ])
            await adapter.send_message(response)

        # 两次 send_msg：先 image，后 link
        assert adapter.api_client.send_msg.await_count == 2
        types = [c.kwargs["msgtype"] for c in adapter.api_client.send_msg.call_args_list]
        assert types == ["image", "link"]


# ---------- 整段长图优先逻辑 ----------


class TestSendMessageFullImageFallback:
    """send_message 在 text 含表格/图片时应优先整段渲染长图，失败时降级分段。"""

    @pytest.mark.asyncio
    async def test_table_text_triggers_full_image(self, adapter):
        """text 含 md 表格 -> 调用 _send_full_text_as_image，不再走分段。"""
        from src.models.message import UnifiedResponse

        resp = UnifiedResponse(
            message_id="msg_1",
            reply_to="external_user_001",
            content={"text": "| A | B |\n|---|---|\n| 1 | 2 |"},
        )

        # mock _send_full_text_as_image 返回 True，验证被调用
        adapter._send_full_text_as_image = AsyncMock(return_value=True)
        adapter._send_segmented = AsyncMock(return_value=True)

        ok = await adapter.send_message(resp)

        assert ok is True
        adapter._send_full_text_as_image.assert_awaited_once()
        # 长图成功时不应走分段
        adapter._send_segmented.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_image_text_triggers_full_image(self, adapter):
        """text 含 file_id: 图片 -> 调用 _send_full_text_as_image。"""
        from src.models.message import UnifiedResponse

        resp = UnifiedResponse(
            message_id="msg_2",
            reply_to="external_user_001",
            content={"text": "看这张图：![cat](file_id:file_abc12345)"},
        )

        adapter._send_full_text_as_image = AsyncMock(return_value=True)
        adapter._send_segmented = AsyncMock(return_value=True)

        ok = await adapter.send_message(resp)

        assert ok is True
        adapter._send_full_text_as_image.assert_awaited_once()
        adapter._send_segmented.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_full_image_failure_falls_back_to_segmented(self, adapter):
        """长图渲染/发送失败 -> 降级走分段逻辑。"""
        from src.models.message import UnifiedResponse

        resp = UnifiedResponse(
            message_id="msg_3",
            reply_to="external_user_001",
            content={"text": "| A | B |\n|---|---|\n| 1 | 2 |"},
        )

        adapter._send_full_text_as_image = AsyncMock(return_value=False)
        adapter._send_segmented = AsyncMock(return_value=True)

        ok = await adapter.send_message(resp)

        assert ok is True
        adapter._send_full_text_as_image.assert_awaited_once()
        adapter._send_segmented.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_plain_text_skips_full_image(self, adapter):
        """纯文本（无表格无图片）-> 不走长图，直接走分段。"""
        from src.models.message import UnifiedResponse

        resp = UnifiedResponse(
            message_id="msg_4",
            reply_to="external_user_001",
            content={"text": "你好，这是一段普通回复。"},
        )

        adapter._send_full_text_as_image = AsyncMock(return_value=True)
        adapter._send_segmented = AsyncMock(return_value=True)

        ok = await adapter.send_message(resp)

        assert ok is True
        adapter._send_full_text_as_image.assert_not_awaited()
        adapter._send_segmented.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_render_disabled_skips_full_image(self, adapter):
        """render_tables=False -> 即使含表格也走纯文本全流程。"""
        from src.models.message import UnifiedResponse

        adapter._render_enabled = False
        resp = UnifiedResponse(
            message_id="msg_5",
            reply_to="external_user_001",
            content={"text": "| A | B |\n|---|---|\n| 1 | 2 |"},
        )

        adapter._send_full_text_as_image = AsyncMock(return_value=True)
        adapter._send_segmented = AsyncMock(return_value=True)
        adapter._send_as_plain_text = AsyncMock(return_value=True)

        ok = await adapter.send_message(resp)

        assert ok is True
        adapter._send_full_text_as_image.assert_not_awaited()
        adapter._send_segmented.assert_not_awaited()
        adapter._send_as_plain_text.assert_awaited_once()


class TestSendFullTextAsImage:
    """_send_full_text_as_image：render_markdown -> upload_media -> send_msg(image)。"""

    @pytest.mark.asyncio
    async def test_success_path(self, adapter, tmp_path):
        """长图渲染成功 + 上传成功 + 发送成功 -> 返回 True。"""
        img_path = tmp_path / "md_xxx.png"
        img_path.write_bytes(b"fake-png")

        # mock renderer.render_markdown 返回路径
        adapter._renderer = MagicMock()
        adapter._renderer.render_markdown = AsyncMock(return_value=str(img_path))

        ok = await adapter._send_full_text_as_image("| A | B |\n|---|---|\n| 1 | 2 |", "user_1")

        assert ok is True
        adapter._renderer.render_markdown.assert_awaited_once()
        adapter.api_client.upload_media.assert_awaited_once()
        args, kwargs = adapter.api_client.upload_media.call_args
        assert kwargs.get("media_type") == "image" or args[1] == "image"
        adapter.api_client.send_msg.assert_awaited_once()
        _, kwargs = adapter.api_client.send_msg.call_args
        assert kwargs.get("msgtype") == "image"
        assert kwargs.get("content") == {"media_id": "MEDIA_FAKE"}

    @pytest.mark.asyncio
    async def test_render_returns_none(self, adapter):
        """renderer.render_markdown 返回 None -> 返回 False。"""
        adapter._renderer = MagicMock()
        adapter._renderer.render_markdown = AsyncMock(return_value=None)

        ok = await adapter._send_full_text_as_image("| A | B |\n|---|---|\n| 1 | 2 |", "user_1")

        assert ok is False
        adapter.api_client.upload_media.assert_not_awaited()
        adapter.api_client.send_msg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upload_no_media_id(self, adapter, tmp_path):
        """upload_media 未返回 media_id -> 返回 False。"""
        img_path = tmp_path / "md_xxx.png"
        img_path.write_bytes(b"fake-png")

        adapter._renderer = MagicMock()
        adapter._renderer.render_markdown = AsyncMock(return_value=str(img_path))
        adapter.api_client.upload_media = AsyncMock(return_value={"errcode": 0, "errmsg": "no media"})

        ok = await adapter._send_full_text_as_image("| A | B |\n|---|---|\n| 1 | 2 |", "user_1")

        assert ok is False
        adapter.api_client.send_msg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_msg_failure(self, adapter, tmp_path):
        """send_msg 返回非 0 errcode -> 返回 False。"""
        img_path = tmp_path / "md_xxx.png"
        img_path.write_bytes(b"fake-png")

        adapter._renderer = MagicMock()
        adapter._renderer.render_markdown = AsyncMock(return_value=str(img_path))
        adapter.api_client.send_msg = AsyncMock(return_value={"errcode": 40001, "errmsg": "invalid"})

        ok = await adapter._send_full_text_as_image("| A | B |\n|---|---|\n| 1 | 2 |", "user_1")

        assert ok is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, adapter):
        """render_markdown 抛异常 -> 捕获并返回 False。"""
        adapter._renderer = MagicMock()
        adapter._renderer.render_markdown = AsyncMock(side_effect=RuntimeError("boom"))

        ok = await adapter._send_full_text_as_image("| A | B |\n|---|---|\n| 1 | 2 |", "user_1")

        assert ok is False


class TestGetDefaultThumbMediaId:
    """默认缩略图 media_id 的缓存键必须带企业维度（corp_id），防止多企业互相污染。

    线上事故：企业 A 上传缩略图缓存后，企业 B 复用该 media_id 发送 link 消息，
    微信侧校验 media_id 不属于企业 B，报 40007 invalid media_id。
    """

    @pytest.fixture
    def real_thumb_adapter(self):
        """与 adapter fixture 相同，但不 mock _get_default_thumb_media_id（走真实实现）。"""
        from src.channels.wecom_kf.adapter import WeComKfAdapter

        a = WeComKfAdapter(
            corp_id="test_corp",
            secret="test_secret_xxxxxxxxxxxxxxxx",
            token="test_token",
            encoding_aes_key="",
            kf_account=[{"open_kfid": "kfXXX", "name": "测试客服"}],
        )
        a.current_open_kfid = "kfXXX"
        a.api_client = MagicMock()
        a.api_client.upload_media = AsyncMock(return_value={"errcode": 0, "media_id": "MEDIA_FAKE"})
        a.api_client.send_msg = AsyncMock(return_value={"errcode": 0, "errmsg": "ok"})
        return a

    @pytest.mark.asyncio
    async def test_cache_key_contains_corp_id(self, real_thumb_adapter, tmp_path):
        """无缓存时上传并写入带企业维度的缓存键。"""
        real_thumb_adapter.corp_id = "corp_a"
        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.WeComKfAdapter._generate_default_thumb", return_value=b"png"), \
             patch("src.channels.wecom_kf.adapter.WeComKfAdapter._resolve_media_dir", return_value=str(tmp_path)):
            mock_redis.get.return_value = None  # 无缓存
            mid = await real_thumb_adapter._get_default_thumb_media_id()

        assert mid == "MEDIA_FAKE"
        key_args = mock_redis.make_key.call_args.args
        assert key_args[0] == "wecom_kf"
        assert "corp_a" in key_args[1]
        # 缓存写入应使用带企业维度的 key
        assert mock_redis.set.call_args.args[0] == mock_redis.make_key.return_value

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached(self, real_thumb_adapter):
        """缓存命中时直接返回缓存 media_id，不触发上传。"""
        real_thumb_adapter.corp_id = "corp_a"
        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis:
            mock_redis.get.return_value = "CACHED_MEDIA"

            mid = await real_thumb_adapter._get_default_thumb_media_id()

        assert mid == "CACHED_MEDIA"
        real_thumb_adapter.api_client.upload_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_different_corp_use_different_cache_key(self, tmp_path):
        """不同企业的缓存键应不同（核心回归点）。"""
        from src.channels.wecom_kf.adapter import WeComKfAdapter

        key_suffixes = []
        for corp in ("corp_a", "corp_b"):
            a = WeComKfAdapter(
                corp_id=corp,
                secret="s",
                token="t",
                encoding_aes_key="",
                kf_account=[{"open_kfid": "kf1", "name": "测试客服"}],
            )
            a.api_client = MagicMock()
            a.api_client.upload_media = AsyncMock(return_value={"errcode": 0, "media_id": f"MEDIA_{corp}"})
            with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
                 patch("src.channels.wecom_kf.adapter.WeComKfAdapter._generate_default_thumb", return_value=b"png"), \
                 patch("src.channels.wecom_kf.adapter.WeComKfAdapter._resolve_media_dir", return_value="/tmp"):
                mock_redis.get.return_value = None
                await a._get_default_thumb_media_id()
                key_suffixes.append(mock_redis.make_key.call_args.args[1])

        assert key_suffixes[0] != key_suffixes[1]
        assert "corp_a" in key_suffixes[0]
        assert "corp_b" in key_suffixes[1]


# ---------- 需切分的纯文本 -> 整段长图 ----------


class TestLongPlainTextAsImage:
    """纯文本超过单条 2048 字节、需要切分时整段转长图；单条装得下仍走分段。"""

    @pytest.mark.asyncio
    async def test_oversize_plain_text_triggers_full_image(self, adapter):
        """约 2100 字节的纯文本（单条装不下）-> 整段长图，不走分段。"""
        from src.models.message import UnifiedResponse

        text = "好" * 700  # 700 汉字 ≈ 2100 字节 > 2048
        resp = UnifiedResponse(message_id="msg_l1", reply_to="external_user_001", content={"text": text})

        adapter._send_full_text_as_image = AsyncMock(return_value=True)
        adapter._send_segmented = AsyncMock(return_value=True)

        ok = await adapter.send_message(resp)

        assert ok is True
        adapter._send_full_text_as_image.assert_awaited_once()
        adapter._send_segmented.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oversize_plain_text_image_failure_falls_back(self, adapter):
        """超长纯文本长图失败 -> 降级走分段逻辑。"""
        from src.models.message import UnifiedResponse

        text = "好" * 700
        resp = UnifiedResponse(message_id="msg_l2", reply_to="external_user_001", content={"text": text})

        adapter._send_full_text_as_image = AsyncMock(return_value=False)
        adapter._send_segmented = AsyncMock(return_value=True)

        ok = await adapter.send_message(resp)

        assert ok is True
        adapter._send_full_text_as_image.assert_awaited_once()
        adapter._send_segmented.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_boundary_fits_single_message_stays_segmented(self, adapter):
        """恰好在单条 2048 字节以内（无需切分）-> 走分段文本，不转长图。"""
        from src.models.message import UnifiedResponse

        text = "好" * 600  # 1800 字节 < 2048
        resp = UnifiedResponse(message_id="msg_l3", reply_to="external_user_001", content={"text": text})

        adapter._send_full_text_as_image = AsyncMock(return_value=True)
        adapter._send_segmented = AsyncMock(return_value=True)

        ok = await adapter.send_message(resp)

        assert ok is True
        adapter._send_full_text_as_image.assert_not_awaited()
        adapter._send_segmented.assert_awaited_once()


# ---------- images（ImageRef：客户留资二维码等）发送 ----------


def _make_image_response(file_id="file_qr1", size_bytes=1024, text=""):
    """构造 content.images 含单个 ImageRef dict 的 UnifiedResponse。"""
    return UnifiedResponse(
        message_id="msg_img_001",
        reply_to="external_user_001",
        content={"text": text, "images": [{"file_id": file_id, "size_bytes": size_bytes}]},
    )


class TestSendImageRefAsImage:
    """content.images 中的 ImageRef 图片（客户留资二维码）应作为 image 消息发送。"""

    @pytest.mark.asyncio
    async def test_image_ref_sent_as_image(self, adapter, tmp_path):
        """正常路径：ImageRef 图片 -> upload_media + send_msg(msgtype=image)。"""
        img_path = tmp_path / "qr.png"
        img_path.write_bytes(b"fake-qr")

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=True):
            mock_redis.make_key.return_value = "key:uploaded_file:file_qr1"
            mock_redis.hgetall.return_value = {"path": str(img_path)}

            result = await adapter.send_message(_make_image_response())

        assert result is True
        adapter.api_client.upload_media.assert_awaited_once()
        upload_args = adapter.api_client.upload_media.call_args
        assert upload_args.args[0] == str(img_path)
        assert upload_args.args[1] == "image"
        send_call = adapter.api_client.send_msg.call_args
        assert send_call.kwargs["msgtype"] == "image"
        assert send_call.kwargs["content"] == {"media_id": "MEDIA_FAKE"}
        assert send_call.kwargs["touser"] == "external_user_001"
        assert send_call.kwargs["open_kfid"] == "kfXXX"

    @pytest.mark.asyncio
    async def test_image_ref_no_file_id_skipped(self, adapter):
        """ImageRef 缺 file_id -> 跳过，不报错。"""
        resp = UnifiedResponse(
            message_id="msg_img_2",
            reply_to="external_user_001",
            content={"text": "", "images": [{}]},
        )
        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis:
            result = await adapter.send_message(resp)
        assert result is True
        adapter.api_client.upload_media.assert_not_awaited()
        adapter.api_client.send_msg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_image_ref_over_2mb_skipped(self, adapter):
        """ImageRef 超过企微 image 2MB 限制 -> 跳过发送。"""
        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {}
            result = await adapter.send_message(
                _make_image_response(size_bytes=3 * 1024 * 1024)
            )
        assert result is True
        adapter.api_client.upload_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_image_ref_no_redis_meta_skipped(self, adapter):
        """Redis 无该 file_id 元数据 -> 跳过。"""
        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {}
            result = await adapter.send_message(_make_image_response())
        assert result is True
        adapter.api_client.upload_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_image_ref_local_path_missing_skipped(self, adapter):
        """本地文件已被清理 -> 跳过。"""
        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=False):
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"path": "/nonexistent/qr.png"}
            result = await adapter.send_message(_make_image_response())
        assert result is True
        adapter.api_client.upload_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_image_ref_upload_no_media_id_failed(self, adapter, tmp_path):
        """upload_media 未返回 media_id -> 发送失败（返回 False）。"""
        img_path = tmp_path / "qr.png"
        img_path.write_bytes(b"x")
        adapter.api_client.upload_media = AsyncMock(
            return_value={"errcode": 40001, "errmsg": "invalid credential"}
        )

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=True):
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"path": str(img_path)}
            result = await adapter.send_message(_make_image_response())
        assert result is False
        adapter.api_client.send_msg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_image_ref_send_errcode_nonzero_failed(self, adapter, tmp_path):
        """image 消息发送 errcode!=0 -> 返回 False。"""
        img_path = tmp_path / "qr.png"
        img_path.write_bytes(b"x")
        adapter.api_client.send_msg = AsyncMock(return_value={"errcode": 40001, "errmsg": "fail"})

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=True):
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"path": str(img_path)}
            result = await adapter.send_message(_make_image_response())
        assert result is False

    @pytest.mark.asyncio
    async def test_image_ref_exception_returns_false(self, adapter, tmp_path):
        """上传抛异常 -> 捕获并返回 False。"""
        img_path = tmp_path / "qr.png"
        img_path.write_bytes(b"x")
        adapter.api_client.upload_media = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=True):
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"path": str(img_path)}
            result = await adapter.send_message(_make_image_response())
        assert result is False


class TestSendMessageWithImagesAndText:
    """text + images 混合：先发文本，再发二维码图片。"""

    @pytest.mark.asyncio
    async def test_text_then_image_ref(self, adapter, tmp_path):
        """文本回复 + 客户留资二维码应同时发送（图片以 image 消息发出）。"""
        img_path = tmp_path / "qr.png"
        img_path.write_bytes(b"x")
        adapter._send_segmented = AsyncMock(return_value=True)

        resp = UnifiedResponse(
            message_id="msg_img_mix",
            reply_to="external_user_001",
            content={
                "text": "没问题！二维码在下面了",
                "images": [{"file_id": "file_qrmix", "size_bytes": 1024}],
            },
        )

        with patch("src.channels.wecom_kf.adapter.redis_client") as mock_redis, \
             patch("src.channels.wecom_kf.adapter.os.path.exists", return_value=True):
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"path": str(img_path)}
            result = await adapter.send_message(resp)

        assert result is True
        # 文本走分段正常发送
        adapter._send_segmented.assert_awaited_once()
        # 图片以 image 消息发送
        adapter.api_client.upload_media.assert_awaited_once()
        send_call = adapter.api_client.send_msg.call_args
        assert send_call.kwargs["msgtype"] == "image"
        assert send_call.kwargs["content"] == {"media_id": "MEDIA_FAKE"}
