"""hongtao_shop vision 单测：解析器行为（fake gateway + MockTransport）+ 图级缓存。"""

import io

import httpx
import pytest

from src.tenant_custom.hongtao_shop.vision import (
    UNRECOGNIZED_TEXT,
    CachedVision,
    ImageParseSuccess,
    VisionParser,
    VisionTarget,
    bytes_to_data_url,
    clean_description,
    describe_images_cached,
)


def _png_bytes(color=(255, 0, 0)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


class _FakeGateway:
    def __init__(self, content: str):
        self._content = content
        self.calls = 0

    async def chat(self, messages, temperature=0.2, **kwargs):
        self.calls += 1
        return {"content": self._content, "usage": {"total_tokens": 10}}


def test_clean_description_strips_prefixes():
    assert clean_description("图片识别结果如下：大理石纹理") == "大理石纹理"
    assert clean_description("标题：仿古砖") == "仿古砖"
    assert clean_description("这张图片展示了通体砖铺贴") == "通体砖铺贴"
    assert clean_description("正文中的 标题： 不动") == "正文中的 标题： 不动"
    assert clean_description("") == ""


def test_bytes_to_data_url_passthrough_and_compress():
    data = _png_bytes()
    url = bytes_to_data_url(data)
    assert url.startswith("data:image/png;base64,")
    # 强制超限 → 走 Pillow 压缩路径转 JPEG
    url2 = bytes_to_data_url(data, max_bytes=8)
    assert url2.startswith("data:image/jpeg;base64,")
    assert bytes_to_data_url(b"not an image") is None


async def test_parser_success_and_unrecognized():
    """成功路径产出描述；unrecognized 标记计失败不计费。"""
    png = _png_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=png)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = _FakeGateway("TPJ157042 通体大理石砖效果图")
        parser = VisionParser(
            targets=[VisionTarget(provider="zhipu", model="GLM-5.3-Flash")],
            gateway_factory=lambda p, m: gateway,
            client=client,
        )
        outcome = await parser.describe_images(["https://oss/a.jpg"])
    assert len(outcome.successes) == 1
    assert outcome.successes[0].description == "TPJ157042 通体大理石砖效果图"
    assert outcome.successes[0].model == "GLM-5.3-Flash"

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway2 = _FakeGateway(UNRECOGNIZED_TEXT)
        parser2 = VisionParser(
            targets=[VisionTarget(provider="zhipu", model="GLM-5.3-Flash")],
            gateway_factory=lambda p, m: gateway2,
            client=client,
        )
        outcome2 = await parser2.describe_images(["https://oss/a.jpg"])
    assert outcome2.successes == []
    assert outcome2.failures[0].reason == "unrecognized"


async def test_parser_download_failed():
    """非 200 下载失败计 download_failed。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        parser = VisionParser(
            targets=[VisionTarget(provider="zhipu", model="GLM-5.3-Flash")],
            gateway_factory=lambda p, m: _FakeGateway("x"),
            client=client,
        )
        outcome = await parser.describe_images(["https://oss/missing.jpg"])
    assert outcome.failures[0].reason == "download_failed"


async def test_parser_description_truncated_to_100():
    png = _png_bytes()
    long_text = "字" * 150

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=png)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        parser = VisionParser(
            targets=[VisionTarget(provider="zhipu", model="GLM-5.3-Flash")],
            gateway_factory=lambda p, m: _FakeGateway(long_text),
            client=client,
        )
        outcome = await parser.describe_images(["https://oss/a.jpg"])
    assert len(outcome.successes[0].description) == 100


async def test_describe_images_cached_dedupes_urls(tenant_id):
    """入口去重兜底：重复 URL 只解析计费一次。"""

    class _CountingParser:
        def __init__(self):
            self.seen: list = []

        async def describe_images(self, urls):
            self.seen.extend(urls)
            from src.tenant_custom.hongtao_shop.vision import (
                ImageParseFailure,
                ImageParseSuccess,
                VisionParseOutcome,
            )

            outcome = VisionParseOutcome()
            outcome.successes = [
                ImageParseSuccess(url=urls[0], description="砖图", model="GLM-5.3-Flash", provider="zhipu")
            ]
            outcome.failures = [ImageParseFailure(url="https://never/", reason="error")]
            return outcome

    parser = _CountingParser()
    url = "https://oss/dup.jpg"
    vision_map, successes, _ = await describe_images_cached(
        tenant_id, [url, url, url], parser=parser
    )
    assert parser.seen == [url]  # 去重后只解析一次
    assert len(successes) == 1  # 计费一条


async def test_download_image_bytes_over_limit():
    """单张下载超 5MB 护栏 → None（计 download_failed）。"""
    from src.tenant_custom.hongtao_shop.vision import MAX_IMAGE_BYTES, download_image_bytes

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_IMAGE_BYTES + 1))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await download_image_bytes(client, "https://oss/big.jpg") is None


async def test_parser_falls_back_to_second_target():
    """首选目标连续失败 → 次选目标成功（降级序列，照 wechat_mp 口径）。"""
    png = _png_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=png)

    class _FlakyGateway:
        async def chat(self, messages, temperature=0.2, **kwargs):
            raise RuntimeError("primary down")

    class _GoodGateway:
        async def chat(self, messages, temperature=0.2, **kwargs):
            return {"content": "次选目标的白描", "usage": {}}

    def factory(provider: str, model: str):
        return _FlakyGateway() if provider == "primary" else _GoodGateway()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        parser = VisionParser(
            targets=[
                VisionTarget(provider="primary", model="M1"),
                VisionTarget(provider="backup", model="M2"),
            ],
            gateway_factory=factory,
            client=client,
        )
        outcome = await parser.describe_images(["https://oss/a.jpg"])
    assert len(outcome.successes) == 1
    assert outcome.successes[0].model == "M2"
    assert outcome.successes[0].description == "次选目标的白描"


class _NoCallParser:
    """缓存全命中时不应被调用的解析器替身。"""

    def __init__(self):
        self.called = False

    async def describe_images(self, urls):
        self.called = True
        raise AssertionError("缓存命中仍触发了解析")


async def test_describe_images_cached_hit_and_miss(tenant_id):
    """缓存：首轮 miss 解析入库（ok/unrecognized 终态），二轮全命中零解析。"""

    class _OnceParser:
        def __init__(self):
            self.calls = 0

        async def describe_images(self, urls):
            self.calls += 1
            from src.tenant_custom.hongtao_shop.vision import ImageParseFailure, VisionParseOutcome

            outcome = VisionParseOutcome()
            outcome.successes = [
                ImageParseSuccess(url=urls[0], description="TPJ157042 砖图", model="GLM-5.3-Flash", provider="zhipu")
            ]
            outcome.failures = [
                ImageParseFailure(url=urls[1], reason="unrecognized"),
                ImageParseFailure(url=urls[2], reason="error"),
            ]
            return outcome

    urls = ["https://oss/x1.jpg", "https://oss/x2.jpg", "https://oss/x3.jpg"]
    parser = _OnceParser()
    vision_map, successes, failures = await describe_images_cached(tenant_id, urls, parser=parser)
    assert parser.calls == 1
    assert len(successes) == 1 and successes[0].url == urls[0]
    assert {f.reason for f in failures} == {"unrecognized", "error"}
    assert vision_map[urls[0]].status == "ok" and vision_map[urls[0]].is_billed is True
    assert vision_map[urls[1]].status == "unrecognized" and vision_map[urls[1]].is_billed is False
    assert urls[2] not in vision_map  # error 失败不入缓存不入 map

    # 二轮：全命中（error 那张会 miss 重试，此处只查前两张已缓存的不触发解析）
    no_call = _NoCallParser()
    vision_map2, successes2, _ = await describe_images_cached(
        tenant_id, [urls[0], urls[1]], parser=no_call
    )
    assert no_call.called is False
    assert successes2 == []
    assert vision_map2[urls[0]].description == "TPJ157042 砖图"
