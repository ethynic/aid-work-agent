"""
钉钉媒体文件处理单元测试

测试 DingTalkMedia 的下载/上传功能，mock 所有外部 HTTP 调用。
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.dingtalk.media import DingTalkMedia, _ext_from_content_type


@pytest.fixture
def token_getter():
    getter = AsyncMock(return_value="mock_access_token")
    return getter


@pytest.fixture
def media(token_getter, tmp_path):
    return DingTalkMedia(access_token_getter=token_getter, upload_dir=str(tmp_path))


class TestDingTalkMediaInit:
    """初始化测试"""

    def test_default_upload_dir(self, token_getter):
        with tempfile.TemporaryDirectory() as tmp:
            custom_dir = os.path.join(tmp, "custom_uploads")
            m = DingTalkMedia(access_token_getter=token_getter, upload_dir=custom_dir)
            assert m.upload_dir == custom_dir
            assert os.path.exists(custom_dir)

    def test_upload_dir_created(self, token_getter, tmp_path):
        new_dir = str(tmp_path / "new" / "nested" / "dir")
        m = DingTalkMedia(access_token_getter=token_getter, upload_dir=new_dir)
        assert os.path.exists(new_dir)
        assert m.upload_dir == new_dir


class TestDingTalkMediaDownloadImage:
    """图片下载测试"""

    @pytest.mark.asyncio
    async def test_download_image_success(self, media, token_getter):
        """成功下载图片"""
        mock_image_content = b"PNG\r\n\x1a\n\x00\x00fake_image_data"

        # mock 获取下载 URL 的响应
        mock_url_response = MagicMock()
        mock_url_response.status_code = 200
        mock_url_response.json.return_value = {
            "downloadUrl": "https://example.com/image.png"
        }

        # mock 下载图片的响应
        mock_image_response = MagicMock()
        mock_image_response.status_code = 200
        mock_image_response.content = mock_image_content
        mock_image_response.headers = {"Content-Type": "image/png"}

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            # 第一次 POST 返回 URL，第二次 GET 返回图片内容
            mock_client.post.return_value = mock_url_response
            mock_client.get.return_value = mock_image_response

            result = await media.download_image("download_code_123", "robot_code_abc")

            assert result is not None
            local_path, content = result
            assert content == mock_image_content
            assert local_path.endswith(".png")
            assert os.path.exists(local_path)
            token_getter.assert_awaited()

    @pytest.mark.asyncio
    async def test_download_image_get_url_failed(self, media):
        """获取下载 URL 失败"""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = await media.download_image("code_xxx", "robot_xxx")
            assert result is None

    @pytest.mark.asyncio
    async def test_download_image_no_download_url(self, media):
        """响应缺少 downloadUrl 字段"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"other": "data"}

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = await media.download_image("code_xxx", "robot_xxx")
            assert result is None

    @pytest.mark.asyncio
    async def test_download_image_download_failed(self, media):
        """下载图片本身失败"""
        mock_url_response = MagicMock()
        mock_url_response.status_code = 200
        mock_url_response.json.return_value = {"downloadUrl": "https://example.com/img"}

        mock_image_response = MagicMock()
        mock_image_response.status_code = 500

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_url_response
            mock_client.get.return_value = mock_image_response

            result = await media.download_image("code_xxx", "robot_xxx")
            assert result is None

    @pytest.mark.asyncio
    async def test_download_image_jpeg_extension(self, media):
        """根据 Content-Type 推断 jpeg 扩展名"""
        mock_url_response = MagicMock()
        mock_url_response.status_code = 200
        mock_url_response.json.return_value = {"downloadUrl": "https://example.com/img"}

        mock_image_response = MagicMock()
        mock_image_response.status_code = 200
        mock_image_response.content = b"jpeg_data"
        mock_image_response.headers = {"Content-Type": "image/jpeg"}

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_url_response
            mock_client.get.return_value = mock_image_response

            result = await media.download_image("code_xxx", "robot_xxx")
            assert result is not None
            local_path, _ = result
            assert local_path.endswith(".jpg")

    @pytest.mark.asyncio
    async def test_download_image_unknown_content_type(self, media):
        """未知 Content-Type 使用 .bin 扩展名"""
        mock_url_response = MagicMock()
        mock_url_response.status_code = 200
        mock_url_response.json.return_value = {"downloadUrl": "https://example.com/img"}

        mock_image_response = MagicMock()
        mock_image_response.status_code = 200
        mock_image_response.content = b"unknown_data"
        mock_image_response.headers = {"Content-Type": "application/octet-stream"}

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_url_response
            mock_client.get.return_value = mock_image_response

            result = await media.download_image("code_xxx", "robot_xxx")
            assert result is not None
            local_path, _ = result
            assert local_path.endswith(".bin")

    @pytest.mark.asyncio
    async def test_download_image_exception(self, media):
        """异常情况下返回 None"""
        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = Exception("network error")

            result = await media.download_image("code_xxx", "robot_xxx")
            assert result is None


class TestDingTalkMediaDownloadFile:
    """文件下载测试"""

    @pytest.mark.asyncio
    async def test_download_file_success(self, media):
        """成功下载文件"""
        mock_file_content = b"file_content_bytes"

        mock_url_response = MagicMock()
        mock_url_response.status_code = 200
        mock_url_response.json.return_value = {
            "downloadUrl": "https://example.com/file.pdf"
        }

        mock_file_response = MagicMock()
        mock_file_response.status_code = 200
        mock_file_response.content = mock_file_content

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_url_response
            mock_client.get.return_value = mock_file_response

            result = await media.download_file(
                "download_code_456", "robot_abc", file_name="report.pdf"
            )

            assert result is not None
            local_path, content = result
            assert content == mock_file_content
            assert local_path.endswith("report.pdf")
            assert os.path.exists(local_path)

    @pytest.mark.asyncio
    async def test_download_file_use_download_code_as_name(self, media):
        """未提供 file_name 时使用 download_code"""
        mock_url_response = MagicMock()
        mock_url_response.status_code = 200
        mock_url_response.json.return_value = {"downloadUrl": "https://example.com/f"}

        mock_file_response = MagicMock()
        mock_file_response.status_code = 200
        mock_file_response.content = b"data"

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_url_response
            mock_client.get.return_value = mock_file_response

            result = await media.download_file("dl_code_xyz", "robot_abc")
            assert result is not None
            local_path, _ = result
            assert "dl_code_xyz" in local_path

    @pytest.mark.asyncio
    async def test_download_file_custom_save_dir(self, media, tmp_path):
        """指定 save_dir 覆盖默认目录"""
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()

        mock_url_response = MagicMock()
        mock_url_response.status_code = 200
        mock_url_response.json.return_value = {"downloadUrl": "https://example.com/f"}

        mock_file_response = MagicMock()
        mock_file_response.status_code = 200
        mock_file_response.content = b"data"

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_url_response
            mock_client.get.return_value = mock_file_response

            result = await media.download_file(
                "code_xxx", "robot_xxx", save_dir=str(custom_dir)
            )
            assert result is not None
            local_path, _ = result
            assert str(custom_dir) in local_path


class TestDingTalkMediaUploadMedia:
    """媒体上传测试"""

    @pytest.mark.asyncio
    async def test_upload_media_success(self, media, tmp_path):
        """成功上传文件"""
        # 创建测试文件
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        mock_response = MagicMock()
        mock_response.json.return_value = {"mediaId": "media_id_123"}

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = await media.upload_media(str(test_file), "robot_abc", "file")
            assert result == "media_id_123"

    @pytest.mark.asyncio
    async def test_upload_media_response_with_data_wrapper(self, media, tmp_path):
        """响应格式：data.mediaId"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"mediaId": "media_id_456"}}

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = await media.upload_media(str(test_file), "robot_abc")
            assert result == "media_id_456"

    @pytest.mark.asyncio
    async def test_upload_media_no_media_id(self, media, tmp_path):
        """响应缺少 mediaId"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "something"}

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = await media.upload_media(str(test_file), "robot_abc")
            assert result is None

    @pytest.mark.asyncio
    async def test_upload_media_file_not_exists(self, media, tmp_path):
        """文件不存在"""
        fake_path = str(tmp_path / "nonexistent.txt")
        result = await media.upload_media(fake_path, "robot_abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_upload_media_exception(self, media, tmp_path):
        """上传异常"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = Exception("upload error")

            result = await media.upload_media(str(test_file), "robot_abc")
            assert result is None


class TestDingTalkMediaUploadFromUrl:
    """upload_from_url 测试 — 适配 DownloadableFileInfo 只携带 download_url 的场景"""

    @pytest.mark.asyncio
    async def test_missing_download_url_returns_none(self, media):
        """download_url 为空 → 直接返回 None"""
        result = await media.upload_from_url("", "file.txt", "robot_abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_failure_returns_none(self, media):
        """下载失败 → 返回 None，不调用 upload_media"""
        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value = mock_resp
            mock_cm.__exit__.return_value = False
            mock_client = MagicMock()
            mock_client.stream.return_value = mock_cm
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await media.upload_from_url(
                "https://example.com/file.txt", "file.txt", "robot_abc"
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_download_and_upload_success(self, media, tmp_path):
        """下载成功 → 落地到 upload_dir → 委托 upload_media 拿到 mediaId"""
        # 让 upload_dir 指向 tmp_path，避免污染真实目录
        media.upload_dir = str(tmp_path)

        download_bytes = b"hello world"

        with patch("src.channels.dingtalk.media.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.iter_bytes.return_value = [download_bytes[:5], download_bytes[5:]]

            mock_cm = MagicMock()
            mock_cm.__enter__.return_value = mock_resp
            mock_cm.__exit__.return_value = False

            mock_client = MagicMock()
            mock_client.stream.return_value = mock_cm
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            # mock upload_media 验证委托链路
            called_with = {}

            async def fake_upload(path, robot_code, media_type):
                called_with["path"] = path
                called_with["robot_code"] = robot_code
                called_with["media_type"] = media_type
                return "media_id_xyz"

            with patch.object(media, "upload_media", side_effect=fake_upload):
                result = await media.upload_from_url(
                    "https://example.com/file.txt",
                    "file.txt",
                    "robot_abc",
                    "file",
                )

            assert result == "media_id_xyz"
            assert called_with["robot_code"] == "robot_abc"
            assert called_with["media_type"] == "file"
            assert os.path.exists(called_with["path"])
            with open(called_with["path"], "rb") as f:
                assert f.read() == download_bytes


class TestExtFromContentType:
    """Content-Type 扩展名推断测试"""

    def test_png(self):
        assert _ext_from_content_type("image/png") == ".png"

    def test_jpeg(self):
        assert _ext_from_content_type("image/jpeg") == ".jpg"

    def test_jpg(self):
        assert _ext_from_content_type("image/jpg") == ".jpg"

    def test_gif(self):
        assert _ext_from_content_type("image/gif") == ".gif"

    def test_bmp(self):
        assert _ext_from_content_type("image/bmp") == ".bmp"

    def test_webp(self):
        assert _ext_from_content_type("image/webp") == ".webp"

    def test_with_charset(self):
        """Content-Type 带 charset 参数"""
        assert _ext_from_content_type("image/png; charset=utf-8") == ".png"

    def test_uppercase(self):
        """Content-Type 大写"""
        assert _ext_from_content_type("IMAGE/PNG") == ".png"

    def test_unknown(self):
        """未知类型返回 .bin"""
        assert _ext_from_content_type("application/octet-stream") == ".bin"
        assert _ext_from_content_type("text/plain") == ".bin"

    def test_empty(self):
        """空字符串返回 .bin"""
        assert _ext_from_content_type("") == ".bin"
