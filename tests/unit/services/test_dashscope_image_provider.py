"""DashScope 图像生成 provider 单测（httpx MockTransport，全 mock 外部 API）

覆盖：
- async 提交：endpoint 选择（参考图有无 → image2image/text2image）、body/headers 组装
- 轮询状态机：SUCCEEDED 取 results[].url、FAILED 带 error、成功无 url 转 FAILED
- sync 提交：messages 多模态形态（image parts + text）、两种响应结构兼容
- 参考图 >3 拒绝、mode 非法拒绝、非 2xx 抛 ImageGenProviderError
"""
import json
from unittest.mock import patch

import httpx
import pytest

from src.services.image_gen.base import (
    ImageGenProviderError,
    ImageGenRequest,
)
from src.services.image_gen.dashscope_provider import DashScopeImageProvider

BASE_URL = "https://dashscope.test/api/v1"


def _patch_async_client(handler):
    """把 provider 内部 httpx.AsyncClient 替换为注入 MockTransport 的客户端"""
    transport = httpx.MockTransport(handler)

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(transport=transport, *args, **kwargs)

    return patch("src.services.image_gen.dashscope_provider.httpx.AsyncClient", _PatchedAsyncClient)


def _make_provider(mode="async_task"):
    return DashScopeImageProvider(
        api_key="sk-test", model="qwen-image-edit", mode=mode, base_url=BASE_URL
    )


class TestInit:
    def test_empty_api_key_rejected(self):
        with pytest.raises(ImageGenProviderError, match="api_key"):
            DashScopeImageProvider(api_key="")

    def test_invalid_mode_rejected(self):
        with pytest.raises(ImageGenProviderError, match="mode"):
            DashScopeImageProvider(api_key="sk", mode="foo")

    def test_valid_init(self):
        p = _make_provider()
        assert p.name == "dashscope"


class TestSubmitAsync:
    @pytest.mark.asyncio
    async def test_text2image_endpoint_when_no_refs(self):
        """无参考图走 text2image/image-synthesis，X-DashScope-Async header 必带"""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"output": {"task_id": "t-1", "task_status": "PENDING"}})

        provider = _make_provider()
        with _patch_async_client(handler):
            result = await provider.submit(ImageGenRequest(prompt="效果图", n=2))

        assert result.task_id == "t-1"
        assert result.task_status == "PENDING"
        assert captured["url"].endswith("/services/aigc/text2image/image-synthesis")
        assert captured["headers"].get("x-dashscope-async") == "enable"
        assert captured["headers"].get("authorization") == "Bearer sk-test"
        body = captured["body"]
        assert body["model"] == "qwen-image-edit"
        assert body["input"]["prompt"] == "效果图"
        assert "images" not in body["input"]
        assert body["parameters"]["size"] == "1280*960"
        assert body["parameters"]["n"] == 2

    @pytest.mark.asyncio
    async def test_image2image_endpoint_and_ref_order(self):
        """有参考图走 image2image，input.images 顺序保持（材质锚点在前）"""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"output": {"task_id": "t-2"}})

        provider = _make_provider()
        refs = ["data:image/png;base64,PRODUCT", "data:image/png;base64,FLOORPLAN"]
        with _patch_async_client(handler):
            await provider.submit(ImageGenRequest(prompt="p", reference_images_data_urls=refs))

        assert captured["url"].endswith("/services/aigc/image2image/image-synthesis")
        assert captured["body"]["input"]["images"] == refs

    @pytest.mark.asyncio
    async def test_negative_prompt_and_seed_in_body(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"output": {"task_id": "t-3"}})

        provider = _make_provider()
        with _patch_async_client(handler):
            await provider.submit(ImageGenRequest(
                prompt="p", negative_prompt="水印, logo", seed=42))

        body = captured["body"]
        assert body["input"]["negative_prompt"] == "水印, logo"
        assert body["parameters"]["seed"] == 42


    @pytest.mark.asyncio
    async def test_too_many_refs_rejected(self):
        provider = _make_provider()
        with pytest.raises(ImageGenProviderError, match="3 张"):
            await provider.submit(ImageGenRequest(
                prompt="p", reference_images_data_urls=["a", "b", "c", "d"]))

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"code": "InvalidApiKey"})

        provider = _make_provider()
        with _patch_async_client(handler):
            with pytest.raises(ImageGenProviderError, match="401"):
                await provider.submit(ImageGenRequest(prompt="p"))

    @pytest.mark.asyncio
    async def test_missing_task_id_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"output": {}})

        provider = _make_provider()
        with _patch_async_client(handler):
            with pytest.raises(ImageGenProviderError, match="task_id"):
                await provider.submit(ImageGenRequest(prompt="p"))


class TestPoll:
    @pytest.mark.asyncio
    async def test_succeeded_extracts_urls(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/tasks/t-9")
            return httpx.Response(200, json={
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [{"url": "http://img/1.png"}, {"url": "http://img/2.png"}],
                }
            })

        provider = _make_provider()
        with _patch_async_client(handler):
            result = await provider.poll("t-9")

        assert result.task_status == "SUCCEEDED"
        assert result.image_urls == ["http://img/1.png", "http://img/2.png"]
        assert result.error is None

    @pytest.mark.asyncio
    async def test_succeeded_without_urls_becomes_failed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "output": {"task_status": "SUCCEEDED", "results": [{}]}
            })

        provider = _make_provider()
        with _patch_async_client(handler):
            result = await provider.poll("t-x")

        assert result.task_status == "FAILED"
        assert "未返回图片" in result.error

    @pytest.mark.asyncio
    async def test_failed_carries_message(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "output": {"task_status": "FAILED", "message": "InvalidParameter", "code": "err"}
            })

        provider = _make_provider()
        with _patch_async_client(handler):
            result = await provider.poll("t-y")

        assert result.task_status == "FAILED"
        assert result.error == "InvalidParameter"
        assert result.image_urls == []

    @pytest.mark.asyncio
    async def test_running_passthrough(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"output": {"task_status": "RUNNING"}})

        provider = _make_provider()
        with _patch_async_client(handler):
            result = await provider.poll("t-z")

        assert result.task_status == "RUNNING"

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        provider = _make_provider()
        with _patch_async_client(handler):
            with pytest.raises(ImageGenProviderError, match="500"):
                await provider.poll("t-e")


class TestSubmitSync:
    @pytest.mark.asyncio
    async def test_messages_shape_with_refs_first(self):
        """sync 模式：content 为 [image parts..., text]，参考图在前 prompt 在后"""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "output": {"results": [{"url": "http://img/sync.png"}]}
            })

        provider = _make_provider(mode="sync")
        refs = ["data:image/png;base64,A", "data:image/png;base64,B"]
        with _patch_async_client(handler):
            result = await provider.submit(ImageGenRequest(
                prompt="prompt-text", reference_images_data_urls=refs, negative_prompt="水印"))

        assert result.task_id is None
        assert result.task_status == "SUCCEEDED"
        assert result.image_urls == ["http://img/sync.png"]
        assert captured["url"].endswith("/services/aigc/multimodal-generation/generation")
        content = captured["body"]["input"]["messages"][0]["content"]
        assert content[0] == {"image": refs[0]}
        assert content[1] == {"image": refs[1]}
        assert content[2] == {"text": "prompt-text"}
        assert captured["body"]["parameters"]["negative_prompt"] == "水印"
        # sync 不带异步 header
        assert "x-dashscope-async" not in captured["headers"]

    @pytest.mark.asyncio
    async def test_choices_response_shape_compat(self):
        """兼容 choices[].message.content[].image 结构"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "output": {"choices": [{"message": {"content": [
                    {"image": "http://img/choice.png"},
                ]}}]}
            })

        provider = _make_provider(mode="sync")
        with _patch_async_client(handler):
            result = await provider.submit(ImageGenRequest(prompt="p"))

        assert result.image_urls == ["http://img/choice.png"]

    @pytest.mark.asyncio
    async def test_no_images_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"output": {}})

        provider = _make_provider(mode="sync")
        with _patch_async_client(handler):
            with pytest.raises(ImageGenProviderError, match="未返回图片"):
                await provider.submit(ImageGenRequest(prompt="p"))
