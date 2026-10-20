"""TencentDocsClient 单测：httpx.MockTransport 模拟 V2/V3 响应"""

import json

import httpx
import pytest

from src.services.tencent_docs.client import (
    TencentDocsClient,
    TencentDocsError,
    TencentDocsRateLimitError,
    TencentDocsUnmarshalError,
)
from src.services.tencent_docs.models import (
    TencentDocsCredentials,
    extract_encoded_id_from_url,
)


CREDS = TencentDocsCredentials(
    client_id="cid-test", access_token="tok-test", open_id="oid-test"
)


def make_client(handler) -> TencentDocsClient:
    return TencentDocsClient(CREDS, transport=httpx.MockTransport(handler))


@pytest.mark.unit
class TestHeaders:
    @pytest.mark.asyncio
    async def test_request_carries_triple_headers(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.headers))
            return httpx.Response(200, json={"ret": 0, "msg": "Succeed", "data": {"list": [], "next": 0}})

        async with make_client(handler) as client:
            await client.list_folder()
        assert seen["access-token"] == "tok-test"
        assert seen["client-id"] == "cid-test"
        assert seen["open-id"] == "oid-test"


@pytest.mark.unit
class TestListFolder:
    @pytest.mark.asyncio
    async def test_list_folder_parses_items(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/openapi/drive/v2/folders/"
            return httpx.Response(200, json={"ret": 0, "msg": "Succeed", "data": {
                "next": 20,
                "list": [
                    {"ID": "MhBnVDCudhNc", "title": "目录", "type": "folder"},
                    {"ID": "300000000$MuJHkdyTzKcE", "title": "工作日报", "type": "doc",
                     "shortcutID": "short-1"},
                ],
            }})

        async with make_client(handler) as client:
            items, next_start = await client.list_folder(start=0, limit=20)
        assert next_start == 20
        assert items[0].type == "folder"
        assert items[1].shortcut_id == "short-1"
        assert items[1].content_target_id() == "short-1"  # 快捷方式文档内容操作用 shortcutID
        assert items[0].content_target_id() == "MhBnVDCudhNc"

    @pytest.mark.asyncio
    async def test_list_all_paginates(self):
        pages = {0: (["a", "b"], 2), 2: (["c"], 0)}

        def handler(request: httpx.Request) -> httpx.Response:
            start = int(request.url.params["start"])
            titles, nxt = pages[start]
            return httpx.Response(200, json={"ret": 0, "data": {
                "next": nxt, "list": [{"ID": t, "title": t, "type": "doc"} for t in titles]}})

        async with make_client(handler) as client:
            items = await client.list_all()
        assert [i.file_id for i in items] == ["a", "b", "c"]


@pytest.mark.unit
class TestErrorMapping:
    @pytest.mark.asyncio
    async def test_v2_business_error(self):
        async def call():
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"ret": 10005, "msg": "身份校验失败"})
            async with make_client(handler) as client:
                await client.list_folder()

        with pytest.raises(TencentDocsError) as exc:
            await call()
        assert exc.value.code == 10005
        assert not exc.value.retryable

    @pytest.mark.asyncio
    async def test_v2_rate_limit_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ret": 9998, "msg": "超限"})
        async with make_client(handler) as client:
            with pytest.raises(TencentDocsRateLimitError):
                await client.list_folder()

    @pytest.mark.asyncio
    async def test_v3_unmarshal_error_maps_to_fallback_signal(self):
        def handler(request: httpx.Request) -> httpx.Response:
            # 线上路径必须保留 %24 编码（httpx 的 .path 会解码回 $，需看 raw_path）
            raw_path = request.url.raw_path
            if isinstance(raw_path, bytes):
                raw_path = raw_path.decode("ascii")
            assert raw_path == "/openapi/doc/v3/300000000%24MQUENxlUkTvJ"
            return httpx.Response(200, json={
                "code": 400010, "message": "Unmarshal document error",
                "details": {"DebugInfo": {"traceId": "t1"}}})

        async with make_client(handler) as client:
            with pytest.raises(TencentDocsUnmarshalError):
                await client.get_doc_content("300000000$MQUENxlUkTvJ")

    @pytest.mark.asyncio
    async def test_v3_rate_limit_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"code": 400007, "message": "请求超限"})
        async with make_client(handler) as client:
            with pytest.raises(TencentDocsRateLimitError) as exc:
                await client.get_doc_content("300000000$AAA")
        assert exc.value.retryable

    @pytest.mark.asyncio
    async def test_v3_http_error_without_json(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")
        async with make_client(handler) as client:
            with pytest.raises(TencentDocsError):
                await client.get_doc_content("300000000$AAA")


@pytest.mark.unit
class TestDocContent:
    @pytest.mark.asyncio
    async def test_get_doc_content_returns_document(self):
        document = {"begin": 0, "end": 6, "type": "Document", "children": []}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"document": document, "version": 4})

        async with make_client(handler) as client:
            data = await client.get_doc_content("300000000$AAA")
        assert data["document"]["type"] == "Document"
        assert data["version"] == 4

    @pytest.mark.asyncio
    async def test_get_doc_content_missing_document_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"foo": "bar"})
        async with make_client(handler) as client:
            with pytest.raises(TencentDocsError):
                await client.get_doc_content("300000000$AAA")


@pytest.mark.unit
class TestConverter:
    @pytest.mark.asyncio
    async def test_convert_encoded_id(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["type"] == "2"
            assert request.url.params["value"] == "DFFFF"
            return httpx.Response(200, json={"ret": 0, "data": {"fileID": "300000000$AAAA"}})

        async with make_client(handler) as client:
            file_id = await client.convert_encoded_id("DFFFF")
        assert file_id == "300000000$AAAA"

    @pytest.mark.asyncio
    async def test_convert_missing_file_id_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ret": 0, "data": {}})
        async with make_client(handler) as client:
            with pytest.raises(TencentDocsError):
                await client.convert_encoded_id("DFFFF")


@pytest.mark.unit
class TestExtractEncodedId:
    def test_plain_share_link(self):
        assert extract_encoded_id_from_url("https://docs.qq.com/doc/DXXXXabc") == "DXXXXabc"

    def test_query_and_fragment_stripped(self):
        assert extract_encoded_id_from_url(
            "https://docs.qq.com/doc/DXXXXabc?tab=bb&u=1#foo") == "DXXXXabc"

    def test_trailing_slash_and_empty(self):
        assert extract_encoded_id_from_url("https://docs.qq.com/doc/DXXXXabc/") == "DXXXXabc"
        assert extract_encoded_id_from_url("") is None
