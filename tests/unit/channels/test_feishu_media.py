"""
飞书媒体处理单元测试

覆盖 FeishuMedia 的 download_image / download_file / upload_image / upload_file 四个方法。
锁定：
- API URL 路径正确
- Authorization header 格式
- 成功响应解析（image_key / file_key 提取）
- 失败响应处理（HTTP 错误、JSON 错误、异常）
- 文件类型自动推断
"""

import os
import tempfile

import pytest

from src.channels.feishu.media import (
    FEISHU_BASE_URL,
    FeishuMedia,
    _ext_from_content_type,
)


# ---------- fixtures ----------


class MockResponse:
    """模拟 httpx 响应"""

    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b"",
        headers: dict = None,
        json_data: dict = None,
        text: str = "",
    ):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self._json_data = json_data
        self.text = text or ("" if json_data is None else str(json_data))

    def json(self):
        if self._json_data is not None:
            return self._json_data
        import json
        return json.loads(self.text)


class MockAsyncClient:
    """模拟 httpx.AsyncClient 上下文管理器

    用法：
        with patch("httpx.AsyncClient", return_value=MockAsyncClient(response)):
    """

    def __init__(self, response: MockResponse):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, **kwargs):
        self.last_url = url
        self.last_kwargs = kwargs
        return self.response

    async def post(self, url, **kwargs):
        self.last_url = url
        self.last_kwargs = kwargs
        return self.response


@pytest.fixture
def upload_dir(tmp_path):
    d = tmp_path / "feishu_media"
    d.mkdir()
    return str(d)


@pytest.fixture
def media(upload_dir):
    async def _get_token():
        return "test_tenant_access_token"

    return FeishuMedia(access_token_getter=_get_token, upload_dir=upload_dir)


# ---------- download_image ----------


class TestDownloadImage:
    """GET /open-apis/im/v1/images/{image_key}?image_type=message"""

    @pytest.mark.asyncio
    async def test_success(self, media, upload_dir):
        image_bytes = b"\x89PNG\r\n\x1a\n...image binary data..."
        resp = MockResponse(
            status_code=200,
            content=image_bytes,
            headers={"Content-Type": "image/png"},
        )
        mock_client = MockAsyncClient(resp)

        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await media.download_image("img_v2_test_123")

        assert result is not None
        local_path, data = result
        assert data == image_bytes
        assert local_path.endswith(".png")
        assert "img_v2_test_123" in local_path
        # 文件已写入磁盘
        with open(local_path, "rb") as f:
            assert f.read() == image_bytes

        # 验证 URL
        assert mock_client.last_url == f"{FEISHU_BASE_URL}/open-apis/im/v1/images/img_v2_test_123"
        # 验证 Authorization header
        assert mock_client.last_kwargs["headers"]["Authorization"] == "Bearer test_tenant_access_token"
        # 验证 image_type 参数
        assert mock_client.last_kwargs["params"]["image_type"] == "message"

    @pytest.mark.asyncio
    async def test_jpeg_extension(self, media):
        resp = MockResponse(
            status_code=200,
            content=b"\xff\xd8\xff\xe0...",
            headers={"Content-Type": "image/jpeg"},
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            result = await media.download_image("img_v2_jpg_test")

        assert result is not None
        local_path, _ = result
        assert local_path.endswith(".jpg")

    @pytest.mark.asyncio
    async def test_http_error(self, media):
        resp = MockResponse(
            status_code=404,
            headers={"Content-Type": "application/json"},
            text='{"code":230001,"msg":"image not found"}',
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            result = await media.download_image("img_v2_not_exist")

        assert result is None

    @pytest.mark.asyncio
    async def test_json_error_response(self, media):
        """飞书返回 JSON 表示业务错误"""
        resp = MockResponse(
            status_code=200,
            headers={"Content-Type": "application/json; charset=utf-8"},
            json_data={"code": 230001, "msg": "image not found"},
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            result = await media.download_image("img_v2_bad")

        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, media):
        from unittest.mock import patch

        class FailingClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, *args, **kwargs):
                raise httpx.TimeoutException("timeout")

        import httpx

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=FailingClient(),
        ):
            result = await media.download_image("img_v2_timeout")

        assert result is None


# ---------- download_file ----------


class TestDownloadFile:
    """GET /open-apis/im/v1/files/{file_key}"""

    @pytest.mark.asyncio
    async def test_success_with_content_disposition(self, media):
        file_bytes = b"PDF file content here"
        resp = MockResponse(
            status_code=200,
            content=file_bytes,
            headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": 'attachment; filename="report.pdf"',
            },
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            result = await media.download_file("file_v2_test_456")

        assert result is not None
        local_path, data = result
        assert data == file_bytes
        # 文件名来自 Content-Disposition
        assert local_path.endswith("report.pdf")

    @pytest.mark.asyncio
    async def test_success_with_explicit_filename(self, media):
        file_bytes = b"doc content"
        resp = MockResponse(
            status_code=200,
            content=file_bytes,
            headers={"Content-Type": "application/octet-stream"},
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            result = await media.download_file(
                "file_v2_test", file_name="my_report.docx"
            )

        assert result is not None
        local_path, _ = result
        # 传入的 file_name 优先
        assert local_path.endswith("my_report.docx")

    @pytest.mark.asyncio
    async def test_success_fallback_to_file_key(self, media):
        """无 Content-Disposition 且无 file_name 时，使用 file_key 作为文件名"""
        resp = MockResponse(
            status_code=200,
            content=b"data",
            headers={"Content-Type": "application/octet-stream"},
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            result = await media.download_file("file_v2_fallback_name")

        assert result is not None
        local_path, _ = result
        assert "file_v2_fallback_name" in local_path

    @pytest.mark.asyncio
    async def test_json_error(self, media):
        resp = MockResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            json_data={"code": 230002, "msg": "file not found"},
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            result = await media.download_file("file_v2_missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_http_error(self, media):
        resp = MockResponse(
            status_code=500,
            headers={"Content-Type": "application/json"},
            text='{"code":-1,"msg":"internal error"}',
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            result = await media.download_file("file_v2_err")

        assert result is None


# ---------- upload_image ----------


class TestUploadImage:
    """POST /open-apis/im/v1/images  multipart: image_type + image"""

    @pytest.mark.asyncio
    async def test_success(self, media, tmp_path):
        # 准备测试图片
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\nfake_png")

        resp = MockResponse(
            status_code=200,
            json_data={"code": 0, "data": {"image_key": "img_v2_uploaded_abc"}},
        )
        mock_client = MockAsyncClient(resp)

        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=mock_client,
        ):
            image_key = await media.upload_image(str(img_path))

        assert image_key == "img_v2_uploaded_abc"
        # 验证 URL
        assert mock_client.last_url == f"{FEISHU_BASE_URL}/open-apis/im/v1/images"
        # 验证 Authorization
        assert mock_client.last_kwargs["headers"]["Authorization"] == "Bearer test_tenant_access_token"
        # 验证 image_type
        assert mock_client.last_kwargs["data"]["image_type"] == "message"

    @pytest.mark.asyncio
    async def test_custom_image_type(self, media, tmp_path):
        img_path = tmp_path / "avatar.png"
        img_path.write_bytes(b"\x89PNG")

        resp = MockResponse(
            status_code=200,
            json_data={"code": 0, "data": {"image_key": "img_v2_avatar"}},
        )
        mock_client = MockAsyncClient(resp)

        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=mock_client,
        ):
            image_key = await media.upload_image(str(img_path), image_type="avatar")

        assert image_key == "img_v2_avatar"
        assert mock_client.last_kwargs["data"]["image_type"] == "avatar"

    @pytest.mark.asyncio
    async def test_file_not_exist(self, media):
        from unittest.mock import patch

        result = await media.upload_image("/nonexistent/path.png")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_code(self, media, tmp_path):
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG")

        resp = MockResponse(
            status_code=200,
            json_data={"code": 230010, "msg": "invalid image"},
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            result = await media.upload_image(str(img_path))

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_image_key_in_response(self, media, tmp_path):
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG")

        resp = MockResponse(
            status_code=200,
            json_data={"code": 0, "data": {}},
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            result = await media.upload_image(str(img_path))

        assert result is None


# ---------- upload_file ----------


class TestUploadFile:
    """POST /open-apis/im/v1/files  multipart: file_type + file_name + file"""

    @pytest.mark.asyncio
    async def test_success_auto_detect_pdf(self, media, tmp_path):
        file_path = tmp_path / "report.pdf"
        file_path.write_bytes(b"%PDF-1.4 fake")

        resp = MockResponse(
            status_code=200,
            json_data={"code": 0, "data": {"file_key": "file_v2_pdf_key"}},
        )
        mock_client = MockAsyncClient(resp)

        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=mock_client,
        ):
            file_key = await media.upload_file(str(file_path))

        assert file_key == "file_v2_pdf_key"
        # 验证 URL
        assert mock_client.last_url == f"{FEISHU_BASE_URL}/open-apis/im/v1/files"
        # 验证 file_type 自动推断为 pdf
        assert mock_client.last_kwargs["data"]["file_type"] == "pdf"
        assert mock_client.last_kwargs["data"]["file_name"] == "report.pdf"

    @pytest.mark.asyncio
    async def test_auto_detect_xlsx(self, media, tmp_path):
        file_path = tmp_path / "data.xlsx"
        file_path.write_bytes(b"PK...")

        resp = MockResponse(
            status_code=200,
            json_data={"code": 0, "data": {"file_key": "file_v2_xlsx_key"}},
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            file_key = await media.upload_file(str(file_path))

        assert file_key == "file_v2_xlsx_key"

    @pytest.mark.asyncio
    async def test_unknown_extension_fallback_to_stream(self, media, tmp_path):
        file_path = tmp_path / "data.zip"
        file_path.write_bytes(b"PK...")

        resp = MockResponse(
            status_code=200,
            json_data={"code": 0, "data": {"file_key": "file_v2_zip_key"}},
        )
        mock_client = MockAsyncClient(resp)

        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=mock_client,
        ):
            file_key = await media.upload_file(str(file_path))

        assert file_key == "file_v2_zip_key"
        # zip 不在显式映射中 → 降级为 stream
        assert mock_client.last_kwargs["data"]["file_type"] == "stream"

    @pytest.mark.asyncio
    async def test_explicit_file_type_overrides(self, media, tmp_path):
        file_path = tmp_path / "unknown.bin"
        file_path.write_bytes(b"\x00\x01\x02")

        resp = MockResponse(
            status_code=200,
            json_data={"code": 0, "data": {"file_key": "file_v2_explicit"}},
        )
        mock_client = MockAsyncClient(resp)

        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=mock_client,
        ):
            file_key = await media.upload_file(str(file_path), file_type="mp4")

        assert file_key == "file_v2_explicit"
        assert mock_client.last_kwargs["data"]["file_type"] == "mp4"

    @pytest.mark.asyncio
    async def test_file_not_exist(self, media):
        result = await media.upload_file("/nonexistent/file.pdf")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_code(self, media, tmp_path):
        file_path = tmp_path / "test.pdf"
        file_path.write_bytes(b"%PDF")

        resp = MockResponse(
            status_code=200,
            json_data={"code": 230011, "msg": "file too large"},
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            result = await media.upload_file(str(file_path))

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_file_key_in_response(self, media, tmp_path):
        file_path = tmp_path / "test.pdf"
        file_path.write_bytes(b"%PDF")

        resp = MockResponse(
            status_code=200,
            json_data={"code": 0, "data": {}},
        )
        from unittest.mock import patch

        with patch(
            "src.channels.feishu.media.httpx.AsyncClient",
            return_value=MockAsyncClient(resp),
        ):
            result = await media.upload_file(str(file_path))

        assert result is None


# ---------- helper ----------


class TestExtFromContentType:
    def test_png(self):
        assert _ext_from_content_type("image/png") == ".png"

    def test_jpeg(self):
        assert _ext_from_content_type("image/jpeg") == ".jpg"

    def test_jpeg_with_charset(self):
        assert _ext_from_content_type("image/jpeg; charset=binary") == ".jpg"

    def test_gif(self):
        assert _ext_from_content_type("image/gif") == ".gif"

    def test_unknown(self):
        assert _ext_from_content_type("application/octet-stream") == ".bin"
