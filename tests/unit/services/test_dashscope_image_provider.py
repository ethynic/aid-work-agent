"""DashScope 图像生成 provider 单测（httpx MockTransport，全 mock 外部 API）

覆盖（qwen-image-3.0 系列 wire 形态，API 文档定版）：
- async 提交：统一端点 image-generation/generation、messages 形态 body（T2I 仅 text / I2I image+text）、headers 组装
- 轮询状态机：SUCCEEDED 取 choices[].message.content[].image、FAILED 带 error、成功无 url 转 FAILED
- sync 提交：multimodal-generation 端点、相同 messages 形态、两种响应结构兼容
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
        api_key="sk-test", model="qwen-image-3.0-pro", mode=mode, base_url=BASE_URL
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
    async def test_text2image_body_and_headers(self):
        """无参考图（T2I）：统一端点 image-generation/generation，content 仅含 text"""
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
        assert captured["url"].endswith("/services/aigc/image-generation/generation")
        assert captured["headers"].get("x-dashscope-async") == "enable"
        assert captured["headers"].get("authorization") == "Bearer sk-test"
        body = captured["body"]
        assert body["model"] == "qwen-image-3.0-pro"
        content = body["input"]["messages"][0]["content"]
        assert content == [{"text": "效果图"}]
        assert body["parameters"]["size"] == "1280*960"
        assert body["parameters"]["n"] == 2
        assert body["parameters"]["enable_thinking"] is False

    @pytest.mark.asyncio
    async def test_enable_thinking_passthrough(self):
        """enable_thinking=True 透传进 parameters（思考模式开关，默认关）"""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"output": {"task_id": "t-1", "task_status": "PENDING"}})

        provider = DashScopeImageProvider(
            api_key="sk-test", model="qwen-image-3.0-pro", base_url=BASE_URL, enable_thinking=True
        )
        with _patch_async_client(handler):
            await provider.submit(ImageGenRequest(prompt="效果图", n=1))

        assert captured["body"]["parameters"]["enable_thinking"] is True

    @pytest.mark.asyncio
    async def test_image2image_body_and_ref_order(self):
        """有参考图（I2I）：content 为 image parts 在前 + text 在后，顺序即语义顺序"""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"output": {"task_id": "t-2"}})

        provider = _make_provider()
        refs = ["data:image/png;base64,PRODUCT", "data:image/png;base64,FLOORPLAN"]
        with _patch_async_client(handler):
            await provider.submit(ImageGenRequest(prompt="p", reference_images_data_urls=refs))

        assert captured["url"].endswith("/services/aigc/image-generation/generation")
        content = captured["body"]["input"]["messages"][0]["content"]
        assert content[0] == {"image": refs[0]}
        assert content[1] == {"image": refs[1]}
        assert content[2] == {"text": "p"}

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
        assert body["parameters"]["negative_prompt"] == "水印, logo"
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
        """SUCCEEDED：结果在 choices[].message.content[].image（3.0 系列轮询结构）"""
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/tasks/t-9")
            return httpx.Response(200, json={
                "output": {
                    "task_status": "SUCCEEDED",
                    "choices": [
                        {"message": {"content": [{"image": "http://img/1.png"}]}},
                        {"message": {"content": [{"image": "http://img/2.png"}]}},
                    ],
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
                "output": {"task_status": "SUCCEEDED", "choices": [{"message": {"content": []}}]}
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
